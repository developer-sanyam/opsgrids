"""Tables owned by the finance service — the accounts-receivable record.

Everything here lives in the `finance` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10). The link to a billed party is a plain UUID within this schema;
foreign keys are used only *inside* the schema, where they are allowed and buy integrity.

Two platform rules shape every table here:

* **Money is integer minor units** (paise) in a single ISO currency — never a float, never
  `Numeric(_, 2)`. Amount columns are `BigInteger`; a large org's receivable book overflows
  a 32-bit integer.
* **Invoices, their lines, and the payment ledger are append-only.** An invoice is *frozen at
  finalize*: a correction is a new credit-note document, never an edit, and a line is never
  updated or deleted. `amount_paid_minor` is therefore **not** a column — it is derived by
  summing the payment allocations that point at a document, so the invoice row cannot drift out
  of agreement with the cash applied to it.

`tax_rates` and `withholding_rates` are the two deliberately different tables: they carry **no
`organization_id`** and are left UNPOLICIED. A GST rate schedule and a statutory withholding rate
are platform vocabulary — the same 5/12/18/28 % bands and the same 2 % under s. 51 for every
tenant in India — not tenant data, so RLS on them would be meaningless (see the RLS migrations,
which list them as exempt alongside `processed_events`).

`einvoice_filings`, `ims_decisions`, `eway_bills`, `gst_return_transitions`,
`invoice_withholdings` and `itc_ledger_entries` are append-only at the *database*, not merely by
convention: a trigger refuses UPDATE and DELETE. A privilege REVOKE would not survive
`apply_schema_grants`, which re-grants DML on every table at the end of every migration run — see
the migrations for the whole reasoning. Each holds **evidence**: what a tax portal was told, what
a person decided about somebody else's claim on this business, what licence goods moved under,
when a return was filed, what somebody else already withheld and paid to a tax authority, and what
was posted to the input-tax-credit ledger and why. `ims_reconciliations` is
pointedly not among them — a suggestion is recomputed opinion, and freezing an opinion buys an
auditor nothing. `gst_returns` is the one hybrid: its *snapshot* is frozen by a trigger the moment
the return is **filed**, while `status`, `filed_at` and `arn` may still move forward, because a
return is a record that gets filed rather than a row that gets appended. Before filing it may be
re-assembled — nothing has been reported to anybody yet — and every assembly appends its own
transition row, so "this was generated three times before it went" stays visible.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase, TimestampMixin, UUIDPrimaryKeyMixin

SCHEMA = "finance"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Customer(Base, TenantBase):
    """A billed party — the finance customer invoices and payments reference.

    A directory-grade record: name and contact/tax details a finance team needs to raise and
    chase an invoice. Distinct from any CRM account by design — no cross-schema link — the id
    here is what `invoices.customer_id` and `payments.customer_id` hold.
    """

    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: GST identification number, or null for an unregistered buyer.
    gstin: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    # ---------------------------------------------------------------- withholding
    # Three facts about the *buyer* that decide whether tax is withheld on what this organization
    # bills them. They live on the customer because that is what they are facts about, and they
    # are flags rather than derivations because the platform genuinely cannot infer them: whether
    # a body is notified under GST s. 51, and whether a buyer's own turnover makes them liable to
    # deduct under s. 194Q, are facts about somebody else's registration and somebody else's
    # books. Defaulting all three to false is what keeps withholding opt-in: an organization that
    # has not told the platform about its counterparties has no figures invented for it.

    #: A body notified under GST s. 51 — a government department, local authority or PSU — which
    #: must deduct 2 % of the taxable value on supplies above the threshold.
    is_tds_deductor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: A buyer liable to deduct income tax under s. 194Q. Mutually exclusive with 206C(1H) by
    #: statute, and this flag is which side of that exclusion the pair falls on.
    deducts_tds_194q: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: This organization collects TCS from this buyer under s. 206C(1H). Set once the seller has
    #: notified the buyer, which is how the obligation actually starts in practice.
    tcs_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # Every index leads with `organization_id`: under RLS the policy qual is ANDed into
        # every query, so a non-tenant-first index would scan the platform's rows (ADR-0002).
        Index("ix_customers_org_name", "organization_id", "name"),
    )


class Invoice(Base, TenantBase):
    """A tax invoice (bills) or a credit note (corrects a prior invoice with a negative total).

    Frozen at finalize: the header, its totals and its lines do not change after the document is
    written. A correction is a new `credit_note` row carrying `corrects_invoice_id`, never an
    edit — which is why there is no update path for these fields.

    Totals foot exactly: `subtotal_minor = Σ line.amount_minor` and
    `total_minor = subtotal_minor + tax_minor`. `amount_paid_minor` is deliberately *not* stored
    — it is the sum of the payment allocations pointing here, computed on read.
    """

    __tablename__ = "invoices"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: invoice | credit_note.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False, default="invoice")
    #: A plain UUID into `finance.customers.id`. A real FK is allowed here — it stays inside the
    #: schema — but it is left as a plain indexed column for symmetry with the cross-schema links
    #: the platform forbids, and validated in application code against the caller's tenant.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    issued_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: draft | sent | paid | overdue | void. Frozen at finalize; the lifecycle is not a mutate
    #: surface in this reads-first slice.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="sent")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: A percentage, not money — a rate, so `Numeric` is correct where floats never are. The
    #: document-level rate: the default each line inherits when it declares none of its own.
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ---------------------------------------------------------------- GST
    # Frozen with the rest of the document. The two GSTINs are *snapshots*, not lookups: a
    # customer may re-register or correct their number tomorrow, and the invoice that was filed
    # must keep naming the number it was filed under. This is also exactly what an IRP payload
    # needs, so the e-invoice can be rebuilt from the row years later.

    #: The GST state code the supply is taxed in — the recipient's location for a B2B supply.
    #: Null on a document raised before the org configured its GST registration.
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    #: b2b | b2c | export | sez. Decides both the tax heads and whether an IRN is required.
    supply_type: Mapped[str] = mapped_column(String(20), nullable=False, default="b2b")
    #: True when the recipient pays the tax instead of the supplier (s. 9(3)/9(4)). The document
    #: still carries the rate; the tax amounts are zero because the supplier does not charge it.
    reverse_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The seller's GSTIN as it stood when the document was finalized.
    seller_gstin: Mapped[str | None] = mapped_column(String(15))
    #: The buyer's GSTIN as it stood when the document was finalized; null for B2C.
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    #: The document's tax, split across heads. Foots twice over: to `tax_minor`, and to the sum
    #: of the same three columns on its lines.
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    notes: Mapped[str | None] = mapped_column(Text)
    #: Set on a credit note: the invoice this document corrects. Same-schema self-reference.
    corrects_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("finance.invoices.id", ondelete="RESTRICT")
    )
    #: The order this invoice was raised for, when `operations`' order engine asked for it
    #: (Fulfilment ADR-0024 §1/§4) — null for a manually raised invoice. A plain indexed UUID,
    #: never a cross-schema FK (ADR-0000 §10): `operations.orders` lives in a different schema, so
    #: this is validated in application code, not enforced by Postgres. `uq_invoices_org_order_id`
    #: below is the actual guarantee — one `invoice` document per order — and is scoped to
    #: `document_type = 'invoice'` precisely so a credit note correcting that invoice may name the
    #: same order without colliding with the index protecting the original document.
    order_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_no",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_invoices_org_number"),
        Index("ix_invoices_org_status", "organization_id", "status"),
        Index("ix_invoices_org_customer", "organization_id", "customer_id"),
        Index("ix_invoices_org_issued", "organization_id", "issued_date"),
        # The filing queue's access path: "which of this org's documents still need an IRN".
        Index("ix_invoices_org_supply_type", "organization_id", "supply_type"),
        # One `invoice` per order. A `credit_note` referencing the same order is deliberately
        # outside this index's predicate — see `order_id`'s own comment above.
        Index(
            "uq_invoices_org_order_id",
            "organization_id",
            "order_id",
            unique=True,
            postgresql_where=text(
                "order_id IS NOT NULL AND document_type = 'invoice' AND deleted_at IS NULL"
            ),
        ),
    )


class InvoiceLine(Base, TenantBase):
    """One line of an invoice — append-only.

    Lines are written once, with the invoice, and never updated or deleted: a change to a
    finalized document is a credit note, not an edit to its lines. `amount_minor` foots to
    `quantity x unit_price_minor` and may be negative on a credit note.
    """

    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 1-based position, so the lines read back in the order they were billed.
    line_no: Mapped[int] = mapped_column(nullable=False, default=1)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    #: A count, possibly fractional (tonnes, cft) and negative on a return line.
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The **taxable value** of the line. GST is computed on this, never on the gross.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ---------------------------------------------------------------- GST
    # A GST return and an e-invoice are both line-level documents: the HSN summary in GSTR-1 is
    # grouped by these codes and rates, and the IRP validates each line's tax against them. So
    # the split is stored per line and summed upward, never apportioned downward from a header.

    #: HSN (goods) or SAC (services), 4/6/8 digits. Null on documents raised before this slice.
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    #: The rate this line was taxed at — usually the document's, occasionally not (a freight or
    #: installation line on a goods invoice is the everyday case).
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    __table_args__ = (Index("ix_invoice_lines_org_invoice", "organization_id", "invoice_id"),)


class Payment(Base, TenantBase):
    """A payment received from a customer — append-only.

    A payment is a settled fact: it is written once and never edited. `amount_minor` is the sum
    of its allocations, which is how it drives each invoice's derived `amount_paid_minor`.
    """

    __tablename__ = "payments"

    reference: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: bank_transfer | upi | card | cheque | cash.
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "reference", name="uq_payments_org_reference"),
        Index("ix_payments_org_customer", "organization_id", "customer_id"),
        Index("ix_payments_org_received", "organization_id", "received_date"),
    )


class PaymentAllocation(Base, TenantBase):
    """How much of one payment was applied to one invoice — append-only.

    The join between cash received and the documents it settles. Summed per invoice, it is the
    invoice's `amount_paid_minor`; summed per payment, it is the payment's `amount_minor`.
    """

    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.invoices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="allocations")

    __table_args__ = (
        Index("ix_payment_allocations_org_payment", "organization_id", "payment_id"),
        Index("ix_payment_allocations_org_invoice", "organization_id", "invoice_id"),
    )


class InvoiceNumberSeries(Base, TenantBase):
    """The server's own invoice-numbering counter — one row per
    (organization, series, financial year).

    `services.allocate_invoice_number` is the only writer, and it never reads this row before
    writing it: the allocation is a single atomic `INSERT ... ON CONFLICT DO UPDATE ...
    RETURNING`, so two requests racing for the same organization serialize on Postgres's own row
    lock rather than on a read-then-write this table would otherwise invite. Mutable by design —
    this is a counter, not evidence — so it carries no append-only trigger.
    """

    __tablename__ = "invoice_number_series"

    #: `invoice` | `credit_note`. Two independent counters so a credit note never contends with
    #: an invoice for the next number in the sequence.
    series: Mapped[str] = mapped_column(String(20), nullable=False)
    #: India's financial year, April to March (`domain.financial_year`), e.g. `2026-27`.
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)
    #: The next sequence number this series will hand out.
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "series",
            "financial_year",
            name="uq_invoice_number_series_org_series_fy",
        ),
    )


class TaxRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A GST rate band, versioned by `effective_from` — **global platform vocabulary**.

    Deliberately not a `TenantBase`: it carries no `organization_id` and is left UNPOLICIED by
    the RLS migration. The Indian GST schedule (0/5/12/18/28 %) is the same for every tenant, so
    it is reference data, not tenant data — the same reasoning that leaves `processed_events`
    exempt. Versioned rather than mutated so a historical invoice's rate can always be explained:
    a rate change is a new row with a later `effective_from`, never an edit to an existing band.
    """

    __tablename__ = "tax_rates"

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    #: ISO country the band applies in. One market today; carried so a second is additive.
    jurisdiction: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("jurisdiction", "rate_pct", "effective_from", name="uq_tax_rates_band"),
        Index("ix_tax_rates_effective", "jurisdiction", "effective_from"),
    )


