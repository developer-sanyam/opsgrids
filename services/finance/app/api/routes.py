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

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    CustomerCreate,
    CustomerOut,
    CustomerSummary,
    CustomerUpdate,
    EInvoiceFilingOut,
    EwayBillOut,
    EwayBillWithInvoice,
    EwayState,
    FileReturnIn,
    GenerateReturnIn,
    GstRegistrationOut,
    GstRegistrationUpsert,
    GstReturnDetail,
    GstReturnOut,
    ImsDecisionCreate,
    ImsDecisionOut,
    ImsDocumentDetail,
    ImsDocumentOut,
    InvoiceCreate,
    InvoiceIssueIn,
    InvoiceOut,
    ItcBalanceOut,
    ItcLedgerEntryOut,
    LedgerReport,
    OverviewSummary,
    PaymentCreate,
    PaymentOut,
    PurchaseDocumentCreate,
    PurchaseDocumentOut,
    ReturnDiffOut,
    ServiceInfo,
    WithholdingRateOut,
    WithholdingSummaryOut,
    WithholdingWithInvoice,
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
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `finance` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("finance"))

# Read contexts. Each floors at ORG: a company's books have no per-user "own" reading.
InvoiceRead = Annotated[
    ScopeContext, Depends(require("finance.billing.invoice.read", minimum=Scope.ORG))
]
#: Issuing a document is dangerous: a B2B/export/SEZ document stages a real IRP filing request on
#: the outbox in the same transaction as the write.
InvoiceIssue = Annotated[
    ScopeContext, Depends(require("finance.billing.invoice.issue", minimum=Scope.ORG))
]
PaymentRead = Annotated[
    ScopeContext, Depends(require("finance.billing.payment.read", minimum=Scope.ORG))
]
PaymentRecord = Annotated[
    ScopeContext, Depends(require("finance.billing.payment.record", minimum=Scope.ORG))
]
CustomerRead = Annotated[
    ScopeContext, Depends(require("finance.billing.customer.read", minimum=Scope.ORG))
]
CustomerWrite = Annotated[
    ScopeContext, Depends(require("finance.billing.customer.write", minimum=Scope.ORG))
]
PurchaseRead = Annotated[
    ScopeContext, Depends(require("finance.billing.purchase.read", minimum=Scope.ORG))
]
PurchaseWrite = Annotated[
    ScopeContext, Depends(require("finance.billing.purchase.write", minimum=Scope.ORG))
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
ImsRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.ims.read", minimum=Scope.ORG))
]
#: Deciding is not reading. Accepting a supplier's document claims the input tax credit on it and
#: rejecting one declines the credit and sends the document back — money either way, which is why
#: this is a separate, dangerous permission rather than a wider read.
ImsDecide = Annotated[
    ScopeContext, Depends(require("finance.compliance.ims.decide", minimum=Scope.ORG))
]
ReturnRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.returns.read", minimum=Scope.ORG))
]
#: Assembling a period and *filing* it are two different acts behind two different permissions.
#: Generating states what this platform believes about its own book; filing makes a claim to a
#: government. Only the second is marked dangerous, and only the second is irreversible.
ReturnGenerate = Annotated[
    ScopeContext, Depends(require("finance.compliance.returns.generate", minimum=Scope.ORG))
]
ReturnFile = Annotated[
    ScopeContext, Depends(require("finance.compliance.returns.file", minimum=Scope.ORG))
]
EwayRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.eway.read", minimum=Scope.ORG))
]
WithholdingRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.withholding.read", minimum=Scope.ORG))
]
#: No write dependency: every ITC ledger posting is a side effect of an IMS decision or a
#: GSTR-3B filing, each already behind its own dangerous permission.
ItcRead = Annotated[
    ScopeContext, Depends(require("finance.compliance.itc.read", minimum=Scope.ORG))
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
    customer_id: Annotated[uuid.UUID | None, Query(description="One customer's documents")] = None,
    sort: Annotated[
        str, Query(description="issued_desc|issued_asc|amount_desc|amount_asc|due_asc")
    ] = "issued_desc",
) -> Page[InvoiceOut]:
    stmt = selectors.invoices_stmt(ctx, q=q, status=status, customer_id=customer_id, sort=sort)
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


