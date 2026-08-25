"""Reads for finance. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds its
own query instead of coming through a selector is how an organization leaks, so routers never
write SQL.

Finance is an ORG-level product (see `app.scope`), so `ctx.apply` is a no-op at the only scope
finance grants — but it is still the single enforcement point, kept so a future narrower grant
cannot silently read the whole book.

These functions also *assemble* the read shapes (`schemas.*Out`) from the rows, because the
derived fields the SPA needs — an invoice's `amount_paid_minor`, a customer's receivable
position, the overview rollups — are reads, computed from the same `app.domain` rules the SPA's
`derive.ts` uses so the live screens and the fixtures agree to the paisa.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import domain
from app.models import (
    Customer,
    EInvoiceFiling,
    EwayBill,
    GstRegistration,
    GstReturn,
    GstReturnTransition,
    ImsDecision,
    ImsDocument,
    ImsReconciliation,
    Invoice,
    InvoiceWithholding,
    Payment,
    PaymentAllocation,
    PurchaseDocument,
)
from app.models import WithholdingRate as WithholdingRateRow
from app.schemas import (
    AgingBucket,
    B2csOut,
    CashflowPoint,
    CustomerOut,
    CustomerSummary,
    DocumentSeriesOut,
    EInvoiceFilingOut,
    EInvoiceState,
    EwayBillOut,
    EwayBillWithInvoice,
    EwayState,
    Gstr1Sections,
    GstRegistrationOut,
    GstReturnDetail,
    GstReturnOut,
    GstReturnTransitionOut,
    HsnSummaryOut,
    ImsDecisionOut,
    ImsDocumentDetail,
    ImsDocumentOut,
    ImsSuggestion,
    InvoiceLineOut,
    InvoiceOut,
    InvoiceWithholdingOut,
    LedgerAccount,
    LedgerReport,
    OverviewKpi,
    OverviewSummary,
    PaymentAllocationOut,
    PaymentOut,
    PurchaseDocumentOut,
    RateGroupOut,
    ReturnInvoiceOut,
    ReturnTotalsOut,
    WithholdingDeductorRow,
    WithholdingRateOut,
    WithholdingSummaryOut,
    WithholdingWithInvoice,
)
from platform_core.auth import ScopeContext
from platform_core.pagination import PageParams

# --------------------------------------------------------------------- invoices

_INVOICE_SORTS: dict[str, tuple[Any, ...]] = {
    "issued_desc": (Invoice.issued_date.desc(), Invoice.number.desc()),
    "issued_asc": (Invoice.issued_date.asc(), Invoice.number.asc()),
    "amount_desc": (Invoice.total_minor.desc(), Invoice.number.desc()),
    "amount_asc": (Invoice.total_minor.asc(), Invoice.number.asc()),
    "due_asc": (Invoice.due_date.asc(), Invoice.number.asc()),
}


def invoices_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    sort: str = "issued_desc",
) -> Select[Any]:
    """Scope-filtered, searchable, sortable invoice list. `ctx.apply` is the enforcement point."""
    stmt = (
        select(Invoice)
        .where(
            Invoice.organization_id == ctx.organization_id,
            Invoice.deleted_at.is_(None),
        )
        .options(selectinload(Invoice.lines))
    )
    stmt = ctx.apply(stmt, owner_col=Invoice.id)

    if q:
        needle = f"%{q.lower()}%"
        matching_customers = select(Customer.id).where(
            Customer.organization_id == ctx.organization_id,
            func.lower(Customer.name).like(needle),
        )
        stmt = stmt.where(
            or_(
                func.lower(Invoice.number).like(needle),
                Invoice.customer_id.in_(matching_customers),
            )
        )
    if status:
        stmt = stmt.where(Invoice.status == status)

    return stmt.order_by(*_INVOICE_SORTS.get(sort, _INVOICE_SORTS["issued_desc"]))


async def paid_by_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Cash applied per invoice — the sum of the payment allocations pointing at each id.

    Org-scoped, so the map never reflects another tenant's allocations. Returns 0 implicitly for
    an invoice nothing was allocated to (the caller uses `.get(id, 0)`)."""
    if not invoice_ids:
        return {}
    stmt = (
        select(
            PaymentAllocation.invoice_id,
            func.coalesce(func.sum(PaymentAllocation.amount_minor), 0),
        )
        .where(
            PaymentAllocation.organization_id == org_id,
            PaymentAllocation.deleted_at.is_(None),
            PaymentAllocation.invoice_id.in_(invoice_ids),
        )
        .group_by(PaymentAllocation.invoice_id)
    )
    return {inv_id: int(total) for inv_id, total in (await session.execute(stmt)).all()}


