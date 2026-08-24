"""Tables owned by the subscription service — the per-customer commercial lifecycle.

This is the state machine ADR-0004 §4 calls the place *where entitlement is decided*. Catalog
holds the immutable, global definition of what can be sold; subscription holds a **mutable
pointer** into it — one customer's place in the life of what they bought. The seam that matters
is exactly that split (ADR-0004 §1): a customer's life changes constantly, the version they are
pinned to does not change under them.

Three tables, and the discipline on each is load-bearing:

* **`subscriptions`** carries the pinned `plan_version_id` (ADR-0000 §9). Re-pointing it — a
  change-plan — never edits the catalog version and never loses the previous pin: the old value
  is preserved on the transition row, so "what was this customer on, and when?" stays a `SELECT`.
* **`subscription_items`** are the priced components of the pin, referenced by the catalog
  `price_id` (a plain UUID — no cross-schema FK, ADR-0000 §10).
* **`subscription_transitions`** is **append-only** (ADR-0000 §9): every accepted move through the
  state machine writes one, and it is never updated or deleted. `REVOKE UPDATE, DELETE` at the DB
  is the net under that (see the append-only migration); the service layer never mutates one.

Everything is tenant-scoped `TenantBase` with row-level security — unlike catalog's global
definitional tables, a subscription belongs to exactly one organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "subscription"

#: The lifecycle states. Kept as data here and asserted by the state machine in `domain.py`;
#: the set is small and closed, so a `CHECK` constraint pins it at the database too.
STATE_TRIAL = "trial"
STATE_ACTIVE = "active"
STATE_PAST_DUE = "past_due"
STATE_SUSPENDED = "suspended"
STATE_CANCELLED = "cancelled"

STATES = (STATE_TRIAL, STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Subscription(Base, TenantBase):
    """One organization's subscription to one product, pinned to a catalog plan version.

    The pin (`plan_version_id`) is what keeps a customer's price stable while the catalogue
    evolves around them (ADR-0000 §9): editing a plan clones a new version in catalog, and this
    row is untouched until an explicit, audited change-plan re-points it. Cancellation and
    suspension set `state`; the row is never deleted, because "when did they have this, and on
    what price?" is the first question any billing dispute asks.
    """

    __tablename__ = "subscriptions"

    #: The product this subscription entitles — the same string the gateway routes and the
    #: `ent` claim carries. A plain string, never an FK (ADR-0000 §10).
    product_key: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The immutable catalog version this subscription is pinned to. A plain UUID resolved from
    #: catalog at subscribe/change-plan time — never a cross-schema foreign key.
    plan_version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    #: `trial`, `active`, `past_due`, `suspended`, `cancelled`. The authority for whether the
    #: product-service `Activation` entitles — the projection consumer reads the state off the
    #: `commerce.subscription.*` event this service emits on every transition.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=STATE_TRIAL)

    #: When a trial lapses. Mirrored into `Activation.trial_ends_at` so entitlement expiry is
    #: evaluated at read time on both sides, never trusted to a nightly job (ADR-0004 §4).
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The billing period this subscription is currently in.
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set when a cancellation is scheduled for period end rather than taken immediately.
    cancel_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: ISO-4217, read from the pinned version. One currency per subscription (ADR-0004 defaults:
    #: one currency per org for now); every amount that references it must agree.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    items: Mapped[list[SubscriptionItem]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    transitions: Mapped[list[SubscriptionTransition]] = relationship(
        back_populates="subscription",
        order_by="SubscriptionTransition.occurred_at",
    )

    __table_args__ = (
        # One live subscription per organization per product. A returning customer re-uses this
        # row (through change-plan / re-subscribe) rather than accumulating rivals that disagree
        # about what they have — the same discipline `product.activations` keeps.
        UniqueConstraint("organization_id", "product_key", name="uq_subscription_org_product"),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint(
            "state in ('trial', 'active', 'past_due', 'suspended', 'cancelled')", name="state"
        ),
        # Leads with the tenant: the RLS qual is ANDed into every query, so an index that does
        # not lead with `organization_id` stops serving its purpose the moment policies apply.
        Index("ix_subscriptions_org_state", "organization_id", "state"),
    )


class SubscriptionItem(Base, TenantBase):
    """A priced component of a subscription, referencing a catalog `price_id`.

    Quantity carries the seat count for a per-seat price; a flat price is quantity 1. The price
    itself lives in catalog and is immutable there — this row only names which one, by its plain
    UUID (no cross-schema FK).
    """

    __tablename__ = "subscription_items"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.subscriptions.id",
            ondelete="CASCADE",
            name="fk_subscription_items_subscription_id",
        ),
        nullable=False,
    )
    #: The catalog price this line bills against. A plain UUID, never an FK across the boundary.
    price_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    subscription: Mapped[Subscription] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        Index("ix_subscription_items_org_subscription", "organization_id", "subscription_id"),
    )


class SubscriptionTransition(Base, TenantBase):
    """One accepted move through the lifecycle. **Append-only** (ADR-0000 §9).

    Every legal transition writes exactly one of these inside the same transaction as the state
    change and the outbox event it emits. It is never updated and never deleted — `REVOKE UPDATE,
    DELETE` enforces that at the database, because the transition stream is what the operator
    console (ADR-0004 §4) rebuilds MRR, trial-conversion and churn from, and a stream you can
    rewrite is not a stream you can defend.

    `from_plan_version_id` / `to_plan_version_id` preserve the pin across a change-plan: the old
    version is recorded here, never overwritten on the subscription, so the previous price stays
    reconstructable exactly as ADR-0004 §2 requires.
    """

    __tablename__ = "subscription_transitions"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.subscriptions.id",
            ondelete="CASCADE",
            name="fk_subscription_transitions_subscription_id",
        ),
        nullable=False,
    )
    #: NULL only for the very first transition into a brand-new subscription (there is no prior
    #: state). Every later row names where it came from.
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The pin before and after. Equal for a pure state move (renew, suspend); different for a
    #: change-plan, which is the row that preserves the previous version (ADR-0004 §2).
    from_plan_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    to_plan_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: A short machine reason: `subscribe`, `trial_convert`, `renew`, `upgrade`, `downgrade`,
    #: `payment_failed`, `operator_suspend`, `customer_cancel`.
    reason: Mapped[str | None] = mapped_column(String(60))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Who caused it — a customer membership, or an operator acting through /internal.
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    subscription: Mapped[Subscription] = relationship(back_populates="transitions")

    __table_args__ = (
        Index(
            "ix_subscription_transitions_org_subscription",
            "organization_id",
            "subscription_id",
            "occurred_at",
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
