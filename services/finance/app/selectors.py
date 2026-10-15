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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

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
    ItcLedgerEntry,
    OutboxEvent,
    Payment,
    PaymentAllocation,
    PurchaseDocument,
    SupplierProfile,
)
from app.models import WithholdingRate as WithholdingRateRow
from app.schemas import (
    AgingBucket,
    B2csOut,
    BankDetailsOut,
    CashflowPoint,
    CurrencyRevenueHistory,
    CustomerOut,
    CustomerSummary,
    DocumentSeriesOut,
    EInvoiceFilingOut,
    EInvoiceState,
    EwayBillOut,
    EwayBillWithInvoice,
    EwayState,
    Gstr1Sections,
    Gstr3bItcOut,
    Gstr3bOutwardOut,
    Gstr3bPaymentOut,
    Gstr3bSections,
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
    ItcBalanceOut,
    ItcHeadBalanceOut,
    ItcLedgerEntryOut,
    LedgerReport,
    MonthlyRevenuePoint,
    OverviewKpi,
    OverviewSummary,
    PaymentAllocationOut,
    PaymentOut,
    PurchaseDocumentOut,
    RateGroupOut,
    ReturnInvoiceOut,
    ReturnTotalsOut,
    SupplierPrintOut,
    SupplierProfileOut,
    SupplierSettingsOut,
    TaxHeadAmountsOut,
    UtilisationLineOut,
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


def _today(today: date | None) -> date:
    """The real date a read reasons against, unless a test injected one. UTC: there is no
    per-organization timezone on this model today (`Organization` lives in another schema and
    carries none either), so the platform clock is the only honest default."""
    return today if today is not None else datetime.now(UTC).date()


def _invoice_facts(inv: Invoice, paid_minor: int, today: date) -> domain.InvoiceFacts:
    """`domain.InvoiceFacts` built with the **derived** status, not the stored column — every
    rollup (customer summaries, the overview) reasons from this rather than `inv.status`
    directly, so `overdue` reads the same everywhere a read reasons about it."""
    status = domain.effective_invoice_status(
        document_type=inv.document_type,
        stored_status=inv.status,
        due_date=inv.due_date,
        total_minor=inv.total_minor,
        paid_minor=paid_minor,
        today=today,
    )
    return domain.InvoiceFacts(
        document_type=inv.document_type,
        status=status,
        issued_date=inv.issued_date,
        due_date=inv.due_date,
        total_minor=inv.total_minor,
        amount_paid_minor=paid_minor,
    )


def effective_status_expr(today: date) -> Any:
    """`domain.effective_invoice_status`, as a SQL expression, so `status=` filters — and
    paginates — at the database rather than after the fact in Python.

    Mirrors that function exactly: `draft`/`void`/a stored `paid` win outright (the last is the
    projection `record_payment` writes in the same transaction as the allocation that settles an
    invoice); a credit note is always `sent`; everything else is derived fresh from the paid-so-
    far figure and `today`, so a legacy `overdue` frozen before this derivation existed is
    recomputed exactly like a `sent` row would be.
    """
    paid_subq = (
        select(func.coalesce(func.sum(PaymentAllocation.amount_minor), 0))
        .where(
            PaymentAllocation.organization_id == Invoice.organization_id,
            PaymentAllocation.invoice_id == Invoice.id,
            PaymentAllocation.deleted_at.is_(None),
        )
        .correlate(Invoice)
        .scalar_subquery()
    )
    return case(
        (Invoice.document_type == "credit_note", literal("sent")),
        (Invoice.status.in_(("draft", "void", "paid")), Invoice.status),
        (paid_subq >= Invoice.total_minor, literal("paid")),
        (Invoice.due_date < today, literal("overdue")),
        else_=literal("sent"),
    )


def invoices_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    customer_id: uuid.UUID | None = None,
    sort: str = "issued_desc",
    today: date | None = None,
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
    if customer_id is not None:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if status:
        stmt = stmt.where(effective_status_expr(_today(today)) == status)

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


async def dated_allocations_by_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[date, int]]]:
    """Every allocation posted against each invoice, dated by the payment it came from.

    The history a month-end snapshot replays: `paid_by_invoice` only needs *how much* is applied
    today, but `domain.revenue_history` needs *when*, so a month a year ago can be reconstructed
    as it stood then rather than as it stands now. Org-scoped like every read here."""
    if not invoice_ids:
        return {}
    stmt = (
        select(PaymentAllocation.invoice_id, Payment.received_date, PaymentAllocation.amount_minor)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.organization_id == org_id,
            PaymentAllocation.deleted_at.is_(None),
            PaymentAllocation.invoice_id.in_(invoice_ids),
        )
    )
    out: dict[uuid.UUID, list[tuple[date, int]]] = {}
    for invoice_id, received_date, amount_minor in (await session.execute(stmt)).all():
        out.setdefault(invoice_id, []).append((received_date, int(amount_minor)))
    return out


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
        # A failed *withdrawal* is not the platform having given up on the document: the
        # registration stands, and `remedy` says what a person can still do about it.
        terminal=domain.is_terminal_filing(latest.status)
        and not domain.is_failed_withdrawal(latest.status, latest.ack_date),
        cancellable_until=(
            domain.cancellation_deadline(latest.ack_date)
            if latest.ack_date is not None
            and (
                latest.status == "filed"
                or domain.is_failed_withdrawal(latest.status, latest.ack_date)
            )
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
    today: date | None = None,
) -> InvoiceOut:
    """Serialise one invoice with its derived paid-so-far figure and filing position attached.

    `status` is never the stored column verbatim — it is `domain.effective_invoice_status`,
    recomputed against `today` on every read, which is what lets an invoice cross into `overdue`
    with no write ever having touched its row.
    """
    status = domain.effective_invoice_status(
        document_type=invoice.document_type,
        stored_status=invoice.status,
        due_date=invoice.due_date,
        total_minor=invoice.total_minor,
        paid_minor=paid_minor,
        today=_today(today),
    )
    return InvoiceOut(
        id=invoice.id,
        number=invoice.number,
        document_type=invoice.document_type,
        customer_id=invoice.customer_id,
        issued_date=invoice.issued_date,
        due_date=invoice.due_date,
        status=status,
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
        round_off_minor=invoice.round_off_minor,
        seller_legal_name=invoice.seller_legal_name,
        seller_address=invoice.seller_address,
        buyer_name=invoice.buyer_name,
        buyer_address=invoice.buyer_address,
        buyer_state_code=invoice.buyer_state_code,
        ship_to_address=invoice.ship_to_address,
        einvoice=to_einvoice_state(invoice, latest_filing, attempts, at=at),
    )


