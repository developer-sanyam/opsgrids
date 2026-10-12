"""Tables owned by the operations service — procurement, inventory and suppliers.

Everything here lives in the `operations` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to people are plain UUIDs (`raised_by_id`) — never a constraint
that crosses a schema boundary. Links **within** this schema (`purchase_orders.supplier_id`,
`purchase_order_lines.purchase_order_id`) are real foreign keys, because only cross-schema
foreign keys are banned.

The procurement tables, then the inventory ones (ADR-0014):

* **suppliers** — the vendors the org buys from. `spend_minor` (lifetime committed spend) and
  `open_pos` are denormalised read-model rollups so a supplier row renders without a join;
  `on_time_pct` is a stored delivery-performance figure the UI labels, never colour alone.
* **warehouses** — the stocking locations. Reference data within the organization; still
  `organization_id` + RLS because one tenant's sites are not another's.
* **purchase_orders** — a PO with an ITIL-style lifecycle (draft → approved → received →
  closed, plus a `cancelled` terminal state reachable from draft or approved).
  `supplier_name`, `category` and `warehouse` are denormalised for the list view; the
  PO **total is derived from its lines and never stored** (ADR-0000 §8 — money is exact, and a
  stored total is one more thing to drift out of sync).
* **purchase_order_lines** — one item on a PO. `unit_price_minor` is per-unit minor units;
  `amount_minor` is `quantity * unit_price_minor`, stored so the lines **foot to the PO total**
  and the arithmetic is checkable in the database, not just in the UI.
* **locations** — zone / rack / bin inside a warehouse; every warehouse has a default bin.
* **inventory_items** — the item master. Stock is **not stored on it**: `on_hand_milli` is an
  aggregate over `inventory_balances`. Low-stock is derived, never a stored flag.
* **inventory_moves** + **inventory_ledger** — the append-only record of every stock change: a
  header per posting and one signed row per bucket it touched. `UPDATE`/`DELETE` are revoked
  from the app role (like `audit_logs`); a wrong move is corrected by an opposite one.
* **putaway_captures** — the append-only proof of a storage put-away (scanned bin, suggestion,
  worker and, when the device gave one, a GPS fix). The bin is the location of record; the fix is
  audit evidence behind its own permission.
* **inventory_balances** — the current quantity per bucket (item, location, status, lot, roll),
  a projection of the ledger written only in the same transaction as the ledger row.
* **inventory_reservations** — stock promised to a demand without moving it.
* **inventory_discrepancies** — ledger/balance drift the reconciler found. Never auto-repaired.

Quantities are integer **milli-units** of the item's UOM (`app.inventory.quantity`) — as with
money, no float ever touches one.

The human identifiers the SPA speaks in — a supplier `code` (`SUP-MER`), a PO `number`
(`PO-2026-0148`), an inventory `sku` — are stored as their own unique-per-org columns; the
primary key is an opaque UUID, and the read models the API exposes carry that UUID as `id`.
Money is always integer minor units in an ISO currency (`*_minor` + `currency`); no float ever
touches an amount.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    cast,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.inventory import quantity
from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import OrgScopedMixin, TenantBase, UUIDPrimaryKeyMixin

SCHEMA = "operations"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Supplier(Base, TenantBase):
    """One vendor the organization procures from.

    `code` (`SUP-MER`) is the human identity, unique within an organization. `spend_minor`
    (lifetime committed spend) and `open_pos` are denormalised rollups kept on the row so a
    directory listing renders without a per-supplier aggregate; `on_time_pct` is the trailing
    on-time delivery rate (0-100), a stored figure the UI labels in words.
    """

    __tablename__ = "suppliers"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: raw_materials | packaging | components | mro | logistics | services.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: active | on_hold | pending.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Trailing on-time delivery rate, 0-100 (one decimal). A percentage, not money — stored
    #: exact as Numeric so it never suffers float drift.
    on_time_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)

    #: Lifetime committed spend, integer minor units of `currency`.
    spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Denormalised count of purchase orders still in flight against this supplier.
    open_pos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    contact_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    contact_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    #: When the org started buying from this supplier.
    since: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        # Partial — `WHERE deleted_at IS NULL` — so a soft-deleted supplier's code can be
        # reused rather than blocking re-creation with an IntegrityError (house precedent:
        # `services/account/app/models.py:250`).
        Index(
            "uq_suppliers_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_suppliers_org_status", "organization_id", "status"),
        Index("ix_suppliers_org_category", "organization_id", "category"),
    )


class Warehouse(Base, TenantBase):
    """A stocking location. Reference data within the organization, still tenant-scoped."""

    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    #: The site's position on a map, integer micro-degrees (10^-6; never a float — ADR-0000 §8's
    #: no-float rule covers any stored measurement). Both set or both NULL; NULL until someone
    #: places the site. These are *site* coordinates — a business address, not a person's
    #: position (`PutawayCapture` holds those, behind their own permission and an audit row).
    lat_micro: Mapped[int | None] = mapped_column(BigInteger)
    lng_micro: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("(lat_micro IS NULL) = (lng_micro IS NULL)", name="coords_paired"),
        CheckConstraint(
            "lat_micro IS NULL OR lat_micro BETWEEN -90000000 AND 90000000", name="lat_in_range"
        ),
        CheckConstraint(
            "lng_micro IS NULL OR lng_micro BETWEEN -180000000 AND 180000000", name="lng_in_range"
        ),
        # Partial — a soft-deleted warehouse's code can be reused.
        Index(
            "uq_warehouses_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_warehouses_org_name", "organization_id", "name"),
    )


class PurchaseOrder(Base, TenantBase):
    """A purchase order raised against a supplier.

    `number` (`PO-2026-0148`) is the human identity, unique within an organization. `supplier_id`
    is a real in-schema FK; `supplier_name`, `category` and `warehouse` are denormalised so a PO
    row renders without a join. `raised_by_id` is a plain directory UUID used for OWN scope (the
    POs a caller raised); `raised_by` is the display name.

    The PO **total is not stored** — it is the sum of its lines' `amount_minor`, computed on read,
    because money is exact and a duplicated total is one more thing to drift (ADR-0000 §8).
    """

    __tablename__ = "purchase_orders"

    number: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The supplier this PO is against. A real FK — same schema, so allowed.
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Denormalised supplier display name, for the list view.
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Denormalised from the supplier: raw_materials | packaging | components | mro | ...
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: draft | approved | received | closed | cancelled. `String(20)`, no CHECK — the vocabulary
    #: lives in `app.domain.PO_TRANSITIONS`, enforced by the write path, not the column.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Destination warehouse — a denormalised name string, matching the wire.
    warehouse: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    raised_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Directory id of the person who raised it, for OWN scope. A plain UUID, never a FK.
    raised_by_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    created_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: When the delivery landed. NULL until received.
    received_date: Mapped[date | None] = mapped_column(Date)

    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_purchase_orders_org_number"),
        Index("ix_purchase_orders_org_status", "organization_id", "status"),
        Index("ix_purchase_orders_org_supplier", "organization_id", "supplier_id"),
        Index("ix_purchase_orders_org_category", "organization_id", "category"),
        Index("ix_purchase_orders_org_raised_by", "organization_id", "raised_by_id"),
        Index("ix_purchase_orders_org_expected", "organization_id", "expected_date"),
    )


class PurchaseOrderLine(Base, TenantBase):
    """One line item on a purchase order.

    `unit_price_minor` is per-unit integer minor units of the PO currency; `amount_minor` is
    `quantity * unit_price_minor`, stored so the lines **foot to the PO total** and the sum is a
    database fact rather than a UI computation. `received_qty` tracks how much of the ordered
    quantity has landed — it becomes the ordered quantity when the PO is received.
    """

    __tablename__ = "purchase_order_lines"

    #: The PO this line belongs to. A real FK — same schema — removed with its PO.
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    #: Per-unit price, integer minor units of the PO currency.
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Line total = quantity * unit_price_minor. Stored so the lines foot to the PO total.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    received_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The exact cumulative quantity received against this line, in milli-units of the line's
    #: **stock** UOM (the item's own unit — a receipt entered in boxes is converted first, see
    #: `app.receiving.domain.to_stock_milli`). `received_qty` above is the whole-unit
    #: compatibility view (`received_milli // 1000`); this is the figure partial, short and
    #: over-receipt are decided on, always under the PO row's lock (inventory Phase 2).
    received_milli: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: The buyer closed the line short: nothing more is expected, and it no longer holds the PO
    #: open. A closed line takes no further receipt.
    short_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint("received_milli >= 0", name="received_milli_non_negative"),
        Index(
            "ix_purchase_order_lines_org_po",
            "organization_id",
            "purchase_order_id",
        ),
    )


#: Stock statuses — a bucket dimension, so a status change is a ledger move (ADR-0014 §4).
#: RESERVED/ALLOCATED are not here: a reservation does not move stock, it narrows what is
#: available (`inventory_balances.reserved_milli`).
STOCK_STATUSES = (
    "AVAILABLE",
    "QUALITY_HOLD",
    "QUARANTINE",
    "DAMAGED",
    "REJECTED",
    "WIP",
    "IN_TRANSIT",
    "AT_JOB_WORKER",
    "BLOCKED",
)
#: Held but not on the premises — reported separately, never in physical on-hand.
OFF_SITE_STATUSES = ("IN_TRANSIT", "AT_JOB_WORKER")
_STATUS_SQL = ", ".join(f"'{s}'" for s in STOCK_STATUSES)


class Location(Base, TenantBase):
    """A place stock sits inside a warehouse (ADR-0014, plan §2; kind vocabulary extended by
    ADR-0024 §5 for fulfilment: zone, aisle, rack, shelf, bin, staging, pack_station, dock,
    receiving, returns, quarantine).

    Every warehouse gets one `is_default` bin at creation; the pre-Phase-1 single-location API
    posts there. `parent_id` builds the tree; a bin with no parent sits directly in its warehouse.
    """

    __tablename__ = "locations"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    #: The floor this location sits on (warehouse geometry, plan §2/F7) — `NULL` until a
    #: published layout places it. Stable identity, not versioned: unlike its *position* on that
    #: floor (`LocationPlacement`, scoped to a `layout_version` so a draft's coordinates never
    #: leak into what is published), which floor a location belongs to is one fact — but it is
    #: still only ever written on *publish* (`app.warehouse.services.publish_layout_version`),
    #: never from a draft save, so it too never reflects a layout that was merely proposed.
    floor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.warehouse_floors.id", ondelete="RESTRICT")
    )
    #: zone | aisle | rack | shelf | bin | staging | pack_station | dock | receiving | returns |
    #: quarantine (ADR-0024 §5, amending ADR-0014 §1's zone|rack|bin). A kind is *where* stock
    #: is, never *what condition* it is in — that is `STOCK_STATUSES`, a separate axis carried by
    #: the ledger, not this column.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="bin")
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Scanned/printed value resolving straight to this location. Unique per org where set;
    #: `NULL` for a location with no label yet.
    barcode: Mapped[str | None] = mapped_column(String(64))
    #: Orders a picker's walk through a zone. No uniqueness — ties and gaps are both fine.
    pick_sequence: Mapped[int | None] = mapped_column(Integer)
    max_weight_grams: Mapped[int | None] = mapped_column(BigInteger)
    max_volume_milli: Mapped[int | None] = mapped_column(BigInteger)
    #: Free text for now (no fixed vocabulary yet — a later phase's call per the fulfilment plan).
    storage_type: Mapped[str | None] = mapped_column(String(30))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('zone', 'aisle', 'rack', 'shelf', 'bin', 'staging', 'pack_station', "
            "'dock', 'receiving', 'returns', 'quarantine')",
            name="kind_known",
        ),
        CheckConstraint(
            "max_weight_grams IS NULL OR max_weight_grams >= 0",
            name="max_weight_grams_non_negative",
        ),
        CheckConstraint(
            "max_volume_milli IS NULL OR max_volume_milli >= 0",
            name="max_volume_milli_non_negative",
        ),
        Index(
            "uq_locations_org_warehouse_code",
            "organization_id",
            "warehouse_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # At most one default bin per warehouse.
        Index(
            "uq_locations_org_warehouse_default",
            "organization_id",
            "warehouse_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        Index(
            "uq_locations_org_barcode",
            "organization_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_locations_org_floor", "organization_id", "floor_id"),
    )


class Uom(Base, TenantBase):
    """A unit of measure the organization stocks in (ADR-0014 Phase 1, plan §2).

    Seeded per organization from the fixed code registry (`app.inventory.quantity.UNITS`,
    `is_system=True`), plus one row for every free-text unit an item used before this table
    existed, or a caller creates by naming an unrecognised code — `is_system=False` marks the
    difference, but both are equally real: an org's stock UOM directory is never missing a unit
    a live item still names (backfill migration `37e17a153b64`; the live write path applies the
    same rule going forward, in `app.inventory.services.resolve_or_create_uom`).

    `dimension`/`is_discrete` mirror the registry's own `Unit` for a code the registry knows; a
    unit the registry does not know gets `dimension="other"` and `is_discrete=False` —
    continuous until an item's own `uom_conversions` row says otherwise.
    """

    __tablename__ = "uoms"

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    #: length | mass | count | area | volume | other.
    dimension: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    is_discrete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: True for a unit this migration or a later reconcile seeded from the fixed registry; False
    #: for one an org's own data (free text, or a caller) invented.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # Partial — a soft-deleted unit's code can be reused.
        Index(
            "uq_uoms_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_uoms_org_dimension", "organization_id", "dimension"),
    )


class ItemCategory(Base, TenantBase):
    """A node in the item master's own category tree (ADR-0014 Phase 1, plan §2) — a finer,
    org-authored classification ("Fabrics > Cotton > 180 GSM") than the fixed procurement
    `category` enum `InventoryItem.category` already carries (`raw_materials`, `components`, …),
    which stays exactly as it is: this tree is optional and additive, not a replacement.

    `parent_id` builds the tree, the same adjacency-list shape `Location` already uses for
    zone → rack → bin — no `ltree` extension, because a two- or three-level catalogue tree never
    needs the recursive-descendant query that would justify one.
    """

    __tablename__ = "item_categories"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.item_categories.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        # Partial — a soft-deleted category's code can be reused.
        Index(
            "uq_item_categories_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_item_categories_org_parent", "organization_id", "parent_id"),
    )


#: An attribute's value lives in exactly one of these columns (ADR-0014 Phase 1: "typed values
#: only"). Numeric attributes (GSM, width in mm) are integers, never floats — the same house rule
#: that already governs money and quantities.
ATTRIBUTE_TYPES = ("text", "number", "boolean", "date")
_ATTRIBUTE_TYPE_SQL = ", ".join(f"'{t}'" for t in ATTRIBUTE_TYPES)


class InventoryItem(Base, TenantBase):
    """The item master: one stocked material, identified by `sku` within the organization.

    Stock is **not stored here** (ADR-0014 §10). `on_hand_milli` / `reserved_milli` are SQL
    aggregates over `inventory_balances`, attached below as column properties so they can be
    filtered and sorted in SQL, and `on_hand` is the whole-unit compatibility view of them.
    Low-stock is derived (`on_hand_milli <= reorder_point * 1000`), never a stored flag.

    `warehouse` is the denormalised display name the wire has always carried; `home_location_id`
    is the default bin of that warehouse, where single-location writes (opening stock, PO receipt,
    adjustments, counts) post until Phase 1 brings per-location writes.

    `uom` stays the denormalised code string every reader (the kernel included — `describe()`,
    every shortfall message) has always read synchronously; `uom_id` is the Phase 1 foreign key
    that gives it referential integrity, kept in sync with `uom` by the write path exactly the
    way `warehouse` (string) and `home_location_id` (FK) already coexist for the same reason: the
    posting kernel reads `item.uom` as a plain column on an already-loaded row, never through a
    relationship that would need an extra join or an async lazy-load to resolve.
    """

    __tablename__ = "inventory_items"

    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: raw_materials | packaging | components | mro | logistics | services.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Denormalised warehouse name, matching the wire.
    warehouse: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    home_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )

    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The stock UOM every quantity of this item is counted in (milli-units of it) — denormalised
    #: code string, synced with `uom_id`.
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    #: The `uoms` row `uom` names. `RESTRICT`: a unit still in use by an item cannot be deleted.
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.uoms.id", ondelete="RESTRICT")
    )
    #: Per-unit stock value, integer minor units of `currency`.
    unit_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: When stock last moved. Surfaced as `updated_at` on the wire.
    stocked_at: Mapped[date | None] = mapped_column(Date)
    #: A free-text skill tag (the same vocabulary `WorkerSkill.skill` uses) a picker needs to be
    #: offered a task touching this item — `NULL` (the default, every existing item) means no
    #: skill is required. Auto-assignment's own scope: schema-only for now, read by
    #: `app.fulfilment.services._required_skills_for_task`; no write path yet exposes it on
    #: `ItemIn`/`ItemPatch` — the same "later phase adds the write path, not a migration"
    #: precedent `WorkerPositionSample` sets below for coarse location pings.
    required_skill: Mapped[str | None] = mapped_column(String(40))

    #: What a customer order line is priced at, per one whole stock UOM — integer minor units of
    #: `selling_price_currency` (ADR-0024 §3, amending ADR-0014: `unit_value_minor` above is
    #: **cost**, on the valuation side of §8, and using it as a selling price would make every
    #: margin figure the platform computes equal to zero). Both null or both set
    #: (`selling_price_currency_matches_minor`); an item with neither can still go on an order,
    #: but only with an explicit per-line price (`app.orders.services`). Never itself changed by
    #: an order — a line snapshots this at `place` so a later edit here never reprices one already
    #: written.
    selling_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    selling_price_currency: Mapped[str | None] = mapped_column(String(3))

    # --- item master (ADR-0014 Phase 1, plan §2) --------------------------------------------
    #: The org's own category tree — optional, additive to the fixed `category` enum above.
    item_category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.item_categories.id", ondelete="RESTRICT")
    )
    #: An alternate unit this item is also reported/purchased in (e.g. stocked in `kg`, bought in
    #: `box`); the ratio between it and the stock `uom` is a `uom_conversions` row keyed on this
    #: item and this unit. `RESTRICT`: a unit still named here cannot be deleted.
    secondary_uom_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.uoms.id", ondelete="RESTRICT")
    )
    #: India's Harmonized System Nomenclature code — tax classification, optional.
    hsn_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    #: Whether this item's stock is tracked by lot, by roll, by expiry date, or holds a quality
    #: status distinct from its ledger bucket status. All optional, all default off: most items
    #: (fasteners, MRO) track none of them.
    track_lot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    track_roll: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    track_expiry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    track_quality: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The item master's own lifecycle flag — distinct from `deleted_at`: an inactive item still
    #: keeps its ledger and its purchase-order history and can still be filtered into a list; a
    #: deleted item cannot. Plain reference data today — nothing yet refuses a write because an
    #: item is inactive; that belongs to whichever later phase adds the transaction it should gate.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: How far above the ordered quantity a purchase-order line of this item may be received, in
    #: basis points of the ordered quantity (500 = 5 %). 0 (the default) means exactly the
    #: ordered quantity, never more. Integer basis points, not a float percentage, so the limit
    #: is exact integer arithmetic (`app.receiving.domain.tolerance_limit_milli`).
    receipt_tolerance_bp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: The code printed on this item's label, or the manufacturer's EAN/UPC when it is the thing
    #: that gets scanned (inventory Phase 9, ADR-0027). Unique per organization among live items;
    #: `NULL` means "scan the SKU" — the label falls back to `og:item:<sku>`.
    barcode: Mapped[str | None] = mapped_column(String(64))

    if TYPE_CHECKING:
        #: Physical stock in milli-units — every bucket except the off-site ones — and the open
        #: reservations against it. Column properties, attached after `InventoryBalance` exists.
        on_hand_milli: Mapped[int]
        reserved_milli: Mapped[int]
        available_milli: Mapped[int]

    @property
    def on_hand(self) -> int:
        """Whole-unit compatibility view (`on_hand_qty` on the wire is the exact figure)."""
        return quantity.whole_units(self.on_hand_milli)

    __table_args__ = (
        # Partial — a soft-deleted item's SKU can be reused (a purchase-order line joins on it).
        Index(
            "uq_inventory_items_org_sku",
            "organization_id",
            "sku",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_inventory_items_org_barcode",
            "organization_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_inventory_items_org_warehouse", "organization_id", "warehouse"),
        Index("ix_inventory_items_org_category", "organization_id", "category"),
        Index("ix_inventory_items_org_uom", "organization_id", "uom_id"),
        Index("ix_inventory_items_org_item_category", "organization_id", "item_category_id"),
        CheckConstraint(
            "(selling_price_minor IS NULL) = (selling_price_currency IS NULL)",
            name="selling_price_currency_matches_minor",
        ),
        CheckConstraint(
            "selling_price_minor IS NULL OR selling_price_minor >= 0",
            name="selling_price_non_negative",
        ),
        CheckConstraint(
            "receipt_tolerance_bp BETWEEN 0 AND 10000", name="receipt_tolerance_bp_range"
        ),
    )


class UomConversion(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """An item-specific rational conversion between an alternate unit and the item's own stock
    UOM — "1 box = 12 pcs" (`numerator=12, denominator=1`) — never a float (ADR-0014 §2). The
    same unit code can carry a different ratio for a different item (one supplier's carton of
    this fabric is 40 m; another item's carton is 25), so the conversion belongs to the item, not
    to the unit.

    `app.inventory.quantity.convert_by_ratio` does the arithmetic; this row only carries the
    exact integer ratio it needs.
    """

    __tablename__ = "uom_conversions"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The alternate unit this ratio converts, e.g. "box".
    uom_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.uoms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: 1 unit of `uom_id` = numerator/denominator units of the item's own stock UOM.
    numerator: Mapped[int] = mapped_column(BigInteger, nullable=False)
    denominator: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("numerator > 0", name="numerator_positive"),
        CheckConstraint("denominator > 0", name="denominator_positive"),
        Index(
            "uq_uom_conversions_org_item_uom",
            "organization_id",
            "inventory_item_id",
            "uom_id",
            unique=True,
        ),
    )


class ItemAttribute(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One typed key/value fact about an item — GSM, width, shade, composition, count,
    construction, finish, grade, season, collection, or any other org-defined key (ADR-0014
    Phase 1, plan §2). Textile presets are just data; nothing here is hardcoded to them.

    **Typed values only.** `value_type` says which of `value_text` / `value_number` /
    `value_boolean` / `value_date` is the real one; the others are NULL, enforced by
    `value_matches_type`. There is no untyped `value` column to mass-assign into — a caller must
    say what kind of fact this is, and the write path (`app.inventory.services.set_item_attribute`)
    rejects a payload that does not name a `value_type` from `ATTRIBUTE_TYPES` before it ever
    reaches this table. `value_number` is an integer (GSM, mm) — attributes are not stock, but
    the no-float house rule still applies to them (ADR-0014 §2).
    """

    __tablename__ = "item_attributes"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: text | number | boolean | date.
    value_type: Mapped[str] = mapped_column(String(10), nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(500))
    value_number: Mapped[int | None] = mapped_column(BigInteger)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"value_type IN ({_ATTRIBUTE_TYPE_SQL})", name="value_type_known"),
        # Ties `value_type` to *its own* column, not merely "exactly one of the four is set" —
        # each `(value_type = 'x') = (value_x IS NOT NULL)` equality forces both halves true
        # together (the named column is set when its type is named) and both false together (a
        # column stays NULL when its type is not the one named), so all four in one row
        # simultaneously pin down exactly the matching column, never a mismatched one
        # (`value_type='number'` with `value_text` set would violate this even though the old
        # `exactly_one_value` phrasing — count of non-null columns == 1 — could not tell that
        # case from a correctly typed row). M1, found by direct-insert review.
        CheckConstraint(
            "(value_type = 'text') = (value_text IS NOT NULL) AND "
            "(value_type = 'number') = (value_number IS NOT NULL) AND "
            "(value_type = 'boolean') = (value_boolean IS NOT NULL) AND "
            "(value_type = 'date') = (value_date IS NOT NULL)",
            name="value_matches_type",
        ),
        Index(
            "uq_item_attributes_org_item_key",
            "organization_id",
            "inventory_item_id",
            "key",
            unique=True,
        ),
    )