class GstRegistration(Base, TenantBase):
    """The seller's own GST registration — this organization's finance-side compliance config.

    Not global vocabulary like `tax_rates`: a GSTIN belongs to one tenant, so this is tenant data
    under row-level security like everything else. A company registers **per state**, so this is a
    table rather than a column, with one row flagged `is_primary` as the default place of supply
    and the identity invoices are filed under.

    Mutable, deliberately. A registration is configuration, not a ledger entry: a trade name
    changes, a registration is cancelled and re-issued. What must not change is the GSTIN an
    already-filed invoice was filed under, which is why `invoices.seller_gstin` snapshots it
    rather than joining here.
    """

    __tablename__ = "gst_registrations"

    #: Stored only after `domain.parse_gstin` has accepted it — 15 characters, never 20.
    gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    #: The first two characters of `gstin`, denormalised because every tax split reads it and
    #: the read must not depend on re-parsing a string.
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    #: The name on the registration certificate, which the IRP validates against.
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: regular | composition | casual. Only `regular` files e-invoices.
    registration_type: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")
    #: The default registration for this organization. A partial unique index makes "at most one"
    #: a database guarantee rather than a rule the service remembers.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "gstin", name="uq_gst_registrations_org_gstin"),
        Index("ix_gst_registrations_org_state", "organization_id", "state_code"),
        Index(
            "uq_gst_registrations_org_primary",
            "organization_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class EInvoiceFiling(Base, TenantBase):
    """One attempt to register one document with the Invoice Registration Portal — append-only.

    **One row per attempt, never mutated.** A retry after a portal timeout is a second row, not an
    edit to the first, so "what did we send, when, and what came back" is a query rather than an
    archaeology exercise — which is the whole point when the evidence has to satisfy an auditor
    rather than a developer. The invoice's *current* filing state is the newest row for it; there
    is no status column on `invoices` that could drift out of agreement with this history.

    Append-only is enforced by a trigger (see the migration), because a privilege REVOKE does not
    survive `apply_schema_grants`.
    """

    __tablename__ = "einvoice_filings"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.invoices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: pending | filed | failed | cancelled | cancelling — `domain.FILING_STATUSES`. `cancelling`
    #: is a claim row (D3): written before the portal is asked to withdraw the IRN.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The Invoice Reference Number: a 64-character hash the portal returns, unique per document
    #: across India. Present only on `filed` and `cancelled`.
    irn: Mapped[str | None] = mapped_column(String(64))
    #: The portal's acknowledgement number and date for this registration.
    ack_no: Mapped[str | None] = mapped_column(String(40))
    ack_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The signed QR payload the portal returns; it must be printed on the invoice, so it is kept
    #: verbatim rather than re-derived.
    qr_payload: Mapped[str | None] = mapped_column(Text)
    #: The portal's own error code (`2150` duplicate IRN, `3028` invalid GSTIN, …) and message,
    #: kept as sent so a support conversation quotes the portal rather than a paraphrase.
    error_code: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    #: When this attempt was made. Distinct from `created_at`, which is when the row was written:
    #: an attempt recorded after the fact (a reconciliation sweep) has the two differ.
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_einvoice_filings_org_invoice", "organization_id", "invoice_id"),
        # "This org's filing history, newest first" — the history endpoint's exact access path.
        Index("ix_einvoice_filings_org_attempted", "organization_id", "attempted_at"),
        # An IRN is unique per document nationally; within a tenant it must never be duplicated.
        Index(
            "uq_einvoice_filings_org_irn",
            "organization_id",
            "irn",
            unique=True,
            postgresql_where=text("irn IS NOT NULL"),
        ),
    )


