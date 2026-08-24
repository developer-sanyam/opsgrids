"""Tables owned by the metering service — the append-only usage sink.

Everything here lives in the `metering` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). The link to a product's subject is a plain UUID, never a constraint
that crosses a schema boundary.

The design fact that shapes this whole service (ADR-0004 §7): product services emit usage
events **from the moment they exist, with `is_billable=false`**. A meter reading not taken is
gone — it cannot be backfilled — so metering is the sink from day one, and billing only starts
reading a meter once a plan version in `catalog` prices it. Turning usage-based pricing on is
therefore *catalogue configuration*, never a change here.

Three tables, and their tenancy classification is a security decision written out per table
rather than inferred (ADR-0002 §5):

* **`usage_events`** — tenant-scoped and **append-only**. The one thing you cannot reconstruct,
  so it is never mutated: a correction is a new event, and the database enforces that with a
  `REVOKE UPDATE, DELETE` on the app role (see the append-only migration), the same tool
  `audit_logs` uses. Idempotent ingest rests on a unique `(organization_id, idempotency_key)`.
* **`meters`** — the reference catalogue of *what a meter is*. **Global / UNPOLICIED**, exactly
  like `product.products` and `catalog.plans`: a meter definition is the platform's vocabulary,
  read by every tenant, not a customer's data. It carries no `organization_id` and takes no
  tenancy policy (ADR-0002 §5, "platform vocabulary must NOT get an org policy").
* **`usage_rollups`** — tenant-scoped, **rebuildable** aggregates. Derived from `usage_events`
  by summation, never a second source of truth: it can be dropped and recomputed at any time, so
  it is a cache of the append-only log, not part of it.

The platform tables (`outbox`, `audit_logs`, `processed_events`) come from the scaffold.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "metering"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class UsageEvent(Base, TenantBase):
    """One recorded unit of consumption — the append-only meter reading.

    A product service posts these server-to-server as it does work: an API call served, a
    gigabyte stored, a document generated. The row is **never updated or deleted** — that is
    enforced below the application by a `REVOKE UPDATE, DELETE` on the app role, so a bug or a
    compromised route cannot rewrite history. A mistaken reading is corrected by recording a new,
    compensating event, exactly as the credit ledger and audit trail work (ADR-0000 §9).

    `is_billable` defaults to **false** and stays false until a plan version in `catalog` prices
    the meter. Recording is free and lossless; billing is a later, separate decision made in the
    catalogue — never by flipping a flag here (ADR-0004 §7). The column carries a database-level
    default of `false` too, so even a raw insert that omits it is non-billable by construction.

    `idempotency_key` is what makes ingest safe to retry: unique per organization, so posting the
    same key twice yields exactly one row. It is the client's key (a message id, a request id),
    scoped to the tenant because two organizations may legitimately reuse the same string.
    """

    __tablename__ = "usage_events"

    #: The meter this reading is against — matches a `meters.meter_key` and a `catalog.prices`
    #: `meter_key`. A plain string, never a foreign key across a schema boundary.
    meter_key: Mapped[str] = mapped_column(String(60), nullable=False)

    #: What was metered, in the product's own terms — a document id, a user id, a job id. A plain
    #: UUID with no cross-schema constraint; nullable because some meters count an organization-
    #: wide quantity with no single subject.
    subject_ref: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: How much. A whole count in `unit`s — integers only, like money, so a sum is always exact.
    #: Sub-unit rates live in `catalog.prices.unit_price_micros`, applied once at billing.
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What one unit is: `api_call`, `gb`, `document`. Mirrors the meter's declared unit.
    unit: Mapped[str] = mapped_column(String(40), nullable=False)

    #: When the usage actually happened, as reported by the emitting service — not when it was
    #: recorded here. Billing periods are cut on this, so it is the client's timestamp.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: False until a plan version prices this meter (ADR-0004 §7). Non-billable at the database by
    #: default, so turning billing on can never be an accident of an omitted field.
    is_billable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: The client's idempotency key, unique within the organization. Two posts of the same key are
    #: one row — the property the ingest endpoint is built around.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would scan
        # the whole platform's rows and discard almost all of them (ADR-0002).
        #
        # Idempotency is enforced here, in the database — the ingest endpoint relies on this
        # constraint via ON CONFLICT rather than a read-then-write race.
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_usage_events_org_idempotency_key"
        ),
        # The customer read sums a period's usage per meter; the billing read (later) sums the
        # billable slice. Both are org-scoped and cut on `occurred_at`.
        Index("ix_usage_events_org_meter_occurred", "organization_id", "meter_key", "occurred_at"),
        Index("ix_usage_events_org_occurred", "organization_id", "occurred_at"),
    )


class Meter(Base):
    """A meter definition — *what a meter is*. **Global / UNPOLICIED reference data.**

    The platform's vocabulary, like `product.products` and `catalog.plans`: the same meter means
    the same thing for every tenant, so this table carries no `organization_id` and takes no
    tenancy policy (ADR-0002 §5). It is read by every organization and authored by the platform,
    seeded through a migration so a fresh database already agrees on what exists.

    `meter_key` is the natural key the rest of commerce references — `usage_events.meter_key` and
    `catalog.prices.meter_key` both point at it by string, never by a cross-schema foreign key.
    """

    __tablename__ = "meters"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The stable machine key: `hrms.api_call`, `core.storage_gb`. Unique platform-wide.
    meter_key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Which product owns the meter — `hrms`, `crm`, `core`. Matches the gateway route table and
    #: the permission prefix. A string, no FK.
    product_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What one unit of this meter is: `api_call`, `gb`, `document`. Events on this meter carry it.
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("meter_key", name="uq_meters_meter_key"),
        Index("ix_meters_product_key", "product_key"),
    )


class UsageRollup(Base, TenantBase):
    """A per-organization, per-meter, per-period aggregate — **derived and rebuildable**.

    A convenience cache over `usage_events`, never a second source of truth: it holds the sum a
    customer read or a billing read would otherwise compute, and it can be dropped and recomputed
    from the append-only log at any time (see `services.rebuild_rollup`). Because it is derived,
    it is *not* append-only — recomputing legitimately overwrites a period's row — which is
    exactly why it is a separate table from `usage_events` rather than a view of it.

    The unique key is `(organization_id, meter_key, period_start)`: one rollup row per meter per
    period, upserted when rebuilt.
    """

    __tablename__ = "usage_rollups"

    meter_key: Mapped[str] = mapped_column(String(60), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The half-open period `[period_start, period_end)` this row aggregates.
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Sum of `quantity` over every event on this meter in the period.
    total_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Sum of `quantity` over only the billable events — what billing would invoice against.
    billable_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "meter_key",
            "period_start",
            name="uq_usage_rollups_org_meter_period",
        ),
        Index("ix_usage_rollups_org_meter", "organization_id", "meter_key"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
