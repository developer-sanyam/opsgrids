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

`tax_rates` is the one deliberately different table: it carries **no `organization_id`** and is
left UNPOLICIED. A GST rate schedule is platform vocabulary — the same 5/12/18/28 % bands for
every tenant in India — not tenant data, so RLS on it would be meaningless (see the RLS
migration, which lists it as exempt alongside `processed_events`).

`einvoice_filings` is append-only at the *database*, not merely by convention: a trigger refuses
UPDATE and DELETE. A privilege REVOKE would not survive `apply_schema_grants`, which re-grants
DML on every table at the end of every migration run — see the migration for the whole reasoning.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    #: pending | filed | failed | cancelled.
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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