class PurchaseDocument(Base, TenantBase):
    """A recorded purchase or expense document — the local counterpart of a supplier's filing.

    The inbound half of the book, and the only reason an IMS document can be reconciled against
    anything rather than merely listed. Deliberately thin: this slice needs the facts a match is
    made on — whose document, which number, which date, and what money — not an accounts-payable
    lifecycle, which is a product of its own.

    The tax split is **computed by `domain.split_gst` at write time**, exactly as an invoice's is,
    rather than accepted from the caller. A purchase document whose heads were entered by hand
    would be a second, quieter implementation of the one calculation on this platform that has to
    foot exactly, and the two would disagree eventually.

    There is no update path, for the same reason invoices have none: a document is a record of
    what a supplier billed, and correcting it is recording the credit or debit note they issue.
    """

    __tablename__ = "purchase_documents"

    #: NOT NULL, unlike `customers.gstin`: this table exists to be joined to a supplier's filings,
    #: and a purchase from an unregistered supplier can never appear in IMS at all.
    supplier_gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: invoice | credit_note | debit_note — `domain.IMS_DOCUMENT_TYPES`.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False, default="invoice")
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: The GST state code this supply was taxed in — the *recipient's*, which is this org.
    place_of_supply: Mapped[str | None] = mapped_column(String(2))

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: The assessable value. GST is computed on this, never on the gross.
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # A document number is unique per supplier, per kind — not globally: two suppliers
        # numbering their invoices `INV-1` is the ordinary case, not a collision.
        UniqueConstraint(
            "organization_id",
            "supplier_gstin",
            "document_type",
            "number",
            name="uq_purchase_documents_org_supplier_number",
        ),
        Index("ix_purchase_documents_org_supplier", "organization_id", "supplier_gstin"),
        Index("ix_purchase_documents_org_date", "organization_id", "document_date"),
    )


