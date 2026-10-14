"""The charge poller for payment — the only way a platform charge learns an outcome while the
provider webhooks are private (ADR-0032, owner decision). Runs inside `payment-worker` / the API's
background group beside the stuck-charge sweep; `python -m app.checkout.cron` runs it alone for
local debugging.

**What it asks about.** Every platform charge that has a provider Order and no terminal state
(`selectors.due_pollable_charges`): a **checkout** the customer may have paid, and a **debit** the
provider authorised and has not yet reported captured. The stuck-charge sweep never looks at either
— a checkout is `created` with no payment by design, and an `authorized` debit is not stuck, merely
unfinished — so without this nothing would ever move them. And a checkout it **closed** as
`checkout_expired`, at a much longer gap, for `checkout_late_capture_window_seconds`: a Razorpay
Order never expires, so a customer with the page still open can pay one after this stopped
waiting, and with the webhooks private nothing else would ever learn of it.

**Claim → call → record**, like every provider call in this service: a short transaction locks the
charge and stamps the poll gap (`claim_poll`); the provider is asked on a worker thread under an
overall deadline with no transaction open (`fetch_order_payments`); a second short transaction
reconciles what it said through the path a signed webhook takes and, for a checkout still unpaid
past its expiry **whose provider was successfully asked**, closes it (`record_poll`). A provider
that cannot be reached changes nothing: a charge is never expired on a question that could not be
asked.

**Stray Orders.** Razorpay accepts a duplicate receipt, so an unknown-outcome create can leave a
second Order for one charge that nobody recorded. Its id never reached a browser, but before a
checkout is closed, and on every late check after, the receipt is also searched for such an Order
and a payment on one is recorded against the charge (`fetch_stray_payments`, best-effort: the
provider's receipt search can miss a fresh Order).

**Routed by the row.** Each charge is asked at the provider it was created under
(`charges.provider`), resolved per charge — a Razorpay checkout is polled through Razorpay while
the default is Cashfree.

Discovery is cross-tenant and runs on the BYPASSRLS outbox role through the same column-scoped
grant as the stuck-charge sweep (`migrations/env.py`); every write runs on the app role inside
`session_scope(organization_id=...)`. A tick that raises backs off rather than ending the loop; an
exception on one charge is logged with its identifiers and counted `failed`.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, selectors
from app.checkout import services as checkout_services
from app.config import settings
from app.deps import resolve_provider
from app.models import CHARGE_CAPTURED, CHARGE_FAILED
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
from platform_core.supervise import GuardedSweep

log = get_logger("payment.checkout.cron")

#: A tenant-bound transaction for one organization — `session_scope` in production, a test's own.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]
#: Resolves a charge's provider by name (its own row's, never the default).
ProviderResolver = Callable[[str], PaymentProvider]


@dataclass
class PollReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    polled: int = 0
    captured: int = 0
    expired: int = 0
    unchecked: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.captured + self.expired


class ChargePoller(GuardedSweep):
    """Asks the provider about charges with an Order and no terminal state, one pass at a time."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        provider_resolver: ProviderResolver = resolve_provider,
        batch_size: int = 100,
        poll_interval: float = 30.0,
        max_backoff: float = 300.0,
    ) -> None:
        super().__init__(poll_interval=poll_interval, max_backoff=max_backoff)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._provider_resolver = provider_resolver
        self._batch_size = batch_size

    @property
    def _log_name(self) -> str:
        return "payment.charge_poller"

    async def on_tick_success(self, result: PollReport) -> None:
        report = result
        if report.acted or report.failed:
            log.info(
                "payment.charge_poller.pass",
                considered=report.considered,
                polled=report.polled,
                captured=report.captured,
                expired=report.expired,
                unchecked=report.unchecked,
                skipped=report.skipped,
                failed=report.failed,
            )

    async def tick(self, now: datetime | None = None) -> PollReport:
        """One pass: discover charges due to be asked about, then ask about each under its own
        tenant binding. `now` pins the clock for a test; left `None` it is read per charge."""
        at = now or datetime.now(UTC)
        report = PollReport()
        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_pollable_charges(session, now=at, limit=self._batch_size)
        report.considered = len(due)
        for organization_id, charge_id in due:
            await self._poll_one(organization_id, charge_id, now or datetime.now(UTC), report)
        return report

    async def _poll_one(
        self,
        organization_id: uuid.UUID,
        charge_id: uuid.UUID,
        now: datetime,
        report: PollReport,
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                ticket = await checkout_services.claim_poll(
                    session, organization_id, charge_id, now=now, respect_gap=True
                )
            if ticket is None:
                report.skipped += 1
                return
            provider = self._provider_resolver(ticket.provider)
            snapshots = await checkout_services.fetch_order_payments(provider, ticket)
            if snapshots is None:
                report.unchecked += 1
                return
            strays = await checkout_services.fetch_stray_payments(provider, ticket)
            report.polled += 1
            async with self._work_scope(organization_id) as session:
                charge = await checkout_services.record_poll(
                    session, ticket, snapshots, now=now, strays=strays
                )
            if charge is not None and charge.state == CHARGE_CAPTURED:
                report.captured += 1
            elif (
                charge is not None
                and not ticket.late
                and charge.state == CHARGE_FAILED
                and charge.failure_reason == domain.FAILURE_CHECKOUT_EXPIRED
            ):
                report.expired += 1
        except Exception:
            log.exception(
                "payment.charge_poller.poll_failed",
                organization_id=str(organization_id),
                charge_id=str(charge_id),
            )
            report.failed += 1


async def main() -> None:
    """Run the poller alone (`python -m app.checkout.cron`), for local debugging. Production runs
    it in `payment-worker` beside the consumers (`app/worker.py`), or in the API's own background
    group (`app/main.py`)."""
    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    poller = build_poller()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, poller.stop)
    await poller.run()


def build_poller(*, work_scope: WorkScope | None = None) -> ChargePoller:
    """`work_scope` defaults to the process-wide engine, right for the standalone worker.
    `app/main.py` passes a bulkhead scope instead when this runs inside the API process, so its
    per-charge writes cannot take a connection from a request or be starved by one."""
    cfg = settings()
    return ChargePoller(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=work_scope
        or (lambda organization_id: session_scope(organization_id=organization_id)),
        poll_interval=cfg.charge_poll_interval_seconds,
    )


if __name__ == "__main__":
    asyncio.run(main())
