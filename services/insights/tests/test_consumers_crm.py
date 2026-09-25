"""Consumer folds for `crm.pipeline.deal.*` (I3) and `crm.lead.*`/`crm.book.company.*` (I4),
`docs/insights/events.md` §1: `already_processed` before acting, `mark_processed` in the same
transaction, and every write version-guarded (data-model.md §1.1) — a redelivered or reordered
event never regresses a fact.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers
from app.models import CompanyFact, DealFact, LeadFact, ProcessedEvent

pytestmark = pytest.mark.integration


def _envelope(
    *,
    event_id: uuid.UUID,
    event_type: str,
    organization_id: uuid.UUID,
    occurred_at: datetime,
    **payload: object,
) -> dict[str, object]:
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "organization_id": str(organization_id),
        "occurred_at": occurred_at.isoformat(),
        "payload": {"organization_id": str(organization_id), **payload},
    }


@pytest.fixture(autouse=True)
def _bind_test_session_scope(work_scope) -> None:
    consumers.use_session_scope(work_scope)
    yield
    consumers.use_session_scope(consumers._default_session_scope)


class TestDealFacts:
    async def test_deal_created_folds_into_deal_facts(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        deal_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        company_id = uuid.uuid4()
        await consumers.on_deal_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                deal_id=str(deal_id),
                name="Fleet rollout",
                stage="lead",
                value_minor=500_000,
                currency="INR",
                owner_id=str(owner_id),
                company_id=str(company_id),
                close_date="2026-12-01",
                probability=20,
                closed_at=None,
                created_at="2026-09-01T10:00:00+00:00",
            )
        )
        row = (
            await admin_session.execute(select(DealFact).where(DealFact.id == deal_id))
        ).scalar_one()
        assert row.name == "Fleet rollout"
        assert row.owner_ref == owner_id
        assert row.company_ref == company_id
        assert row.closed_at is None

    async def test_redelivery_is_idempotent(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        event_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        envelope = _envelope(
            event_id=event_id,
            event_type=consumers.EVT_DEAL_CREATED,
            organization_id=org_id,
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            deal_id=str(deal_id),
            name="Deal A",
            stage="lead",
            value_minor=100_00,
            currency="INR",
            owner_id=str(uuid.uuid4()),
            company_id=str(uuid.uuid4()),
            close_date="2026-12-01",
            probability=20,
            closed_at=None,
            created_at="2026-09-01T10:00:00+00:00",
        )
        await consumers.on_deal_created(envelope)
        await consumers.on_deal_created(envelope)  # redelivered, same event_id

        processed = (
            await admin_session.execute(
                select(func.count())
                .select_from(ProcessedEvent)
                .where(ProcessedEvent.event_id == event_id)
            )
        ).scalar_one()
        assert processed == 1
        facts = (
            (await admin_session.execute(select(DealFact).where(DealFact.id == deal_id)))
            .scalars()
            .all()
        )
        assert len(facts) == 1

    async def test_an_older_version_never_overwrites_a_newer_one(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        deal_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        company_id = uuid.uuid4()
        newer = datetime(2026, 9, 10, tzinfo=UTC)
        older = datetime(2026, 9, 1, tzinfo=UTC)

        await consumers.on_deal_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_CREATED,
                organization_id=org_id,
                occurred_at=newer,
                deal_id=str(deal_id),
                name="Newer name",
                stage="qualified",
                value_minor=200_00,
                currency="INR",
                owner_id=str(owner_id),
                company_id=str(company_id),
                close_date="2026-12-01",
                probability=40,
                closed_at=None,
                created_at=newer.isoformat(),
            )
        )
        # A reordered `updated` event with an *older* version must not regress the row.
        await consumers.on_deal_updated(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_UPDATED,
                organization_id=org_id,
                occurred_at=older,
                deal_id=str(deal_id),
                changed=["name"],
                name="Stale name",
                stage="qualified",
                value_minor=200_00,
                currency="INR",
                owner_id=str(owner_id),
                company_id=str(company_id),
                close_date="2026-12-01",
                probability=40,
                closed_at=None,
            )
        )
        row = (
            await admin_session.execute(select(DealFact).where(DealFact.id == deal_id))
        ).scalar_one()
        assert row.name == "Newer name"

    async def test_stage_changed_is_partial_and_update_only(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        deal_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        company_id = uuid.uuid4()
        await consumers.on_deal_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                deal_id=str(deal_id),
                name="Deal B",
                stage="negotiation",
                value_minor=300_00,
                currency="INR",
                owner_id=str(owner_id),
                company_id=str(company_id),
                close_date="2026-12-01",
                probability=70,
                closed_at=None,
                created_at="2026-09-01T10:00:00+00:00",
            )
        )
        await consumers.on_deal_stage_changed(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_STAGE_CHANGED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 5, tzinfo=UTC),
                deal_id=str(deal_id),
                **{"from": "negotiation", "to": "closed_won"},
                name="Deal B",
                value_minor=300_00,
                currency="INR",
                owner_id=str(owner_id),
                probability=100,
                closed_at="2026-09-05T00:00:00+00:00",
            )
        )
        row = (
            await admin_session.execute(select(DealFact).where(DealFact.id == deal_id))
        ).scalar_one()
        assert row.stage == "closed_won"
        assert row.closed_at is not None
        # The partial event never touched company_ref — proving it updates, never inserts.
        assert row.company_ref == company_id

    async def test_an_orphan_stage_change_is_logged_and_acked_not_raised(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        """The creating event was lost: the row is not there. This must not raise — it logs
        `insights.fold.orphan_transition` and acks (data-model.md §1.1)."""
        deal_id = uuid.uuid4()
        await consumers.on_deal_stage_changed(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_STAGE_CHANGED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                deal_id=str(deal_id),
                **{"from": "lead", "to": "qualified"},
                name="Ghost deal",
                value_minor=0,
                currency="INR",
                owner_id=str(uuid.uuid4()),
                probability=40,
                closed_at=None,
            )
        )
        rows = (
            (await admin_session.execute(select(DealFact).where(DealFact.id == deal_id)))
            .scalars()
            .all()
        )
        assert rows == []

    async def test_deal_deleted_marks_deleted_at(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        deal_id = uuid.uuid4()
        await consumers.on_deal_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                deal_id=str(deal_id),
                name="Deal C",
                stage="lead",
                value_minor=0,
                currency="INR",
                owner_id=str(uuid.uuid4()),
                company_id=str(uuid.uuid4()),
                close_date="2026-12-01",
                probability=20,
                closed_at=None,
                created_at="2026-09-01T10:00:00+00:00",
            )
        )
        await consumers.on_deal_deleted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_DEAL_DELETED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
                deal_id=str(deal_id),
            )
        )
        row = (
            await admin_session.execute(select(DealFact).where(DealFact.id == deal_id))
        ).scalar_one()
        assert row.deleted_at is not None


class TestLeadFacts:
    async def test_lead_created_folds_into_lead_facts(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        lead_id = uuid.uuid4()
        await consumers.on_lead_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_LEAD_CREATED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                lead_id=str(lead_id),
                source="form",
                status="new",
                owner_id=None,
            )
        )
        row = (
            await admin_session.execute(select(LeadFact).where(LeadFact.id == lead_id))
        ).scalar_one()
        assert row.status == "new"
        assert row.source == "form"
        assert row.owner_ref is None

    async def test_lead_converted_is_partial_and_update_only(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        lead_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        await consumers.on_lead_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_LEAD_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                lead_id=str(lead_id),
                source="manual",
                status="qualified",
                owner_id=str(owner_id),
            )
        )
        converted_at = datetime(2026, 9, 3, tzinfo=UTC)
        await consumers.on_lead_converted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_LEAD_CONVERTED,
                organization_id=org_id,
                occurred_at=converted_at + timedelta(seconds=1),
                lead_id=str(lead_id),
                company_id=str(uuid.uuid4()),
                contact_id=None,
                deal_id=str(uuid.uuid4()),
                owner_id=str(owner_id),
                converted_at=converted_at.isoformat(),
            )
        )
        row = (
            await admin_session.execute(select(LeadFact).where(LeadFact.id == lead_id))
        ).scalar_one()
        assert row.status == "converted"
        assert row.converted_at is not None
        # The original source survives — the converted fold never touched it.
        assert row.source == "manual"

    async def test_an_orphan_conversion_is_logged_and_acked_not_raised(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        lead_id = uuid.uuid4()
        await consumers.on_lead_converted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_LEAD_CONVERTED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                lead_id=str(lead_id),
                company_id=str(uuid.uuid4()),
                contact_id=None,
                deal_id=str(uuid.uuid4()),
                owner_id=str(uuid.uuid4()),
                converted_at=datetime.now(UTC).isoformat(),
            )
        )
        rows = (
            (await admin_session.execute(select(LeadFact).where(LeadFact.id == lead_id)))
            .scalars()
            .all()
        )
        assert rows == []


class TestCompanyFacts:
    async def test_company_created_folds_into_company_facts(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        company_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        await consumers.on_company_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_COMPANY_CREATED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                company_id=str(company_id),
                name="Northwind Logistics",
                status="customer",
                owner_id=str(owner_id),
                created_at="2026-09-01T10:00:00+00:00",
            )
        )
        row = (
            await admin_session.execute(select(CompanyFact).where(CompanyFact.id == company_id))
        ).scalar_one()
        assert row.name == "Northwind Logistics"
        assert row.status == "customer"

    async def test_company_deleted_marks_deleted_at(
        self, org_id: uuid.UUID, admin_session: AsyncSession
    ) -> None:
        company_id = uuid.uuid4()
        await consumers.on_company_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_COMPANY_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                company_id=str(company_id),
                name="Meridian Steelworks",
                status="prospect",
                owner_id=str(uuid.uuid4()),
                created_at="2026-09-01T10:00:00+00:00",
            )
        )
        await consumers.on_company_deleted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_COMPANY_DELETED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
                company_id=str(company_id),
            )
        )
        row = (
            await admin_session.execute(select(CompanyFact).where(CompanyFact.id == company_id))
        ).scalar_one()
        assert row.deleted_at is not None