class ItemVariant(Base, TenantBase):
    """One purchasable/stockable variant of an item master row — a size, a shade, a length cut —
    with its own SKU (ADR-0014 Phase 1, plan §2). Variant-specific typed attributes are not
    modelled yet; a variant's own facts beyond its name are a later phase's addition, tracked the
    same way `item_attributes` tracks an item's.
    """

    __tablename__ = "item_variants"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Partial — a soft-deleted variant's SKU can be reused.
        Index(
            "uq_item_variants_org_sku",
            "organization_id",
            "sku",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_item_variants_org_item", "organization_id", "inventory_item_id"),
    )


def _bucket_lot_roll_fks(table: str) -> tuple[ForeignKeyConstraint, ForeignKeyConstraint]:
    """The organization-scoped foreign keys every table carrying a bucket key shares (ADR-0014 §5:
    "their foreign keys arrive with the lots/rolls tables in Phase 1"). Composite on
    `(organization_id, lot_id|roll_id)`, so the referenced row must be the same tenant's; a NULL
    `lot_id`/`roll_id` (a bucket with no lot or roll) is not checked, as `MATCH SIMPLE` does."""
    return (
        ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name=f"fk_{table}_org_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name=f"fk_{table}_org_roll",
            ondelete="RESTRICT",
        ),
    )


#: A lot's quality disposition (ADR-0014 Phase 1). Phase 4 (QC) owns the transitions into and out
#: of these; Phase 1 only records and filters on them.
LOT_QUALITY_STATUSES = ("PENDING", "APPROVED", "ON_HOLD", "REJECTED")
_LOT_QUALITY_SQL = ", ".join(f"'{s}'" for s in LOT_QUALITY_STATUSES)
#: A roll is whole until it is split or merged away; `app.domain.ROLL_TRANSITIONS` is the map.
ROLL_STATES = ("ACTIVE", "SPLIT", "MERGED")
_ROLL_STATE_SQL = ", ".join(f"'{s}'" for s in ROLL_STATES)
#: What a `roll_events` row records. SPLIT/MERGED sit on the roll that was consumed;
#: SPLIT_CHILD/MERGE_RESULT on the roll that came out, with `related_roll_id` pointing back.
ROLL_EVENT_KINDS = (
    "CREATED",
    "OPENING",
    "SPLIT",
    "SPLIT_CHILD",
    "MERGED",
    "MERGE_RESULT",
    "MOVED",
    "STATUS_CHANGED",
    "MEASURED",
)
_ROLL_EVENT_SQL = ", ".join(f"'{s}'" for s in ROLL_EVENT_KINDS)
#: Why one lot is the parent of another. SPLIT/MERGE are the self-edges a roll split or merge
#: leaves inside one lot (evidence tied to the move, never traversed); the rest link two lots.
LOT_GENEALOGY_RELATIONS = ("SPLIT", "MERGE", "BLEND", "REWORK", "DERIVED")
_LOT_RELATION_SQL = ", ".join(f"'{s}'" for s in LOT_GENEALOGY_RELATIONS)


class Lot(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A production or supply batch of one item (ADR-0014 §5, Phase 1 I3) — the unit quality and
    traceability are tracked in. A bucket's `lot_id` (ledger, balances, discrepancies) is a real,
    organization-scoped foreign key to this table: `(organization_id, id)` is its target, so a
    bucket can never name another tenant's lot even if a query forgot the filter.

    `lot_no` is the organization's own number and unique across it; `supplier_lot_no` is whatever
    the supplier printed and is not unique (two suppliers may reuse a number). `attributes` is a
    small flat bag of org-defined facts (text, integers, booleans — never floats; see
    `app.schemas.LotIn`), not stock.
    """

    __tablename__ = "lots"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lot_no: Mapped[str] = mapped_column(String(60), nullable=False)
    supplier_lot_no: Mapped[str | None] = mapped_column(String(60))
    #: A code someone else printed (a supplier's lot label); `NULL` means "scan the lot number".
    barcode: Mapped[str | None] = mapped_column(String(64))
    manufactured_on: Mapped[date | None] = mapped_column(Date)
    received_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    quality_status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    #: The document this lot came from (`purchase_order`, `production_order`, …) — a plain UUID
    #: pair, never a cross-entity FK.
    source_type: Mapped[str | None] = mapped_column(String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"quality_status IN ({_LOT_QUALITY_SQL})", name="quality_status_known"),
        CheckConstraint("(source_type IS NULL) = (source_id IS NULL)", name="source_matches_id"),
        # The target of the organization-scoped foreign keys on the ledger, balances,
        # discrepancies, rolls and lot_genealogy.
        UniqueConstraint("organization_id", "id", name="uq_lots_org_id"),
        Index("uq_lots_org_lot_no", "organization_id", "lot_no", unique=True),
        Index(
            "uq_lots_org_barcode",
            "organization_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
        Index("ix_lots_org_item", "organization_id", "inventory_item_id"),
        Index("ix_lots_org_created", "organization_id", text("created_at DESC"), text("id DESC")),
    )


class Roll(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One physical roll (ADR-0014 §2 — a roll's length and weight are two *measured* quantities,
    never derived from each other): `primary_milli` in the item's stock UOM (82.5 m is `82500`)
    and `secondary_milli` in `secondary_uom` (14.8 kg is `14800`). The ledger, not this row, says
    how much of the roll is actually in a bucket; these are what the roll measured as when it was
    created or last re-measured, and what a split apportions.

    `parent_roll_id` is set on a split's children. A merge's result has no single parent — its
    sources are `roll_events` `MERGE_RESULT` rows — so the trace walks both
    (`app.inventory.selectors.trace_rolls`).
    """

    __tablename__ = "rolls"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_no: Mapped[str] = mapped_column(String(60), nullable=False)
    #: A code someone else printed (a mill's roll label); `NULL` means "scan the roll number".
    barcode: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")
    primary_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    secondary_milli: Mapped[int | None] = mapped_column(BigInteger)
    secondary_uom: Mapped[str | None] = mapped_column(String(10))
    width_mm: Mapped[int | None] = mapped_column(Integer)
    gsm: Mapped[int | None] = mapped_column(Integer)
    shade: Mapped[str | None] = mapped_column(String(40))
    grade: Mapped[str | None] = mapped_column(String(20))
    parent_roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"state IN ({_ROLL_STATE_SQL})", name="state_known"),
        CheckConstraint("primary_milli >= 0", name="primary_non_negative"),
        CheckConstraint(
            "secondary_milli IS NULL OR secondary_milli >= 0", name="secondary_non_negative"
        ),
        CheckConstraint(
            "(secondary_milli IS NULL) = (secondary_uom IS NULL)", name="secondary_uom_matches"
        ),
        CheckConstraint("width_mm IS NULL OR width_mm > 0", name="width_positive"),
        CheckConstraint("gsm IS NULL OR gsm > 0", name="gsm_positive"),
        CheckConstraint("parent_roll_id IS NULL OR parent_roll_id <> id", name="not_own_parent"),
        ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_rolls_org_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_rolls_org_parent",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("organization_id", "id", name="uq_rolls_org_id"),
        Index("uq_rolls_org_roll_no", "organization_id", "roll_no", unique=True),
        Index(
            "uq_rolls_org_barcode",
            "organization_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
        Index("ix_rolls_org_item", "organization_id", "inventory_item_id"),
        Index(
            "ix_rolls_org_lot",
            "organization_id",
            "lot_id",
            postgresql_where=text("lot_id IS NOT NULL"),
        ),
        Index(
            "ix_rolls_org_parent",
            "organization_id",
            "parent_roll_id",
            postgresql_where=text("parent_roll_id IS NOT NULL"),
        ),
        Index("ix_rolls_org_created", "organization_id", text("created_at DESC"), text("id DESC")),
    )


