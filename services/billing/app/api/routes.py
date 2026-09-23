"""Customer HTTP surface for billing — an organization's own invoices and credit balance.

Fronted by the product gateway at `/api/v1/billing`. Every route is tenant-scoped: it goes
through `get_tenant_session` (the chokepoint that binds the transaction to the caller's
organization) and requires an `ORG`-scoped permission, because an invoice is an organization-
level document with no "own" reading. Each list carries a negative-authz test (ADR-0003 §3).

These are reads only. Invoices and credit are written through the internal, service-token API
(`app/api/internal.py`) — a customer sees the financial record, it is not produced by their
request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.models import INVOICE_DRAFT, SCHEMA
from app.schemas import CreditBalanceOut, InvoiceOut, ServiceInfo
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem

router = APIRouter(tags=["billing"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: Reading invoices and the credit balance is an organization-level act — there is no per-user
#: "own" invoice — so both require ORG scope.
InvoiceRead = Annotated[
    ScopeContext, Depends(require("core.billing.invoice.read", minimum=Scope.ORG))
]
CreditRead = Annotated[
    ScopeContext, Depends(require("core.billing.credit.read", minimum=Scope.ORG))
]


@router.get("/billing/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get("/invoices", response_model=list[InvoiceOut], summary="This organization's invoices")
async def list_invoices(session: SessionDep, ctx: InvoiceRead) -> list[InvoiceOut]:
    invoices = await selectors.list_invoices(session, ctx)
    return [InvoiceOut.model_validate(inv) for inv in invoices]


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, summary="One invoice")
async def get_invoice(invoice_id: uuid.UUID, session: SessionDep, ctx: InvoiceRead) -> InvoiceOut:
    invoice = await selectors.get_invoice(session, invoice_id)
    if invoice is None or invoice.state == INVOICE_DRAFT:
        # A billing-run draft is invisible to the customer until it is finalized.
        raise NotFoundProblem("No such invoice")
    return InvoiceOut.model_validate(invoice)


@router.get(
    "/credit-balance",
    response_model=CreditBalanceOut,
    summary="Credit available now — the windowed SUM over the ledger (ADR-0004 §3)",
)
async def credit_balance(
    session: SessionDep,
    ctx: CreditRead,
    currency: Annotated[str, Query(min_length=3, max_length=3, description="ISO-4217 code")],
) -> CreditBalanceOut:
    """Balance is derived, never stored: a `SUM` over the ledger rows whose validity window
    covers this instant. A lapsed grant is already gone from it, with nothing having run."""
    at = datetime.now(UTC)
    balance = await selectors.credit_balance(session, currency=currency, at=at)
    return CreditBalanceOut(
        organization_id=ctx.organization_id,
        currency=currency.upper(),
        balance_minor=balance,
        as_of=at,
    )
