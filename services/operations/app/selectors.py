"""Reads for the operations service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The filter
is not redundant with row-level security: RLS is the net under the tightrope, not permission to
walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds its own query
instead of coming through a selector is how an organization leaks, so routers never write SQL.

Operations scope is org-wide with an OWN reading of "the purchase orders I raised" — the owner
column is `raised_by_id`. Suppliers, inventory and warehouses are org reference data with no
per-person owner, so their selectors filter by organization only (read still requires the
permission); TEAM and LOCATION are not offered (see `scope.py`).

This module also holds the ORM→schema projections, because shaping a row for the wire is part of
reading it: the human ids, the denormalised names and the nested line items are resolved here so
routes stay thin.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InventoryItem,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMovement,
    Supplier,
    Warehouse,
)
from app.schemas import (
    InventoryItemOut,
    PoLineItem,
    PurchaseOrderOut,
    StockMovementOut,
    SupplierOut,
    WarehouseOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ suppliers


def suppliers_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Supplier directory for the org. Not scope-narrowed: suppliers are shared reference data
    within the organization (read still requires `supplier.read`)."""
    stmt = select(Supplier).where(
        Supplier.organization_id == ctx.organization_id,
        Supplier.deleted_at.is_(None),
    )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Supplier.code).like(needle),
                func.lower(Supplier.name).like(needle),
                func.lower(Supplier.contact_name).like(needle),
                func.lower(Supplier.location).like(needle),
            )
        )
    if category:
        stmt = stmt.where(Supplier.category == category)
    if status:
        stmt = stmt.where(Supplier.status == status)

    if sort == "spend":
        return stmt.order_by(Supplier.spend_minor.desc(), Supplier.name.asc())
    if sort == "on_time":
        return stmt.order_by(Supplier.on_time_pct.desc(), Supplier.name.asc())
    if sort == "lead_time":
        return stmt.order_by(Supplier.lead_time_days.asc(), Supplier.name.asc())
    return stmt.order_by(Supplier.name.asc())