class RollEvent(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only fact in a roll's life — created, opened into stock, split, merged, moved,
    re-statused. `related_roll_id` names the other roll in a split or merge, which is what makes
    the roll trace a graph walk. `UPDATE`/`DELETE` are revoked from the app role."""

    __tablename__ = "roll_events"

    roll_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    related_roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    move_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT"),
    )
    quantity_milli: Mapped[int | None] = mapped_column(BigInteger)
    secondary_milli: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"kind IN ({_ROLL_EVENT_SQL})", name="kind_known"),
        ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_roll_events_org_roll",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "related_roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_roll_events_org_related",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_roll_events_org_roll",
            "organization_id",
            "roll_id",
            text("occurred_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_roll_events_org_related",
            "organization_id",
            "related_roll_id",
            postgresql_where=text("related_roll_id IS NOT NULL"),
        ),
    )


class LotGenealogy(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """`parent_lot_id` -> `child_lot_id`: this lot's material went into that one (ADR-0014 §5).
    Append-only. A roll split or merge inside one lot leaves a self-edge (`parent == child`)
    tied to its move — evidence for the lot's history, skipped by the trace; `BLEND`/`REWORK`/
    `DERIVED` edges link two different lots and are what the trace follows."""

    __tablename__ = "lot_genealogy"

    parent_lot_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    child_lot_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(String(10), nullable=False)
    move_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT"),
    )
    quantity_milli: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"relation IN ({_LOT_RELATION_SQL})", name="relation_known"),
        CheckConstraint(
            "parent_lot_id <> child_lot_id OR relation IN ('SPLIT', 'MERGE')",
            name="self_edge_only_for_roll_ops",
        ),
        ForeignKeyConstraint(
            ["organization_id", "parent_lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_lot_genealogy_org_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "child_lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_lot_genealogy_org_child",
            ondelete="RESTRICT",
        ),
        Index("ix_lot_genealogy_org_parent", "organization_id", "parent_lot_id"),
        Index("ix_lot_genealogy_org_child", "organization_id", "child_lot_id"),
    )


class InventoryMove(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """The header of one stock posting — what happened and why. Its `inventory_ledger` rows say
    which buckets moved by how much. Append-only, like the ledger.

    `idempotency_key` + `request_hash` make a retried post a replay rather than a second move:
    the key is unique per organization, and the hash tells a genuine retry from a key reused for
    a different request (which is refused).
    """

    __tablename__ = "inventory_moves"

    #: opening | receipt | adjustment | count | relocation | issue — vocabulary in
    #: `app.inventory.ledger.MOVE_KINDS`, enforced by the write path.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: damaged | lost | found | consumed | returned | other. Required with `kind == "adjustment"`.
    reason: Mapped[str | None] = mapped_column(String(30))
    #: "purchase_order" when a receipt posts against one. A plain UUID, never a cross-entity FK.
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index(
            "uq_inventory_moves_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_inventory_moves_org_reference", "organization_id", "reference_id"),
        Index("ix_inventory_moves_org_occurred", "organization_id", text("occurred_on DESC")),
    )


class PutawayCapture(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """The proof that goes with a storage put-away: which bin was scanned, what the app suggested,
    who did it, and — when the device gave one — where the worker stood. One row per put-away
    move, written in the same transaction as the move and **append-only** (`UPDATE`/`DELETE`
    revoked from the app role, re-applied in `migrations/env.py`; the 30-day purge is an
    admin-run procedure, `tools/purge_putaway_captures.py`).

    **The bin is the location of record; the GPS fix is never used to say where stock is.** The
    coordinates are audit evidence only. They are integer micro-degrees (no float), plaintext
    behind a dedicated permission (`operations.inventory.putaway.location_read`) whose every
    successful use writes an audit row, and they appear in no other response, audit `after`,
    event payload or log line.

    `worker_id` is nullable: a manager with no worker row can still put stock away. `created_by`
    is the actor's directory id and is always set. `captured_at` is the client's claimed time of
    the fix (clamped by the service); `created_at` is the server's.
    """

    __tablename__ = "putaway_captures"

    move_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The scanned destination bin.
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    suggested_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.workers.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lat_micro: Mapped[int | None] = mapped_column(BigInteger)
    lng_micro: Mapped[int | None] = mapped_column(BigInteger)
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Whether the scanned bin was the one the server's own rule suggests — computed server-side
    #: from `domain.suggest_putaway_bin`, never taken from the client. NULL when it suggested none.
    matched_suggestion: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        CheckConstraint("(lat_micro IS NULL) = (lng_micro IS NULL)", name="coords_paired"),
        CheckConstraint(
            "lat_micro IS NULL OR lat_micro BETWEEN -90000000 AND 90000000", name="lat_in_range"
        ),
        CheckConstraint(
            "lng_micro IS NULL OR lng_micro BETWEEN -180000000 AND 180000000", name="lng_in_range"
        ),
        CheckConstraint("accuracy_m IS NULL OR accuracy_m >= 0", name="accuracy_non_negative"),
        Index("uq_putaway_captures_org_move", "organization_id", "move_id", unique=True),
        Index("ix_putaway_captures_org_location", "organization_id", "location_id"),
        # Not tenant-leading, on purpose: the 30-day purge (`tools/purge_putaway_captures.py`)
        # filters `created_at < cutoff` and batches `ORDER BY created_at, id` across every
        # organization, and no tenant-leading index can serve that.
        Index("ix_putaway_captures_created_at", "created_at"),
    )


class InventoryImportRun(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One committed bulk import (inventory Phase 9, ADR-0027) — append-only, written in the same
    transaction as the rows it created. A preview or a refused import writes nothing here.

    `idempotency_key` + `request_hash` make a retried commit a replay (the stored `summary` is
    returned) and a reused key with a different file a 409, the same contract `inventory_moves`
    gives a stock posting.
    """

    __tablename__ = "inventory_import_runs"

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: items | locations | lots | rolls | opening_stock
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    on_duplicate: Mapped[str] = mapped_column(String(10), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The response the first commit returned, replayed verbatim.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index(
            "uq_inventory_import_runs_org_key", "organization_id", "idempotency_key", unique=True
        ),
        Index(
            "ix_inventory_import_runs_org_created",
            "organization_id",
            text("created_at DESC"),
        ),
    )


class LedgerEntry(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only change to one bucket — the authoritative record of stock (ADR-0014 §3).

    `balance_after_milli` is the bucket's balance once this row applied, so the stored balance
    must equal the latest row's `balance_after_milli` and the `SUM(quantity_milli)` of the bucket.
    `UPDATE`/`DELETE` are revoked from the app role; a wrong entry is corrected by an opposite
    move. This is the table formerly called `stock_movements`; its row ids are unchanged.
    """

    __tablename__ = "inventory_ledger"

    move_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    #: Lot / roll bucket keys — organization-scoped foreign keys to `lots`/`rolls` (Phase 1).
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Signed change, milli-units of the item's UOM. Zero only on a count that found no change.
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Per-unit cost of an inbound row, integer minor units — the costing input (ADR-0014 §8).
    #: Its currency is whatever the receipt was priced in — a purchase order's currency, which
    #: is not necessarily the item's `currency` — recorded as-is and never converted (§8, revised
    #: for the currency defect found in review). NULL exactly when `unit_cost_minor` is NULL: a
    #: cost with no currency, or a currency with no cost, is a number nobody can act on.
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    unit_cost_currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    #: The header this row belongs to. `raise`: always loaded explicitly (`contains_eager`), so
    #: a history page can never degrade into one lazy query per row.
    move: Mapped[InventoryMove] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("balance_after_milli >= 0", name="balance_after_non_negative"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status_known"),
        CheckConstraint(
            "(unit_cost_minor IS NULL) = (unit_cost_currency IS NULL)",
            name="unit_cost_currency_matches_minor",
        ),
        *_bucket_lot_roll_fks("inventory_ledger"),
        Index(
            "ix_inventory_ledger_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_inventory_ledger_org_location_created",
            "organization_id",
            "location_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_inventory_ledger_org_lot_created",
            "organization_id",
            "lot_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("lot_id IS NOT NULL"),
        ),
        Index(
            "ix_inventory_ledger_org_roll_created",
            "organization_id",
            "roll_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("roll_id IS NOT NULL"),
        ),
        Index(
            "ix_inventory_ledger_org_item_created",
            "organization_id",
            "inventory_item_id",
            text("created_at DESC"),
        ),
        Index("ix_inventory_ledger_org_move", "organization_id", "move_id"),
        Index(
            "ix_inventory_ledger_org_bucket",
            "organization_id",
            "inventory_item_id",
            "location_id",
            "status",
        ),
    )


class InventoryBalance(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """The current quantity of one bucket — a projection of the ledger, written only by
    `app.inventory.ledger` in the same transaction as the ledger row, under this row's lock.

    `reserved_milli` caches the open remainder of the bucket's active reservations. Available to
    promise is `quantity_milli - reserved_milli`; the CHECKs make over-reservation and negative
    stock impossible even for a write path that forgot to look.
    """

    __tablename__ = "inventory_balances"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("quantity_milli >= 0", name="quantity_non_negative"),
        CheckConstraint(
            "reserved_milli >= 0 AND reserved_milli <= quantity_milli", name="reserved_within"
        ),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status_known"),
        *_bucket_lot_roll_fks("inventory_balances"),
        Index("ix_inventory_balances_org_location", "organization_id", "location_id"),
        Index(
            "ix_inventory_balances_org_lot",
            "organization_id",
            "lot_id",
            postgresql_where=text("lot_id IS NOT NULL"),
        ),
        Index(
            "ix_inventory_balances_org_roll",
            "organization_id",
            "roll_id",
            postgresql_where=text("roll_id IS NOT NULL"),
        ),
        # One row per bucket. NULLS NOT DISTINCT: a bucket with no lot is still one bucket.
        Index(
            "uq_inventory_balances_bucket",
            "organization_id",
            "inventory_item_id",
            "location_id",
            "status",
            "lot_id",
            "roll_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class InventoryReservation(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Stock promised to a demand (a sales order, a production order) without moving it.

    `quantity_milli - consumed_milli - released_milli - transferred_milli` is the open remainder
    while `status == "active"`; the sum of open remainders per balance is that balance's
    `reserved_milli`. `released_milli` is what a caller has explicitly given back
    (ADR-0014 §11) — kept separate from `consumed_milli` (what an outbound move drew against
    this reservation) so `quantity_milli` stays the original promise forever and never has to
    cross zero, which the `quantity_positive` CHECK would otherwise forbid on a full release of
    an untouched reservation.

    `transferred_milli` (ADR-0024 §6) is what `app.inventory.ledger.post_move` moved to another
    bucket's reservation on a relocation — a pick or a pack, never a real dispatch. It is its own
    bucket, distinct from `consumed_milli` (a real draw-down, dispatch's move) and
    `released_milli` (given back, free again): the demand behind a transferred quantity was not
    met and not freed, it moved with the stock. `transferred_from_reservation_id` is the mirror
    on the *destination* reservation a transfer opens or tops up — `NULL` on every reservation
    `reserve`/`reserve_many` create directly, set on one only when `post_move` created or topped
    it up from another reservation's transfer.
    """

    __tablename__ = "inventory_reservations"

    balance_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_balances.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    consumed_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Given back by `release()`, all at once or in parts. Never reduces `quantity_milli` — the
    #: original promise is kept for the record; the sum with `consumed_milli` is what is no
    #: longer open.
    released_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Drawn off by `post_move`'s reservation transfer (ADR-0024 §6) and opened or topped up on
    #: the destination bucket's reservation — a pick or pack, not a consumption.
    transferred_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: active | released | consumed | transferred — widened from `String(10)`: "transferred" is
    #: 11 characters, one more than the old column ever needed to hold (ADR-0024 §6).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    #: Set only on a reservation `post_move` opened (or topped up) as the destination side of a
    #: transfer — traces it back to the source reservation the quantity moved from. Self-FK
    #: within this schema (not the cross-schema kind ADR-0000 §10 forbids).
    transferred_from_reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_reservations.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(64))
    #: Phase 3: a hold that lapses. `NULL` is a reservation that stays until something closes it
    #: (an order's own, which `orders` releases or consumes). The expiry sweep releases the open
    #: remainder of an active reservation past this instant, through the kernel's own `release`.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set by the sweep (and nothing else) on the reservation it released for lapsing, so a list
    #: can tell "expired" from "released by a person" without a second table.
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "consumed_milli >= 0 AND released_milli >= 0 AND transferred_milli >= 0 "
            "AND consumed_milli + released_milli + transferred_milli <= quantity_milli",
            name="consumed_within",
        ),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed', 'transferred')", name="status_known"
        ),
        Index(
            "uq_inventory_reservations_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_inventory_reservations_org_balance_active",
            "organization_id",
            "balance_id",
            postgresql_where=text("status = 'active'"),
        ),
        # The expiry sweep's one question: which active reservations are past due?
        Index(
            "ix_inventory_reservations_org_expires_active",
            "organization_id",
            "expires_at",
            postgresql_where=text("status = 'active' AND expires_at IS NOT NULL"),
        ),
        Index(
            "ix_inventory_reservations_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )


class InventoryReservationRelease(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only row per `ledger.release` call that actually released something — the
    idempotency record for that call, and the release history a reservation otherwise has no
    record of (ADR-0014 §11).

    `UNIQUE (organization_id, reservation_id, idempotency_key)` is what makes a retried release a
    replay: `ledger.release` looks a key up here under the reservation's own row lock before
    applying anything, so a concurrent duplicate either finds nothing (and proceeds, serialised by
    that same lock) or finds this row and replays instead. A single mutable column on the
    reservation row can only remember the *most recent* key; a reservation released more than
    once, with a different key each time, needs one row per release, not one column overwritten.
    """

    __tablename__ = "inventory_reservation_releases"

    # Overrides `OrgScopedMixin`'s automatic single-column index (INFO): the only read this table
    # ever serves is `ledger.release`'s exact `(organization_id, reservation_id, idempotency_key)`
    # lookup, already served by the composite unique index below, which also leads with
    # `organization_id`. A second, single-column index on the same leading column would only cost
    # every append an extra write with nothing ever reading it alone.
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_reservations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    released_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("released_milli > 0", name="released_milli_positive"),
        Index(
            "uq_inventory_reservation_releases_org_reservation_key",
            "organization_id",
            "reservation_id",
            "idempotency_key",
            unique=True,
        ),
    )


class InventoryDiscrepancy(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A disagreement the reconciler found between a stored figure and what the source of truth
    says it must be. Recorded, surfaced, alerted on — never repaired automatically.

    `kind`: `balance` (stored `quantity_milli` vs `SUM(ledger)`) or `reserved` (stored
    `reserved_milli` vs the open reservations). One open row per bucket and kind; a later run that
    still sees it bumps `last_seen_at`.
    """

    __tablename__ = "inventory_discrepancies"

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    stored_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    __table_args__ = (
        CheckConstraint("kind IN ('balance', 'reserved')", name="kind_known"),
        *_bucket_lot_roll_fks("inventory_discrepancies"),
        Index(
            "uq_inventory_discrepancies_open_bucket",
            "organization_id",
            "kind",
            "inventory_item_id",
            "location_id",
            "status",
            "lot_id",
            "roll_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_inventory_discrepancies_org_detected", "organization_id", "detected_at"),
    )


class OperationalAlert(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One open operational condition F12's alert sweep found — low stock, or a pick task or
    shipment sitting past its SLA. Recorded and surfaced the same way `InventoryDiscrepancy` is:
    a sweep opens (or refreshes) one row per subject and stages an alert event only the first
    time it opens; the condition clearing (stock replenished, the task or shipment reaching a
    terminal status) closes the row automatically on the next sweep. Nothing here repairs
    anything — this is the read model an automation (and the insight dashboard) hangs off, not a
    corrective action.

    `kind`: `low_stock` (an `inventory_items` row), `task_delayed` (a `pick_tasks` row still open
    past `fulfilment_settings.task_sla_minutes`), or `shipment_delayed` (a `shipments` row still
    non-terminal past the same window). `subject_type`/`subject_id` name the row the alert is
    about; `warehouse_id` is denormalised from it so the insight views can group by warehouse
    without a join back to three different tables.
    """

    __tablename__ = "operational_alerts"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: inventory_item | pick_task | shipment.
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Everything the alert event's own payload also carries — enough to render the row without
    #: rejoining the subject table.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: `"system:alert_sweep"` when the condition cleared on its own; a principal id when a person
    #: dismissed it by hand (`POST /operations/alerts/{id}/resolve`).
    resolved_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('low_stock', 'task_delayed', 'shipment_delayed')", name="kind_known"
        ),
        CheckConstraint(
            "subject_type IN ('inventory_item', 'pick_task', 'shipment')",
            name="subject_type_known",
        ),
        Index(
            "uq_operational_alerts_open_subject",
            "organization_id",
            "kind",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_operational_alerts_org_detected", "organization_id", "detected_at"),
        Index("ix_operational_alerts_org_warehouse", "organization_id", "warehouse_id"),
        Index("ix_operational_alerts_org_kind_open", "organization_id", "kind", "resolved_at"),
    )


def _on_hand_expr(column: Any) -> Any:
    # `SUM(bigint)` is NUMERIC in PostgreSQL, which asyncpg hands back as a Decimal; the cast keeps
    # every quantity a plain int from the database to the wire.
    return (
        select(cast(func.coalesce(func.sum(column), 0), BigInteger))
        .where(
            InventoryBalance.organization_id == InventoryItem.organization_id,
            InventoryBalance.inventory_item_id == InventoryItem.id,
            InventoryBalance.status.not_in(OFF_SITE_STATUSES),
        )
        .correlate_except(InventoryBalance)
        .scalar_subquery()
    )


InventoryItem.on_hand_milli = column_property(_on_hand_expr(InventoryBalance.quantity_milli))
InventoryItem.reserved_milli = column_property(_on_hand_expr(InventoryBalance.reserved_milli))
#: The item's derived stock attributes — what a writer re-reads after touching the item's stock.
STOCK_AGGREGATES = ("on_hand_milli", "reserved_milli", "available_milli")

#: Available to promise: only AVAILABLE stock, less what is reserved. Stock on quality hold,
#: damaged, WIP etc. is on hand but not available (plan §2).
InventoryItem.available_milli = column_property(
    select(
        cast(
            func.coalesce(
                func.sum(InventoryBalance.quantity_milli - InventoryBalance.reserved_milli), 0
            ),
            BigInteger,
        )
    )
    .where(
        InventoryBalance.organization_id == InventoryItem.organization_id,
        InventoryBalance.inventory_item_id == InventoryItem.id,
        InventoryBalance.status == "AVAILABLE",
    )
    .correlate_except(InventoryBalance)
    .scalar_subquery()
)


# ------------------------------------------------------------------ orders (ADR-0024, ADR-0025)


class Order(Base, TenantBase):
    """A customer order — the sell-side document ADR-0024 §2 introduces (`app/orders/`), distinct
    from `PurchaseOrder` (buy-side: a supplier, `draft -> approved -> received -> closed`) and
    `storefront.Enquiry` (a pre-sale lead with opaque JSON lines, no pricing, no stock reference).

    `status` runs the vocabulary ADR-0025 §1 fixes: `DRAFT, PLACED, ACCEPTED, IN_FULFILMENT,
    ON_HOLD, READY, HANDED_OVER, DELIVERED, COMPLETED, CANCELLED, REJECTED`. `String(20)`, no
    CHECK — the vocabulary lives in `app.domain.ORDER_TRANSITIONS`, enforced by the write path
    (`app/orders/services.py`), the same precedent `PurchaseOrder.status` already sets for
    `PO_TRANSITIONS`.

    `version` is the optimistic lock ADR-0025 §1 requires on **every** order write: each
    transition takes `SELECT ... FOR UPDATE` on this row, checks the caller's `version` against
    this column (a stale value is a 409), and bumps it by one in the same transaction as the
    status change, the `order_transitions` row, the audit row and the outbox row. Starts at 0.

    `customer_id` is a plain UUID into `services/customer` — never a cross-schema foreign key
    (ADR-0000 §10) — nullable because a walk-in or manual order may carry no CRM record at all.
    `customer_name`/`customer_phone` are a snapshot taken when the order is created (from the CRM
    lookup the frontend performs, or typed by hand for a walk-in), so the order keeps its own
    record of who it was for even if the CRM contact is later renamed or deleted.

    `subtotal_minor`/`total_minor` are **derived and stored**, not computed on every read (unlike
    `PurchaseOrder`'s total, which is never stored) — an order's total is asked for by `accept`'s
    credit check before the lines are necessarily still loaded, and storing it here means that ask
    is a column read, not a join and a sum. Recomputed and rewritten every time `place` reprices
    the lines; untouched after that (ADR-0024 §3's snapshot rule applies to the total the same way
    it applies to each line's price).

    `settings_snapshot` is the resolved `fulfilment_settings` this order was accepted under
    (ADR-0024 §4's "an order records the resolved settings it was accepted under") — a JSONB copy,
    not a foreign key to the settings row, so a later settings edit never reinterprets an
    already-accepted order. NULL until `accept` runs.

    Four more facts, all written by an event consumer or by the READY/handover write paths
    themselves, never by a caller directly (`app/orders/services.py`; Fulfilment plan F4,
    ADR-0025 §1's billing gate). Every one of them is a fact, never a second status
    (ADR-0025 §1's own rule for invoice/payment references):

    * `invoice_id` — a plain UUID into `finance.invoices`, recorded from finance's own
      `finance.orders.invoice.issued` event (`app.consumers.finance_invoice_issued`). Never a
      cross-schema FK (ADR-0000 §10). NULL until finance issues one.
    * `paid_at` — when this order's payment was confirmed. Two writers, and the billing gate does
      not care which: the counter/carrier operator's own attestation at handover
      (`attest_payment_if_confirmed`, cash at handover), and — for an order paid online —
      `services/payment`'s tenant collections (ADR-0024 A4): a **verified** capture (a signed
      webhook or a server-to-server status check, never a browser's word) arrives here as
      `payment.collections.payment.captured` and `app.orders.services.record_online_payment`
      sets it. `paid_via_charge_id` names that collection, so a later full refund of *it* can
      reverse the fact (and a failed refund restore it) without ever touching a paid fact an
      operator attested.
    * `pickup_code` / `pickup_code_verified_at` — issued at READY when
      `fulfilment_settings.require_pickup_code` is set (snapshotted at accept), and verified (the
      caller's presented code matched) in the same transaction as the handover write.
      `pickup_code` itself is **never** returned by `OrderOut` (`app.orders.selectors.order_out`
      deliberately omits it) — only `pickup_code_verified_at` is: the code is only useful as a
      secret the customer presents back, and putting it on an ORG-wide read endpoint would let
      any counter-read holder bypass the point of asking for it. How the code reaches the
      customer at all (SMS/notification) is not built in this slice — see this slice's own report.
    """

    __tablename__ = "orders"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: phone | manual | sales | walk_in.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: See class docstring — vocabulary in `app.domain.ORDER_TRANSITIONS`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )

    #: Plain UUID into `services/customer` — never a cross-schema FK (ADR-0000 §10).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    customer_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    settings_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: See class docstring's "Four more facts" — ADR-0025 §1's billing gate (Fulfilment plan F4).
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The `services/payment` collection whose verified capture set `paid_at` — a plain UUID
    #: (ADR-0000 §10, no cross-schema FK). NULL when the order was not paid online.
    paid_via_charge_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    pickup_code: Mapped[str | None] = mapped_column(String(20))
    pickup_code_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_orders_org_number",
            "organization_id",
            "number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_orders_org_status", "organization_id", "status"),
        Index("ix_orders_org_channel", "organization_id", "channel"),
        Index("ix_orders_org_customer", "organization_id", "customer_id"),
        Index("ix_orders_org_warehouse", "organization_id", "warehouse_id"),
        CheckConstraint("subtotal_minor >= 0", name="subtotal_non_negative"),
        CheckConstraint("total_minor >= 0", name="total_non_negative"),
        CheckConstraint("version >= 0", name="version_non_negative"),
    )


class OrderLine(Base, TenantBase):
    """One line on an order. `quantity_milli` is milli-units of the item's stock UOM
    (ADR-0014 §2, `app.inventory.quantity`).

    `selling_price_minor` is nullable until `place` fills it — from the item's own
    `InventoryItem.selling_price_minor`, unless `price_overridden` says the line already named its
    own price at create time — and **NOT NULL from `place` onward** (ADR-0024 §3): once written
    here, a later change to the item's price never reprices this line. `line_total_minor` is
    `quantity_milli * selling_price_minor / 1000`, rounded half-to-even once
    (`app.domain.order_line_amount_minor`), filled at the same moment as the price.
    """

    __tablename__ = "order_lines"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Whether the line named its own price at create time — kept so `place` knows not to
    #: overwrite a deliberate override with the item's current price.
    price_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selling_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    line_total_minor: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "selling_price_minor IS NULL OR selling_price_minor >= 0",
            name="selling_price_non_negative",
        ),
        Index("ix_order_lines_org_order", "organization_id", "order_id"),
        Index("ix_order_lines_org_item", "organization_id", "inventory_item_id"),
    )


class OrderTransition(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only row per order status change (ADR-0025 §4) — `order_transitions` joins
    `APPEND_ONLY_TABLES` in `services/operations/migrations/env.py`, the same privilege-enforced
    append-only rule the stock ledger and `inventory_reservation_releases` already carry.

    Never edited or deleted: a reopened or reversed order is a new row here, never a rewritten
    old one (ADR-0000 §9).
    """

    __tablename__ = "order_transitions"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_order_transitions_org_order", "organization_id", "order_id", "at"),)


class FulfilmentSettings(Base, TenantBase):
    """Per-organization fulfilment policy (ADR-0024 §4) — the platform's first per-org settings
    table. One row has `warehouse_id IS NULL` (the organization's default); an optional second row
    per warehouse overrides it **wholesale** — every column, not merged field by field, which
    keeps resolution a single "prefer the warehouse row, else the org row, else a hardcoded
    default" lookup (`app.orders.selectors.resolve_settings`) rather than a per-column merge no
    caller has asked for yet. Two partial unique indexes enforce at most one of each.

    `invoice_timing` says which order transition should make `operations` ask finance for an
    invoice (`on_accept` | `on_dispatch` | `on_delivery`) — read by the billing gate (ADR-0025
    §1's `READY -> HANDED_OVER` guard), not built until dispatch exists (F4/F5).

    `payment_defaults` maps a `channel` (`phone | manual | sales | walk_in`) to a payment term
    (`prepaid | cash | credit`) — free-form JSON rather than one column per channel, since the
    channel vocabulary is itself app-level, not a schema fact. A channel missing from the map
    defaults to `"cash"` wherever it is read, never `"credit"` — an unconfigured org's orders
    never silently gain a credit check.

    `offer_timeout_seconds`/`offer_rounds` govern the worker-offer rounds `app/fulfilment/` (F3)
    will run; carried here now because they are one settings row with the rest, not because F2
    reads them.

    `require_pickup_code` and `default_tax_rate_pct` are F4's own additions (ADR-0025 §1's
    billing gate; Fulfilment plan F4). `require_pickup_code` defaults `False` so an existing
    organization's counter flow is unaffected until it opts in. `default_tax_rate_pct` is the
    document-level GST rate `request_invoice_for_order` uses when the order names no rate of its
    own (F2 carries no per-order tax field yet — a documented gap, see this slice's own report);
    `Numeric(5, 2)`, the same shape `services/finance`'s own `tax_rate_pct` columns use, since
    this value only ever travels to finance inside an `invoice_requested` payload.

    `auto_assignment_enabled` is auto-assignment's own opt-in (ADR-0024 §9): defaults `False` so
    an org with production dispatchers already relying on F3's manual-only offering sees **no**
    change in behaviour until it explicitly turns this on. Every column here is written through
    `PUT /operations/fulfilment/settings` (INVENTORY-02, `operations.fulfilment.settings.write`).

    `task_sla_minutes`, `alert_email` and `alert_phone` are F12's own additions (fulfilment plan
    §4, operational automation). `task_sla_minutes` is how long a pick task may stay open
    (`domain.PICK_TASK_OPEN_STATUSES`) or a shipment non-terminal before `app.alerts`'s sweep
    opens a `task_delayed`/`shipment_delayed` alert for it. `alert_email`/`alert_phone` are the
    address an operational alert's event payload (`payload.alert_contact.*`) carries so a
    notification automation has somewhere to send it without inventing its own directory lookup —
    null until an organization opts in, in which case the alert carries no contact and only the
    in-product alert list (`GET /operations/alerts`) shows it.
    """

    __tablename__ = "fulfilment_settings"

    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.warehouses.id", ondelete="RESTRICT")
    )
    #: on_accept | on_dispatch | on_delivery.
    #: `on_delivery` by default (INVENTORY-02): the two earlier timings need an order invoice before
    #: handover, and nothing can raise one yet (INVENTORY-01), so they refused every handover.
    invoice_timing: Mapped[str] = mapped_column(String(20), nullable=False, default="on_delivery")
    #: {channel: "prepaid" | "cash" | "credit"}.
    payment_defaults: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    offer_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    offer_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    require_pickup_code: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_tax_rate_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    auto_assignment_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Order invoicing's per-organization opt-in (INVENTORY-01): an order accepted under a row with
    #: this false raises no invoice at READY, whatever `ORDER_INVOICING_MODE` says.
    auto_invoice_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    task_sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    alert_email: Mapped[str | None] = mapped_column(String(254))
    alert_phone: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (
        CheckConstraint(
            "invoice_timing IN ('on_accept', 'on_dispatch', 'on_delivery')",
            name="invoice_timing_known",
        ),
        CheckConstraint("offer_timeout_seconds > 0", name="offer_timeout_positive"),
        CheckConstraint("offer_rounds > 0", name="offer_rounds_positive"),
        CheckConstraint(
            "default_tax_rate_pct >= 0 AND default_tax_rate_pct <= 100",
            name="default_tax_rate_pct_in_range",
        ),
        CheckConstraint("task_sla_minutes > 0", name="task_sla_minutes_positive"),
        Index(
            "uq_fulfilment_settings_org_default",
            "organization_id",
            unique=True,
            postgresql_where=text("warehouse_id IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_fulfilment_settings_org_warehouse",
            "organization_id",
            "warehouse_id",
            unique=True,
            postgresql_where=text("warehouse_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


# ------------------------------------------------------------------ fulfilment (ADR-0024 §7,
# ADR-0025 §2/§3; docs/fulfilment/plan.md F3)


class Worker(Base, TenantBase):
    """A person who can be offered pick/pack work (plan F3). `employee_id` is a plain UUID into
    `services/employee`'s `Employee.id` — never a cross-schema foreign key (ADR-0000 §10);
    `services/employee` is the source of truth for the person, this row is only the
    fulfilment-facing fact that they can hold tasks, at which warehouse, and whether they are
    currently active.

    `display_name` is denormalised (the same reasoning `PurchaseOrder.raised_by` already uses) so
    a task or offer row renders a name without a cross-service join.
    """

    __tablename__ = "workers"

    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Partial — a soft-deleted worker's employee link can be reused (e.g. re-onboarded).
        Index(
            "uq_workers_org_employee",
            "organization_id",
            "employee_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_workers_org_warehouse", "organization_id", "warehouse_id"),
        Index("ix_workers_org_active", "organization_id", "is_active"),
    )


class WorkerSkill(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One skill tag on a worker (plan F3) — free text today (`"forklift"`, `"cold_storage"`), no
    fixed vocabulary yet; a later phase's scoring engine is what would give these weight."""

    __tablename__ = "worker_skills"

    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_worker_skills_org_worker_skill",
            "organization_id",
            "worker_id",
            "skill",
            unique=True,
        ),
    )


class WorkerZoneAccess(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Which zones a worker may be offered tasks in (plan F3) — `location_id` names a `zone`-kind
    (or any) `Location`; a dispatcher offering a task whose zone the worker has no row for here is
    refused (`app.fulfilment.services.offer_task`)."""

    __tablename__ = "worker_zone_access"

    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_worker_zone_access_org_worker_location",
            "organization_id",
            "worker_id",
            "location_id",
            unique=True,
        ),
    )


class WorkerPresence(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One row per worker — online/offline plus a last-seen timestamp (plan F3). Upserted by
    `app.fulfilment.services.set_presence`, never inserted twice for the same worker."""

    __tablename__ = "worker_presence"

    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: online | offline.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="offline")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('online', 'offline')", name="status_known"),
        Index("uq_worker_presence_org_worker", "organization_id", "worker_id", unique=True),
    )


class WorkerPositionSample(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A coarse location ping (plan F3) — **schema only**: off by default, no code path writes to
    it yet (the plan explicitly marks live position tracking optional/future, beyond raw
    presence). Kept here so the later phase that turns it on is a permission and a write path, not
    a migration."""

    __tablename__ = "worker_position_samples"

    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    #: Coarse coordinates, integer micro-degrees (10^-6) — never a float (ADR-0000 §8's no-float
    #: rule applies to any stored measurement, not only money).
    lat_micro: Mapped[int | None] = mapped_column(BigInteger)
    lng_micro: Mapped[int | None] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_worker_position_samples_org_worker_recorded",
            "organization_id",
            "worker_id",
            "recorded_at",
        ),
    )


class PickTask(Base, TenantBase):
    """One picker's unit of work on an accepted order (ADR-0025 §2, plan F3) — derived from the
    order's lines, usually one per zone (ADR-0025 §1). `status` runs
    `app.domain.PICK_TASK_TRANSITIONS`'s vocabulary; unlike `Order.status` this has no dedicated
    transitions table — ADR-0025 §4 captures pick-task history on the existing `AuditLog` plus the
    outbox row each change emits, since this is an operational trail, not a customer-facing
    document.

    `round`/`max_rounds` drive escalation (`CREATED -> ESCALATED` once `round >= max_rounds`,
    default 3 from `fulfilment_settings`, snapshotted here at task creation the same way
    `Order.settings_snapshot` freezes the policy an order was accepted under — a later settings
    edit must never reinterpret a round already in flight). `offer_timeout_seconds` is snapshotted
    the same way, for the expiry `schedule_event` each offer schedules.

    `auto_assign_failure_reason` is set by `app.fulfilment.services.attempt_auto_assign` when an
    org has opted into auto-assignment but no worker was eligible for this task — a dispatcher-
    readable sentence (e.g. "No eligible worker online for zone DOCK-A"), never a silent stall:
    the task simply sits `CREATED` with no live offer, exactly where an unoffered F3 task already
    sits, for a human to offer by hand. Cleared back to `NULL` the moment any offer — auto or
    manual — is made for this task (`offer_task`).
    """

    __tablename__ = "pick_tasks"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The zone this task's lines share, when one could be resolved by walking each line's source
    #: location up to its nearest `kind == 'zone'` ancestor — `NULL` when the order's lines could
    #: not all be grouped under one (a single-bin shop, say), in which case the whole order is one
    #: task.
    zone_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    #: See class docstring — vocabulary in `app.domain.PICK_TASK_TRANSITIONS`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")
    #: How many offer rounds have already failed (declined or expired) — `PICK_TASK_TRANSITIONS`'s
    #: `CREATED -> ESCALATED` fires once this reaches `max_rounds`.
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    offer_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    #: See class docstring — set on a failed auto-assignment attempt, cleared on the next offer.
    auto_assign_failure_reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        Index("ix_pick_tasks_org_order", "organization_id", "order_id"),
        Index("ix_pick_tasks_org_status", "organization_id", "status"),
        Index("ix_pick_tasks_org_warehouse", "organization_id", "warehouse_id"),
        CheckConstraint("round >= 0", name="round_non_negative"),
        CheckConstraint("max_rounds > 0", name="max_rounds_positive"),
    )


class PickTaskLine(Base, TenantBase):
    """One order line's worth of picking inside a task (plan F3). `source_location_id` is the bin
    the order's `accept` reserved stock at (the item's `home_location_id`);
    `destination_location_id` is the staging bucket the pick relocates it to.
    `reservation_id` names the *order line's own*
    `InventoryReservation` (ADR-0024 §6) — resolved once at task creation
    (`app.fulfilment.selectors.reservations_by_order_line`) and carried here so a pick confirmation
    always transfers the right promise, never a different line's.

    `picked_milli` is what has actually moved through `post_move`; `short_milli` is what a
    `pick_exceptions` row recorded instead. A line is settled once `picked_milli + short_milli ==
    quantity_milli` — the task can reach `PICKED` only once every one of its lines is.
    """

    __tablename__ = "pick_task_lines"

    pick_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.order_lines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_reservations.id", ondelete="RESTRICT"),
    )
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    picked_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    short_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Copied from `source_location_id`'s own `pick_sequence` at task creation — orders the
    #: picker's walk (F1).
    sequence: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_pick_task_lines_org_task", "organization_id", "pick_task_id"),
        Index("ix_pick_task_lines_org_order_line", "organization_id", "order_line_id"),
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "picked_milli >= 0 AND short_milli >= 0 "
            "AND picked_milli + short_milli <= quantity_milli",
            name="picked_within",
        ),
    )


class AssignmentOffer(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One offer of one task to one worker (ADR-0025 §3, exact row shape). A task can be offered to
    several workers across rounds; this table is the history, never summarised away.

    `state`: `OFFERED, ACCEPTED, DECLINED, EXPIRED, WITHDRAWN, RELEASED`. At most one live offer
    per task — `uq_assignment_offers_org_task_live`, a partial unique index on `(organization_id,
    task_id) WHERE state IN ('OFFERED', 'ACCEPTED')`. `score_breakdown` records why this worker was
    chosen. F3's manual offer names who assigned it (`{"kind": "manual", "assigned_by": "<principal
    id>"}`); auto-assignment (ADR-0024 §9) names the ranking it believed at offer time instead
    (`{"kind": "auto", "open_task_count": <int>, "last_offered_at": <iso ts or null>,
    "required_skills": [...]}`) — kept as JSON, not re-derived later, since the engine's own
    scoring may change and this is what it believed *then* (ADR-0025 §3's own reasoning for this
    column). A manual override of a live offer records `{"kind": "manual", "assigned_by": ...,
    "overrides_offer_id": "<id>"}`.
    """

    __tablename__ = "assignment_offers"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    #: OFFERED | ACCEPTED | DECLINED | EXPIRED | WITHDRAWN | RELEASED.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="OFFERED")
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    offered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('OFFERED', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'WITHDRAWN', 'RELEASED')",
            name="state_known",
        ),
        CheckConstraint("round > 0", name="round_positive"),
        Index(
            "uq_assignment_offers_org_task_live",
            "organization_id",
            "task_id",
            unique=True,
            postgresql_where=text("state IN ('OFFERED', 'ACCEPTED')"),
        ),
        Index("ix_assignment_offers_org_task", "organization_id", "task_id"),
        Index("ix_assignment_offers_org_worker_state", "organization_id", "worker_id", "state"),
    )


class PickException(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A short pick, damage or an empty location, raised against one task line (plan F3). Leaves
    the task's other lines pickable — it flags this line and pauses the task (`IN_PROGRESS ->
    EXCEPTION`), it never silently completes it."""

    __tablename__ = "pick_exceptions"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    task_line_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_task_lines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: short_pick | damaged | not_found | other.
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    #: open | resolved.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    raised_by: Mapped[str | None] = mapped_column(String(64))
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "reason IN ('short_pick', 'damaged', 'not_found', 'other')", name="reason_known"
        ),
        CheckConstraint("status IN ('open', 'resolved')", name="status_known"),
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        Index("ix_pick_exceptions_org_task", "organization_id", "task_id"),
    )


class ScanEvent(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One barcode scan, append-only (plan F3 names this table explicitly as append-only) —
    `scan_events` joins `APPEND_ONLY_TABLES` in `migrations/env.py`. A wrong scan is never edited
    or deleted, only superseded by the next one."""

    __tablename__ = "scan_events"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.workers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.inventory_items.id", ondelete="RESTRICT")
    )
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_scan_events_org_task", "organization_id", "task_id", "scanned_at"),)


class PackagingMaterial(Base, TenantBase):
    """A box size or bag the org packs into (plan F3) — small reference data, like `Uom`."""

    __tablename__ = "packaging_materials"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    length_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    max_weight_grams: Mapped[int | None] = mapped_column(BigInteger)
    tare_weight_grams: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_packaging_materials_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Package(Base, TenantBase):
    """What went out for one order in one box (plan F3). A pack confirmation creates this row and
    its lines and posts the staging -> pack/dock relocation in the same call
    (`app.fulfilment.services.confirm_package`) — F3 has no separate open/add-lines/confirm
    sequence; one call does all three, mirroring the one-shot shape `post_move` itself already
    uses for a whole move."""

    __tablename__ = "packages"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    packaging_material_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.packaging_materials.id", ondelete="RESTRICT")
    )
    destination_location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: OPEN | PACKED. F3 always writes `PACKED` directly — see class docstring; `OPEN` is the
    #: vocabulary's reserved starting value for a later phase that splits pack into steps.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")
    weight_grams: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'PACKED')", name="status_known"),
        Index("ix_packages_org_order", "organization_id", "order_id"),
    )


class PackageLine(Base, TenantBase):
    """One task line's stock inside one package (plan F3)."""

    __tablename__ = "package_lines"

    package_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    pick_task_line_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.pick_task_lines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        Index("ix_package_lines_org_package", "organization_id", "package_id"),
        Index("ix_package_lines_org_task_line", "organization_id", "pick_task_line_id"),
    )


class Shipment(Base, TenantBase):
    """A carrier handover for one order (ADR-0024 §6; Fulfilment plan F5) — the delivery/courier
    counterpart to the counter/walk-in path `hand_over_order` covers. `app.dispatch.services
    .dispatch_order` creates this row, posts the dispatch `issue` move (ADR-0024 §6: the first
    point in the whole flow the reservation pick and pack transferred is actually consumed, not
    merely relocated), and moves the order `READY -> HANDED_OVER -> DELIVERED` in the same
    transaction — the carrier physically taking the goods is what proves handover happened on
    this path, the same role the counter's pickup-code/payment attestation plays on the other.

    `status` runs `PICKUP_HANDOVER -> IN_TRANSIT -> DELIVERED` (or `FAILED`/`RETURNED`) —
    `app.domain.SHIPMENT_TRANSITIONS` — a lifecycle of its own, deliberately apart from
    `orders.status` (ADR-0025 §1: everything but the order's own short commercial status lives on
    its own record). The order's status reaches `DELIVERED` the moment this row is created
    (commercially, the goods have left the business); this row's own status is what actually
    tracks the physical movement after that.

    `carrier_name`/`tracking_reference` are manual text fields only — no carrier API integration
    exists in this build (Fulfilment plan F5's own "not in phase" list). `tracking_reference` is
    nullable: a courier's own tracking number may not be known at the moment of handover and can
    be filled in (or corrected) on a later transition.

    One shipment per order in this build (`uq_shipments_org_order`) — ADR-0025's own "revisit if
    ... a single order routinely needs partial handover of some lines ahead of others" names a
    multi-shipment order as a later, unbuilt design, not this one.
    """

    __tablename__ = "shipments"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: See class docstring — vocabulary in `app.domain.SHIPMENT_TRANSITIONS`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PICKUP_HANDOVER")
    carrier_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tracking_reference: Mapped[str | None] = mapped_column(String(120))
    #: Set on a `-> FAILED` transition — why the delivery attempt did not land, for the next
    #: attempt or the return to read.
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_shipments_org_order",
            "organization_id",
            "order_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_shipments_org_status", "organization_id", "status"),
        Index("ix_shipments_org_warehouse", "organization_id", "warehouse_id"),
        CheckConstraint(
            "status IN ('PICKUP_HANDOVER', 'IN_TRANSIT', 'DELIVERED', 'FAILED', 'RETURNED')",
            name="status_known",
        ),
    )


class ShipmentTransition(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only row per shipment status change (Fulfilment plan F5) — `shipment_transitions`
    joins `APPEND_ONLY_TABLES` in `services/operations/migrations/env.py`, the same
    privilege-enforced append-only rule `order_transitions` already carries.

    Never edited or deleted: a retried delivery attempt or a return is a new row here, never a
    rewritten old one (ADR-0000 §9).
    """

    __tablename__ = "shipment_transitions"

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.shipments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_shipment_transitions_org_shipment", "organization_id", "shipment_id", "at"),
    )


# ------------------------------------------------------------------ warehouse geometry
# (plan §2 domain model table: `warehouse_floors, layout_versions, location_rules, walk_nodes,
# walk_edges`; `location_rules` is not built in this slice — nothing yet needs a rule beyond the
# overlap/connectivity checks `app.domain` already runs at validate/publish time, so it stays a
# later phase's addition, the same "schema-only until a phase needs it" precedent F1 set for this
# whole group.)


class WarehouseFloor(Base, TenantBase):
    """One physical floor of a warehouse (plan §2/F7) — the thing a `LayoutVersion` is a version
    *of*. `floor_number` need not start at 1 or be contiguous (basements, mezzanines); it is
    display ordering, not an array index. Deleting a floor is a soft delete like every other
    reference table here; nothing yet refuses one with locations or published layouts still
    pointing at it, since deleting a floor with real geometry is not a normal admin action —
    revisit if that turns out to be wrong in practice."""

    __tablename__ = "warehouse_floors"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    floor_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    __table_args__ = (
        Index(
            "uq_warehouse_floors_org_warehouse_number",
            "organization_id",
            "warehouse_id",
            "floor_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_warehouse_floors_org_warehouse", "organization_id", "warehouse_id"),
    )


#: draft -> published -> archived (superseded by a later publish) -> published again (rollback:
#: republishing an archived version, `app.warehouse.services.publish_layout_version`). Never
#: draft -> archived directly — a draft that is abandoned is discarded outright (`DELETE
#: /layout-versions/{id}`, only while still `draft`), not archived, so "archived" always means
#: "this was published once" and a rollback target is always something that really was live.
LAYOUT_VERSION_STATUSES = ("draft", "published", "archived")


class LayoutVersion(Base, TenantBase):
    """One version of a floor's layout (plan §2/F7; ADR-0024 §5's "floors / layout versions /
    walk graph, schema only" now gets its first write path). `version_number` is a per-floor
    sequence starting at 1, assigned once at creation and never reused.

    **Versioning discipline (this slice's whole point): a published version is never edited in
    place.** `app.warehouse.services.save_draft_geometry` refuses on anything but a `draft`
    version; editing a published layout means opening a new draft first (optionally cloned from
    the current published version, so an edit starts from what is live) and publishing *that* —
    which archives the previous published version rather than overwriting it. History survives
    every publish, so a rollback (`publish_layout_version` called again on an `archived` version)
    has something real to point back at.

    At most one `draft` and at most one `published` version per floor at a time (the two partial
    unique indexes below) — the same "at most one current row" shape `FulfilmentSettings` already
    uses for its org/warehouse split, chosen here so "the floor's current layout" is always a
    single, unambiguous lookup rather than a most-recent-published query.
    """

    __tablename__ = "layout_versions"

    warehouse_floor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouse_floors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: draft | published | archived — `LAYOUT_VERSION_STATUSES`, enforced by the write path
    #: (`app.domain.can_transition_layout_version`), the same convention every other status
    #: column in this service follows.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Directory id of whoever published (or rolled back to) this version — `layout.publish` is
    #: dangerous, so this is what an audit question ("who made this live") reads first, before
    #: reaching for `audit_logs`.
    published_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"status IN {LAYOUT_VERSION_STATUSES!r}", name="status_known"),
        Index(
            "uq_layout_versions_org_floor_number",
            "organization_id",
            "warehouse_floor_id",
            "version_number",
            unique=True,
        ),
        Index(
            "uq_layout_versions_org_floor_draft",
            "organization_id",
            "warehouse_floor_id",
            unique=True,
            # `deleted_at IS NULL`: a discarded draft (`discard_draft_version` soft-deletes it —
            # its `status` stays `"draft"` forever, only `deleted_at` changes) must not go on
            # blocking a new one from opening, the same partial-index shape `Location`'s own
            # `uq_locations_org_warehouse_default` already uses for exactly this reason.
            postgresql_where=text("status = 'draft' AND deleted_at IS NULL"),
        ),
        Index(
            "uq_layout_versions_org_floor_published",
            "organization_id",
            "warehouse_floor_id",
            unique=True,
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
        Index("ix_layout_versions_org_floor", "organization_id", "warehouse_floor_id"),
    )


class LocationPlacement(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Where one `Location` sits on one floor, **in one `LayoutVersion`** (plan §2/F7) — this is
    the "geometry columns" ADR-0024 §5 and the plan's domain-model table ask for, kept off
    `Location` itself and off this table's own would-be-second-identity shape on purpose: a plain
    x/y pair on the `locations` row cannot hold two different values for "where the draft has it"
    and "where the published layout has it" at once, and the whole point of this slice is that a
    draft edit must not leak into what is published until someone publishes it. This row carries
    only geometry and a location/version pair — no `code`, `kind`, `parent_id` or `barcode` — so
    `Location` stays the one place a location's *identity* lives (CLAUDE.md: never duplicate it).

    Integer millimetres throughout (ADR-0000 §8's no-float rule applies to any stored measurement,
    not only money — `WorkerPositionSample.lat_micro`/`lng_micro` sets the same precedent).
    `width_mm`/`depth_mm` are the footprint a 2D editor or the F9 3D viewer renders as a box;
    `z_mm`/`height_mm` are optional (most floor plans are flat) and cheap to carry now rather than
    added in a second migration later.
    """

    __tablename__ = "location_placements"

    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.layout_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    x_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    y_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    z_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    depth_mm: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("width_mm > 0 AND depth_mm > 0", name="footprint_positive"),
        CheckConstraint("height_mm IS NULL OR height_mm > 0", name="height_positive_when_present"),
        Index(
            "uq_location_placements_org_version_location",
            "organization_id",
            "layout_version_id",
            "location_id",
            unique=True,
        ),
        Index("ix_location_placements_org_location", "organization_id", "location_id"),
    )


class WalkNode(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One point in a floor's walk graph (plan §2/F7) — near a location (`location_id` set) or a
    bare junction/turn (`location_id` NULL). `code` is the caller-assigned label
    (`"N1"`, `"AISLE-A-JUNC"`) a walk-edge payload names its endpoints by, so a full-draft
    replace (`app.warehouse.services.save_draft_geometry`) never has to round-trip
    server-generated ids before it can wire up edges — unique per version, not globally, so two
    floors' drafts can both use `"N1"` without colliding.
    """

    __tablename__ = "walk_nodes"

    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.layout_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The location this node stands for, if any — routing resolves a `from_location_id`/
    #: `to_location_id` request to a node through this column, so at most one node per location
    #: per version may set it (`uq_walk_nodes_org_version_location`).
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="CASCADE")
    )
    x_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    y_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    z_mm: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_walk_nodes_org_version_code",
            "organization_id",
            "layout_version_id",
            "code",
            unique=True,
        ),
        Index(
            "uq_walk_nodes_org_version_location",
            "organization_id",
            "layout_version_id",
            "location_id",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
        ),
        Index("ix_walk_nodes_org_version", "organization_id", "layout_version_id"),
    )


class WalkEdge(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One traversable (or currently blocked) segment between two `WalkNode`s (plan §2/F7),
    weighted for `app.domain.shortest_walk_path`'s Dijkstra search. `bidirectional` (the default —
    most aisles walk both ways) makes the routing graph treat this one row as two directed edges;
    a one-way segment (a turnstile, a one-way dock lane) sets it `False` and is traversable only
    `from_node_id -> to_node_id`. `is_traversable=False` blocks the edge without deleting it —
    a temporary obstruction (a spill, a closed aisle) toggles one field rather than losing the
    edge's own distance/geometry.
    """

    __tablename__ = "walk_edges"

    layout_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.layout_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.walk_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.walk_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Millimetres — the routing weight. Never a float (ADR-0000 §8); "cost" beyond raw distance
    #: (a ramp, a slow corner) is expressed by inflating this figure, not a second column, until a
    #: real use case needs to tell the two apart.
    distance_mm: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_traversable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bidirectional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("distance_mm > 0", name="distance_positive"),
        CheckConstraint("from_node_id != to_node_id", name="no_self_loop"),
        Index(
            "uq_walk_edges_org_version_nodes",
            "organization_id",
            "layout_version_id",
            "from_node_id",
            "to_node_id",
            unique=True,
        ),
        Index("ix_walk_edges_org_version", "organization_id", "layout_version_id"),
        Index("ix_walk_edges_org_from_node", "organization_id", "from_node_id"),
        Index("ix_walk_edges_org_to_node", "organization_id", "to_node_id"),
    )


class CountSession(Base, TenantBase):
    """One scoped physical stock count (Fulfilment plan F8, ADR-0026) — a whole warehouse, one
    zone, or an explicit list of locations. `status` runs `app.domain.COUNT_SESSION_TRANSITIONS`'s
    vocabulary (`ACTIVE, REVIEW, CLOSED, CANCELLED`); like `PickTask`, there is no dedicated
    transitions table — this is an operational trail, served by `AuditLog`, not a customer-facing
    document the way `Order`/`Shipment` are.

    `uq_count_sessions_org_warehouse_live` (a partial unique index, `WHERE status IN ('ACTIVE',
    'REVIEW')`) is the whole concurrency story (ADR-0026 §5): at most one session in flight per
    warehouse, regardless of how narrow its own `scope_kind` is — two nominally disjoint zone
    counts in one warehouse can still race (locations nest; stock relocates mid-count), so
    exclusivity is enforced one level coarser than the count's own declared scope.
    """

    __tablename__ = "count_sessions"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: WAREHOUSE | ZONE | LOCATIONS.
    scope_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: Set iff scope_kind == 'ZONE'.
    zone_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    #: Set iff scope_kind == 'LOCATIONS' — a JSON array of location id strings. Not a join table:
    #: this list is read once, at session open, to resolve the snapshot; it is never queried by
    #: its contents afterwards.
    location_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    #: See class docstring — vocabulary in `app.domain.COUNT_SESSION_TRANSITIONS`.
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('WAREHOUSE', 'ZONE', 'LOCATIONS')", name="scope_kind_known"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'REVIEW', 'CLOSED', 'CANCELLED')", name="status_known"
        ),
        CheckConstraint(
            "(scope_kind <> 'ZONE') OR (zone_location_id IS NOT NULL)", name="zone_needs_location"
        ),
        CheckConstraint(
            "(scope_kind <> 'LOCATIONS') OR (location_ids IS NOT NULL)",
            name="locations_needs_list",
        ),
        Index(
            "uq_count_sessions_org_warehouse_live",
            "organization_id",
            "warehouse_id",
            unique=True,
            postgresql_where=text("status IN ('ACTIVE', 'REVIEW')"),
        ),
        Index("ix_count_sessions_org_warehouse", "organization_id", "warehouse_id"),
        Index("ix_count_sessions_org_status", "organization_id", "status"),
    )


class CountLine(Base, TenantBase):
    """One bucket's worth of a count session (Fulfilment plan F8, ADR-0026) — the exact
    `(inventory_item_id, location_id, status, lot_id, roll_id)` tuple
    `app.inventory.ledger.Bucket` already keys on.

    `expected_milli` is frozen at line creation (the session's open-time snapshot of
    `inventory_balances`) and never rewritten — the count's own `counted_milli` is compared
    against *both* this frozen figure and the live balance at review time (the live balance can
    have moved since the snapshot; `app.inventory.ledger.post_move` always computes the actual
    posted delta from the live figure under lock, never from this snapshot). `is_unlisted` marks a
    line a worker added for a bucket that had no balance row at snapshot time — stock found where
    none was expected is a real stocktake outcome, not refused.

    `review_status` starts `NONE` (nothing to review — either not yet counted, or counted and it
    matched) and only ever becomes `PENDING` when a submitted count disagrees with `expected_milli`
    at that moment. A manager's `APPROVED` is what allows the post; `REJECTED` closes the line with
    no ledger write, ever.
    """

    __tablename__ = "count_lines"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.count_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    is_unlisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expected_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expected_snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    counted_milli: Mapped[int | None] = mapped_column(BigInteger)
    #: scan | manual — how the counted quantity was entered. Null until counted.
    count_method: Mapped[str | None] = mapped_column(String(10))
    scanned_barcode: Mapped[str | None] = mapped_column(String(64))
    counted_by: Mapped[str | None] = mapped_column(String(64))
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: NONE | PENDING | APPROVED | REJECTED.
    review_status: Mapped[str] = mapped_column(String(10), nullable=False, default="NONE")
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    posted_move_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status_known"),
        CheckConstraint(
            "count_method IS NULL OR count_method IN ('scan', 'manual')", name="count_method_known"
        ),
        CheckConstraint(
            "review_status IN ('NONE', 'PENDING', 'APPROVED', 'REJECTED')",
            name="review_status_known",
        ),
        CheckConstraint("expected_milli >= 0", name="expected_non_negative"),
        CheckConstraint("counted_milli IS NULL OR counted_milli >= 0", name="counted_non_negative"),
        # One row per bucket per session. NULLS NOT DISTINCT — the same technique
        # `uq_inventory_balances_bucket` uses, since lot_id/roll_id are usually NULL.
        Index(
            "uq_count_lines_session_bucket",
            "organization_id",
            "session_id",
            "inventory_item_id",
            "location_id",
            "status",
            "lot_id",
            "roll_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_count_lines_org_session", "organization_id", "session_id"),
        Index("ix_count_lines_org_review", "organization_id", "review_status"),
    )


class Channel(Base, TenantBase):
    """One organization's connection to an external sales channel — a marketplace or storefront
    platform this business also sells on (Fulfilment plan F11; not to be confused with
    `Order.channel`, the `phone | manual | sales | walk_in | marketplace` intake-mode string that
    records *how one order was taken*. A `Channel` row is the connector itself; an order that
    arrived through one is tagged `Order.channel = "marketplace"` and linked back to the specific
    row that imported it via `ChannelOrderImport`).

    Shaped after `services/notification`'s `ProviderAccount` (`channel` column so the table holds
    the next provider without a new table; a sealed credential; connected/disconnected status) —
    the platform's own precedent for "one row per organization's connection to an external
    provider, with a sealed secret". Only `provider = "mock_marketplace"` exists today: a
    self-hosted sandbox this build proves the whole integration boundary against, in place of
    real Shopify/Amazon credentials this environment does not have (see
    `app/channels/mock_marketplace.py`). Adding a real provider is a new `ChannelClient`
    implementation behind `app/channels/base.py`'s `Protocol` — nothing here changes shape.

    `api_key_enc` is the tenant's own credential for the connected shop, sealed with
    `platform_core.pii.seal` and bound (`aad`) to this row's own id — the same envelope
    `gst_portal_credentials` (ADR-0019) and `provider_accounts.access_token_enc` (notification)
    use, and the same rule: no route ever returns it, masked or otherwise reveals only
    `api_key_masked`. Nobody sees the plaintext; it is opened only inside this service's own
    sync/sweep code, for the one HTTP call to the provider (ADR-0019 §3's "not a sensitive read"
    reasoning — a worker using its own credential to do its own job is not a human unmasking one).

    `api_base_url` is null for the mock provider (platform-configured, `Settings
    .mock_marketplace_api_base` — the same "fixed, operator-configured base" reasoning
    `whatsapp_provider.py`'s module docstring gives for skipping `EgressGuard`). A **real**
    provider that lets a tenant name their own shop domain (a Shopify `*.myshopify.com` handle,
    say) would store it here and must route every call through `platform_core.egress.EgressGuard`
    before reuse — that is a tenant-supplied URL, the exact SSRF shape ADR-0016 exists for, and is
    flagged rather than built because no such provider exists yet to prove it against.

    `consecutive_failures` / `last_error_code` / `last_error_message` are this connection's own
    health: what the admin UI's sync-status screen renders, and what
    `app.channels.sweep.ChannelSyncSweep` backs off on (ADR-0014-style discovery/act split, `app
    .cron.ReconcileSweep`'s own precedent) — a channel that is failing must slow itself down, not
    hammer a struggling or misconfigured provider.
    """

    __tablename__ = "channels"

    #: mock_marketplace today; a real provider (shopify, amazon, ...) is a new `ChannelClient`
    #: behind the same `Protocol`, not a new table (`app.domain.CHANNEL_PROVIDERS`).
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: connected | disconnected | error — `app.domain.CHANNEL_STATUSES`. `error` is set by a sync
    #: attempt that hit `ChannelAuthError` (an expired or revoked credential): the connection is
    #: still configured, but nothing will sync again until it is reconnected with a fresh key.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="disconnected")

    #: The provider's own identifier for the shop/store this row is connected to — the mock
    #: provider's per-shop sandbox id. Global to the provider, not this org, the same reason
    #: `provider_accounts.phone_number_id` is a global unique key: a shop is one tenant's, but the
    #: id itself is the provider's namespace.
    external_shop_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Null for the mock provider (platform-configured — see class docstring). Present so a real
    #: provider that takes a tenant-supplied domain has somewhere to store it.
    api_base_url: Mapped[str | None] = mapped_column(String(300))

    #: The tenant's credential for the connected shop, sealed. Never returned by any API — see
    #: class docstring.
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_key_masked: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    #: Where an imported order lands and what it is priced in.
    default_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.warehouses.id", ondelete="RESTRICT")
    )
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Whether an imported order is also accepted (reserving its stock) as part of import, or only
    #: placed and left for a person to accept. On by default: a real marketplace order already
    #: represents a paying, committed customer — the same reasoning a `prepaid` channel's
    #: `fulfilment_settings.payment_defaults` already encodes — so holding every imported order for
    #: manual acceptance would make the integration announce stock it will not actually commit.
    auto_accept_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: The order-import poll's own bookmark (`ChannelClient.list_new_orders`'s cursor) — provider
    #: state, not a business fact, so it lives on the connection row rather than a table of its
    #: own.
    last_order_cursor: Mapped[str | None] = mapped_column(String(120))
    last_orders_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inventory_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Consecutive failed sync attempts of any kind (order import, inventory push, fulfilment
    #: push, health check) — reset to 0 the moment one succeeds. What the sweep backs off on and
    #: the admin UI's own health indicator reads.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_error_message: Mapped[str | None] = mapped_column(String(500))

    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_channels_org_provider_shop",
            "organization_id",
            "provider",
            "external_shop_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_channels_org_status", "organization_id", "status"),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_non_negative"),
    )


class ChannelSkuMapping(Base, TenantBase):
    """One external SKU's link to this org's own item master (Fulfilment plan F11) — what lets an
    imported order line and an outbound inventory push resolve to the same `InventoryItem`. No
    mapping, no import: `app.channels.services.import_channel_order` refuses (never guesses) an
    order line naming a SKU with no row here, and no mapping means no inventory push for that item
    either (`app.channels.sweep`).

    `last_pushed_available_milli` / `last_pushed_at` / `last_push_status` / `last_push_error` are
    this one SKU's own push history — what the admin UI's sync-log screen reads before ever
    joining `channel_sync_logs`, and what the sweep diffs the item's current `available_milli`
    against so a quiet SKU is never re-pushed for no reason.
    """

    __tablename__ = "channel_sku_mappings"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_sku: Mapped[str] = mapped_column(String(120), nullable=False)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )

    last_pushed_available_milli: Mapped[int | None] = mapped_column(BigInteger)
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ok | failed. Null until the first push attempt.
    last_push_status: Mapped[str | None] = mapped_column(String(20))
    last_push_error: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        Index(
            "uq_channel_sku_mappings_org_channel_sku",
            "organization_id",
            "channel_id",
            "external_sku",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_channel_sku_mappings_org_item", "organization_id", "inventory_item_id"),
    )


class ChannelOrderImport(Base, TenantBase):
    """The idempotency claim and audit trail for one external order (Fulfilment plan F11) — the
    `ProcessedEvent`-style ledger this platform uses everywhere an external trigger must not be
    allowed to create a duplicate, keyed here on the provider's own order id rather than an
    envelope's `event_id` because a poll (unlike a queue delivery) has no envelope at all.

    `order_id` is null until the real F2 order exists (`app.orders.services.create_order`) —
    `app.channels.services.import_channel_order` claims this row **first**, in the same
    transaction that creates the order, via `INSERT ... ON CONFLICT (organization_id, channel_id,
    external_order_id) DO NOTHING`: a concurrent second import of the same external order id loses
    the race on this unique index and finds the winner's row instead of inserting its own, never
    creating a second `Order`. `rejected_reason` is set instead of `order_id` when the order could
    not be created at all (an order line naming a SKU with no `ChannelSkuMapping`) — a permanent
    outcome distinct from "not attempted yet" (`order_id IS NULL AND rejected_reason IS NULL`,
    which is what an import interrupted between the claim and `create_order` leaves behind, and
    what a resumed import or a retry resolves by re-attempting from wherever it actually got to,
    reading `Order.status` itself rather than a second status column here — the same "never a
    second status" rule `Order`'s own docstring already draws for `invoice_id`/`paid_at`).

    `raw_payload` is the external order exactly as the provider sent it (small — order-sized, not
    a catalogue), kept for support and for a person to see why a rejected import was rejected.
    """

    __tablename__ = "channel_order_imports"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_order_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.orders.id", ondelete="RESTRICT")
    )
    rejected_reason: Mapped[str | None] = mapped_column(String(500))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index(
            "uq_channel_order_imports_org_channel_external",
            "organization_id",
            "channel_id",
            "external_order_id",
            unique=True,
        ),
        Index(
            "uq_channel_order_imports_org_order",
            "organization_id",
            "order_id",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )


class ChannelSyncLog(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only row per sync attempt (Fulfilment plan F11) — `channel_sync_logs` joins
    `APPEND_ONLY_TABLES` in `services/operations/migrations/env.py`, the same privilege-enforced
    append-only rule `order_transitions` and `scan_events` already carry: what actually happened
    is a fact, and a retry is a new attempt, never a rewrite of the one it is retrying. This is
    what the admin UI's sync-status screen renders and what a "retry" action re-reads to know what
    to re-attempt (`sync_type` + `external_ref`).
    """

    __tablename__ = "channel_sync_logs"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: order_import | inventory_push | fulfilment_push | health_check —
    #: `app.domain.CHANNEL_SYNC_TYPES`.
    sync_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: success | failed.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The external order id, SKU, or shipment id this attempt concerned — what a retry re-reads;
    #: blank for a health check, which concerns the channel as a whole.
    external_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_channel_sync_logs_org_channel_at", "organization_id", "channel_id", "attempted_at"
        ),
        Index("ix_channel_sync_logs_org_channel_status", "organization_id", "channel_id", "status"),
    )


# ------------------------------------------------------------------ receiving (inventory Phase 2)

#: The requisition state machine's vocabulary; `app.receiving.domain.REQUISITION_TRANSITIONS` is
#: the map. DRAFT -> SUBMITTED -> APPROVED -> CONVERTED is the happy path.
REQUISITION_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CONVERTED", "CANCELLED")
_REQ_STATUS_SQL = ", ".join(f"'{s}'" for s in REQUISITION_STATUSES)
#: How one goods-receipt line stood against its purchase-order line when it was posted.
RECEIPT_OUTCOMES = ("PARTIAL", "EXACT", "SHORT", "OVER")
_RECEIPT_OUTCOME_SQL = ", ".join(f"'{s}'" for s in RECEIPT_OUTCOMES)


class PurchaseRequisition(Base, TenantBase):
    """An internal request to buy (inventory Phase 2): lines of items and quantities, approved by
    someone with `requisition.approve`, then converted into a draft purchase order.
    `requested_by_id` is the directory id used for OWN scope (never a foreign key).
    `converted_po_id` is a real in-schema foreign key — set once, on conversion."""

    __tablename__ = "purchase_requisitions"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.warehouses.id", ondelete="RESTRICT")
    )
    needed_by: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    #: The approver's / rejecter's / canceller's note.
    decision_note: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    converted_po_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.purchase_orders.id", ondelete="RESTRICT")
    )
    #: The MRP requisition request this was made from — set only by the MRP consumer, never from
    #: user input, and unique per organization so a redelivery cannot make a second one.
    mrp_request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        Index(
            "uq_purchase_requisitions_org_mrp_request",
            "organization_id",
            "mrp_request_id",
            unique=True,
            postgresql_where=text("mrp_request_id IS NOT NULL"),
        ),
        CheckConstraint(f"status IN ({_REQ_STATUS_SQL})", name="status_known"),
        CheckConstraint(
            "(status = 'CONVERTED') = (converted_po_id IS NOT NULL)", name="converted_matches_po"
        ),
        UniqueConstraint("organization_id", "number", name="uq_purchase_requisitions_org_number"),
        Index("ix_purchase_requisitions_org_status", "organization_id", "status"),
        Index("ix_purchase_requisitions_org_requester", "organization_id", "requested_by_id"),
        Index(
            "ix_purchase_requisitions_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )


class PurchaseRequisitionLine(Base, TenantBase):
    """One requested item. `quantity_milli` is in the item's own stock UOM."""

    __tablename__ = "purchase_requisition_lines"

    requisition_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.purchase_requisitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    suggested_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.suppliers.id", ondelete="RESTRICT")
    )
    #: Per stock unit, integer minor units — an estimate the buyer may overwrite on conversion.
    est_unit_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "est_unit_price_minor IS NULL OR est_unit_price_minor >= 0", name="price_non_negative"
        ),
        Index(
            "ix_purchase_requisition_lines_org_req", "organization_id", "requisition_id", "line_no"
        ),
    )


