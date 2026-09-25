"""The hourly snapshot sweep — ADR-0020 §2, `docs/insights/metrics.md` §8, data-model.md §5.

Captured state has no source history to derive from later, so it is recorded once an hour, for
every organization's **current local day**, and the last capture of a day is that day's value.
Discovery is cross-tenant (which organizations exist) and runs on the narrow BYPASSRLS
`opsgrids_outbox` role, reading only `org_profile(organization_id, timezone)` — the exact column
grant `docs/insights/data-model.md` §1 names, and the same split every other cross-tenant
discovery in this platform uses (CLAUDE.md: the outbox role "may read and update outbox and
nothing else" plus whatever narrow, named exceptions a service's own migration grants). Each
organization's own capture then runs inside its own tenant-bound transaction.

**I1 registered no capture** (`pipeline_value`, `open_deals`, `weighted_pipeline` and
`customers` still await I3/I4's facts). I6 registers `open_tasks`, local and client-free
(`app/open_tasks_capture.py`); I5 registers `stock_value`/`low_stock_items`/
`out_of_stock_items`, a pulled read from operations that needs a live `httpx.AsyncClient` and so
is constructed and appended to `SNAPSHOT_CAPTURES` at process-wiring time instead of living in
this module's own static tuple (`app/stock_capture.py`, `app/main.py`/`app/worker.py`). This
module still runs the whole loop — discovery, one transaction per organization, the write
through `app/services.upsert_daily_snapshot` — whether or not a capture is registered. A slice
that adds a local capture appends one `SnapshotCapture` to the tuple below, in the same commit
as the fact table it computes from; nothing else changes.
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
from app.open_tasks_capture import OpenTasksCapture
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


#: I6's `open_tasks` needs no client and so is registered here directly. I5's stock captures
#: (`app/stock_capture.py:StockCapture`) are appended at wiring time instead — see the module
#: docstring. Append-only: I3/I4 add their own local captures the same way `OpenTasksCapture`
#: does.
SNAPSHOT_CAPTURES: tuple[SnapshotCapture, ...] = (OpenTasksCapture(),)

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
