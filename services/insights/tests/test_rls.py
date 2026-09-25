"""Row-level security isolation (ADR-0002, `docs/insights/data-model.md` §1): another
organization's rows are invisible under a bound tenant, even with the application-level filter
removed — RLS as the net, not the filter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackfillRun,
    CompanyFact,
    DailySnapshot,
    DealFact,
    LeadFact,
    MetricCoverage,
    OrgProfile,
)

pytestmark = pytest.mark.integration


async def _seed_profile(session: AsyncSession, organization_id: uuid.UUID) -> None:
    session.add(
        OrgProfile(
            organization_id=organization_id,
            timezone="Asia/Kolkata",
            currency="INR",
            fiscal_year_start_month=4,
            source_updated_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def test_org_profile_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        await _seed_profile(admin_session, other_org)

    # Bound to org_id, a query with NO application-level filter must still see nothing of
    # other_org — the policy itself is the isolation.
    rows = (await session.execute(select(OrgProfile))).scalars().all()
    assert rows == []

    # The admin (BYPASSRLS) session proves the row is really there.
    admin_rows = (
        (
            await admin_session.execute(
                select(OrgProfile).where(OrgProfile.organization_id == other_org)
            )
        )
        .scalars()
        .all()
    )
    assert len(admin_rows) == 1


async def test_org_profile_write_is_confined_to_the_bound_tenant(
    session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    """`WITH CHECK` (ADR-0002 §3): a write naming another organization's id must be refused, not
    silently reassigned."""
    from sqlalchemy.exc import DBAPIError

    session.add(
        OrgProfile(
            organization_id=other_org,
            timezone="Asia/Kolkata",
            currency="INR",
            fiscal_year_start_month=4,
            source_updated_at=datetime.now(UTC),
        )
    )
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_daily_snapshots_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            DailySnapshot(
                organization_id=other_org,
                metric="pipeline_value",
                dimension="INR",
                day=date(2026, 9, 25),
                value=100,
                currency="INR",
                captured_at=datetime.now(UTC),
            )
        )

    rows = (await session.execute(select(DailySnapshot))).scalars().all()
    assert rows == []


async def test_metric_coverage_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            MetricCoverage(
                organization_id=other_org,
                metric="pipeline_value",
                history_from=None,
                basis="first_capture",
            )
        )

    rows = (await session.execute(select(MetricCoverage))).scalars().all()
    assert rows == []


async def test_deal_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            DealFact(
                organization_id=other_org,
                id=uuid.uuid4(),
                name="Someone else's deal",
                stage="lead",
                value_minor=100_00,
                currency="INR",
                probability=20,
                owner_ref=uuid.uuid4(),
                company_ref=uuid.uuid4(),
                close_date=date(2026, 12, 1),
                source_created_at=datetime.now(UTC),
                source_updated_at=datetime.now(UTC),
            )
        )

    rows = (await session.execute(select(DealFact))).scalars().all()
    assert rows == []


async def test_lead_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            LeadFact(
                organization_id=other_org,
                id=uuid.uuid4(),
                status="new",
                source="form",
                owner_ref=uuid.uuid4(),
                source_created_at=datetime.now(UTC),
                source_updated_at=datetime.now(UTC),
            )
        )

    rows = (await session.execute(select(LeadFact))).scalars().all()
    assert rows == []


async def test_company_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            CompanyFact(
                organization_id=other_org,
                id=uuid.uuid4(),
                name="Someone else's company",
                status="prospect",
                owner_ref=uuid.uuid4(),
                source_created_at=datetime.now(UTC),
                source_updated_at=datetime.now(UTC),
            )
        )

    rows = (await session.execute(select(CompanyFact))).scalars().all()
    assert rows == []


async def test_backfill_runs_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(
            BackfillRun(organization_id=other_org, entity="finance.invoices", kind="initial")
        )

    rows = (await session.execute(select(BackfillRun))).scalars().all()
    assert rows == []


async def test_unset_tenant_guc_yields_zero_rows_not_an_error(
    admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    """ADR-0002 §4: a missing GUC fails closed and quietly, on the admin engine reused without a
    tenant bound — this asserts the *shape* of that failure (empty, not an exception) so a
    regression that turned it into a 500 or an information leak would be caught here."""
    async with admin_session.begin():
        await _seed_profile(admin_session, org_id)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models import SCHEMA

    engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as unbound:
            # No set_config call at all — the GUC is unset on this fresh connection.
            rows = (await unbound.execute(select(OrgProfile))).scalars().all()
            assert rows == []
    finally:
        await engine.dispose()


async def test_processed_events_is_deliberately_unpolicied(session: AsyncSession) -> None:
    """ADR-0020 risk 8: `processed_events` carries no `organization_id`, so it is exempt from
    the policy loop rather than silently broken by one — this is the platform's own documented
    trade, verified rather than assumed."""
    result = await session.execute(
        text(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE relname = 'processed_events' AND relnamespace = 'insights'::regnamespace"
        )
    )
    assert result.scalar() is False
