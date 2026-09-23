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
(`now() - retention`) is computed from the same read.

`run()` never lets a single failure end the process. Three layers of it, from narrowest to
widest: `_fire`/`_purge` catch a failure on *one row or organization* and count it, so the rest
of the tick still runs; a failure raised out of `tick()` itself (discovery's own query — a DB
restart, a deadlock) is caught here in `run()` and backed off exactly like the outbox relay's own
failure backoff, rather than propagating — this process shares an `asyncio.gather` with the event
consumer (`app/worker.py`), so an uncaught exception here would take the consumer down with it,
over a fault that is very likely to have cleared by the next tick. An idle tick — nothing due,
nothing to purge — backs off the same way the relay's idle poll does, so a quiet platform is not a
constant stream of empty discovery queries.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    parked: int = 0
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
        max_backoff: float = 60.0,
        max_idle_interval: float = 30.0,
        retention: timedelta = services.SCHEDULED_JOB_RETENTION,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._max_idle_interval = max_idle_interval
        self._retention = retention
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info("scheduler.sweep.started", poll_interval=self._poll_interval)
        backoff = self._poll_interval
        # Below poll_interval, max() clamps this to poll_interval every pass — i.e. no idle
        # backoff at all, the same opt-out `OutboxRelay.run()` documents.
        idle_cap = max(self._max_idle_interval, self._poll_interval)
        idle = self._poll_interval
        while not self._stop:
            try:
                report = await self.tick()
            except Exception:
                # Discovery itself failed — a DB restart, a deadlock, a connection blip. Not
                # one row's problem, all of them: logged and backed off exactly like the
                # relay's own failure path, never propagated. This process shares an
                # `asyncio.gather` with the event consumer (`app/worker.py`); an uncaught
                # exception here takes that down too, over a fault very likely gone by the
                # next tick.
                log.exception("scheduler.sweep.tick_failed")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            backoff = self._poll_interval
            if report.fired or report.skipped or report.failed or report.purged or report.parked:
                log.info(
                    "scheduler.sweep.pass",
                    considered=report.considered,
                    fired=report.fired,
                    skipped=report.skipped,
                    purged=report.purged,
                    parked=report.parked,
                    failed=report.failed,
                )
            if report.considered == 0 and report.purged == 0:
                # Nothing was due and nothing was stale to purge: a genuinely idle tick.
                # Doubles each consecutive one, capped, and reset the moment either query
                # finds something — the same shape `OutboxRelay.run()`'s idle backoff uses,
                # including not sleeping at all on a productive tick: a backlog drains at
                # full speed rather than pacing itself at `poll_interval` per row.
                await asyncio.sleep(idle)
                idle = min(idle * 2, idle_cap)
            else:
                idle = self._poll_interval

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
                session, cutoff=at - self._retention, limit=self._batch_size
            )

        report = SweepReport(considered=len(due))
        for organization_id, job_id in due:
            await self._fire(organization_id, job_id, report)
        for organization_id in stale_orgs:
            await self._purge(organization_id, at - self._retention, report)
        return report

    async def _fire(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, report: SweepReport
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                result = await services.fire_job(
                    session, organization_id=organization_id, job_id=job_id
                )
        except Exception as exc:
            log.exception(
                "scheduler.sweep.fire_failed",
                organization_id=str(organization_id),
                job_id=str(job_id),
            )
            report.failed += 1
            await self._record_fire_failure(organization_id, job_id, str(exc), report)
            return
        if result.fired:
            report.fired += 1
        else:
            report.skipped += 1

    async def _record_fire_failure(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, error: str, report: SweepReport
    ) -> None:
        """A second, fresh transaction for the failure itself — `fire_job`'s own transaction
        already rolled back by the time `_fire` catches its exception, taking any attribute
        change on that attempt's `job` with it. If even *this* write fails (the database is
        genuinely gone, not just this one row's problem), it is logged and swallowed rather
        than re-raised into `tick()` — the outer failure is already counted and the process's
        one chance to record it should not itself become the reason the whole tick aborts.
        """
        try:
            async with self._work_scope(organization_id) as session:
                parked = await services.record_fire_failure(
                    session, organization_id=organization_id, job_id=job_id, error=error
                )
        except Exception:
            log.exception(
                "scheduler.sweep.record_fire_failure_failed",
                organization_id=str(organization_id),
                job_id=str(job_id),
            )
            return
        if parked:
            report.parked += 1

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
        retention=timedelta(days=cfg.scheduled_job_retention_days),
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
