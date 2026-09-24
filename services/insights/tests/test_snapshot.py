"""The snapshot sweep framework (ADR-0020 §2, `docs/insights/metrics.md` §8), against real
PostgreSQL. `SNAPSHOT_CAPTURES` is empty in production; every test here injects its own fake
capture."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.snapshot import SnapshotSweep

pytestmark = pytest.mark.integration


@dataclass
class FakeCapture:
    metric: str = "test_metric"
    calls: list[uuid.UUID] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        self.calls.append(organization_id)
        await services.upsert_daily_snapshot(
            session, organization_id=organization_id, metric=self.metric, dimension="",
            day=day, value=42, currency=None, captured_at=now,
        )


async def test_discovers_zero_organizations_and_does_nothing(
    discovery_sessionmaker: Any, work_scope: Any
) -> None:
    sweep = SnapshotSweep(
        poll_interval=3600.0, discovery_factory=discovery_sessionmaker, work_scope=work_scope
    )
    report = await sweep.tick()
    assert report.organizations_seen == 0
    assert report.captured == 0


async def test_captures_for_every_discovered_organization(
    session: AsyncSession, org_id: uuid.UUID, work_scope: Any, discovery_sessionmaker: Any
) -> None:
    from app.models import OrgProfile

    async with work_scope(org_id) as scoped_session:
        scoped_session.add(
            OrgProfile(
                organization_id=org_id, timezone="Asia/Kolkata", currency="INR",
                fiscal_year_start_month=4, source_updated_at=datetime.now(UTC),
            )
        )

    capture = FakeCapture()
    sweep = SnapshotSweep(
        poll_interval=3600.0, discovery_factory=discovery_sessionmaker, work_scope=work_scope,
        captures=(capture,),
    )
    report = await sweep.tick()
    assert report.organizations_seen == 1
    assert report.captured == 1
    assert capture.calls == [org_id]

    from sqlalchemy import select

    from app.models import DailySnapshot

    row = (
        await session.execute(
            select(DailySnapshot).where(DailySnapshot.organization_id == org_id)
        )
    ).scalar_one()
    assert row.value == 42
    assert row.metric == "test_metric"


async def test_a_failing_capture_does_not_stop_the_sweep(
    org_id: uuid.UUID, work_scope: Any, discovery_sessionmaker: Any
) -> None:
    from app.models import OrgProfile

    async with work_scope(org_id) as scoped_session:
        scoped_session.add(
            OrgProfile(
                organization_id=org_id, timezone="Asia/Kolkata", currency="INR",
                fiscal_year_start_month=4, source_updated_at=datetime.now(UTC),
            )
        )

    class BrokenCapture:
        metric = "broken"

        async def capture(self, session: AsyncSession, **kwargs: Any) -> None:
            raise RuntimeError("simulated capture failure")

    good = FakeCapture(metric="good")
    sweep = SnapshotSweep(
        poll_interval=3600.0, discovery_factory=discovery_sessionmaker, work_scope=work_scope,
        captures=(BrokenCapture(), good),
    )
    report = await sweep.tick()
    assert report.failed == 1
    assert report.captured == 1
    assert good.calls == [org_id]