class GoodsReceipt(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One posted delivery against a purchase order (inventory Phase 2): immutable once written
    (`UPDATE`/`DELETE` revoked) — a mistake is corrected by a later document, never an edit. The
    stock it put on the books is the `inventory_moves` row with `reference_type='goods_receipt'`
    and `reference_id=<this id>`. `idempotency_key` + `request_hash` make a retried POST a replay
    (`uq_goods_receipts_org_key`)."""

    __tablename__ = "goods_receipts"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.purchase_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: The supplier's delivery challan / invoice number.
    delivery_note: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="POSTED")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("status IN ('POSTED')", name="status_known"),
        CheckConstraint(
            "(idempotency_key IS NULL) = (request_hash IS NULL)", name="key_matches_hash"
        ),
        UniqueConstraint("organization_id", "number", name="uq_goods_receipts_org_number"),
        Index(
            "uq_goods_receipts_org_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_goods_receipts_org_po", "organization_id", "purchase_order_id"),
        Index("ix_goods_receipts_org_supplier", "organization_id", "supplier_id"),
        Index(
            "ix_goods_receipts_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )


class GoodsReceiptLine(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One line of a receipt: what was entered (`entered_milli` of `entered_uom`), what it was in
    the item's stock UOM (`received_milli`), and the figures the tolerance decision was made on —
    ordered, previously received, cumulative after, the item's tolerance then — snapshotted, so
    the audit of an over- or short-receipt never depends on what the item or PO says later.
    `inventory_item_id`/`location_id` are NULL for a PO line whose SKU is no stocked item (a
    service line): accounted, but no stock posted. `roll_ids` lists the rolls this line created."""

    __tablename__ = "goods_receipt_lines"

    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.goods_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    purchase_order_line_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.purchase_order_lines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.inventory_items.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    stock_status: Mapped[str | None] = mapped_column(String(20))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    supplier_lot_no: Mapped[str | None] = mapped_column(String(60))
    roll_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    entered_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entered_uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    received_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordered_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prior_received_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cumulative_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tolerance_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    close_short: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    unit_cost_currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"outcome IN ({_RECEIPT_OUTCOME_SQL})", name="outcome_known"),
        CheckConstraint("received_milli >= 0", name="received_non_negative"),
        CheckConstraint("entered_milli >= 0", name="entered_non_negative"),
        CheckConstraint("tolerance_bp BETWEEN 0 AND 10000", name="tolerance_range"),
        ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_goods_receipt_lines_org_lot",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_goods_receipt_lines_org_receipt", "organization_id", "goods_receipt_id", "line_no"
        ),
        Index("ix_goods_receipt_lines_org_po_line", "organization_id", "purchase_order_line_id"),
    )


