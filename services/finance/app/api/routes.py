"""HTTP surface for finance.

Every route declares the permission it needs and is floored at ORG — finance is an
organization-level product (see `app.scope`), so there is no OWN reading of a company's books
and a narrower grant is refused rather than silently widened. Every principal route is also
guarded by `require_product("finance")` — defence in depth behind the gateway's entitlement
check (a routing mistake must not become an entitlement bypass).

Reads go through `selectors`; a router that builds its own query is how an organization leaks, so
none of them do. Every route takes the tenant-bound session and opens no transaction of its own —
the chokepoint (`get_tenant_session`) owns it.

The paths sit under `/finance/...`, which the gateway maps to `/api/v1/finance/...` — the prefix
already routed to this service in `app-gateway/routes.json`. They match the finance SPA's
`client.ts` call shapes (list+detail invoices, list payments, list customers, overview, ledger).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    CustomerSummary,
    EInvoiceFilingOut,
    GstRegistrationOut,
    GstRegistrationUpsert,
    InvoiceOut,
    LedgerReport,
    OverviewSummary,
    PaymentOut,
    ServiceInfo,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the finance scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers finance scope questions.
scope.register()

router = APIRouter(tags=["finance"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `finance` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("finance"))

# Read contexts. Each floors at ORG: a company's books have no per-user "own" reading.
InvoiceRead = Annotated[
    ScopeContext, Depends(require("finance.billing.invoice.read", minimum=Scope.ORG))
]
PaymentRead = Annotated[
    ScopeContext, Depends(require("finance.billing.payment.read", minimum=Scope.ORG))
]
CustomerRead = Annotated[
    ScopeContext, Depends(require("finance.billing.customer.read", minimum=Scope.ORG))
]
SummaryRead = Annotated[
    ScopeContext, Depends(require("finance.reports.summary.read", minimum=Scope.ORG))
]
LedgerRead = Annotated[
    ScopeContext, Depends(require("finance.reports.ledger.read", minimum=Scope.ORG))
]
FilingRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.einvoice.read", minimum=Scope.ORG))
]
RegistrationRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.registration.read", minimum=Scope.ORG))
]
RegistrationWrite = Annotated[
    ScopeContext, Depends(require("finance.compliance.registration.write", minimum=Scope.ORG))
]


@router.get("/finance/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# --------------------------------------------------------------------- invoices


@router.get(
    "/finance/invoices",
    response_model=Page[InvoiceOut],
    summary="Invoices and credit notes",
    dependencies=[Entitled],
)
async def list_invoices(
    session: SessionDep,
    ctx: InvoiceRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Invoice number or customer name")] = None,
    status: Annotated[str | None, Query(description="Lifecycle status filter")] = None,
    sort: Annotated[
        str, Query(description="issued_desc|issued_asc|amount_desc|amount_asc|due_asc")
    ] = "issued_desc",
) -> Page[InvoiceOut]:
    stmt = selectors.invoices_stmt(ctx, q=q, status=status, sort=sort)
    rows, total = await paginate(session, stmt, params)
    items = await selectors.invoices_with_filings(session, ctx.organization_id, rows)
    return Page.build(items, total=total, params=params)


@router.get(
    "/finance/invoices/{invoice_id}",
    response_model=InvoiceOut,
    summary="One invoice",
    dependencies=[Entitled],
)
async def read_invoice(invoice_id: uuid.UUID, session: SessionDep, ctx: InvoiceRead) -> InvoiceOut:
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    items = await selectors.invoices_with_filings(session, ctx.organization_id, [invoice])
    return items[0]


# --------------------------------------------------------------------- GST compliance


@router.get(
    "/finance/invoices/{invoice_id}/filings",
    response_model=list[EInvoiceFilingOut],
    summary="Every e-invoice filing attempt for one document, newest first",
    dependencies=[Entitled],
)
async def list_filings(
    invoice_id: uuid.UUID, session: SessionDep, ctx: FilingRead
) -> list[EInvoiceFilingOut]:
    """The filing history, which is a plain query because the table is append-only.

    Behind its own permission rather than the invoice one: seeing the receivable book and seeing
    what was reported to the tax authority are different questions, and an organization may
    reasonably answer them for different people.
    """
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    rows = await selectors.filings_for_invoice(session, ctx.organization_id, invoice_id)
    return [selectors.to_filing_out(row) for row in rows]


@router.get(
    "/finance/gst/registration",
    response_model=GstRegistrationOut,
    summary="This organization's GST registration",
    dependencies=[Entitled],
)
async def read_gst_registration(session: SessionDep, ctx: RegistrationRead) -> GstRegistrationOut:
    registration = await selectors.get_gst_registration(session, ctx.organization_id)
    if registration is None:
        raise NotFoundProblem("This organization has no GST registration configured")
    return selectors.to_gst_registration_out(registration)


@router.put(
    "/finance/gst/registration",
    response_model=GstRegistrationOut,
    summary="Set this organization's GST registration",
    dependencies=[Entitled],
)
async def set_gst_registration(
    payload: GstRegistrationUpsert, session: SessionDep, ctx: RegistrationWrite, request: Request
) -> GstRegistrationOut:
    """The one write on the public finance surface, and the only one this slice adds.

    It is not a document: a registration is configuration, so it is idempotent on the GSTIN and
    updates in place. What it must never do is rewrite history — an invoice already filed keeps
    the `seller_gstin` it was filed under, because that column is a snapshot on the invoice and
    not a join to here.
    """
    registration = await services.set_gst_registration(session, ctx, payload, request=request)
    return selectors.to_gst_registration_out(registration)


# --------------------------------------------------------------------- payments


@router.get(
    "/finance/payments",
    response_model=Page[PaymentOut],
    summary="Payments received",
    dependencies=[Entitled],
)
async def list_payments(
    session: SessionDep, ctx: PaymentRead, params: PageQuery
) -> Page[PaymentOut]:
    items, total = await selectors.list_payments(session, ctx, params)
    return Page.build(items, total=total, params=params)


# --------------------------------------------------------------------- customers


@router.get(
    "/finance/customers",
    response_model=Page[CustomerSummary],
    summary="Customers and their receivable positions",
    dependencies=[Entitled],
)
async def list_customers(
    session: SessionDep,
    ctx: CustomerRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Customer name or city")] = None,
    sort: Annotated[
        str, Query(description="receivables_desc|overdue_desc|name_asc")
    ] = "receivables_desc",
) -> Page[CustomerSummary]:
    summaries = await selectors.list_customer_summaries(session, ctx, q=q, sort=sort)
    window = summaries[params.offset : params.offset + params.size]
    return Page.build(window, total=len(summaries), params=params)


# --------------------------------------------------------------------- reports


@router.get(
    "/finance/overview",
    response_model=OverviewSummary,
    summary="Finance overview — KPIs, cashflow, AR aging, attention",
    dependencies=[Entitled],
)
async def get_overview(session: SessionDep, ctx: SummaryRead) -> OverviewSummary:
    return await selectors.get_overview(session, ctx)


@router.get(
    "/finance/ledger",
    response_model=LedgerReport,
    summary="Ledger / trial balance",
    dependencies=[Entitled],
)
async def get_ledger(session: SessionDep, ctx: LedgerRead) -> LedgerReport:
    # The trial balance is representative booked data (see `app.domain`); the tenant-bound
    # session is still taken so the route stays inside the chokepoint like every other.
    return selectors.get_ledger()
