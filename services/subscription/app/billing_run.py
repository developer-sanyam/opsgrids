"""The billing run: trials convert, periods roll, upgrades prorate, stops close their usage — and
each invoice is raised exactly once.

Before this, nothing ever looked at `trial_ends_at` or `current_period_end`: trials never
converted, nothing recurred, and billing's finalize route had no caller. This is a sweep in
`app/worker.py`, beside the dunning ladder, in the same process and the same shape (ADR-0005 §6;
its addendum of 2026-09-23 records the rules, and its "review fixes" section the second round).

**Claim → call → record**, because no network call may run inside a database transaction:

1. **Discover** (outbox role, BYPASSRLS, ids only, granted columns only): trials whose end has
   passed and periods that have closed — **excluding** any subscription whose claim is leased or
   backing off, ordered by when each next becomes workable, so a stuck row never blocks the batch
   — plus rows that are their own work (a proration, a closing period, an invoice to void).
2. **Claim** (app role, tenant-bound, one short transaction): lock, re-decide from stored
   timestamps, apply a downgrade waiting for this roll, write or re-find the claim row, lease it.
3. **Call** (no transaction): billing prices and finalizes (or voids) under a per-claim key; for
   a trial conversion that owes money, payment says whether a mandate can collect it.
4. **Record** (app role, tenant-bound, one short transaction): write billing's answer and make the
   transition with its outbox event.

**Two clocks, on purpose.** Whether a period is *due* is decided against the pass's `now` — the
stored-boundary rule, and the seam a test drives a year through in milliseconds. Leases, backoff
and `recorded_at` use the **wall clock** (`services._wall`): how long a process may be mid-call
is a fact about now, and a synthetic instant must never lease a row for a month or expire one early.

Every pass logs what it did (`billing_run.pass`), every failure names its subscription
(`billing_run.failed`), and `/health/billing-run` exposes the counts an alarm should watch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain
from app.config import Settings, parse_cutover
from app.models import (
    LIFECYCLE_KINDS,
    PERIOD_FINALIZE_PENDING,
    PERIOD_PENDING,
    PERIOD_VOID_PENDING,
    STATE_ACTIVE,
    STATE_TRIAL,
    BillingPeriod,
)
from app.supervise import backoff_loop
from platform_core.config import ServiceSettings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("subscription.billing_run")

#: A tenant-bound transaction for one organization — `session_scope` in the process, a test's own
#: in the suite. The same seam `app.cron.DunningSweep` takes.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

#: The claim kind → the billing request kind.
_BILLING_KIND = {
    domain.KIND_TRIAL_CONVERSION: "period",
    domain.KIND_RENEWAL: "period",
    domain.KIND_PRORATION: "proration",
    domain.KIND_CLOSE: "close",
    # An opening is billed as a proration from nothing: the period's recurring share for the time
    # remaining, less zero (review round 3).
    domain.KIND_OPENING: "proration",
}


# --------------------------------------------------------------------- the calls


@dataclass(frozen=True)
class BilledInvoice:
    """billing's answer: the claim's one invoice, and whether this call made it."""

    invoice_id: uuid.UUID
    total_minor: int
    currency: str
    created: bool


@dataclass(frozen=True)
class VoidResult:
    """billing's answer to a void: whether an invoice under the key was (or already was) void."""

    voided: bool
    invoice_id: uuid.UUID | None


@dataclass(frozen=True)
class PeriodRequest:
    """Everything a billing call sends, read off the claimed row — so a retry after a crash sends
    exactly what the first attempt did, whatever has changed on the subscription since."""

    organization_id: uuid.UUID
    subscription_id: uuid.UUID
    period_id: uuid.UUID
    kind: str
    product_key: str
    plan_version_id: uuid.UUID
    currency: str
    period_start: datetime
    period_end: datetime | None
    usage_start: datetime | None
    usage_end: datetime | None
    items: list[dict[str, Any]]
    change_id: uuid.UUID | None = None
    from_plan_version_id: uuid.UUID | None = None
    from_items: list[dict[str, Any]] = field(default_factory=list)
    full_period_start: datetime | None = None

    @property
    def billing_kind(self) -> str:
        return _BILLING_KIND[self.kind]

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
            change_id=period.change_id,
            from_plan_version_id=period.from_plan_version_id,
            from_items=list(period.from_items or []),
            full_period_start=period.full_period_start,
        )