async def get_invoice(
    session: AsyncSession,
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    for_update: bool = False,
    skip_locked: bool = False,
) -> Invoice | None:
    """Org-scoped fetch by id, with lines. Returns None on a tenant mismatch or a soft-deleted
    row — confirming an id exists in *some* organization is itself a disclosure.

    `for_update=True` row-locks it — the credit-note path takes this before reading how much has
    already been credited against it (`credited_total_for_invoice`), so a second credit note
    racing the same invoice serializes on the lock instead of both reading the same figure and
    both passing the cap.

    `skip_locked=True` (with `for_update`) returns None for a row somebody else holds rather than
    waiting behind them — what the reclaim sweep wants: an invoice another replica, or a request,
    is already driving is left alone this tick, not queued behind."""
    stmt = (
        select(Invoice)
        .where(
            Invoice.id == invoice_id,
            Invoice.organization_id == org_id,
            Invoice.deleted_at.is_(None),
        )
        .options(selectinload(Invoice.lines))
    )
    if for_update:
        stmt = stmt.with_for_update(skip_locked=skip_locked, of=Invoice)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_customer(
    session: AsyncSession, org_id: uuid.UUID, customer_id: uuid.UUID, *, for_update: bool = False
) -> Customer | None:
    """Org-scoped customer fetch. None on a tenant mismatch or a soft-deleted row.

    `for_update=True` row-locks it — `archive_customer` takes this before checking
    `customer_has_references`, and `create_invoice`/`record_payment` take it before naming the
    customer on a new document, so an archive racing a new invoice or payment for the same
    customer serializes on the row instead of the reference-check and the insert each running
    against a version of the world the other has already invalidated."""
    stmt = select(Customer).where(
        Customer.id == customer_id,
        Customer.organization_id == org_id,
        Customer.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def live_customer_with_gstin(
    session: AsyncSession,
    org_id: uuid.UUID,
    gstin: str,
    *,
    excluding: uuid.UUID | None = None,
) -> Customer | None:
    """The live customer already holding this GSTIN, if any — the natural key a second customer
    must not repeat. `excluding` leaves out the customer being edited."""
    stmt = select(Customer).where(
        Customer.organization_id == org_id,
        Customer.gstin == gstin,
        Customer.deleted_at.is_(None),
    )
    if excluding is not None:
        stmt = stmt.where(Customer.id != excluding)
    return (await session.execute(stmt.limit(1))).scalars().first()


async def get_customer_by_crm_contact(
    session: AsyncSession, org_id: uuid.UUID, crm_contact_id: uuid.UUID
) -> Customer | None:
    """The billed party already bridged from this CRM contact, if one exists —
    `ensure_customer_for_crm_contact`'s own lookup, both on the replay path (no network call
    needed) and re-checked for a concurrent creator before a fresh insert. None on a tenant
    mismatch, an unbridged contact, or a soft-deleted row, backed by
    `uq_customers_org_crm_contact`."""
    stmt = select(Customer).where(
        Customer.crm_contact_id == crm_contact_id,
        Customer.organization_id == org_id,
        Customer.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def invoice_exists_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> bool:
    """Whether this organization already has an `invoice` document (not a credit note) for this
    order — the read-side mirror of `uq_invoices_org_order_id`, checked before a write is
    attempted so the ordinary case (a redelivered `invoice_requested`) never has to reach the
    database's own race guard. A credit note naming this order is deliberately not seen here,
    for the same reason the index does not count one: it corrects the original invoice rather
    than competing with it for the order."""
    stmt = select(Invoice.id).where(
        Invoice.organization_id == org_id,
        Invoice.order_id == order_id,
        Invoice.document_type == "invoice",
        Invoice.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def get_invoices(
    session: AsyncSession,
    org_id: uuid.UUID,
    invoice_ids: list[uuid.UUID],
    *,
    for_update: bool = False,
) -> dict[uuid.UUID, Invoice]:
    """Org-scoped batch fetch by id — the multi-invoice analogue of `get_invoice`, so a payment
    that allocates across several documents costs one query rather than one per allocation.

    `for_update=True` row-locks every matching invoice, in a deterministic order (`ORDER BY id`),
    before `record_payment` reads how much is already applied to each (`paid_by_invoice`) and
    checks it against what this payment would add. Without the lock two concurrent payments
    allocating the same invoice each read the same "already applied" figure, each pass the
    outstanding-amount check, and the invoice is paid past its total. Locking in id order — not
    payload order — means a payment allocating invoices A and B can never deadlock against one
    allocating the same two in the other order."""
    if not invoice_ids:
        return {}
    stmt = (
        select(Invoice)
        .where(
            Invoice.id.in_(invoice_ids),
            Invoice.organization_id == org_id,
            Invoice.deleted_at.is_(None),
        )
        .order_by(Invoice.id)
    )
    if for_update:
        stmt = stmt.with_for_update()
    rows = (await session.execute(stmt)).scalars().all()
    return {row.id: row for row in rows}


async def credited_total_for_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> int:
    """What has already been credited against one invoice, as a positive minor-unit figure —
    the sum of the (negative) totals of every credit note that names it as `corrects_invoice_id`.
    """
    stmt = select(func.coalesce(func.sum(Invoice.total_minor), 0)).where(
        Invoice.organization_id == org_id,
        Invoice.corrects_invoice_id == invoice_id,
        Invoice.document_type == "credit_note",
        Invoice.deleted_at.is_(None),
    )
    total = (await session.execute(stmt)).scalar_one()
    return -int(total)


async def number_exists(session: AsyncSession, org_id: uuid.UUID, number: str) -> bool:
    stmt = select(Invoice.id).where(Invoice.organization_id == org_id, Invoice.number == number)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def reference_exists(session: AsyncSession, org_id: uuid.UUID, reference: str) -> bool:
    stmt = select(Payment.id).where(
        Payment.organization_id == org_id, Payment.reference == reference
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def customer_has_references(
    session: AsyncSession, org_id: uuid.UUID, customer_id: uuid.UUID
) -> bool:
    """Whether any invoice or payment — settled or not, live or soft-deleted — still names this
    customer. Archiving is refused unconditionally while this is true (`services.archive_customer`
    explains why): append-only documents can never be reassigned to another customer."""
    invoice_stmt = select(Invoice.id).where(
        Invoice.organization_id == org_id, Invoice.customer_id == customer_id
    )
    if (await session.execute(invoice_stmt)).first() is not None:
        return True
    payment_stmt = select(Payment.id).where(
        Payment.organization_id == org_id, Payment.customer_id == customer_id
    )
    return (await session.execute(payment_stmt)).first() is not None


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

#: The two claim statuses (D3): the row committed *before* a portal call, never an outcome.
CLAIM_STATUSES: tuple[str, ...] = ("submitting", "cancelling")


def _claims_last(model: type[EInvoiceFiling] | type[EwayBill]) -> Any:
    """The last tie-break in every newest-first ordering of an attempt ledger: at an identical
    `(attempted_at, created_at)` an outcome row is newer than a claim row.

    A claim and the outcome it leads to share `attempted_at` by design (the outcome is recorded
    against the instant the attempt was claimed), and when both are written in one transaction —
    the single-transaction convenience wrappers the unit tests drive — they share `created_at`
    too. A claim is always written before its own outcome, never after, so ranking claims last at
    a tie is not a guess; without it the sort is free to put the claim first, and the read model
    would report a finished attempt as still in flight."""
    return case((model.status.in_(CLAIM_STATUSES), 1), else_=0)


def _newest_first(model: type[EInvoiceFiling] | type[EwayBill]) -> tuple[Any, ...]:
    return (model.attempted_at.desc(), model.created_at.desc(), _claims_last(model).asc())


def _is_newest(model: Any) -> Any:
    """SQL: `model`'s row is the newest live row for its document, by exactly `_newest_first`'s
    ordering — a `NOT EXISTS` over a later row for the same `(organization_id, invoice_id)`.

    `select(newer.id)`, not a bare `exists()`: the latter renders `SELECT *`, which the outbox
    role's column-scoped grant (`migrations/env.py`) refuses — as it should — and this predicate
    is also the reclaim sweep's discovery test."""
    newer = aliased(model)
    return ~(
        select(newer.id)
        .where(
            # The organization too, not only the invoice: it is what lets the correlated probe
            # ride `(organization_id, invoice_id)` rather than scan the ledger.
            newer.organization_id == model.organization_id,
            newer.invoice_id == model.invoice_id,
            newer.deleted_at.is_(None),
            or_(
                newer.attempted_at > model.attempted_at,
                and_(
                    newer.attempted_at == model.attempted_at,
                    newer.created_at > model.created_at,
                ),
                and_(
                    newer.attempted_at == model.attempted_at,
                    newer.created_at == model.created_at,
                    _claims_last(newer) < _claims_last(model),
                ),
            ),
        )
        .exists()
    )


def visible_attempts(rows: list[Any]) -> list[Any]:
    """A newest-first attempt history as a person reads it: every outcome row, and a claim row
    only while it is still the newest — i.e. while that attempt is genuinely in flight.

    A claim that has been answered (or re-driven by the reclaim sweep, whose every drive appends a
    fresh claim as its lease) says nothing its outcome row does not: the outcome carries the same
    reason, the same transport facts, the same instant. Showing them would double every history
    and turn one withdrawal the sweep had to retry into five lines. They stay on the ledger — it
    is append-only — and who asked is on the audit row (`*.cancel_requested`,
    `eway.generate_requested`); this is the read model only.
    """
    return [row for index, row in enumerate(rows) if index == 0 or row.status not in CLAIM_STATUSES]


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
        .order_by(*_newest_first(EInvoiceFiling))
    )
    return list((await session.execute(stmt)).scalars().all())


#: `services.EVT_INVOICE_FILING_REQUESTED`, named here because selectors sit below services and may
#: not import them. A test pins the two together.
FILING_REQUESTED_EVENT = "finance.compliance.invoice.filing_requested"


def _request_backoff(requests: Any) -> Any:
    """SQL: `domain.resume_after(n)` for an integer SQL expression `n` — the same bounded backoff
    the Python side computes, so discovery and the tenant-bound re-check agree on when a re-queued
    invoice is due again."""
    return (
        literal(domain.FILING_RECLAIM_AFTER)
        + func.least(
            literal(domain.FILING_RETRY_BASE) * func.power(2, requests - 1),
            literal(domain.FILING_RETRY_CEILING),
        )
        - literal(domain.FILING_RETRY_BASE)
    )


def _requests_backing_off(org_col: Any, invoice_col: Any, after_col: Any) -> Any:
    """SQL: a `filing_requested` for this invoice is still in flight — either one the relay has
    **not yet published** (any, whenever staged), or one published since `after_col` whose
    newest is still inside `resume_after(count)` of the published ones. Measured on the
    database's clock (`created_at` is `now()` at insert).

    Unpublished requests are never attempts: with the relay down they only mean the request is
    queued, and counting them would spend an invoice's whole budget in the outage and end it
    `retry_exhausted` with nothing ever having been asked of anybody. While one is queued, the
    invoice is simply left alone — re-queueing behind it would only queue a duplicate.

    The published half is an aggregate with `HAVING` and no `GROUP BY`: one row when the condition
    holds, none otherwise (no requests gives a NULL `max`, and the comparison is not true), so the
    whole thing is an `EXISTS`. This is what keeps a re-queued or backing-off invoice **out of the
    discovery batch** instead of occupying one of its `limit` slots every tick."""
    queued = (
        select(OutboxEvent.id)
        .where(
            OutboxEvent.organization_id == org_col,
            OutboxEvent.aggregate_id == invoice_col,
            OutboxEvent.event_type == FILING_REQUESTED_EVENT,
            OutboxEvent.published_at.is_(None),
        )
        .exists()
    )
    recent = (
        select(literal(1))
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.organization_id == org_col,
            OutboxEvent.aggregate_id == invoice_col,
            OutboxEvent.event_type == FILING_REQUESTED_EVENT,
            OutboxEvent.published_at.is_not(None),
            OutboxEvent.created_at > after_col,
        )
        .having(func.max(OutboxEvent.created_at) + _request_backoff(func.count()) > func.now())
        .exists()
    )
    return or_(queued, recent)


def _has_live_registration(org_col: Any) -> Any:
    """SQL: the organization has a primary, live GST registration. Without one nothing can be
    filed — the consumer answers `no_registration` and writes no row — so re-queueing its
    invoices would only loop. Read on the outbox role's column grant on `gst_registrations`."""
    return (
        select(GstRegistration.id)
        .where(
            GstRegistration.organization_id == org_col,
            GstRegistration.is_primary.is_(True),
            GstRegistration.deleted_at.is_(None),
        )
        .exists()
    )


async def due_filing_reclaims(
    session: AsyncSession, *, older_than: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every reportable, unfiled invoice with no filing row at all, as `(organization_id,
    invoice_id)` — the automatic filing pipeline having lost track of it entirely.

    **Cross-tenant**, like the outbox relay's, subscription's dunning sweep's and payment's
    stuck-charge sweep's discovery: it exists to discover *which* organizations have such an
    invoice, so there is no tenant to bind while asking. Run on the BYPASSRLS outbox role
    (`get_outbox_sessionmaker`), which holds column-scoped `SELECT` on exactly what this reads
    (`migrations/env.py`); as the NOBYPASSRLS app role it would return nothing, and a document
    whose `filing_requested` never arrived — or whose one attempt crashed between calling the
    portal and recording it, leaving no row at all — would sit unfiled past its window, silently.

    Only invoices the sweep can actually move are returned, so the batch is never filled by ones it
    cannot (a head-of-line block across organizations):

    * the organization has a live GST registration (`_has_live_registration`) — without one the
      consumer files nothing and writes nothing, and re-queueing would loop forever;
    * no `filing_requested` staged since the invoice was created is still inside its backoff
      (`_requests_backing_off`) — a re-queue already in flight is not re-queued, and each further
      one waits `domain.resume_after(n)`. The tenant-bound re-check (`services.claim_lost_filing`)
      stops at `domain.MAX_FILING_ATTEMPTS` with a terminal row.

    Deliberately **not** every invoice with a non-terminal newest filing row: that is
    `due_stale_filings`' question, on its own clock. `older_than` is the floor below which an
    invoice is simply still waiting on its own first attempt.
    """
    reportable_document_types = tuple(domain.IRP_DOCUMENT_TYPES)
    stmt = (
        select(Invoice.organization_id, Invoice.id)
        .where(
            Invoice.supply_type.in_(domain.EINVOICE_SUPPLY_TYPES),
            Invoice.document_type.in_(reportable_document_types),
            Invoice.status.not_in(domain.UNREPORTED_STATUSES),
            Invoice.deleted_at.is_(None),
            Invoice.created_at <= older_than,
            ~select(EInvoiceFiling.id)
            .where(
                EInvoiceFiling.organization_id == Invoice.organization_id,
                EInvoiceFiling.invoice_id == Invoice.id,
                EInvoiceFiling.deleted_at.is_(None),
            )
            .exists(),
            _has_live_registration(Invoice.organization_id),
            ~_requests_backing_off(Invoice.organization_id, Invoice.id, Invoice.created_at),
        )
        .order_by(Invoice.created_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


def _stale_newest_stmt(
    model: type[EInvoiceFiling] | type[EwayBill],
    *,
    floors: dict[str, datetime],
    limit: int,
    extra: Any = None,
) -> Select[Any]:
    """`(organization_id, invoice_id)` for every document whose **newest** attempt row has one of
    the statuses in `floors`, made at or before that status's floor.

    "Newest" is `_is_newest`, ordered exactly as `filings_for_invoice` / `eway_bills_for_invoice`
    order it, so the sweep and the read model can never disagree about which row is current.
    Filtering by status first keeps the scan to the handful of rows in a claim or outage state
    rather than the whole ledger. Oldest first: a row backing off after a re-drive was re-stamped
    by it (every re-drive appends a fresh claim), so it sorts behind rows that are genuinely due.
    """
    stmt = (
        select(model.organization_id, model.invoice_id)
        .where(
            or_(
                *(
                    and_(model.status == status, model.attempted_at <= floor)
                    for status, floor in floors.items()
                )
            ),
            model.deleted_at.is_(None),
            _is_newest(model),
        )
        .order_by(model.attempted_at)
        .limit(limit)
    )
    if extra is not None:
        stmt = stmt.where(extra)
    return stmt


async def due_stale_filings(
    session: AsyncSession, *, floors: dict[str, datetime], limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every invoice whose newest e-invoice filing row is a stale `pending` or `cancelling` — an
    outage answer whose scheduled retry may have been lost, or a cancellation claim whose process
    died between the portal call and recording it.

    **Cross-tenant, like `due_filing_reclaims`**, on the same role and column grant. `floors` is
    per status: a `pending` row's own retry may be scheduled up to
    `domain.pending_redrive_after(MAX_FILING_ATTEMPTS)` out, so it is not even discovered before
    then — hundreds of rows inside their own retry window during a portal outage must not fill the
    batch. A `pending` row is further excluded while a re-queue of it is backing off, or when its
    organization has no registration to file under (the same two filters `due_filing_reclaims`
    applies). The exact per-attempt deadline is re-checked under the tenant binding before
    anything is driven.
    """
    pending_is_actionable = or_(
        EInvoiceFiling.status != "pending",
        and_(
            _has_live_registration(EInvoiceFiling.organization_id),
            ~_requests_backing_off(
                EInvoiceFiling.organization_id, EInvoiceFiling.invoice_id, EInvoiceFiling.created_at
            ),
        ),
    )
    stmt = _stale_newest_stmt(
        EInvoiceFiling, floors=floors, limit=limit, extra=pending_is_actionable
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def due_stale_eway(
    session: AsyncSession, *, floors: dict[str, datetime], limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """The e-Way twin of `due_stale_filings`: newest row `submitting` (a generation claim whose
    process died), `pending` (an outage nothing else retries) or `cancelling` (a withdrawal claim).
    Cross-tenant on the outbox role's column grant on `eway_bills`. No outbox lease is needed here:
    every re-drive appends a fresh claim row, which is itself the lease."""
    stmt = _stale_newest_stmt(EwayBill, floors=floors, limit=limit)
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


@dataclass(frozen=True, slots=True)
class RequestHistory:
    """`filing_requested` for one invoice, read under the tenant binding: how many the relay has
    **published** since some moment (the attempts that count), the newest of those, whether any
    request is still queued unpublished, and the database's own clock."""

    count: int
    newest_at: datetime | None
    db_now: datetime
    queued: bool = False

    def backing_off(self) -> bool:
        """A request is still queued, or the newest published one is still inside
        `domain.resume_after(count)` — exactly `_requests_backing_off`'s SQL, so discovery and the
        re-check agree."""
        if self.queued:
            return True
        if self.count == 0 or self.newest_at is None:
            return False
        return self.newest_at + domain.resume_after(self.count) > self.db_now


async def filing_requests_since(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID, *, after: datetime
) -> RequestHistory:
    """How many `filing_requested` events for the invoice the relay has **published** since
    `after` (a filing row's, or the invoice's own, `created_at`), and whether any is still queued
    unpublished — the sweep's **lease and budget** for a re-queue.

    A re-queue cannot be recorded on the ledger (a `pending` row is never rewritten, and a lost
    invoice has no row at all), so the outbox rows the re-queues themselves staged are the record:
    while one is still queued, or the newest published one is inside its backoff, the invoice is
    left alone; at `domain.MAX_FILING_ATTEMPTS` *published* requests the sweep stops with a
    terminal row. Unpublished rows are never counted — a relay outage is not an invoice's failure
    to file. The finalize request is staged in the invoice's own transaction, so it is never newer
    than `invoice.created_at` and never counts as an attempt. Runs under the tenant binding, on
    the app role, against this service's own outbox — no cross-schema read.
    """
    published_since = and_(OutboxEvent.published_at.is_not(None), OutboxEvent.created_at > after)
    stmt = select(
        func.count().filter(published_since),
        func.max(OutboxEvent.created_at).filter(published_since),
        func.count().filter(OutboxEvent.published_at.is_(None)),
        func.now(),
    ).where(
        OutboxEvent.organization_id == org_id,
        OutboxEvent.event_type == FILING_REQUESTED_EVENT,
        OutboxEvent.aggregate_id == invoice_id,
    )
    count, newest_at, queued, db_now = (await session.execute(stmt)).one()
    return RequestHistory(
        count=int(count), newest_at=newest_at, db_now=db_now, queued=int(queued) > 0
    )


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
        .order_by(EInvoiceFiling.invoice_id, *_newest_first(EInvoiceFiling))
    )
    latest = {row.invoice_id: row for row in (await session.execute(latest_stmt)).scalars().all()}

    # Attempts are outcomes: a claim and the answer it led to are one attempt, and a sweep's
    # lease rows are not attempts at all (see `visible_attempts`).
    count_stmt = (
        select(EInvoiceFiling.invoice_id, func.count())
        .where(
            EInvoiceFiling.organization_id == org_id,
            EInvoiceFiling.invoice_id.in_(invoice_ids),
            EInvoiceFiling.deleted_at.is_(None),
            EInvoiceFiling.status.not_in(CLAIM_STATUSES),
        )
        .group_by(EInvoiceFiling.invoice_id)
    )
    counts = {inv_id: int(n) for inv_id, n in (await session.execute(count_stmt)).all()}
    return latest, counts


async def invoices_with_filings(
    session: AsyncSession,
    org_id: uuid.UUID,
    invoices: list[Invoice],
    *,
    today: date | None = None,
) -> list[InvoiceOut]:
    """Serialise a page of invoices with their paid-so-far figures and filing positions.

    One round trip for the cash applied, two for the filings — never one per row.
    """
    ids = [inv.id for inv in invoices]
    paid = await paid_by_invoice(session, org_id, ids)
    latest, counts = await latest_filings(session, org_id, ids)
    return [
        to_invoice_out(
            inv,
            paid.get(inv.id, 0),
            latest.get(inv.id),
            counts.get(inv.id, 0),
            today=today,
        )
        for inv in invoices
    ]


def to_filing_out(filing: EInvoiceFiling) -> EInvoiceFilingOut:
    return EInvoiceFilingOut.model_validate(filing)


# --------------------------------------------------------------------- GST registration


async def get_gst_registration(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> GstRegistration | None:
    """This organization's primary GST registration — the seller side of every tax decision.

    None means the org has not configured one, which is a real state and not an error: an invoice
    can still be raised, it simply cannot be filed, and the filing attempt says so.

    `for_update=True` row-locks it — `generate_return`/`file_return` take this before assembling
    or filing a `gstr3b`, so two calls racing the same registration serialise on the row instead
    of both reading the same ITC ledger position and both posting a utilisation against it.
    """
    stmt = select(GstRegistration).where(
        GstRegistration.organization_id == org_id,
        GstRegistration.is_primary.is_(True),
        GstRegistration.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def all_gst_registrations(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> list[GstRegistration]:
    """Every live registration for the org. A company registers per state, so there may be
    several; exactly one is primary.

    `for_update=True` row-locks every one returned, in the same deterministic order this already
    sorts by, for the same reason `get_gst_registration`'s lock exists."""
    stmt = (
        select(GstRegistration)
        .where(
            GstRegistration.organization_id == org_id,
            GstRegistration.deleted_at.is_(None),
        )
        .order_by(GstRegistration.is_primary.desc(), GstRegistration.state_code)
    )
    if for_update:
        stmt = stmt.with_for_update()
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
        address_line1=registration.address_line1,
        address_line2=registration.address_line2,
        city=registration.city,
        pincode=registration.pincode,
    )


# --------------------------------------------------------------------- supplier profile


async def get_supplier_profile(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> SupplierProfile | None:
    """This organization's PAN and bank details, or None before any were entered.

    `for_update=True` row-locks it, so two settings saves racing each other serialise rather than
    the second silently overwriting fields the first changed."""
    stmt = select(SupplierProfile).where(
        SupplierProfile.organization_id == org_id,
        SupplierProfile.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def to_bank_details(profile: SupplierProfile, *, account_number: str) -> BankDetailsOut:
    """The bank block, with whichever form of the account number the caller is entitled to: the
    mask everywhere except the printable invoice."""
    return BankDetailsOut(
        account_name=profile.bank_account_name,
        account_number=account_number,
        ifsc=profile.bank_ifsc,
        bank_name=profile.bank_name,
        branch=profile.bank_branch,
        upi_id=profile.upi_id,
    )


def to_supplier_profile_out(profile: SupplierProfile | None) -> SupplierProfileOut:
    if profile is None:
        return SupplierProfileOut(
            pan=None,
            bank=BankDetailsOut(
                account_name="", account_number="", ifsc="", bank_name="", branch="", upi_id=""
            ),
        )
    return SupplierProfileOut(
        pan=profile.pan,
        bank=to_bank_details(profile, account_number=profile.bank_account_number_masked),
    )


def supplier_missing_for(registration: GstRegistration | None) -> list[str]:
    """`domain.supplier_missing` over a registration row."""
    if registration is None:
        return domain.supplier_missing(has_registration=False)
    return domain.supplier_missing(
        has_registration=True,
        legal_name=registration.legal_name,
        address_line1=registration.address_line1,
        city=registration.city,
        pincode=registration.pincode,
    )


async def supplier_settings(session: AsyncSession, org_id: uuid.UUID) -> SupplierSettingsOut:
    """Everything Finance settings shows about the supplier, account number masked."""
    registration = await get_gst_registration(session, org_id)
    profile = await get_supplier_profile(session, org_id)
    missing = supplier_missing_for(registration)
    return SupplierSettingsOut(
        registration=to_gst_registration_out(registration) if registration else None,
        profile=to_supplier_profile_out(profile),
        ready=not missing,
        missing=missing,
    )


def to_supplier_print(
    registration: GstRegistration | None,
    profile: SupplierProfile | None,
    bank: BankDetailsOut | None,
) -> SupplierPrintOut:
    return SupplierPrintOut(
        gstin=registration.gstin if registration else None,
        legal_name=(registration.legal_name or None) if registration else None,
        trade_name=(registration.trade_name or None) if registration else None,
        state_code=registration.state_code if registration else None,
        address_line1=registration.address_line1 if registration else "",
        address_line2=registration.address_line2 if registration else "",
        city=registration.city if registration else "",
        pincode=registration.pincode if registration else "",
        pan=profile.pan if profile else None,
        bank=bank,
    )


# --------------------------------------------------------------------- purchases


def purchase_documents_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
) -> Select[Any]:
    """Scope-filtered, searchable purchase-document list. `ctx.apply` is the enforcement point —
    mirrors `invoices_stmt`'s shape rather than `all_purchase_documents`, which is unscoped by
    design (the reconciler's own whole-book read, never a route's)."""
    stmt = select(PurchaseDocument).where(
        PurchaseDocument.organization_id == ctx.organization_id,
        PurchaseDocument.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=PurchaseDocument.id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(PurchaseDocument.supplier_name).like(needle),
                func.lower(PurchaseDocument.supplier_gstin).like(needle),
                func.lower(PurchaseDocument.number).like(needle),
            )
        )
    return stmt.order_by(PurchaseDocument.document_date.desc(), PurchaseDocument.number.desc())


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
    session: AsyncSession, org_id: uuid.UUID, document_id: uuid.UUID, *, for_update: bool = False
) -> ImsDocument | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row — confirming an id
    exists in *some* organization is itself a disclosure.

    `for_update=True` row-locks it — `record_ims_decision` takes this before reading `status`, so
    two decisions racing the same document serialise on the row instead of each reading the same
    `before_status`, each computing an accrual (or a reversal) of their own, and the ledger ending
    up with the transition posted twice."""
    stmt = select(ImsDocument).where(
        ImsDocument.id == document_id,
        ImsDocument.organization_id == org_id,
        ImsDocument.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
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
    items = [to_payment_out(p, numbers) for p in rows]
    return items, int(total)


def to_payment_out(payment: Payment, invoice_numbers: dict[uuid.UUID, str]) -> PaymentOut:
    """Serialise one payment with its allocations resolved to invoice numbers. Shared between the
    list page and the single-payment response `POST /finance/payments` returns."""
    return PaymentOut(
        id=payment.id,
        reference=payment.reference,
        customer_id=payment.customer_id,
        received_date=payment.received_date,
        method=payment.method,
        amount_minor=payment.amount_minor,
        allocations=[
            PaymentAllocationOut(
                invoice_id=alloc.invoice_id,
                invoice_number=invoice_numbers.get(alloc.invoice_id, ""),
                amount_minor=alloc.amount_minor,
            )
            for alloc in payment.allocations
        ],
        note=payment.note,
    )


async def payment_out_for(session: AsyncSession, org_id: uuid.UUID, payment: Payment) -> PaymentOut:
    """Serialise a single freshly-recorded payment — one round trip for the invoice numbers its
    allocations point at."""
    invoice_ids = {alloc.invoice_id for alloc in payment.allocations}
    numbers = await _invoice_numbers(session, org_id, invoice_ids)
    return to_payment_out(payment, numbers)


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
    today: date | None = None,
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

    resolved_today = _today(today)
    summaries: list[CustomerSummary] = []
    for customer in customers:
        rows = by_customer.get(customer.id, [])
        facts = [_invoice_facts(inv, paid.get(inv.id, 0), resolved_today) for inv in rows]
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


async def get_overview(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> OverviewSummary:
    """The whole Overview payload: derived KPIs + representative trends, AR aging, attention,
    plus the real revenue history.

    The KPIs (UTC — see `_today`) are anchored to the real date, not the demo constant: "this
    period" is the calendar month `today` falls in, aging bands are days-overdue against `today`,
    and nothing here is frozen at write time. `revenue`/`receivables`/`overdue` and `aging` are
    computed from this tenant's own invoices and payments; `cash` and every KPI's trend line are
    representative figures with no general ledger behind them yet and are flagged
    `sample`/`trend_sample` on the wire rather than served as though they were real.

    `revenue_history` is anchored separately, to `now` resolved in `domain.DEFAULT_TIMEZONE`
    (IST) rather than `_today`'s UTC — see `domain`'s module note on why the two are not (yet)
    the same clock. `now` defaults to the real clock; a test may inject either or both.
    """
    org_id = ctx.organization_id
    resolved_today = _today(today)
    period_month = resolved_today.strftime("%Y-%m")
    period_label = resolved_today.strftime("%B %Y")
    resolved_now = now if now is not None else datetime.now(UTC)
    anchor_month = domain.month_key(domain.today_in(domain.DEFAULT_TIMEZONE, now=resolved_now))

    invoices = await all_invoices(session, org_id)
    paid = await paid_by_invoice(session, org_id, [inv.id for inv in invoices])
    facts = [_invoice_facts(inv, paid.get(inv.id, 0), resolved_today) for inv in invoices]

    revenue = domain.net_revenue_for_month(facts, period_month)
    receivables = domain.total_outstanding(facts)
    overdue = domain.total_overdue(facts)

    allocations = await dated_allocations_by_invoice(session, org_id, [inv.id for inv in invoices])
    history_facts = [
        domain.InvoiceHistoryFacts(
            currency=inv.currency,
            document_type=inv.document_type,
            stored_status=inv.status,
            issued_date=inv.issued_date,
            total_minor=inv.total_minor,
            allocations=tuple(allocations.get(inv.id, [])),
        )
        for inv in invoices
    ]
    history = domain.revenue_history(history_facts, anchor_month=anchor_month)
    revenue_history = [
        CurrencyRevenueHistory(
            currency=h.currency,
            revenue_current_minor=h.revenue_current_minor,
            revenue_previous_minor=h.revenue_previous_minor,
            revenue_delta_pct=domain.delta_pct(h.revenue_current_minor, h.revenue_previous_minor),
            received_current_minor=h.received_current_minor,
            received_previous_minor=h.received_previous_minor,
            received_delta_pct=domain.delta_pct(
                h.received_current_minor, h.received_previous_minor
            ),
            outstanding_current_minor=h.outstanding_current_minor,
            outstanding_previous_minor=h.outstanding_previous_minor,
            outstanding_delta_pct=domain.delta_pct(
                h.outstanding_current_minor, h.outstanding_previous_minor
            ),
            months=[
                MonthlyRevenuePoint(
                    month=p.month,
                    label=p.label,
                    revenue_minor=p.revenue_minor,
                    received_minor=p.received_minor,
                    outstanding_minor=p.outstanding_minor,
                )
                for p in h.months
            ],
        )
        for h in history
    ]

    def kpi(key: str, label: str, value: int, caption: str, *, sample: bool = False) -> OverviewKpi:
        trend = domain.KPI_TRENDS[key]
        return OverviewKpi(
            key=key,
            label=label,
            value_minor=value,
            deltaPct=trend.delta_pct,
            series=list(trend.series),
            caption=caption,
            sample=sample,
            # Every KPI's trend line is representative today — there is no history table behind
            # any of them — regardless of whether the headline `value_minor` is real.
            trend_sample=True,
        )

    kpis = [
        kpi("revenue", "Revenue this period", revenue, "Net of credit notes"),
        kpi("receivables", "Outstanding receivables", receivables, "Across all open invoices"),
        kpi("overdue", "Overdue", overdue, "Past the due date"),
        kpi(
            "cash",
            "Cash balance",
            domain.CASH_BALANCE_MINOR,
            "Bank + cash on hand",
            sample=True,
        ),
    ]

    aging = [
        AgingBucket(key=b.key, label=b.label, amount_minor=b.amount_minor, count=b.count)
        for b in domain.aging_buckets(facts, resolved_today)
    ]

    # The "needs attention" queue: open + overdue invoices, largest outstanding first.
    attention_rows = sorted(
        (
            inv
            for inv, f in zip(invoices, facts, strict=True)
            if domain.is_open_invoice(f) and f.status == "overdue"
        ),
        key=lambda inv: domain.outstanding_minor(
            _invoice_facts(inv, paid.get(inv.id, 0), resolved_today)
        ),
        reverse=True,
    )
    latest, counts = await latest_filings(session, org_id, [inv.id for inv in attention_rows])
    attention = [
        to_invoice_out(
            inv,
            paid.get(inv.id, 0),
            latest.get(inv.id),
            counts.get(inv.id, 0),
            today=resolved_today,
        )
        for inv in attention_rows
    ]

    return OverviewSummary(
        period_label=period_label,
        kpis=kpis,
        cashflow=[
            CashflowPoint(
                label=row.label, inflow_minor=row.inflow_minor, outflow_minor=row.outflow_minor
            )
            for row in domain.CASHFLOW
        ],
        aging=aging,
        attention=attention,
        cashflow_sample=True,
        revenue_history=revenue_history,
        revenue_history_timezone=domain.DEFAULT_TIMEZONE,
    )


def get_ledger(*, today: date | None = None) -> LedgerReport:
    """The trial-balance report. There is no general ledger behind it yet, so it carries no
    accounts: `sample=True` and an empty list, never invented figures presented as an
    organization's books."""
    return LedgerReport(
        period_label=domain.LEDGER_PERIOD_LABEL,
        as_of=_today(today),
        sample=True,
        accounts=[],
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


async def latest_filed_gstr3b_period(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID
) -> str | None:
    """The latest period this registration has a **filed** `gstr3b` for, or `None` if it has never
    filed one. `YYYY-MM` strings compare lexicographically the same as chronologically, so `MAX`
    is exactly "latest"."""
    stmt = select(func.max(GstReturn.period)).where(
        GstReturn.organization_id == org_id,
        GstReturn.gst_registration_id == gst_registration_id,
        GstReturn.return_type == "gstr3b",
        GstReturn.status == "filed",
        GstReturn.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def claim_period_for(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, source_period: str
) -> str:
    """The period a posting against `source_period` actually lands in: `source_period` itself, or
    the period right after whichever `gstr3b` this registration most recently filed — whichever is
    later.

    Not "the period right after `source_period`'s own filed return" — a decision can arrive long
    after several periods have been filed (a document reported for July decided on only after
    August has already gone out), and a walk that only asks "is `source_period` itself filed"
    would leave the posting sitting in a period no future GSTR-3B could ever pick up again. The
    latest filed period is a single fact about the whole registration, not about any one document,
    so one `MAX` query answers it regardless of how far behind the source period is.
    """
    latest_filed = await latest_filed_gstr3b_period(session, org_id, gst_registration_id)
    if latest_filed is None:
        return source_period
    return max(source_period, domain.next_period(latest_filed))


async def gstr3b_periods_after(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> list[str]:
    """Every `gstr3b` period for this registration, later than `period`, that already exists —
    filed or merely generated.

    A non-empty result means generating or filing `period` now would let it utilise brought-forward
    credit a later period has already drawn on (or is mid-way through drawing on), spending the
    same credit twice — brought-forward is read straight off the ledger, not off any particular
    return, so a later period's return existing at all is enough for this to be live even before
    that return itself is filed.
    """
    stmt = (
        select(GstReturn.period)
        .where(
            GstReturn.organization_id == org_id,
            GstReturn.gst_registration_id == gst_registration_id,
            GstReturn.return_type == "gstr3b",
            GstReturn.period > period,
            GstReturn.deleted_at.is_(None),
        )
        .order_by(GstReturn.period)
    )
    return list((await session.execute(stmt)).scalars().all())


async def gstr3b_pending_periods_before(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> list[str]:
    """Every `gstr3b` period for this registration, earlier than `period`, that has been
    generated but not yet filed — the periods that must be filed before `period` may be generated
    or filed itself.

    A period this platform was simply never asked to generate is not "pending": an organization
    that onboarded mid-year has no earlier rows at all, and that is not this platform's problem to
    refuse. Only a period that *exists* and is not `filed` blocks the one after it — the exact
    shape of the double-utilisation defect this check exists to close: generating September while
    August sat generated-but-unfiled let both periods utilise the same brought-forward credit.
    """
    stmt = (
        select(GstReturn.period)
        .where(
            GstReturn.organization_id == org_id,
            GstReturn.gst_registration_id == gst_registration_id,
            GstReturn.return_type == "gstr3b",
            GstReturn.period < period,
            GstReturn.status != "filed",
            GstReturn.deleted_at.is_(None),
        )
        .order_by(GstReturn.period)
    )
    return list((await session.execute(stmt)).scalars().all())


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


def _sections_model_for(return_type: str) -> type[Gstr1Sections] | type[Gstr3bSections]:
    """Which contract a return's snapshot validates against. The two tables share nothing a
    discriminated union could read off the JSONB itself, so `return_type` is the discriminator."""
    return Gstr3bSections if return_type == "gstr3b" else Gstr1Sections


async def return_detail(
    session: AsyncSession, org_id: uuid.UUID, ret: GstReturn
) -> GstReturnDetail:
    """The return, its snapshot and its trail.

    The snapshot is re-validated through `Gstr1Sections`/`Gstr3bSections` on the way out rather
    than handed back as raw JSON: a snapshot written by an older revision of this service must
    either satisfy the contract it is served under or fail loudly here, not reach a screen as a
    shape nothing describes.
    """
    transitions = await return_transitions(session, org_id, ret.id)
    model = _sections_model_for(ret.return_type)
    return GstReturnDetail(
        ret=to_return_out(ret),
        sections=model.model_validate(ret.sections) if ret.sections else None,
        transitions=[to_return_transition_out(t) for t in transitions],
    )


# --------------------------------------------------------------------- the ITC ledger
# The reads behind GSTR-3B's Table 4 and Table 6.1, and the ledger's own list/balance surface.
# Every posting is written by `services` in the same transaction as the decision or the filing
# that produced it; nothing here writes.

_ITC_HEADS: tuple[str, ...] = domain.ITC_TAX_HEAD_ORDER


def itc_ledger_stmt(
    ctx: ScopeContext,
    *,
    gst_registration_id: uuid.UUID | None = None,
    period: str | None = None,
    tax_head: str | None = None,
    kind: str | None = None,
) -> Select[Any]:
    """Scope-filtered ledger list, newest posting first."""
    stmt = select(ItcLedgerEntry).where(
        ItcLedgerEntry.organization_id == ctx.organization_id,
        ItcLedgerEntry.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ItcLedgerEntry.id)
    if gst_registration_id:
        stmt = stmt.where(ItcLedgerEntry.gst_registration_id == gst_registration_id)
    if period:
        stmt = stmt.where(ItcLedgerEntry.period == period)
    if tax_head:
        stmt = stmt.where(ItcLedgerEntry.tax_head == tax_head)
    if kind:
        stmt = stmt.where(ItcLedgerEntry.kind == kind)
    return stmt.order_by(ItcLedgerEntry.posted_at.desc(), ItcLedgerEntry.created_at.desc())


def to_itc_ledger_entry_out(row: ItcLedgerEntry) -> ItcLedgerEntryOut:
    return ItcLedgerEntryOut.model_validate(row)


async def _itc_amounts_by_head(
    session: AsyncSession,
    org_id: uuid.UUID,
    gst_registration_id: uuid.UUID,
    *,
    kind: str | None = None,
    period: str | None = None,
    period_before: str | None = None,
) -> domain.TaxHeadAmounts:
    """`SUM(amount_minor)` per tax head, filtered the way each caller below needs it — grouped in
    SQL rather than pulled row by row, so a registration's whole history is one query."""
    stmt = select(
        ItcLedgerEntry.tax_head, func.coalesce(func.sum(ItcLedgerEntry.amount_minor), 0)
    ).where(
        ItcLedgerEntry.organization_id == org_id,
        ItcLedgerEntry.gst_registration_id == gst_registration_id,
        ItcLedgerEntry.deleted_at.is_(None),
    )
    if kind:
        stmt = stmt.where(ItcLedgerEntry.kind == kind)
    if period:
        stmt = stmt.where(ItcLedgerEntry.period == period)
    if period_before:
        stmt = stmt.where(ItcLedgerEntry.period < period_before)
    stmt = stmt.group_by(ItcLedgerEntry.tax_head)
    totals = {head: int(amount) for head, amount in (await session.execute(stmt)).all()}
    return domain.TaxHeadAmounts(
        igst_minor=totals.get("igst", 0),
        cgst_minor=totals.get("cgst", 0),
        sgst_minor=totals.get("sgst", 0),
        cess_minor=totals.get("cess", 0),
    )


async def itc_brought_forward(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> domain.TaxHeadAmounts:
    """The ledger's balance carried into `period` — every posting of every kind from an earlier
    period, summed. What Table 6.1 has *on top of* the period's own net eligible credit."""
    return await _itc_amounts_by_head(session, org_id, gst_registration_id, period_before=period)


async def itc_period_movement(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> tuple[domain.TaxHeadAmounts, domain.TaxHeadAmounts]:
    """`(accrued, reversed)` for exactly this period — Table 4A(5) and 4B(1)."""
    accrued = await _itc_amounts_by_head(
        session, org_id, gst_registration_id, kind="accrual", period=period
    )
    reversed_ = await _itc_amounts_by_head(
        session, org_id, gst_registration_id, kind="reversal", period=period
    )
    # Reversal rows are posted negative so the ledger's own balance needs no case split; a GSTR-3B
    # table declares a reversal as a positive figure being taken back, so the sign flips here, at
    # the read boundary, rather than in the ledger itself.
    reversed_ = domain.TaxHeadAmounts(
        igst_minor=-reversed_.igst_minor,
        cgst_minor=-reversed_.cgst_minor,
        sgst_minor=-reversed_.sgst_minor,
        cess_minor=-reversed_.cess_minor,
    )
    return accrued, reversed_


async def itc_document_net_accrued(
    session: AsyncSession, org_id: uuid.UUID, ims_document_id: uuid.UUID
) -> domain.TaxHeadAmounts:
    """One document's net standing on the ledger — every accrual it ever posted, less every
    reversal already posted against it. What a further reversal must exactly mirror, so a
    document's credit can never be withdrawn twice or withdrawn past zero.

    A ledger row's `source_id` is the **decision** that produced it, not the document — a document
    accepted, rejected and accepted again posts a fresh accrual each time, keyed on the decision
    that made it, and `source_ref`'s uniqueness depends on that. So finding a document's net
    standing means going by way of its decision history rather than a direct filter.
    """
    decisions = await decisions_for_document(session, org_id, ims_document_id)
    if not decisions:
        return domain.ZERO_ITC
    stmt = (
        select(ItcLedgerEntry.tax_head, func.coalesce(func.sum(ItcLedgerEntry.amount_minor), 0))
        .where(
            ItcLedgerEntry.organization_id == org_id,
            ItcLedgerEntry.source_type == "ims_decision",
            ItcLedgerEntry.source_id.in_([d.id for d in decisions]),
            ItcLedgerEntry.kind.in_(("accrual", "reversal")),
            ItcLedgerEntry.deleted_at.is_(None),
        )
        .group_by(ItcLedgerEntry.tax_head)
    )
    totals = {head: int(amount) for head, amount in (await session.execute(stmt)).all()}
    return domain.TaxHeadAmounts(
        igst_minor=totals.get("igst", 0),
        cgst_minor=totals.get("cgst", 0),
        sgst_minor=totals.get("sgst", 0),
        cess_minor=totals.get("cess", 0),
    )


async def itc_blocked_amounts_for_period(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> domain.TaxHeadAmounts:
    """Table 4D(1), informational: the tax on accepted documents whose declared category is
    blocked under s. 17(5), and which therefore posted **no** accrual at all, landing in `period`.

    Not read off the ledger — a blocked document leaves no row there by design — but off
    `ims_documents` joined to each one's newest decision, which is where the category the person
    asserted actually lives. Keyed on the **claim period** the same way a real accrual would be,
    not the document's own reported period: a blocked document decided after its own period's
    return has already been filed would, had it been eligible, have accrued into the next open
    period — so its blocked figure belongs there too, for the same reason. Every accepted document
    up to and including `period` is a candidate, since a late decision can redirect forward past
    several already-filed periods.
    """
    candidates = [
        d
        for d in await all_ims_documents(session, org_id)
        if d.status == "accepted" and d.period <= period
    ]
    if not candidates:
        return domain.ZERO_ITC
    decisions = await latest_decisions(session, org_id, [d.id for d in candidates])
    total = domain.ZERO_ITC
    for document in candidates:
        decision = decisions.get(document.id)
        category = decision.expense_category if decision is not None else "general"
        if domain.is_itc_eligible(category):
            continue
        claim_period = await claim_period_for(session, org_id, gst_registration_id, document.period)
        if claim_period != period:
            continue
        # A credit note reduces what a supplier billed, so a blocked one would have reduced
        # credit, not added to it — the same sign convention `services.record_ims_decision`
        # applies to a real accrual.
        sign = -1 if document.document_type == "credit_note" else 1
        total = total + domain.TaxHeadAmounts(
            igst_minor=sign * document.igst_minor,
            cgst_minor=sign * document.cgst_minor,
            sgst_minor=sign * document.sgst_minor,
        )
    return total


def to_tax_head_amounts_out(amounts: domain.TaxHeadAmounts) -> TaxHeadAmountsOut:
    return TaxHeadAmountsOut(
        igst_minor=amounts.igst_minor,
        cgst_minor=amounts.cgst_minor,
        sgst_minor=amounts.sgst_minor,
        cess_minor=amounts.cess_minor,
        total_minor=amounts.total_minor,
    )


def to_gstr3b_sections(ret: domain.Gstr3bReturn) -> Gstr3bSections:
    """The assembled GSTR-3B, on the wire — and the exact shape the snapshot is stored in."""
    return Gstr3bSections(
        outward=Gstr3bOutwardOut(
            taxable_supplies=to_tax_head_amounts_out(ret.outward.taxable_supplies),
            outward_taxable_minor=ret.outward.outward_taxable_minor,
            zero_rated=to_tax_head_amounts_out(ret.outward.zero_rated),
            zero_rated_taxable_minor=ret.outward.zero_rated_taxable_minor,
            nil_rated_taxable_minor=ret.outward.nil_rated_taxable_minor,
            reverse_charge_inward_taxable_minor=ret.outward.reverse_charge_inward_taxable_minor,
            non_gst_taxable_minor=ret.outward.non_gst_taxable_minor,
            outward_reverse_charge=to_tax_head_amounts_out(ret.outward.outward_reverse_charge),
            outward_reverse_charge_taxable_minor=ret.outward.outward_reverse_charge_taxable_minor,
        ),
        itc=Gstr3bItcOut(
            available=to_tax_head_amounts_out(ret.itc.available),
            reversed_section_17_5=to_tax_head_amounts_out(ret.itc.reversed_section_17_5),
            reversed_other=to_tax_head_amounts_out(ret.itc.reversed_other),
            reclaimed=to_tax_head_amounts_out(ret.itc.reclaimed),
            ineligible_other=to_tax_head_amounts_out(ret.itc.ineligible_other),
            total_reversed=to_tax_head_amounts_out(ret.itc.total_reversed),
            net_eligible=to_tax_head_amounts_out(ret.itc.net_eligible),
        ),
        itc_brought_forward=to_tax_head_amounts_out(ret.itc_brought_forward),
        payment=Gstr3bPaymentOut(
            liability=to_tax_head_amounts_out(ret.payment.liability),
            utilisation=[
                UtilisationLineOut(
                    head=line.head, against=line.against, amount_minor=line.amount_minor
                )
                for line in ret.payment.utilisation.lines
            ],
            credit_remaining=to_tax_head_amounts_out(ret.payment.utilisation.credit_remaining),
            cash_payable=to_tax_head_amounts_out(ret.payment.cash_payable),
            excess_liability=to_tax_head_amounts_out(ret.payment.utilisation.excess_liability),
            credit_deficit=to_tax_head_amounts_out(ret.payment.utilisation.credit_deficit),
        ),
    )


async def itc_balance(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID, period: str
) -> ItcBalanceOut:
    """The ledger's position for one registration as of the end of `period` — brought-forward plus
    every posting of every kind made *in* that period, per head."""
    brought_forward = await itc_brought_forward(session, org_id, gst_registration_id, period)
    accrued, reversed_ = await itc_period_movement(session, org_id, gst_registration_id, period)
    utilised = await _itc_amounts_by_head(
        session, org_id, gst_registration_id, kind="utilisation", period=period
    )
    heads = []
    total = 0
    for head in _ITC_HEADS:
        accrued_h = accrued.for_head(head)
        reversed_h = reversed_.for_head(head)
        utilised_h = -utilised.for_head(head)
        balance_h = brought_forward.for_head(head) + accrued_h - reversed_h - utilised_h
        heads.append(
            ItcHeadBalanceOut(
                tax_head=head,
                accrued_minor=accrued_h,
                reversed_minor=reversed_h,
                utilised_minor=utilised_h,
                balance_minor=balance_h,
            )
        )
        total += balance_h
    return ItcBalanceOut(
        gst_registration_id=gst_registration_id,
        period=period,
        heads=heads,
        total_balance_minor=total,
    )


async def itc_total_balance(
    session: AsyncSession, org_id: uuid.UUID, gst_registration_id: uuid.UUID
) -> domain.TaxHeadAmounts:
    """The registration's whole ledger balance, right now — every accrual, reversal and
    utilisation ever posted, with no period boundary at all.

    The simplest statement of "is there enough credit to spend" there is, and deliberately not
    scoped to any period: `file_return`'s pre-posting safety net uses this rather than a
    period-scoped balance, so it stays correct even if the period-scoped bookkeeping elsewhere
    ever disagrees with it — the two are independent checks on purpose.
    """
    return await _itc_amounts_by_head(session, org_id, gst_registration_id)


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
        .order_by(*_newest_first(EwayBill))
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
        .order_by(EwayBill.invoice_id, *_newest_first(EwayBill))
    )
    latest = {row.invoice_id: row for row in (await session.execute(latest_stmt)).scalars().all()}
    count_stmt = (
        select(EwayBill.invoice_id, func.count())
        .where(
            EwayBill.organization_id == org_id,
            EwayBill.invoice_id.in_(invoice_ids),
            EwayBill.deleted_at.is_(None),
            EwayBill.status.not_in(CLAIM_STATUSES),
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


def generation_under_claim(bills: list[EwayBill]) -> EwayBill | None:
    """The `generated` row a `cancelling` claim is withdrawing — the newest one not already
    withdrawn, **regardless of expiry**.

    Not `live_eway_bill`: the claim checked the window and the validity when it was made, and a
    resumed withdrawal (`app/cron.py`) must ask the portal about the bill it claimed even if the
    clock has since run past its validity — the portal may already have done the work, and only
    it can say. The claim rows themselves carry no bill number (the partial unique index on
    `(org, eway_bill_no)` would refuse a second row claiming it), so this is how the number is
    found again.
    """
    for bill in bills:
        if bill.status == "cancelled":
            return None
        if bill.status == "generated":
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
        # both are the platform having stopped, which is what a screen needs to say out loud. A
        # failed *withdrawal* (a `failed` row carrying the bill's `valid_from`) is neither: the
        # bill stands, and `remedy` says what can still be done.
        terminal=effective in ("failed", "cancelled", "expired")
        and not (effective == "failed" and latest.valid_from is not None),
        cancellable_until=(
            domain.eway_cancellation_deadline(latest.valid_from)
            if effective in ("generated", "failed") and latest.valid_from is not None
            else None
        ),
        remedy=domain.eway_remedy_for(status=effective, generated_at=latest.valid_from, at=now),
        attempts=attempts,
    )


def eway_bills_stmt(
    ctx: ScopeContext, *, status: str | None = None, invoice_id: uuid.UUID | None = None
) -> Select[Any]:
    """Scope-filtered e-Way attempts, newest first. `status` filters the **stored** value; an
    expired bill is stored as `generated`, so a caller after those reads `effective_status`.

    A claim row (`submitting`, `cancelling`) is listed only while it is its document's newest —
    an attempt in flight — never once answered; the same rule `visible_attempts` applies to one
    document's history, in SQL so the page and its total agree."""
    stmt = select(EwayBill).where(
        EwayBill.organization_id == ctx.organization_id,
        EwayBill.deleted_at.is_(None),
        or_(EwayBill.status.not_in(CLAIM_STATUSES), _is_newest(EwayBill)),
    )
    stmt = ctx.apply(stmt, owner_col=EwayBill.id)
    if status:
        stmt = stmt.where(EwayBill.status == status)
    if invoice_id:
        stmt = stmt.where(EwayBill.invoice_id == invoice_id)
    return stmt.order_by(*_newest_first(EwayBill))


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
