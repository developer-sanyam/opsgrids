"""Tables owned by manufacturing (ADR-0014 §7, plan Phase 5) — all in the `operations` schema.

* **boms** — one per produced item. A header only: what the thing is, never how it is made.
* **bom_versions** — how it is made, versioned. Mutable while `DRAFT`; once `PUBLISHED` neither
  the version nor its components can change (service refusal *and* a database trigger), and a
  production order pins the version it was planned from.
* **bom_components** / **bom_substitutes** — the lines, each with a scrap allowance (basis
  points) and optional effective dates; an alternative item per line with a conversion ratio.
  A component that is itself a produced item (has its own BOM) is what makes a BOM multi-level.
* **production_orders** — the state machine (`domain.PRODUCTION_ORDER_TRANSITIONS`).
* **production_materials** — one row per BOM line the order needs, with every quantity the order
  has moved: required, reserved, issued, consumed, returned, wasted.
* **production_picks** — one row per stock bucket (location, lot, roll) a material was reserved
  from / issued out of. This is what makes WIP exact per lot and gives genealogy its parents.
* **production_outputs**, **production_wastage**, **production_genealogy** — append-only facts
  (UPDATE/DELETE revoked from the app role, like the ledger).
* **wastage_thresholds** — how much wastage is acceptable before an alert is raised.
* **manufacturing_requests** — `Idempotency-Key` claims for the mutating endpoints.

`item_id`, `location_id`, `lot_id` and `roll_id` are **plain indexed UUIDs, never foreign keys**:
`lots`/`rolls` arrive with Phase 1, and the existence of an item or a location is checked in the
service, filtered by organization, where the answer is a 404 and not an IntegrityError.

Quantities are integer milli-units, scrap and thresholds integer basis points. No float, no
`Numeric` anywhere.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from platform_core.models import (
    OrgScopedMixin,
    TenantBase,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

_VERSION_STATUS_SQL = "'DRAFT', 'PUBLISHED', 'RETIRED'"
_ORDER_STATUS_SQL = "'DRAFT', 'APPROVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'"
_WASTAGE_SQL = "'CUTTING', 'DEFECT', 'SPILLAGE', 'DAMAGE', 'PROCESS_LOSS', 'LEFTOVER', 'OTHER'"


def _uuid(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable)


def _milli(*, default: int | None = 0, nullable: bool = False) -> Mapped[int]:
    return mapped_column(BigInteger, nullable=nullable, default=default)


class Bom(Base, TenantBase):
    """The bill of materials of one produced item. One live BOM per item, so "the BOM of this
    item" is unambiguous when a component is looked up for multi-level explosion."""

    __tablename__ = "boms"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_id: Mapped[uuid.UUID] = _uuid()
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_boms_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_boms_org_item",
            "organization_id",
            "item_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class BomVersion(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One way of making the item. `output_quantity_milli` is the batch the component quantities
    are stated for (1 piece, or 100 m, ...). Immutable once published — see the module docstring."""

    __tablename__ = "bom_versions"

    bom_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.boms.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")
    output_quantity_milli: Mapped[int] = _milli(default=1000)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(String(64))
    #: SHA-256 of the canonical component list at the moment of publishing — proves, long after,
    #: that what an order was planned from is still what is stored.
    content_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"status IN ({_VERSION_STATUS_SQL})", name="status_known"),
        CheckConstraint("output_quantity_milli > 0", name="output_positive"),
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="window_ordered",
        ),
        UniqueConstraint("organization_id", "bom_id", "version_no", name="uq_bom_versions_number"),
        # At most one draft per BOM: edit it, publish it, then start the next.
        Index(
            "uq_bom_versions_one_draft",
            "organization_id",
            "bom_id",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
        ),
        Index("ix_bom_versions_org_bom", "organization_id", "bom_id"),
    )


class BomComponent(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One line of a version: `quantity_milli` of `component_item_id` per version batch, plus a
    scrap allowance. Effective dates narrow when the line applies (a swap of a supplier's
    fabric on a given date), inside the version's own window."""

    __tablename__ = "bom_components"

    bom_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.bom_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    component_item_id: Mapped[uuid.UUID] = _uuid()
    quantity_milli: Mapped[int] = _milli(default=None)
    #: Scrap allowance, basis points of the line's quantity (250 = 2.5 %).
    scrap_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint("scrap_bps >= 0 AND scrap_bps <= 10000", name="scrap_in_range"),
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="window_ordered",
        ),
        UniqueConstraint(
            "organization_id", "bom_version_id", "line_no", name="uq_bom_components_line"
        ),
        Index("ix_bom_components_org_version", "organization_id", "bom_version_id"),
        Index("ix_bom_components_org_item", "organization_id", "component_item_id"),
    )


