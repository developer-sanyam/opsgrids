"""Tenant collections — the internal API operations calls (ADR-0024 §8).

Only `operations` may call it (`COLLECTION_CALLERS`): it owns the order, so it is the one service
that knows what an order's payment is *for* and how much it is — the amount here is the order's
own stored total, never something a browser typed. Operations checks the person's permission
(`operations.orders.payment.*`) and the order's state first; this service checks everything about
the money.

Each route binds its transaction to the organization in the path as its first statement, and
every lookup below also filters on it and on the reference (`reference_type`/`reference_id`) in
the path: a collection id from another order, or another organization, is a 404.

**Claim → call → record.** A route that talks to the provider runs up to three acts: a short
transaction that claims (and opens the merchant's sealed key into memory for one call), the
provider call with no transaction or lock held, and a second short transaction that records the
answer. Mounted at the root, excluded from the public OpenAPI document.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.collections import domain, selectors, services
from app.config import settings
from app.deps import CollectionsProviderFactory, get_collections_provider_factory
from app.providers.base import OrderResult
from app.schemas import (
    CashfreeCheckoutSessionOut,
    CheckoutIn,
    CheckoutOut,
    CollectionActorIn,
    CollectionOut,
    CollectionRefundIn,
    RazorpayCheckoutSessionOut,
    ReferenceCollectionsOut,
)
from platform_core.auth import require_service_token
from platform_core.db import get_session
from platform_core.errors import ConflictProblem, ForbiddenProblem, NotFoundProblem
from platform_core.logging import get_logger

log = get_logger("payment.collections.internal")

#: The one service that may ask payment to take, check, cancel or refund a tenant's collection.
COLLECTION_CALLERS = frozenset({"operations"})


def require_collection_caller(request: Request) -> None:
    if getattr(request.state, "service_caller", None) not in COLLECTION_CALLERS:
        raise ForbiddenProblem("this service may not manage tenant collections")


router = APIRouter(
    prefix="/internal/v1/organizations/{organization_id}/collections/{reference_type}/{reference_id}",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token), Depends(require_collection_caller)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
FactoryDep = Annotated[CollectionsProviderFactory, Depends(get_collections_provider_factory)]


async def _view(
    session: AsyncSession, organization_id: uuid.UUID, charge_id: uuid.UUID
) -> CollectionOut:
    async with session.begin():
        await services.bind(session, organization_id)
        charge = await selectors.get_collection(session, organization_id, charge_id)
        if charge is None:  # pragma: no cover - the route checked it a moment ago
            raise NotFoundProblem("No such online payment")
        return (await selectors.collection_views(session, organization_id, [charge]))[0]


@router.get("", response_model=ReferenceCollectionsOut)
async def list_collections(
    organization_id: uuid.UUID, reference_type: str, reference_id: uuid.UUID, session: SessionDep
) -> ReferenceCollectionsOut:
    """Every online payment taken for one order, newest first."""
    async with session.begin():
        await services.bind(session, organization_id)
        account = await selectors.live_merchant_account(session, organization_id)
        charges = await selectors.collections_for_reference(
            session, organization_id, reference_type, reference_id
        )
        views = await selectors.collection_views(session, organization_id, charges)
    provider = account.provider if account is not None else None
    return ReferenceCollectionsOut(
        merchant_account_configured=account is not None,
        mode=account.mode if account is not None else None,
        wire=settings().collections_wire,
        provider=provider,
        provider_wire=settings().collections_wire_for(provider) if provider else None,
        collections=views,
    )


def _payer(payload: CheckoutIn) -> services.Payer | None:
    if payload.payer is None:
        return None
    return services.Payer(
        name=payload.payer.name, email=payload.payer.email, phone=payload.payer.phone
    )


@router.post("/checkout", response_model=CheckoutOut)
async def open_checkout(
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    payload: CheckoutIn,
    session: SessionDep,
    factory: FactoryDep,
    request: Request,
) -> CheckoutOut:
    """Open (or reuse) the checkout for one order and return what a browser needs to open the
    provider's own hosted checkout — Razorpay: the public key id and the provider Order id;
    Cashfree: the `payment_session_id`, fetched from Cashfree for this response and never stored.
    Never a secret. A provider that needs the paying customer refuses a request without `payer`
    (422 `payer-required`) before anything is written."""
    async with session.begin():
        await services.bind(session, organization_id)
        claim = await services.claim_checkout(
            session,
            organization_id=organization_id,
            reference_type=reference_type,
            reference_id=reference_id,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            payer=_payer(payload),
            request=request,
        )

    order: OrderResult | None = None
    if claim.ticket is not None:
        try:
            order = await services.create_provider_order(factory, claim.ticket)
        except services.ProviderRefusedProblem:
            # Refused: nothing was created there. Release the right to call so a fix (new keys)
            # can be retried at once; a timeout keeps it, so a retry cannot race a late create.
            if claim.reopen:
                # A reopen holds no right to call, so there is nothing to release. The Order is
                # there but cannot be paid any more (expired at the provider, or no session).
                raise ConflictProblem(
                    "This online payment can no longer be reopened at the payment provider; "
                    "cancel it and start a new one"
                ) from None
            async with session.begin():
                await services.bind(session, organization_id)
                await services.release_call(
                    session, organization_id=organization_id, charge_id=claim.charge_id
                )
            raise
        if not claim.reopen:
            async with session.begin():
                await services.bind(session, organization_id)
                await services.record_checkout(
                    session,
                    organization_id=organization_id,
                    charge_id=claim.charge_id,
                    provider_order_id=order.provider_order_id,
                    request=request,
                )

    async with session.begin():
        await services.bind(session, organization_id)
        charge = await selectors.get_collection(session, organization_id, claim.charge_id)
        account = await selectors.live_merchant_account(session, organization_id)
        assert charge is not None
        view = (await selectors.collection_views(session, organization_id, [charge]))[0]
    checkout: RazorpayCheckoutSessionOut | CashfreeCheckoutSessionOut | None = None
    if (
        charge.provider_order_id is not None
        and account is not None
        and account.id == charge.merchant_account_id
        and charge.state not in ("captured", "cancelled", "expired")
    ):
        openable = settings().collections_wire_for(account.provider) == "live"
        if account.provider == domain.PROVIDER_CASHFREE:
            # The session token is in memory only — this response is the one place it goes.
            token = order.checkout_token if order is not None else None
            if token is not None and order is not None:
                checkout = CashfreeCheckoutSessionOut(
                    provider="cashfree",
                    payment_session_id=token,
                    mode="production" if account.mode == "live" else "sandbox",
                    provider_order_id=charge.provider_order_id,
                    amount_minor=charge.amount_minor,
                    currency=charge.currency,
                    openable=openable,
                )
        else:
            checkout = RazorpayCheckoutSessionOut(
                provider="razorpay",
                key_id=account.key_id,
                provider_order_id=charge.provider_order_id,
                amount_minor=charge.amount_minor,
                currency=charge.currency,
                mode=account.mode,
                openable=openable,
            )
    log.info(
        "payment.collection.checkout",
        organization_id=str(organization_id),
        charge_id=str(claim.charge_id),
        provider=charge.provider,
        reopened=claim.reopen,
        ready=checkout is not None,
        state=view.state,
    )
    return CheckoutOut(collection=view, checkout=checkout)


@router.post("/charges/{charge_id}/sync", response_model=CollectionOut)
async def check_status(
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
    session: SessionDep,
    factory: FactoryDep,
    request: Request,
) -> CollectionOut:
    """The verified server-to-server status check: ask the provider, with the merchant's own keys,
    what happened to this collection's Order, and apply it. What the browser's Checkout callback
    reported is never an input — this route takes no body at all."""
    async with session.begin():
        await services.bind(session, organization_id)
        ticket = await services.claim_status_check(
            session,
            organization_id=organization_id,
            reference_type=reference_type,
            reference_id=reference_id,
            charge_id=charge_id,
        )
    if ticket is not None:
        payments = await services.fetch_order_payments(factory, ticket)
        async with session.begin():
            await services.bind(session, organization_id)
            await services.record_status_check(
                session,
                organization_id=organization_id,
                charge_id=charge_id,
                payments=payments,
                request=request,
            )
    return await _view(session, organization_id, charge_id)


@router.post("/charges/{charge_id}/cancel", response_model=CollectionOut)
async def cancel(
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
    payload: CollectionActorIn,
    session: SessionDep,
    factory: FactoryDep,
    request: Request,
) -> CollectionOut:
    """Cancel an unpaid checkout, after asking the provider that it really is unpaid."""
    async with session.begin():
        await services.bind(session, organization_id)
        ticket = await services.claim_cancel(
            session,
            organization_id=organization_id,
            reference_type=reference_type,
            reference_id=reference_id,
            charge_id=charge_id,
        )
    if ticket is not None:
        payments = await services.fetch_order_payments(factory, ticket)
        # Committed on its own: if the customer has paid, that is recorded even though the
        # cancel below is then refused.
        async with session.begin():
            await services.bind(session, organization_id)
            await services.record_status_check(
                session,
                organization_id=organization_id,
                charge_id=charge_id,
                payments=payments,
                request=request,
            )
    async with session.begin():
        await services.bind(session, organization_id)
        await services.record_cancel(
            session,
            organization_id=organization_id,
            charge_id=charge_id,
            actor_id=payload.actor_id,
            request=request,
        )
    return await _view(session, organization_id, charge_id)


@router.post("/charges/{charge_id}/refunds", response_model=CollectionOut)
async def refund_collection(
    organization_id: uuid.UUID,
    reference_type: str,
    reference_id: uuid.UUID,
    charge_id: uuid.UUID,
    payload: CollectionRefundIn,
    session: SessionDep,
    factory: FactoryDep,
    request: Request,
) -> CollectionOut:
    """Refund part or all of a captured collection. A retry with the same `idempotency_key` is
    the same refund (here, and at the provider)."""
    async with session.begin():
        await services.bind(session, organization_id)
        claim = await services.claim_refund(
            session,
            organization_id=organization_id,
            reference_type=reference_type,
            reference_id=reference_id,
            charge_id=charge_id,
            amount_minor=payload.amount_minor,
            caller_key=payload.idempotency_key,
        )
    if claim.existing is None:
        result = await services.call_refund(factory, claim)
        async with session.begin():
            await services.bind(session, organization_id)
            await services.record_refund(
                session,
                organization_id=organization_id,
                charge_id=charge_id,
                claim=claim,
                result=result,
                reason=payload.reason,
                actor_id=payload.actor_id,
                request=request,
            )
    return await _view(session, organization_id, charge_id)
