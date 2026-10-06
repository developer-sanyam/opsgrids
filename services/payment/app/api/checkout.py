"""Platform checkout — the customer surface for paying one of OpsGrids' own invoices (ADR-0032).

Three routes, one owner gate. A person behind the gateway (`/api/v1/payment/checkouts…`) who may
**activate and deactivate products** for the organization — `core.product.activation.write` at
`ORG`, the permission that guards subscribe, cancel and change-plan, because paying for a
subscription is the same spend decision. No new permission: that would mean an ADR-0007 rollout to
every live tenant for a gate the existing one already expresses.

* `POST   /payment/checkouts` — open (or reopen) the checkout for an invoice. The amount comes from
  the invoice's own charge, never the body; one open checkout per invoice; claim → commit → provider
  call **outside any transaction** → record.
* `GET    /payment/checkouts/{charge_id}` — read-through status: while the checkout is open (and,
  once it has closed as `checkout_expired`, for its late-capture window) the provider is asked what
  happened, and the answer reconciled through the path a signed webhook takes. What a return page
  polls.
* `POST   /payment/checkouts/{charge_id}/confirm` — Razorpay only. Checkout's callback hands the
  browser `{payment_id, order_id, signature}`; the server checks `HMAC(order_id|payment_id)` in
  constant time (a **precondition**, 400 on a mismatch or a missing field, nothing marked paid) and
  then asks Razorpay. Success is reported only after that answer shows the payment.

**The browser is never evidence.** Nothing here accepts "the payment succeeded" as input. A charge
becomes `captured` only from `order_payments` (this path, the poller) or a signature-verified
webhook, and every route answers another organization's id with a 404 (the reads filter on the
organization; RLS is the net).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.checkout import services
from app.checkout.services import bind
from app.collections.services import ProviderUnreachableProblem
from app.deps import ProviderResolver, get_provider, resolve_provider
from app.models import CHARGE_CAPTURED, CHARGE_FAILED, Charge
from app.providers.base import PaymentProvider, capabilities_of, in_test_mode
from app.providers.wire import CREDENTIALS
from app.schemas import (
    CheckoutConfirmIn,
    PlatformCheckoutIn,
    PlatformCheckoutOut,
    PlatformCheckoutStatusOut,
)
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_session
from platform_core.errors import ConflictProblem, NotFoundProblem, ProblemError
from platform_core.logging import get_logger

log = get_logger("payment.checkout.api")

router = APIRouter(prefix="/payment/checkouts", tags=["checkout"])

#: Not `get_tenant_session`: that is one transaction for the whole request, and these routes call
#: the provider, which must never happen inside one. Each opens its own short ones around the call.
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[PaymentProvider, Depends(get_provider)]
Manage = Annotated[
    ScopeContext, Depends(require("core.product.activation.write", minimum=Scope.ORG))
]


def _provider_for(default: PaymentProvider) -> ProviderResolver:
    """The adapter of an existing row's provider: the request's default when it is that provider (so
    a test that overrides `get_provider` is asked about the adapter it set up), otherwise the
    registry's. Never the default for a row that belongs to another provider."""

    def resolve(name: str) -> PaymentProvider:
        return default if default.name == name else resolve_provider(name)

    return resolve


def _test_mode(provider_for: ProviderResolver, name: str) -> bool:
    """Whether provider `name` — the one that owns the charge — runs on test / sandbox
    credentials. A provider that cannot be built here (a row from a provider this deployment no
    longer has) is reported as not test mode: a status read never fails over a display flag."""
    try:
        return in_test_mode(provider_for(name))
    except (ValueError, RuntimeError):
        return False


def _status_out(
    charge: Charge, *, provider_checked: bool, provider_for: ProviderResolver
) -> PlatformCheckoutStatusOut:
    """The status answer. `test_mode` is the **owning** provider's (the row's, never the default's):
    a Razorpay checkout opened before a flip to Cashfree is still paid at Razorpay."""
    return PlatformCheckoutStatusOut(
        charge_id=charge.id,
        provider=charge.provider,
        state=charge.state,
        paid=charge.state == CHARGE_CAPTURED,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        expires_at=charge.checkout_expires_at,
        failure_reason=charge.failure_reason if charge.state == CHARGE_FAILED else None,
        provider_checked=provider_checked,
        test_mode=_test_mode(provider_for, charge.provider),
    )


