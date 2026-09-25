"""The hourly snapshot sweep — ADR-0020 §2, `docs/insights/metrics.md` §8, data-model.md §5.

Captured state has no source history to derive from later, so it is recorded once an hour, for
every organization's **current local day**, and the last capture of a day is that day's value.
Discovery is cross-tenant (which organizations exist) and runs on the narrow BYPASSRLS
`opsgrids_outbox` role, reading only `org_profile(organization_id, timezone)` — the exact column
grant `docs/insights/data-model.md` §1 names, and the same split every other cross-tenant
discovery in this platform uses (CLAUDE.md: the outbox role "may read and update outbox and
nothing else" plus whatever narrow, named exceptions a service's own migration grants). Each
organization's own capture then runs inside its own tenant-bound transaction.

**I3/I4 register the first four captures**: `pipeline_value`, `open_deals` and
`weighted_pipeline` from `deal_facts`, and `customers` from `company_facts`.
`stock_value`/`low_stock_items`/`out_of_stock_items` still wait on I5's pulled read from
operations. Each capture also records `metric_coverage` the moment its **first** row for an
organization lands (`basis="first_capture"`, `docs/insights/metrics.md` §3) — nothing else
computes that row for a snapshot metric, so the capture is the one place it can happen.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, services
from app.models import CompanyFact, DealFact, MetricCoverage, OrgProfile
from app.selectors import local_today
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("insights.snapshot")


async def _record_first_capture_coverage(
    session: AsyncSession, *, organization_id: uuid.UUID, metric: str, day: date
) -> None:
    """`metric_coverage` for a snapshot metric is "the first day it was captured"
    (`docs/insights/metrics.md` §3) — written once, the first time this organization gets a row
    for this metric, and never touched again (a coverage date only ever moves earlier than
    "now", never later)."""
    existing = await session.get(
        MetricCoverage, {"organization_id": organization_id, "metric": metric}
    )
    if existing is not None:
        return
    await services.record_metric_coverage(
        session,
        organization_id=organization_id,
        metric=metric,
        history_from=day,
        basis="first_capture",
    )


class SnapshotCapture(Protocol):
    """One metric's hourly capture (`docs/insights/metrics.md` §8). `metric` is the
    `daily_snapshots.metric` value it writes; `capture` runs inside the sweep's own
    tenant-bound transaction for one organization and calls
    `app/services.upsert_daily_snapshot` itself — from local facts (I3/I4/I6) or a pulled read
    (I5's `GET /internal/v1/insights/snapshot` on operations)."""

    metric: str

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None: ...


#: ORG-only, non-deleted, non-closed deals — the source rows every deal-facts capture below
#: reads (`docs/insights/metrics.md` §8, data-model.md §5's open-pipeline index).
def _open_deals_stmt(organization_id: uuid.UUID) -> Any:
    return select(DealFact.currency, DealFact.value_minor, DealFact.probability).where(
        DealFact.organization_id == organization_id,
        DealFact.deleted_at.is_(None),
        DealFact.stage.notin_(("closed_won", "closed_lost")),
    )


class OpenDealsCapture:
    """`open_deals` (I3): a plain count, no currency dimension."""

    metric = "open_deals"

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        count = (
            await session.execute(
                select(func.count())
                .select_from(DealFact)
                .where(
                    DealFact.organization_id == organization_id,
                    DealFact.deleted_at.is_(None),
                    DealFact.stage.notin_(("closed_won", "closed_lost")),
                )
            )
        ).scalar_one()
        await services.upsert_daily_snapshot(
            session,
            organization_id=organization_id,
            metric=self.metric,
            dimension="",
            day=day,
            value=count,
            currency=None,
            captured_at=now,
        )
        await _record_first_capture_coverage(
            session, organization_id=organization_id, metric=self.metric, day=day
        )


class PipelineValueCapture:
    """`pipeline_value` (I3): `Σ value_minor` of open deals, one row per currency
    (`docs/insights/metrics.md` §8) — never summed across currencies."""

    metric = "pipeline_value"

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        rows = (
            await session.execute(
                select(DealFact.currency, func.coalesce(func.sum(DealFact.value_minor), 0))
                .where(
                    DealFact.organization_id == organization_id,
                    DealFact.deleted_at.is_(None),
                    DealFact.stage.notin_(("closed_won", "closed_lost")),
                )
                .group_by(DealFact.currency)
            )
        ).all()
        for currency, total in rows:
            await services.upsert_daily_snapshot(
                session,
                organization_id=organization_id,
                metric=self.metric,
                dimension=currency,
                day=day,
                value=int(total),
                currency=currency,
                captured_at=now,
            )
        if rows:
            await _record_first_capture_coverage(
                session, organization_id=organization_id, metric=self.metric, day=day
            )


class WeightedPipelineCapture:
    """`weighted_pipeline` (I3): `Σ round_half_even(value_minor * probability / 100)` per open
    deal, grouped by currency — `domain.weighted_pipeline_minor`'s own rule, computed in Python
    because Postgres's `round(numeric)` rounds half away from zero (`docs/insights/metrics.md`
    §4.2)."""

    metric = "weighted_pipeline"

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        rows = (await session.execute(_open_deals_stmt(organization_id))).all()
        by_currency: dict[str, int] = {}
        for currency, value_minor, probability in rows:
            by_currency[currency] = by_currency.get(currency, 0) + domain.weighted_pipeline_minor(
                value_minor, probability
            )
        for currency, total in by_currency.items():
            await services.upsert_daily_snapshot(
                session,
                organization_id=organization_id,
                metric=self.metric,
                dimension=currency,
                day=day,
                value=total,
                currency=currency,
                captured_at=now,
            )
        if rows:
            await _record_first_capture_coverage(
                session, organization_id=organization_id, metric=self.metric, day=day
            )


class CustomersCapture:
    """`customers` (I4): a plain count of `company_facts` at `status = 'customer'`."""

    metric = "customers"

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        count = (
            await session.execute(
                select(func.count())
                .select_from(CompanyFact)
                .where(
                    CompanyFact.organization_id == organization_id,
                    CompanyFact.deleted_at.is_(None),
                    CompanyFact.status == "customer",
                )
            )
        ).scalar_one()
        await services.upsert_daily_snapshot(
            session,
            organization_id=organization_id,
            metric=self.metric,
            dimension="",
            day=day,
            value=count,
            currency=None,
            captured_at=now,
        )
        await _record_first_capture_coverage(
            session, organization_id=organization_id, metric=self.metric, day=day
        )


#: I3 registers `open_deals`, `pipeline_value` and `weighted_pipeline`; I4 adds `customers`.
#: `stock_value`, `low_stock_items` and `out_of_stock_items` still wait on I5's pulled read.
SNAPSHOT_CAPTURES: tuple[SnapshotCapture, ...] = (
    OpenDealsCapture(),
    PipelineValueCapture(),
    WeightedPipelineCapture(),
    CustomersCapture(),
)

#: Same shape as `app.backfill.WorkScope` — defined again here, not imported, because
#: `app.snapshot` and `app.backfill` are independent loops that happen to share a process
#: (`app/main.py`), the same "defined again, not imported" rule `app.consumers` follows for
#: `app.cron`'s `WorkScope` elsewhere in this platform.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SnapshotTickReport:
    organizations_seen: int = 0
    captured: int = 0
    failed: int = 0


class SnapshotSweep(GuardedSweep):
    def __init__(
        self,
        *,
        poll_interval: float,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        captures: tuple[SnapshotCapture, ...] | None = None,
    ) -> None:
        super().__init__(poll_interval=poll_interval)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._captures = SNAPSHOT_CAPTURES if captures is None else captures

    @property
    def _log_name(self) -> str:
        return "insights.snapshot"

    async def on_tick_success(self, result: SnapshotTickReport) -> None:
        if result.captured or result.failed:
            log.info(
                "insights.snapshot.pass",
                organizations_seen=result.organizations_seen,
                captured=result.captured,
                failed=result.failed,
            )

    async def tick(self) -> SnapshotTickReport:
        now = datetime.now(UTC)
        async with self._discovery_factory() as session:
            rows = (
                await session.execute(select(OrgProfile.organization_id, OrgProfile.timezone))
            ).all()
        report = SnapshotTickReport(organizations_seen=len(rows))
        if not self._captures:
            return report

        for organization_id, timezone in rows:
            today = local_today(timezone)
            async with self._work_scope(organization_id) as session:
                for capture in self._captures:
                    try:
                        await capture.capture(
                            session, organization_id=organization_id, day=today, now=now
                        )
                        report.captured += 1
                    except Exception:
                        report.failed += 1
                        log.exception(
                            "insights.snapshot.capture_failed",
                            organization_id=str(organization_id),
                            metric=capture.metric,
                        )
        return report
