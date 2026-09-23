"""The internal API — service-to-service, never reachable from outside the cluster.

This is where the financial record is *written*: a billing run finalizes an invoice, an operator
delegate grants comp credit, a correction reverses a ledger entry. There is no person and no
gateway-forwarded principal here — the caller is another service — so the routes do not take
`get_tenant_session`. Instead each one binds the transaction to the **organization named in the
request body** as its first statement, exactly as `get_tenant_session` binds to a principal's
organization, so row-level security and every `WITH CHECK` apply to these writes too.

Guarded by a short-lived service token addressed to this service by name (`require_service_token`).
Mounted at the root and excluded from the OpenAPI document: this is not public API and must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import billing_run, selectors, services
from app.config import Settings
from app.models import CREDIT_GRANT
from app.schemas import (
    BilledPeriodOut,
    BillSubscriptionPeriodIn,
    CreditConsumeIn,
    CreditEntryOut,
    CreditGrantIn,
    CreditPackOut,
    CreditReverseIn,
    FinalizeInvoiceIn,
    InvoiceOut,
    VoidInvoiceIn,
)
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import BusinessRuleProblem, ForbiddenProblem, NotFoundProblem

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the payload's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


@router.post(
    "/invoices:finalize",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a finalized invoice from explicit line items",
)
async def finalize_invoice(
    payload: FinalizeInvoiceIn, session: SessionDep, request: Request
) -> InvoiceOut:
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        invoice = await services.finalize_invoice(session, payload, request=request)
        fresh = await services.require_invoice(session, invoice.id)
        return InvoiceOut.model_validate(fresh)


#: The services allowed to ask for a billing-run invoice. The service token proves *a* platform
#: service is calling; this names which one may. Only subscription owns the period boundaries,
#: so only subscription may ask for a period to be invoiced — a compromised or buggy sibling
#: holding a valid token for billing still cannot mint invoices for arbitrary periods.
BILLING_RUN_CALLERS = frozenset({"subscription"})


def get_billing_catalog(request: Request) -> billing_run.Catalog:
    """The catalog read the billing run prices from. A dependency so a test substitutes a fake
    without any network, exactly as subscription's pin lookup does."""
    settings: Settings = request.app.state.settings
    return billing_run.HttpCatalog(settings.catalog_service_url, settings)


def get_billing_metering(request: Request) -> billing_run.Metering:
    settings: Settings = request.app.state.settings
    return billing_run.HttpMetering(settings.metering_service_url, settings)


@router.post(
    "/subscription-invoices",
    response_model=BilledPeriodOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invoice one subscription period, once — the billing run",
    responses={200: {"model": BilledPeriodOut, "description": "Already invoiced"}},
)
async def bill_subscription_period(
    payload: BillSubscriptionPeriodIn,
    session: SessionDep,
    request: Request,
    catalog: Annotated[billing_run.Catalog, Depends(get_billing_catalog)],
    metering: Annotated[billing_run.Metering, Depends(get_billing_metering)],
) -> BilledPeriodOut | JSONResponse:
    """Price and finalize one subscription period's invoice, idempotently on the period.

    **No transaction is opened here.** `billing_run.bill_subscription_period` opens two short
    ones itself and makes the catalog and metering calls between them — the claim → call →
    record shape that keeps a slow sibling from holding a row lock open (ADR-0005 addendum).
    201 when this call created the invoice, 200 with the same body when the period was already
    invoiced — a redelivery, or the loser of a race.
    """
    caller = getattr(request.state, "service_caller", None)
    if caller not in BILLING_RUN_CALLERS:
        raise ForbiddenProblem("this service may not raise billing-run invoices")
    settings: Settings = request.app.state.settings
    invoice, created = await billing_run.bill_subscription_period(
        session,
        payload,
        catalog=catalog,
        metering=metering,
        settings=settings,
        request=request,
    )
    body = BilledPeriodOut(created=created, invoice=InvoiceOut.model_validate(invoice))
    if created:
        return body
    return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))


@router.post(
    "/invoices/{invoice_id}:void",
    response_model=InvoiceOut,
    summary="Void a finalized invoice — the one legal post-finalize transition",
)
async def void_invoice(
    invoice_id: uuid.UUID, payload: VoidInvoiceIn, session: SessionDep, request: Request
) -> InvoiceOut:
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        invoice = await services.require_invoice(session, invoice_id)
        invoice = await services.void_invoice(session, invoice, payload, request=request)
        fresh = await services.require_invoice(session, invoice.id)
        return InvoiceOut.model_validate(fresh)


@router.post(
    "/credit",
    response_model=CreditEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Grant credit (comp access / goodwill) with a validity window",
)
async def grant_credit(
    payload: CreditGrantIn, session: SessionDep, request: Request
) -> CreditEntryOut:
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        entry = await services.grant_credit(session, payload, request=request)
        return CreditEntryOut.model_validate(entry)


@router.post(
    "/credit:consume",
    response_model=CreditEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Spend credit — a negative append-only row",
)
async def consume_credit(
    payload: CreditConsumeIn, session: SessionDep, request: Request
) -> CreditEntryOut:
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        entry = await services.consume_credit(session, payload, request=request)
        return CreditEntryOut.model_validate(entry)


@router.get(
    "/organizations/{organization_id}/credit/packs/{pack_id}",
    response_model=CreditPackOut,
    summary="What is left on one prepaid credit pack",
)
async def get_credit_pack(
    organization_id: uuid.UUID, pack_id: uuid.UUID, session: SessionDep, request: Request
) -> CreditPackOut:
    """The pack, and its remaining balance as a `SUM` over everything drawn from it.

    A billing run asks this **before** drawing so it knows how much of an invoice the pack can
    cover and how much is still payable. Asking afterwards, by trying to draw the whole invoice
    and catching the refusal, would work — and would make the ordinary case an exception path.

    The organization is in the path rather than inferred, because the transaction has to be bound
    to a tenant as its first statement and there is no principal on an internal call to infer one
    from. Row-level security then confirms the pack really belongs there: another tenant's id is
    a 404, not a 403.
    """
    async with session.begin():
        await _bind(session, organization_id, request)
        pack = await selectors.get_credit_entry(session, pack_id)
        if pack is None:
            raise NotFoundProblem("No such credit ledger entry")
        if pack.entry_type != CREDIT_GRANT:
            raise BusinessRuleProblem(
                f"entry {pack.id} is a {pack.entry_type}, not a grant, so it is not a pack"
            )
        remaining = await selectors.pack_remaining(session, pack.id)
        at = datetime.now(UTC)
        return CreditPackOut(
            id=pack.id,
            currency=pack.currency,
            granted_minor=pack.amount_minor,
            remaining_minor=remaining,
            reason=pack.reason,
            valid_from=pack.valid_from,
            valid_until=pack.valid_until,
            lapsed=pack.valid_until is not None and pack.valid_until <= at,
            as_of=at,
        )


@router.post(
    "/credit:reverse",
    response_model=CreditEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reverse an earlier ledger entry with a negating row",
)
async def reverse_credit(
    payload: CreditReverseIn, session: SessionDep, request: Request
) -> CreditEntryOut:
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        entry = await services.reverse_credit(session, payload, request=request)
        return CreditEntryOut.model_validate(entry)