@dataclass(frozen=True, slots=True)
class _Settled:
    """What asking about one checkout found: the charge as it stands afterwards, whether the
    provider could be asked, and whether it reported a payment still in flight
    (`services.payment_in_flight`)."""

    charge: Charge | None
    asked: bool
    in_flight: bool = False


async def _settle(
    session: AsyncSession,
    provider_for: ProviderResolver,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    request: Request | None,
    respect_gap: bool = False,
) -> _Settled:
    """Ask the provider what happened to one checkout and reconcile the answer: claim → call →
    record, three acts, **no transaction open across the call**. An open checkout, or a closed one
    still inside its late-capture window (`services.late_checkable`)."""
    now = datetime.now(UTC)
    async with session.begin():
        await bind(session, organization_id)
        ticket = await services.claim_poll(
            session, organization_id, charge_id, now=now, respect_gap=respect_gap
        )
    if ticket is None:
        async with session.begin():
            await bind(session, organization_id)
            charge = await selectors.get_checkout_charge(session, organization_id, charge_id)
        return _Settled(charge, asked=False)
    provider = provider_for(ticket.provider)
    snapshots = await services.fetch_order_payments(provider, ticket)
    strays = await services.fetch_stray_payments(provider, ticket) if snapshots is not None else ()
    async with session.begin():
        await bind(session, organization_id)
        charge = await services.record_poll(
            session, ticket, snapshots, now=now, request=request, strays=strays
        )
    in_flight = (
        charge is not None
        and charge.state != CHARGE_CAPTURED
        and snapshots is not None
        and services.payment_in_flight(charge, [*snapshots, *(s.snapshot for s in strays)], now=now)
    )
    return _Settled(charge, asked=snapshots is not None, in_flight=in_flight)


def _asks_provider(charge: Charge, now: datetime) -> bool:
    """Whether a read of this checkout asks the provider: an open one with an Order, or a closed
    one still inside its late-capture window (`services.late_checkable`) — a customer who paid a
    checkout after it closed must see `paid: true`, not `failed`."""
    if charge.provider_order_id is None or charge.state == CHARGE_CAPTURED:
        return False
    if charge.state == CHARGE_FAILED:
        return services.late_checkable(charge, now=now)
    return True


