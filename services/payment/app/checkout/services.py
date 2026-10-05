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

**What is never done here:** trust a browser (`confirm` only checks an HMAC as a precondition for
asking the provider), store or log the customer's details or the checkout session token, or call a
provider other than the one the row was created under.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
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

#: The provider's own refusals that mean "this request is wrong", and so are the caller's 422 and
#: not a 502 from upstream.
_REQUEST_CODES: dict[str, str] = {
    "unsupported_currency": "Only INR invoices can be paid online",
    "amount_below_minimum": "The amount is below the minimum that can be paid online (Rs 1)",
    "customer_phone_required": "Add a customer phone number to pay online",
    "customer_details_required": "Add the customer's details to pay online",
}


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
) -> CheckoutClaim:
    """The first act: decide whether a checkout may be opened for this invoice, and take the right
    to call the provider for it. In the caller's tenant-bound transaction.

    * **404** when the organization has no platform charge for the invoice (another organization's
      invoice is the same answer as none: the read filters on the organization);
    * **409** when the invoice is already paid (any charge of it is captured);
    * **422** for what could never be paid online — nothing owed, a currency the provider does not
      take, an amount below its minimum, a provider that needs the customer's phone and was not
      given a usable one — **before** any call;
    * an open checkout for the invoice is **reused**, at the provider **its row** was created under
      (`provider_for(charge.provider)`, whatever the default is now), with a fresh lease — a
      double-click, or a customer coming back — and an open one past its expiry is reported `stale`
      to be settled first (`CheckoutClaim.stale_charge_id`);
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
        raise ConflictProblem("This invoice has already been paid")
    source = debits[-1]
    checkouts = [c for c in charges if c.flow == FLOW_CHECKOUT]
    existing_checkout = next(
        (c for c in checkouts if c.state in (CHARGE_CREATED, CHARGE_AUTHORIZED)), None
    )

    provider_name = existing_checkout.provider if existing_checkout is not None else default_name
    capabilities = capabilities_for(provider_name)
    if not capabilities.supports_checkout:
        raise ConflictProblem("Online payment is not available with the configured provider")
    if source.amount_minor <= 0:
        raise ValidationProblem("There is nothing to pay on this invoice")
    customer_value = _customer_for(organization_id, customer)
    if capabilities.requires_customer_details and (
        customer_value is None or not valid_phone(customer_value.phone)
    ):
        # Never fabricated: a made-up phone would be sent to a provider and to a bank.
        raise ValidationProblem(_REQUEST_CODES["customer_phone_required"])
    if (
        capabilities.currencies is not None
        and source.currency.upper() not in capabilities.currencies
    ):
        raise ValidationProblem(_REQUEST_CODES["unsupported_currency"])
    if source.amount_minor < capabilities.min_amount_minor:
        raise ValidationProblem(_REQUEST_CODES["amount_below_minimum"])

    if existing_checkout is not None:
        if (
            existing_checkout.checkout_expires_at is not None
            and existing_checkout.checkout_expires_at <= now
        ):
            return CheckoutClaim(
                organization_id=organization_id,
                charge_id=existing_checkout.id,
                provider=existing_checkout.provider,
                request=None,
                stale_charge_id=existing_checkout.id,
            )
        claimed = await claim_checkout_lease(session, existing_checkout, now=now)
        if claimed is None:
            raise ConflictProblem(
                "This invoice's checkout is being prepared; try again in a moment"
            )
        return _build_claim(claimed, customer=customer_value, reused=True)

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
            raise ConflictProblem(
                "This invoice's checkout is being prepared; try again in a moment"
            )
        claimed = await claim_checkout_lease(session, winner, now=now)
        if claimed is None:
            raise ConflictProblem(
                "This invoice's checkout is being prepared; try again in a moment"
            )
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
    caller's 422; anything else is the provider's 502 with its stable code and no free text."""
    message = _REQUEST_CODES.get(exc.code or "")
    if message is not None:
        return ValidationProblem(message)
    return ProviderRefusedProblem(str(exc))


