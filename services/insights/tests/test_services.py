"""`app/services.py`'s version-guarded upserts (`docs/insights/data-model.md` §1.1), against
real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.selectors import get_org_profile

pytestmark = pytest.mark.integration


async def test_upsert_creates_a_new_row(session: AsyncSession, org_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    applied = await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="Asia/Kolkata",
        currency="INR",
        fiscal_year_start_month=4,
        source_updated_at=now,
    )
    assert applied is True
    profile = await get_org_profile(session, org_id)
    assert profile is not None
    assert profile.timezone == "Asia/Kolkata"
    assert profile.currency == "INR"


async def test_a_newer_version_overwrites_an_older_one(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    t0 = datetime.now(UTC)
    await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="Asia/Kolkata",
        currency="INR",
        fiscal_year_start_month=4,
        source_updated_at=t0,
    )
    t1 = t0 + timedelta(seconds=1)
    applied = await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="America/New_York",
        currency="USD",
        fiscal_year_start_month=1,
        source_updated_at=t1,
    )
    assert applied is True
    profile = await get_org_profile(session, org_id)
    assert profile is not None
    assert profile.timezone == "America/New_York"
    assert profile.currency == "USD"


async def test_an_older_version_is_dropped_as_stale(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    t1 = datetime.now(UTC)
    t0 = t1 - timedelta(seconds=1)
    await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="America/New_York",
        currency="USD",
        fiscal_year_start_month=1,
        source_updated_at=t1,
    )
    applied = await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="Asia/Kolkata",
        currency="INR",
        fiscal_year_start_month=4,
        source_updated_at=t0,
    )
    assert applied is False
    profile = await get_org_profile(session, org_id)
    assert profile is not None
    assert profile.timezone == "America/New_York"  # unchanged


async def test_a_replay_of_the_same_version_is_applied_not_dropped(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """data-model.md §1.1: `apply iff incoming.source_updated_at >= stored.source_updated_at` —
    equal is a legitimate replay, not staleness."""
    t0 = datetime.now(UTC)
    await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="Asia/Kolkata",
        currency="INR",
        fiscal_year_start_month=4,
        source_updated_at=t0,
    )
    applied = await services.upsert_org_profile(
        session,
        organization_id=org_id,
        timezone="Asia/Kolkata",
        currency="INR",
        fiscal_year_start_month=4,
        source_updated_at=t0,
    )
    assert applied is True


async def test_daily_snapshot_upsert_is_last_write_wins(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    from datetime import date

    from sqlalchemy import select

    from app.models import DailySnapshot

    day = date(2026, 9, 25)
    await services.upsert_daily_snapshot(
        session,
        organization_id=org_id,
        metric="pipeline_value",
        dimension="INR",
        day=day,
        value=1000,
        currency="INR",
        captured_at=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    )
    await services.upsert_daily_snapshot(
        session,
        organization_id=org_id,
        metric="pipeline_value",
        dimension="INR",
        day=day,
        value=2000,
        currency="INR",
        captured_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
    )
    row = (
        await session.execute(
            select(DailySnapshot).where(
                DailySnapshot.organization_id == org_id, DailySnapshot.day == day
            )
        )
    ).scalar_one()
    assert row.value == 2000


async def test_metric_coverage_upsert(session: AsyncSession, org_id: uuid.UUID) -> None:
    from datetime import date

    await services.record_metric_coverage(
        session,
        organization_id=org_id,
        metric="pipeline_value",
        history_from=date(2026, 10, 2),
        basis="first_capture",
    )
    from sqlalchemy import select

    from app.models import MetricCoverage

    row = (
        await session.execute(
            select(MetricCoverage).where(MetricCoverage.organization_id == org_id)
        )
    ).scalar_one()
    assert row.history_from == date(2026, 10, 2)
    assert row.basis == "first_capture"
