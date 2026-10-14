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
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain
from app.config import settings
from app.models import DUNNING_ABANDONED, DunningCycle
from app.supervise import supervise
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
from platform_core.supervise import GuardedSweep

log = get_logger("subscription.cron")

#: How far a cycle whose action raised is pushed back before the sweep looks at it again.
CYCLE_FAILURE_BACKOFF = timedelta(minutes=15)

#: The longest a failing sweep waits between passes (the cap the worker's other loops use).
MAX_BACKOFF_SECONDS = 3600.0

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
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.retried + self.suspended + self.failed


class DunningSweep(GuardedSweep):
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
        super().__init__(poll_interval=poll_interval, max_backoff=MAX_BACKOFF_SECONDS)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._policy = policy or domain.POLICY
        self._batch_size = batch_size

    @property
    def _log_name(self) -> str:
        return "dunning.sweep"

    async def on_tick_success(self, result: SweepReport) -> None:
        self._log_pass(result)

    @staticmethod
    def _log_pass(report: SweepReport) -> None:
        if report.acted:
            log.info(
                "dunning.sweep.pass",
                considered=report.considered,
                retried=report.retried,
                suspended=report.suspended,
                skipped=report.skipped,
                failed=report.failed,
            )

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
            try:
                await self._advance(organization_id, cycle_id, at, report)
            except Exception as exc:
                # One poisoned cycle is one cycle: logged, pushed back so it is not first in line
                # again next pass, and the rest of the pass carries on.
                report.failed += 1
                log.error(
                    "dunning.sweep.cycle_failed",
                    organization_id=str(organization_id),
                    cycle_id=str(cycle_id),
                    error=str(exc)[:300],
                )
                await self._back_off(organization_id, cycle_id)
        return report

    async def _back_off(self, organization_id: uuid.UUID, cycle_id: uuid.UUID) -> None:
        """Push a failing cycle's next action out by `CYCLE_FAILURE_BACKOFF` on the wall clock.
        Best effort: if even this fails the database is down, and the next pass finds out."""
        try:
            async with self._work_scope(organization_id) as session:
                await session.execute(
                    update(DunningCycle)
                    .where(
                        DunningCycle.organization_id == organization_id,
                        DunningCycle.id == cycle_id,
                    )
                    .values(next_action_at=datetime.now(UTC) + CYCLE_FAILURE_BACKOFF)
                )
        except Exception as exc:  # pragma: no cover - the database itself is unavailable
            log.error("dunning.sweep.backoff_failed", cycle_id=str(cycle_id), error=str(exc)[:300])

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
            # Lock order is subscription, then cycle — the order every other writer takes
            # (cancel, suspend, a payment, the billing run). Taking the cycle first and the
            # subscription inside `suspend_for_dunning` deadlocked against a cancel that held the
            # subscription and was closing the cycle (final money review).
            unlocked = await selectors.get_cycle(session, organization_id, cycle_id)
            if unlocked is None:
                report.skipped += 1
                return
            subscription = None
            if await selectors.get_subscription(session, unlocked.subscription_id) is not None:
                subscription = await selectors.lock_subscription(
                    session, organization_id, unlocked.subscription_id, skip_locked=True
                )
                if subscription is None:
                    # A cancel, a payment or the billing run holds it: this cycle waits for the
                    # next pass, and whatever that writer did is what the pass then decides from.
                    report.skipped += 1
                    return
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
    # The billing run shares this process for the same reason it shares `app/worker.py`'s: both
    # are idle between passes, and both are due-work sweeps on the same two engines.
    from app.billing_run import BillingRunSweep, HttpBilling, HttpPayment, billing_run_loop

    billing_run = BillingRunSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        billing=HttpBilling(cfg.billing_service_url, cfg),
        payment=HttpPayment(cfg.payment_service_url, cfg),
        poll_interval=cfg.billing_run_poll_interval,
        not_before=cfg.billing_run_not_before,
    )
    loops = {"dunning_sweep": sweep.run()}
    run_loop = billing_run_loop(cfg, billing_run)
    if run_loop is not None:
        loops["billing_run"] = run_loop
    await supervise(loops)


if __name__ == "__main__":
    asyncio.run(main())