class Billing(Protocol):
    async def bill_period(self, request: PeriodRequest) -> BilledInvoice | None: ...

    async def finalize_period(self, request: PeriodRequest) -> bool: ...

    async def void_period(self, request: PeriodRequest, *, reason: str) -> VoidResult: ...


class Payment(Protocol):
    async def can_collect(
        self, *, organization_id: uuid.UUID, currency: str, amount_minor: int
    ) -> bool: ...


class BillingRunCallError(RuntimeError):
    """A sibling answered with an error. The claim stays pending and backs off; `permanent` when
    the answer was a 4xx — a data defect a retry will not fix by itself."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


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


def _key_fields(request: PeriodRequest) -> dict[str, Any]:
    """The fields billing builds the claim's idempotency key from."""
    return {
        "organization_id": str(request.organization_id),
        "subscription_id": str(request.subscription_id),
        "kind": request.billing_kind,
        "period_start": request.period_start.isoformat(),
        "change_id": str(request.change_id) if request.change_id else None,
    }


def _raise_for(response: httpx.Response, who: str) -> None:
    if response.status_code >= 400:
        raise BillingRunCallError(
            f"{who} answered {response.status_code}: {response.text[:200]}",
            permanent=400 <= response.status_code < 500 and response.status_code != 429,
        )


class HttpBilling:
    """billing's `POST /internal/v1/subscription-invoices` and `…:void`."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = settings
        self._timeout = timeout

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.post(
                    path, json=body, headers=_headers(self._settings, "billing")
                )
        except httpx.HTTPError as exc:
            raise BillingRunCallError(f"billing unreachable: {exc!s}"[:200]) from exc
        _raise_for(response, "billing")
        data: dict[str, Any] = response.json()
        return data

    async def bill_period(self, request: PeriodRequest) -> BilledInvoice | None:
        assert request.period_end is not None
        body: dict[str, Any] = {
            **_key_fields(request),
            "product_key": request.product_key,
            "plan_version_id": str(request.plan_version_id),
            "currency": request.currency,
            "period_end": request.period_end.isoformat(),
            "items": request.items,
        }
        if request.usage_start is not None and request.usage_end is not None:
            body["usage_period_start"] = request.usage_start.isoformat()
            body["usage_period_end"] = request.usage_end.isoformat()
        if request.billing_kind == "proration":
            body["from_plan_version_id"] = str(request.from_plan_version_id)
            body["from_items"] = request.from_items
            body["full_period_start"] = (
                request.full_period_start.isoformat() if request.full_period_start else None
            )
        data = await self._post("/internal/v1/subscription-invoices", body)
        invoice = data["invoice"]
        if invoice is None:
            return None  # a proration that netted to nothing: no invoice at all
        return BilledInvoice(
            invoice_id=uuid.UUID(invoice["id"]),
            total_minor=int(invoice["total_minor"]),
            currency=str(invoice["currency"]),
            created=bool(data["created"]),
        )

    async def finalize_period(self, request: PeriodRequest) -> bool:
        """Finalize the recorded draft under the claim's key — idempotent; True if this call did."""
        data = await self._post(
            "/internal/v1/subscription-invoices:finalize",
            {**_key_fields(request), "product_key": request.product_key},
        )
        return bool(data["finalized"])

    async def void_period(self, request: PeriodRequest, *, reason: str) -> VoidResult:
        data = await self._post(
            "/internal/v1/subscription-invoices:void", {**_key_fields(request), "reason": reason}
        )
        raw = data.get("invoice_id")
        return VoidResult(voided=bool(data["voided"]), invoice_id=uuid.UUID(raw) if raw else None)


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
        _raise_for(response, "payment")
        return bool(response.json()["usable"])


# --------------------------------------------------------------------- the sweep


@dataclass
class RunReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    activated: int = 0
    past_due: int = 0
    renewed: int = 0
    prorated: int = 0
    opened: int = 0
    closed: int = 0
    finalized: int = 0
    voided: int = 0
    skipped_suspended: int = 0
    skipped_pre_cutover: int = 0
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
            + self.prorated
            + self.opened
            + self.closed
            + self.finalized
            + self.voided
            + self.skipped_suspended
            + self.skipped_pre_cutover
            + self.superseded
            + self.failed
            + self.stale_closed
        )