@router.post(
    "/finance/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an invoice, or a credit note against one",
    dependencies=[Entitled],
)
async def issue_invoice(
    payload: InvoiceIssueIn, session: SessionDep, ctx: InvoiceIssue, request: Request
) -> InvoiceOut:
    """Finalize a new document. There is no edit, delete, void or draft -> issued transition —
    the only legitimate corrections are a credit note (this same route, `document_type` =
    `credit_note` and `corrects_invoice_id` set) and, for an e-invoiced document, an IRN
    cancellation inside its 24-hour window.

    The number is never taken from the caller: the server allocates the next one in this
    organization's own series (`services.allocate_invoice_number`), so a duplicate is
    structurally impossible rather than merely checked for. The lifecycle status is always
    derived, never forced — `InvoiceIssueIn` carries no `status` field at all.
    """
    number = await services.allocate_invoice_number(
        session, ctx, document_type=payload.document_type, on=payload.issued_date
    )
    create = InvoiceCreate(
        number=number,
        customer_id=payload.customer_id,
        issued_date=payload.issued_date,
        due_date=payload.due_date,
        tax_rate_pct=payload.tax_rate_pct,
        lines=payload.lines,
        document_type=payload.document_type,
        status=None,
        currency=payload.currency,
        notes=payload.notes,
        corrects_invoice_id=payload.corrects_invoice_id,
        supply_type=payload.supply_type,
        place_of_supply=payload.place_of_supply,
        reverse_charge=payload.reverse_charge,
    )
    invoice = await services.create_invoice(session, ctx, create, request=request)
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
    return [selectors.to_filing_out(row) for row in selectors.visible_attempts(rows)]


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
    """Configuration, not a document — the one write here that overwrites rather than appends.

    A registration is configuration, so it is idempotent on the GSTIN and
    updates in place. What it must never do is rewrite history — an invoice already filed keeps
    the `seller_gstin` it was filed under, because that column is a snapshot on the invoice and
    not a join to here.
    """
    registration = await services.set_gst_registration(session, ctx, payload, request=request)
    return selectors.to_gst_registration_out(registration)


# --------------------------------------------------------------------- GST returns


@router.get(
    "/finance/gst/returns",
    response_model=Page[GstReturnOut],
    summary="Returns generated and filed, newest period first",
    dependencies=[Entitled],
)
async def list_returns(
    session: SessionDep,
    ctx: ReturnRead,
    params: PageQuery,
    period: Annotated[str | None, Query(description="Tax period, YYYY-MM")] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="draft|generated|filed")
    ] = None,
    return_type: Annotated[str | None, Query(description="gstr1|gstr7|gstr3b")] = None,
    sort: Annotated[str, Query(description="period_desc|period_asc")] = "period_desc",
) -> Page[GstReturnOut]:
    """The list page: one row per (registration, period, type), which is the grain the obligation
    itself has. The snapshot is not here — it is opened, not scrolled past."""
    stmt = selectors.returns_stmt(
        ctx, period=period, status=status_filter, return_type=return_type, sort=sort
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.to_return_out(row) for row in rows], total=total, params=params)


@router.get(
    "/finance/gst/returns/{return_id}",
    response_model=GstReturnDetail,
    summary="One return, with every section and its whole status trail",
    dependencies=[Entitled],
)
async def read_return(
    return_id: uuid.UUID, session: SessionDep, ctx: ReturnRead
) -> GstReturnDetail:
    """The return as it was assembled — the snapshot, not a re-derivation.

    Serving the stored sections rather than re-assembling on read is the whole point of freezing
    them: this is what was reported, and it must keep saying so after the book moves underneath
    it. What the book says *now* is a different question, answered by the diff endpoint.
    """
    ret = await selectors.get_return(session, ctx.organization_id, return_id)
    if ret is None:
        raise NotFoundProblem("That return is not part of this organization")
    return await selectors.return_detail(session, ctx.organization_id, ret)


