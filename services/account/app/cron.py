"""Periodic re-verification of already-verified email domains. Runs as its own process
(`bin/cron`), a distinct process from the API and the outbox relay for the same reason every
other sweep in this platform is: a poll loop must not share a pool, or a restart, with request
traffic.

**Why this exists.** `services.claim_domain`/`verify_domain` only ever ask DNS once, at the
moment ownership is proved. The TXT record that satisfied that one check can be removed the
same afternoon — deliberately, by mistake, or because a registrar migration silently dropped it
— and nothing before this sweep would ever notice. This is the "notice": it re-asks DNS for
verified domains on a schedule and records what it finds.

**Discovery runs on the SELECT-only directory role, at no new cost.** `opsgrids_directory`
already reads `org_domains` for the internal SSO lookup (`api/internal.py`), so
`selectors.due_for_recheck`'s `SELECT ... WHERE verified_at IS NOT NULL ORDER BY
last_checked_at NULLS FIRST LIMIT :n` needs no new grant — the same reasoning
`services/storefront/app/cron.py`'s own sweep states for its own discovery, and the same split:
discovery is cross-tenant and read-only; every write that follows runs on the ordinary
NOBYPASSRLS app role inside `session_scope(organization_id=...)`, one transaction per domain.

**It never revokes `verified_at`.** See `services.recheck_domain`'s own docstring for the full
argument — briefly, a DNS miss is usually transient, and auto-revoking single sign-on for a
whole organization on the first one would fail far more often than it protects anything. A
failure is recorded and flagged (`last_check_ok`, `failing_since`, `EVT_DOMAIN_CHECK_FAILED`
published once on the transition); an administrator releases the domain deliberately if it
turns out to be real.

**Poisoned-step guard.** An unexpected exception re-checking one domain is caught, logged with
its organization and domain id (never a token or a record value) and counted
`SweepReport.failed` — the tick moves on to the next domain rather than one bad domain stalling
every other organization's re-check, forever, on every restart. The same reasoning
`services/storefront/app/cron.py`'s own guard states.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import selectors, services
from app.config import settings
from platform_core.db import (
    assert_app_role,
    dispose_directory_engine,
    dispose_engine,
    get_directory_sessionmaker,
    init_directory_engine,
    init_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("account.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own — the same seam every other sweep's `WorkScope` documents.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    checked: int = 0
    transitioned_to_failing: int = 0
    recovered: int = 0
    failed: int = 0


class DomainRecheckSweep:
    """Drains the due domain re-checks, one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: timedelta = timedelta(seconds=86_400),
        batch_size: int = 50,
        poll_interval: float = 900.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._interval = interval
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        # An `Event` rather than a plain flag, so `stop()` interrupts the sleep between ticks
        # immediately instead of waiting out the rest of a (possibly 15-minute) poll interval —
        # the same reasoning `services/storefront/app/cron.py`'s own loop states.
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "account.cron.domain_recheck.started",
            poll_interval=self._poll_interval,
            batch_size=self._batch_size,
            interval_seconds=self._interval.total_seconds(),
        )
        while not self._stop_event.is_set():
            report = await self.tick()
            if report.checked or report.failed:
                log.info(
                    "account.cron.domain_recheck.pass",
                    considered=report.considered,
                    checked=report.checked,
                    transitioned_to_failing=report.transitioned_to_failing,
                    recovered=report.recovered,
                    failed=report.failed,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover verified domains due for a re-check, and advance each. `now`
        exists for tests; production always reads the wall clock."""
        at = now or datetime.now(UTC)
        cutoff = at - self._interval
        async with self._discovery_factory() as session:
            due = await selectors.due_for_recheck(session, before=cutoff, limit=self._batch_size)

        report = SweepReport(considered=len(due))
        for candidate in due:
            await self._recheck_one(candidate.organization_id, candidate.id, at, report)
        return report

    async def _recheck_one(
        self, organization_id: uuid.UUID, domain_id: uuid.UUID, at: datetime, report: SweepReport
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                row = await selectors.get_domain(session, organization_id, domain_id)
                # Released, or somehow un-verified, since discovery read it a moment ago on a
                # different session — nothing to do, and not a failure of this sweep's own.
                if row is None or row.verified_at is None:
                    return
                was_failing = row.failing_since is not None
                await services.recheck_domain(session, row, now=at)
        except Exception:
            log.exception(
                "account.cron.domain_recheck_failed",
                organization_id=str(organization_id),
                domain_id=str(domain_id),
            )
            report.failed += 1
            return

        report.checked += 1
        if row.last_check_ok is False and not was_failing:
            report.transitioned_to_failing += 1
        elif row.last_check_ok is True and was_failing:
            report.recovered += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant — the same
    # assertion every other sweep and the consumers make.
    await assert_app_role(engine, environment=cfg.environment)
    # Same silent-nothing failure shape ADR-0010 §4 names for the anonymous route and
    # `services/storefront/app/cron.py`'s own sweep restates: without the directory role,
    # discovery would run on the ordinary application session, bind no tenant, match zero rows,
    # and this sweep would look identical to one with nothing due, forever.
    init_directory_engine(cfg)

    sweep = DomainRecheckSweep(
        discovery_factory=get_directory_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        interval=timedelta(seconds=cfg.domain_recheck_interval_seconds),
        batch_size=cfg.domain_recheck_batch_size,
        poll_interval=cfg.domain_recheck_poll_interval_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, sweep.stop)

    try:
        await sweep.run()
    finally:
        await dispose_directory_engine()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