def to_einvoice_state(
    invoice: Invoice,
    latest: EInvoiceFiling | None,
    attempts: int = 0,
    *,
    at: datetime | None = None,
) -> EInvoiceState:
    """An invoice's current e-invoice position, from its newest filing attempt.

    `required` is answered from the document itself, not from the filings, so a document that has
    never been attempted still reports the obligation. A status of `None` alongside
    `required=True` is precisely the queue the auto-filing consumer drains.

    Since filing became automatic the interesting question moved: not "is there a button" but
    "has the platform given up, and if a correction is needed which one is still legal". Both are
    answered here, from the stored acknowledgement rather than from a clock read on the client —
    `cancellable_until` is arithmetic over `ack_date`, and `remedy` is that deadline compared to
    `at` exactly as `services.cancel_filing` compares it.
    """
    required = domain.requires_einvoice(
        supply_type=invoice.supply_type, document_type=invoice.document_type
    )
    if latest is None:
        return EInvoiceState(required=required, status=None, attempts=attempts)
    return EInvoiceState(
        required=required,
        status=latest.status,
        irn=latest.irn,
        ack_no=latest.ack_no,
        ack_date=latest.ack_date,
        error_code=latest.error_code,
        error=latest.error,
        attempted_at=latest.attempted_at,
        attempts=attempts,
        terminal=domain.is_terminal_filing(latest.status),
        cancellable_until=(
            domain.cancellation_deadline(latest.ack_date)
            if latest.status == "filed" and latest.ack_date is not None
            else None
        ),
        remedy=domain.remedy_for(status=latest.status, ack_date=latest.ack_date, at=at or _now()),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def to_invoice_out(
    invoice: Invoice,
    paid_minor: int,
    latest_filing: EInvoiceFiling | None = None,
    attempts: int = 0,
    *,
    at: datetime | None = None,
) -> InvoiceOut:
    """Serialise one invoice with its derived paid-so-far figure and filing position attached."""
    return InvoiceOut(
        id=invoice.id,
        number=invoice.number,
        document_type=invoice.document_type,
        customer_id=invoice.customer_id,
        issued_date=invoice.issued_date,
        due_date=invoice.due_date,
        status=invoice.status,
        currency=invoice.currency,
        lines=[InvoiceLineOut.model_validate(line) for line in invoice.lines],
        subtotal_minor=invoice.subtotal_minor,
        tax_rate_pct=float(invoice.tax_rate_pct),
        tax_minor=invoice.tax_minor,
        total_minor=invoice.total_minor,
        amount_paid_minor=paid_minor,
        notes=invoice.notes,
        corrects_invoice_id=invoice.corrects_invoice_id,
        place_of_supply=invoice.place_of_supply,
        supply_type=invoice.supply_type,
        reverse_charge=invoice.reverse_charge,
        seller_gstin=invoice.seller_gstin,
        buyer_gstin=invoice.buyer_gstin,
        cgst_minor=invoice.cgst_minor,
        sgst_minor=invoice.sgst_minor,
        igst_minor=invoice.igst_minor,
        einvoice=to_einvoice_state(invoice, latest_filing, attempts, at=at),
    )


async def get_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> Invoice | None:
    """Org-scoped fetch by id, with lines. Returns None on a tenant mismatch or a soft-deleted
    row — confirming an id exists in *some* organization is itself a disclosure."""
    stmt = (
        select(Invoice)
        .where(
            Invoice.id == invoice_id,
            Invoice.organization_id == org_id,
            Invoice.deleted_at.is_(None),
        )
        .options(selectinload(Invoice.lines))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_customer(
    session: AsyncSession, org_id: uuid.UUID, customer_id: uuid.UUID
) -> Customer | None:
    """Org-scoped customer fetch. None on a tenant mismatch or a soft-deleted row."""
    stmt = select(Customer).where(
        Customer.id == customer_id,
        Customer.organization_id == org_id,
        Customer.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def invoice_exists(session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    stmt = select(Invoice.id).where(
        Invoice.id == invoice_id,
        Invoice.organization_id == org_id,
        Invoice.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def number_exists(session: AsyncSession, org_id: uuid.UUID, number: str) -> bool:
    stmt = select(Invoice.id).where(Invoice.organization_id == org_id, Invoice.number == number)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def reference_exists(session: AsyncSession, org_id: uuid.UUID, reference: str) -> bool:
    stmt = select(Payment.id).where(
        Payment.organization_id == org_id, Payment.reference == reference
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def all_invoices(session: AsyncSession, org_id: uuid.UUID) -> list[Invoice]:
    """Every live invoice for the org, with lines — the base for the customer and overview
    rollups. Scoped like every read here."""
    stmt = (
        select(Invoice)
        .where(Invoice.organization_id == org_id, Invoice.deleted_at.is_(None))
        .options(selectinload(Invoice.lines))
        .order_by(Invoice.issued_date.desc(), Invoice.number.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------- e-invoicing


async def filings_for_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[EInvoiceFiling]:
    """Every recorded attempt for one document, newest first.

    This is the whole filing history and it is a plain query, which is the point of the table
    being append-only: nothing has to be reconstructed, because nothing was overwritten.
    """
    stmt = (
        select(EInvoiceFiling)
        .where(
            EInvoiceFiling.organization_id == org_id,
            EInvoiceFiling.invoice_id == invoice_id,
            EInvoiceFiling.deleted_at.is_(None),
        )
        .order_by(EInvoiceFiling.attempted_at.desc(), EInvoiceFiling.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def latest_filings(
    session: AsyncSession, org_id: uuid.UUID, invoice_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, EInvoiceFiling], dict[uuid.UUID, int]]:
    """The newest attempt per invoice, and how many attempts each has had.

    `DISTINCT ON` rather than a per-invoice query: a list page of invoices must not become a
    filing query per row. Org-scoped like every read here, so the map can never carry another
    tenant's attempts.
    """
    if not invoice_ids:
        return {}, {}

    latest_stmt = (
        select(EInvoiceFiling)
        .where(
            EInvoiceFiling.organization_id == org_id,
            EInvoiceFiling.invoice_id.in_(invoice_ids),
            EInvoiceFiling.deleted_at.is_(None),
        )
        .distinct(EInvoiceFiling.invoice_id)
        .order_by(
            EInvoiceFiling.invoice_id,
            EInvoiceFiling.attempted_at.desc(),
            EInvoiceFiling.created_at.desc(),
        )
    )
    latest = {row.invoice_id: row for row in (await session.execute(latest_stmt)).scalars().all()}

    count_stmt = (
        select(EInvoiceFiling.invoice_id, func.count())
        .where(
            EInvoiceFiling.organization_id == org_id,
            EInvoiceFiling.invoice_id.in_(invoice_ids),
            EInvoiceFiling.deleted_at.is_(None),
        )
        .group_by(EInvoiceFiling.invoice_id)
    )
    counts = {inv_id: int(n) for inv_id, n in (await session.execute(count_stmt)).all()}
    return latest, counts


async def invoices_with_filings(
    session: AsyncSession, org_id: uuid.UUID, invoices: list[Invoice]
) -> list[InvoiceOut]:
    """Serialise a page of invoices with their paid-so-far figures and filing positions.

    One round trip for the cash applied, two for the filings — never one per row.
    """
    ids = [inv.id for inv in invoices]
    paid = await paid_by_invoice(session, org_id, ids)
    latest, counts = await latest_filings(session, org_id, ids)
    return [
        to_invoice_out(inv, paid.get(inv.id, 0), latest.get(inv.id), counts.get(inv.id, 0))
        for inv in invoices
    ]


def to_filing_out(filing: EInvoiceFiling) -> EInvoiceFilingOut:
    return EInvoiceFilingOut.model_validate(filing)


# --------------------------------------------------------------------- GST registration


async def get_gst_registration(session: AsyncSession, org_id: uuid.UUID) -> GstRegistration | None:
    """This organization's primary GST registration — the seller side of every tax decision.

    None means the org has not configured one, which is a real state and not an error: an invoice
    can still be raised, it simply cannot be filed, and the filing attempt says so.
    """
    stmt = select(GstRegistration).where(
        GstRegistration.organization_id == org_id,
        GstRegistration.is_primary.is_(True),
        GstRegistration.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()


async def all_gst_registrations(session: AsyncSession, org_id: uuid.UUID) -> list[GstRegistration]:
    """Every live registration for the org. A company registers per state, so there may be
    several; exactly one is primary."""
    stmt = (
        select(GstRegistration)
        .where(
            GstRegistration.organization_id == org_id,
            GstRegistration.deleted_at.is_(None),
        )
        .order_by(GstRegistration.is_primary.desc(), GstRegistration.state_code)
    )
    return list((await session.execute(stmt)).scalars().all())


async def gst_registration_by_gstin(
    session: AsyncSession, org_id: uuid.UUID, gstin: str
) -> GstRegistration | None:
    stmt = select(GstRegistration).where(
        GstRegistration.organization_id == org_id,
        GstRegistration.gstin == gstin,
        GstRegistration.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def to_gst_registration_out(registration: GstRegistration) -> GstRegistrationOut:
    return GstRegistrationOut(
        id=registration.id,
        gstin=registration.gstin,
        state_code=registration.state_code,
        state_name=domain.GST_STATE_CODES.get(registration.state_code, ""),
        legal_name=registration.legal_name,
        trade_name=registration.trade_name,
        registration_type=registration.registration_type,
        is_primary=registration.is_primary,
    )


# --------------------------------------------------------------------- purchases


async def all_purchase_documents(
    session: AsyncSession, org_id: uuid.UUID
) -> list[PurchaseDocument]:
    """Every live purchase document for the org — the book a reconciliation matches against.

    Read whole rather than per supplier: a reconciliation pass compares every inbound document
    against every recorded one, and doing that with a query per row is the shape that works on a
    demo and stops working on a real book.
    """
    stmt = (
        select(PurchaseDocument)
        .where(PurchaseDocument.organization_id == org_id, PurchaseDocument.deleted_at.is_(None))
        .order_by(PurchaseDocument.document_date.desc(), PurchaseDocument.number)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_purchase_document(
    session: AsyncSession, org_id: uuid.UUID, purchase_id: uuid.UUID
) -> PurchaseDocument | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row."""
    stmt = select(PurchaseDocument).where(
        PurchaseDocument.id == purchase_id,
        PurchaseDocument.organization_id == org_id,
        PurchaseDocument.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def purchase_exists(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    supplier_gstin: str,
    document_type: str,
    number: str,
) -> bool:
    stmt = select(PurchaseDocument.id).where(
        PurchaseDocument.organization_id == org_id,
        PurchaseDocument.supplier_gstin == supplier_gstin,
        PurchaseDocument.document_type == document_type,
        PurchaseDocument.number == number,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


def to_purchase_out(purchase: PurchaseDocument) -> PurchaseDocumentOut:
    return PurchaseDocumentOut(
        id=purchase.id,
        supplier_gstin=purchase.supplier_gstin,
        supplier_name=purchase.supplier_name,
        number=purchase.number,
        document_type=purchase.document_type,
        document_date=purchase.document_date,
        currency=purchase.currency,
        taxable_minor=purchase.taxable_minor,
        tax_rate_pct=float(purchase.tax_rate_pct),
        cgst_minor=purchase.cgst_minor,
        sgst_minor=purchase.sgst_minor,
        igst_minor=purchase.igst_minor,
        total_minor=purchase.total_minor,
        notes=purchase.notes,
    )


# --------------------------------------------------------------------- IMS reconciliation

_IMS_SORTS: dict[str, tuple[Any, ...]] = {
    # Oldest first is the default because a queue is worked forward: the document closest to its
    # return deadline is the one that matters, not the one that arrived most recently.
    "date_asc": (ImsDocument.document_date.asc(), ImsDocument.source_ref.asc()),
    "date_desc": (ImsDocument.document_date.desc(), ImsDocument.source_ref.desc()),
    "amount_desc": (ImsDocument.total_minor.desc(), ImsDocument.source_ref.asc()),
    "amount_asc": (ImsDocument.total_minor.asc(), ImsDocument.source_ref.asc()),
}


def _newest_suggestion_subquery(org_id: uuid.UUID) -> Any:
    """One row per document: its newest suggestion. `DISTINCT ON`, so filtering the queue by
    reason stays a single query rather than a suggestion lookup per row."""
    return (
        select(ImsReconciliation.ims_document_id.label("document_id"), ImsReconciliation.reason)
        .where(
            ImsReconciliation.organization_id == org_id,
            ImsReconciliation.deleted_at.is_(None),
        )
        .distinct(ImsReconciliation.ims_document_id)
        .order_by(
            ImsReconciliation.ims_document_id,
            ImsReconciliation.computed_at.desc(),
            ImsReconciliation.created_at.desc(),
        )
        .subquery()
    )


def ims_documents_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    supplier_gstin: str | None = None,
    period: str | None = None,
    reason: str | None = None,
    q: str | None = None,
    sort: str = "date_asc",
) -> Select[Any]:
    """The reconciliation queue, filtered the way it is actually worked.

    Every filter here answers a question somebody has out loud: "what is still undecided",
    "everything from this supplier", "August", "show me only the mismatches". `ctx.apply` is the
    enforcement point, and the explicit `organization_id` predicate stays beside it — RLS is the
    net under the tightrope, not permission to walk without one.
    """
    stmt = select(ImsDocument).where(
        ImsDocument.organization_id == ctx.organization_id,
        ImsDocument.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ImsDocument.id)

    if status:
        stmt = stmt.where(ImsDocument.status == status)
    if supplier_gstin:
        stmt = stmt.where(ImsDocument.supplier_gstin == domain.normalise_gstin(supplier_gstin))
    if period:
        stmt = stmt.where(ImsDocument.period == period)
    if reason:
        newest = _newest_suggestion_subquery(ctx.organization_id)
        stmt = stmt.where(
            ImsDocument.id.in_(select(newest.c.document_id).where(newest.c.reason == reason))
        )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(ImsDocument.supplier_name).like(needle),
                func.lower(ImsDocument.document_number).like(needle),
                func.lower(ImsDocument.supplier_gstin).like(needle),
            )
        )

    return stmt.order_by(*_IMS_SORTS.get(sort, _IMS_SORTS["date_asc"]))


async def get_ims_document(
    session: AsyncSession, org_id: uuid.UUID, document_id: uuid.UUID
) -> ImsDocument | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row — confirming an id
    exists in *some* organization is itself a disclosure."""
    stmt = select(ImsDocument).where(
        ImsDocument.id == document_id,
        ImsDocument.organization_id == org_id,
        ImsDocument.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_ims_documents(
    session: AsyncSession, org_id: uuid.UUID, *, period: str | None = None
) -> list[ImsDocument]:
    """Every live inbound document for the org, oldest arrival first.

    The reconciliation pass reads them all whatever `period` it was asked for, because the
    duplicate check spans periods: a supplier re-filing December's invoice in January is exactly
    the case a period-limited read would call two distinct documents.
    """
    stmt = (
        select(ImsDocument)
        .where(ImsDocument.organization_id == org_id, ImsDocument.deleted_at.is_(None))
        .order_by(ImsDocument.fetched_at.asc(), ImsDocument.source_ref.asc())
    )
    if period:
        stmt = stmt.where(ImsDocument.period == period)
    return list((await session.execute(stmt)).scalars().all())


async def ims_documents_by_source_ref(
    session: AsyncSession, org_id: uuid.UUID, source_refs: list[str]
) -> dict[str, ImsDocument]:
    """The documents already held for these portal references — the ingest's idempotency read.

    One round trip for a whole fetch, so re-syncing a period is a single lookup rather than one
    per document.
    """
    if not source_refs:
        return {}
    stmt = select(ImsDocument).where(
        ImsDocument.organization_id == org_id,
        ImsDocument.source_ref.in_(source_refs),
    )
    return {row.source_ref: row for row in (await session.execute(stmt)).scalars().all()}


async def latest_reconciliations(
    session: AsyncSession, org_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ImsReconciliation]:
    """The newest suggestion per document. `DISTINCT ON`, never a query per row."""
    if not document_ids:
        return {}
    stmt = (
        select(ImsReconciliation)
        .where(
            ImsReconciliation.organization_id == org_id,
            ImsReconciliation.ims_document_id.in_(document_ids),
            ImsReconciliation.deleted_at.is_(None),
        )
        .distinct(ImsReconciliation.ims_document_id)
        .order_by(
            ImsReconciliation.ims_document_id,
            ImsReconciliation.computed_at.desc(),
            ImsReconciliation.created_at.desc(),
        )
    )
    return {row.ims_document_id: row for row in (await session.execute(stmt)).scalars().all()}


async def latest_decisions(
    session: AsyncSession, org_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ImsDecision]:
    """The newest decision per document, which is the one the status projects."""
    if not document_ids:
        return {}
    stmt = (
        select(ImsDecision)
        .where(
            ImsDecision.organization_id == org_id,
            ImsDecision.ims_document_id.in_(document_ids),
            ImsDecision.deleted_at.is_(None),
        )
        .distinct(ImsDecision.ims_document_id)
        .order_by(
            ImsDecision.ims_document_id,
            ImsDecision.decided_at.desc(),
            ImsDecision.created_at.desc(),
        )
    )
    return {row.ims_document_id: row for row in (await session.execute(stmt)).scalars().all()}


async def decisions_for_document(
    session: AsyncSession, org_id: uuid.UUID, document_id: uuid.UUID
) -> list[ImsDecision]:
    """Every decision recorded against one document, newest first.

    A plain query, which is the point of the table being append-only: nothing has to be
    reconstructed, because nothing was overwritten.
    """
    stmt = (
        select(ImsDecision)
        .where(
            ImsDecision.organization_id == org_id,
            ImsDecision.ims_document_id == document_id,
            ImsDecision.deleted_at.is_(None),
        )
        .order_by(ImsDecision.decided_at.desc(), ImsDecision.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


def to_suggestion(row: ImsReconciliation) -> ImsSuggestion:
    return ImsSuggestion.model_validate(row)


def to_ims_decision_out(row: ImsDecision) -> ImsDecisionOut:
    return ImsDecisionOut.model_validate(row)


def to_ims_document_out(
    document: ImsDocument,
    suggestion: ImsReconciliation | None = None,
    decision: ImsDecision | None = None,
) -> ImsDocumentOut:
    """Serialise one queue row with its newest suggestion and newest decision attached."""
    return ImsDocumentOut(
        id=document.id,
        source_ref=document.source_ref,
        source=document.source,
        supplier_gstin=document.supplier_gstin,
        supplier_name=document.supplier_name,
        document_number=document.document_number,
        document_type=document.document_type,
        document_date=document.document_date,
        period=document.period,
        taxable_minor=document.taxable_minor,
        cgst_minor=document.cgst_minor,
        sgst_minor=document.sgst_minor,
        igst_minor=document.igst_minor,
        total_minor=document.total_minor,
        irn=document.irn,
        status=document.status,
        fetched_at=document.fetched_at,
        suggestion=to_suggestion(suggestion) if suggestion is not None else None,
        decision=to_ims_decision_out(decision) if decision is not None else None,
    )


async def ims_documents_with_context(
    session: AsyncSession, org_id: uuid.UUID, documents: list[ImsDocument]
) -> list[ImsDocumentOut]:
    """Serialise a page of the queue. Two round trips for the whole page, never one per row."""
    ids = [d.id for d in documents]
    suggestions = await latest_reconciliations(session, org_id, ids)
    decisions = await latest_decisions(session, org_id, ids)
    return [to_ims_document_out(d, suggestions.get(d.id), decisions.get(d.id)) for d in documents]


async def list_ims_documents(
    session: AsyncSession,
    ctx: ScopeContext,
    params: PageParams,
    **filters: Any,
) -> tuple[list[ImsDocumentOut], int]:
    """One page of the reconciliation queue, with each row's suggestion and decision."""
    stmt = ims_documents_stmt(ctx, **filters)
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = list(
        (await session.execute(stmt.offset(params.offset).limit(params.size))).scalars().all()
    )
    items = await ims_documents_with_context(session, ctx.organization_id, rows)
    return items, int(total)


async def ims_document_detail(
    session: AsyncSession, org_id: uuid.UUID, document: ImsDocument
) -> ImsDocumentDetail:
    """One inbound document with the evidence behind its suggestion.

    The evidence is the point: an operator asked to accept or reject somebody else's claim on this
    business needs to see the purchase document it was matched to, or the earlier copy it
    duplicates, or the absence of both — not a verdict they have to take on trust.
    """
    suggestions = await latest_reconciliations(session, org_id, [document.id])
    suggestion = suggestions.get(document.id)
    decisions = await decisions_for_document(session, org_id, document.id)

    matched: PurchaseDocument | None = None
    duplicate: ImsDocument | None = None
    if suggestion is not None:
        if suggestion.matched_purchase_id is not None:
            matched = await get_purchase_document(session, org_id, suggestion.matched_purchase_id)
        if suggestion.duplicate_of_id is not None:
            duplicate = await get_ims_document(session, org_id, suggestion.duplicate_of_id)

    return ImsDocumentDetail(
        document=to_ims_document_out(document, suggestion, decisions[0] if decisions else None),
        matched_purchase=to_purchase_out(matched) if matched is not None else None,
        duplicate_of=to_ims_document_out(duplicate) if duplicate is not None else None,
        decisions=[to_ims_decision_out(row) for row in decisions],
    )


# --------------------------------------------------------------------- payments


async def list_payments(
    session: AsyncSession, ctx: ScopeContext, params: PageParams
) -> tuple[list[PaymentOut], int]:
    """Payments received, newest first, with their allocations resolved to invoice numbers."""
    stmt = (
        select(Payment)
        .where(
            Payment.organization_id == ctx.organization_id,
            Payment.deleted_at.is_(None),
        )
        .options(selectinload(Payment.allocations))
        .order_by(Payment.received_date.desc(), Payment.reference.desc())
    )
    stmt = ctx.apply(stmt, owner_col=Payment.id)

    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = list(
        (await session.execute(stmt.offset(params.offset).limit(params.size))).scalars().all()
    )

    invoice_ids = {alloc.invoice_id for p in rows for alloc in p.allocations}
    numbers = await _invoice_numbers(session, ctx.organization_id, invoice_ids)

    items = [
        PaymentOut(
            id=p.id,
            reference=p.reference,
            customer_id=p.customer_id,
            received_date=p.received_date,
            method=p.method,
            amount_minor=p.amount_minor,
            allocations=[
                PaymentAllocationOut(
                    invoice_id=alloc.invoice_id,
                    invoice_number=numbers.get(alloc.invoice_id, ""),
                    amount_minor=alloc.amount_minor,
                )
                for alloc in p.allocations
            ],
            note=p.note,
        )
        for p in rows
    ]
    return items, int(total)


async def _invoice_numbers(
    session: AsyncSession, org_id: uuid.UUID, invoice_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not invoice_ids:
        return {}
    stmt = select(Invoice.id, Invoice.number).where(
        Invoice.organization_id == org_id,
        Invoice.id.in_(invoice_ids),
    )
    return dict((await session.execute(stmt)).tuples().all())


# --------------------------------------------------------------------- customers


async def list_customer_summaries(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    q: str | None = None,
    sort: str = "receivables_desc",
) -> list[CustomerSummary]:
    """Each customer rolled up into a receivable position — the customers ledger.

    Mirrors the SPA's `buildCustomerSummaries`: invoice_count is every invoice document (not
    credit notes), receivables/overdue come from the *open* ones via the shared `app.domain`
    rules, and standing follows from the two figures.
    """
    org_id = ctx.organization_id
    customers_stmt = select(Customer).where(
        Customer.organization_id == org_id, Customer.deleted_at.is_(None)
    )
    customers_stmt = ctx.apply(customers_stmt, owner_col=Customer.id)
    if q:
        needle = f"%{q.lower()}%"
        customers_stmt = customers_stmt.where(
            or_(func.lower(Customer.name).like(needle), func.lower(Customer.city).like(needle))
        )
    customers = list((await session.execute(customers_stmt)).scalars().all())

    invoices = await all_invoices(session, org_id)
    paid = await paid_by_invoice(session, org_id, [inv.id for inv in invoices])

    by_customer: dict[uuid.UUID, list[Invoice]] = {}
    for inv in invoices:
        if inv.document_type == "invoice":
            by_customer.setdefault(inv.customer_id, []).append(inv)

    summaries: list[CustomerSummary] = []
    for customer in customers:
        rows = by_customer.get(customer.id, [])
        facts = [
            domain.InvoiceFacts(
                document_type=inv.document_type,
                status=inv.status,
                issued_date=inv.issued_date,
                due_date=inv.due_date,
                total_minor=inv.total_minor,
                amount_paid_minor=paid.get(inv.id, 0),
            )
            for inv in rows
        ]
        receivables = domain.total_outstanding(facts)
        overdue = domain.total_overdue(facts)
        last = max((inv.issued_date for inv in rows), default=None)
        summaries.append(
            CustomerSummary(
                customer=CustomerOut.model_validate(customer),
                receivables_minor=receivables,
                overdue_minor=overdue,
                invoice_count=len(rows),
                last_invoice_date=last,
                standing=domain.customer_standing(receivables, overdue),
            )
        )

    if sort == "name_asc":
        summaries.sort(key=lambda s: s.customer.name.lower())
    elif sort == "overdue_desc":
        summaries.sort(key=lambda s: s.overdue_minor, reverse=True)
    else:  # receivables_desc
        summaries.sort(key=lambda s: s.receivables_minor, reverse=True)
    return summaries


# --------------------------------------------------------------------- overview & ledger


async def get_overview(session: AsyncSession, ctx: ScopeContext) -> OverviewSummary:
    """The whole Overview payload: derived KPIs + representative trends, AR aging, attention."""
    org_id = ctx.organization_id
    invoices = await all_invoices(session, org_id)
    paid = await paid_by_invoice(session, org_id, [inv.id for inv in invoices])

    facts = [
        domain.InvoiceFacts(
            document_type=inv.document_type,
            status=inv.status,
            issued_date=inv.issued_date,
            due_date=inv.due_date,
            total_minor=inv.total_minor,
            amount_paid_minor=paid.get(inv.id, 0),
        )
        for inv in invoices
    ]

    revenue = domain.net_revenue_for_month(facts, domain.PERIOD_MONTH)
    receivables = domain.total_outstanding(facts)
    overdue = domain.total_overdue(facts)

    def kpi(key: str, label: str, value: int, caption: str) -> OverviewKpi:
        trend = domain.KPI_TRENDS[key]
        return OverviewKpi(
            key=key,
            label=label,
            value_minor=value,
            deltaPct=trend.delta_pct,
            series=list(trend.series),
            caption=caption,
        )

    kpis = [
        kpi("revenue", "Revenue this period", revenue, "Net of credit notes"),
        kpi("receivables", "Outstanding receivables", receivables, "Across all open invoices"),
        kpi("overdue", "Overdue", overdue, "Past the due date"),
        kpi("cash", "Cash balance", domain.CASH_BALANCE_MINOR, "Bank + cash on hand"),
    ]

    aging = [
        AgingBucket(key=b.key, label=b.label, amount_minor=b.amount_minor, count=b.count)
        for b in domain.aging_buckets(facts, domain.REFERENCE_DATE)
    ]

    # The "needs attention" queue: open + overdue invoices, largest outstanding first.
    attention_rows = sorted(
        (
            inv
            for inv, f in zip(invoices, facts, strict=True)
            if domain.is_open_invoice(f) and f.status == "overdue"
        ),
        key=lambda inv: domain.outstanding_minor(
            domain.InvoiceFacts(
                inv.document_type,
                inv.status,
                inv.issued_date,
                inv.due_date,
                inv.total_minor,
                paid.get(inv.id, 0),
            )
        ),
        reverse=True,
    )
    latest, counts = await latest_filings(session, org_id, [inv.id for inv in attention_rows])
    attention = [
        to_invoice_out(inv, paid.get(inv.id, 0), latest.get(inv.id), counts.get(inv.id, 0))
        for inv in attention_rows
    ]

    return OverviewSummary(
        period_label=domain.PERIOD_LABEL,
        kpis=kpis,
        cashflow=[
            CashflowPoint(
                label=row.label, inflow_minor=row.inflow_minor, outflow_minor=row.outflow_minor
            )
            for row in domain.CASHFLOW
        ],
        aging=aging,
        attention=attention,
    )


def get_ledger() -> LedgerReport:
    """The trial-balance report. Representative booked figures (see `app.domain`) pending a GL."""
    return LedgerReport(
        period_label=domain.LEDGER_PERIOD_LABEL,
        as_of=domain.REFERENCE_DATE,
        accounts=[
            LedgerAccount(
                code=row.code,
                name=row.name,
                type=row.type,
                debit_minor=row.debit_minor,
                credit_minor=row.credit_minor,
            )
            for row in domain.LEDGER_ACCOUNTS
        ],
    )


# --------------------------------------------------------------------- GST returns


_RETURN_SORTS: dict[str, tuple[Any, ...]] = {
    "period_desc": (GstReturn.period.desc(), GstReturn.return_type.asc()),
    "period_asc": (GstReturn.period.asc(), GstReturn.return_type.asc()),
}


def returns_stmt(
    ctx: ScopeContext,
    *,
    period: str | None = None,
    status: str | None = None,
    return_type: str | None = None,
    sort: str = "period_desc",
) -> Select[Any]:
    """Scope-filtered return list. `ctx.apply` is the enforcement point, as everywhere here."""
    stmt = select(GstReturn).where(
        GstReturn.organization_id == ctx.organization_id,
        GstReturn.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=GstReturn.id)
    if period:
        stmt = stmt.where(GstReturn.period == period)
    if status:
        stmt = stmt.where(GstReturn.status == status)
    if return_type:
        stmt = stmt.where(GstReturn.return_type == return_type)
    return stmt.order_by(*_RETURN_SORTS.get(sort, _RETURN_SORTS["period_desc"]))


async def get_return(
    session: AsyncSession, org_id: uuid.UUID, return_id: uuid.UUID
) -> GstReturn | None:
    """Org-scoped fetch by id. None on a tenant mismatch — confirming an id exists in *some*
    organization is itself a disclosure."""
    stmt = select(GstReturn).where(
        GstReturn.id == return_id,
        GstReturn.organization_id == org_id,
        GstReturn.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_return(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    registration_id: uuid.UUID,
    period: str,
    return_type: str,
) -> GstReturn | None:
    """The return for one (registration, period, type) — the grain the obligation itself has."""
    stmt = select(GstReturn).where(
        GstReturn.organization_id == org_id,
        GstReturn.gst_registration_id == registration_id,
        GstReturn.period == period,
        GstReturn.return_type == return_type,
        GstReturn.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def return_transitions(
    session: AsyncSession, org_id: uuid.UUID, return_id: uuid.UUID
) -> list[GstReturnTransition]:
    """The whole status trail, oldest first — a plain query, because nothing was overwritten."""
    stmt = (
        select(GstReturnTransition)
        .where(
            GstReturnTransition.organization_id == org_id,
            GstReturnTransition.gst_return_id == return_id,
            GstReturnTransition.deleted_at.is_(None),
        )
        .order_by(GstReturnTransition.transitioned_at.asc(), GstReturnTransition.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def documents_for_period(
    session: AsyncSession, org_id: uuid.UUID, period: str
) -> list[Invoice]:
    """Every document this organization issued in one tax period, with its lines.

    Voids included, and deliberately: their figures are not a supply but their **numbers** are
    declared in the document-series section, and a series with a hole in it is how an unreported
    invoice announces itself. `assemble_gstr1` drops them from every other section.

    Drafts are not included at all — a draft was never issued to anybody, so no number was
    consumed and there is nothing to declare.
    """
    year, month = domain.parse_period(period)
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    stmt = (
        select(Invoice)
        .where(
            Invoice.organization_id == org_id,
            Invoice.deleted_at.is_(None),
            Invoice.status != "draft",
            Invoice.issued_date >= start,
            Invoice.issued_date < end,
        )
        .options(selectinload(Invoice.lines))
        .order_by(Invoice.number.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def return_documents(
    session: AsyncSession, org_id: uuid.UUID, period: str
) -> list[domain.ReturnDocument]:
    """The period's documents, reduced to the pure shape `domain.assemble_gstr1` reasons about.

    The counterparty name comes from the customer row and the corrected document's *number* from
    the invoice it points at — both resolved here, in bulk, because the assembly below this is
    pure and must not reach for a session.
    """
    invoices = await documents_for_period(session, org_id, period)
    if not invoices:
        return []

    names = await _customer_names(session, org_id, {inv.customer_id for inv in invoices})
    corrected = await _invoice_numbers(
        session, org_id, {inv.corrects_invoice_id for inv in invoices if inv.corrects_invoice_id}
    )
    return [
        domain.ReturnDocument(
            id=inv.id,
            number=inv.number,
            document_type=inv.document_type,
            issued_date=inv.issued_date,
            supply_type=inv.supply_type,
            place_of_supply=inv.place_of_supply,
            reverse_charge=inv.reverse_charge,
            buyer_gstin=inv.buyer_gstin,
            buyer_name=names.get(inv.customer_id, ""),
            taxable_minor=inv.subtotal_minor,
            total_minor=inv.total_minor,
            status=inv.status,
            corrects_number=(
                corrected.get(inv.corrects_invoice_id) if inv.corrects_invoice_id else None
            ),
            lines=tuple(
                domain.ReturnLine(
                    hsn_sac=line.hsn_sac,
                    description=line.description,
                    quantity=line.quantity,
                    rate_pct=line.tax_rate_pct,
                    taxable_minor=line.amount_minor,
                    cgst_minor=line.cgst_minor,
                    sgst_minor=line.sgst_minor,
                    igst_minor=line.igst_minor,
                )
                for line in inv.lines
            ),
        )
        for inv in invoices
    ]


async def _customer_names(
    session: AsyncSession, org_id: uuid.UUID, customer_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Names for a page of customer ids, in one round trip rather than one per document."""
    if not customer_ids:
        return {}
    stmt = select(Customer.id, Customer.name).where(
        Customer.organization_id == org_id, Customer.id.in_(customer_ids)
    )
    return dict((await session.execute(stmt)).tuples().all())


def to_rate_group_out(group: domain.RateGroup) -> RateGroupOut:
    return RateGroupOut(
        rate_pct=float(group.rate_pct),
        taxable_minor=group.taxable_minor,
        cgst_minor=group.cgst_minor,
        sgst_minor=group.sgst_minor,
        igst_minor=group.igst_minor,
        total_minor=group.total_minor,
    )


def to_return_invoice_out(entry: domain.ReturnInvoiceEntry) -> ReturnInvoiceOut:
    return ReturnInvoiceOut(
        document_id=entry.document_id,
        number=entry.number,
        document_type=entry.document_type,
        issued_date=entry.issued_date,
        place_of_supply=entry.place_of_supply,
        counterparty_gstin=entry.counterparty_gstin,
        counterparty_name=entry.counterparty_name,
        reverse_charge=entry.reverse_charge,
        invoice_value_minor=entry.invoice_value_minor,
        corrects_number=entry.corrects_number,
        items=[to_rate_group_out(item) for item in entry.items],
    )


def to_totals_out(totals: domain.ReturnTotals) -> ReturnTotalsOut:
    return ReturnTotalsOut(
        taxable_minor=totals.taxable_minor,
        cgst_minor=totals.cgst_minor,
        sgst_minor=totals.sgst_minor,
        igst_minor=totals.igst_minor,
        tax_minor=totals.tax_minor,
        total_minor=totals.total_minor,
        document_count=totals.document_count,
        line_count=totals.line_count,
    )


def to_sections(ret: domain.Gstr1Return) -> Gstr1Sections:
    """The assembled return, on the wire — and the exact shape the snapshot is stored in."""
    return Gstr1Sections(
        b2b=[to_return_invoice_out(e) for e in ret.b2b],
        b2cl=[to_return_invoice_out(e) for e in ret.b2cl],
        b2cs=[
            B2csOut(
                place_of_supply=e.place_of_supply,
                rate_pct=float(e.rate_pct),
                inter_state=e.inter_state,
                taxable_minor=e.taxable_minor,
                cgst_minor=e.cgst_minor,
                sgst_minor=e.sgst_minor,
                igst_minor=e.igst_minor,
                total_minor=e.total_minor,
                document_count=e.document_count,
            )
            for e in ret.b2cs
        ],
        cdnr=[to_return_invoice_out(e) for e in ret.cdnr],
        cdnur=[to_return_invoice_out(e) for e in ret.cdnur],
        exp=[to_return_invoice_out(e) for e in ret.exp],
        hsn=[
            HsnSummaryOut(
                hsn_sac=e.hsn_sac,
                rate_pct=float(e.rate_pct),
                quantity=float(e.quantity),
                taxable_minor=e.taxable_minor,
                cgst_minor=e.cgst_minor,
                sgst_minor=e.sgst_minor,
                igst_minor=e.igst_minor,
                total_minor=e.total_minor,
            )
            for e in ret.hsn
        ],
        docs=[
            DocumentSeriesOut(
                document_type=e.document_type,
                from_number=e.from_number,
                to_number=e.to_number,
                total_count=e.total_count,
                cancelled_count=e.cancelled_count,
                net_count=e.net_count,
            )
            for e in ret.docs
        ],
    )


def to_return_out(ret: GstReturn) -> GstReturnOut:
    return GstReturnOut.model_validate(ret)


def to_return_transition_out(row: GstReturnTransition) -> GstReturnTransitionOut:
    return GstReturnTransitionOut.model_validate(row)


async def return_detail(
    session: AsyncSession, org_id: uuid.UUID, ret: GstReturn
) -> GstReturnDetail:
    """The return, its snapshot and its trail.

    The snapshot is re-validated through `Gstr1Sections` on the way out rather than handed back
    as raw JSON: a snapshot written by an older revision of this service must either satisfy the
    contract it is served under or fail loudly here, not reach a screen as a shape nothing
    describes.
    """
    transitions = await return_transitions(session, org_id, ret.id)
    return GstReturnDetail(
        ret=to_return_out(ret),
        sections=Gstr1Sections.model_validate(ret.sections) if ret.sections else None,
        transitions=[to_return_transition_out(t) for t in transitions],
    )


# --------------------------------------------------------------------- e-Way bills


async def eway_bills_for_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[EwayBill]:
    """Every recorded attempt for one document, newest first."""
    stmt = (
        select(EwayBill)
        .where(
            EwayBill.organization_id == org_id,
            EwayBill.invoice_id == invoice_id,
            EwayBill.deleted_at.is_(None),
        )
        .order_by(EwayBill.attempted_at.desc(), EwayBill.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def latest_eway_bills(
    session: AsyncSession, org_id: uuid.UUID, invoice_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, EwayBill], dict[uuid.UUID, int]]:
    """The newest attempt per document, and how many each has had. `DISTINCT ON`, never a query
    per row — a list page of documents must not become an e-Way query per document."""
    if not invoice_ids:
        return {}, {}
    latest_stmt = (
        select(EwayBill)
        .where(
            EwayBill.organization_id == org_id,
            EwayBill.invoice_id.in_(invoice_ids),
            EwayBill.deleted_at.is_(None),
        )
        .distinct(EwayBill.invoice_id)
        .order_by(EwayBill.invoice_id, EwayBill.attempted_at.desc(), EwayBill.created_at.desc())
    )
    latest = {row.invoice_id: row for row in (await session.execute(latest_stmt)).scalars().all()}
    count_stmt = (
        select(EwayBill.invoice_id, func.count())
        .where(
            EwayBill.organization_id == org_id,
            EwayBill.invoice_id.in_(invoice_ids),
            EwayBill.deleted_at.is_(None),
        )
        .group_by(EwayBill.invoice_id)
    )
    counts = {inv_id: int(n) for inv_id, n in (await session.execute(count_stmt)).all()}
    return latest, counts


def live_eway_bill(bills: list[EwayBill], *, at: datetime | None = None) -> EwayBill | None:
    """The bill that currently stands for a document: its newest `generated` attempt, if it has
    not been cancelled since and has not expired.

    `bills` is newest-first, as `eway_bills_for_invoice` returns it, so the first `generated` row
    is the standing one and a `cancelled` row ahead of it means there is none. Expiry is checked
    against the stored `valid_until` rather than assumed, because a bill that ran out is not a
    bill you may cancel or move goods under.
    """
    now = at or _now()
    for bill in bills:
        if bill.status == "cancelled":
            return None
        if bill.status == "generated":
            if bill.valid_until is not None and domain.eway_expired(bill.valid_until, now):
                return None
            return bill
    return None


def to_eway_bill_out(bill: EwayBill, *, at: datetime | None = None) -> EwayBillOut:
    """Serialise one attempt, with the two fields that are only true *at a moment*.

    `effective_status` and `remedy` are computed here rather than stored, because the table is
    append-only: nothing can update a row when the clock passes its validity, so the only honest
    place for "is this expired" is the read.
    """
    now = at or _now()
    effective = domain.eway_status_at(status=bill.status, valid_until=bill.valid_until, at=now)
    return EwayBillOut(
        id=bill.id,
        invoice_id=bill.invoice_id,
        status=bill.status,
        effective_status=effective,
        eway_bill_no=bill.eway_bill_no,
        transport_mode=bill.transport_mode,
        distance_km=bill.distance_km,
        vehicle_no=bill.vehicle_no,
        transporter_id=bill.transporter_id,
        transport_doc_no=bill.transport_doc_no,
        transport_doc_date=bill.transport_doc_date,
        over_dimensional=bill.over_dimensional,
        consignment_value_minor=bill.consignment_value_minor,
        valid_from=bill.valid_from,
        valid_until=bill.valid_until,
        remedy=domain.eway_remedy_for(status=effective, generated_at=bill.valid_from, at=now),
        error_code=bill.error_code,
        error=bill.error,
        attempted_at=bill.attempted_at,
    )


def to_eway_state(
    invoice: Invoice, latest: EwayBill | None, attempts: int, *, at: datetime | None = None
) -> EwayState:
    """A document's current e-Way position.

    `required` is answered even when nothing has been attempted, which is the point: an
    obligation nobody has met must be *visible*, and a null status with `required: true` is what
    a screen turns into "this consignment cannot legally move yet".
    """
    now = at or _now()
    required = domain.requires_eway_bill(
        consignment_value_minor=invoice.total_minor,
        document_type=invoice.document_type,
        supply_type=invoice.supply_type,
        status=invoice.status,
    )
    if latest is None:
        return EwayState(
            required=required,
            status=None,
            eway_bill_no=None,
            valid_from=None,
            valid_until=None,
            terminal=False,
            cancellable_until=None,
            remedy=None,
            attempts=0,
        )
    effective = domain.eway_status_at(status=latest.status, valid_until=latest.valid_until, at=now)
    return EwayState(
        required=required,
        status=effective,
        eway_bill_no=latest.eway_bill_no,
        valid_from=latest.valid_from,
        valid_until=latest.valid_until,
        # A refusal stands until the consignment changes, and an expired bill cannot be revived —
        # both are the platform having stopped, which is what a screen needs to say out loud.
        terminal=effective in ("failed", "cancelled", "expired"),
        cancellable_until=(
            domain.eway_cancellation_deadline(latest.valid_from)
            if effective == "generated" and latest.valid_from is not None
            else None
        ),
        remedy=domain.eway_remedy_for(status=effective, generated_at=latest.valid_from, at=now),
        attempts=attempts,
    )


def eway_bills_stmt(
    ctx: ScopeContext, *, status: str | None = None, invoice_id: uuid.UUID | None = None
) -> Select[Any]:
    """Scope-filtered e-Way attempts, newest first. `status` filters the **stored** value; an
    expired bill is stored as `generated`, so a caller after those reads `effective_status`."""
    stmt = select(EwayBill).where(
        EwayBill.organization_id == ctx.organization_id,
        EwayBill.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=EwayBill.id)
    if status:
        stmt = stmt.where(EwayBill.status == status)
    if invoice_id:
        stmt = stmt.where(EwayBill.invoice_id == invoice_id)
    return stmt.order_by(EwayBill.attempted_at.desc(), EwayBill.created_at.desc())


async def eway_bills_with_invoice(
    session: AsyncSession, org_id: uuid.UUID, bills: list[EwayBill]
) -> list[EwayBillWithInvoice]:
    """A page of attempts with enough of each document to be recognisable. Two round trips for
    the page, never one per row."""
    if not bills:
        return []
    stmt = select(Invoice.id, Invoice.number, Invoice.customer_id).where(
        Invoice.organization_id == org_id,
        Invoice.id.in_({b.invoice_id for b in bills}),
    )
    rows = (await session.execute(stmt)).all()
    numbers = {row[0]: row[1] for row in rows}
    names = await _customer_names(session, org_id, {row[2] for row in rows})
    customers = {row[0]: names.get(row[2], "") for row in rows}
    return [
        EwayBillWithInvoice(
            bill=to_eway_bill_out(bill),
            invoice_number=numbers.get(bill.invoice_id, ""),
            customer_name=customers.get(bill.invoice_id, ""),
        )
        for bill in bills
    ]


# --------------------------------------------------------------------- withholding: TDS / TCS


async def withholding_schedule(
    session: AsyncSession, *, jurisdiction: str = "IN"
) -> list[domain.WithholdingRate]:
    """The whole statutory schedule, every version of every head.

    Not org-scoped, and correctly so: `withholding_rates` carries no `organization_id` and is
    left unpolicied, because a rate under GST s. 51 is the law of one country rather than one
    tenant's data — the same reasoning that exempts `tax_rates`.

    Returned whole rather than filtered by date in SQL because the resolution is a domain rule
    (`domain.resolve_withholding_rate`), and a rule that lives half in a WHERE clause is a rule
    with two implementations.
    """
    stmt = (
        select(WithholdingRateRow)
        .where(WithholdingRateRow.jurisdiction == jurisdiction)
        .order_by(WithholdingRateRow.code, WithholdingRateRow.effective_from)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        domain.WithholdingRate(
            id=row.id,
            code=row.code,
            rate_pct=row.rate_pct,
            threshold_minor=row.threshold_minor,
            basis=row.basis,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            jurisdiction=row.jurisdiction,
        )
        for row in rows
    ]


async def financial_year_turnover(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    customer_id: uuid.UUID,
    on: date,
    before_invoice_id: uuid.UUID | None = None,
) -> int:
    """This organization's running total with one customer, in `on`'s financial year, in paise.

    The window an aggregate threshold (194Q, 206C(1H)) is measured over. Credit notes carry a
    negative total and therefore reduce it, which is right: a return reduces the consideration
    the threshold is measured against.

    `before_invoice_id` excludes the document being assessed, so the caller gets the *prior*
    total rather than one that already contains the invoice it is about to withhold on — the
    off-by-one that would put every threshold crossing one document early.
    """
    start, end = domain.financial_year_bounds(on)
    stmt = select(func.coalesce(func.sum(Invoice.total_minor), 0)).where(
        Invoice.organization_id == org_id,
        Invoice.customer_id == customer_id,
        Invoice.deleted_at.is_(None),
        Invoice.status.notin_(tuple(domain.UNREPORTED_STATUSES)),
        Invoice.issued_date >= start,
        Invoice.issued_date < end,
    )
    if before_invoice_id is not None:
        stmt = stmt.where(Invoice.id != before_invoice_id)
    return int((await session.execute(stmt)).scalar_one())


def withholdings_stmt(
    ctx: ScopeContext,
    *,
    period: str | None = None,
    code: str | None = None,
    invoice_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered withholding register, newest document first."""
    stmt = select(InvoiceWithholding).where(
        InvoiceWithholding.organization_id == ctx.organization_id,
        InvoiceWithholding.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=InvoiceWithholding.id)
    if period:
        stmt = stmt.where(InvoiceWithholding.period == period)
    if code:
        stmt = stmt.where(InvoiceWithholding.code == code)
    if invoice_id:
        stmt = stmt.where(InvoiceWithholding.invoice_id == invoice_id)
    return stmt.order_by(
        InvoiceWithholding.resolved_on.desc(), InvoiceWithholding.created_at.desc()
    )


async def withholdings_for_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[InvoiceWithholding]:
    stmt = (
        select(InvoiceWithholding)
        .where(
            InvoiceWithholding.organization_id == org_id,
            InvoiceWithholding.invoice_id == invoice_id,
            InvoiceWithholding.deleted_at.is_(None),
        )
        .order_by(InvoiceWithholding.code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def to_withholding_out(row: InvoiceWithholding) -> InvoiceWithholdingOut:
    return InvoiceWithholdingOut.model_validate(row)


async def withholdings_with_invoice(
    session: AsyncSession, org_id: uuid.UUID, rows: list[InvoiceWithholding]
) -> list[WithholdingWithInvoice]:
    """A page of register rows with enough of each document to be recognisable."""
    if not rows:
        return []
    stmt = select(Invoice.id, Invoice.number, Invoice.issued_date, Invoice.total_minor).where(
        Invoice.organization_id == org_id,
        Invoice.id.in_({r.invoice_id for r in rows}),
    )
    facts = {row[0]: (row[1], row[2], row[3]) for row in (await session.execute(stmt)).all()}
    return [
        WithholdingWithInvoice(
            withholding=to_withholding_out(row),
            invoice_number=facts.get(row.invoice_id, ("", date.min, 0))[0],
            invoice_issued_date=facts.get(row.invoice_id, ("", date.min, 0))[1],
            invoice_total_minor=facts.get(row.invoice_id, ("", date.min, 0))[2],
        )
        for row in rows
    ]


async def withholding_summary(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    period: str,
    code: str | None = None,
) -> WithholdingSummaryOut:
    """One period's withholding, grouped per counterparty — the shape GSTR-7 declares.

    Grouped in SQL rather than in Python because a busy period is thousands of rows and a report
    that pulls them all to add them up is a report that stops working at exactly the volume it
    starts mattering at. The totals are summed from the grouped rows, so the summary foots to its
    own rows by construction.
    """
    stmt = (
        select(
            InvoiceWithholding.counterparty_gstin,
            func.min(InvoiceWithholding.counterparty_name),
            func.count(func.distinct(InvoiceWithholding.invoice_id)),
            func.coalesce(func.sum(InvoiceWithholding.basis_minor), 0),
            func.coalesce(func.sum(InvoiceWithholding.amount_minor), 0),
            func.coalesce(func.sum(InvoiceWithholding.cgst_minor), 0),
            func.coalesce(func.sum(InvoiceWithholding.sgst_minor), 0),
            func.coalesce(func.sum(InvoiceWithholding.igst_minor), 0),
        )
        .where(
            InvoiceWithholding.organization_id == ctx.organization_id,
            InvoiceWithholding.deleted_at.is_(None),
            InvoiceWithholding.period == period,
        )
        .group_by(InvoiceWithholding.counterparty_gstin)
        .order_by(func.coalesce(func.sum(InvoiceWithholding.amount_minor), 0).desc())
    )
    if code:
        stmt = stmt.where(InvoiceWithholding.code == code)

    rows = [
        WithholdingDeductorRow(
            counterparty_gstin=row[0],
            counterparty_name=row[1] or "",
            document_count=int(row[2]),
            basis_minor=int(row[3]),
            amount_minor=int(row[4]),
            cgst_minor=int(row[5]),
            sgst_minor=int(row[6]),
            igst_minor=int(row[7]),
        )
        for row in (await session.execute(stmt)).all()
    ]
    return WithholdingSummaryOut(
        period=period,
        code=code,
        direction=domain.WITHHOLDING_DIRECTION.get(code) if code else None,
        rows=rows,
        document_count=sum(r.document_count for r in rows),
        total_basis_minor=sum(r.basis_minor for r in rows),
        total_amount_minor=sum(r.amount_minor for r in rows),
        total_cgst_minor=sum(r.cgst_minor for r in rows),
        total_sgst_minor=sum(r.sgst_minor for r in rows),
        total_igst_minor=sum(r.igst_minor for r in rows),
    )


async def withholding_rates_out(
    session: AsyncSession, *, jurisdiction: str = "IN"
) -> list[WithholdingRateOut]:
    """The published schedule, every version. Vocabulary, so it is readable by anybody who may
    read the register — the figures are only explicable beside the rates that produced them."""
    stmt = (
        select(WithholdingRateRow)
        .where(WithholdingRateRow.jurisdiction == jurisdiction)
        .order_by(WithholdingRateRow.code, WithholdingRateRow.effective_from)
    )
    return [
        WithholdingRateOut.model_validate(row)
        for row in (await session.execute(stmt)).scalars().all()
    ]