async def get_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID, *, for_update: bool = False
) -> Supplier | None:
    """Org-scoped fetch by primary key. None on a tenant mismatch or a soft-deleted row.
    `for_update` locks the row — used wherever a rollup (`spend_minor`, `open_pos`) is about to
    be bumped, so two concurrent PO transitions cannot lose one's update."""
    stmt = select(Supplier).where(
        Supplier.id == supplier_id,
        Supplier.organization_id == org_id,
        Supplier.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_supplier_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> Supplier | None:
    """Fetch by human code — used by the demo seed for idempotency."""
    stmt = select(Supplier).where(
        Supplier.code == code,
        Supplier.organization_id == org_id,
        Supplier.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_suppliers(session: AsyncSession, org_id: uuid.UUID) -> list[Supplier]:
    """Every supplier in the org — powers the overview rollups."""
    stmt = select(Supplier).where(Supplier.organization_id == org_id, Supplier.deleted_at.is_(None))
    return list((await session.execute(stmt)).scalars().all())


async def count_open_purchase_orders_for_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID
) -> int:
    """How many `draft`/`approved` purchase orders still name this supplier — the delete guard
    (409 while any are open; suggest `on_hold` instead)."""
    stmt = (
        select(func.count())
        .select_from(PurchaseOrder)
        .where(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.deleted_at.is_(None),
            PurchaseOrder.status.in_(("draft", "approved")),
        )
    )
    return (await session.execute(stmt)).scalar_one()


# ------------------------------------------------------------------ purchase orders


def purchase_orders_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    category: str | None = None,
    supplier_id: uuid.UUID | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Scope-filtered purchase-order list. `ctx.apply` is the enforcement point (OWN reads the
    POs the caller raised)."""
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.organization_id == ctx.organization_id,
        PurchaseOrder.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=PurchaseOrder.raised_by_id)

    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(PurchaseOrder.number).like(needle),
                func.lower(PurchaseOrder.supplier_name).like(needle),
                func.lower(PurchaseOrder.raised_by).like(needle),
                func.lower(PurchaseOrder.warehouse).like(needle),
            )
        )
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if category:
        stmt = stmt.where(PurchaseOrder.category == category)

    if sort == "expected":
        return stmt.order_by(PurchaseOrder.expected_date.asc(), PurchaseOrder.number.desc())
    if sort == "created":
        return stmt.order_by(PurchaseOrder.created_date.desc(), PurchaseOrder.number.desc())
    return stmt.order_by(PurchaseOrder.number.desc())


async def get_purchase_order(
    session: AsyncSession, org_id: uuid.UUID, po_id: uuid.UUID
) -> PurchaseOrder | None:
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.id == po_id,
        PurchaseOrder.organization_id == org_id,
        PurchaseOrder.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_purchase_order_by_number(
    session: AsyncSession, org_id: uuid.UUID, number: str
) -> PurchaseOrder | None:
    """Fetch by human number — used by the demo seed for idempotency."""
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.number == number,
        PurchaseOrder.organization_id == org_id,
        PurchaseOrder.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lines_for(
    session: AsyncSession, org_id: uuid.UUID, po_id: uuid.UUID
) -> list[PurchaseOrderLine]:
    """The line items on one purchase order, in insertion order."""
    stmt = (
        select(PurchaseOrderLine)
        .where(
            PurchaseOrderLine.organization_id == org_id,
            PurchaseOrderLine.purchase_order_id == po_id,
        )
        .order_by(PurchaseOrderLine.created_at.asc(), PurchaseOrderLine.sku.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def lines_map(
    session: AsyncSession, org_id: uuid.UUID, po_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[PurchaseOrderLine]]:
    """All lines for a set of POs, grouped by PO id — one query for a whole list page."""
    if not po_ids:
        return {}
    stmt = (
        select(PurchaseOrderLine)
        .where(
            PurchaseOrderLine.organization_id == org_id,
            PurchaseOrderLine.purchase_order_id.in_(po_ids),
        )
        .order_by(PurchaseOrderLine.created_at.asc(), PurchaseOrderLine.sku.asc())
    )
    out: dict[uuid.UUID, list[PurchaseOrderLine]] = {}
    for line in (await session.execute(stmt)).scalars().all():
        out.setdefault(line.purchase_order_id, []).append(line)
    return out


async def all_purchase_orders(session: AsyncSession, ctx: ScopeContext) -> list[PurchaseOrder]:
    """Every in-scope purchase order — powers the overview rollups."""
    return list((await session.execute(purchase_orders_stmt(ctx))).scalars().all())


async def all_lines(session: AsyncSession, org_id: uuid.UUID) -> list[PurchaseOrderLine]:
    """Every line in the org, for building the PO-total map the overview needs."""
    stmt = select(PurchaseOrderLine).where(PurchaseOrderLine.organization_id == org_id)
    return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------------------------ inventory


def inventory_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    category: str | None = None,
    warehouse: str | None = None,
    low_stock: bool | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Inventory on hand for the org. Not scope-narrowed: stock is shared org reference data
    (read still requires `item.read`). `low_stock` filters to items at or below reorder point."""
    stmt = select(InventoryItem).where(
        InventoryItem.organization_id == ctx.organization_id,
        InventoryItem.deleted_at.is_(None),
    )
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(InventoryItem.sku).like(needle),
                func.lower(InventoryItem.name).like(needle),
                func.lower(InventoryItem.warehouse).like(needle),
            )
        )
    if category:
        stmt = stmt.where(InventoryItem.category == category)
    if warehouse:
        stmt = stmt.where(InventoryItem.warehouse == warehouse)
    if low_stock:
        stmt = stmt.where(InventoryItem.on_hand <= InventoryItem.reorder_point)

    if sort == "on_hand":
        return stmt.order_by(InventoryItem.on_hand.asc(), InventoryItem.sku.asc())
    if sort == "value":
        return stmt.order_by(
            (InventoryItem.on_hand * InventoryItem.unit_value_minor).desc(),
            InventoryItem.sku.asc(),
        )
    return stmt.order_by(InventoryItem.sku.asc())


