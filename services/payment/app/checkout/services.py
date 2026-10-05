"""Writes for platform checkout — a customer paying one of OpsGrids' own invoices in the provider's
hosted checkout (ADR-0029, Phase A).

**Why it exists.** The platform's only collection path was a server-side debit of a saved mandate,
and no mandate can be created yet. A customer who owes an invoice — a trial that ended into
`no_mandate`, a dunning ladder running out — needs a way to pay it now. The checkout is that: the
customer is present, pays in Razorpay's or Cashfree's own page, and the payment is learned by
asking the provider, never by believing the browser. A captured payment recovers the subscription
through the existing `commerce.payment.captured` path; the state machine is unchanged.

**The charge.** A checkout is a `flow = 'checkout'` row of `charges`, raised from the invoice's own
`debit`-flow charge — the amount, the currency, the subscription and the product are **read from it,
never from the request body**. At most one is open per invoice (a partial unique index backs the
service). It is `created` with no provider payment until the customer pays, ends `captured`, or ends
`failed` as `checkout_expired` — and **nothing else moves it**: a failed or abandoned attempt is the
customer retrying, not the charge failing, so it never emits `commerce.payment.failed`
(`services._reconcile_charge`), and the stuck-charge sweep never sees it (`flow = 'debit'` filters).

**Claim → call → record** (`docs/io-outside-transactions.md`): a short transaction decides and
claims (the lease is the right to call the provider for this charge, one caller at a time); the
provider call runs on a worker thread under an overall deadline with **no transaction open**; a
second short transaction records the Order id. Every function here that touches a session takes one
already bound to the organization and inside its transaction; the callers (`app/api/checkout.py`,
`app/checkout/cron.py`) own the transactions, so nothing here can hold one across a provider call.

**The recorded Order id is the checkout's identity at the provider** (ADR-0029, sandbox facts of
2026-10-06: Razorpay accepts a duplicate receipt, and its receipt search can miss a fresh Order).
It is recorded before any browser sees it, it is the only id a browser is ever handed
(`withheld_order`), every reopen fetches that Order by its id, and the poller asks about it — plus,
before closing a checkout and through its late window, any **stray** Order carrying the same
receipt (`fetch_stray_payments`), the duplicate an unknown-outcome create may have left behind.

**What is never done here:** trust a browser (`confirm` only checks an HMAC as a precondition for
asking the provider), store or log the customer's details or the checkout session token, or call a
provider other than the one the row was created under.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app import services as charge_services
from app.collections.services import ProviderRefusedProblem, ProviderUnreachableProblem
from app.config import settings
from app.models import (
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_CREATED,
    CHARGE_FAILED,
    FLOW_CHECKOUT,
    FLOW_DEBIT,
    PAYEE_PLATFORM,
    AuditLog,
    Charge,
)
from app.providers.base import (
    CheckoutCustomer,
    CheckoutProvider,
    CheckoutRequest,
    CheckoutSession,
    PaymentProvider,
    PaymentSnapshot,
    ProviderCallError,
    ProviderCapabilities,
    StrayPayment,
    best_attempt,
    valid_phone,
)
from app.providers.wire import (
    CREDENTIALS,
    ProviderMisconfigured,
    ProviderRejected,
    call_deadline,
)
from platform_core.audit import record_audit
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger

log = get_logger("payment.checkout")

# --------------------------------------------------------------------------- refusal codes
#
# The stable machine codes `POST /payment/checkouts` refuses with, in `errors[0].code` — the shape
# the rest of the platform uses for a coded problem (`{"field"?, "code", "message"}`). A client
# branches on the code, never on the sentence (which may be reworded); each 409 has its own, so
# "already paid" can never be confused with "try again in a moment". Part of the API contract
# (frontend PR #107): rename one only with the frontend.

#: 409: a charge of the invoice is captured. The one refusal a client may read as "paid".
CODE_INVOICE_ALREADY_PAID = "invoice_already_paid"
#: 409: another request holds the invoice's checkout (a call in flight, or a concurrent open that
#: changed the picture); the same request a moment later succeeds.
CODE_CHECKOUT_IN_PROGRESS = "checkout_in_progress"
#: 409: the invoice's open checkout is past its expiry and could not be closed yet — its provider
#: could not be asked, or it still reports a payment in flight.
CODE_PREVIOUS_CHECKOUT_NOT_CLOSABLE = "previous_checkout_not_closable"
#: 409: a checkout of the invoice that already closed reports a payment still being processed.
CODE_PREVIOUS_PAYMENT_IN_FLIGHT = "previous_checkout_payment_in_flight"
#: 409: the provider the checkout would use has no hosted checkout.
CODE_CHECKOUT_NOT_SUPPORTED = "checkout_not_supported"
#: 422: the invoice's charge is for nothing.
CODE_NOTHING_TO_PAY = "nothing_to_pay"
#: 422: the provider needs the paying customer's phone, and the request carried no usable one.
CODE_PHONE_REQUIRED = "phone_required"
#: 422: the provider needs the paying customer's details, and the request carried none.
CODE_CUSTOMER_DETAILS_REQUIRED = "customer_details_required"
#: 422: the invoice's currency cannot be paid online.
CODE_CURRENCY_UNSUPPORTED = "currency_unsupported"
#: 422: the invoice's amount is below what can be paid online.
CODE_AMOUNT_BELOW_MINIMUM = "amount_below_minimum"

_INVOICE = "invoice_id"
_PHONE = "customer.phone"

#: The 422 refusals: code → (the field at fault, the sentence). The sentences are unchanged from the
#: first contract, so a client still matching them keeps working while it moves to the codes.
_REFUSALS: dict[str, tuple[str, str]] = {
    CODE_NOTHING_TO_PAY: (_INVOICE, "There is nothing to pay on this invoice"),
    CODE_PHONE_REQUIRED: (_PHONE, "Add a customer phone number to pay online"),
    CODE_CUSTOMER_DETAILS_REQUIRED: ("customer", "Add the customer's details to pay online"),
    CODE_CURRENCY_UNSUPPORTED: (_INVOICE, "Only INR invoices can be paid online"),
    CODE_AMOUNT_BELOW_MINIMUM: (
        _INVOICE,
        "The amount is below the minimum that can be paid online (Rs 1)",
    ),
}

#: The provider's own refusals that mean "this request is wrong" (an adapter's stable code), and so
#: are the caller's 422 — with the API's code — and not a 502 from upstream.
_PROVIDER_REQUEST_CODES: dict[str, str] = {
    "unsupported_currency": CODE_CURRENCY_UNSUPPORTED,
    "amount_below_minimum": CODE_AMOUNT_BELOW_MINIMUM,
    "customer_phone_required": CODE_PHONE_REQUIRED,
    "customer_details_required": CODE_CUSTOMER_DETAILS_REQUIRED,
}


def refusal(code: str) -> ValidationProblem:
    """The 422 for one of `_REFUSALS`: `errors = [{field, code, message}]`."""
    field_name, message = _REFUSALS[code]
    return ValidationProblem(
        message, errors=[{"field": field_name, "code": code, "message": message}]
    )


def conflict(code: str, detail: str, *, field_name: str | None = None) -> ConflictProblem:
    """A 409 carrying its machine code: `errors = [{field?, code, message}]`."""
    error: dict[str, str] = {"code": code, "message": detail}
    if field_name is not None:
        error = {"field": field_name, **error}
    return ConflictProblem(detail, errors=[error])


def checkout_in_progress() -> ConflictProblem:
    return conflict(
        CODE_CHECKOUT_IN_PROGRESS,
        "This invoice's checkout is being prepared; try again in a moment",
    )


class InvalidCheckoutConfirmationProblem(ProblemError):
    """A checkout confirmation that does not carry the proof it must: a missing field, an Order that
    is not this checkout's, or a signature that is not the HMAC of `order_id|payment_id` under the
    key secret. 400, and **nothing is marked paid**. The message stays vague: a prober learns
    nothing about which of the three it was."""

    status_code = 400
    problem_type = "invalid-checkout-confirmation"
    title = "Invalid checkout confirmation"


class CheckoutUnavailableProblem(UpstreamUnavailableProblem):
    """Online payment cannot be offered right now — the platform's own provider credentials were
    refused, so nothing is the customer's to fix. 503, and an operator has been paged."""


