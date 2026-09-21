"""Tables owned by the operations service — procurement, inventory and suppliers.

Everything here lives in the `operations` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to people are plain UUIDs (`raised_by_id`) — never a constraint
that crosses a schema boundary. Links **within** this schema (`purchase_orders.supplier_id`,
`purchase_order_lines.purchase_order_id`) are real foreign keys, because only cross-schema
foreign keys are banned.

Six tables, one schema:

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
* **inventory_items** — stock on hand at a warehouse. `on_hand` is a **cached balance**, changed
  only by inserting a `stock_movements` row in the same transaction — never a bare column write.
  Low-stock is *derived* (`on_hand <= reorder_point`), never a stored flag, so it can never
  contradict the counts.
* **stock_movements** — the append-only ledger `on_hand` is the running sum of. `UPDATE`/`DELETE`
  are revoked from the app role (like `audit_logs`); a wrong movement is corrected by an
  opposite one, never rewritten.

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

from sqlalchemy import (
    BigInteger,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

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


class InventoryItem(Base, TenantBase):
    """A stocked material at a warehouse.

    `sku` is the human identity, unique within an organization. Low-stock is **derived**
    (`on_hand <= reorder_point`) rather than stored, so a flag can never disagree with the count.
    `unit_value_minor` is the per-unit stock value in integer minor units; stock value is
    `on_hand * unit_value_minor`, computed on read. `stocked_at` is when the count was last taken
    — surfaced as `updated_at` on the wire, and distinct from the row's own audit `updated_at`.
    """

    __tablename__ = "inventory_items"

    sku: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: raw_materials | packaging | components | mro | logistics | services.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Denormalised warehouse name, matching the wire.
    warehouse: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uom: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    #: Per-unit stock value, integer minor units of `currency`.
    unit_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: When the stock count was last taken. Surfaced as `updated_at` on the wire.
    stocked_at: Mapped[date | None] = mapped_column(Date)

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


class StockMovement(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only fact about an inventory item's balance changing.

    `inventory_items.on_hand` is a cached running balance; this table is the only thing
    allowed to change it, and only by inserting a row here in the same transaction (never a
    bare column write). `on_hand_after` is the resulting balance, so the item's current
    `on_hand` must always equal the most recent movement's `on_hand_after`, and the sum of
    every movement's `quantity_delta` for an item must always equal its `on_hand`.

    No `TenantBase`: this table carries no `updated_at`/`deleted_at` — a movement is never
    edited or removed once written (`UPDATE`/`DELETE` are revoked from the app role in the
    migration and re-revoked in `migrations/env.py` after every grant re-application). A wrong
    adjustment is corrected by an opposite movement, not a rewrite.
    """

    __tablename__ = "stock_movements"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: opening | receipt | adjustment | count.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: damaged | lost | found | consumed | returned | other. Required when `kind == "adjustment"`.
    reason: Mapped[str | None] = mapped_column(String(30))
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The resulting balance — `on_hand` after this movement is applied.
    on_hand_after: Mapped[int] = mapped_column(Integer, nullable=False)
    #: "purchase_order" when a receipt posts against one. A plain UUID, never a cross-entity FK.
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("on_hand_after >= 0", name="on_hand_after_non_negative"),
        CheckConstraint(
            "quantity_delta <> 0 OR kind = 'count'",
            name="quantity_delta_nonzero",
        ),
        Index(
            "ix_stock_movements_org_item_created",
            "organization_id",
            "inventory_item_id",
            text("created_at DESC"),
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