@router.get(
    "/finance/gst/returns/{return_id}/diff",
    response_model=ReturnDiffOut,
    summary="What has changed in the book since this return was generated",
    dependencies=[Entitled],
)
async def read_return_diff(
    return_id: uuid.UUID, session: SessionDep, ctx: ReturnRead
) -> ReturnDiffOut:
    """The difference, per section and in total, between the snapshot and the book as it stands.

    A credit note raised in September against an August invoice does not make August a different
    return; it makes a difference, and a difference is what an amendment in a later period
    declares. This is that difference, so the amendment is prepared from an answer rather than
    from somebody's memory of what August looked like.
    """
    return await services.diff_return(session, ctx, return_id)


@router.post(
    "/finance/gst/returns",
    response_model=GstReturnOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assemble one period's return from the invoices already recorded",
    dependencies=[Entitled],
)
async def generate_return(
    payload: GenerateReturnIn, session: SessionDep, ctx: ReturnGenerate, request: Request
) -> GstReturnOut:
    """Generate the return and freeze the snapshot.

    A 201 whether or not the record already existed, because what is created either way is the
    snapshot — the return record is only the box it lives in. Refused outright on a **filed**
    period: the snapshot is what was reported, and regenerating over it would erase the only
    record of what the filing actually said.
    """
    ret = await services.generate_return(session, ctx, payload, request=request)
    return selectors.to_return_out(ret)


@router.post(
    "/finance/gst/returns/{return_id}/filing",
    response_model=GstReturnOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record that a generated return went to the portal, with its ARN",
    dependencies=[Entitled],
)
async def file_return(
    return_id: uuid.UUID,
    payload: FileReturnIn,
    session: SessionDep,
    ctx: ReturnFile,
    request: Request,
) -> GstReturnOut:
    """Mark the return filed. Behind its own dangerous permission, and terminal.

    A POST to a `filing` sub-collection rather than a PATCH of `status`, for the same reason IMS
    decisions are a collection: this appends a row to a trail that is never rewritten, and the
    verb should say so.
    """
    ret = await services.file_return(session, ctx, return_id, payload, request=request)
    return selectors.to_return_out(ret)


# --------------------------------------------------------------------- the ITC ledger
# Read-only: every posting is a side effect of an IMS decision (`accrual`/`reversal`) or a
# GSTR-3B filing (`utilisation`), each already behind its own dangerous permission.


@router.get(
    "/finance/gst/itc-ledger",
    response_model=Page[ItcLedgerEntryOut],
    summary="The input-tax-credit ledger — accrual, reversal and utilisation, newest first",
    dependencies=[Entitled],
)
async def list_itc_ledger(
    session: SessionDep,
    ctx: ItcRead,
    params: PageQuery,
    gst_registration_id: Annotated[
        uuid.UUID | None, Query(description="One registration's postings")
    ] = None,
    period: Annotated[str | None, Query(description="Tax period, YYYY-MM")] = None,
    tax_head: Annotated[str | None, Query(description="igst|cgst|sgst|cess")] = None,
    kind: Annotated[str | None, Query(description="accrual|reversal|utilisation")] = None,
) -> Page[ItcLedgerEntryOut]:
    stmt = selectors.itc_ledger_stmt(
        ctx,
        gst_registration_id=gst_registration_id,
        period=period,
        tax_head=tax_head,
        kind=kind,
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [selectors.to_itc_ledger_entry_out(row) for row in rows], total=total, params=params
    )


@router.get(
    "/finance/gst/itc-ledger/balance",
    response_model=ItcBalanceOut,
    summary="The ledger's position for one registration as of the end of one period",
    dependencies=[Entitled],
)
async def read_itc_balance(
    session: SessionDep,
    ctx: ItcRead,
    period: Annotated[str, Query(description="Tax period, YYYY-MM")],
    gst_registration_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ItcBalanceOut:
    """Brought-forward plus every posting made *in* `period`, per head — what a GSTR-3B for the
    *next* period would have on top of its own accrual and reversal to utilise. Defaults to the
    organization's primary registration, the same default `generate_return` uses."""
    registration = await services.registration_for(session, ctx, gst_registration_id)
    return await selectors.itc_balance(session, ctx.organization_id, registration.id, period)


# --------------------------------------------------------------------- e-Way bills


@router.get(
    "/finance/eway-bills",
    response_model=Page[EwayBillWithInvoice],
    summary="e-Way bill attempts, newest first",
    dependencies=[Entitled],
)
async def list_eway_bills(
    session: SessionDep,
    ctx: EwayRead,
    params: PageQuery,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description=(
                "pending|generated|failed|cancelled — filters the **stored** status. An expired "
                "bill is stored as `generated`; read `effective_status` for those."
            ),
        ),
    ] = None,
    invoice_id: Annotated[uuid.UUID | None, Query(description="One document's attempts")] = None,
) -> Page[EwayBillWithInvoice]:
    stmt = selectors.eway_bills_stmt(ctx, status=status_filter, invoice_id=invoice_id)
    rows, total = await paginate(session, stmt, params)
    items = await selectors.eway_bills_with_invoice(session, ctx.organization_id, rows)
    return Page.build(items, total=total, params=params)