async def run_checkout_call(provider: PaymentProvider, claim: CheckoutClaim) -> CheckoutSession:
    """The second act: ask the provider for the Order and what the browser opens checkout with, on a
    worker thread, bounded, with **no session and no transaction open**. Looks up before it
    creates (`ensure_checkout`), so a call whose answer was lost, repeated, lands on the Order the
    first one made.

    Every way it can fail is a problem the route answers with, never a raw exception: a timeout, a
    transport failure or a `5xx` is `ProviderUnreachableProblem` (503, safe to repeat); a refused
    credential is `CheckoutUnavailableProblem` (503) and trips the latch — nothing here is the
    customer's to fix; a refusal of the request is a 422 or a 502 (`_classify_for_request`)."""
    request = claim.request
    assert request is not None
    if not isinstance(provider, CheckoutProvider):
        raise ConflictProblem("Online payment is not available with this provider")
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
    Audited once (`payment.checkout.order_created`), by whoever first records it."""
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


# --------------------------------------------------------------------------- asking about a charge


#: How far apart two provider status checks of one charge are, at the least. The poller and the
#: read-through both stamp it (`Charge.next_attempt_at`), so one charge is not asked about twice in
#: a window by the poller, and a customer's own refresh does not make a second poller call.
def _poll_gap() -> timedelta:
    return timedelta(seconds=settings().charge_poll_interval_seconds)


@dataclass(frozen=True, slots=True)
class PollTicket:
    """What a poll claim hands the call: which charge, at which provider, about which Order."""

    organization_id: uuid.UUID
    charge_id: uuid.UUID
    provider: str
    order_id: str


async def claim_poll(
    session: AsyncSession,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    now: datetime,
    respect_gap: bool,
) -> PollTicket | None:
    """The first act of a status check: lock the charge (skipping one somebody else holds), confirm
    it is still pollable — a platform charge with an Order and no terminal state, and either a
    checkout or an authorized debit — stamp the poll gap, and hand back the ticket. None when there
    is nothing to ask. `respect_gap` is the poller's: a customer's own read-through (`False`)
    always asks."""
    charge = await selectors.lock_charge(session, organization_id, charge_id, skip_locked=True)
    if (
        charge is None
        or charge.payee != PAYEE_PLATFORM
        or charge.deleted_at is not None
        or charge.provider_order_id is None
        or charge.state not in (CHARGE_CREATED, CHARGE_AUTHORIZED)
        or (charge.flow == FLOW_DEBIT and charge.state != CHARGE_AUTHORIZED)
        or (respect_gap and charge.next_attempt_at is not None and charge.next_attempt_at > now)
    ):
        return None
    charge.next_attempt_at = now + _poll_gap()
    await session.flush()
    return PollTicket(
        organization_id=charge.organization_id,
        charge_id=charge.id,
        provider=charge.provider,
        order_id=charge.provider_order_id,
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


async def expire_checkout_charge(session: AsyncSession, charge: Charge, *, now: datetime) -> bool:
    """Close a checkout nobody paid: `failed` with the reason `checkout_expired`, audited — and **no
    `commerce.payment.failed`**. An unpaid checkout is not a failed collection: the customer simply
    did not pay yet, the subscription's own dunning decides what that means, and a checkout that
    announced its own expiry would suspend somebody who had not been asked to pay twice. A capture
    that still lands afterwards is recorded (`checkout_expired` is a local failure reason)."""
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
) -> Charge | None:
    """The third act of a status check, in its own short transaction: re-read the charge under its
    row lock (a webhook may have moved it while the call was out), reconcile the attempt that says
    what happened (`services.apply_payment_snapshot` — the path a signed webhook takes), and, if
    that left a checkout unpaid past its expiry and the provider **was** asked, close it
    (`expire_checkout_charge`). `snapshots=None` (the provider could not be asked) changes
    nothing."""
    charge = await selectors.lock_charge(session, ticket.organization_id, ticket.charge_id)
    if charge is None or charge.payee != PAYEE_PLATFORM:
        return None
    if charge.state not in (CHARGE_CREATED, CHARGE_AUTHORIZED) or snapshots is None:
        return charge
    best = best_attempt(snapshots)
    if best is not None:
        await charge_services.apply_payment_snapshot(session, charge, best, request=request)
    if charge.state in (CHARGE_CREATED, CHARGE_AUTHORIZED):
        await expire_checkout_charge(session, charge, now=now)
    return charge
