"""The collection expiry sweep — `created`/`failed` checkouts nobody paid, closed as `expired`.

Same claim-and-drain shape as `app/cron.py`'s stuck-charge sweep (and the outbox relay's):
**discovery** (`selectors.due_expiring_collections`) on the BYPASSRLS outbox role's column grant,
because there is no tenant to bind while asking which organizations have a stale checkout; every
**write** on the app role inside `session_scope(organization_id=...)`.

**Never expired blind.** A Razorpay Order does not expire on its own, so a customer can still pay
a checkout this platform stopped waiting for. Before closing one, the sweep asks the provider —
server to server, with the merchant's own keys, lookup only — what happened to the Order: a
captured or authorised payment is recorded instead (the money moved), and only an Order with
nothing paid is expired. A payment that lands after that is still recorded by its webhook
(`captured_after_expired`). A provider that cannot be asked leaves the checkout for the next tick
(its lease lapses first); an account that is no longer connected has nothing to ask, and the
checkout is expired directly. So is one whose Order the stub made (`charges.order_wire`): nothing
can have been paid into it, so a stub that no longer remembers it is "no payments", and a live
provider is never asked about it (`services._stub_order_payments`).

Runs beside the stuck-charge sweep in `payment-worker` / the API's folded worker
(`app/worker.py`, `app/main.py`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collections import selectors, services
from app.config import settings
from app.deps import CollectionsProviderFactory, get_collections_provider_factory
from platform_core.db import get_outbox_sessionmaker, session_scope
from platform_core.errors import ProblemError
from platform_core.logging import get_logger

log = get_logger("payment.collections.cron")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class ExpiryReport:
    considered: int = 0
    expired: int = 0
    reconciled: int = 0
    skipped: int = 0
    failed: int = 0


class CollectionExpirySweep:
    """Closes stale checkouts one pass at a time. See the module docstring."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        provider_factory: Callable[
            [], CollectionsProviderFactory
        ] = get_collections_provider_factory,
        ttl: timedelta,
        batch_size: int = 50,
        poll_interval: float = 300.0,
        max_backoff: float = 900.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._provider_factory = provider_factory
        self._ttl = ttl
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self.consecutive_failures = 0
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        log.info(
            "payment.collection_expiry.sweep.started",
            poll_interval=self._poll_interval,
            ttl_seconds=self._ttl.total_seconds(),
        )
        wait = min(self._poll_interval, self._max_backoff)
        while not self._stop_event.is_set():
            try:
                report = await self.tick()
            except Exception:
                self.consecutive_failures += 1
                log.exception(
                    "payment.collection_expiry.sweep.tick_failed",
                    consecutive_failures=self.consecutive_failures,
                    retry_in_seconds=wait,
                )
                await self._sleep(wait)
                wait = min(wait * 2, self._max_backoff)
                continue
            self.consecutive_failures = 0
            wait = min(self._poll_interval, self._max_backoff)
            if report.expired or report.reconciled or report.failed:
                log.info(
                    "payment.collection_expiry.sweep.pass",
                    considered=report.considered,
                    expired=report.expired,
                    reconciled=report.reconciled,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            await self._sleep(self._poll_interval)

    async def _sleep(self, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    async def tick(self, now: datetime | None = None) -> ExpiryReport:
        at = now or datetime.now(UTC)
        report = ExpiryReport()
        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_expiring_collections(
                session, now=at, older_than=at - self._ttl, limit=self._batch_size
            )
        report.considered = len(due)
        for organization_id, charge_id in due:
            await self._expire_one(organization_id, charge_id, now or datetime.now(UTC), report)
        return report

    async def _expire_one(
        self, organization_id: uuid.UUID, charge_id: uuid.UUID, now: datetime, report: ExpiryReport
    ) -> None:
        try:
            async with self._work_scope(organization_id) as session:
                claim = await services.claim_expiry(
                    session, organization_id=organization_id, charge_id=charge_id, now=now
                )
            if claim is None:
                report.skipped += 1
                return
            order_id: str | None = None
            payments: list = []  # type: ignore[type-arg]
            if claim.ticket is not None:
                try:
                    order_id, payments = await services.expiry_lookup(
                        self._provider_factory(), claim.ticket
                    )
                except ProblemError:
                    # Could not ask: leave it. The lease lapses and a later tick asks again.
                    report.failed += 1
                    return
            async with self._work_scope(organization_id) as session:
                expired = await services.record_expiry(
                    session,
                    organization_id=organization_id,
                    charge_id=charge_id,
                    provider_order_id=order_id,
                    payments=payments,
                )
            if expired is not None:
                report.expired += 1
            else:
                report.reconciled += 1
        except Exception:
            log.exception(
                "payment.collection_expiry.sweep.expire_failed",
                organization_id=str(organization_id),
                charge_id=str(charge_id),
            )
            report.failed += 1


def build_expiry_sweep(*, work_scope: WorkScope | None = None) -> CollectionExpirySweep:
    """`work_scope` defaults to the process-wide engine; `app/main.py` passes a bulkhead scope
    when the sweep is folded into the API process, exactly as `app.cron.build_sweep` does."""
    cfg = settings()
    return CollectionExpirySweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=work_scope
        or (lambda organization_id: session_scope(organization_id=organization_id)),
        ttl=timedelta(seconds=cfg.collection_checkout_ttl_seconds),
        poll_interval=cfg.collection_sweep_poll_interval_seconds,
    )