@router.get(
    "/finance/invoices/{invoice_id}/eway-bills",
    response_model=list[EwayBillOut],
    summary="Every e-Way bill attempt for one document, newest first",
    dependencies=[Entitled],
)
async def list_eway_bills_for_invoice(
    invoice_id: uuid.UUID, session: SessionDep, ctx: EwayRead
) -> list[EwayBillOut]:
    """The attempt history, which is a plain query because the table is append-only.

    Behind its own permission rather than the invoice one: seeing the receivable book and seeing
    which consignments are legally on the road are different questions.
    """
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    rows = await selectors.eway_bills_for_invoice(session, ctx.organization_id, invoice_id)
    return [selectors.to_eway_bill_out(row) for row in selectors.visible_attempts(rows)]


@router.get(
    "/finance/invoices/{invoice_id}/eway",
    response_model=EwayState,
    summary="One document's current e-Way position",
    dependencies=[Entitled],
)
async def read_eway_state(invoice_id: uuid.UUID, session: SessionDep, ctx: EwayRead) -> EwayState:
    """`required` is answered even when nothing has been attempted, which is the point: an
    obligation nobody has met must be visible rather than absent."""
    invoice = await selectors.get_invoice(session, ctx.organization_id, invoice_id)
    if invoice is None:
        raise NotFoundProblem("That invoice is not part of this organization")
    latest, counts = await selectors.latest_eway_bills(session, ctx.organization_id, [invoice.id])
    return selectors.to_eway_state(invoice, latest.get(invoice.id), counts.get(invoice.id, 0))


# --------------------------------------------------------------------- withholding: TDS / TCS


@router.get(
    "/finance/gst/withholdings",
    response_model=Page[WithholdingWithInvoice],
    summary="The withholding register — TDS deducted and TCS collected, per document",
    dependencies=[Entitled],
)
async def list_withholdings(
    session: SessionDep,
    ctx: WithholdingRead,
    params: PageQuery,
    period: Annotated[str | None, Query(description="Tax period, YYYY-MM")] = None,
    code: Annotated[str | None, Query(description="gst_tds_51|itax_194q|itax_206c_1h")] = None,
    invoice_id: Annotated[uuid.UUID | None, Query(description="One document's rows")] = None,
) -> Page[WithholdingWithInvoice]:
    stmt = selectors.withholdings_stmt(ctx, period=period, code=code, invoice_id=invoice_id)
    rows, total = await paginate(session, stmt, params)
    items = await selectors.withholdings_with_invoice(session, ctx.organization_id, rows)
    return Page.build(items, total=total, params=params)


@router.get(
    "/finance/gst/withholdings/summary",
    response_model=WithholdingSummaryOut,
    summary="One period's withholding grouped per counterparty — the GSTR-7 shape",
    dependencies=[Entitled],
)
async def read_withholding_summary(
    session: SessionDep,
    ctx: WithholdingRead,
    period: Annotated[str, Query(description="Tax period, YYYY-MM")],
    code: Annotated[str | None, Query(description="gst_tds_51|itax_194q|itax_206c_1h")] = None,
) -> WithholdingSummaryOut:
    """Grouped per deductor, because that is how GSTR-7 is declared — which is what makes these
    figures comparable to the return the deductor actually files, and therefore reconcilable."""
    return await selectors.withholding_summary(session, ctx, period=period, code=code)


