"""The hourly snapshot sweep — ADR-0020 §2, `docs/insights/metrics.md` §8, data-model.md §5.

Captured state has no source history to derive from later, so it is recorded once an hour, for
every organization's **current local day**, and the last capture of a day is that day's value.
Discovery is cross-tenant (which organizations exist) and runs on the narrow BYPASSRLS
`opsgrids_outbox` role, reading only `org_profile(organization_id, timezone)` — the exact column
grant `docs/insights/data-model.md` §1 names, and the same split every other cross-tenant
discovery in this platform uses (CLAUDE.md: the outbox role "may read and update outbox and
nothing else" plus whatever narrow, named exceptions a service's own migration grants). Each
organization's own capture then runs inside its own tenant-bound transaction.

**I1 registers no capture** (`SNAPSHOT_CAPTURES` is empty): `pipeline_value`, `open_deals`,
`weighted_pipeline` and `customers` need I3/I4's facts, and `stock_value`/`low_stock_items`/
`out_of_stock_items` need I5's pulled read from operations. This module still runs the whole
loop — discovery, one transaction per organization, the write through
`app/services.upsert_daily_snapshot` — so the framework is proven and tested before any capture
exists to plug into it. A slice that adds a capture appends one `SnapshotCapture` here, in the
same commit as the fact table (or the pulled read) it computes from; nothing else changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OrgProfile
from app.selectors import local_today
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("insights.snapshot")


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


#: Empty in I1 — see the module docstring.
SNAPSHOT_CAPTURES: tuple[SnapshotCapture, ...] = ()

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
