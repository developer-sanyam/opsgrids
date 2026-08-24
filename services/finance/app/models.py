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
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
    #: A percentage, not money — a rate, so `Numeric` is correct where floats never are.
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

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
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
