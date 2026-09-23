"""The due-job sweep and the retention pass for scheduler (ADR-0015). Runs as its own process
(`bin/cron`; production runs it combined with the consumer via `bin/worker` — see `app/worker.py`).

Same claim-and-drain shape as `approval/app/cron.py`'s SLA sweep and `subscription/app/cron.py`'s
dunning sweep — ADR-0002's split, applied here a third time: **discovery** runs on the BYPASSRLS
outbox role (`get_outbox_sessionmaker`), because it exists to find out *which organizations* have
due work, and there is no tenant to bind while asking; as the NOBYPASSRLS app role it would select
nothing, and every due schedule in the platform would sit `pending` forever, looking exactly like
a healthy, idle sweep. **Every write** runs on the app role inside
`session_scope(organization_id=...)`, under the tenant policy — the bypass buys discovery, never
authority.

`SELECT ... FOR UPDATE SKIP LOCKED` in the firing transaction
(`app/selectors.py:get_job_for_firing`) is what makes two concurrent sweepers safe: the loser of a
race skips a row another replica already holds, and re-checks the row's own `status` under the
lock rather than trusting discovery's now-possibly-stale read — a job already fired in the gap
between discovery and here is a skip, never a second delivery.

One clock, read once per tick, and it is the database's: discovery evaluates `due_at <= now()`
against the database's own clock, read once at the top of `tick`; retention's own cutoff
(`now() - SCHEDULED_JOB_RETENTION`) is computed from the same read.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import selectors, services
from app.config import settings
from platform_core.db import (
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("scheduler.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the sweep can be driven without a process-wide engine — the same seam
#: `approval/app/cron.py`'s `WorkScope` documents.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    considered: int = 0
    fired: int = 0
    skipped: int = 0
    purged: int = 0
    #: One organization's own failure is not the rest of the tick's problem — see
    #: `EscalationSweep`'s identical reasoning in `approval/app/cron.py`. Counted here rather than
    #: left to propagate, because an exception out of `tick()` would kill the whole sweep process
    #: over one poisoned row.
    failed: int = 0


class Sweep:
    """Drains due jobs and prunes old fired ones, one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        batch_size: int = 200,
        poll_interval: float = 5.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info("scheduler.sweep.started", poll_interval=self._poll_interval)
        while not self._stop:
            report = await self.tick()
            if report.fired or report.skipped or report.failed or report.purged:
                log.info(
                    "scheduler.sweep.pass",
                    considered=report.considered,
                    fired=report.fired,
                    skipped=report.skipped,
                    purged=report.purged,
                    failed=report.failed,
                )
            await asyncio.sleep(self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: fire everything due, then purge fired rows past retention.

        `now` exists **only** for tests, the same seam `approval/app/cron.py`'s `tick` offers:
        production never passes it, so discovery always reads the database's own clock. Every
        write this tick makes still gets its own instant from inside its own transaction.
        """
        async with self._discovery_factory() as session, session.begin():
            if now is not None:
                at = now
            else:
                at = (await session.execute(select(func.now()))).scalar_one()
            due = await selectors.due_jobs(session, now=at, limit=self._batch_size)
            stale_orgs = await selectors.orgs_with_stale_fired_jobs(
                session, cutoff=at - services.SCHEDULED_JOB_RETENTION, limit=self._batch_size
            )

        report = SweepReport(considered=len(due))
        for organization_id, job_id in due:
            await self._fire(organization_id, job_id, report)
        for organization_id in stale_orgs:
            await self._purge(organization_id, at - services.SCHEDULED_JOB_RETENTION, report)
        return report

    async def _fire(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, report: SweepReport
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                result = await services.fire_job(
                    session, organization_id=organization_id, job_id=job_id
                )
        except Exception:
            log.exception(
                "scheduler.sweep.fire_failed",
                organization_id=str(organization_id),
                job_id=str(job_id),
            )
            report.failed += 1
            return
        if result.fired:
            report.fired += 1
        else:
            report.skipped += 1

    async def _purge(
        self, organization_id: uuid.UUID, cutoff: datetime, report: SweepReport
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                purged = await services.purge_stale_fired_jobs(
                    session, organization_id=organization_id, cutoff=cutoff
                )
        except Exception:
            log.exception("scheduler.sweep.purge_failed", organization_id=str(organization_id))
            report.failed += 1
            return
        report.purged += purged


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant.
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every due schedule in the platform stays pending forever.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = Sweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