@router.post(
    "",
    response_model=PlatformCheckoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open (or reopen) the checkout for an invoice",
    responses={
        404: {"description": "The organization has no payable charge for this invoice."},
        409: {
            "description": (
                "Refused, with a stable machine code in `errors[0].code`: "
                f"`{services.CODE_INVOICE_ALREADY_PAID}` (field `invoice_id`; the invoice is paid "
                "— the only 409 that means paid), "
                f"`{services.CODE_CHECKOUT_IN_PROGRESS}` (another request holds the checkout; try "
                "again in a moment), "
                f"`{services.CODE_PREVIOUS_CHECKOUT_NOT_CLOSABLE}` (the expired checkout could "
                "not be closed yet; try again shortly), "
                f"`{services.CODE_PREVIOUS_PAYMENT_IN_FLIGHT}` (a payment on a previous checkout "
                "is still being processed; try again in a few minutes), "
                f"`{services.CODE_CHECKOUT_NOT_SUPPORTED}` (the provider has no hosted checkout)."
            )
        },
        422: {
            "description": (
                "Could never be paid online, refused before any provider call, with "
                "`errors[0] = {field, code, message}`: "
                f"`{services.CODE_PHONE_REQUIRED}` (field `customer.phone`), "
                f"`{services.CODE_CUSTOMER_DETAILS_REQUIRED}` (field `customer`), "
                f"`{services.CODE_CURRENCY_UNSUPPORTED}`, `{services.CODE_AMOUNT_BELOW_MINIMUM}` "
                f"and `{services.CODE_NOTHING_TO_PAY}` (field `invoice_id`). A malformed body is "
                "a 422 with the framework's own field errors."
            )
        },
    },
)
async def open_checkout(
    payload: PlatformCheckoutIn,
    ctx: Manage,
    session: RawSessionDep,
    provider: ProviderDep,
    request: Request,
) -> PlatformCheckoutOut:
    """Open the provider's hosted checkout for one invoice. The amount is the invoice's own
    charge's, never the body's; a second request for an invoice with an open checkout returns that
    checkout (at the provider it was created under). **404** if the organization has no charge for
    the invoice, **409** if it is already paid (`invoice_already_paid`) or cannot be opened right
    now (each with its own code), **422** for what could never be paid online.

    The Order handed back is always the one the charge **records**: it is recorded before this
    answers, a reopen fetches it by its id, and an Order the call made that the charge does not
    record is never handed out (a 409 `checkout_in_progress` instead; the next request gets the
    recorded one)."""
    organization_id = ctx.organization_id
    provider_for = _provider_for(provider)
    customer = payload.customer.model_dump() if payload.customer is not None else None

    rechecked: set[uuid.UUID] = set()

    async def claim() -> services.CheckoutClaim:
        async with session.begin():
            await bind(session, organization_id)
            return await services.claim_platform_checkout(
                session,
                capabilities_for=lambda name: capabilities_of(provider_for(name)),
                default_name=provider.name,
                organization_id=organization_id,
                invoice_id=payload.invoice_id,
                customer=customer,
                actor_id=ctx.principal.sub,
                request=request,
                rechecked=frozenset(rechecked),
            )

    claimed = await claim()
    if claimed.stale_charge_id is not None:
        # The invoice's open checkout has expired. Ask the provider about it first (it may have been
        # paid), close it if it was not, and only then open a fresh one. One with a payment still
        # in flight stays open, and so does one whose provider could not be asked: both a 409.
        await _settle(
            session, provider_for, organization_id, claimed.stale_charge_id, request=request
        )
        # Just asked: if that closed it, it is not asked about a second time below.
        rechecked.add(claimed.stale_charge_id)
        claimed = await claim()
        if claimed.stale_charge_id is not None:
            raise services.conflict(
                services.CODE_PREVIOUS_CHECKOUT_NOT_CLOSABLE,
                "The previous checkout for this invoice could not be closed yet; try again shortly",
            )
    if claimed.recheck_charge_ids:
        # Closed checkouts of this invoice whose Orders may still be paid. Ask about each before a
        # second Order is put in front of the customer: one paid meanwhile is recorded (and the
        # next claim answers "already paid"), one with a payment in flight refuses the new one.
        for closed_id in claimed.recheck_charge_ids:
            settled = await _settle(
                session, provider_for, organization_id, closed_id, request=request
            )
            if settled.in_flight:
                raise services.conflict(
                    services.CODE_PREVIOUS_PAYMENT_IN_FLIGHT,
                    "A payment on this invoice's previous checkout is still being processed; "
                    "try again in a few minutes",
                )
            rechecked.add(closed_id)
        claimed = await claim()
        if claimed.request is None:
            # Every one was just rechecked, so only a concurrent request can have changed the
            # picture meanwhile: never an open call made on a claim that holds no request.
            raise services.checkout_in_progress()

    try:
        opened = await services.run_checkout_call(provider_for(claimed.provider), claimed)
    except ProblemError:
        # The call failed (and said so as a problem): give the lease back so the next click is not
        # told the checkout is "being prepared". Nothing is failed or announced.
        async with session.begin():
            await bind(session, organization_id)
            await services.release_checkout_lease(session, organization_id, claimed.charge_id)
        raise
    async with session.begin():
        await bind(session, organization_id)
        charge = await services.record_checkout(session, claimed, opened, request=request)
    if services.withheld_order(charge, opened):
        # The charge records another Order than this call returned (two exist for one charge): the
        # browser is never handed the unrecorded one. The lease is already given back, so the next
        # request fetches the recorded Order by its id.
        raise services.checkout_in_progress()
    log.info(
        "payment.checkout.opened",
        charge_id=str(charge.id),
        organization_id=str(organization_id),
        provider=charge.provider,
        reused=claimed.reused,
    )
    assert charge.checkout_expires_at is not None
    return PlatformCheckoutOut(
        charge_id=charge.id,
        provider=charge.provider,
        client_action=opened.client_action,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        expires_at=charge.checkout_expires_at,
        test_mode=_test_mode(provider_for, charge.provider),
    )


