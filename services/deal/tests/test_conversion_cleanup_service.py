"""Deal's building blocks for the aborted-conversion cleanup: a system context, an audited delete
that names its reason, and the one "pristine orphan" predicate the handler and the references
probe share (so the two can never disagree about which deal is disposable)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import AuditLog, Deal, OutboxEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _write(org_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)


async def _deal(
    session: AsyncSession, org_id: uuid.UUID, *, acting_as: str | None = "lead"
) -> Deal:
    async with session.begin():
        return await services.create_deal(
            session,
            services.lead_conversion_ctx(org_id, None) if acting_as else _write(org_id),
            name=f"Deal {uuid.uuid4()}",
            company_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            value_minor=0,
            close_date=date(2026, 12, 31),
            acting_as=acting_as,
        )


async def _pristine(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID) -> bool:
    async with session.begin():
        return await services.is_pristine_lead_deal(session, org_id, deal_id)


def test_service_ctx_is_org_scope_with_no_membership_and_no_subject() -> None:
    org = uuid.uuid4()
    ctx = services.service_ctx(org)
    assert ctx.scope is Scope.ORG
    assert ctx.principal.membership_id is None
    assert ctx.principal.subject_ref is None
    assert ctx.principal.sub == uuid.UUID(int=0)
    assert ctx.organization_id == org


async def test_delete_deal_records_acting_as_and_reason_on_the_audit_row(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id)
    reason = f"lead_conversion_aborted:{uuid.uuid4()}:g1"
    async with session.begin():
        await services.delete_deal(
            session,
            services.service_ctx(org_id),
            deal,
            acting_as="crm-conversion-cleanup",
            reason=reason,
        )
    async with session.begin():
        row = (
            await session.execute(select(AuditLog).where(AuditLog.action == "deal.deleted"))
        ).scalar_one()
        types = list(
            (
                await session.execute(
                    select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == deal.id)
                )
            ).scalars()
        )
    assert row.acting_as == "crm-conversion-cleanup"
    assert row.reason == reason
    assert types.count("crm.pipeline.deal.deleted") == 1
    assert "crm.pipeline.deal.stage_changed" not in types


async def test_delete_deal_without_the_new_arguments_is_unchanged(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id, acting_as=None)
    async with session.begin():
        await services.delete_deal(session, _write(org_id), deal)
    async with session.begin():
        row = (
            await session.execute(select(AuditLog).where(AuditLog.action == "deal.deleted"))
        ).scalar_one()
    assert row.acting_as is None
    assert row.reason is None


async def test_a_deal_created_by_lead_with_no_activity_is_pristine(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id)
    assert await _pristine(session, org_id, deal.id) is True


async def test_a_deal_with_an_activity_is_not_pristine(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id)
    async with session.begin():
        await services.log_activity(
            session,
            _write(org_id),
            activity_type="note",
            subject="Called",
            owner_id=uuid.uuid4(),
            deal_id=deal.id,
        )
    assert await _pristine(session, org_id, deal.id) is False


async def test_a_moved_deal_is_not_pristine(session: AsyncSession, org_id: uuid.UUID) -> None:
    deal = await _deal(session, org_id)
    async with session.begin():
        await services.move_stage(session, _write(org_id), deal, new_stage="proposal")
    assert await _pristine(session, org_id, deal.id) is False


async def test_an_edited_deal_is_not_pristine(session: AsyncSession, org_id: uuid.UUID) -> None:
    deal = await _deal(session, org_id)
    async with session.begin():
        await services.update_deal(session, _write(org_id), deal, DealPatch(value_minor=5))
    assert await _pristine(session, org_id, deal.id) is False


async def test_a_deal_created_by_a_rep_is_not_pristine(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id, acting_as=None)
    assert await _pristine(session, org_id, deal.id) is False


async def test_an_unknown_id_is_not_pristine(session: AsyncSession, org_id: uuid.UUID) -> None:
    assert await _pristine(session, org_id, uuid.uuid4()) is False


async def test_pristine_never_reads_another_tenants_audit_rows(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        deal = await services.create_deal(
            admin_session,
            services.lead_conversion_ctx(other, None),
            name="Other tenant's orphan",
            company_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            value_minor=0,
            close_date=date(2026, 12, 31),
            acting_as="lead",
        )
    async with admin_session.begin():
        assert await services.is_pristine_lead_deal(admin_session, other, deal.id) is True
    assert await _pristine(session, org_id, deal.id) is False


async def test_the_locked_any_state_read_finds_a_soft_deleted_deal(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    deal = await _deal(session, org_id)
    async with session.begin():
        await services.delete_deal(session, _write(org_id), deal)
    async with session.begin():
        found = await selectors.get_deal_for_update_any_state(session, org_id, deal.id)
        assert found is not None and found.deleted_at is not None
        assert await selectors.get_deal_for_update_any_state(session, uuid.uuid4(), deal.id) is None