def _now() -> datetime:
    return datetime.now(UTC)


async def bind(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """The first statement of every transaction here: the tenant binding RLS and every
    `WITH CHECK` apply to (the GUC is transaction-local, so each short transaction binds again)."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


def _lease() -> timedelta:
    return timedelta(seconds=settings().charge_reclaim_after_seconds)


def _inflight_grace() -> timedelta:
    return timedelta(seconds=settings().checkout_inflight_grace_seconds)


def _late_window() -> timedelta:
    return timedelta(seconds=settings().checkout_late_capture_window_seconds)


def _late_gap() -> timedelta:
    return timedelta(seconds=settings().checkout_late_capture_poll_interval_seconds)


def _is_past_expiry(charge: Charge, now: datetime) -> bool:
    return charge.checkout_expires_at is not None and charge.checkout_expires_at <= now


def late_checkable(charge: Charge, *, now: datetime) -> bool:
    """Whether a **closed** checkout is still asked about: closed as `checkout_expired` (this
    platform stopped waiting; nobody's payment failed), on an Order the customer may still hold
    open, with no payment recorded, and inside `checkout_late_capture_window_seconds` of its
    expiry. A capture found then is recorded (`captured_after_local_failure`): the money moved.
    The poller, the status read-through and a signature-verified confirm all ask about such a
    checkout; nothing else about it can change."""
    return (
        charge.flow == FLOW_CHECKOUT
        and charge.payee == PAYEE_PLATFORM
        and charge.deleted_at is None
        and charge.state == CHARGE_FAILED
        and charge.failure_reason == domain.FAILURE_CHECKOUT_EXPIRED
        and charge.provider_order_id is not None
        and charge.provider_payment_id is None
        and charge.checkout_expires_at is not None
        and now < charge.checkout_expires_at + _late_window()
    )


def attempt_in_flight(snapshots: list[PaymentSnapshot]) -> bool:
    """Whether the provider reports any attempt on the Order that has not failed: one in progress
    (`created` — Cashfree `PENDING` among them — or `authorized`, an auto-capture still to run), or
    a capture this platform could not apply (`sync_not_applied`: a person must look). Only an Order
    whose every attempt failed, or that has none, is one nobody is paying."""
    return any(snapshot.status != "failed" for snapshot in snapshots)


def payment_in_flight(
    charge: Charge, snapshots: list[PaymentSnapshot] | None, *, now: datetime
) -> bool:
    """Whether a checkout must be treated as still being paid: the provider was asked, reported an
    attempt that has not failed (`attempt_in_flight`), and the checkout is inside
    `checkout_inflight_grace_seconds` of its expiry. Bounded, so an attempt the provider never
    resolves cannot hold the invoice for ever; past it, the late-capture window still records a
    capture that lands."""
    return (
        snapshots is not None
        and attempt_in_flight(snapshots)
        and charge.checkout_expires_at is not None
        and now < charge.checkout_expires_at + _inflight_grace()
    )


# --------------------------------------------------------------------------- opening a checkout


@dataclass(frozen=True, slots=True, repr=False)
class CheckoutClaim:
    """What a claim hands the call: the charge and the provider it belongs to, the request to make,
    and — for this one call, in memory — the paying customer's details. `repr` is suppressed so a
    traceback cannot print them. `stale_charge_id` is set instead when the invoice's open checkout
    has expired and must be settled (asked about, then closed) before a new one is opened."""

    organization_id: uuid.UUID
    charge_id: uuid.UUID
    provider: str
    request: CheckoutRequest | None
    amount_minor: int = 0
    currency: str = ""
    expires_at: datetime | None = None
    stale_charge_id: uuid.UUID | None = None
    #: Closed checkouts of the invoice still inside their late-capture window, to be asked about
    #: before a new checkout is opened beside them (`claim_platform_checkout`). Set instead of a
    #: request; nothing was inserted.
    recheck_charge_ids: tuple[uuid.UUID, ...] = ()
    reused: bool = False
    opened: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"CheckoutClaim(charge_id={self.charge_id})"


def _checkout_notes(charge: Charge) -> dict[str, str]:
    return {**charge_services._notes_for_charge(charge), "flow": FLOW_CHECKOUT}


def _return_url(charge_id: uuid.UUID) -> str:
    """Where Cashfree sends the browser back to — built from the platform's own base URL, never from
    the request (an open redirect), and never evidence of payment (`GET /payment/checkouts/{id}`
    is what the return page asks)."""
    base = settings().app_base_url.rstrip("/")
    return f"{base}/billing/return?charge_id={charge_id}"


def _customer_for(
    organization_id: uuid.UUID, customer: dict[str, str | None] | None
) -> CheckoutCustomer | None:
    if not customer:
        return None
    return CheckoutCustomer(
        customer_id=organization_id.hex,
        name=customer.get("name"),
        email=customer.get("email"),
        phone=customer.get("phone"),
    )


def _build_claim(
    charge: Charge,
    *,
    customer: CheckoutCustomer | None,
    reused: bool,
) -> CheckoutClaim:
    assert charge.checkout_expires_at is not None
    return CheckoutClaim(
        organization_id=charge.organization_id,
        charge_id=charge.id,
        provider=charge.provider,
        request=CheckoutRequest(
            receipt=str(charge.id),
            amount_minor=charge.amount_minor,
            currency=charge.currency,
            notes=_checkout_notes(charge),
            expires_at=charge.checkout_expires_at,
            customer=customer,
            return_url=_return_url(charge.id),
            recorded_order_id=charge.provider_order_id,
        ),
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        expires_at=charge.checkout_expires_at,
        reused=reused,
    )


async def claim_checkout_lease(
    session: AsyncSession, charge: Charge, *, now: datetime
) -> Charge | None:
    """Take the right to call the provider for an **existing** open checkout, or learn that somebody
    else holds it — one conditional `UPDATE … RETURNING`, so a double-click's second request never
    makes a second call. None when a call is in flight."""
    stmt = (
        update(Charge)
        .where(
            Charge.organization_id == charge.organization_id,
            Charge.id == charge.id,
            Charge.payee == PAYEE_PLATFORM,
            Charge.flow == FLOW_CHECKOUT,
            Charge.state.in_((CHARGE_CREATED, CHARGE_AUTHORIZED)),
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
        )
        .values(lease_until=now + _lease(), attempts=Charge.attempts + 1)
        .returning(Charge)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def close_orderless_checkout(
    session: AsyncSession,
    charge: Charge,
    *,
    now: datetime,
    request: Request | None = None,
) -> bool:
    """Close, as `checkout_expired`, an open checkout past its expiry that **never recorded an
    Order** — every attempt to open it failed at the provider (a timeout, a refusal, a tripped
    credentials latch) and the customer did not come back inside its day.

    No Order id and no checkout session was ever handed to a browser for it (the route answers only
    after `record_checkout`), so nothing can have been paid on it — even an Order whose create
    landed with its answer lost cannot be paid by anybody who never learnt its id. There is also
    nothing to ask a provider about, so the poller and `_settle` can never close it, and the open-
    checkout index would answer every later "Pay now" for the invoice with a 409 for good.

    One conditional `UPDATE … RETURNING`, and only while **no call holds the lease**: a call still
    out may yet record an Order and hand it to a browser, so that checkout waits (the caller's
    stale answer is a 409 "try again shortly" until the call ends). True when closed."""
    # Read before the statement: `populate_existing` refreshes this very object from the row.
    before = charge.state
    stmt = (
        update(Charge)
        .where(
            Charge.organization_id == charge.organization_id,
            Charge.id == charge.id,
            Charge.payee == PAYEE_PLATFORM,
            Charge.flow == FLOW_CHECKOUT,
            Charge.state.in_((CHARGE_CREATED, CHARGE_AUTHORIZED)),
            Charge.provider_order_id.is_(None),
            Charge.provider_payment_id.is_(None),
            Charge.checkout_expires_at <= now,
            or_(Charge.lease_until.is_(None), Charge.lease_until <= now),
        )
        .values(
            state=CHARGE_FAILED,
            failure_reason=domain.FAILURE_CHECKOUT_EXPIRED,
            lease_until=None,
            next_attempt_at=None,
            updated_at=now,
        )
        .returning(Charge)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    closed = (await session.execute(stmt)).scalar_one_or_none()
    if closed is None:
        return False
    record_audit(
        session,
        AuditLog,
        organization_id=closed.organization_id,
        actor_id=None,
        acting_as="checkout",
        action="payment.charge.checkout_expired",
        entity="charge",
        entity_id=closed.id,
        before={"state": before},
        after={
            "state": closed.state,
            "failure_reason": closed.failure_reason,
            "provider_order_id": None,
        },
        request=request,
    )
    log.info(
        "payment.charge.checkout_expired",
        charge_id=str(closed.id),
        organization_id=str(closed.organization_id),
        order=False,
    )
    await session.flush()
    return True


async def claim_platform_checkout(
    session: AsyncSession,
    *,
    capabilities_for: Callable[[str], ProviderCapabilities],
    default_name: str,
    organization_id: uuid.UUID,
    invoice_id: uuid.UUID,
    customer: dict[str, str | None] | None,
    actor_id: uuid.UUID | None,
    now: datetime | None = None,
    request: Request | None = None,
    rechecked: frozenset[uuid.UUID] = frozenset(),
) -> CheckoutClaim:
    """The first act: decide whether a checkout may be opened for this invoice, and take the right
    to call the provider for it. In the caller's tenant-bound transaction.

    * **404** when the organization has no platform charge for the invoice (another organization's
      invoice is the same answer as none: the read filters on the organization);
    * **409** `invoice_already_paid` when the invoice is already paid (any charge of it is
      captured); `checkout_not_supported` when the provider has no hosted checkout;
      `checkout_in_progress` when another request holds the open checkout;
    * **422** for what could never be paid online — `nothing_to_pay`, `currency_unsupported`,
      `amount_below_minimum`, `phone_required` (a provider that needs the customer's phone and was
      not given a usable one) — **before** any call;
    * an open checkout for the invoice is **reused**, at the provider **its row** was created under
      (`provider_for(charge.provider)`, whatever the default is now), with a fresh lease — a
      double-click, or a customer coming back — and an open one past its expiry is reported `stale`
      to be settled first (`CheckoutClaim.stale_charge_id`);
    * an open one past its expiry that **never got an Order** is closed here, in this transaction
      (`close_orderless_checkout`): no Order id or session was ever handed to a browser, so nothing
      can have been paid on it, and there is nothing to ask a provider about — left open, it would
      answer every later "Pay now" with a 409 for good;
    * closed checkouts of the invoice still inside their late-capture window, and not in
      `rechecked`, are reported to be asked about first (`CheckoutClaim.recheck_charge_ids`): the
      customer may have paid one of them after it closed, or be paying it now;
    * otherwise a new `checkout` charge is inserted holding the lease, on the default provider
      (`default_name`), its amount, currency, subscription and product copied from the invoice's
      own charge.
    """
    now = now or _now()
    charges = await selectors.platform_charges_for_invoice(session, organization_id, invoice_id)
    debits = [c for c in charges if c.flow == FLOW_DEBIT]
    if not debits:
        raise NotFoundProblem("No payable charge for this invoice")
    if any(c.state == CHARGE_CAPTURED for c in charges):
        raise conflict(
            CODE_INVOICE_ALREADY_PAID, "This invoice has already been paid", field_name=_INVOICE
        )
    source = debits[-1]
    checkouts = [c for c in charges if c.flow == FLOW_CHECKOUT]
    existing_checkout = next(
        (c for c in checkouts if c.state in (CHARGE_CREATED, CHARGE_AUTHORIZED)), None
    )
    if (
        existing_checkout is not None
        and existing_checkout.provider_order_id is None
        and _is_past_expiry(existing_checkout, now)
        and await close_orderless_checkout(session, existing_checkout, now=now, request=request)
    ):
        existing_checkout = None

    provider_name = existing_checkout.provider if existing_checkout is not None else default_name
    capabilities = capabilities_for(provider_name)
    if not capabilities.supports_checkout:
        raise conflict(
            CODE_CHECKOUT_NOT_SUPPORTED,
            "Online payment is not available with the configured provider",
        )
    if source.amount_minor <= 0:
        raise refusal(CODE_NOTHING_TO_PAY)
    customer_value = _customer_for(organization_id, customer)
    if capabilities.requires_customer_details and (
        customer_value is None or not valid_phone(customer_value.phone)
    ):
        # Never fabricated: a made-up phone would be sent to a provider and to a bank.
        raise refusal(CODE_PHONE_REQUIRED)
    if (
        capabilities.currencies is not None
        and source.currency.upper() not in capabilities.currencies
    ):
        raise refusal(CODE_CURRENCY_UNSUPPORTED)
    if source.amount_minor < capabilities.min_amount_minor:
        raise refusal(CODE_AMOUNT_BELOW_MINIMUM)

    if existing_checkout is not None:
        if _is_past_expiry(existing_checkout, now):
            return CheckoutClaim(
                organization_id=organization_id,
                charge_id=existing_checkout.id,
                provider=existing_checkout.provider,
                request=None,
                stale_charge_id=existing_checkout.id,
            )
        claimed = await claim_checkout_lease(session, existing_checkout, now=now)
        if claimed is None:
            raise checkout_in_progress()
        return _build_claim(claimed, customer=customer_value, reused=True)

    recheck = tuple(c.id for c in checkouts if c.id not in rechecked and late_checkable(c, now=now))
    if recheck:
        return CheckoutClaim(
            organization_id=organization_id,
            charge_id=recheck[0],
            provider=provider_name,
            request=None,
            recheck_charge_ids=recheck,
        )

    return await _insert_checkout(
        session,
        source=source,
        provider_name=provider_name,
        count=len(checkouts),
        customer=customer_value,
        actor_id=actor_id,
        now=now,
        request=request,
    )


async def _insert_checkout(
    session: AsyncSession,
    *,
    source: Charge,
    provider_name: str,
    count: int,
    customer: CheckoutCustomer | None,
    actor_id: uuid.UUID | None,
    now: datetime,
    request: Request | None,
) -> CheckoutClaim:
    assert source.invoice_id is not None
    # `checkout:<invoice>` for the first, `…:<n>` for each later one (after an expiry): one key, one
    # charge, so a retried request that raced this one lands on the row it created.
    key = f"checkout:{source.invoice_id}" + (f":{count}" if count else "")
    charge_id = uuid.uuid4()
    expires_at = now + timedelta(seconds=settings().checkout_ttl_seconds)
    stmt = (
        pg_insert(Charge)
        .values(
            id=charge_id,
            organization_id=source.organization_id,
            invoice_id=source.invoice_id,
            subscription_id=source.subscription_id,
            product_key=source.product_key,
            provider=provider_name,
            amount_minor=source.amount_minor,
            currency=source.currency,
            state=CHARGE_CREATED,
            idempotency_key=key,
            flow=FLOW_CHECKOUT,
            checkout_expires_at=expires_at,
            attempts=1,
            lease_until=now + _lease(),
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
        .returning(Charge.id)
    )
    try:
        async with session.begin_nested():
            inserted = (await session.execute(stmt)).scalar_one_or_none()
    except IntegrityError:
        # The open-checkout index: another request opened one for this invoice between our read
        # and our insert, under a different key. Theirs wins; ours is a reuse.
        inserted = None
    if inserted is None:
        existing = await selectors.platform_charges_for_invoice(
            session, source.organization_id, source.invoice_id
        )
        winner = next(
            (
                c
                for c in existing
                if c.flow == FLOW_CHECKOUT and c.state in (CHARGE_CREATED, CHARGE_AUTHORIZED)
            ),
            None,
        )
        if winner is None:
            raise checkout_in_progress()
        claimed = await claim_checkout_lease(session, winner, now=now)
        if claimed is None:
            raise checkout_in_progress()
        return _build_claim(claimed, customer=customer, reused=True)

    charge = await selectors.get_charge(session, source.organization_id, inserted)
    assert charge is not None
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=actor_id,
        action="payment.checkout.opened",
        entity="charge",
        entity_id=charge.id,
        after={
            "state": charge.state,
            "flow": charge.flow,
            "provider": charge.provider,
            "amount_minor": charge.amount_minor,
            "currency": charge.currency,
            "invoice_id": str(charge.invoice_id),
            "expires_at": expires_at.isoformat(),
        },
        request=request,
    )
    return _build_claim(charge, customer=customer, reused=False)


def _classify_for_request(exc: ProviderRejected) -> ProblemError:
    """A provider refusal as the problem the caller sees: a request that could never be paid is the
    caller's 422, with the API's own code (`_PROVIDER_REQUEST_CODES`); anything else is the
    provider's 502 with its stable code and no free text."""
    code = _PROVIDER_REQUEST_CODES.get(exc.code or "")
    if code is not None:
        return refusal(code)
    return ProviderRefusedProblem(str(exc))


async def run_checkout_call(provider: PaymentProvider, claim: CheckoutClaim) -> CheckoutSession:
    """The second act: ask the provider for the Order and what the browser opens checkout with, on a
    worker thread, bounded, with **no session and no transaction open**. A charge that records an
    Order gets that Order, fetched by its id; one that records none gets a best-effort receipt
    search and then a create (`ensure_checkout`) — so a call whose answer was lost, repeated, lands
    on the Order the first one made when the provider's search shows it, and otherwise makes a
    second one that `record_checkout` records and the first, never recorded, is shown to nobody.

    Every way it can fail is a problem the route answers with, never a raw exception: a timeout, a
    transport failure or a `5xx` is `ProviderUnreachableProblem` (503, safe to repeat); a refused
    credential is `CheckoutUnavailableProblem` (503) and trips the latch — nothing here is the
    customer's to fix; a refusal of the request is a 422 or a 502 (`_classify_for_request`)."""
    request = claim.request
    assert request is not None
    if not isinstance(provider, CheckoutProvider):
        raise conflict(
            CODE_CHECKOUT_NOT_SUPPORTED, "Online payment is not available with this provider"
        )
    if CREDENTIALS.tripped(provider.name):
        raise CheckoutUnavailableProblem("Online payment is temporarily unavailable")
    timeout = settings().provider_call_timeout_seconds
    try:
        with call_deadline(timeout):
            return await asyncio.wait_for(
                asyncio.to_thread(provider.ensure_checkout, request), timeout=timeout
            )
    except TimeoutError:
        log.warning(
            "payment.checkout.provider_timeout",
            charge_id=str(claim.charge_id),
            organization_id=str(claim.organization_id),
            timeout_seconds=timeout,
        )
        raise ProviderUnreachableProblem(
            "The payment provider did not answer in time; try again"
        ) from None
    except ProviderMisconfigured as exc:
        charge_services._credentials_rejected(provider.name, exc.code)
        raise CheckoutUnavailableProblem("Online payment is temporarily unavailable") from None
    except ProviderRejected as exc:
        log.warning(
            "payment.checkout.provider_rejected",
            charge_id=str(claim.charge_id),
            organization_id=str(claim.organization_id),
            provider=provider.name,
            code=exc.code,
        )
        raise _classify_for_request(exc) from None
    except (ProviderCallError, httpx.HTTPError) as exc:
        log.warning(
            "payment.checkout.provider_unreachable",
            charge_id=str(claim.charge_id),
            organization_id=str(claim.organization_id),
            provider=provider.name,
            error=type(exc).__name__,
        )
        raise ProviderUnreachableProblem("The payment provider could not be reached") from None


async def release_checkout_lease(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> None:
    """Give back the right to call the provider after a call that failed, so the customer's next
    click is not told the checkout is "being prepared" for the five minutes the lease would
    otherwise last. Only the lease: the charge, and whatever Order the provider may or may not have
    made, are untouched — the next call looks before it creates."""
    charge = await selectors.lock_checkout_charge(session, organization_id, charge_id)
    if charge is not None and charge.state in (CHARGE_CREATED, CHARGE_AUTHORIZED):
        charge.lease_until = None
        await session.flush()


async def record_checkout(
    session: AsyncSession,
    claim: CheckoutClaim,
    opened: CheckoutSession,
    *,
    request: Request | None = None,
) -> Charge:
    """The third act, in its own short transaction: record the Order the provider holds for the
    charge and release the lease. Re-reads the charge under its row lock — a webhook or the poller
    may have moved it while the call was out — and never overwrites an Order id already recorded.
    Audited once (`payment.checkout.order_created`), by whoever first records it.

    **The recorded id is the only one a browser is ever handed.** The route compares the charge
    this returns with the call's Order (`withheld_order`) and refuses rather than hand out an Order
    the charge does not record: Razorpay accepts a duplicate receipt, so two Orders can exist for
    one charge, and a customer who paid the unrecorded one would pay an Order the poller never
    asks about."""
    charge = await selectors.lock_checkout_charge(session, claim.organization_id, claim.charge_id)
    if charge is None:  # pragma: no cover - the claim committed before the call
        raise NotFoundProblem("No such checkout")
    first = charge.provider_order_id is None
    if first:
        charge.provider_order_id = opened.order.provider_order_id
    elif charge.provider_order_id != opened.order.provider_order_id:
        log.error(
            "payment.checkout.order_id_mismatch",
            charge_id=str(charge.id),
            organization_id=str(charge.organization_id),
        )
    charge.lease_until = None
    charge.updated_at = _now()
    if first:
        record_audit(
            session,
            AuditLog,
            organization_id=charge.organization_id,
            actor_id=None,
            acting_as=f"{charge.provider}-checkout",
            action="payment.checkout.order_created",
            entity="charge",
            entity_id=charge.id,
            after={
                "state": charge.state,
                "provider_order_id": charge.provider_order_id,
                "provider": charge.provider,
            },
            request=request,
        )
    await session.flush()
    return charge


def withheld_order(charge: Charge, opened: CheckoutSession) -> bool:
    """Whether the call's Order is **not** the one the charge records — and so must not be handed
    to the browser (`record_checkout`). Logged at error: it means two Orders exist for one charge
    (or a call outlived its lease); the recorded one is what the next open fetches."""
    if charge.provider_order_id == opened.order.provider_order_id:
        return False
    log.error(
        "payment.checkout.unrecorded_order_withheld",
        charge_id=str(charge.id),
        organization_id=str(charge.organization_id),
        provider=charge.provider,
    )
    return True


# --------------------------------------------------------------------------- asking about a charge


#: How far apart two provider status checks of one charge are, at the least. The poller and the
#: read-through both stamp it (`Charge.next_attempt_at`), so one charge is not asked about twice in
#: a window by the poller, and a customer's own refresh does not make a second poller call.
def _poll_gap() -> timedelta:
    return timedelta(seconds=settings().charge_poll_interval_seconds)


@dataclass(frozen=True, slots=True)
class PollTicket:
    """What a poll claim hands the call: which charge, at which provider, about which Order — and
    whether it is a **late** check of a checkout already closed as `checkout_expired`.

    `stray_check` is set for a checkout that this poll may close (past its expiry) or that is
    already closed (a late check): only then is the provider also searched for **stray** Orders —
    others carrying the charge's exact `receipt` (`fetch_stray_payments`) — because only then could
    a payment on one be lost by closing. Not on every poll: the search is a request per charge,
    and Razorpay rate-limits."""

    organization_id: uuid.UUID
    charge_id: uuid.UUID
    provider: str
    order_id: str
    late: bool = False
    receipt: str = ""
    stray_check: bool = False


async def claim_poll(
    session: AsyncSession,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    now: datetime,
    respect_gap: bool,
) -> PollTicket | None:
    """The first act of a status check: lock the charge (skipping one somebody else holds), confirm
    it is still pollable — a platform charge with an Order and either no terminal state (a checkout,
    or an authorized debit) or a closed checkout still inside its late-capture window
    (`late_checkable`) — stamp the poll gap (the longer late one for a closed checkout), and hand
    back the ticket. None when there is nothing to ask. `respect_gap` is the poller's: a customer's
    own read-through (`False`) always asks.

    A closed checkout past its window is **retired** here: its `next_attempt_at` is cleared, which
    is what takes it out of the poller's discovery for good."""
    charge = await selectors.lock_charge(session, organization_id, charge_id, skip_locked=True)
    if (
        charge is None
        or charge.payee != PAYEE_PLATFORM
        or charge.deleted_at is not None
        or charge.provider_order_id is None
    ):
        return None
    late = charge.state == CHARGE_FAILED
    if late:
        if not late_checkable(charge, now=now):
            if charge.flow == FLOW_CHECKOUT and charge.next_attempt_at is not None:
                charge.next_attempt_at = None
                await session.flush()
            return None
    elif charge.state not in (CHARGE_CREATED, CHARGE_AUTHORIZED) or (
        charge.flow == FLOW_DEBIT and charge.state != CHARGE_AUTHORIZED
    ):
        return None
    if respect_gap and charge.next_attempt_at is not None and charge.next_attempt_at > now:
        return None
    charge.next_attempt_at = now + (_late_gap() if late else _poll_gap())
    await session.flush()
    return PollTicket(
        organization_id=charge.organization_id,
        charge_id=charge.id,
        provider=charge.provider,
        order_id=charge.provider_order_id,
        late=late,
        receipt=str(charge.id),
        stray_check=charge.flow == FLOW_CHECKOUT and (late or _is_past_expiry(charge, now)),
    )


async def fetch_order_payments(
    provider: PaymentProvider, ticket: PollTicket
) -> list[PaymentSnapshot] | None:
    """The second act: every payment attempt on the charge's Order, as the provider reports it now —
    on a worker thread, bounded, with no transaction open. **None when the provider could not be
    asked** (a timeout, a transport failure, a refusal, a rejected credential): nothing is known,
    nothing may be concluded, and a charge is never expired on the strength of a question that
    could not be asked."""
    if CREDENTIALS.tripped(provider.name):
        return None
    timeout = settings().provider_call_timeout_seconds
    try:
        with call_deadline(timeout):
            return await asyncio.wait_for(
                asyncio.to_thread(provider.order_payments, order_id=ticket.order_id),
                timeout=timeout,
            )
    except TimeoutError:
        log.warning(
            "payment.charge.poll_timeout",
            charge_id=str(ticket.charge_id),
            organization_id=str(ticket.organization_id),
            timeout_seconds=timeout,
        )
    except ProviderMisconfigured as exc:
        charge_services._credentials_rejected(provider.name, exc.code)
    except (ProviderCallError, httpx.HTTPError) as exc:
        log.warning(
            "payment.charge.poll_unavailable",
            charge_id=str(ticket.charge_id),
            organization_id=str(ticket.organization_id),
            provider=provider.name,
            error=type(exc).__name__,
            code=getattr(exc, "code", None),
        )
    return None


async def fetch_stray_payments(
    provider: PaymentProvider, ticket: PollTicket
) -> tuple[StrayPayment, ...]:
    """Payments on **stray** Orders of the charge — others carrying its exact receipt, which
    Razorpay allows (a duplicate an unknown-outcome create left behind) — for a ticket that may
    close the checkout or is a late check (`PollTicket.stray_check`); on a worker thread, bounded,
    no transaction open. Asked only of an adapter that offers it (`stray_order_payments`: Razorpay;
    Cashfree's Order id *is* the receipt, so it cannot have a duplicate).

    **Best-effort, and never a reason to wait.** The provider's receipt search can miss an Order
    that exists, so an empty answer — or one that could not be had (a timeout, a `429`, a refused
    key) — is "nothing found", logged, and changes nothing; the late-capture window keeps asking
    for days after a checkout closes. A stray Order's id was never handed to anyone, so a payment
    on one should not exist at all; this is the net under that."""
    search = getattr(provider, "stray_order_payments", None)
    if not ticket.stray_check or not callable(search) or CREDENTIALS.tripped(provider.name):
        return ()
    timeout = settings().provider_call_timeout_seconds
    try:
        with call_deadline(timeout):
            found = await asyncio.wait_for(
                asyncio.to_thread(
                    search, receipt=ticket.receipt, recorded_order_id=ticket.order_id
                ),
                timeout=timeout,
            )
    except TimeoutError:
        log.warning(
            "payment.checkout.stray_search_timeout",
            charge_id=str(ticket.charge_id),
            organization_id=str(ticket.organization_id),
            timeout_seconds=timeout,
        )
        return ()
    except ProviderMisconfigured as exc:
        charge_services._credentials_rejected(provider.name, exc.code)
        return ()
    except (ProviderCallError, httpx.HTTPError) as exc:
        log.warning(
            "payment.checkout.stray_search_unavailable",
            charge_id=str(ticket.charge_id),
            organization_id=str(ticket.organization_id),
            provider=provider.name,
            error=type(exc).__name__,
            code=getattr(exc, "code", None),
        )
        return ()
    strays = tuple(s for s in found if isinstance(s, StrayPayment))
    if strays:
        log.error(
            "payment.checkout.stray_order_found",
            charge_id=str(ticket.charge_id),
            organization_id=str(ticket.organization_id),
            provider=provider.name,
            stray_orders=sorted({s.provider_order_id for s in strays}),
            statuses=sorted({s.snapshot.status for s in strays}),
        )
    return strays


async def apply_stray_payment(
    session: AsyncSession, charge: Charge, stray: StrayPayment, *, request: Request | None
) -> None:
    """Record money that moved on a stray Order (same exact receipt, not the recorded Order) against
    the charge, through the reconciliation path every status check takes, with the Order check
    replaced by that receipt match (`services.apply_payment_snapshot(stray_order_id=...)`). When it
    settles the charge it is audited `payment.checkout.stray_order_payment`, naming both Orders, so
    a person can see why a charge was settled by an Order it never recorded; when it cannot be
    applied (another amount, another payment already held) the `payment.charge.sync_not_applied`
    row names the stray Order instead."""
    # Read now: a SAVEPOINT rollback inside the apply expires the row.
    organization_id, charge_id = charge.organization_id, charge.id
    provider, recorded = charge.provider, charge.provider_order_id
    moved = await charge_services.apply_payment_snapshot(
        session, charge, stray.snapshot, request=request, stray_order_id=stray.provider_order_id
    )
    if not moved:
        return
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as=f"{provider}-sync",
        action="payment.checkout.stray_order_payment",
        entity="charge",
        entity_id=charge_id,
        after={
            "recorded_order_id": recorded,
            "stray_order_id": stray.provider_order_id,
            "provider_payment_id": stray.snapshot.provider_payment_id,
            "status": stray.snapshot.status,
        },
        request=request,
    )
    await session.flush()


def _money_moved(strays: Sequence[StrayPayment]) -> StrayPayment | None:
    """The stray attempt that moved money, if any — the only kind that settles a charge."""
    moved = [s for s in strays if s.snapshot.status in _MONEY_MOVED]
    best = best_attempt([s.snapshot for s in moved])
    return next((s for s in moved if s.snapshot is best), None)


async def expire_checkout_charge(session: AsyncSession, charge: Charge, *, now: datetime) -> bool:
    """Close a checkout nobody paid: `failed` with the reason `checkout_expired`, audited — and **no
    `commerce.payment.failed`**. An unpaid checkout is not a failed collection: the customer simply
    did not pay yet, the subscription's own dunning decides what that means, and a checkout that
    announced its own expiry would suspend somebody who had not been asked to pay twice.

    **Closed, not forgotten.** The Order may still be payable (a Razorpay Order never expires), so
    the charge's `next_attempt_at` is set one late gap out: the poller keeps asking about it for
    `checkout_late_capture_window_seconds` (`claim_poll`, `late_checkable`), and a capture found
    then is recorded — `checkout_expired` is a local failure reason, so `failed -> captured` is
    allowed (`captured_after_local_failure`)."""
    if (
        charge.flow != FLOW_CHECKOUT
        or charge.state not in (CHARGE_CREATED, CHARGE_AUTHORIZED)
        or charge.checkout_expires_at is None
        or charge.checkout_expires_at > now
    ):
        return False
    before = charge.state
    charge.state = CHARGE_FAILED
    charge.failure_reason = domain.FAILURE_CHECKOUT_EXPIRED
    charge.lease_until = None
    charge.next_attempt_at = now + _late_gap() if charge.provider_order_id is not None else None
    charge.updated_at = now
    record_audit(
        session,
        AuditLog,
        organization_id=charge.organization_id,
        actor_id=None,
        acting_as="checkout-poller",
        action="payment.charge.checkout_expired",
        entity="charge",
        entity_id=charge.id,
        before={"state": before},
        after={"state": charge.state, "failure_reason": charge.failure_reason},
    )
    log.info(
        "payment.charge.checkout_expired",
        charge_id=str(charge.id),
        organization_id=str(charge.organization_id),
    )
    await session.flush()
    return True


async def record_poll(
    session: AsyncSession,
    ticket: PollTicket,
    snapshots: list[PaymentSnapshot] | None,
    *,
    now: datetime,
    request: Request | None = None,
    strays: Sequence[StrayPayment] = (),
) -> Charge | None:
    """The third act of a status check, in its own short transaction: re-read the charge under its
    row lock (a webhook may have moved it while the call was out), reconcile the attempt that says
    what happened (`services.apply_payment_snapshot` — the path a signed webhook takes), and, if
    that left a checkout unpaid past its expiry and the provider **was** asked, close it
    (`expire_checkout_charge`) — **unless an attempt is still in flight** (`payment_in_flight`: a
    payment in progress, an authorization whose capture has not run, a capture that could not be
    applied), which keeps it open for up to `checkout_inflight_grace_seconds` past its expiry. A
    closed checkout inside its late-capture window (`late_checkable`) is moved by a capture and by
    nothing else. `snapshots=None` (the provider could not be asked) changes nothing.

    `strays` (`fetch_stray_payments`) are attempts on other Orders carrying the charge's exact
    receipt. One that moved money settles the charge (`apply_stray_payment`) when its own Order did
    not, and is flagged (`payment.charge.sync_not_applied`, a different payment) when the charge is
    already captured — the same invoice paid twice is detected here, never silently dropped. One
    still in flight holds the close like an attempt on the recorded Order would."""
    charge = await selectors.lock_charge(session, ticket.organization_id, ticket.charge_id)
    if charge is None or charge.payee != PAYEE_PLATFORM or snapshots is None:
        return charge
    best = best_attempt(snapshots)
    stray_moved = _money_moved(strays)
    if charge.state in (CHARGE_CREATED, CHARGE_AUTHORIZED):
        if best is not None:
            await charge_services.apply_payment_snapshot(session, charge, best, request=request)
            # A permanent condition rolls a SAVEPOINT back inside it, which expires the row; read
            # it again (still under this transaction's lock) before deciding anything from it.
            charge = (
                await selectors.lock_charge(session, ticket.organization_id, ticket.charge_id)
                or charge
            )
        if stray_moved is not None:
            await apply_stray_payment(session, charge, stray_moved, request=request)
            charge = (
                await selectors.lock_charge(session, ticket.organization_id, ticket.charge_id)
                or charge
            )
        if (
            charge.state in (CHARGE_CREATED, CHARGE_AUTHORIZED)
            and charge.flow == FLOW_CHECKOUT
            and _is_past_expiry(charge, now)
        ):
            snapshots = [*snapshots, *(s.snapshot for s in strays)]
            best = best_attempt(snapshots)
            if payment_in_flight(charge, snapshots, now=now):
                log.info(
                    "payment.checkout.expiry_deferred",
                    charge_id=str(charge.id),
                    organization_id=str(charge.organization_id),
                    attempt_status=best.status if best is not None else None,
                )
            else:
                if snapshots and attempt_in_flight(snapshots):
                    # Past the grace with an attempt the provider never resolved (or a capture that
                    # could not be applied): closed, and still asked about for the late window.
                    log.warning(
                        "payment.checkout.expired_with_attempt_in_flight",
                        charge_id=str(charge.id),
                        organization_id=str(charge.organization_id),
                        attempt_status=best.status if best is not None else None,
                    )
                await expire_checkout_charge(session, charge, now=now)
        return charge
    if late_checkable(charge, now=now) and best is not None and best.status in _MONEY_MOVED:
        await charge_services.apply_payment_snapshot(session, charge, best, request=request)
        charge = (
            await selectors.lock_charge(session, ticket.organization_id, ticket.charge_id) or charge
        )
    if stray_moved is not None and (
        late_checkable(charge, now=now) or charge.state == CHARGE_CAPTURED
    ):
        await apply_stray_payment(session, charge, stray_moved, request=request)
    return charge


#: The neutral attempt statuses that mean money moved — the only ones that reopen a closed checkout.
_MONEY_MOVED = frozenset({"captured", "refunded"})