class ImsDocument(Base, TenantBase):
    """One document a supplier filed against this organization, as the IMS portal shows it.

    Inbound, not outbound: this is what somebody else reported *about* this business, and what the
    business does with it decides how much input tax credit it may claim. It is therefore a queue
    with a decision attached, not a report.

    `source_ref` is the portal's own row reference and the **ingest idempotency key**: a unique
    index on `(organization_id, source_ref)` means re-syncing a period cannot double a document,
    while two genuinely distinct portal rows describing the same supplier document remain
    representable — which they must be, because *that* is the duplicate the reconciliation exists
    to flag. A unique constraint on the supplier's document number would have made the duplicate
    reason unreachable by making duplicates unwritable.
    """

    __tablename__ = "ims_documents"

    #: The portal's reference for this row. Unique per organization; the ingest's dedupe key.
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which portal or feed this arrived through. One today; carried so a second is additive.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="ims")

    supplier_gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    document_number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: invoice | credit_note | debit_note — `domain.IMS_DOCUMENT_TYPES`.
    document_type: Mapped[str] = mapped_column(String(20), nullable=False, default="invoice")
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: The return period this document lands in, `YYYY-MM`. The queue is worked a period at a
    #: time, so this is a filter before it is a fact.
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: The supplier's own IRN, when they e-invoiced the document. Not this platform's to verify.
    irn: Mapped[str | None] = mapped_column(String(64))

    #: pending | accepted | rejected | pending_action — `domain.IMS_STATUSES`.
    #:
    #: A projection of the newest `ims_decisions` row, maintained in the same transaction as the
    #: decision that changes it, and written **nowhere else**. `einvoice_filings` deliberately has
    #: no such column on `invoices`; the difference is the access path. This queue's whole purpose
    #: is "show me every pending document for August", and deriving that from a newest-decision
    #: lateral join on every list page is the query that cannot be indexed. A test asserts the
    #: projection agrees with the decision history, which is what keeps the shortcut honest.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    #: When this document was pulled from the portal. Decides which of two identical arrivals is
    #: the original — see `domain.InboundDocument.first_seen`.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "source_ref", name="uq_ims_documents_org_source_ref"),
        # "This org's queue, filtered by where each document stands" — the list endpoint's path.
        Index("ix_ims_documents_org_status", "organization_id", "status"),
        Index("ix_ims_documents_org_period", "organization_id", "period"),
        Index("ix_ims_documents_org_supplier", "organization_id", "supplier_gstin"),
    )


