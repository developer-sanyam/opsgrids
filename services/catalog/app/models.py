"""Tables owned by the catalog service — the definitional layer: *what can be sold*.

Everything here is **global**, exactly like `product.products` (ADR-0002 §5, "platform
vocabulary must NOT get an org policy"). A plan is the platform's, not a customer's: the
same three tiers of HRMS are offered to everybody. So the definitional tables carry no
`organization_id`, take no tenancy policy, and are read by every downstream service.

The one apparent exception proves the rule. `plan_versions.restricted_to_org_id` is a
*nullable pointer*, not tenancy. A public version leaves it NULL; a negotiated enterprise
price sets it to the one organization allowed to see and pin that version — an audience of
one (ADR-0004 §2). It is a plain indexed UUID, never a cross-schema foreign key, and
visibility is enforced by an application filter defended by a negative-authz test, because
a global table cannot lean on row-level security the way a tenant table does.

Two invariants are load-bearing and enforced below the application (ADR-0000 §9):

* **A published plan version is immutable.** Editing a plan clones its active version into a
  new draft; the published row is never mutated. A `BEFORE UPDATE` trigger raises on any
  attempt (see the immutability migration), and `services.py` refuses it before the database
  has to — the trigger is the net, the guard is the message.
* **Money is integer minor units** with an ISO-4217 currency (ADR-0000 §8). Amounts are
  `BIGINT` minor units; a usage rate that needs sub-minor precision is `BIGINT` micros of a
  major unit (`platform_core.money`). No floats, no `Numeric`.

The platform tables (`outbox`, `audit_logs`, `processed_events`) come from the scaffold and
carry the events this service emits.
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base

SCHEMA = "catalog"

#: The organization id stamped on catalog's own `audit_logs`/`outbox` rows. Authoring a plan
#: is a platform act, not a tenant one — there is no customer whose data changed — so the two
#: policied platform tables are bound to this sentinel rather than to a real tenant. The
#: nil-UUID-as-platform convention is the same one auth-service uses for pre-organization
#: audit and outbox rows (ADR-0002 §6). A bespoke version still carries its real customer in
#: `restricted_to_org_id` and in the event payload.
PLATFORM_ORG: uuid.UUID = uuid.UUID(int=0)

#: Lifecycle states.
PLAN_ACTIVE = "active"
PLAN_RETIRED = "retired"

VERSION_DRAFT = "draft"
VERSION_PUBLISHED = "published"
VERSION_GRANDFATHERED = "grandfathered"

PRICE_FLAT = "flat"
PRICE_PER_SEAT = "per_seat"
PRICE_USAGE = "usage"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Plan(Base):
    """A named offering under one product. **Global — deliberately not tenant-scoped.**

    Keyed by `product_key`, the same string the product service and the gateway use — a
    string, not a foreign key, so the catalogue can name a product that lives in another
    schema without crossing a boundary the platform exists to keep clean.
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: `hrms`, `crm`, `core`. Matches the gateway route table and the permission prefix. No FK.
    product_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Stable machine code, unique within a product: `starter`, `growth`, `enterprise`.
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    #: `active` or `retired`. Retiring stops the plan being offered; it never deletes it,
    #: because a subscription pinned to one of its versions must stay reconstructable.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=PLAN_ACTIVE)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    versions: Mapped[list[PlanVersion]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanVersion.version_no",
    )

    __table_args__ = (
        UniqueConstraint("product_key", "code", name="uq_plans_product_code"),
        Index("ix_plans_product_key", "product_key"),
    )


class PlanVersion(Base):
    """One immutable priced snapshot of a plan. A subscription pins exactly one of these.

    `state` moves `draft -> published`, and `published -> grandfathered` when a newer version
    supersedes it while existing subscriptions stay pinned. **Once `published`, the row never
    changes** — enforced by a `BEFORE UPDATE` trigger and by the service layer. Re-pricing is
    a new version, never an edit (ADR-0000 §9, ADR-0004 §2).
    """

    __tablename__ = "plan_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.plans.id", ondelete="CASCADE", name="fk_plan_versions_plan_id"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=VERSION_DRAFT)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: NULL for a public version; a single organization id for a bespoke, audience-of-one
    #: price (ADR-0004 §2). A plain UUID, never a cross-schema FK. The customer read filters
    #: on `restricted_to_org_id IS NULL OR = principal.organization_id`.
    restricted_to_org_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: `month`, `year`, `one_time`. The cadence a subscription renews on.
    billing_interval: Mapped[str] = mapped_column(String(20), nullable=False, default="month")
    #: ISO-4217. One currency per version; every price on it must agree (ADR-0000 §8).
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plan: Mapped[Plan] = relationship(back_populates="versions")
    prices: Mapped[list[Price]] = relationship(
        back_populates="plan_version", cascade="all, delete-orphan"
    )
    features: Mapped[list[Feature]] = relationship(
        back_populates="plan_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("plan_id", "version_no", name="uq_plan_versions_plan_version"),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        # The customer read resolves published versions for a product and filters on the
        # audience pointer, so both lead the index.
        Index("ix_plan_versions_plan_state", "plan_id", "state"),
        Index("ix_plan_versions_restricted_to_org", "restricted_to_org_id"),
    )


class Price(Base):
    """One priced component of a plan version. Money in integer minor units (ADR-0000 §8).

    `kind` decides which columns carry meaning: a `flat` or `per_seat` price uses
    `amount_minor`; a `usage` price uses `unit_price_micros` (micros of a major unit, so a
    fraction of a paisa per call is exact) and names the `meter_key` it bills against.
    """

    __tablename__ = "prices"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.plan_versions.id",
            ondelete="CASCADE",
            name="fk_prices_plan_version_id",
        ),
        nullable=False,
    )
    #: `flat`, `per_seat`, `usage`.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Integer minor units. Set for `flat`/`per_seat`; NULL for a pure `usage` rate.
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    #: What a unit is, for `per_seat`/`usage`: `seat`, `api_call`, `gb`.
    unit: Mapped[str | None] = mapped_column(String(40))
    #: Micros of a major unit for a usage rate — sub-minor precision, rounded once at billing.
    unit_price_micros: Mapped[int | None] = mapped_column(BigInteger)
    #: The meter a usage price bills against, matching a metering `meter_key`. No FK.
    meter_key: Mapped[str | None] = mapped_column(String(60))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plan_version: Mapped[PlanVersion] = relationship(back_populates="prices")

    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("kind in ('flat', 'per_seat', 'usage')", name="kind"),
        Index("ix_prices_plan_version_id", "plan_version_id"),
    )


class Feature(Base):
    """A capability flag or limit a plan version switches on, keyed by the product's own name.

    `value` is JSONB so a feature can be a boolean, a numeric limit, or a small structure,
    without the catalogue needing to understand what any product does with it — the same
    contract `product.features` already uses.
    """

    __tablename__ = "features"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.plan_versions.id",
            ondelete="CASCADE",
            name="fk_features_plan_version_id",
        ),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plan_version: Mapped[PlanVersion] = relationship(back_populates="features")

    __table_args__ = (
        UniqueConstraint("plan_version_id", "feature_key", name="uq_features_version_key"),
        Index("ix_features_plan_version_id", "plan_version_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