class BillingRunSweep:
    """Drains due billing work one pass at a time. Injected with its sessions and its two
    clients, so a test drives real PostgreSQL and real locks with fake siblings."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        billing: Billing,
        payment: Payment,
        batch_size: int = 50,
        poll_interval: float = 300.0,
        not_before: datetime | None = None,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._billing = billing
        self._payment = payment
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        #: The billing cutover (`BILLING_RUN_NOT_BEFORE`, final review fix 3). None only in tests
        #: and in a dry run with no cutover chosen yet: the loop refuses to start without one.
        self._not_before = not_before
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info("billing_run.started", poll_interval=self._poll_interval)
        # Discovery failing (a database restart, a deadlock) is logged and retried with capped
        # backoff rather than raised: raising would take the dunning sweep and the payment
        # consumer in this process down with it. `CancelledError` still propagates.
        await backoff_loop(
            "billing_run",
            self.tick,
            poll_interval=self._poll_interval,
            stopped=lambda: self._stop,
            on_pass=self._log_pass,
        )

    @staticmethod
    def _log_pass(report: RunReport) -> None:
        if report.acted:
            log.info(
                "billing_run.pass",
                considered=report.considered,
                issued=report.issued,
                activated=report.activated,
                past_due=report.past_due,
                renewed=report.renewed,
                prorated=report.prorated,
                opened=report.opened,
                closed=report.closed,
                finalized=report.finalized,
                voided=report.voided,
                skipped_suspended=report.skipped_suspended,
                skipped_pre_cutover=report.skipped_pre_cutover,
                superseded=report.superseded,
                failed=report.failed,
                stale_closed=report.stale_closed,
            )

    async def tick(self, now: datetime | None = None) -> RunReport:
        """One pass. `now` decides what is due; leases and backoff read the wall clock."""
        wall = datetime.now(UTC)
        at = now or wall
        report = RunReport()

        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_billing_subscriptions(
                session, now=at, wall=wall, limit=self._batch_size
            )
            actionable = await selectors.actionable_periods(
                session, wall=wall, limit=self._batch_size
            )
            stale = await selectors.stale_pending_periods(
                session, wall=wall, limit=self._batch_size
            )

        report.considered = len(due) + len(actionable)
        for organization_id, subscription_id in due:
            await self._guarded(
                self._bill_one(organization_id, subscription_id, at, report),
                organization_id,
                subscription_id,
                report,
            )
        for organization_id, period_id in actionable:
            await self._guarded(
                self._work_row(organization_id, period_id, report),
                organization_id,
                period_id,
                report,
            )
        for organization_id, period_id in stale:
            await self._guarded(
                self._close_if_stale(organization_id, period_id, report),
                organization_id,
                period_id,
                report,
            )
        return report

    async def _guarded(
        self, work: Any, organization_id: uuid.UUID, target_id: uuid.UUID, report: RunReport
    ) -> None:
        try:
            await work
        except Exception as exc:
            # One tenant's failure is one tenant's: logged with its ids, counted, and the pass
            # carries on. The claim (if any) stays pending and is retried after its lease.
            report.failed += 1
            log.error(
                "billing_run.failed",
                organization_id=str(organization_id),
                target_id=str(target_id),
                error=str(exc)[:300],
            )

    async def _fail(
        self, organization_id: uuid.UUID, period_id: uuid.UUID, exc: BaseException
    ) -> None:
        from app import selectors, services

        async with self._work_scope(organization_id) as session:
            claimed = await selectors.lock_billing_period_by_id(session, organization_id, period_id)
            if claimed is not None:
                await services.fail_billing_period(
                    session,
                    claimed,
                    error=str(exc),
                    permanent=bool(getattr(exc, "permanent", False)),
                )

    async def _bill_one(
        self,
        organization_id: uuid.UUID,
        subscription_id: uuid.UUID,
        now: datetime,
        report: RunReport,
    ) -> None:
        """One due subscription. Any failure — in the claim, the call or the record — backs off
        the subscription's pending claim (review round 2, fix M4), so a row that fails the same
        way every pass cannot be rediscovered at the head of every batch."""
        try:
            await self._bill_one_unguarded(organization_id, subscription_id, now, report)
        except Exception as exc:
            await self._fail_subscription(organization_id, subscription_id, exc)
            raise

    async def _fail_subscription(
        self, organization_id: uuid.UUID, subscription_id: uuid.UUID, exc: BaseException
    ) -> None:
        from app import selectors, services

        try:
            async with self._work_scope(organization_id) as session:
                claim = await selectors.pending_lifecycle_claim(
                    session, organization_id, subscription_id
                )
                if claim is not None:
                    locked = await selectors.lock_billing_period_by_id(
                        session, organization_id, claim.id
                    )
                    if locked is not None:
                        await services.fail_billing_period(
                            session,
                            locked,
                            error=str(exc),
                            permanent=bool(getattr(exc, "permanent", False)),
                        )
        except Exception as inner:  # pragma: no cover - the database itself is down
            log.error("billing_run.backoff_failed", error=str(inner)[:300])

    async def _bill_one_unguarded(
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
                session, organization_id, subscription_id, skip_locked=True
            )
            if subscription is None:
                report.skipped += 1  # another replica holds it, or it is not this tenant's
                return

            def decide() -> domain.DuePeriod | None:
                assert subscription is not None
                return domain.due_period(
                    state=subscription.state,
                    trial_ends_at=subscription.trial_ends_at,
                    current_period_start=subscription.current_period_start,
                    current_period_end=subscription.current_period_end,
                    billing_interval=subscription.billing_interval,
                    now=now,
                    # No usage from before the cutover is ever billed (final review, fix 3).
                    usage_billed_through=domain.usage_floor(
                        subscription.usage_billed_through, self._not_before
                    ),
                )

            due = decide()
            if due is None:
                report.skipped += 1  # dealt with between discovery and this lock
                return
            existing = await selectors.lock_billing_period(
                session, organization_id, subscription_id, due.kind, due.start
            )
            if existing is not None and existing.state != PERIOD_PENDING:
                report.skipped += 1  # recorded already; nothing for this period
                return
            # A downgrade waiting for a roll is applied only to a period **not yet claimed**
            # (review round 2, fix B2): a claimed period keeps the snapshot it was claimed with,
            # and changing the pin under it would entitle one plan and bill another. Applied, the
            # new period is priced at the lower plan and rolls on its cadence; the ending period's
            # usage was closed at the old pin, so this one carries none.
            if (
                existing is None
                and due.kind == domain.KIND_RENEWAL
                and await services.apply_pending_change(session, subscription, boundary=due.start)
            ):
                due = decide()
                assert due is not None
            if not due.billable:
                await services.record_not_billable(session, subscription, due)
                report.failed += 1
                log.error(
                    "billing_run.not_billable",
                    organization_id=str(organization_id),
                    subscription_id=str(subscription_id),
                    error=due.error,
                )
                return
            if (
                existing is None
                and domain.cutover_action(
                    kind=due.kind,
                    period_start=due.start,
                    usage_start=due.usage_start,
                    usage_end=due.usage_end,
                    not_before=self._not_before,
                )
                == domain.CUTOVER_SKIP
            ):
                # Began before billing went live: rolled (or converted) with no invoice, so the
                # subscription starts paying at its first period after the cutover (fix 3).
                await services.skip_pre_cutover_period(session, subscription, due)
                report.skipped_pre_cutover += 1
                return
            if (
                existing is None
                and due.kind == domain.KIND_RENEWAL
                and not domain.bills_period(subscription.state)
                and not services.served_into(subscription, due.start)
            ):
                # A suspended roll — only when nothing is claimed for the period. A claim made
                # before the suspension is finished by the record step, which voids its advance
                # charge and keeps its usage (round 2, M4).
                await services.skip_suspended_period(session, subscription, due)
                report.skipped_suspended += 1
                return
            period = await services.claim_billing_period(session, subscription, due)
            if period is None:
                report.skipped += 1  # leased by a replica mid-call, backing off, or recorded
                return
            request = PeriodRequest.of(period, subscription.product_key)

        # --- call (no transaction is open) ---------------------------------------------------
        try:
            billed = await self._billing.bill_period(request)
            if billed is None:  # pragma: no cover - only a proration can net to nothing
                raise BillingRunCallError("billing raised no invoice for a period", permanent=True)
            collectable: bool | None = None
            if request.kind == domain.KIND_TRIAL_CONVERSION and billed.total_minor > 0:
                collectable = await self._payment.can_collect(
                    organization_id=organization_id,
                    currency=billed.currency,
                    amount_minor=billed.total_minor,
                )
        except Exception as exc:
            await self._fail(organization_id, request.period_id, exc)
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
                session, organization_id, subscription_id, skip_locked=False
            )
            claimed = await selectors.lock_billing_period_by_id(
                session, organization_id, request.period_id
            )
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
            )

        if outcome == "already_recorded":
            report.skipped += 1
            return
        report.invoices.append(billed.invoice_id)
        if outcome == "superseded":
            report.superseded += 1
            # A draft nobody was told about (round 2, M5): void it now, before anything could.
            await self._work_row(organization_id, request.period_id, report)
            return
        if outcome == "activated":
            report.activated += 1
        elif outcome == "past_due":
            report.past_due += 1
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
        # --- finalize: only now is the invoice announced, and so chargeable ----------------
        await self._work_row(organization_id, request.period_id, report)

    async def _work_row(
        self, organization_id: uuid.UUID, period_id: uuid.UUID, report: RunReport
    ) -> None:
        """A row that is its own work: bill a proration or a closing period, finalize a recorded
        draft, or void a superseded one. Claim → call → record, like a lifecycle period."""
        from app import selectors, services

        async with self._work_scope(organization_id) as session:
            period = await selectors.lock_billing_period_by_id(
                session, organization_id, period_id, skip_locked=True
            )
            if period is None:
                return
            action = {
                PERIOD_VOID_PENDING: "void",
                PERIOD_FINALIZE_PENDING: "finalize",
            }.get(period.state)
            if action is None:
                if period.state != PERIOD_PENDING or period.kind in LIFECYCLE_KINDS:
                    return
                action = "bill"
            subscription = await selectors.get_subscription(session, period.subscription_id)
            product_key = subscription.product_key if subscription is not None else ""
            # The cutover decides a row once, before its first lease (`attempts == 0`). A row
            # already sent to billing is retried exactly as sent — neither skipped nor re-clamped
            # — even if someone moved the cutover later in between (documented as never to be
            # done): billing may hold an invoice for it, and the retry must land on that invoice.
            if action == "bill" and period.attempts == 0:
                cutover = domain.cutover_action(
                    kind=period.kind,
                    period_start=period.period_start,
                    usage_start=period.usage_start,
                    usage_end=period.usage_end,
                    not_before=self._not_before,
                )
                if cutover == domain.CUTOVER_SKIP:
                    await services.settle_pre_cutover_row(session, period)
                    report.skipped_pre_cutover += 1
                    return
                if cutover == domain.CUTOVER_CLAMP:
                    # Only the usage window moves; `period_start` is the billing key and stays.
                    period.usage_start = self._not_before
            if await services.claim_actionable(session, period) is None:
                report.skipped += 1
                return
            request = PeriodRequest.of(period, product_key)

        voided: VoidResult | None = None
        billed: BilledInvoice | None = None
        try:
            if action == "void":
                voided = await self._billing.void_period(request, reason="superseded period")
            elif action == "finalize":
                await self._billing.finalize_period(request)
            else:
                billed = await self._billing.bill_period(request)
        except Exception as exc:
            await self._fail(organization_id, period_id, exc)
            report.failed += 1
            log.error(
                "billing_run.failed",
                organization_id=str(organization_id),
                period_id=str(period_id),
                kind=request.kind,
                action=action,
                error=str(exc)[:300],
            )
            return

        async with self._work_scope(organization_id) as session:
            claimed = await selectors.lock_billing_period_by_id(session, organization_id, period_id)
            if claimed is None:  # pragma: no cover - cascade-deleted
                return
            if voided is not None:
                outcome = await services.record_void(
                    session, claimed, voided=voided.voided, invoice_id=voided.invoice_id
                )
                if outcome == "voided":
                    report.voided += 1
                return
            if action == "finalize":
                if await services.record_finalized(session, claimed) == "finalized":
                    report.finalized += 1
                return
            outcome = await services.record_actionable_invoice(
                session,
                claimed,
                invoice_id=billed.invoice_id if billed is not None else None,
                total_minor=billed.total_minor if billed is not None else 0,
            )
        if outcome in ("already_recorded", "nothing_to_prorate") or billed is None:
            return
        report.invoices.append(billed.invoice_id)
        if outcome == "prorated":
            report.prorated += 1
        elif outcome == "opened":
            report.opened += 1
        else:
            report.closed += 1
        await self._work_row(organization_id, period_id, report)

    async def _close_if_stale(
        self, organization_id: uuid.UUID, period_id: uuid.UUID, report: RunReport
    ) -> None:
        """A lifecycle claim with a lapsed lease: leave it if its subscription is still due for it
        (the due pass re-claims it), otherwise send it to be voided."""
        from app import selectors, services

        async with self._work_scope(organization_id) as session:
            period = await selectors.lock_billing_period_by_id(
                session, organization_id, period_id, skip_locked=True
            )
            if period is None or period.state != PERIOD_PENDING:
                return
            if period.kind not in LIFECYCLE_KINDS:
                return
            subscription = await selectors.lock_subscription(
                session, organization_id, period.subscription_id, skip_locked=True
            )
            if subscription is None or services.still_due_for(subscription, period):
                return
            await services.close_stale_period(session, period)
            report.stale_closed += 1
        await self._work_row(organization_id, period_id, report)

    async def preview(
        self, now: datetime | None = None, *, limit: int = 10_000
    ) -> list[dict[str, Any]]:
        """What the run *would* do, and how far behind each subscription is. Writes nothing.

        The runbook's first-deploy check. Per subscription: the first kind and period, how many
        periods are owed, how many of those would be **invoiced**, how many would be **skipped
        before the cutover** (`pre_cutover`), how many would only **roll** (suspended), and for a
        trial whether its grace window — anchored at the trial's end — would already have
        expired, which means the dunning sweep suspends it on its next pass if no mandate can
        collect (a trial that ended before the cutover converts with no invoice instead).
        """
        at = now or datetime.now(UTC)
        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            due = await selectors.due_billing_subscriptions(
                session, now=at, wall=datetime.now(UTC), limit=limit
            )

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
                state = subscription.state
                start, end = subscription.current_period_start, subscription.current_period_end
                periods: list[domain.DuePeriod] = []
                error: str | None = None
                while len(periods) < 120:
                    step = domain.due_period(
                        state=state,
                        trial_ends_at=subscription.trial_ends_at,
                        current_period_start=start,
                        current_period_end=end,
                        billing_interval=subscription.billing_interval,
                        now=at,
                    )
                    if step is None:
                        break
                    if not step.billable:
                        error = step.error
                        break
                    periods.append(step)
                    state = STATE_ACTIVE if state == STATE_TRIAL else state
                    start, end = step.start, step.end
                invoiced = domain.bills_period(subscription.state)
                pre_cutover = [
                    p
                    for p in periods
                    if self._not_before is not None and p.start < self._not_before
                ]
                billable = len(periods) - len(pre_cutover)
                row["kind"] = periods[0].kind if periods else None
                row["first_period_start"] = periods[0].start.isoformat() if periods else None
                row["periods_owed"] = len(periods)
                row["periods_pre_cutover"] = len(pre_cutover)
                row["periods_invoiced"] = billable if invoiced else 0
                row["periods_suspended_roll"] = 0 if invoiced else billable
                row["grace_already_expired"] = bool(
                    subscription.state == STATE_TRIAL
                    and subscription.trial_ends_at is not None
                    and domain.grace_ends_at(subscription.trial_ends_at) <= at
                    and not (
                        self._not_before is not None
                        and subscription.trial_ends_at < self._not_before
                    )
                )
                if error:
                    row["error"] = error
                out.append(row)
        return out

    async def preview_backlog(self, *, limit: int = 10_000) -> dict[str, Any]:
        """The rows that are their own work, by what the run would do with each: bill an opening,
        a close or a proration (split into invoiced and skipped `pre_cutover`), finalize a
        recorded draft, or void a superseded one. Writes nothing (final review, fix 3)."""
        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            rows = await selectors.billing_backlog(session, limit=limit)

        by_kind = {
            domain.KIND_OPENING: 0,
            domain.KIND_CLOSE: 0,
            domain.KIND_PRORATION: 0,
            PERIOD_FINALIZE_PENDING: 0,
            PERIOD_VOID_PENDING: 0,
        }
        to_invoice = pre_cutover = clamped = 0
        for organization_id, period_id in rows:
            async with self._work_scope(organization_id) as session:
                period = await selectors.get_billing_period(session, organization_id, period_id)
            if period is None:
                continue
            if period.state in (PERIOD_FINALIZE_PENDING, PERIOD_VOID_PENDING):
                by_kind[period.state] += 1
                continue
            by_kind[period.kind] = by_kind.get(period.kind, 0) + 1
            # As the run decides it: only a row never yet sent to billing is judged by the cutover.
            cutover = (
                domain.cutover_action(
                    kind=period.kind,
                    period_start=period.period_start,
                    usage_start=period.usage_start,
                    usage_end=period.usage_end,
                    not_before=self._not_before,
                )
                if period.attempts == 0
                else None
            )
            if cutover == domain.CUTOVER_SKIP:
                pre_cutover += 1
            else:
                to_invoice += 1
                clamped += int(cutover == domain.CUTOVER_CLAMP)
        return {
            "backlog": by_kind,
            "backlog_invoiced": to_invoice,
            "backlog_pre_cutover": pre_cutover,
            "backlog_usage_clamped": clamped,
        }


def billing_run_loop(cfg: Settings, sweep: BillingRunSweep) -> Coroutine[Any, Any, None] | None:
    """The run's loop coroutine, or None when it must not run — the one place `app.worker` and
    `app.cron` decide it (final review, fix 3).

    Off unless `BILLING_RUN_ENABLED=true` (review fix 6). On, it **refuses to start without
    `BILLING_RUN_NOT_BEFORE`**: with no cutover, the first passes would invoice every period and
    conversion a box has owed since before billing existed. Refused rather than crashed — the
    payment consumer and the dunning sweep in the same process keep running — and logged at
    error, so the alarm on `/health/billing-run`'s `overdue` has a named cause.
    """
    if not cfg.billing_run_enabled:
        # Staged rollout (the runbook's billing-run section): the process runs, the run does not.
        # Loud, because a box left like this converts no trial and invoices nothing.
        log.warning("billing_run.disabled", hint="BILLING_RUN_ENABLED=false; nothing is invoiced")
        return None
    if cfg.billing_run_not_before_invalid:
        # A malformed cutover is refused here, not at settings load: the worker's other loops
        # must keep running (second post-review fixup).
        log.error("billing_run.refused", hint=cfg.billing_run_not_before_invalid)
        return None
    if cfg.billing_run_not_before is None:
        log.error(
            "billing_run.refused",
            hint=(
                "BILLING_RUN_ENABLED=true but BILLING_RUN_NOT_BEFORE is unset; set it to the "
                "moment billing goes live (with a UTC offset) — see the runbook"
            ),
        )
        return None
    log.info("billing_run.cutover", not_before=cfg.billing_run_not_before.isoformat())
    return sweep.run()


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
    parser.add_argument(
        "--not-before",
        default=None,
        help="preview against this cutover instead of BILLING_RUN_NOT_BEFORE (ISO, with offset)",
    )
    args = parser.parse_args(argv)

    cfg = settings()
    not_before = cfg.billing_run_not_before
    if args.not_before:
        parsed, problem = parse_cutover(args.not_before)
        if problem is not None:
            parser.error(problem.replace("BILLING_RUN_NOT_BEFORE", "--not-before"))
        not_before = parsed
    elif cfg.billing_run_not_before_invalid:
        parser.error(cfg.billing_run_not_before_invalid)
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    sweep = BillingRunSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        billing=HttpBilling(cfg.billing_service_url, cfg),
        payment=HttpPayment(cfg.payment_service_url, cfg),
        not_before=not_before,
    )
    rows = await sweep.preview()
    for row in rows:
        print(json.dumps(row))  # noqa: T201 - an operator's report on stdout
    print(json.dumps(summarise_preview(rows, await sweep.preview_backlog(), not_before)))  # noqa: T201


def summarise_preview(
    rows: list[dict[str, Any]], backlog: dict[str, Any], not_before: datetime | None
) -> dict[str, Any]:
    """The dry run's last line: what the first passes would invoice, skip and roll."""
    return {
        "not_before": not_before.isoformat() if not_before else None,
        "subscriptions": len(rows),
        "trial_conversions": sum(1 for r in rows if r.get("kind") == "trial_conversion"),
        "periods_invoiced": sum(int(r.get("periods_invoiced") or 0) for r in rows),
        "periods_pre_cutover": sum(int(r.get("periods_pre_cutover") or 0) for r in rows),
        "periods_suspended_roll": sum(int(r.get("periods_suspended_roll") or 0) for r in rows),
        "grace_already_expired": sum(1 for r in rows if r.get("grace_already_expired")),
        "not_billable": sum(1 for r in rows if "error" in r),
        **backlog,
    }


if __name__ == "__main__":
    asyncio.run(main())
