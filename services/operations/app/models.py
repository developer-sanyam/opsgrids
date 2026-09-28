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
    Index,
    Integer,
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

    __table_args__ = (
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

    __table_args__ = (
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
    #: Lot / roll bucket keys. Plain UUIDs until `lots`/`rolls` exist (Phase 1 adds the FKs).
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

    `quantity_milli - consumed_milli - released_milli` is the open remainder while
    `status == "active"`; the sum of open remainders per balance is that balance's
    `reserved_milli`. `released_milli` is what a caller has explicitly given back
    (ADR-0014 §11) — kept separate from `consumed_milli` (what an outbound move drew against
    this reservation) so `quantity_milli` stays the original promise forever and never has to
    cross zero, which the `quantity_positive` CHECK would otherwise forbid on a full release of
    an untouched reservation.
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
    #: active | released | consumed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        CheckConstraint(
            "consumed_milli >= 0 AND released_milli >= 0 "
            "AND consumed_milli + released_milli <= quantity_milli",
            name="consumed_within",
        ),
        CheckConstraint("status IN ('active', 'released', 'consumed')", name="status_known"),
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
    """

    __tablename__ = "fulfilment_settings"

    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.warehouses.id", ondelete="RESTRICT")
    )
    #: on_accept | on_dispatch | on_delivery.
    invoice_timing: Mapped[str] = mapped_column(String(20), nullable=False, default="on_accept")
    #: {channel: "prepaid" | "cash" | "credit"}.
    payment_defaults: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    offer_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    offer_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    __table_args__ = (
        CheckConstraint(
            "invoice_timing IN ('on_accept', 'on_dispatch', 'on_delivery')",
            name="invoice_timing_known",
        ),
        CheckConstraint("offer_timeout_seconds > 0", name="offer_timeout_positive"),
        CheckConstraint("offer_rounds > 0", name="offer_rounds_positive"),
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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
