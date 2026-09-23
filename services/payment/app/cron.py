"""The stuck-charge sweep for payment. Runs as its own process (`bin/cron`).

**Why this exists.** `create_charge` used to be one transaction: claim the row, call the
provider, record what it said, commit. That held a pool connection — and, briefly, the row lock
from the idempotent insert — for as long as the provider took to answer, which is exactly the
failure this platform is sweeping for everywhere (D3): a slow processor exhausts the connection
pool, and a timeout *after* the provider actually moved money leaves this service's ledger and
the provider's disagreeing about whether a charge exists.

`app/api/internal.py` and `app/consumers.py` now run three acts instead: **claim**
(`services.stage_charge`, one short transaction), **call** (`services.call_provider_charge`, no
session), **record** (`services.finalize_charge`, a second short transaction). A process that
dies between the call and the record leaves a charge `created` with no `provider_payment_id` —
visible, not lost, and this sweep is what resumes it.

**Same claim-and-drain shape as `subscription`'s dunning sweep and `finance`'s filing-reclaim
sweep** (the outbox relay's own shape, ADR-0002):

* **discovery** (`selectors.due_stuck_charges`) runs on the BYPASSRLS outbox role — it asks
  *which organizations* have a charge stuck, and there is no tenant to bind while asking. As the
  NOBYPASSRLS app role it would see nothing, and a charge orphaned by a crash would sit `created`
  forever, silently, looking exactly like a healthy idle sweep.
* **every write** runs on the app role inside `session_scope(organization_id=...)`, under the
  tenant policy. The bypass buys discovery, never authority — the sweep never writes on it.

**Calling the provider again is safe** because `call_provider_charge` always sends the platform's
own `idempotency_key`, and Razorpay has no idempotency of its own to disagree with it (ADR-0004
§6): a resumed call lands on the same provider payment a real network partition would have left
half-finished, not a second debit.

**A poisoned charge cannot stall the sweep.** `older_than` only considers charges staged more than
`Settings.charge_reclaim_after_seconds` ago — comfortably longer than a provider call is ever
expected to take — so a charge legitimately mid-flight in the very request that staged it is never
mistaken for stuck. An unexpected exception resuming one charge is caught, logged with its own
identifiers, and counted as `failed`; the tick moves on to the next charge rather than letting one
bad row block every other organization's chance to be resumed this pass, the same reasoning every
other sweep in this platform states for the same shape of bug.
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
from app.deps import get_provider
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

#: How long a charge must have sat `created` with no `provider_payment_id` before this sweep will
#: touch it — the poisoned-step guard against reclaiming a charge that is simply still in the
#: request that staged it.
DEFAULT_RECLAIM_AFTER = timedelta(minutes=5)


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    resumed: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.resumed


class StuckChargeSweep:
    """Drains charges staged but never finalized, one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        provider_factory: Callable[[], PaymentProvider] = get_provider,
        reclaim_after: timedelta = DEFAULT_RECLAIM_AFTER,
        batch_size: int = 100,
        poll_interval: float = 60.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._provider_factory = provider_factory
        self._reclaim_after = reclaim_after
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "payment.stuck_charge.sweep.started",
            poll_interval=self._poll_interval,
            reclaim_after_seconds=self._reclaim_after.total_seconds(),
        )
        while not self._stop_event.is_set():
            report = await self.tick()
            if report.acted or report.failed:
                log.info(
                    "payment.stuck_charge.sweep.pass",
                    considered=report.considered,
                    resumed=report.resumed,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover charges stuck before `now - reclaim_after`, then resume each under
        its own tenant binding. Two transactions per charge is deliberate — discovery cannot hold
        a tenant binding, and the resumed call must run with none at all."""
        at = now or datetime.now(UTC)
        cutoff = at - self._reclaim_after
        report = SweepReport()

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_stuck_charges(
                session, older_than=cutoff, limit=self._batch_size
            )

        report.considered = len(due)
        for organization_id, charge_id in due:
            await self._resume_one(organization_id, charge_id, report)
        return report

    async def _resume_one(
        self, organization_id: uuid.UUID, charge_id: uuid.UUID, report: SweepReport
    ) -> None:
        """Resume one charge: re-check it is still stuck under the tenant binding (a webhook or a
        concurrent request could have finalized it between discovery and here), call the provider
        with no transaction open, then record the result in a second short transaction."""
        try:
            provider = self._provider_factory()
            async with self._work_scope(organization_id) as session:
                charge = await selectors.get_charge(session, charge_id)
                if charge is None or charge.provider_payment_id is not None:
                    # Finalized, or gone, since discovery ran. The good race.
                    report.skipped += 1
                    return
                # A copy of the fields `call_provider_charge` needs survives the transaction's
                # commit below (`expire_on_commit=False` on every sessionmaker in this platform),
                # but the call itself must run after this `async with` block has exited, not
                # inside it — there is nothing left to claim, so nothing more is written here.

            result = services.call_provider_charge(provider, charge)
            async with self._work_scope(organization_id) as session:
                await services.finalize_charge(session, charge_id, result)
            report.resumed += 1
        except Exception:
            log.exception(
                "payment.stuck_charge.sweep.resume_failed",
                organization_id=str(organization_id),
                charge_id=str(charge_id),
            )
            report.failed += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumers make applies here too (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and a charge orphaned by a crash sits `created` forever — the same silent stall
    # ADR-0002 records for the relay, in the path that decides whether money that already moved
    # is ever recorded.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = StuckChargeSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        reclaim_after=timedelta(seconds=cfg.charge_reclaim_after_seconds),
        poll_interval=cfg.charge_sweep_poll_interval_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, sweep.stop)

    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