class ImsReconciliation(Base, TenantBase):
    """One computed suggestion for one inbound document — derived data, not evidence.

    A run appends a row per document and the newest is the current suggestion, so "why did this
    row's advice change when the purchase book did" reads off the history. Deliberately **not**
    trigger-protected, unlike `einvoice_filings` and `ims_decisions`: nothing here is a fact about
    the world, it is an opinion recomputed from facts that are stored elsewhere, and freezing an
    opinion buys nothing an auditor can use.

    What it must never do is act. `suggested_action` is what a screen pre-selects; the document's
    status does not move until a person records a decision.
    """

    __tablename__ = "ims_reconciliations"

    ims_document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.ims_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Groups every row one reconciliation pass produced, so a run can be read back whole.
    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    #: accept | reject | pending_action — `domain.IMS_ACTIONS`.
    suggested_action: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Why — `domain.IMS_REASONS`. The half of the suggestion an operator actually reads.
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The reason as a sentence, naming the counterpart document and the delta.
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Signed minor units: the supplier's total less the recorded document's. Zero unless the
    #: reason is `amount_mismatch`.
    delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    matched_purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("finance.purchase_documents.id", ondelete="SET NULL")
    )
    #: On a `duplicate`, the earlier arrival this one duplicates. Same-table self-reference.
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("finance.ims_documents.id", ondelete="SET NULL")
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ims_reconciliations_org_document", "organization_id", "ims_document_id"),
        Index("ix_ims_reconciliations_org_computed", "organization_id", "computed_at"),
        Index("ix_ims_reconciliations_org_run", "organization_id", "run_id"),
    )


class ImsDecision(Base, TenantBase):
    """One operator's decision on one inbound document — append-only, and evidence.

    This is the row that decides money: accepting a document claims the input tax credit on it,
    rejecting it declines the credit and sends the document back to the supplier. Which means the
    question asked about it later is never "what does it say now" but "who decided this, when, and
    what were they shown at the time" — so the suggestion the platform made is recorded *beside*
    the decision, and a mistake stays visible with its correction after it.

    Append-only is enforced by a trigger, not a `REVOKE`: `apply_schema_grants` re-grants DML on
    every table at the end of every migration run, so a revoke written in a migration is undone
    minutes later in the same `alembic upgrade`. See the migration.
    """

    __tablename__ = "ims_decisions"

    ims_document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.ims_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: accept | reject | pending_action — `domain.IMS_ACTIONS`.
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The operator's own note. Free text on purpose: the portal takes no reason code for an IMS
    #: action, and a dropdown would only invite an untrue answer.
    note: Mapped[str | None] = mapped_column(Text)

    #: What the platform suggested when this decision was made, and why. Snapshotted rather than
    #: joined, so "did the operator follow the machine" stays answerable after a re-run changes
    #: the current suggestion.
    suggested_action: Mapped[str | None] = mapped_column(String(20))
    suggestion_reason: Mapped[str | None] = mapped_column(String(40))
    reconciliation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: What the accepted document was *for* — `domain.ITC_EXPENSE_CATEGORIES`, `general` by
    #: default. The one input the ITC ledger's accrual needs that nothing upstream can supply:
    #: `ims_documents` carries no HSN, no description and no line items, so there is nothing to
    #: derive a s. 17(5) blocked-credit category from. Set on every decision, not only `accept`,
    #: so a `reject` or `pending_action` still records what the person understood the document to
    #: be — evidence, the same reason `note` is free text rather than a fixed reason code.
    expense_category: Mapped[str] = mapped_column(String(40), nullable=False, default="general")

    #: Who. The principal's subject id, as a string, like every `created_by` on the platform —
    #: stated explicitly here rather than left to the mixin because it is the point of the row.
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False)
    #: When the decision was taken, which is not always when the row was written.
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ims_decisions_org_document", "organization_id", "ims_document_id"),
        Index("ix_ims_decisions_org_decided", "organization_id", "decided_at"),
    )


