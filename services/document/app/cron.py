"""The purge sweep for document (ADR-0009 §8, and its 2026-09-23 addendum's §9). Runs as its
own process (`bin/cron`).

**Why this exists and when it had to.** ADR-0009 §8 makes the 30-day retention window a
promise, not a fact, until something actually acts on it: "the retention window is only real
if the sweep exists... that is a date, not a preference." The document service reached the
edge on 2026-09-23 (`infrastructure/edge/compose.prod.yml`'s `document` container, behind the
`storefront` profile); the deadline this module exists to meet is thirty days after that.

**Same claim-and-drain shape as `approval/app/cron.py`'s SLA sweep, `subscription/app/cron.py`'s
dunning sweep and `scheduler/app/cron.py`'s due-job sweep** — ADR-0002's split, applied here a
fourth time: **discovery** runs on the BYPASSRLS outbox role (`get_outbox_sessionmaker`),
because it exists to find out *which organizations* have assets due for purging, and there is
no tenant to bind while asking; as the NOBYPASSRLS app role it would select nothing, and every
soft-deleted asset in the platform would sit world-readable past its window forever, looking
exactly like a healthy, idle sweep. **Every write** runs on the app role inside
`session_scope(organization_id=...)`, under the tenant policy — the bypass buys discovery,
never authority.

`SELECT ... FOR UPDATE SKIP LOCKED` in the purge transaction (`app/selectors.py`'s
`get_asset_for_purge_sweep`) is what makes two concurrent sweepers, and a sweeper racing an
ordinary `?purge=true` request, safe against the same row: the loser of a race skips an asset
another replica (or another request) already holds, and re-checks the retention window under
the lock against its own, later clock read rather than trusting discovery's now-possibly-stale
one — an asset already purged, or undeleted, in the gap between discovery and here is a skip,
never a second purge or a wrongful one.

**`run()` never lets one failure end the process**, the same three-layer shape
`scheduler/app/cron.py`'s `Sweep.run` uses: `_purge` catches a failure on *one asset* and
counts it, so the rest of the tick still runs; a failure raised out of `tick()` itself
(discovery's own query — a DB restart, a deadlock) is caught in `run()` and backed off exactly
like the outbox relay's own failure backoff, never propagated. An idle tick — nothing due,
nothing orphaned — backs off the same way the relay's idle poll does, so a quiet platform is
not a constant stream of empty discovery queries.

**The orphan pass (ADR-0009 §9 addendum) is a second, independent job sharing this loop**,
not a separate process: both are "find bucket residue nobody has a use for and remove it",
both run on the same schedule, and a box that needs one almost certainly wants the other. It
is off by default (`orphan_sweep_enabled`) and, even switched on, defaults to reporting rather
than deleting (`orphan_sweep_dry_run`) — see the module docstring on `_sweep_orphans` for why
an automated, bucket-wide delete earns that much caution before it is trusted with a real
`DeleteObject`.
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

from app import domain, selectors, services
from app.config import settings
from app.storage import ObjectStore, OrphanScanner, get_object_store, get_orphan_scanner
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
from platform_core.supervise import register_sweep_health

log = get_logger("document.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the sweep can be driven without a process-wide engine — the same
#: seam `approval/app/cron.py`'s `WorkScope` documents.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    purged: int = 0
    skipped: int = 0
    #: One asset's own purge raising is not the rest of the tick's problem (the identical
    #: reasoning `approval/app/cron.py`'s `SweepReport.failed` carries) — counted here rather
    #: than left to propagate, because an exception out of `tick()` would kill the whole sweep
    #: process over one poisoned row.
    failed: int = 0
    #: The orphan pass's own counters. `orphan_considered` is candidates old enough to look at
    #: (age-filtered, before the row lookup); `orphan_deleted` and `orphan_would_delete` are
    #: mutually exclusive across a single run — one or the other fills, never both, depending
    #: on `orphan_sweep_dry_run`.
    orphan_considered: int = 0
    orphan_deleted: int = 0
    orphan_would_delete: int = 0
    orphan_failed: int = 0

    @property
    def acted(self) -> int:
        return self.purged + self.orphan_deleted + self.orphan_would_delete


class PurgeSweep:
    """Drains due purges and, optionally, sweeps orphaned objects. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        store: ObjectStore,
        scanner: OrphanScanner,
        retention: timedelta,
        batch_size: int = 200,
        poll_interval: float = 300.0,
        max_backoff: float = 3600.0,
        max_idle_interval: float = 1800.0,
        orphan_enabled: bool = False,
        orphan_dry_run: bool = True,
        orphan_min_age: timedelta = timedelta(hours=24),
        orphan_max_deletes: int = 100,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._store = store
        self._scanner = scanner
        self._retention = retention
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._max_idle_interval = max_idle_interval
        self._orphan_enabled = orphan_enabled
        self._orphan_dry_run = orphan_dry_run
        self._orphan_min_age = orphan_min_age
        self._orphan_max_deletes = orphan_max_deletes
        self._stop = False
        #: `sweep_alive` / `sweep_consecutive_failures` / `sweep_last_success_timestamp_seconds`
        #: on `/metrics` when this runs inside the API — see `platform_core.supervise.SweepHealth`.
        self.health = register_sweep_health("document.sweep")

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info(
            "document.sweep.started",
            poll_interval=self._poll_interval,
            retention_days=self._retention.days,
            orphan_enabled=self._orphan_enabled,
            orphan_dry_run=self._orphan_dry_run,
        )
        # `min(poll_interval, max_backoff)`: the first retry after a failed tick must not wait a
        # whole (possibly larger-than-the-cap) poll interval — same fix as `GuardedSweep`.
        initial_backoff = min(self._poll_interval, self._max_backoff)
        backoff = initial_backoff
        # Below poll_interval, max() clamps this to poll_interval every pass — the same
        # opt-out `OutboxRelay.run()` and `scheduler/app/cron.py`'s `Sweep.run` document.
        idle_cap = max(self._max_idle_interval, self._poll_interval)
        idle = self._poll_interval
        while not self._stop:
            try:
                report = await self.tick()
            except Exception:
                # Discovery itself failed — a DB restart, a deadlock, a connection blip. Not
                # one asset's problem, all of them: logged and backed off exactly like the
                # relay's own failure path, never propagated.
                self.health.record_failure()
                log.exception(
                    "document.sweep.tick_failed",
                    consecutive_failures=self.health.consecutive_failures,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            backoff = initial_backoff
            if report.acted or report.skipped or report.failed or report.orphan_failed:
                log.info(
                    "document.sweep.pass",
                    considered=report.considered,
                    purged=report.purged,
                    skipped=report.skipped,
                    failed=report.failed,
                    orphan_considered=report.orphan_considered,
                    orphan_deleted=report.orphan_deleted,
                    orphan_would_delete=report.orphan_would_delete,
                    orphan_failed=report.orphan_failed,
                )
            # Progress — something was actually purged or removed — is the only thing that
            # earns an immediate re-run (a backlog larger than `batch_size` drains back to back).
            # **Considered is not progress.** The first version of this loop keyed the idle sleep
            # on "nothing considered", so a tick that found work and got nowhere — one asset
            # whose object-store delete fails every time, or rows another sweeper's lock keeps
            # skipping — re-ran at once, with no sleep, forever: ~700 ticks a second of discovery
            # queries and failing deletes, in a loop that now shares an event loop and a
            # connection pool with the API's own request traffic (round 3, MEDIUM). Anything
            # short of progress backs off exactly like an idle tick: doubling, capped.
            progressed = report.purged > 0 or report.orphan_deleted > 0
            if report.failed and not progressed:
                # Every attempt this tick failed: the sweep is running and going nowhere, which is
                # the state an alert on `sweep_consecutive_failures` exists to catch.
                self.health.record_failure()
            else:
                self.health.record_success()
            if progressed:
                idle = self._poll_interval
            else:
                await asyncio.sleep(idle)
                idle = min(idle * 2, idle_cap)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: purge everything past its retention window, then sweep orphans.

        `now` exists **only** for tests, the same seam `approval/app/cron.py`'s `tick` offers:
        production never passes it, so discovery always reads the database's own clock. Every
        write this tick makes still gets its own, later instant from inside its own
        transaction (`app/services.purge_asset`'s `db_now`).
        """
        async with self._discovery_factory() as session, session.begin():
            if now is not None:
                at = now
            else:
                at = (await session.execute(select(func.now()))).scalar_one()
            cutoff = at - self._retention
            due = await selectors.due_purge_assets(session, cutoff=cutoff, limit=self._batch_size)

        report = SweepReport(considered=len(due))
        for organization_id, asset_id in due:
            await self._purge(organization_id, asset_id, report)

        if self._orphan_enabled:
            await self._sweep_orphans(at, report)
        return report

    async def _purge(
        self, organization_id: uuid.UUID, asset_id: uuid.UUID, report: SweepReport
    ) -> None:
        """Act on one asset, in its own tenant-bound transaction. Discovery orders every
        tick's candidates by `deleted_at` platform-wide, so an asset whose purge keeps raising
        is head-of-line on every tick until it is fixed — the identical concern
        `approval/app/cron.py`'s `_escalate` names, and the identical fix: caught, logged with
        identifiers a correlation needs and none a leak would, and counted rather than left to
        propagate."""
        try:
            async with self._work_scope(organization_id) as session:
                result = await services.purge_asset(
                    session,
                    self._store,
                    organization_id=organization_id,
                    asset_id=asset_id,
                    retention=self._retention,
                )
        except Exception:
            log.exception(
                "document.sweep.purge_failed",
                organization_id=str(organization_id),
                asset_id=str(asset_id),
            )
            report.failed += 1
            return
        if result.purged:
            report.purged += 1
        else:
            report.skipped += 1

    async def _sweep_orphans(self, now: datetime, report: SweepReport) -> None:
        """The bucket-side half of ADR-0009 §9: an object whose write order (`app/services.py`
        `create_asset`'s own docstring) left it stranded because the row insert that should
        have followed it never committed.

        **Why this is judged safe to automate, deliberately, rather than dry-run-only.** The
        one way this could go wrong — deleting an object whose row insert is genuinely still
        in flight — is bounded by `orphan_sweep_min_age_hours` (default 24h, ADR-0009 §9's own
        number): the gap between `store.put` succeeding and the row's `INSERT` committing is a
        single request's duration, not hours, so an object old enough to pass this filter and
        still have no row almost certainly never will. Two more independent backstops sit on
        top of that judgement rather than replacing it: `orphan_sweep_max_deletes_per_tick`
        caps the blast radius of a wrong discovery query on any single tick regardless of how
        many candidates it finds, and `orphan_sweep_dry_run` (on by default even once
        `orphan_sweep_enabled` is set) means the very first thing a newly turned-on sweep does
        is log what it *would* delete, not delete it — an operator reads a run or two of that
        before flipping the second flag.

        Every candidate is looked up by row existence **regardless of `deleted_at` or
        `purged_at`** (`selectors.existing_asset_keys`): a soft-deleted or already-purged
        asset still has a row, so it is never a candidate here — this pass only ever touches
        an object with **no row at all**, which is the one shape ADR-0009 §9 calls an orphan.
        """
        if not self._scanner.is_configured():
            log.debug("document.sweep.orphan_pass_unconfigured")
            return

        try:
            objects = await self._scanner.list_objects()
        except Exception:
            log.exception("document.sweep.orphan_list_failed")
            report.orphan_failed += 1
            return

        cutoff = now - self._orphan_min_age
        candidates: list[tuple[str, datetime, uuid.UUID, uuid.UUID]] = []
        for key, last_modified, _size in objects:
            if last_modified > cutoff:
                continue
            parsed = domain.parse_storage_key(key)
            if parsed is None:
                # An object this service never wrote the key shape for — a future service
                # sharing the bucket, an operator's `mc cp`. Left alone rather than guessed at.
                continue
            organization_id, asset_id = parsed
            candidates.append((key, last_modified, organization_id, asset_id))

        # Oldest first, capped: fairness across ticks the same way the purge pass gets it for
        # free from `ORDER BY deleted_at` — an unbounded discovery query must not translate
        # into an unbounded amount of work, on a single tick, no matter how large the bucket.
        candidates.sort(key=lambda c: c[1])
        candidates = candidates[: self._orphan_max_deletes]
        report.orphan_considered = len(candidates)
        if not candidates:
            return

        async with self._discovery_factory() as session, session.begin():
            existing = await selectors.existing_asset_keys(
                session, [(org, asset) for _key, _lm, org, asset in candidates]
            )

        for key, _last_modified, organization_id, asset_id in candidates:
            if (organization_id, asset_id) in existing:
                continue  # a real, referenced asset — never touched
            if self._orphan_dry_run:
                log.info(
                    "document.sweep.orphan_would_delete",
                    organization_id=str(organization_id),
                    asset_id=str(asset_id),
                    key=key,
                )
                report.orphan_would_delete += 1
                continue
            try:
                await self._scanner.delete(key, organization_id=organization_id)
            except Exception:
                log.exception(
                    "document.sweep.orphan_delete_failed",
                    organization_id=str(organization_id),
                    asset_id=str(asset_id),
                )
                report.orphan_failed += 1
                continue
            log.info(
                "document.sweep.orphan_deleted",
                organization_id=str(organization_id),
                asset_id=str(asset_id),
                key=key,
            )
            report.orphan_deleted += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumers make applies: started on a BYPASSRLS DSN by mistake, row-level
    # security would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role,
    # sees no rows, and every soft-deleted asset in the platform stays world-readable past its
    # retention window forever — the relay's silent stall, in the path that is the only thing
    # standing between ADR-0009 §8's promise and quota accounting drifting from reality.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = PurgeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        store=get_object_store(),
        scanner=get_orphan_scanner(),
        retention=timedelta(days=cfg.asset_purge_retention_days),
        orphan_enabled=cfg.orphan_sweep_enabled,
        orphan_dry_run=cfg.orphan_sweep_dry_run,
        orphan_min_age=timedelta(hours=cfg.orphan_sweep_min_age_hours),
        orphan_max_deletes=cfg.orphan_sweep_max_deletes_per_tick,
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
