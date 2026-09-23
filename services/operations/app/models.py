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
    """A place stock sits inside a warehouse: zone → rack → bin (ADR-0014, plan §2).

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
    #: zone | rack | bin
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="bin")
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("kind IN ('zone', 'rack', 'bin')", name="kind_known"),
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
    )


class InventoryItem(Base, TenantBase):
    """The item master: one stocked material, identified by `sku` within the organization.

    Stock is **not stored here** (ADR-0014 §10). `on_hand_milli` / `reserved_milli` are SQL
    aggregates over `inventory_balances`, attached below as column properties so they can be
    filtered and sorted in SQL, and `on_hand` is the whole-unit compatibility view of them.
    Low-stock is derived (`on_hand_milli <= reorder_point * 1000`), never a stored flag.

    `warehouse` is the denormalised display name the wire has always carried; `home_location_id`
    is the default bin of that warehouse, where single-location writes (opening stock, PO receipt,
    adjustments, counts) post until Phase 1 brings per-location writes.
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
    #: The stock UOM every quantity of this item is counted in (milli-units of it).
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    #: Per-unit stock value, integer minor units of `currency`.
    unit_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: When stock last moved. Surfaced as `updated_at` on the wire.
    stocked_at: Mapped[date | None] = mapped_column(Date)

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

    `quantity_milli - consumed_milli` is the open remainder while `status == "active"`; the sum
    of open remainders per balance is that balance's `reserved_milli`.
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
            "consumed_milli >= 0 AND consumed_milli <= quantity_milli", name="consumed_within"
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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