class ItcLedgerEntry(Base, TenantBase):
    """One posting to the input-tax-credit ledger — append-only, and evidence, like
    `einvoice_filings` and `ims_decisions`.

    Three kinds of row (`domain.ITC_POSTING_KINDS`), and the balance is never a column: it is
    `SUM(amount_minor)` per (registration, tax head, period), carried forward — the same shape as
    a prepaid credit pack's draws. `amount_minor` is signed so that sum needs no case split:
    positive on an `accrual`, negative on a `reversal` or a `utilisation`.

    * **accrual** — credit from an IMS document this organization accepted, posted by
      `services.record_ims_decision` in the same transaction as the decision that accepted it.
      Always positive — see the sign convention below.
    * **reversal** — credit withdrawn: an accepted document's status later moving away from
      `accepted`, or a supplier credit note accepted against credit already claimed (or, if it is
      the *first* thing accepted against that document, a negative entry with nothing preceding it
      to reverse — still a `reversal` by amount, since `amount_minor`'s sign is what the kind
      answers for, not the narrative). (From July 2022's Table 4 shape, a blocked s. 17(5) category
      is read informationally at GSTR-3B assembly time rather than posted here at all.) What
      `services.record_ims_decision` actually posts is **the delta** between what should now be
      outstanding for a document and what already is — covering a plain accept, a plain un-accept,
      *and* a later decision that re-classifies an already-accepted document under a different
      `expense_category`, with one formula rather than three. It can therefore never withdraw more
      than was actually accrued: the delta is computed against the ledger's own current standing,
      not recomputed independently.
    * **utilisation** — credit consumed against output liability, posted by `services.file_return`
      in the same transaction as filing a `gstr3b`, one row per (head, counter-head) the statutory
      utilisation order actually applied — see `domain.utilise_itc`.

    The document's own decision is locked `FOR UPDATE` before any of this runs (see
    `selectors.get_ims_document`), and the registration row is locked before a `gstr3b` is
    generated or filed (see `services.registration_for`) — both because two of these three kinds
    read "what is already posted" before deciding what to post next, and two callers racing that
    read-then-write must serialise on the row rather than both acting on the same stale answer.

    `source_ref` is the idempotency key: a unique index on `(organization_id, kind, source_ref)`
    means the row a retried write would produce collides rather than doubles the ledger — the same
    shape `ims_documents.source_ref` gives the IMS ingest.
    """

    __tablename__ = "itc_ledger_entries"

    gst_registration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.gst_registrations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The return period this posting is **attributed to** for GSTR-3B purposes, `YYYY-MM` — the
    #: *claim* period, which `selectors.claim_period_for` may advance past `source_period` when a
    #: decision arrives after its own period's `gstr3b` has already been filed (the same
    #: "amendment in a later period" shape a credit note against a filed invoice already has).
    #: `selectors.itc_period_movement`/`itc_brought_forward` filter on this column, because it is
    #: what decides which return sees the posting.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    #: The period the *source event itself* belongs to — the IMS document's own reported period
    #: for an accrual or a reversal, or the return's own period for a utilisation. Kept apart from
    #: `period` purely for audit: "what period was this really about" must stay answerable even
    #: after a late decision has pushed the posting into a later one.
    source_period: Mapped[str] = mapped_column(String(7), nullable=False)
    #: igst | cgst | sgst | cess — `domain.ITC_TAX_HEADS`. `cess` is accepted so the schema does
    #: not need to change shape the day a cess-bearing document exists; nothing posts one today.
    tax_head: Mapped[str] = mapped_column(String(10), nullable=False)
    #: accrual | reversal | utilisation — `domain.ITC_POSTING_KINDS`.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Signed: positive on an accrual, negative on a reversal or a utilisation, so the balance is
    #: a plain `SUM` with no case split. `ck_itc_ledger_entries_amount_sign` holds the database to
    #: that convention rather than trusting every future caller to remember it.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ims_decision | gst_return — `domain.ITC_SOURCE_TYPES`. What produced this row.
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The id of the row named above — an `ims_decisions.id` or a `gst_returns.id`. A plain UUID
    #: rather than a foreign key: the two source tables are a union, so no single `ForeignKey`
    #: could name both, and enforcing referential integrity against whichever one applies is what
    #: `source_ref`'s uniqueness already does in practice.
    source_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The idempotency key: `f"{source_type}:{source_id}:{tax_head}"`. Unique per `kind` — see
    #: `__table_args__` — so a retried post collides instead of doubling the ledger.
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The principal's subject id — the person who accepted or rejected the document, or filed the
    #: return, whose action this row is the financial consequence of.
    posted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "kind", "source_ref", name="uq_itc_ledger_entries_org_kind_source"
        ),
        Index(
            "ix_itc_ledger_entries_org_registration_period",
            "organization_id",
            "gst_registration_id",
            "period",
        ),
        Index("ix_itc_ledger_entries_org_kind", "organization_id", "kind"),
        # Belt and braces beside `domain.ITC_TAX_HEADS`/`ITC_POSTING_KINDS`/`ITC_SOURCE_TYPES`: an
        # application-layer typo in a literal string must not be able to write a row a report then
        # silently drops on the floor by never matching any filter.
        # `name=` is the short suffix, not the full constraint name — the metadata's own naming
        # convention (`libs/platform-core/src/platform_core/models.py`) already prepends
        # `ck_<table>_`, so passing the full name here would double it.
        CheckConstraint("tax_head IN ('igst', 'cgst', 'sgst', 'cess')", name="tax_head"),
        CheckConstraint("kind IN ('accrual', 'reversal', 'utilisation')", name="kind"),
        CheckConstraint(
            "source_type IN ('ims_decision', 'gst_return')",
            name="source_type",
        ),
        # The sign convention `amount_minor`'s own docstring states, held at the database: an
        # accrual only ever adds, a reversal or a utilisation only ever takes away — including a
        # credit note's first-ever negative entry against a document nothing was accrued for yet,
        # which is a `reversal` by amount even though nothing preceded it to reverse. Zero is
        # deliberately excluded from every branch — `services._post_itc_entries` never posts a
        # zero-amount row, so a zero here would already be a bug the constraint should catch too.
        CheckConstraint(
            "(kind = 'accrual' AND amount_minor > 0) OR "
            "(kind IN ('reversal', 'utilisation') AND amount_minor < 0)",
            name="amount_sign",
        ),
    )


