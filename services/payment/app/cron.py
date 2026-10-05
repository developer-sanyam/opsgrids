"""The stuck-charge sweep for payment. Runs inside `payment-worker` (`app/worker.py`); `bin/cron`
runs it alone for local debugging.

**Why this exists.** `create_charge` used to be one transaction: claim the row, call the
provider, record what it said, commit. That held a pool connection — and, briefly, the row lock
from the idempotent insert — for as long as the provider took to answer, which is exactly the
failure this platform is sweeping for everywhere (D3): a slow processor exhausts the connection
pool, and a timeout *after* the provider actually moved money leaves this service's ledger and
the provider's disagreeing about whether a charge exists.

`app/api/internal.py` and `app/consumers.py` now run three acts instead: **claim**
(`services.stage_charge`, one short transaction, which also takes the *lease* — the right to call
the provider), **call** (`services.call_provider_charge`, on a worker thread, no session),
**record** (`services.finalize_charge`, a second short transaction). A process that dies between
the call and the record leaves a charge `created` with no `provider_payment_id` and a lease that
will lapse — visible, not lost — and this sweep is what resumes it. The consumers mark their
event processed in the claim transaction, so the queue never retries such a charge; only this
sweep does, which is why it must run wherever payment runs.

**Same claim-and-drain shape as `subscription`'s dunning sweep and `finance`'s filing-reclaim
sweep** (the outbox relay's own shape, ADR-0002):

* **discovery** (`selectors.due_stuck_charges`) runs on the BYPASSRLS outbox role, with a
  column-scoped grant on `charges` (`migrations/env.py`) — there is no tenant to bind while
  asking *which* organizations have a stuck charge. It returns only charges the sweep can act on
  now: no call in flight (lease lapsed), not backing off, not deleted.
* **every write** runs on the app role inside `session_scope(organization_id=...)`.

**Calling the provider again is safe because the call looks up before it creates** — not
because of an idempotency key: Razorpay has no idempotency key on a payment create (ADR-0004 §6
and its addendum). `call_provider_charge` finds the charge's Order by its `receipt` (the
platform's key) and asks for a payment already made against it before creating one, so a resumed
call lands on the payment a crashed one made. And the lease (`services.claim_charge_lease`, one
conditional `UPDATE … RETURNING`) means the sweep never calls while a request's call for the same
charge is still in flight.

**Bounded, and never failed blind.** Each call claims an attempt; the next waits
`domain.charge_retry_backoff(n)`. At `Settings.charge_max_attempts` the sweep does not simply fail
the charge — the last attempt's create may have reached the provider with its answer lost, and a
failure would tell dunning to debit again. It makes a **lookup-only** call (the charge's Order by
receipt, then a payment against it): a payment found is recorded; none found fails the charge as
`attempts_exhausted` and stages `commerce.payment.failed`, so dunning proceeds; a lookup that
cannot be made is retried up to `charge_final_lookup_attempts` times, then the charge is failed
as `lookup_unavailable` with an alert. A provider's late `payment.captured` for a charge the
platform failed this way is still recorded (`services._reconcile_charge`).
A tick that raises (a discovery query failing) backs off and tries again rather than ending the
loop — it shares a process with the consumers. An exception on one charge is logged with its
identifiers and counted `failed`; the tick moves on.
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
from app.deps import get_provider, resolve_provider
from app.models import Charge
from app.providers.base import PaymentProvider
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

log = get_logger("payment.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own, the same seam every other sweep in this platform is built on.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

#: The floor for charges staged before the lease columns existed (no `lease_until`): one staged
#: less than this long ago may still be inside the request that staged it.
DEFAULT_RECLAIM_AFTER = timedelta(minutes=5)


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    resumed: int = 0
    exhausted: int = 0
    #: Charges the provider refused outright (nothing created): failed `provider_rejected:<code>`.
    rejected: int = 0
    #: Charges whose attempt was given back because the platform's own credentials were refused —
    #: not failed, not announced; an operator was paged (`services.CallOutcome`).
    misconfigured: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.resumed + self.exhausted + self.rejected


class StuckChargeSweep:
    """Drains charges staged but never finalized, one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        provider_factory: Callable[[], PaymentProvider] = get_provider,
        provider_resolver: Callable[[str], PaymentProvider] | None = None,
        reclaim_after: timedelta = DEFAULT_RECLAIM_AFTER,
        batch_size: int = 100,
        poll_interval: float = 60.0,
        max_backoff: float = 300.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._provider_factory = provider_factory
        self._provider_resolver = provider_resolver
        self._reclaim_after = reclaim_after
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        #: Consecutive failed ticks, reset by the first clean one — the number worth alerting on.
        self.consecutive_failures = 0
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "payment.stuck_charge.sweep.started",
            poll_interval=self._poll_interval,
            reclaim_after_seconds=self._reclaim_after.total_seconds(),
        )
        wait = self._poll_interval
        while not self._stop_event.is_set():
            # The per-tick guard: in `payment-worker` this loop shares a process with the
            # consumers, and the supervisor there ends the process when any loop ends. A tick
            # that raises backs off and tries again; `CancelledError` always propagates.
            try:
                report = await self.tick()
            except Exception:
                self.consecutive_failures += 1
                log.exception(
                    "payment.stuck_charge.sweep.tick_failed",
                    consecutive_failures=self.consecutive_failures,
                    retry_in_seconds=wait,
                )
                await self._sleep(wait)
                wait = min(wait * 2, self._max_backoff)
                continue
            self.consecutive_failures = 0
            wait = self._poll_interval
            if report.acted or report.failed:
                log.info(
                    "payment.stuck_charge.sweep.pass",
                    considered=report.considered,
                    resumed=report.resumed,
                    exhausted=report.exhausted,
                    rejected=report.rejected,
                    misconfigured=report.misconfigured,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            await self._sleep(self._poll_interval)

    async def _sleep(self, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    def _provider_for(self, charge: Charge) -> PaymentProvider:
        """The adapter that finishes **this charge**: the one it was created under
        (`charges.provider`), whatever the configured default is now. A Razorpay charge left
        `created` is looked up and resumed through Razorpay while the default is Cashfree — Cashfree
        cannot find Razorpay's Order, and "creating" it there is a second debit (ADR-0029). The
        zero-argument `provider_factory` stays the answer when it names that provider, so an adapter
        a test (or a wiring) built keeps its own state."""
        if self._provider_resolver is not None:
            return self._provider_resolver(charge.provider)
        default = self._provider_factory()
        if getattr(default, "name", None) == charge.provider:
            return default
        return resolve_provider(charge.provider)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover actionable stuck charges, then resume each under its own tenant
        binding. `now` pins the clock for a test; left `None`, it is read per charge — a pass
        spending minutes on provider calls must not judge its last charge by its first's clock."""
        at = now or datetime.now(UTC)
        report = SweepReport()

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_stuck_charges(
                session, now=at, older_than=at - self._reclaim_after, limit=self._batch_size
            )

        report.considered = len(due)
        for organization_id, charge_id in due:
            await self._resume_one(organization_id, charge_id, now or datetime.now(UTC), report)
        return report

    async def _resume_one(
        self,
        organization_id: uuid.UUID,
        charge_id: uuid.UUID,
        now: datetime,
        report: SweepReport,
    ) -> None:
        """Claim (`services.claim_stuck_charge` — re-checked under the tenant binding, the lease
        taken), call the provider on a worker thread with no transaction open, record in a second
        short transaction.

        At the attempt budget the call is a **lookup only** (`final_lookup`): a payment the
        provider holds is recorded like any answer; none at all fails the charge
        (`fail_exhausted_charge`, re-checked under the lock) and tells dunning; a lookup that
        could not be made leaves the charge for the next tick, until the final-lookup budget is
        spent too (`exhausted`, `lookup_unavailable`)."""
        try:
            async with self._work_scope(organization_id) as session:
                step, charge = await services.claim_stuck_charge(
                    session, charge_id, organization_id=organization_id, now=now
                )
            if step == "exhausted":
                report.exhausted += 1
                return
            if step not in ("call", "final_lookup") or charge is None:
                report.skipped += 1
                return
            provider = self._provider_for(charge)
            if step == "final_lookup":
                await self._final_lookup(organization_id, charge, provider, report)
                return

            outcome = await services.attempt_provider_charge(provider, charge)
            if not outcome.needs_record:
                report.failed += 1  # no answer; the lease lapses and a later tick resumes it
                return
            async with self._work_scope(organization_id) as session:
                await services.record_charge_outcome(
                    session, charge_id, outcome, organization_id=organization_id
                )
            if outcome.kind == services.OUTCOME_RESULT:
                report.resumed += 1
            elif outcome.kind == services.OUTCOME_REJECTED:
                report.rejected += 1
            else:
                report.misconfigured += 1
        except Exception:
            log.exception(
                "payment.stuck_charge.sweep.resume_failed",
                organization_id=str(organization_id),
                charge_id=str(charge_id),
            )
            report.failed += 1

    async def _final_lookup(
        self,
        organization_id: uuid.UUID,
        charge: Charge,
        provider: PaymentProvider,
        report: SweepReport,
    ) -> None:
        lookup = await services.lookup_provider_charge_off_loop(provider, charge)
        if lookup.kind == "found" and lookup.result is not None:
            async with self._work_scope(organization_id) as session:
                await services.finalize_charge(
                    session, charge.id, lookup.result, organization_id=organization_id
                )
            log.warning(
                "payment.stuck_charge.found_at_final_lookup",
                organization_id=str(organization_id),
                charge_id=str(charge.id),
            )
            report.resumed += 1
            return
        if lookup.kind == "none":
            async with self._work_scope(organization_id) as session:
                failed = await services.fail_exhausted_charge(
                    session, charge.id, organization_id=organization_id
                )
            if failed is not None:
                report.exhausted += 1
            else:
                report.skipped += 1
            return
        report.failed += 1  # the lookup itself could not be made; the next tick asks again


async def main() -> None:
    """Run the sweep alone (`bin/cron`), for local debugging. Production runs it in
    `payment-worker` beside the consumers (`app/worker.py`)."""
    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and a charge orphaned by a crash sits `created` forever (ADR-0002).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = build_sweep()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, sweep.stop)

    await sweep.run()


def build_sweep(*, work_scope: WorkScope | None = None) -> StuckChargeSweep:
    """`work_scope` defaults to the process-wide engine — right for `bin/cron` and the standalone
    `bin/worker`, both one process with nothing else to share a pool with. `app/main.py` passes a
    bulkhead scope instead when this sweep is folded into the API process, so its per-charge
    writes cannot take a connection from a request or be starved by one."""
    cfg = settings()
    return StuckChargeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=work_scope
        or (lambda organization_id: session_scope(organization_id=organization_id)),
        reclaim_after=timedelta(seconds=cfg.charge_reclaim_after_seconds),
        poll_interval=cfg.charge_sweep_poll_interval_seconds,
    )


if __name__ == "__main__":
    asyncio.run(main())