# ------------------------------------------------------------------ movements and control (Phase 3)

TRANSFER_STATUSES = (
    "DRAFT",
    "REQUESTED",
    "APPROVED",
    "IN_TRANSIT",
    "RECEIVED",
    "CANCELLED",
    "REJECTED",
)
_TRANSFER_STATUS_SQL = ", ".join(f"'{s}'" for s in TRANSFER_STATUSES)
ADJUSTMENT_STATUSES = ("PENDING_APPROVAL", "APPLIED", "REJECTED", "CANCELLED", "FAILED")
_ADJUSTMENT_STATUS_SQL = ", ".join(f"'{s}'" for s in ADJUSTMENT_STATUSES)


class InventoryControlSettings(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One row per organization: the thresholds above which a transfer or an adjustment needs the
    approval service (ADR-0006), and the document-number counters.

    A threshold is milli-units of **each line's own item UOM** (an item is never summed with a
    different unit's). `NULL` means "no threshold": a transfer then needs a direct sign-off by a
    holder of `inventory.transfer.approve`, and an adjustment applies at once. The counters are
    advanced with `INSERT .. ON CONFLICT DO UPDATE .. RETURNING`, so two creates never share a
    number and the row is a short-lived lock, not a hot spot.
    """

    __tablename__ = "inventory_control_settings"

    transfer_approval_threshold_milli: Mapped[int | None] = mapped_column(BigInteger)
    adjustment_approval_threshold_milli: Mapped[int | None] = mapped_column(BigInteger)
    next_transfer_no: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    next_adjustment_no: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "transfer_approval_threshold_milli IS NULL OR transfer_approval_threshold_milli > 0",
            name="transfer_threshold_positive",
        ),
        CheckConstraint(
            "adjustment_approval_threshold_milli IS NULL "
            "OR adjustment_approval_threshold_milli > 0",
            name="adjustment_threshold_positive",
        ),
        CheckConstraint(
            "next_transfer_no >= 1 AND next_adjustment_no >= 1", name="counters_positive"
        ),
        Index("uq_inventory_control_settings_org", "organization_id", unique=True),
    )


class StockTransfer(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """An inter-warehouse transfer document (ADR-0014 §6, Phase 3). Moves stock out of the source
    warehouse into the `IN_TRANSIT` bucket at dispatch and into the destination at receipt — it
    never writes a quantity itself; every movement is `ledger.post_move`.

    `approval_route` is decided when the transfer is requested: `AUTO` (a threshold is set and
    every line is under it — approved on the spot), `DIRECT` (no threshold — a holder of
    `inventory.transfer.approve` other than the requester signs off), or `SERVICE` (at or over the
    threshold — routed through the approval service over events, ADR-0006; the answer comes back
    as `core.approval.request.decided`).
    """

    __tablename__ = "stock_transfers"

    number: Mapped[str] = mapped_column(String(30), nullable=False)
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    expected_on: Mapped[date | None] = mapped_column(Date)
    approval_route: Mapped[str | None] = mapped_column(String(8))
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approver_name: Mapped[str | None] = mapped_column(String(200))
    requested_by: Mapped[str | None] = mapped_column(String(64))
    requested_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    dispatched_by: Mapped[str | None] = mapped_column(String(64))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[str | None] = mapped_column(String(64))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_TRANSFER_STATUS_SQL})", name="status_known"),
        CheckConstraint("from_warehouse_id <> to_warehouse_id", name="warehouses_differ"),
        CheckConstraint(
            "approval_route IS NULL OR approval_route IN ('AUTO', 'DIRECT', 'SERVICE')",
            name="approval_route_known",
        ),
        Index("uq_stock_transfers_org_number", "organization_id", "number", unique=True),
        Index(
            "uq_stock_transfers_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_stock_transfers_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("ix_stock_transfers_org_status", "organization_id", "status"),
        Index(
            "ix_stock_transfers_org_approval_request",
            "organization_id",
            "approval_request_id",
            postgresql_where=text("approval_request_id IS NOT NULL"),
        ),
    )


class StockTransferLine(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One bucket of a transfer: `quantity_milli` is what was asked for; `dispatched_milli` what
    left the source (all of it, at dispatch); `received_milli` + `shortage_milli` what the
    destination has accounted for. `received + shortage <= dispatched <= quantity` is a CHECK."""

    __tablename__ = "stock_transfer_lines"

    transfer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.stock_transfers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Where the in-transit stock is held: the destination warehouse's default bin, fixed at
    #: dispatch so a receipt takes it from exactly where it was put.
    in_transit_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    to_location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.locations.id", ondelete="RESTRICT")
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dispatched_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    received_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shortage_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: A shortage at or over the adjustment threshold, held while its adjustment request awaits a
    #: decision: still in transit, but spoken for — it can be neither received nor reported short
    #: again. Approval moves it into `shortage_milli`; rejection or cancellation releases it.
    pending_shortage_milli: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "dispatched_milli >= 0 AND received_milli >= 0 AND shortage_milli >= 0 "
            "AND pending_shortage_milli >= 0 "
            "AND dispatched_milli <= quantity_milli "
            "AND received_milli + shortage_milli + pending_shortage_milli <= dispatched_milli",
            name="quantities_within",
        ),
        ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_stock_transfer_lines_org_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_stock_transfer_lines_org_roll",
            ondelete="RESTRICT",
        ),
        Index("uq_stock_transfer_lines_transfer_no", "transfer_id", "line_no", unique=True),
        Index("ix_stock_transfer_lines_org_transfer", "organization_id", "transfer_id"),
        Index("ix_stock_transfer_lines_org_item", "organization_id", "inventory_item_id"),
    )