class GstReturn(Base, TenantBase):
    """One period's return for one registration — the record, and its frozen snapshot.

    **One row per (registration, period, type)**, which is the grain the obligation itself has: a
    taxpayer files GSTR-1 once for August under one GSTIN, and a second row for the same three
    would be a second filing, not a second version.

    The row's *snapshot* — `sections` and the totals beside it — is frozen by a trigger the moment
    the return is **filed**. That is the whole design: a filed return is what was reported, so an
    invoice corrected in September must show up as a **difference** against August's snapshot
    rather than by August quietly becoming a different return. Only `status`, `filed_at` and `arn`
    may move afterwards, and only forward. Before filing the snapshot may still be re-assembled —
    nothing has been reported to anybody yet, and looking at a return before sending it is the
    point of generating one.

    The status trail is not a column's history — it is `gst_return_transitions`, appended to and
    never rewritten, so "when did this become filed, and who said so" is a query.
    """

    __tablename__ = "gst_returns"

    #: The registration this return is filed under. Same-schema reference; the GSTIN beside it is
    #: a **snapshot**, for the same reason `invoices.seller_gstin` is one: a return already filed
    #: keeps naming the number it was filed under even if the registration is later corrected.
    gst_registration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.gst_registrations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    #: The tax period, `YYYY-MM`. A string rather than a date because it is an identifier the
    #: portal echoes back, not a point in time to do arithmetic on.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    #: gstr1 | gstr7 — `domain.GST_RETURN_TYPES`.
    return_type: Mapped[str] = mapped_column(String(10), nullable=False, default="gstr1")
    #: draft | generated | filed. Forward only; `filed` is terminal.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    #: The snapshot: every section, exactly as assembled, in the shape `schemas.Gstr1Sections`
    #: validates. JSONB because a return is one atomic declaration — splitting it across tables
    #: would let half of it be replaced.
    sections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: The one-line declaration, denormalised out of the snapshot so the list page does not have
    #: to open it. Foots to the sections, and a test asserts that it does.
    total_taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    document_count: Mapped[int] = mapped_column(nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(nullable=False, default=0)

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The portal's Acknowledgement Reference Number for the filing. The one fact about a return
    #: that only exists after it has left this platform.
    arn: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "gst_registration_id",
            "period",
            "return_type",
            name="uq_gst_returns_org_registration_period_type",
        ),
        Index("ix_gst_returns_org_period", "organization_id", "period"),
        Index("ix_gst_returns_org_status", "organization_id", "status"),
    )


class GstReturnTransition(Base, TenantBase):
    """One movement of one return between statuses — append-only.

    The trail the return's `status` column is only the head of. A filing is a claim about a tax
    period made to a government, and "who moved this to filed, when, and what ARN came back" is
    evidence rather than telemetry — so it is a row per transition, refused UPDATE and DELETE by
    a trigger, exactly as `einvoice_filings` and `ims_decisions` are.
    """

    __tablename__ = "gst_return_transitions"

    gst_return_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.gst_returns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Null on the first transition — a return that did not exist has no status to leave.
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The ARN, on the transition to `filed`. Kept here as well as on the header so the evidence
    #: row is self-contained.
    arn: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    #: The totals as they stood at this transition, so the trail records *what* was filed and not
    #: merely that something was.
    total_taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transitioned_by: Mapped[str] = mapped_column(String(64), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_gst_return_transitions_org_return", "organization_id", "gst_return_id"),
        Index("ix_gst_return_transitions_org_at", "organization_id", "transitioned_at"),
    )


