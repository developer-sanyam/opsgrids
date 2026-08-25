"""The dunning sweep for subscription. Runs as its own process (`bin/cron`).

**Why a claim-and-drain sweep and not something else.** ADR-0005 needs work that happens *at a
time*, and the platform has three plausible mechanisms:

* **`services/scheduler`** is an empty scaffold — no tables, no logic, not one caller. Designing a
  general cross-service scheduler is its own ADR, and dunning would be its first customer rather
  than a user of something proven. Deferred, not rejected: when it exists, this loop becomes one
  of its jobs and the schedule stays exactly where it is, on the cycle row.
* **A delay on the broker** (a delayed-exchange plugin, or per-message TTL into a dead-letter
  ladder) puts the schedule inside RabbitMQ, where it cannot be queried, corrected, or explained
  to a customer. "When does my access end?" would be answerable only by draining a queue, and a
  broker that loses its durable store would silently forget every open grace window.
* **A due-work table drained on a poll**, which is what this is. The schedule is rows in Postgres:
  indexed, inspectable, correctable by an operator, and covered by the same backups as the money
  it defends. Restarting the sweep loses nothing, because it holds no state — everything it needs
  is `dunning_cycles.next_action_at`.

The drain is deliberately the **outbox relay's own shape**, because it is the same problem: find
work across every tenant, claim a batch with `FOR UPDATE SKIP LOCKED` so replicas divide it
safely, act, sleep. Two engines, and the split is the point (ADR-0002):

* **discovery** runs on the BYPASSRLS outbox role — it asks *which organizations have work*, so
  there is no tenant to bind while asking. As the app role it would return nothing, log nothing,
  and every past-due customer in the platform would keep their product for free forever.
* **every write** runs on the NOBYPASSRLS app role inside `session_scope(organization_id=...)`,
  under the tenant policy. The bypass buys discovery, never authority — and never a write.

Nothing here reads a clock except `tick`, once, at the top. That single `now` is threaded through
every decision in the pass, so a sweep cannot straddle a boundary and treat two cycles as being at
different moments; and every due instant it compares against is a **stored** timestamp anchored to
the cycle's `opened_at`, never `now + interval`, so a late pass does not push the ladder out.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain
from app.config import settings
from app.models import DUNNING_ABANDONED
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

log = get_logger("subscription.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the sweep can be driven without a process-wide engine.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    retried: int = 0
    suspended: int = 0
    skipped: int = 0

    @property
    def acted(self) -> int:
        return self.retried + self.suspended


class DunningSweep:
    """Drains the due dunning work, one pass at a time.

    Constructed with its two session factories rather than reaching for the module-level engines,
    for the same reason every other seam in this codebase is injected: a test drives real
    PostgreSQL through the same code the process runs, with no clock and no engine patched.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        policy: domain.DunningPolicy | None = None,
        batch_size: int = 100,
        poll_interval: float = 300.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._policy = policy or domain.POLICY
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info(
            "dunning.sweep.started",
            poll_interval=self._poll_interval,
            retries=self._policy.max_attempts,
            grace_days=self._policy.grace.days,
        )
        while not self._stop:
            report = await self.tick()
            if report.acted:
                log.info(
                    "dunning.sweep.pass",
                    considered=report.considered,
                    retried=report.retried,
                    suspended=report.suspended,
                    skipped=report.skipped,
                )
            await asyncio.sleep(self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover due cycles, then act on each under its own tenant binding.

        The clock is read **once**, here, and passed down. Two transactions per cycle is
        deliberate — discovery cannot hold a tenant binding, and the work must — and the gap
        between them is closed by re-reading and re-deciding under the lock, so a cycle another
        replica dealt with in that gap is skipped rather than acted on twice.
        """
        at = now or datetime.now(UTC)
        report = SweepReport()

        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_dunning_cycles(session, now=at, limit=self._batch_size)

        report.considered = len(due)
        for organization_id, cycle_id in due:
            await self._advance(organization_id, cycle_id, at, report)
        return report

    async def _advance(
        self,
        organization_id: uuid.UUID,
        cycle_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """Act on one cycle, in its own tenant-bound transaction."""
        from app import selectors, services

        async with self._work_scope(organization_id) as session:
            cycle = await selectors.lock_open_cycle(session, cycle_id)
            if cycle is None:
                # Held by another replica, or closed between discovery and here — a payment
                # captured in that window is exactly this, and it is the good outcome.
                report.skipped += 1
                return
            if cycle.next_action_at is None or cycle.next_action_at > now:
                # Already advanced by whoever held the lock first. Re-checking after the claim is
                # what makes "at most one action per due moment" true across replicas.
                report.skipped += 1
                return

            subscription = await selectors.get_subscription(session, cycle.subscription_id)
            if subscription is None:
                # The subscription is gone but its cycle is not. Close it rather than leave a row
                # the sweep will pick up on every pass forever.
                cycle.state = DUNNING_ABANDONED
                cycle.closed_at = now
                cycle.closed_reason = "subscription_missing"
                cycle.next_action_at = None
                report.skipped += 1
                return

            action = domain.next_dunning_action(
                opened_at=cycle.opened_at,
                window_ends_at=cycle.grace_ends_at,
                attempts_made=cycle.attempts_made,
                now=now,
                policy=self._policy,
            )
            if action == domain.ACTION_RETRY:
                await services.request_dunning_retry(session, subscription, cycle, now=now)
                report.retried += 1
            elif action == domain.ACTION_SUSPEND:
                await services.suspend_for_dunning(session, subscription, cycle, now=now)
                report.suspended += 1
            else:
                # The ladder is spent but the window is still open — the deliberate quiet gap
                # before suspension (ADR-0005). Nothing to do; come back when the window closes.
                cycle.next_action_at = cycle.grace_ends_at
                report.skipped += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumers make applies: started on a BYPASSRLS DSN by mistake, row-level
    # security would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every grace window stays open forever — the relay's silent stall, in the path
    # that decides whether anybody pays. Refuse it outside local.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = DunningSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
