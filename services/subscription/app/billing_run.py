"""The billing run: trials convert, periods roll, and each period is invoiced exactly once.

Before this, nothing ever looked at `trial_ends_at` or `current_period_end`: trials never
converted, nothing recurred, and billing's finalize route had no caller. This is the second sweep
in `app/worker.py`, beside the dunning ladder, in the same process and the same shape
(ADR-0005 §6; its addendum of 2026-09-23 records the rules below).

**Claim → call → record**, because no network call may run inside a database transaction:

1. **Discover** (outbox role, BYPASSRLS, ids only): trials whose end has passed; active,
   past-due or suspended subscriptions whose period has closed.
2. **Claim** (app role, tenant-bound, one short transaction): lock the subscription
   `FOR UPDATE SKIP LOCKED`, re-decide from its stored timestamps (`domain.due_period`), and write
   or re-find the period's `billing_periods` row with a lease. A suspended subscription's period
   rolls here, uninvoiced, and nothing is called.
3. **Call** (no transaction): billing prices and finalizes the period's invoice — idempotent on
   `(subscription, period_start)`, so a retry returns the invoice it already made — and, for a
   trial conversion that owes money, payment says whether a mandate can collect it.
4. **Record** (app role, tenant-bound, one short transaction): lock the subscription again, write
   billing's answer on the period row, advance the period, and make the transition — `activated`,
   `past_due` (a trial with no usable mandate, ADR-0013 §6), or `renewed` — with its outbox event.

A crash between 2 and 4 leaves a `pending` row with a lease; when the lease lapses the next pass
claims the same row and sends the identical request, and billing answers with the same invoice.
Two replicas cannot both call: the second finds the row locked or leased. The invoice exists once
because billing's unique key says so, not because this process remembered.

Every pass logs what it did (`billing_run.pass`) and every failure names its subscription
(`billing_run.failed`); `/health/billing-run` exposes the counts an alarm should watch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain
from app.models import PERIOD_PENDING, STATE_ACTIVE, STATE_TRIAL, BillingPeriod
from platform_core.config import ServiceSettings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("subscription.billing_run")

#: A tenant-bound transaction for one organization — `session_scope` in the process, a test's own
#: in the suite. The same seam `app.cron.DunningSweep` takes.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


# --------------------------------------------------------------------- the two calls


@dataclass(frozen=True)
class BilledInvoice:
    """billing's answer: the period's one invoice, and whether this call made it."""

    invoice_id: uuid.UUID
    total_minor: int
    currency: str
    created: bool


@dataclass(frozen=True)
class PeriodRequest:
    """Everything the billing call sends, read off the claimed row — so a retry after a crash sends
    exactly what the first attempt did, whatever has changed on the subscription since."""

    organization_id: uuid.UUID
    subscription_id: uuid.UUID
    period_id: uuid.UUID
    kind: str
    product_key: str
    plan_version_id: uuid.UUID
    currency: str
    period_start: datetime
    period_end: datetime
    usage_start: datetime | None
    usage_end: datetime | None
    items: list[dict[str, Any]]

    @classmethod
    def of(cls, period: BillingPeriod, product_key: str) -> PeriodRequest:
        return cls(
            organization_id=period.organization_id,
            subscription_id=period.subscription_id,
            period_id=period.id,
            kind=period.kind,
            product_key=product_key,
            plan_version_id=period.plan_version_id,
            currency=period.currency,
            period_start=period.period_start,
            period_end=period.period_end,
            usage_start=period.usage_start,
            usage_end=period.usage_end,
            items=list(period.items),
        )


class Billing(Protocol):
    async def bill_period(self, request: PeriodRequest) -> BilledInvoice: ...


class Payment(Protocol):
    async def can_collect(
        self, *, organization_id: uuid.UUID, currency: str, amount_minor: int
    ) -> bool: ...


class BillingRunCallError(RuntimeError):
    """A sibling answered with an error. The claim stays `pending` and backs off."""


def _headers(settings: ServiceSettings, audience: str) -> dict[str, str]:
    headers = {
        # Minted per call, addressed to the callee by name, good for seconds. `iss` is this
        # service's name, which is what billing's and payment's caller allow-lists check.
        "X-Internal-Token": mint_service_token(settings, audience=audience),
        "X-Calling-Service": settings.service_name,
    }
    correlation = correlation_id_ctx.get()
    if correlation:
        headers["X-Correlation-ID"] = correlation
    return headers


class HttpBilling:
    """billing's `POST /internal/v1/subscription-invoices`."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = settings
        self._timeout = timeout

    async def bill_period(self, request: PeriodRequest) -> BilledInvoice:
        body: dict[str, Any] = {
            "organization_id": str(request.organization_id),
            "subscription_id": str(request.subscription_id),
            "product_key": request.product_key,
            "plan_version_id": str(request.plan_version_id),
            "currency": request.currency,
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "items": request.items,
        }
        if request.usage_start is not None and request.usage_end is not None:
            body["usage_period_start"] = request.usage_start.isoformat()
            body["usage_period_end"] = request.usage_end.isoformat()
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.post(
                    "/internal/v1/subscription-invoices",
                    json=body,
                    headers=_headers(self._settings, "billing"),
                )
        except httpx.HTTPError as exc:
            raise BillingRunCallError(f"billing unreachable: {exc!s}"[:200]) from exc
        if response.status_code not in (200, 201):
            raise BillingRunCallError(
                f"billing answered {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        invoice = data["invoice"]
        return BilledInvoice(
            invoice_id=uuid.UUID(invoice["id"]),
            total_minor=int(invoice["total_minor"]),
            currency=str(invoice["currency"]),
            created=bool(data["created"]),
        )


class HttpPayment:
    """payment's `GET /internal/v1/organizations/{org}/mandates:usable`."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = settings
        self._timeout = timeout

    async def can_collect(
        self, *, organization_id: uuid.UUID, currency: str, amount_minor: int
    ) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.get(
                    f"/internal/v1/organizations/{organization_id}/mandates:usable",
                    params={"currency": currency, "amount_minor": amount_minor},
                    headers=_headers(self._settings, "payment"),
                )
        except httpx.HTTPError as exc:
            raise BillingRunCallError(f"payment unreachable: {exc!s}"[:200]) from exc
        if response.status_code != 200:
            raise BillingRunCallError(
                f"payment answered {response.status_code}: {response.text[:200]}"
            )
        return bool(response.json()["usable"])


# --------------------------------------------------------------------- the sweep


@dataclass
class RunReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    activated: int = 0
    past_due: int = 0
    renewed: int = 0
    skipped_suspended: int = 0
    superseded: int = 0
    skipped: int = 0
    failed: int = 0
    stale_closed: int = 0
    invoices: list[uuid.UUID] = field(default_factory=list)

    @property
    def issued(self) -> int:
        return len(self.invoices)

    @property
    def acted(self) -> int:
        return (
            self.activated
            + self.past_due
            + self.renewed
            + self.skipped_suspended
            + self.superseded
            + self.failed
            + self.stale_closed
        )


class BillingRunSweep:
    """Drains due billing work one pass at a time. Injected with its sessions and its two
    clients, so a test drives real PostgreSQL and real locks with fake siblings and no clock."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        billing: Billing,
        payment: Payment,
        batch_size: int = 50,
        poll_interval: float = 300.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._billing = billing
        self._payment = payment
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info("billing_run.started", poll_interval=self._poll_interval)
        while not self._stop:
            try:
                report = await self.tick()
            except Exception as exc:
                # Discovery itself failed (a database restart, a deadlock). Logged and retried
                # next pass rather than raised: raising would take the dunning sweep and the
                # payment consumer in this process down with it.
                log.error("billing_run.tick_failed", error=str(exc)[:300])
            else:
                if report.acted:
                    log.info(
                        "billing_run.pass",
                        considered=report.considered,
                        issued=report.issued,
                        activated=report.activated,
                        past_due=report.past_due,
                        renewed=report.renewed,
                        skipped_suspended=report.skipped_suspended,
                        superseded=report.superseded,
                        failed=report.failed,
                        stale_closed=report.stale_closed,
                    )
            await asyncio.sleep(self._poll_interval)

    async def tick(self, now: datetime | None = None) -> RunReport:
        """One pass. The clock is read once, here, and threaded through every decision."""
        at = now or datetime.now(UTC)
        report = RunReport()

        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_billing_subscriptions(session, now=at, limit=self._batch_size)
            stale = await selectors.stale_pending_periods(session, now=at, limit=self._batch_size)

        report.considered = len(due)
        for organization_id, subscription_id in due:
            try:
                await self._bill_one(organization_id, subscription_id, at, report)
            except Exception as exc:
                # One tenant's failure is one tenant's: logged with its ids, counted, and the pass
                # carries on. The claim (if any) stays pending and is retried after its lease.
                report.failed += 1
                log.error(
                    "billing_run.failed",
                    organization_id=str(organization_id),
                    subscription_id=str(subscription_id),
                    error=str(exc)[:300],
                )
        for organization_id, period_id in stale:
            try:
                await self._close_if_stale(organization_id, period_id, at, report)
            except Exception as exc:
                log.error(
                    "billing_run.stale_check_failed",
                    organization_id=str(organization_id),
                    period_id=str(period_id),
                    error=str(exc)[:300],
                )
        return report

    async def _bill_one(
        self,
        organization_id: uuid.UUID,
        subscription_id: uuid.UUID,
        now: datetime,
        report: RunReport,
    ) -> None:
        from app import selectors, services

        # --- claim ------------------------------------------------------------------------
        async with self._work_scope(organization_id) as session:
            subscription = await selectors.lock_subscription(
                session, subscription_id, skip_locked=True
            )
            if subscription is None:
                report.skipped += 1  # another replica holds it, or it is not this tenant's
                return
            try:
                due = domain.due_period(
                    state=subscription.state,
                    trial_ends_at=subscription.trial_ends_at,
                    current_period_start=subscription.current_period_start,
                    current_period_end=subscription.current_period_end,
                    billing_interval=subscription.billing_interval,
                    now=now,
                )
            except domain.NotBillable as exc:
                report.failed += 1
                log.error(
                    "billing_run.not_billable",
                    organization_id=str(organization_id),
                    subscription_id=str(subscription_id),
                    error=str(exc),
                )
                return
            if due is None:
                report.skipped += 1  # dealt with between discovery and this lock
                return
            if due.kind == domain.KIND_RENEWAL and not domain.bills_period(subscription.state):
                await services.skip_suspended_period(session, subscription, due, now=now)
                report.skipped_suspended += 1
                return
            period = await services.claim_billing_period(session, subscription, due, now=now)
            if period is None:
                report.skipped += 1  # leased by a replica mid-call, or backing off
                return
            request = PeriodRequest.of(period, subscription.product_key)

        # --- call (no transaction is open) ---------------------------------------------------
        try:
            billed = await self._billing.bill_period(request)
            collectable: bool | None = None
            if request.kind == domain.KIND_TRIAL_CONVERSION and billed.total_minor > 0:
                collectable = await self._payment.can_collect(
                    organization_id=organization_id,
                    currency=billed.currency,
                    amount_minor=billed.total_minor,
                )
        except Exception as exc:
            async with self._work_scope(organization_id) as session:
                claimed = await selectors.lock_billing_period_by_id(session, request.period_id)
                if claimed is not None:
                    await services.fail_billing_period(session, claimed, error=str(exc), now=now)
            report.failed += 1
            log.error(
                "billing_run.failed",
                organization_id=str(organization_id),
                subscription_id=str(subscription_id),
                period_start=request.period_start.isoformat(),
                error=str(exc)[:300],
            )
            return

        # --- record -------------------------------------------------------------------------
        async with self._work_scope(organization_id) as session:
            subscription = await selectors.lock_subscription(
                session, subscription_id, skip_locked=False
            )
            claimed = await selectors.lock_billing_period_by_id(session, request.period_id)
            if subscription is None or claimed is None:  # pragma: no cover - cascade-deleted
                report.skipped += 1
                return
            outcome = await services.record_billing_period(
                session,
                subscription,
                claimed,
                invoice_id=billed.invoice_id,
                total_minor=billed.total_minor,
                collectable=collectable,
                now=now,
            )

        if outcome == "already_recorded":
            report.skipped += 1
            return
        report.invoices.append(billed.invoice_id)
        if outcome == "activated":
            report.activated += 1
        elif outcome == "past_due":
            report.past_due += 1
        elif outcome == "superseded":
            report.superseded += 1
        else:
            report.renewed += 1
        log.info(
            "billing_run.recorded",
            organization_id=str(organization_id),
            subscription_id=str(subscription_id),
            invoice_id=str(billed.invoice_id),
            total_minor=billed.total_minor,
            currency=billed.currency,
            outcome=outcome,
            invoice_created=billed.created,
        )

    async def _close_if_stale(
        self,
        organization_id: uuid.UUID,
        period_id: uuid.UUID,
        now: datetime,
        report: RunReport,
    ) -> None:
        """A pending claim with a lapsed lease: leave it if its subscription is still due for it
        (the due pass above re-claims it), close it if the subscription has moved on."""
        from app import selectors, services

        async with self._work_scope(organization_id) as session:
            period = await selectors.lock_billing_period_by_id(session, period_id)
            if period is None or period.state != PERIOD_PENDING:
                return
            subscription = await selectors.lock_subscription(
                session, period.subscription_id, skip_locked=True
            )
            if subscription is None:
                return
            if services.still_due_for(subscription, period):
                return
            await services.close_stale_period(session, period, now=now)
            report.stale_closed += 1

    async def preview(
        self, now: datetime | None = None, *, limit: int = 10_000
    ) -> list[dict[str, Any]]:
        """What the run *would* do, and how far behind each subscription is. Writes nothing.

        The runbook's first-deploy check: on a box with history, the first passes convert every
        expired trial and bill every overdue period, one period per pass. This lists them — kind,
        the first period, how many periods are owed — so an operator decides before invoices
        exist. Reads under each tenant's binding, like the run itself; calls nobody.
        """
        at = now or datetime.now(UTC)
        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_billing_subscriptions(session, now=at, limit=limit)

        out: list[dict[str, Any]] = []
        for organization_id, subscription_id in due:
            async with self._work_scope(organization_id) as session:
                subscription = await selectors.get_subscription(session, subscription_id)
                if subscription is None:
                    continue
                row: dict[str, Any] = {
                    "organization_id": str(organization_id),
                    "subscription_id": str(subscription_id),
                    "product_key": subscription.product_key,
                    "state": subscription.state,
                }
                try:
                    state = subscription.state
                    trial_ends_at = subscription.trial_ends_at
                    start, end = subscription.current_period_start, subscription.current_period_end
                    periods: list[domain.DuePeriod] = []
                    while len(periods) < 120:
                        step = domain.due_period(
                            state=state,
                            trial_ends_at=trial_ends_at,
                            current_period_start=start,
                            current_period_end=end,
                            billing_interval=subscription.billing_interval,
                            now=at,
                        )
                        if step is None:
                            break
                        periods.append(step)
                        state = STATE_ACTIVE if state == STATE_TRIAL else state
                        start, end = step.start, step.end
                    row["kind"] = periods[0].kind if periods else None
                    row["first_period_start"] = periods[0].start.isoformat() if periods else None
                    row["periods_owed"] = len(periods)
                    row["invoiced"] = domain.bills_period(subscription.state)
                except domain.NotBillable as exc:
                    row["error"] = str(exc)
                out.append(row)
        return out


async def main(argv: list[str] | None = None) -> None:
    """`python -m app.billing_run --dry-run` — the preview above, one JSON line per subscription
    and a summary line last. The loop itself runs from `app.worker` (or `app.cron`), not here."""
    import argparse
    import json

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

    parser = argparse.ArgumentParser(prog="python -m app.billing_run")
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.parse_args(argv)

    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    sweep = BillingRunSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        billing=HttpBilling(cfg.billing_service_url, cfg),
        payment=HttpPayment(cfg.payment_service_url, cfg),
    )
    rows = await sweep.preview()
    for row in rows:
        print(json.dumps(row))  # noqa: T201 - an operator's report on stdout
    print(  # noqa: T201
        json.dumps(
            {
                "subscriptions": len(rows),
                "trial_conversions": sum(1 for r in rows if r.get("kind") == "trial_conversion"),
                "periods_owed": sum(int(r.get("periods_owed") or 0) for r in rows),
                "not_billable": sum(1 for r in rows if "error" in r),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
