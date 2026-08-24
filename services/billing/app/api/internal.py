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
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.schemas import (
    CreditConsumeIn,
    CreditEntryOut,
    CreditGrantIn,
    CreditReverseIn,
    FinalizeInvoiceIn,
    InvoiceOut,
    VoidInvoiceIn,
)
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant

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