class StockTransferTransition(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Append-only trail of a transfer's state changes (the `order_transitions` shape):
    `UPDATE`/`DELETE` revoked from the app role."""

    __tablename__ = "stock_transfer_transitions"

    transfer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.stock_transfers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"to_status IN ({_TRANSFER_STATUS_SQL})", name="to_status_known"),
        Index(
            "ix_stock_transfer_transitions_org_transfer",
            "organization_id",
            "transfer_id",
            "created_at",
        ),
    )


class AdjustmentRequest(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A stock adjustment with its reason and evidence — **every** adjustment made through
    `/operations/inventory/adjustments` is one of these, applied at once when under the
    organization's threshold and held `PENDING_APPROVAL` (routed through the approval service,
    ADR-0006) when at or over it. Applying posts one `adjustment` move through `ledger.post_move`
    under a deterministic idempotency key, so a redelivered decision cannot post twice.

    `evidence_document_id` is a plain UUID into the document service (ADR-0009) — no foreign key
    crosses a schema boundary.
    """

    __tablename__ = "adjustment_requests"

    number: Mapped[str] = mapped_column(String(30), nullable=False)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.locations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stock_status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    quantity_delta_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The transfer line whose shortage this request writes off, when it came from a receipt.
    transfer_line_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_APPROVAL")
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approver_name: Mapped[str | None] = mapped_column(String(200))
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    applied_move_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.inventory_moves.id", ondelete="RESTRICT")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    cancelled_by: Mapped[str | None] = mapped_column(String(64))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_ADJUSTMENT_STATUS_SQL})", name="status_known"),
        CheckConstraint(f"stock_status IN ({_STATUS_SQL})", name="stock_status_known"),
        CheckConstraint("quantity_delta_milli <> 0", name="delta_non_zero"),
        CheckConstraint(
            "reason IN ('damaged', 'lost', 'found', 'consumed', 'returned', 'other')",
            name="reason_known",
        ),
        CheckConstraint(
            "NOT approval_required OR evidence_document_id IS NOT NULL",
            name="approval_needs_evidence",
        ),
        ForeignKeyConstraint(
            ["organization_id", "lot_id"],
            ["operations.lots.organization_id", "operations.lots.id"],
            name="fk_adjustment_requests_org_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "roll_id"],
            ["operations.rolls.organization_id", "operations.rolls.id"],
            name="fk_adjustment_requests_org_roll",
            ondelete="RESTRICT",
        ),
        Index("uq_adjustment_requests_org_number", "organization_id", "number", unique=True),
        Index(
            "uq_adjustment_requests_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_adjustment_requests_org_created",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("ix_adjustment_requests_org_status", "organization_id", "status"),
        Index(
            "ix_adjustment_requests_org_approval_request",
            "organization_id",
            "approval_request_id",
            postgresql_where=text("approval_request_id IS NOT NULL"),
        ),
    )


