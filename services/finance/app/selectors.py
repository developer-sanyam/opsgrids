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
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import domain
from app.models import (
    Customer,
    EInvoiceFiling,
    GstRegistration,
    Invoice,
    Payment,
    PaymentAllocation,
)
from app.schemas import (
    AgingBucket,
    CashflowPoint,
    CustomerOut,
    CustomerSummary,
    EInvoiceFilingOut,
    EInvoiceState,
    GstRegistrationOut,
    InvoiceLineOut,
    InvoiceOut,
    LedgerAccount,
    LedgerReport,
    OverviewKpi,
    OverviewSummary,
    PaymentAllocationOut,
    PaymentOut,
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
    invoice: Invoice, latest: EInvoiceFiling | None, attempts: int = 0
) -> EInvoiceState:
    """An invoice's current e-invoice position, from its newest filing attempt.

    `required` is answered from the document itself, not from the filings, so a document that has
    never been attempted still reports the obligation. A status of `None` alongside
    `required=True` is precisely the queue an auto-filing agent drains.
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
    )


def to_invoice_out(
    invoice: Invoice,
    paid_minor: int,
    latest_filing: EInvoiceFiling | None = None,
    attempts: int = 0,
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
        einvoice=to_einvoice_state(invoice, latest_filing, attempts),
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
