"""Tables owned by the product service — what exists to sell, and who has it.

Three tables, and the distinction between the first two is the whole design.

**`products` is global.** It is the platform's catalogue, not a customer's: HRMS is HRMS for
everybody. It carries no `organization_id`, takes no tenancy policy, and is readable by every
tenant on purpose — a marketplace is a list of things you do not have yet.

**`activations` is tenant-scoped.** One row per organization per product, and it answers the
only question the gateway asks: may this organization use this product? Everything else here
exists to keep that row honest.

**`activation_sources` is why an activation is on.** A product can be entitled by more than one
thing at once — its own subscription, and a bundle that includes it — and an activation is on
while *any* of them is live (ADR-0004 §5, addendum). Each source is one row; the activation's
status is recomputed from them, never set directly by whichever event arrived last. Without it,
cancelling a suite would switch off the standalone HRMS the same customer also pays for.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "product"

#: The organization id stamped on this service's own `audit_logs`/`outbox` rows when an act has
#: no tenant — flipping what a deployment sells is a platform decision, not a customer's.
#: `products` itself carries no policy and needs no GUC at all (see above); `audit_logs` does,
#: and there is no real organization to bind it to. The same nil-UUID-as-platform convention
#: auth-service and catalog-service use for their own pre-tenant and platform-wide rows.
PLATFORM_ORG: uuid.UUID = uuid.UUID(int=0)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Product(Base):
    """One sellable product. **Global — deliberately not tenant-scoped.**

    The `key` is the same string everywhere it appears: the URL prefix a gateway routes, the
    first segment of every permission code, the `ent` claim in a token, and the slug on the
    marketplace page. One string rather than four mappings, because a mapping is somewhere for
    them to drift apart.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: `hrms`, `crm`, `pulseboard`. Matches the gateway route table and the permission prefix.
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))

    #: `available`, `beta`, `soon`. A product marked available that cannot actually be bought
    #: is the fastest way to lose a deal at the demo, so the states stay distinct and honest.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="soon")

    #: What this product reads from or writes to. Shown on the marketplace, and the reason the
    #: ecosystem is worth more than the sum of its parts.
    connects_to: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    #: Feature keys this product understands, so a plan can switch them per organization
    #: without the product ever needing to know what a plan is.
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    #: Platform capabilities every customer has, whatever they bought. The gateway never gates
    #: these — the same idea as a route with no product in `routes.json`.
    is_platform: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    #: A bundle is sold, not routed: `suite` is a key a subscription is bought under and a
    #: marketplace card shows, but no gateway route names it and no activation is ever projected
    #: for it — a subscription to it entitles its members instead (docs/commerce-packaging.md).
    #: `activate()` refuses one outright; it has nothing to switch on.
    is_bundle: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Activation(Base, TenantBase):
    """One organization's access to one product. The row a 402 depends on.

    Deactivation sets `deactivated_at` rather than deleting the row. A customer who leaves and
    comes back should find their configuration, and a deleted row cannot answer "when did they
    have this?" — which is the first question any billing dispute asks.
    """

    __tablename__ = "activations"

    product_key: Mapped[str] = mapped_column(String(40), nullable=False)

    #: `trial`, `active`, `suspended`. Only `active`, or a trial that has not lapsed, entitles.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trial")

    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: When the lifecycle fact this row currently reflects occurred (the envelope's
    #: `occurred_at`), NULL for rows written by the manual `activate` path. The projection's
    #: out-of-order guard: delivery is at-least-once and unordered across relay/consumer
    #: replicas and DLQ replays, so a stale transition folded late must not overwrite a newer
    #: one — a customer suspended-then-reactivated must never end up suspended because the
    #: suspension arrived last.
    state_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Per-organization switches, keyed by the product's own feature names.
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: Who turned it on — a membership, or null when the platform did.
    activated_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        # One row per organization per product. Without this, reactivating quietly creates a
        # second row and the two disagree about what the customer has.
        UniqueConstraint("organization_id", "product_key", name="uq_activation_org_product"),
        # Leads with the tenant: the row-level security qual is ANDed into every query, so an
        # index that does not lead with it stops serving its purpose the moment policies apply.
        Index("ix_activations_org_status", "organization_id", "status"),
    )

    @property
    def entitles(self) -> bool:
        """Whether this row lets the gateway through.

        Expiry is evaluated here rather than written into `status` by a nightly job, so a
        lapsed trial stops entitling the moment it lapses rather than whenever the job next
        runs. "The job has not run yet" is not a reason to keep serving a product nobody is
        paying for.
        """
        if self.deactivated_at is not None:
            return False
        if self.status == "active":
            return True
        if self.status == "trial":
            return self.trial_ends_at is None or self.trial_ends_at > datetime.now(UTC)
        return False


#: Source statuses. The first two keep a source live (a trial only until it lapses); `suspended`
#: is a source that exists and does not entitle; `removed` is a member a bundle no longer includes
#: after a plan change — kept as a tombstone rather than deleted, because its `state_occurred_at`
#: is what stops a stale, older event from quietly re-adding it.
SOURCE_TRIAL = "trial"
SOURCE_ACTIVE = "active"
SOURCE_SUSPENDED = "suspended"
SOURCE_REMOVED = "removed"


class ActivationSource(Base, TenantBase):
    """One reason an activation is on. An activation entitles while **any** live source does.

    A source is identified by the subscription that provides it, and a subscription is named by
    its `source_product_key`: subscription-service keeps exactly one subscription per organization
    per product key for ever (`uq_subscription_org_product` — cancelled rows are kept, never
    replaced), so `(organization_id, source_product_key)` names one subscription as surely as its
    id does. The id is recorded too, when the event carries it; a row backfilled from an activation
    that predates sources has none, and is adopted by the next event for the same source.

    `source_product_key == product_key` is the product's own source: its standalone subscription,
    or a hand activation through `activate()` — the two always shared the one activation row, and
    still share this one. A bundle contributes a row per member, `source_product_key = 'suite'`.

    `state_occurred_at` is the out-of-order guard, **per source**: every row of a source carries
    the time of the last event applied to it, and an event older than that is dropped whole. It is
    per source rather than per activation because two sources are independent histories — a
    standalone cancellation must not be dropped as "stale" because a newer suite event already
    touched the same activation.
    """

    __tablename__ = "activation_sources"

    #: The product this source entitles — the activation it feeds.
    product_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The key the providing subscription was bought under: `hrms` for a standalone HRMS
    #: subscription (or a hand activation), `suite` for a bundle.
    source_product_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The providing subscription, when known. A plain UUID into subscription's schema, never an
    #: FK (ADR-0000 §10). NULL for a hand activation and for rows backfilled before sources existed.
    source_subscription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: `trial`, `active`, `suspended`, `removed` — see the constants above.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The envelope `occurred_at` of the event this source reflects. NULL for a hand activation.
    state_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "product_key",
            "source_product_key",
            name="uq_activation_sources_org_product_source",
        ),
        # The recompute reads every source of one activation; the diff reads every member of one
        # source. Both lead with the tenant, which the RLS qual is ANDed into.
        Index(
            "ix_activation_sources_org_source",
            "organization_id",
            "source_product_key",
        ),
        CheckConstraint("status in ('trial', 'active', 'suspended', 'removed')", name="status"),
    )

    @property
    def is_live(self) -> bool:
        """Whether this source, on its own, would let the gateway through right now."""
        if self.status == SOURCE_ACTIVE:
            return True
        if self.status == SOURCE_TRIAL:
            return self.trial_ends_at is None or self.trial_ends_at > datetime.now(UTC)
        return False


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