# ------------------------------------------------------------------ quality (plan Phase 4)
#
# `item_id`, `location_id`, `lot_id`, `roll_id`, `template_id` and `supplier_id` on these tables are
# **plain indexed UUIDs, never foreign keys** (docs/inventory/quality.md §2): an inspection is a
# record about stock at a moment (`create_inspection` validates the lot and roll against Phase 1's
# tables when it is opened, but the row keeps no FK to them) — it must
# survive an item being renamed, a location being retired or a template being replaced, and must
# not be able to block any of those. Every read re-checks organization on the referenced row.

QC_KINDS = ("incoming", "in-process", "finished")
QC_PARAM_KINDS = ("numeric", "pass_fail")
INSPECTION_STATUSES = ("PENDING", "PASSED", "FAILED", "HOLD", "REWORK", "PARTIAL_PASS")
_QC_KIND_SQL = ", ".join(f"'{k}'" for k in QC_KINDS)
_INSPECTION_STATUS_SQL = ", ".join(f"'{s}'" for s in INSPECTION_STATUSES)


class QcTemplate(Base, TenantBase):
    """A reusable checklist of measurable parameters for one kind of inspection."""

    __tablename__ = "qc_templates"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: incoming | in-process | finished.
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: Optional narrowing to one item; plain UUID, no FK (see section comment).
    item_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    create_key: Mapped[str | None] = mapped_column(String(128))
    create_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"kind IN ({_QC_KIND_SQL})", name="kind_known"),
        Index(
            "uq_qc_templates_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_qc_templates_org_create_key",
            "organization_id",
            "create_key",
            unique=True,
            postgresql_where=text("create_key IS NOT NULL"),
        ),
        Index("ix_qc_templates_org_kind", "organization_id", "kind"),
    )