class BomSubstitute(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """An alternative for one BOM line. `ratio_milli` is how much substitute replaces one unit of
    the component (1000 = 1:1, 1250 = 1.25 of the substitute per 1 of the component)."""

    __tablename__ = "bom_substitutes"

    bom_component_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.bom_components.id", ondelete="RESTRICT"),
        nullable=False,
    )
    substitute_item_id: Mapped[uuid.UUID] = _uuid()
    ratio_milli: Mapped[int] = _milli(default=1000)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("ratio_milli > 0", name="ratio_positive"),
        UniqueConstraint(
            "organization_id",
            "bom_component_id",
            "substitute_item_id",
            name="uq_bom_substitutes_item",
        ),
        Index("ix_bom_substitutes_org_component", "organization_id", "bom_component_id"),
    )


class ProductionOrder(Base, TenantBase):
    """A request to make `planned_quantity_milli` of `item_id` from a pinned BOM version."""

    __tablename__ = "production_orders"

    #: Per-organization sequence behind `number` (`MO-00042`).
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    bom_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.boms.id", ondelete="RESTRICT"), nullable=False
    )
    #: The version the order was planned from. Never changes; the version is immutable anyway.
    bom_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.bom_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = _uuid()
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")
    planned_quantity_milli: Mapped[int] = _milli(default=None)
    produced_quantity_milli: Mapped[int] = _milli()
    #: Where finished goods are received.
    output_location_id: Mapped[uuid.UUID] = _uuid()
    #: Where WIP is held; when null, each material's WIP stays at the location it was issued from.
    wip_location_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    planned_start: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    #: The date effective dates were evaluated on when the order's lines were materialised.
    resolved_on: Mapped[date] = mapped_column(Date, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    __table_args__ = (
        CheckConstraint(f"status IN ({_ORDER_STATUS_SQL})", name="status_known"),
        CheckConstraint("planned_quantity_milli > 0", name="planned_positive"),
        CheckConstraint("produced_quantity_milli >= 0", name="produced_non_negative"),
        UniqueConstraint("organization_id", "seq", name="uq_production_orders_seq"),
        UniqueConstraint("organization_id", "number", name="uq_production_orders_number"),
        Index("ix_production_orders_org_status", "organization_id", "status"),
        Index("ix_production_orders_org_item", "organization_id", "item_id"),
    )


class ProductionMaterial(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One BOM line as the order needs it. The BOM figures are **copied** here at creation
    (`bom_quantity_milli`, `output_milli`, `scrap_bps`), so the order's arithmetic never depends
    on a row that could be read differently later. `item_id` is the item actually used — the BOM's
    own `component_item_id` unless a substitute was chosen (then `ratio_milli` != 1000).

    `required_milli` includes the scrap allowance. `wip = issued - consumed - returned - wasted`
    is what is physically sitting in the WIP bucket for this line."""

    __tablename__ = "production_materials"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bom_component_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.bom_components.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    component_item_id: Mapped[uuid.UUID] = _uuid()
    item_id: Mapped[uuid.UUID] = _uuid()
    bom_quantity_milli: Mapped[int] = _milli(default=None)
    output_milli: Mapped[int] = _milli(default=None)
    scrap_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ratio_milli: Mapped[int] = _milli(default=1000)
    discrete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required_milli: Mapped[int] = _milli(default=None)
    reserved_milli: Mapped[int] = _milli()
    issued_milli: Mapped[int] = _milli()
    consumed_milli: Mapped[int] = _milli()
    returned_milli: Mapped[int] = _milli()
    wasted_milli: Mapped[int] = _milli()
    #: Set when wastage has crossed its threshold, so the alert fires on the crossing only.
    threshold_alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("required_milli >= 0", name="required_non_negative"),
        CheckConstraint(
            "reserved_milli >= 0 AND issued_milli >= 0 AND consumed_milli >= 0 "
            "AND returned_milli >= 0 AND wasted_milli >= 0",
            name="quantities_non_negative",
        ),
        CheckConstraint(
            "issued_milli - consumed_milli - returned_milli - wasted_milli >= 0",
            name="wip_non_negative",
        ),
        UniqueConstraint(
            "organization_id", "production_order_id", "line_no", name="uq_production_materials_line"
        ),
        Index("ix_production_materials_org_order", "organization_id", "production_order_id"),
    )


class ProductionPick(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """A bucket a material was reserved from and/or issued out of: `(source location, lot, roll)`
    of `item_id`. `reservation_id` is the kernel reservation (a plain id — the reservation lives
    in `inventory_reservations`, which only the kernel writes)."""

    __tablename__ = "production_picks"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_materials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = _uuid()
    source_location_id: Mapped[uuid.UUID] = _uuid()
    wip_location_id: Mapped[uuid.UUID] = _uuid()
    lot_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    roll_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    reservation_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    #: Open remainder of the reservation not yet issued (mirrors the kernel's own figure).
    reserved_milli: Mapped[int] = _milli()
    issued_milli: Mapped[int] = _milli()
    consumed_milli: Mapped[int] = _milli()
    returned_milli: Mapped[int] = _milli()
    wasted_milli: Mapped[int] = _milli()

    __table_args__ = (
        CheckConstraint(
            "reserved_milli >= 0 AND issued_milli >= 0 AND consumed_milli >= 0 "
            "AND returned_milli >= 0 AND wasted_milli >= 0",
            name="quantities_non_negative",
        ),
        CheckConstraint(
            "issued_milli - consumed_milli - returned_milli - wasted_milli >= 0",
            name="wip_non_negative",
        ),
        Index(
            "uq_production_picks_bucket",
            "organization_id",
            "material_id",
            "source_location_id",
            "lot_id",
            "roll_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_production_picks_org_order", "organization_id", "production_order_id"),
    )


class ProductionOutput(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Good output received into stock. Append-only; a mistake is corrected by an adjustment."""

    __tablename__ = "production_outputs"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = _uuid()
    quantity_milli: Mapped[int] = _milli(default=None)
    location_id: Mapped[uuid.UUID] = _uuid()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    lot_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    roll_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    consume_move_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    receipt_move_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint("status IN ('AVAILABLE', 'QUALITY_HOLD')", name="status_known"),
        Index("ix_production_outputs_org_order", "organization_id", "production_order_id"),
    )


class ProductionWastage(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Material lost in production, by type, drawn out of WIP. One row per pick drawn from (so a
    loss is attributable to a lot); `event_id` groups the rows of one request. Append-only."""

    __tablename__ = "production_wastage"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_materials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pick_id: Mapped[uuid.UUID] = _uuid()
    event_id: Mapped[uuid.UUID] = _uuid()
    item_id: Mapped[uuid.UUID] = _uuid()
    wastage_type: Mapped[str] = mapped_column(String(14), nullable=False)
    quantity_milli: Mapped[int] = _milli(default=None)
    location_id: Mapped[uuid.UUID] = _uuid()
    lot_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    roll_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    move_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(f"wastage_type IN ({_WASTAGE_SQL})", name="type_known"),
        Index("ix_production_wastage_org_order", "organization_id", "production_order_id"),
    )


class ProductionGenealogy(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Which input lot/roll went into which output lot/roll — plain-UUID references, owned here
    (Phase 1's `lot_genealogy` is a different table, written for receipts and roll splits).
    `quantity_milli` is the parent quantity consumed for that output. Append-only."""

    __tablename__ = "production_genealogy"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    output_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.production_outputs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_item_id: Mapped[uuid.UUID] = _uuid()
    parent_lot_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    parent_roll_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    child_item_id: Mapped[uuid.UUID] = _uuid()
    child_lot_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    child_roll_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    quantity_milli: Mapped[int] = _milli(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        Index("ix_production_genealogy_org_order", "organization_id", "production_order_id"),
        Index("ix_production_genealogy_org_output", "organization_id", "output_id"),
        Index(
            "ix_production_genealogy_org_parent_lot",
            "organization_id",
            "parent_lot_id",
            postgresql_where=text("parent_lot_id IS NOT NULL"),
        ),
        Index(
            "ix_production_genealogy_org_child_lot",
            "organization_id",
            "child_lot_id",
            postgresql_where=text("child_lot_id IS NOT NULL"),
        ),
    )


class WastageThreshold(Base, TenantBase):
    """How much wastage is acceptable, as basis points of an order's standard (no-scrap) need of
    a material. `item_id` is the **produced** item (null: every product); `wastage_type` null:
    every type. The most specific matching row wins; with none, the BOM line's own scrap
    allowance is the threshold."""

    __tablename__ = "wastage_thresholds"

    item_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    wastage_type: Mapped[str | None] = mapped_column(String(14))
    max_bps: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("max_bps >= 0 AND max_bps <= 10000", name="max_in_range"),
        CheckConstraint(
            f"wastage_type IS NULL OR wastage_type IN ({_WASTAGE_SQL})", name="type_known"
        ),
        Index(
            "uq_wastage_thresholds_scope",
            "organization_id",
            "item_id",
            "wastage_type",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ManufacturingRequest(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """An `Idempotency-Key` claim. Inserted first, `ON CONFLICT DO NOTHING`: a concurrent
    duplicate blocks on the unique index until the first request commits, then replays it."""

    __tablename__ = "manufacturing_requests"

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_manufacturing_requests_key"
        ),
    )