class EwayBill(Base, TenantBase):
    """One attempt to raise an e-Way bill for one document — append-only.

    The same shape as `einvoice_filings`, because it is the same kind of fact: a trip to a
    government portal, and what came back. One row per attempt, never mutated; the document's
    current position is its newest row.

    What is different is that an e-Way bill **expires**. An IRN is permanent once issued; a bill
    is a licence for goods to be on a road for a stated number of days, and `expired` is not a
    status anybody writes — the table is append-only, so nothing can update a row when the clock
    passes it. It is derived on read from `valid_until`, which is itself derived from the stored
    `distance_km` and `valid_from` by `domain.eway_valid_until`. Storing the deadline **and**
    keeping it recomputable from the inputs is deliberate: a screen and a checkpoint must agree,
    and the only way to be sure they do is for both to be the same arithmetic over the same two
    stored values.
    """

    __tablename__ = "eway_bills"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.invoices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: pending | generated | failed | cancelled | expired | submitting | cancelling —
    #: `domain.EWAY_STATUSES`. The last two are claim rows (D3), written before the portal call.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The portal's 12-digit bill number. Present only once a bill exists.
    eway_bill_no: Mapped[str | None] = mapped_column(String(16))

    #: road | rail | air | ship.
    transport_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="road")
    #: Approximate road distance in kilometres — an **input** to validity, so it is stored rather
    #: than the deadline alone.
    distance_km: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Required for road movement; the portal refuses without it.
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    #: The transporter's GSTIN or 15-character transporter id, required when they move it.
    transporter_id: Mapped[str | None] = mapped_column(String(20))
    transport_doc_no: Mapped[str | None] = mapped_column(String(40))
    transport_doc_date: Mapped[date | None] = mapped_column(Date)
    #: Over-dimensional cargo buys a tenth of the distance per day of validity.
    over_dimensional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The consignment value the threshold was tested against, snapshotted so the decision to
    #: raise a bill can be explained without re-reading a document that may since be corrected.
    consignment_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_code: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_eway_bills_org_invoice", "organization_id", "invoice_id"),
        Index("ix_eway_bills_org_attempted", "organization_id", "attempted_at"),
        # A bill number is unique nationally; within a tenant it must never be duplicated.
        Index(
            "uq_eway_bills_org_number",
            "organization_id",
            "eway_bill_no",
            unique=True,
            postgresql_where=text("eway_bill_no IS NOT NULL"),
        ),
    )


class WithholdingRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One version of one withholding head — **global platform vocabulary**, like `tax_rates`.

    Carries no `organization_id` and is left UNPOLICIED for the same reason the GST rate schedule
    is: 2 % under GST s. 51 and 0.1 % under s. 194Q are the law of one country, identical for
    every tenant, and row-level security over a statute would be meaningless.

    Versioned by `effective_from`, and versioned for a sharper reason than the rate bands are: a
    withholding is computed *per document* and stored, so a rate change that edited this row
    would silently restate every figure any recomputation touched. A change is a new row; the
    resolution is against the **document's** date; and the resolved rate is snapshotted onto the
    register row beside the id of the version it came from.
    """

    __tablename__ = "withholding_rates"

    #: gst_tds_51 | itax_194q | itax_206c_1h — `domain.WITHHOLDING_CODES`.
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: A percentage, not money — a rate, so `Numeric` is right where a float never is.
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    #: The value the head bites above, in minor units. How it is measured — per supply or against
    #: the financial year's running total — is `basis`.
    threshold_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: supply | aggregate — `domain.WITHHOLDING_BASES`.
    basis: Mapped[str] = mapped_column(String(20), nullable=False, default="supply")
    jurisdiction: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint(
            "jurisdiction", "code", "effective_from", name="uq_withholding_rates_version"
        ),
        Index("ix_withholding_rates_code_effective", "jurisdiction", "code", "effective_from"),
    )


class InvoiceWithholding(Base, TenantBase):
    """Tax withheld on one document under one head — append-only.

    The register GSTR-7 reconciliation is done against, and a TDS certificate is claimed from.
    Append-only at the database, by trigger, because it is evidence about money somebody else
    already paid to a tax authority on this organization's behalf: a figure that can be rewritten
    is a figure an auditor cannot use.

    The rate and threshold are **snapshots** beside `withholding_rate_id`. Recomputing this row
    from today's schedule would restate history the first time a rate changed, which is exactly
    what versioning the schedule exists to prevent — so the answer is stored, not derived, and
    the version it came from is named so it can still be explained.
    """

    __tablename__ = "invoice_withholdings"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.invoices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: gst_tds_51 | itax_194q | itax_206c_1h.
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    #: deducted | collected — which way the money went.
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The version of the schedule that produced this figure, plus the two numbers it supplied.
    withholding_rate_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance.withholding_rates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    threshold_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: supply | aggregate — carried so the register explains *why* the basis is what it is.
    basis: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What the rate was applied to: the whole taxable value for a per-supply head, this
    #: document's share of the excess for an aggregate one.
    basis_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: GST s. 51 splits across heads exactly as the levy does. Income tax does not, so these stay
    #: zero there rather than being given a meaning they do not have.
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: The counterparty as the register names them: the deductor under s. 51 and 194Q, the buyer
    #: TCS was collected from under 206C(1H). Snapshotted like every other GSTIN here.
    counterparty_gstin: Mapped[str | None] = mapped_column(String(15))
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: The document date the rate was resolved against — the reason a later rate change cannot
    #: reach this row.
    resolved_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: The return period this belongs to, `YYYY-MM`, denormalised so the report is one query.
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    __table_args__ = (
        # One head bites once per document. A second deduction under the same section is a
        # correction, and a correction to a document is a new document.
        UniqueConstraint(
            "organization_id", "invoice_id", "code", name="uq_invoice_withholdings_invoice_code"
        ),
        Index("ix_invoice_withholdings_org_period", "organization_id", "period"),
        Index("ix_invoice_withholdings_org_code", "organization_id", "code"),
        Index("ix_invoice_withholdings_org_invoice", "organization_id", "invoice_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