async def _require_checkout(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> Charge:
    async with session.begin():
        await bind(session, organization_id)
        charge = await selectors.get_checkout_charge(session, organization_id, charge_id)
    if charge is None:
        # Another organization's checkout, a charge that is not a checkout, and one that does not
        # exist are one answer: never a disclosure.
        raise NotFoundProblem("No such checkout")
    return charge


@router.get(
    "/{charge_id}",
    response_model=PlatformCheckoutStatusOut,
    summary="The state of one checkout (asks the provider while it is open)",
)
async def get_checkout(
    charge_id: uuid.UUID,
    ctx: Manage,
    session: RawSessionDep,
    provider: ProviderDep,
    request: Request,
) -> PlatformCheckoutStatusOut:
    """Read-through status: while the checkout is open the provider is asked, server to server, what
    happened to its Order, and the answer reconciled like a signed webhook's. What the return page
    polls. `paid` is true only for a captured payment. A checkout closed as `checkout_expired` is
    still asked about inside its late-capture window: a payment made on its Order after it closed is
    recorded, and the answer is `paid: true`."""
    organization_id = ctx.organization_id
    provider_for = _provider_for(provider)
    charge = await _require_checkout(session, organization_id, charge_id)
    if not _asks_provider(charge, datetime.now(UTC)):
        return _status_out(charge, provider_checked=False, provider_for=provider_for)
    settled = await _settle(session, provider_for, organization_id, charge_id, request=request)
    return _status_out(
        settled.charge or charge, provider_checked=settled.asked, provider_for=provider_for
    )


@router.post(
    "/{charge_id}/confirm",
    response_model=PlatformCheckoutStatusOut,
    summary="Razorpay: verify Checkout's callback signature, then ask Razorpay what happened",
)
async def confirm_checkout(
    charge_id: uuid.UUID,
    payload: CheckoutConfirmIn,
    ctx: Manage,
    session: RawSessionDep,
    provider: ProviderDep,
    request: Request,
) -> PlatformCheckoutStatusOut:
    """Razorpay's Checkout hands the browser `{payment_id, order_id, signature}`. This checks the
    signature — `HMAC-SHA256(order_id|payment_id)` under the key secret, in constant time — and that
    the Order is this checkout's; a missing field, a mismatch or a signature that is not the digest
    is a **400 and nothing is marked paid**. Only then does it ask Razorpay (`order_payments`) and
    reconcile the answer. The callback proves nothing: `paid` is true only if that answer shows a
    captured payment."""
    organization_id = ctx.organization_id
    provider_for = _provider_for(provider)
    charge = await _require_checkout(session, organization_id, charge_id)
    adapter = provider_for(charge.provider)
    if not capabilities_of(adapter).checkout_confirmation:
        raise ConflictProblem("This checkout has no browser confirmation; poll its status instead")
    order_id, payment_id, signature = (
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
    )
    verify = getattr(adapter, "verify_checkout_signature", None)
    if (
        not order_id
        or not payment_id
        or not signature
        or order_id != charge.provider_order_id
        or not callable(verify)
        or not verify(order_id=order_id, payment_id=payment_id, signature=signature)
    ):
        raise services.InvalidCheckoutConfirmationProblem("could not verify the confirmation")

    # A checkout already closed as `checkout_expired` is asked about too, inside its late-capture
    # window: the callback is proof that a payment was made on its Order, and a capture of it is
    # money that moved after this platform stopped waiting.
    if not _asks_provider(charge, datetime.now(UTC)):
        return _status_out(charge, provider_checked=False, provider_for=provider_for)
    if CREDENTIALS.tripped(adapter.name):
        raise services.CheckoutUnavailableProblem("Online payment is temporarily unavailable")
    settled = await _settle(session, provider_for, organization_id, charge_id, request=request)
    if not settled.asked:
        # The signature held, but the provider could not be asked: not "paid", not "failed" — the
        # browser polls `GET` and learns when it can.
        raise ProviderUnreachableProblem(
            "The payment provider could not confirm the payment yet; try again"
        )
    return _status_out(settled.charge or charge, provider_checked=True, provider_for=provider_for)
