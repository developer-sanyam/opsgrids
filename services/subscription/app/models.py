"""Tables owned by the subscription service — the per-customer commercial lifecycle.

This is the state machine ADR-0004 §4 calls the place *where entitlement is decided*. Catalog
holds the immutable, global definition of what can be sold; subscription holds a **mutable
pointer** into it — one customer's place in the life of what they bought. The seam that matters
is exactly that split (ADR-0004 §1): a customer's life changes constantly, the version they are
pinned to does not change under them.

Four tables, and the discipline on each is load-bearing:

* **`subscriptions`** carries the pinned `plan_version_id` (ADR-0000 §9). Re-pointing it — a
  change-plan — never edits the catalog version and never loses the previous pin: the old value
  is preserved on the transition row, so "what was this customer on, and when?" stays a `SELECT`.
* **`subscription_items`** are the priced components of the pin, referenced by the catalog
  `price_id` (a plain UUID — no cross-schema FK, ADR-0000 §10).
* **`subscription_transitions`** is **append-only** (ADR-0000 §9): every accepted move through the
  state machine writes one, and it is never updated or deleted. `REVOKE UPDATE, DELETE` at the DB
  is the net under that (see the append-only migration); the service layer never mutates one.
* **`dunning_cycles`** is one collection failure and everything that followed it (ADR-0005) — the
  open grace window, how far up the retry ladder it has climbed, and how it ended.

Everything is tenant-scoped `TenantBase` with row-level security — unlike catalog's global
definitional tables, a subscription belongs to exactly one organization.
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

SCHEMA = "subscription"

#: The lifecycle states. Kept as data here and asserted by the state machine in `domain.py`;
#: the set is small and closed, so a `CHECK` constraint pins it at the database too.
STATE_TRIAL = "trial"
STATE_ACTIVE = "active"
STATE_PAST_DUE = "past_due"
STATE_SUSPENDED = "suspended"
STATE_CANCELLED = "cancelled"

STATES = (STATE_TRIAL, STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED, STATE_CANCELLED)

#: The states of one dunning cycle (ADR-0005). `open` is the live grace window; the other three
#: are how it ended, and a closed cycle is never reopened — a later failure opens a new one.
DUNNING_OPEN = "open"
DUNNING_RECOVERED = "recovered"
DUNNING_EXHAUSTED = "exhausted"
DUNNING_ABANDONED = "abandoned"

DUNNING_STATES = (DUNNING_OPEN, DUNNING_RECOVERED, DUNNING_EXHAUSTED, DUNNING_ABANDONED)


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


class DunningCycle(Base, TenantBase):
    """One failed collection and the grace window it opened (ADR-0005).

    A cycle is born when the first `commerce.payment.failed` for a subscription arrives, and it
    is the only place the platform keeps *when the next thing happens*. Two columns carry that,
    and both are written once at open and never recomputed from a clock:

    * **`opened_at`** is the anchor. Every rung of the ladder is `opened_at + offset`, so a sweep
      that runs late lands on the schedule it would have had if it had run on time. Deriving the
      next attempt as `now + interval` instead would let each late tick push the remaining ladder
      out, and fourteen days of grace would quietly become twenty.
    * **`grace_ends_at`** is the promise. It is stored rather than derived from the policy at read
      time so that editing the policy — a perfectly reasonable thing to do — cannot move the
      window under a customer who has already been told when their access ends.

    `next_action_at` is the sweep's index into all of that: the earlier of the next retry and the
    close of the window. It is denormalised (both are recoverable from `opened_at` and
    `attempts_made`) so the cross-tenant discovery query is one indexed range scan rather than a
    computation over every open cycle in the platform.

    **Mutable, deliberately.** The append-only record of what happened to this subscription is
    `subscription_transitions`, the audit log and the outbox — a cycle is the *live aggregate* the
    same way `subscriptions.state` is, and no financial fact lives here: money is billing's, and
    what was actually attempted is payment's. `amount_minor`/`currency`/`invoice_id` are a copy of
    what the failure event carried, held only so a retry can be requested without this service
    reaching into another schema (ADR-0000 §10).
    """

    __tablename__ = "dunning_cycles"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.subscriptions.id",
            ondelete="CASCADE",
            name="fk_dunning_cycles_subscription_id",
        ),
        nullable=False,
    )

    #: What billing was collecting, by plain UUID — echoed back to payment on each retry so the
    #: debit is raised against the same invoice. Nullable: a charge need not name an invoice.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The amount the failed charge was for. Integer minor units, BIGINT (ADR-0000 §8 — no floats
    #: anywhere in billing, and that includes the copy dunning keeps of one).
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=DUNNING_OPEN)

    #: The first failure. The anchor every due moment is computed from — never `now`.
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: When entitlement stops. Stored at open; a policy edit does not move an open window.
    grace_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The next retry, or the close of the window once the ladder is spent. NULL once closed.
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Retries *requested* so far — the rung of the ladder, not the number of failures.
    attempts_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Failures *observed* so far, including the one that opened the cycle. Differs from
    #: `attempts_made` by exactly the original charge, and by more if a retry failed twice over.
    failures_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: `payment_captured`, `grace_expired`, `subscription_cancelled`, `operator_suspend`.
    closed_reason: Mapped[str | None] = mapped_column(String(60))

    subscription: Mapped[Subscription] = relationship()

    __table_args__ = (
        CheckConstraint("state in ('open', 'recovered', 'exhausted', 'abandoned')", name="state"),
        CheckConstraint("attempts_made >= 0", name="attempts_non_negative"),
        CheckConstraint("failures_seen >= 1", name="failures_seen_positive"),
        CheckConstraint("amount_minor is null or amount_minor >= 0", name="amount_non_negative"),
        CheckConstraint("currency is null or length(currency) = 3", name="currency_iso"),
        CheckConstraint("grace_ends_at > opened_at", name="window_is_forward"),
        # At most one live cycle per subscription. Two would race to suspend the same
        # subscription on two different schedules, and the second failure of a cycle already
        # running is not a new grace window — it is the same one, still open.
        Index(
            "uq_dunning_cycles_open_per_subscription",
            "organization_id",
            "subscription_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
        # Leads with the tenant, like every other index here: the RLS qual is ANDed into every
        # tenant-bound query.
        Index("ix_dunning_cycles_org_subscription", "organization_id", "subscription_id"),
        # The one index that deliberately does **not** lead with `organization_id`. The sweep's
        # discovery query is cross-tenant by construction — it exists to find *which* tenants have
        # work due, so there is no organization to bind while asking (the same shape as the outbox
        # relay's drain query, and it runs on the same BYPASSRLS role). An org-leading index would
        # not serve it.
        Index(
            "ix_dunning_cycles_due",
            "next_action_at",
            postgresql_where=text("state = 'open'"),
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