class QcTemplateParam(Base, TenantBase):
    """One measurable check. Bounds are milli-scaled integers (three implied decimals) — the same
    exact-decimal representation quantities use; a float never touches one."""

    __tablename__ = "qc_template_params"

    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.qc_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: numeric | pass_fail.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="numeric")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    min_milli: Mapped[int | None] = mapped_column(BigInteger)
    max_milli: Mapped[int | None] = mapped_column(BigInteger)
    #: A failed critical parameter makes release impossible (domain.can_release).
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("kind IN ('numeric', 'pass_fail')", name="kind_known"),
        CheckConstraint(
            "min_milli IS NULL OR max_milli IS NULL OR min_milli <= max_milli",
            name="bounds_ordered",
        ),
        CheckConstraint(
            "kind = 'numeric' OR (min_milli IS NULL AND max_milli IS NULL)",
            name="pass_fail_has_no_bounds",
        ),
        Index(
            "uq_qc_template_params_template_name",
            "organization_id",
            "template_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_qc_template_params_org_template", "organization_id", "template_id"),
    )


class Inspection(Base, TenantBase):
    """One quality inspection of a quantity of stock (plan Phase 4).

    `status` runs `app.quality.domain.INSPECTION_TRANSITIONS`. The inspected quantity sits in the
    `QUALITY_HOLD` bucket from the moment the inspection is opened (or was received there), and
    leaves it only through the kernel — released to `AVAILABLE`, rejected to `REJECTED` /
    `QUARANTINE`, and finally disposed of (return to vendor / scrap) out of the rejected bucket.
    """

    __tablename__ = "inspections"

    #: incoming | in-process | finished.
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    template_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_milli: Mapped[int | None] = mapped_column(BigInteger)
    rejected_milli: Mapped[int | None] = mapped_column(BigInteger)
    #: Where rejected stock was parked: REJECTED | QUARANTINE.
    rejected_bucket: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    hold_move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    decision_move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    #: NONE | RETURN_TO_VENDOR | SCRAP — the follow-on for rejected stock.
    disposition: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")
    disposition_move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    disposition_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    disposition_reference: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    disposed_by: Mapped[str | None] = mapped_column(String(64))
    disposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_key: Mapped[str | None] = mapped_column(String(128))
    create_hash: Mapped[str | None] = mapped_column(String(64))
    decision_key: Mapped[str | None] = mapped_column(String(128))
    decision_hash: Mapped[str | None] = mapped_column(String(64))
    disposition_key: Mapped[str | None] = mapped_column(String(128))
    disposition_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(f"kind IN ({_QC_KIND_SQL})", name="kind_known"),
        CheckConstraint(f"status IN ({_INSPECTION_STATUS_SQL})", name="status_known"),
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "accepted_milli IS NULL OR (accepted_milli >= 0 AND accepted_milli <= quantity_milli)",
            name="accepted_within_quantity",
        ),
        CheckConstraint(
            "rejected_milli IS NULL OR (rejected_milli >= 0 AND rejected_milli <= quantity_milli)",
            name="rejected_within_quantity",
        ),
        CheckConstraint(
            "rejected_bucket IS NULL OR rejected_bucket IN ('REJECTED', 'QUARANTINE')",
            name="rejected_bucket_known",
        ),
        CheckConstraint(
            "disposition IN ('NONE', 'RETURN_TO_VENDOR', 'SCRAP')", name="disposition_known"
        ),
        Index("ix_inspections_org_status", "organization_id", "status"),
        Index("ix_inspections_org_item", "organization_id", "item_id"),
        Index("ix_inspections_org_lot", "organization_id", "lot_id"),
        Index("ix_inspections_org_roll", "organization_id", "roll_id"),
        Index("ix_inspections_org_location", "organization_id", "location_id"),
        Index(
            "uq_inspections_org_create_key",
            "organization_id",
            "create_key",
            unique=True,
            postgresql_where=text("create_key IS NOT NULL"),
        ),
    )


class InspectionResult(Base, TenantBase):
    """One measured parameter on an inspection. The parameter's name, bounds and criticality are
    **snapshotted** at record time, so editing or deleting the template later never changes what
    an inspection was judged against. `passed` is computed on the server, never trusted."""

    __tablename__ = "inspection_results"

    inspection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inspections.id", ondelete="CASCADE"),
        nullable=False,
    )
    param_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    param_name: Mapped[str] = mapped_column(String(80), nullable=False)
    param_kind: Mapped[str] = mapped_column(String(10), nullable=False, default="numeric")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    min_milli: Mapped[int | None] = mapped_column(BigInteger)
    max_milli: Mapped[int | None] = mapped_column(BigInteger)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The measured value for a numeric parameter; NULL for pass_fail.
    value_milli: Mapped[int | None] = mapped_column(BigInteger)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    recorded_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("param_kind IN ('numeric', 'pass_fail')", name="param_kind_known"),
        Index(
            "uq_inspection_results_inspection_param",
            "organization_id",
            "inspection_id",
            "param_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_inspection_results_org_inspection", "organization_id", "inspection_id"),
    )


# ------------------------------------------------------------------ job work (Phase 6)

JOB_WORK_STATUSES = (
    "DRAFT",
    "APPROVED",
    "ISSUED",
    "PARTIALLY_RECEIVED",
    "RECEIVED",
    "CLOSED",
    "CANCELLED",
)
_JOB_WORK_STATUS_SQL = ", ".join(f"'{s}'" for s in JOB_WORK_STATUSES)
#: Where an order is while stock sits at the vendor — the states the overdue sweep watches.
JOB_WORK_OPEN_AT_VENDOR = ("ISSUED", "PARTIALLY_RECEIVED")
EWAY_STATUSES = ("NOT_REQUIRED", "REQUESTED", "GENERATED", "FAILED")
_EWAY_STATUS_SQL = ", ".join(f"'{s}'" for s in EWAY_STATUSES)


class JobWorkOrder(Base, TenantBase):
    """Material sent to an outside processor (a dyer, a stitcher, a finisher) and expected back
    (Inventory plan Phase 6). The document half of the story: the **stock** half lives in the
    ledger, moved only through `app.inventory.ledger.post_move` — material at the vendor sits in
    the `AT_JOB_WORKER` bucket, which is reported separately from on-hand (ADR-0014 §3).

    `supplier_id` is a plain indexed UUID, never a foreign key — the vendor is a `suppliers` row
    in this schema today, but nothing here may assume that stays true (CLAUDE.md: no FK that
    would stop a later split). The same goes for `source_warehouse_id` and `receive_location_id`.

    `status` runs `app.jobwork.domain.JOB_WORK_TRANSITIONS`. Being **overdue** is not a status:
    it is derived (`due_date` passed while the order is still at the vendor), because a status
    nobody can move out of except by receiving would be a second source of truth.

    `transition_keys` maps an `Idempotency-Key` to the status it produced, so a retried approve /
    cancel / close replays instead of answering 409 for a request that already succeeded. It is
    read and written only under the order's own row lock.
    """

    __tablename__ = "job_work_orders"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: What the vendor does — "Dyeing", "Stitching". Free text; a process master is a later phase.
    process: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    due_date: Mapped[date | None] = mapped_column(Date)
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Where good and rejected goods land on return, unless a receipt line says otherwise.
    receive_location_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Returned goods go to `QUALITY_HOLD` and a QC hand-off event is emitted (the quality module
    #: is built elsewhere and consumes the event; nothing here depends on it).
    qc_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set by the overdue sweep the first time it announces this order — one notification per
    #: order, not one per tick.
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    transition_keys: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_JOB_WORK_STATUS_SQL})", name="status_known"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("uq_job_work_orders_org_number", "organization_id", "number", unique=True),
        Index(
            "uq_job_work_orders_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_job_work_orders_org_status", "organization_id", "status"),
        Index("ix_job_work_orders_org_supplier", "organization_id", "supplier_id"),
        Index(
            "ix_job_work_orders_org_due_open",
            "organization_id",
            "due_date",
            postgresql_where=text("status IN ('ISSUED', 'PARTIALLY_RECEIVED')"),
        ),
    )


class JobWorkMaterial(Base, TenantBase):
    """One bucket's worth of material on a job-work order.

    `quantity_milli` is what was planned (and, once issued, sent: `sent_milli` is set to it at
    issue). `received_milli` / `rejected_milli` / `shortage_milli` only ever grow, each by a
    receipt, and `received + rejected + shortage <= sent` is a CHECK — so the reconciliation
    identity `sent = received + rejected + shortage + outstanding` cannot be violated by a bug in
    any one write path. `item_id`, `lot_id`, `roll_id`, `location_id` are plain UUIDs (lots and
    rolls arrive from another branch).
    """

    __tablename__ = "job_work_materials"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.job_work_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    roll_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The bucket the material is issued **from**, and the `AT_JOB_WORKER` bucket's location.
    location_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Snapshots, so the challan and the screens never depend on a later edit of the item.
    sku: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    unit_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sent_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    received_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rejected_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shortage_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "sent_milli >= 0 AND received_milli >= 0 AND rejected_milli >= 0 "
            "AND shortage_milli >= 0",
            name="figures_non_negative",
        ),
        CheckConstraint(
            "received_milli + rejected_milli + shortage_milli <= sent_milli",
            name="accounted_within_sent",
        ),
        CheckConstraint("sent_milli = 0 OR sent_milli = quantity_milli", name="sent_is_planned"),
        Index(
            "uq_job_work_materials_bucket",
            "organization_id",
            "order_id",
            "item_id",
            "location_id",
            "lot_id",
            "roll_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("uq_job_work_materials_line", "organization_id", "order_id", "line_no", unique=True),
        Index("ix_job_work_materials_org_item", "organization_id", "item_id"),
    )


class JobWorkReceipt(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One material's share of one receipt from the vendor — append-only (`job_work_receipts`
    joins `APPEND_ONLY_TABLES`): a wrong receipt is never edited, the figures it moved are on the
    ledger. A receipt document (`receipt_no`) is one row per material it covers.

    `received_milli` came back good (into `good_status`), `rejected_milli` came back unusable
    (into `REJECTED`), `shortage_milli` is declared lost at the vendor (written off the
    `AT_JOB_WORKER` bucket through an adjustment). At least one is positive.
    """

    __tablename__ = "job_work_receipts"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.job_work_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.job_work_materials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_no: Mapped[str] = mapped_column(String(40), nullable=False)
    received_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rejected_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shortage_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    location_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: AVAILABLE, or QUALITY_HOLD when the order asked for a QC hand-off.
    good_status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    qc_handoff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    return_move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    shortage_move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "received_milli >= 0 AND rejected_milli >= 0 AND shortage_milli >= 0",
            name="figures_non_negative",
        ),
        CheckConstraint(
            "received_milli + rejected_milli + shortage_milli > 0", name="moves_something"
        ),
        Index("ix_job_work_receipts_org_order", "organization_id", "order_id", "created_at"),
        Index("ix_job_work_receipts_org_material", "organization_id", "material_id"),
        Index(
            "uq_job_work_receipts_org_key_material",
            "organization_id",
            "idempotency_key",
            "material_id",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class Challan(Base, TenantBase):
    """The delivery challan that goes with material to a job worker (CGST Rule 55).

    **Immutable once issued** — a database trigger refuses any change to the number, the order,
    the supplier, the goods snapshot or the value (`migrations/versions/d7b3e5a19f62_job_work.py`);
    only the e-way-bill facts (`eway_*`, and the transport fields that feed it) move afterwards,
    because they are filled in by a later step and, eventually, by finance. Numbers come from a
    gapless per-organization, per-financial-year series (`app.jobwork.numbering`).

    The e-way bill itself is **not** built here: `eway_status` records only that this module asked
    finance to consider one (`operations.jobwork.challan.eway_requested`, fire-and-forget, the
    same hook `operations.dispatch.shipment.eway_requested` set) and what finance later answers.
    """

    __tablename__ = "challans"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.job_work_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    challan_number: Mapped[str] = mapped_column(String(40), nullable=False)
    #: DELIVERY (to the vendor) — the only kind issued today; the column leaves room for a
    #: return challan without a migration of meaning.
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="DELIVERY")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    process: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: The goods, frozen at issue: `[{sku, uom, quantity, unit_value_minor, value_minor, ...}]`.
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    total_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Transport facts — the caller's, not derivable; feed finance's e-way machinery.
    vehicle_no: Mapped[str | None] = mapped_column(String(20))
    transporter_id: Mapped[str | None] = mapped_column(String(30))
    distance_km: Mapped[int | None] = mapped_column(Integer)
    eway_status: Mapped[str] = mapped_column(String(14), nullable=False, default="NOT_REQUIRED")
    eway_bill_no: Mapped[str | None] = mapped_column(String(20))
    eway_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    move_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("kind IN ('DELIVERY')", name="kind_known"),
        CheckConstraint(f"eway_status IN ({_EWAY_STATUS_SQL})", name="eway_status_known"),
        CheckConstraint("total_value_minor >= 0", name="value_non_negative"),
        CheckConstraint("distance_km IS NULL OR distance_km >= 0", name="distance_non_negative"),
        Index("uq_challans_org_number", "organization_id", "challan_number", unique=True),
        Index(
            "uq_challans_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_challans_org_order", "organization_id", "order_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