@router.get(
    "/finance/gst/withholding-rates",
    response_model=list[WithholdingRateOut],
    summary="The statutory withholding schedule, every version",
    dependencies=[Entitled],
)
async def list_withholding_rates(
    session: SessionDep, ctx: WithholdingRead
) -> list[WithholdingRateOut]:
    """Global vocabulary rather than tenant data — a rate under GST s. 51 is the law of one
    country. Published beside the register because a figure is only explicable next to the
    version of the rate that produced it."""
    return await selectors.withholding_rates_out(session)


# --------------------------------------------------------------------- IMS reconciliation


@router.get(
    "/finance/ims/documents",
    response_model=Page[ImsDocumentOut],
    summary="The IMS reconciliation queue — documents suppliers filed against this organization",
    dependencies=[Entitled],
)
async def list_ims_documents(
    session: SessionDep,
    ctx: ImsRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Supplier name, GSTIN or document number")] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="pending|accepted|rejected|pending_action"),
    ] = None,
    supplier_gstin: Annotated[str | None, Query(description="One supplier's documents")] = None,
    period: Annotated[str | None, Query(description="Return period, YYYY-MM")] = None,
    reason: Annotated[
        str | None,
        Query(
            description=(
                "matched|amount_mismatch|supplier_unknown|no_purchase_record|duplicate — "
                "filters on the newest suggestion"
            )
        ),
    ] = None,
    sort: Annotated[
        str, Query(description="date_asc|date_desc|amount_desc|amount_asc")
    ] = "date_asc",
) -> Page[ImsDocumentOut]:
    """The queue, filtered the way it is actually worked.

    Every filter answers a question somebody asks out loud — "what is still undecided", "August",
    "everything from this supplier", "show me only the mismatches" — and `reason` is the one that
    makes the queue tractable: it filters on the newest suggestion, so an operator can take the
    exact matches in one pass and give the disagreements the attention they need.

    Oldest first by default, because a queue is worked forward: the document closest to its return
    deadline matters more than the one that arrived most recently.
    """
    items, total = await selectors.list_ims_documents(
        session,
        ctx,
        params,
        q=q,
        status=status_filter,
        supplier_gstin=supplier_gstin,
        period=period,
        reason=reason,
        sort=sort,
    )
    return Page.build(items, total=total, params=params)


@router.get(
    "/finance/ims/documents/{document_id}",
    response_model=ImsDocumentDetail,
    summary="One inbound supplier document, with the evidence behind its suggestion",
    dependencies=[Entitled],
)
async def read_ims_document(
    document_id: uuid.UUID, session: SessionDep, ctx: ImsRead
) -> ImsDocumentDetail:
    """The document, its newest suggestion, what that suggestion was based on, and every decision
    recorded against it.

    The evidence is the whole point of the detail view. An operator asked to accept or reject
    somebody else's claim on this business needs to see the purchase document it was matched to,
    or the earlier copy it duplicates, or the absence of both — a verdict on its own is something
    they can only take on trust, and trust is not what a compliance queue is for.
    """
    document = await selectors.get_ims_document(session, ctx.organization_id, document_id)
    if document is None:
        raise NotFoundProblem("That supplier document is not part of this organization")
    return await selectors.ims_document_detail(session, ctx.organization_id, document)


@router.post(
    "/finance/ims/documents/{document_id}/decisions",
    response_model=ImsDecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Accept, reject or park one inbound supplier document",
    dependencies=[Entitled],
)
async def decide_ims_document(
    document_id: uuid.UUID,
    payload: ImsDecisionCreate,
    session: SessionDep,
    ctx: ImsDecide,
    request: Request,
) -> ImsDecisionOut:
    """Record what a person decided — append-only, with who and when.

    A POST rather than a PUT, and a `decisions` collection rather than a `status` field, because
    that is what this is: a decision is appended to a history, never written over the last one. A
    change of mind is a further row, and the row that was superseded stays visible with the
    correction after it — which is the same shape invoices and filings already have, and the
    reason GST's own model fits this platform without adaptation.

    Behind its own permission, marked dangerous: accepting claims the input tax credit on the
    document and rejecting declines it. Reading the queue and deciding it are different jobs.
    """
    decision = await services.record_ims_decision(
        session, ctx, document_id, payload, request=request
    )
    return selectors.to_ims_decision_out(decision)


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


