"""A conversion that *reuses* a record leaves a trace, and the reuse and the aborted-conversion
cleanup serialise on the record's row lock.

The cleanup deletes a company or contact only when its audit history is exactly the attempt's own
creation row. A reuse by name or email writes one `*.reused_for_lead` marker row (or the
`contact.updated` / masked-phone row it already wrote), so an adopted record never looks pristine.
Both sides lock the row (`FOR UPDATE`), so in either order the later one sees the earlier one's
commit: a cleanup first makes the adoption create a fresh company; an adoption first makes the
cleanup keep it. Two real sessions, never one, so the locks actually contend.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, AuditLog
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

CLEANUP = "crm-conversion-cleanup"


@pytest_asyncio.fixture
async def other_session(org_id: uuid.UUID, session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A second app-role session bound to the same tenant (`session` truncates around both)."""
    engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        yield s
    await engine.dispose()


async def _owner(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    async with session.begin():
        owner = await services.create_owner(
            session,
            ctx_for(org_id, "crm.book.owner.write", Scope.ORG),
            full_name="Priya Rep",
            email=f"rep-{uuid.uuid4()}@example.com",
        )
    return owner.id


async def _ensure(
    session: AsyncSession,
    org_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    company_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    name: str = "Stellar Robotics",
    email: str | None = None,
    phone: str | None = None,
):  # type: ignore[no-untyped-def]
    return await services.ensure_company_and_contact_for_lead(
        session,
        services.lead_conversion_ctx(org_id, None),
        company_id=company_id or uuid.uuid4(),
        contact_id=contact_id or uuid.uuid4(),
        company_name=name,
        owner_id=owner_id,
        contact_full_name="Priya Sharma",
        contact_email=email,
        contact_raw_phone=phone,
    )


async def _audit(session: AsyncSession, entity_id: uuid.UUID) -> list[AuditLog]:
    session.expire_all()
    async with session.begin():
        stmt = select(AuditLog).where(AuditLog.entity_id == entity_id).order_by(AuditLog.at)
        return list((await session.execute(stmt)).scalars())


async def test_a_reuse_by_name_writes_a_marker_audit_row_and_locks_the_row(
    session: AsyncSession, other_session: AsyncSession, org_id: uuid.UUID
) -> None:
    owner = await _owner(session, org_id)
    async with session.begin():
        existing, _ = await _ensure(session, org_id, owner)
    existing_id = existing.id

    await session.begin()
    reused, _ = await _ensure(session, org_id, owner, company_id=uuid.uuid4())
    assert reused.id == existing_id
    async with other_session.begin():
        await other_session.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(DBAPIError):
            await selectors.get_company_for_update_any_state(other_session, org_id, existing_id)
    await session.commit()

    async with other_session.begin():
        assert await selectors.get_company_for_update_any_state(other_session, org_id, existing_id)
    rows = await _audit(session, existing_id)
    assert [(r.action, r.acting_as) for r in rows] == [
        ("company.created", "lead"),
        ("company.reused_for_lead", "lead"),
    ]
    assert rows[1].after == {"matched_by": "name"}


@pytest.mark.parametrize(
    ("first_phone", "second_phone", "expected"),
    [
        (None, None, "contact.reused_for_lead:matched_by"),
        ("+919876543210", "+919876543210", "contact.reused_for_lead:matched_by"),
        (None, "+919876543210", "contact.updated"),
        ("+919876543210", "+919988776655", "contact.reused_for_lead:phone"),
    ],
)
async def test_a_contact_reuse_by_email_writes_exactly_one_audit_row_whatever_the_phone_shape(
    session: AsyncSession,
    org_id: uuid.UUID,
    first_phone: str | None,
    second_phone: str | None,
    expected: str,
) -> None:
    owner = await _owner(session, org_id)
    email = "priya@stellar.example"
    async with session.begin():
        _, contact = await _ensure(session, org_id, owner, email=email, phone=first_phone)
    contact_id = contact.id
    async with session.begin():
        _, reused = await _ensure(
            session, org_id, owner, name="Other Co", email=email, phone=second_phone
        )
    assert reused.id == contact_id

    rows = await _audit(session, contact_id)
    assert rows[0].action == "contact.created"
    [marker] = rows[1:]
    action, _, kind = expected.partition(":")
    assert marker.action == action
    assert marker.acting_as == "lead"
    if kind == "matched_by":
        assert marker.after == {"matched_by": "email"}
    elif kind == "phone":
        assert set(marker.after) == {"phone_from_lead", "kept_existing_phone"}


async def test_a_reuse_by_id_writes_no_marker(session: AsyncSession, org_id: uuid.UUID) -> None:
    owner = await _owner(session, org_id)
    company_id, contact_id = uuid.uuid4(), uuid.uuid4()
    for _ in range(2):
        async with session.begin():
            await _ensure(
                session,
                org_id,
                owner,
                company_id=company_id,
                contact_id=contact_id,
                email="priya@stellar.example",
            )
    assert [r.action for r in await _audit(session, company_id)] == ["company.created"]
    assert [r.action for r in await _audit(session, contact_id)] == ["contact.created"]


async def test_a_cleanup_then_an_adoption_creates_a_fresh_company(
    session: AsyncSession, other_session: AsyncSession, org_id: uuid.UUID
) -> None:
    owner = await _owner(session, org_id)
    orphan_id = uuid.uuid4()
    async with session.begin():
        await _ensure(session, org_id, owner, company_id=orphan_id)

    await session.begin()
    orphan = await selectors.get_company_for_update_any_state(session, org_id, orphan_id)
    assert orphan is not None
    await services.delete_company(
        session, services.service_ctx(org_id), orphan, acting_as=CLEANUP, reason="test"
    )

    async def adopt() -> uuid.UUID:
        async with other_session.begin():
            company, _ = await _ensure(other_session, org_id, owner, company_id=uuid.uuid4())
            return company.id

    adoption = asyncio.create_task(adopt())
    await asyncio.sleep(0.3)
    assert not adoption.done()
    await session.commit()
    adopted = await adoption

    assert adopted != orphan_id
    assert [r.action for r in await _audit(session, adopted)] == ["company.created"]


async def test_an_adoption_then_a_cleanup_keeps_the_company(
    session: AsyncSession, other_session: AsyncSession, org_id: uuid.UUID
) -> None:
    owner = await _owner(session, org_id)
    orphan_id = uuid.uuid4()
    async with session.begin():
        await _ensure(session, org_id, owner, company_id=orphan_id)
    async with other_session.begin():
        adopted, _ = await _ensure(other_session, org_id, owner, company_id=uuid.uuid4())
    assert adopted.id == orphan_id

    session.expire_all()
    async with session.begin():
        await selectors.get_company_for_update_any_state(session, org_id, orphan_id)
        rows = await selectors.audit_rows_for_entity(session, org_id, "company", orphan_id)
    assert rows == [("company.created", "lead"), ("company.reused_for_lead", "lead")]


async def test_cleanup_and_adoption_serialise_on_the_row_lock(
    session: AsyncSession, other_session: AsyncSession, org_id: uuid.UUID
) -> None:
    owner = await _owner(session, org_id)
    orphan_id = uuid.uuid4()
    async with session.begin():
        await _ensure(session, org_id, owner, company_id=orphan_id)

    await session.begin()
    await selectors.get_company_for_update_any_state(session, org_id, orphan_id)
    with pytest.raises(DBAPIError):
        async with other_session.begin():
            await other_session.execute(text("SET LOCAL lock_timeout = '300ms'"))
            await _ensure(other_session, org_id, owner, company_id=uuid.uuid4())
    await session.commit()

    async with other_session.begin():
        adopted, _ = await _ensure(other_session, org_id, owner, company_id=uuid.uuid4())
    assert adopted.id == orphan_id


async def test_delete_company_and_delete_contact_record_acting_as_and_reason(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    owner = await _owner(session, org_id)
    async with session.begin():
        company, contact = await _ensure(session, org_id, owner, email="priya@stellar.example")
    company_id, contact_id = company.id, contact.id
    ctx = services.service_ctx(org_id)

    async with session.begin():
        company = await selectors.get_company_for_update_any_state(session, org_id, company_id)
        with pytest.raises(ConflictProblem):
            await services.delete_company(session, ctx, company, acting_as=CLEANUP, reason="r")
    async with session.begin():
        contact = await selectors.get_contact_for_update_any_state(session, org_id, contact_id)
        await services.delete_contact(session, ctx, contact, acting_as=CLEANUP, reason="r1")
        company = await selectors.get_company_for_update_any_state(session, org_id, company_id)
        await services.delete_company(session, ctx, company, acting_as=CLEANUP, reason="r2")

    for entity_id, reason in ((contact_id, "r1"), (company_id, "r2")):
        deleted = [r for r in await _audit(session, entity_id) if r.action.endswith(".deleted")]
        assert [(r.acting_as, r.reason) for r in deleted] == [(CLEANUP, reason)]
