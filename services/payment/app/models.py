"""Tables owned by the payment service — mandates, charges, refunds, and the webhook inbox.

Everything here lives in the `payment` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). A charge points at a billing invoice and a subscription by plain UUID,
never a constraint that crosses a schema boundary.

`payment` is deliberately the most isolated commerce service (ADR-0004 §1, §6): it carries the
provider-specific truth — *what was actually collected*, and the messy reality behind it — so a
provider's constraints never leak into the rest of the platform. The seam is built against the
**more constrained** provider (Razorpay): mandate caps, multi-day eMandate activation, and no
native idempotency key, so a later Stripe adapter fits inside the same shape (see
`app/providers/`).

Four business tables, and the tenancy/immutability discipline on each is a security decision
written out per table (ADR-0002 §5):

* **`mandates`** — a customer's standing authorization to be debited, tenant-scoped. Carries the
  Razorpay cap (`max_amount_minor`) and the eMandate activation moment (`activated_at`), which is
  days after creation for a multi-day mandate.
* **`charges`** — one collection attempt against an invoice, tenant-scoped. `idempotency_key` is
  **platform-supplied** (Razorpay has no native one, ADR-0004 §6): unique per organization, so
  raising the same charge twice is one row. Mutable through its small state machine
  (`created → authorized → captured | failed`).
* **`refunds`** — **append-only** (ADR-0000 §9). A correction is a new row; a refund is never
  edited or deleted. Enforced below the application by a trigger (see the append-only migration).
* **`provider_events`** — the **append-only** webhook inbox and the reconciliation source of
  truth. Idempotent on `provider_event_id` (unique per organization), so a provider re-delivering
  a webhook lands exactly one row and reconciles exactly once.

The platform tables (`outbox`, `audit_logs`, `processed_events`) come from the scaffold.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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
from platform_core.models import TenantBase

SCHEMA = "payment"

# --- charge states -------------------------------------------------------------------
#: A charge is born `created`, moves to `authorized` when the provider accepts it, and reaches a
#: terminal `captured` or `failed` when the money moves (or does not). Kept as data here and
#: asserted by the pure state machine in `domain.py`; the closed set is pinned by a CHECK too.
CHARGE_CREATED = "created"
CHARGE_AUTHORIZED = "authorized"
CHARGE_CAPTURED = "captured"
CHARGE_FAILED = "failed"
CHARGE_STATES = (CHARGE_CREATED, CHARGE_AUTHORIZED, CHARGE_CAPTURED, CHARGE_FAILED)

# --- mandate states ------------------------------------------------------------------
#: A Razorpay eMandate is `created`, `pending` while the bank confirms (multi-day), `active` once
#: it can be debited, and `revoked` when the customer or bank withdraws it.
MANDATE_CREATED = "created"
MANDATE_PENDING = "pending"
MANDATE_ACTIVE = "active"
MANDATE_REVOKED = "revoked"
MANDATE_STATES = (MANDATE_CREATED, MANDATE_PENDING, MANDATE_ACTIVE, MANDATE_REVOKED)

# --- refund states -------------------------------------------------------------------
REFUND_CREATED = "created"
REFUND_PROCESSED = "processed"
REFUND_FAILED = "failed"
REFUND_STATES = (REFUND_CREATED, REFUND_PROCESSED, REFUND_FAILED)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Mandate(Base, TenantBase):
    """A customer's standing authorization to be debited by a provider.

    Shaped to Razorpay's constraints (ADR-0004 §6): a mandate carries a **cap**
    (`max_amount_minor`) no single debit may exceed, and an **activation moment**
    (`activated_at`) that is days after creation for a multi-day eMandate — the mandate exists but
    cannot be debited until the bank confirms it. Both are first-class here because an interface
    that ignored them would be one Razorpay cannot satisfy.
    """

    __tablename__ = "mandates"

    #: Which processor holds the mandate — `razorpay` today. A string, never an enum in the DB, so
    #: adding a provider is not a migration.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's id for the mandate. Null until the provider assigns one (the stub returns a
    #: deterministic fake); unique per organization once set.
    provider_mandate_id: Mapped[str | None] = mapped_column(String(120))

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=MANDATE_CREATED)

    #: The Razorpay cap: no debit under this mandate may exceed it. Integer minor units, BIGINT
    #: because a mandate cap can be large (ADR-0000 §8 — no floats, ever).
    max_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ISO-4217, three chars. One currency per mandate.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    #: When the eMandate became debitable — days after creation for a multi-day mandate, NULL
    #: while it is still `created`/`pending`.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("max_amount_minor >= 0", name="max_amount_non_negative"),
        CheckConstraint("state in ('created', 'pending', 'active', 'revoked')", name="state"),
        # Unique per organization when present, so a provider id cannot collide across a tenant's
        # mandates. Partial (WHERE NOT NULL) because many mandates share NULL before assignment.
        Index(
            "uq_mandates_org_provider_mandate_id",
            "organization_id",
            "provider_mandate_id",
            unique=True,
            postgresql_where=text("provider_mandate_id IS NOT NULL"),
        ),
        # Leads with the tenant: the RLS qual is ANDed into every query.
        Index("ix_mandates_org_state", "organization_id", "state"),
    )


class Charge(Base, TenantBase):
    """One collection attempt against a billing invoice. Tenant-scoped, mutable through its states.

    The `idempotency_key` is **platform-supplied** because Razorpay has no native one (ADR-0004
    §6): it is unique per organization, and the charge-create path inserts `ON CONFLICT DO
    NOTHING` against it, so raising the same charge twice — a retried billing run, a redelivered
    `commerce.invoice.finalized` — yields exactly one row and one collection attempt.

    `invoice_id`, `subscription_id` and `product_key` are how a *failure* finds what to suspend
    without payment reaching into another schema: they travel out on `commerce.payment.failed`,
    and the subscription service turns that into a suspension (ADR-0004 §5). They are plain
    references — no cross-schema foreign key (ADR-0000 §10).
    """

    __tablename__ = "charges"

    #: The billing invoice this charge collects, by plain UUID (billing owns `invoices`; no FK
    #: crosses the schema boundary). Nullable for a charge not tied to a specific invoice.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The subscription whose entitlement rides on this charge, by plain UUID. A failed charge
    #: suspends exactly this subscription (the suspend loop, ADR-0004 §5). Nullable: not every
    #: charge is for a subscription.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The product the subscription entitles — the same string the gateway routes. Carried so the
    #: failure event is self-describing without a lookup. A plain string, no FK.
    product_key: Mapped[str | None] = mapped_column(String(40))

    #: The mandate this charge debits, when one was named. `create_charge` refuses the debit
    #: unless the mandate exists in this organization, is `active`, matches the currency, and
    #: the amount is within its cap — and records which mandate authorized the debit here, so
    #: the authority a debit ran under is never a matter of reconstruction. Intra-schema FK,
    #: which the boundary rule allows (ADR-0000 §10 forbids only cross-schema keys).
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.mandates.id", name="fk_charges_mandate_id"),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's payment id. Null until the provider accepts the charge (the stub returns a
    #: deterministic fake); unique per organization once set. The webhook reconciles against it.
    provider_payment_id: Mapped[str | None] = mapped_column(String(120))

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=CHARGE_CREATED)
    #: Why a charge failed, straight from the provider — for support and the audit trail.
    failure_reason: Mapped[str | None] = mapped_column(String(200))

    #: Platform-supplied idempotency key, unique within the organization. Two creates with the
    #: same key are one charge — the property the create path is built around.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    #: The provider Order this charge's payment is created against. Razorpay has no idempotency
    #: key of its own (ADR-0004 §6): the Order, created with `receipt` = `idempotency_key` and
    #: looked up by it before any create (`services.call_provider_charge`), is what lets a resumed
    #: call find the payment a crashed one already made. Recorded at finalize.
    provider_order_id: Mapped[str | None] = mapped_column(String(120))

    # --- who may call the provider, and when (D3) -------------------------------------
    #: Calls claimed so far, the stager's own included. The stuck-charge sweep stops at
    #: `Settings.charge_max_attempts` and fails the charge, so dunning proceeds.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Earliest moment the sweep may claim another call — the bounded backoff between attempts.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: While in the future, a call is in flight and nobody else may make one: a caller's HTTP
    #: retry of the same key, or the sweep. Claimed by a conditional `UPDATE … RETURNING`.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    refunds: Mapped[list[Refund]] = relationship(back_populates="charge")

    __table_args__ = (
        # Idempotency enforced in the database, relied on via ON CONFLICT rather than a
        # read-then-write race — exactly as metering does for usage ingest.
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_charges_org_idempotency_key"
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        CheckConstraint("state in ('created', 'authorized', 'captured', 'failed')", name="state"),
        # The webhook reconciles a charge by its provider payment id, under a bound tenant.
        Index(
            "uq_charges_org_provider_payment_id",
            "organization_id",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index("ix_charges_org_state", "organization_id", "state"),
        Index("ix_charges_org_invoice", "organization_id", "invoice_id"),
        Index(
            "uq_charges_org_provider_order_id",
            "organization_id",
            "provider_order_id",
            unique=True,
            postgresql_where=text("provider_order_id IS NOT NULL"),
        ),
        # The stuck-charge sweep's discovery: only unresolved charges, oldest first.
        Index(
            "ix_charges_unresolved",
            "created_at",
            postgresql_where=text("state = 'created' AND provider_payment_id IS NULL"),
        ),
    )


class Refund(Base, TenantBase):
    """A refund against a charge. **Append-only** (ADR-0000 §9).

    A refund is never edited or deleted: a correction is a new row, and the database enforces that
    with a `BEFORE UPDATE OR DELETE` trigger (see the append-only migration), the same discipline
    the credit ledger and audit trail use. It references its charge by an intra-schema foreign key
    (allowed — the boundary rule forbids only *cross-schema* keys, ADR-0000 §10).
    """

    __tablename__ = "refunds"

    charge_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.charges.id", name="fk_refunds_charge_id"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(120))

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=REFUND_CREATED)
    reason: Mapped[str | None] = mapped_column(String(200))

    charge: Mapped[Charge] = relationship(back_populates="refunds")

    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        # Zero refunds nothing and negative would mint money; both are refused in the service
        # too, but a CHECK is the layer a stray script cannot argue with.
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("state in ('created', 'processed', 'failed')", name="state"),
        Index("ix_refunds_org_charge", "organization_id", "charge_id"),
        # Mirrors charges/mandates: one local row per provider refund. Without it a replayed
        # provider response could be recorded twice, and the append-only trigger would make
        # the duplicate uncorrectable in place.
        Index(
            "uq_refunds_org_provider_refund_id",
            "organization_id",
            "provider_refund_id",
            unique=True,
            postgresql_where=text("provider_refund_id IS NOT NULL"),
        ),
    )


class ProviderEvent(Base, TenantBase):
    """The webhook inbox and the reconciliation source of truth. **Append-only**.

    Every provider callback that verifies its signature is recorded here *before* it is acted on,
    idempotently: the unique `(organization_id, provider_event_id)` means a provider re-delivering
    the same webhook — which they do, freely — lands exactly one row and reconciles exactly once.
    The row is never mutated except to stamp `processed_at` once… which is itself a mutation, so
    instead reconciliation is recorded by the charge it updates and by the outbox event it emits,
    and this table stays strictly insert-only under the append-only trigger.

    Storing the raw `payload` and `signature` makes reconciliation replayable and auditable: if a
    projection is ever found wrong, the truth is the provider's own bytes, kept here unaltered.
    """

    __tablename__ = "provider_events"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's unique event id (Razorpay's `x-razorpay-event-id`). Idempotency rests on it.
    provider_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The provider's event name, e.g. `payment.captured`, `payment.failed`.
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)

    #: The charge this event reconciled against, resolved from the payload. Null if none matched.
    charge_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: The raw provider body, kept verbatim — the replayable, auditable truth.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The HMAC signature the provider sent, kept for audit.
    signature: Mapped[str | None] = mapped_column(String(256))

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider_event_id", name="uq_provider_events_org_event_id"
        ),
        Index("ix_provider_events_org_charge", "organization_id", "charge_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
