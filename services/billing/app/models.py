"""Tables owned by the billing service — the financial record of truth (ADR-0004 §1, §3).

Everything here lives in the `billing` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). A `subscription_id`, a `plan_version_id` or a `reversal_of` is a plain
indexed UUID, never a constraint that crosses a schema boundary — that omission is what keeps a
later database-per-service split a config change rather than a rewrite.

Three immutability rules are load-bearing and are enforced **below** the application, because a
financial record the application can quietly rewrite is not a record you can defend in a dispute
(ADR-0000 §9, ADR-0004 §3):

* **A finalized invoice is frozen.** It is written already `finalized` (there is no draft →
  finalize edit in this service), and from that moment the only change the database permits is
  the single legal transition `finalized → void`. A `BEFORE UPDATE` trigger raises on anything
  else (see the freeze migration), `services.py` refuses it first with a usable message, and
  `DELETE` is revoked outright.
* **Invoice lines are append-only.** They foot exactly to their invoice's `total_minor`
  (`Money.allocate` guarantees the parts sum to the whole), and `UPDATE`/`DELETE` are revoked so
  a line cannot be edited after the fact — a correction is a new invoice, never a mutated line.
* **The credit ledger is append-only and its balance is never stored.** A grant, a consumption,
  a reversal and an expiry are all rows; the balance available to an organization at an instant
  is a `SUM` over the rows whose `[valid_from, valid_until)` window covers that instant, computed
  in the database and never cached in a column (ADR-0004 §3). `UPDATE`/`DELETE` are revoked.

Money is integer minor units with an ISO-4217 currency (ADR-0000 §8); a usage rate that needs
sub-minor precision is `BIGINT` micros of a major unit (`platform_core.money`). No floats, no
`Numeric` on any money column. `quantity` is a measured count, not money, so it is the one
`Numeric` here — a usage line can legitimately bill 1.5 GB.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "billing"

#: Invoice lifecycle. `draft` is reserved for a future draft → finalize flow (part of the
#: deferred billing-run orchestration); this service writes an invoice straight to `finalized`
#: and the only legal move from there is `void`.
INVOICE_DRAFT = "draft"
INVOICE_FINALIZED = "finalized"
INVOICE_VOID = "void"

#: Credit ledger entry kinds. Every one is a row; none mutates another.
CREDIT_GRANT = "grant"
CREDIT_CONSUME = "consume"
CREDIT_REVERSAL = "reversal"
CREDIT_EXPIRY = "expiry"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Invoice(Base, TenantBase):
    """One invoice for one organization — the addressable financial document.

    Written already `finalized`, with its lines footing exactly to `total_minor`. From there it
    is frozen: the freeze trigger permits only `finalized → void`, and `DELETE` is revoked, so
    the row a customer was billed against can never be silently re-priced (ADR-0000 §9).
    """

    __tablename__ = "invoices"

    #: The subscription this invoice bills, when there is one. A plain UUID into the
    #: subscription service's schema — never a foreign key across the boundary.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Human-facing invoice number, unique within an organization.
    number: Mapped[str] = mapped_column(String(60), nullable=False)

    #: `draft` | `finalized` | `void`. See the module docstring — an invoice is born
    #: `finalized` here, and `void` is the one legal transition out.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=INVOICE_FINALIZED)

    #: ISO-4217. Every line on this invoice, and any credit applied to it, must agree.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: Integer minor units, equal to the exact sum of this invoice's lines (ADR-0000 §8).
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: The billing period this invoice covers.
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set on the one legal post-finalize transition. The row is otherwise unchanged — voiding
    #: marks the document uncollectible, it does not rewrite what was billed.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(200))

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        order_by="InvoiceLine.created_at",
    )

    __table_args__ = (
        # Under row-level security the policy qual is ANDed into every query, so every index
        # leads with the tenant column (ADR-0002) — otherwise it scans the whole platform.
        UniqueConstraint("organization_id", "number", name="uq_invoices_org_number"),
        Index("ix_invoices_org_state", "organization_id", "state"),
        Index("ix_invoices_org_subscription", "organization_id", "subscription_id"),
        Index("ix_invoices_org_period", "organization_id", "period_start"),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("state in ('draft', 'finalized', 'void')", name="state"),
    )


class InvoiceLine(Base, TenantBase):
    """One priced line of an invoice. **Append-only** (`UPDATE`/`DELETE` revoked).

    `plan_version_id` records the catalog version this line was priced against — a plain UUID,
    no FK — so an invoice stays reconstructable years later even as the catalogue moves on
    (ADR-0004 §2). The lines of an invoice sum exactly to its `total_minor`.
    """

    __tablename__ = "invoice_lines"

    #: The invoice this line belongs to. A real foreign key — it stays inside the `billing`
    #: schema, and only cross-schema foreign keys are banned (ADR-0000 §10).
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.invoices.id", name="fk_invoice_lines_invoice_id"),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(String(300), nullable=False)
    #: A measured count, not money — the one `Numeric` here, so a usage line can bill 1.5 GB.
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=Decimal(1))
    #: Micros of a major unit (`platform_core.money`) — sub-minor precision, rounded once when
    #: the line was priced. NULL for a line quoted directly as a minor-unit amount.
    unit_price_micros: Mapped[int | None] = mapped_column(BigInteger)
    #: This line's money, integer minor units (ADR-0000 §8).
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The catalog plan version this line was priced against, for reconstruction. Plain UUID.
    plan_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    __table_args__ = (Index("ix_invoice_lines_org_invoice", "organization_id", "invoice_id"),)


class CreditLedgerEntry(Base, TenantBase):
    """One append-only entry in the credit ledger. **Balance is derived, never stored.**

    A grant is a positive-amount row with a validity window; a consumption is a negative row; a
    reversal is a row that negates the one it names in `reversal_of`; an expiry is a row (or, for
    a time-boxed grant, simply the grant's window closing). The balance available to an
    organization at an instant is a `SUM` over the rows whose `[valid_from, valid_until)` covers
    it — a query, at any point in time, historically included (ADR-0004 §3). `UPDATE`/`DELETE`
    are revoked: nothing is mutated to spend, revoke or expire a credit.
    """

    __tablename__ = "credit_ledger"

    #: `grant` | `consume` | `reversal` | `expiry`. What this row records; it never changes
    #: another row.
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Signed integer minor units — positive adds credit, negative spends it (ADR-0000 §8).
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)

    #: The window this entry counts inside. `valid_from` defaults to now; `valid_until` NULL is
    #: open-ended (a consumption or a reversal, which always counts once it has happened). A
    #: time-boxed grant sets `valid_until`, and drops out of the balance the instant it passes.
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The entry this one reverses, for a `reversal`. A plain UUID into this same table; the
    #: service checks it, so no self-FK is needed and none is added.
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        # The balance query filters org, currency and the window, so those lead the index.
        Index("ix_credit_ledger_org_currency_window", "organization_id", "currency", "valid_from"),
        Index("ix_credit_ledger_org_valid_until", "organization_id", "valid_until"),
        Index("ix_credit_ledger_org_reversal_of", "organization_id", "reversal_of"),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint(
            "entry_type in ('grant', 'consume', 'reversal', 'expiry')", name="entry_type"
        ),
    )


class TaxRate(Base):
    """A statutory tax rate, versioned by `effective_from`. **Global and un-policied.**

    Deliberately not tenant-scoped, and deliberately carrying no row-level security policy — a
    VAT/GST rate is platform reference data, the same statutory fact for every customer in a
    jurisdiction, exactly like `catalog.plans` or `product.products` (ADR-0002 §5, "platform
    vocabulary must NOT get an org policy"). Re-running March's billing must use March's rate,
    which is why the table is versioned rather than mutated (ADR-0000 §9): a new rate is a new
    row with a later `effective_from`, never an edit.

    Only the *table* is modelled now. **Which** rate applies to a given customer — jurisdiction
    resolution, place-of-supply, exemptions — is explicitly deferred (ADR-0004 "Open: Tax
    computation and jurisdiction"), so this service reads no tax rate yet. Modelling the shape now
    keeps that later work an additive migration rather than a schema change.
    """

    __tablename__ = "tax_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: An ISO country (or finer) code the rate applies in — deferred resolution, so nullable.
    jurisdiction: Mapped[str | None] = mapped_column(String(20))
    #: Machine name for the tax: `gst`, `vat`, `gst_igst`.
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The rate in basis points (10000 bps = 100%), so it composes with `Money.percent_bps`
    #: without a float ever appearing.
    rate_bps: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The date this rate takes effect. A later row supersedes an earlier one for the same code.
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("code", "jurisdiction", "effective_from", name="uq_tax_rates_code_from"),
        Index("ix_tax_rates_code_from", "code", "effective_from"),
        CheckConstraint("rate_bps >= 0", name="rate_bps_nonneg"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
