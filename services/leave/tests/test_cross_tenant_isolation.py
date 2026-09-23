"""The acceptance test for tenancy. Everything the RLS migration does exists to make this pass.

The authorization tests check that the application *refuses*. These check that the application
could not succeed even if it *forgot*: a session bound to one organization cannot see or touch
another's leave request even when it names it by primary key, with no filter at all.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import SCHEMA, LeaveRequest, LeaveType
from platform_core.db import TENANT_GUC

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _tenant_session(org: uuid.UUID):
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _seed_other_org(admin_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a second organization's leave type and request on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    other_type = uuid.uuid4()
    other_request = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            LeaveType.__table__.insert().values(
                id=other_type,
                organization_id=other_org,
                code="EL",
                name="Earned leave",
                is_paid=True,
                active=True,
            )
        )
        await admin_session.execute(
            LeaveRequest.__table__.insert().values(
                id=other_request,
                organization_id=other_org,
                employee_id=uuid.uuid4(),
                leave_type_id=other_type,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 21),
                half_day_start=False,
                half_day_end=False,
                total_half_days=2,
                reason="another tenant's leave",
                status="pending",
            )
        )
    return other_org, other_request


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_requests(
        self, org_id, session, admin_session
    ) -> None:
        """The headline. No filter in the query — the database applies it."""
        other_org, _ = await _seed_other_org(admin_session)

        # Something belonging to this test's own organization, so the "sees its own" half is
        # not vacuously true.
        leave_type_id = uuid.uuid4()
        async with session.begin():
            await session.execute(
                LeaveType.__table__.insert().values(
                    id=leave_type_id,
                    organization_id=org_id,
                    code="EL",
                    name="Earned leave",
                    is_paid=True,
                    active=True,
                )
            )
            await session.execute(
                LeaveRequest.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    employee_id=uuid.uuid4(),
                    leave_type_id=leave_type_id,
                    start_date=date(2026, 9, 21),
                    end_date=date(2026, 9, 21),
                    half_day_start=False,
                    half_day_end=False,
                    total_half_days=2,
                    reason="this tenant's own leave",
                    status="pending",
                )
            )

        bound, engine = await _tenant_session(org_id)
        try:
            async with bound.begin():
                rows = (
                    (await bound.execute(text("SELECT organization_id FROM leave.leave_requests")))
                    .scalars()
                    .all()
                )
        finally:
            await bound.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's requests"
        assert set(rows) == {org_id}
        assert other_org not in set(rows)

    async def test_naming_another_tenants_request_directly_returns_nothing(
        self, org_id, admin_session
    ) -> None:
        _, stolen = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM leave.leave_requests WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "a leave request from another organization was readable by primary key. The policy "
            "is not applying — check the connecting role is NOBYPASSRLS and does not own the "
            "table."
        )

    async def test_writing_into_another_tenant_is_refused(self, org_id, admin_session) -> None:
        other_org, other_type = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO leave.leave_requests "
                            "(id, organization_id, employee_id, leave_type_id, start_date, "
                            " end_date, half_day_start, half_day_end, total_half_days, reason, "
                            " status, created_at, updated_at) "
                            "VALUES (:id, :org, :emp, :ltype, '2026-09-21', '2026-09-21', "
                            " false, false, 2, 'planted', 'pending', now(), now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "org": other_org,
                            "emp": uuid.uuid4(),
                            "ltype": other_type,
                        },
                    )
        finally:
            await session.close()
            await engine.dispose()

    async def test_an_unbound_session_sees_nothing_at_all(self, org_id, session) -> None:
        """Fail closed. A request that somehow skipped the chokepoint reads zero rows."""
        async with session.begin():
            await session.execute(
                LeaveType.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    code="EL",
                    name="Earned leave",
                    is_paid=True,
                    active=True,
                )
            )

        engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as unbound, unbound.begin():
                count = (
                    await unbound.execute(text("SELECT count(*) FROM leave.leave_types"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} row(s). With no tenant set the policy must match "
            "nothing — otherwise forgetting the chokepoint exposes every tenant."
        )