@router.post(
    "/finance/payments",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment received and allocate it to invoices",
    dependencies=[Entitled],
)
async def record_payment(
    payload: PaymentCreate, session: SessionDep, ctx: PaymentRecord, request: Request
) -> PaymentOut:
    """Append-only: there is no edit, delete or "unapply" for a payment or an allocation.

    Settling an invoice in full moves its status to `paid` in the same transaction as this
    write — see `services.record_payment` for the rules an allocation is checked against
    (open, correct customer, no double allocation, never over the outstanding balance).
    """
    payment = await services.record_payment(session, ctx, payload, request=request)
    return await selectors.payment_out_for(session, ctx.organization_id, payment)


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


@router.get(
    "/finance/customers/{customer_id}",
    response_model=CustomerOut,
    summary="One customer",
    dependencies=[Entitled],
)
async def read_customer(
    customer_id: uuid.UUID, session: SessionDep, ctx: CustomerRead
) -> CustomerOut:
    customer = await selectors.get_customer(session, ctx.organization_id, customer_id)
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")
    return CustomerOut.model_validate(customer)


@router.post(
    "/finance/customers",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a customer",
    dependencies=[Entitled],
)
async def create_customer(
    payload: CustomerCreate, session: SessionDep, ctx: CustomerWrite, request: Request
) -> CustomerOut:
    customer = await services.create_customer(session, ctx, payload, request=request)
    return CustomerOut.model_validate(customer)


@router.patch(
    "/finance/customers/{customer_id}",
    response_model=CustomerOut,
    summary="Edit a customer",
    dependencies=[Entitled],
)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    session: SessionDep,
    ctx: CustomerWrite,
    request: Request,
) -> CustomerOut:
    """The only mutable billing entity: invoices snapshot `buyer_gstin` and never rewrite what
    they were raised with, so editing a customer's directory record cannot rewrite history."""
    customer = await selectors.get_customer(session, ctx.organization_id, customer_id)
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")
    updated = await services.update_customer(session, ctx, customer, payload, request=request)
    return CustomerOut.model_validate(updated)


@router.delete(
    "/finance/customers/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a customer",
    dependencies=[Entitled],
)
async def archive_customer(
    customer_id: uuid.UUID, session: SessionDep, ctx: CustomerWrite, request: Request
) -> None:
    """Soft delete. Refused with 409 while any invoice or payment still names this customer —
    see `services.archive_customer`."""
    # Locked before the reference-check, so it serializes against a concurrent invoice or
    # payment for the same customer instead of racing it: see `selectors.get_customer`.
    customer = await selectors.get_customer(
        session, ctx.organization_id, customer_id, for_update=True
    )
    if customer is None:
        raise NotFoundProblem("That customer is not part of this organization")
    await services.archive_customer(session, ctx, customer, request=request)


# --------------------------------------------------------------------- purchases


@router.get(
    "/finance/purchases",
    response_model=Page[PurchaseDocumentOut],
    summary="Purchase documents recorded against this organization",
    dependencies=[Entitled],
)
async def list_purchases(
    session: SessionDep,
    ctx: PurchaseRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Supplier name, GSTIN or document number")] = None,
) -> Page[PurchaseDocumentOut]:
    stmt = selectors.purchase_documents_stmt(ctx, q=q)
    rows, total = await paginate(session, stmt, params)
    items = [selectors.to_purchase_out(row) for row in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/finance/purchases",
    response_model=PurchaseDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record what a supplier billed this organization",
    dependencies=[Entitled],
)
async def record_purchase(
    payload: PurchaseDocumentCreate, session: SessionDep, ctx: PurchaseWrite, request: Request
) -> PurchaseDocumentOut:
    """No update or delete path (`models.py:451-452`): a supplier's own correction is recorded as
    their credit or debit note, a new document, never an edit to this one."""
    purchase = await services.record_purchase_document(session, ctx, payload, request=request)
    return selectors.to_purchase_out(purchase)


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
