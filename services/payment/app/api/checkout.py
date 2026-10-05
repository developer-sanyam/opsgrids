"""Platform checkout — the customer surface for paying one of OpsGrids' own invoices (ADR-0029).

Three routes, one owner gate. A person behind the gateway (`/api/v1/payment/checkouts…`) who may
**activate and deactivate products** for the organization — `core.product.activation.write` at
`ORG`, the permission that guards subscribe, cancel and change-plan, because paying for a
subscription is the same spend decision. No new permission: that would mean an ADR-0007 rollout to
every live tenant for a gate the existing one already expresses.

* `POST   /payment/checkouts` — open (or reopen) the checkout for an invoice. The amount comes from
  the invoice's own charge, never the body; one open checkout per invoice; claim → commit → provider
  call **outside any transaction** → record.
* `GET    /payment/checkouts/{charge_id}` — read-through status: while the checkout is open the
  provider is asked what happened, and the answer reconciled through the path a signed webhook
  takes. What a
  return page polls.
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
from app.providers.base import PaymentProvider, capabilities_of
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


def _status_out(charge: Charge, *, provider_checked: bool) -> PlatformCheckoutStatusOut:
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
    )


async def _settle(
    session: AsyncSession,
    provider_for: ProviderResolver,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    *,
    request: Request | None,
    respect_gap: bool = False,
) -> tuple[Charge | None, bool]:
    """Ask the provider what happened to one checkout and reconcile the answer: claim → call →
    record, three acts, **no transaction open across the call**. Returns the charge as it stands
    afterwards and whether the provider could be asked."""
    now = datetime.now(UTC)
    async with session.begin():
        await bind(session, organization_id)
        ticket = await services.claim_poll(
            session, organization_id, charge_id, now=now, respect_gap=respect_gap
        )
    if ticket is None:
        async with session.begin():
            await bind(session, organization_id)
            return await selectors.get_checkout_charge(session, organization_id, charge_id), False
    snapshots = await services.fetch_order_payments(provider_for(ticket.provider), ticket)
    async with session.begin():
        await bind(session, organization_id)
        charge = await services.record_poll(session, ticket, snapshots, now=now, request=request)
    return charge, snapshots is not None


@router.post(
    "",
    response_model=PlatformCheckoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open (or reopen) the checkout for an invoice",
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
    the invoice, **409** if it is already paid, **422** for what could never be paid online."""
    organization_id = ctx.organization_id
    provider_for = _provider_for(provider)
    customer = payload.customer.model_dump() if payload.customer is not None else None

    async def claim() -> services.CheckoutClaim:
        async with session.begin():
            await bind(session, organization_id)
            return await services.claim_checkout(
                session,
                provider_for=provider_for,
                default_name=provider.name,
                organization_id=organization_id,
                invoice_id=payload.invoice_id,
                customer=customer,
                actor_id=ctx.principal.sub,
                request=request,
            )

    claimed = await claim()
    if claimed.stale_charge_id is not None:
        # The invoice's open checkout has expired. Ask the provider about it first (it may have been
        # paid), close it if it was not, and only then open a fresh one.
        await _settle(
            session, provider_for, organization_id, claimed.stale_charge_id, request=request
        )
        claimed = await claim()
        if claimed.stale_charge_id is not None:
            raise ConflictProblem(
                "The previous checkout for this invoice could not be closed yet; try again shortly"
            )

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
    polls. `paid` is true only for a captured payment."""
    organization_id = ctx.organization_id
    charge = await _require_checkout(session, organization_id, charge_id)
    if charge.state in (CHARGE_CAPTURED, CHARGE_FAILED) or charge.provider_order_id is None:
        return _status_out(charge, provider_checked=False)
    settled, asked = await _settle(
        session, _provider_for(provider), organization_id, charge_id, request=request
    )
    return _status_out(settled or charge, provider_checked=asked)


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
    charge = await _require_checkout(session, organization_id, charge_id)
    adapter = _provider_for(provider)(charge.provider)
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

    if charge.state in (CHARGE_CAPTURED, CHARGE_FAILED):
        return _status_out(charge, provider_checked=False)
    if CREDENTIALS.tripped(adapter.name):
        raise services.CheckoutUnavailableProblem("Online payment is temporarily unavailable")
    settled, asked = await _settle(
        session, _provider_for(provider), organization_id, charge_id, request=request
    )
    if not asked:
        # The signature held, but the provider could not be asked: not "paid", not "failed" — the
        # browser polls `GET` and learns when it can.
        raise ProviderUnreachableProblem(
            "The payment provider could not confirm the payment yet; try again"
        )
    return _status_out(settled or charge, provider_checked=True)