async def get_inventory_by_sku(
    session: AsyncSession, org_id: uuid.UUID, sku: str, *, for_update: bool = False
) -> InventoryItem | None:
    """Fetch by SKU — used by the demo seed for idempotency, and by `receive_purchase_order`
    (locked) to match a PO line against a stocked item."""
    stmt = select(InventoryItem).where(
        InventoryItem.sku == sku,
        InventoryItem.organization_id == org_id,
        InventoryItem.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_inventory_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
) -> InventoryItem | None:
    """Org-scoped fetch by primary key. `for_update` locks the row so a concurrent stock
    adjustment cannot lose an update to `on_hand`."""
    stmt = select(InventoryItem).where(
        InventoryItem.id == item_id,
        InventoryItem.organization_id == org_id,
        InventoryItem.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_inventory(session: AsyncSession, org_id: uuid.UUID) -> list[InventoryItem]:
    """Every inventory item in the org — powers the overview rollups."""
    stmt = select(InventoryItem).where(
        InventoryItem.organization_id == org_id, InventoryItem.deleted_at.is_(None)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_inventory_for_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_name: str
) -> int:
    """How many live inventory items still carry this warehouse's name — a delete guard."""
    stmt = (
        select(func.count())
        .select_from(InventoryItem)
        .where(
            InventoryItem.organization_id == org_id,
            InventoryItem.warehouse == warehouse_name,
            InventoryItem.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one()


# ------------------------------------------------------------------ stock movements


def movements_stmt(
    ctx: ScopeContext, item_id: uuid.UUID, *, kind: str | None = None
) -> Select[Any]:
    """One item's movement history, newest first. Not scope-narrowed — movement reads reuse
    `operations.inventory.item.read`, which is org-wide, same as the item itself."""
    stmt = select(StockMovement).where(
        StockMovement.organization_id == ctx.organization_id,
        StockMovement.inventory_item_id == item_id,
    )
    if kind:
        stmt = stmt.where(StockMovement.kind == kind)
    return stmt.order_by(StockMovement.created_at.desc())


# ------------------------------------------------------------------ warehouses (reference)


def warehouses_stmt(org_id: uuid.UUID) -> Select[Any]:
    """The org's warehouses, ordered by name. Shared reference data (read requires
    `warehouse.read`), not per-person, so it is not scope-narrowed."""
    return (
        select(Warehouse)
        .where(Warehouse.organization_id == org_id, Warehouse.deleted_at.is_(None))
        .order_by(Warehouse.name.asc())
    )


async def get_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Warehouse | None:
    stmt = select(Warehouse).where(
        Warehouse.id == warehouse_id,
        Warehouse.organization_id == org_id,
        Warehouse.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_warehouse_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> Warehouse | None:
    stmt = select(Warehouse).where(
        Warehouse.code == code,
        Warehouse.organization_id == org_id,
        Warehouse.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_warehouse_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Warehouse | None:
    """Fetch by display name — the join key `inventory_items.warehouse` and
    `purchase_orders.warehouse` carry, and what a create/patch payload's `warehouse` field is
    validated against."""
    stmt = select(Warehouse).where(
        Warehouse.name == name,
        Warehouse.organization_id == org_id,
        Warehouse.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_open_purchase_orders_for_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_name: str
) -> int:
    """How many `draft`/`approved` purchase orders still target this warehouse — a delete
    guard, alongside `count_inventory_for_warehouse`."""
    stmt = (
        select(func.count())
        .select_from(PurchaseOrder)
        .where(
            PurchaseOrder.organization_id == org_id,
            PurchaseOrder.warehouse == warehouse_name,
            PurchaseOrder.deleted_at.is_(None),
            PurchaseOrder.status.in_(("draft", "approved")),
        )
    )
    return (await session.execute(stmt)).scalar_one()


# ------------------------------------------------------------------ projections (ORM → wire)


def supplier_out(supplier: Supplier) -> SupplierOut:
    return SupplierOut(
        id=str(supplier.id),
        code=supplier.code,
        name=supplier.name,
        category=supplier.category,
        status=supplier.status,
        lead_time_days=supplier.lead_time_days,
        on_time_pct=float(supplier.on_time_pct),
        spend_minor=supplier.spend_minor,
        currency=supplier.currency,
        open_pos=supplier.open_pos,
        contact_name=supplier.contact_name,
        contact_email=supplier.contact_email,
        location=supplier.location,
        since=supplier.since,
    )


def po_line_out(line: PurchaseOrderLine) -> PoLineItem:
    return PoLineItem(
        id=str(line.id),
        sku=line.sku,
        description=line.description,
        quantity=line.quantity,
        uom=line.uom,
        unit_price_minor=line.unit_price_minor,
        received_qty=line.received_qty,
    )


def purchase_order_out(
    po: PurchaseOrder, *, lines: Sequence[PurchaseOrderLine] = ()
) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=str(po.id),
        po_number=po.number,
        supplier_id=str(po.supplier_id),
        supplier_name=po.supplier_name,
        category=po.category,
        status=po.status,
        currency=po.currency,
        warehouse=po.warehouse,
        raised_by=po.raised_by,
        created_date=po.created_date,
        expected_date=po.expected_date,
        received_date=po.received_date,
        line_items=[po_line_out(line) for line in lines],
        notes=po.notes,
    )


def inventory_out(item: InventoryItem) -> InventoryItemOut:
    return InventoryItemOut(
        id=str(item.id),
        sku=item.sku,
        name=item.name,
        category=item.category,
        warehouse=item.warehouse,
        on_hand=item.on_hand,
        reorder_point=item.reorder_point,
        uom=item.uom,
        unit_value_minor=item.unit_value_minor,
        currency=item.currency,
        updated_at=item.stocked_at,
    )


def warehouse_out(warehouse: Warehouse) -> WarehouseOut:
    return WarehouseOut(
        id=str(warehouse.id),
        code=warehouse.code,
        name=warehouse.name,
        location=warehouse.location,
    )


def movement_out(movement: StockMovement) -> StockMovementOut:
    return StockMovementOut(
        id=str(movement.id),
        kind=movement.kind,
        reason=movement.reason,
        quantity_delta=movement.quantity_delta,
        on_hand_after=movement.on_hand_after,
        reference_type=movement.reference_type,
        reference_id=str(movement.reference_id) if movement.reference_id else None,
        note=movement.note,
        occurred_on=movement.occurred_on,
        created_at=movement.created_at,
        created_by=movement.created_by,
    )
