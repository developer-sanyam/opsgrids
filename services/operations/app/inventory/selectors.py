"""Reads for inventory and warehouses. Every query filters on `organization_id` explicitly; RLS is
the net, not the filter (CLAUDE.md).

Stock figures come from `inventory_balances` — through `InventoryItem.on_hand_milli` for the
item-level views and directly for the per-bucket view — never from a stored item column.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Select, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.inventory.quantity import QTY_SCALE, format_qty, whole_units
from app.models import (
    InventoryBalance,
    InventoryDiscrepancy,
    InventoryItem,
    InventoryMove,
    LedgerEntry,
    Location,
    PurchaseOrder,
    Warehouse,
)
from app.schemas import (
    InventoryDiscrepancyOut,
    InventoryItemOut,
    StockBalanceOut,
    StockMovementOut,
    WarehouseOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ inventory items


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
        stmt = stmt.where(InventoryItem.on_hand_milli <= InventoryItem.reorder_point * QTY_SCALE)

    if sort == "on_hand":
        return stmt.order_by(InventoryItem.on_hand_milli.asc(), InventoryItem.sku.asc())
    if sort == "value":
        return stmt.order_by(
            (InventoryItem.on_hand_milli * InventoryItem.unit_value_minor).desc(),
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
        stmt = stmt.with_for_update(of=InventoryItem)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_inventory_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
) -> InventoryItem | None:
    """Org-scoped fetch by primary key. `for_update` locks the item row — the item-level routes
    take it before posting, so two edits of one item's card serialise."""
    stmt = select(InventoryItem).where(
        InventoryItem.id == item_id,
        InventoryItem.organization_id == org_id,
        InventoryItem.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update(of=InventoryItem)
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


# ------------------------------------------------------------------ stock


def movements_stmt(
    ctx: ScopeContext, item_id: uuid.UUID, *, kind: str | None = None
) -> Select[Any]:
    """One item's ledger, newest first, each row with its move header. Not scope-narrowed —
    ledger reads reuse `operations.inventory.item.read`, which is org-wide, like the item."""
    stmt = (
        select(LedgerEntry)
        .join(InventoryMove, InventoryMove.id == LedgerEntry.move_id)
        .options(contains_eager(LedgerEntry.move))
        .where(
            LedgerEntry.organization_id == ctx.organization_id,
            InventoryMove.organization_id == ctx.organization_id,
            LedgerEntry.inventory_item_id == item_id,
        )
    )
    if kind:
        stmt = stmt.where(InventoryMove.kind == kind)
    return stmt.order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())


async def balances_for_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> list[tuple[InventoryBalance, str, str, str]]:
    """Every non-empty bucket of one item, with its warehouse code, warehouse name and location
    code — the item's stock broken down by where and in what state it is."""
    stmt = (
        select(InventoryBalance, Warehouse.code, Warehouse.name, Location.code)
        .join(Location, Location.id == InventoryBalance.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.inventory_item_id == item_id,
            (InventoryBalance.quantity_milli != 0) | (InventoryBalance.reserved_milli != 0),
        )
        .order_by(Warehouse.code, Location.code, InventoryBalance.status)
    )
    return [tuple(row) for row in (await session.execute(stmt)).all()]


async def stock_outside_home(session: AsyncSession, item: InventoryItem) -> int:
    """Milli-units of this item held anywhere but its home AVAILABLE bucket — what the
    single-location compatibility API cannot see, and so must not overwrite."""
    stmt = select(
        cast(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0), BigInteger)
    ).where(
        InventoryBalance.organization_id == item.organization_id,
        InventoryBalance.inventory_item_id == item.id,
        ~(
            (InventoryBalance.location_id == item.home_location_id)
            & (InventoryBalance.status == "AVAILABLE")
            & InventoryBalance.lot_id.is_(None)
            & InventoryBalance.roll_id.is_(None)
        ),
    )
    return int((await session.execute(stmt)).scalar_one())


async def stock_at_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> int:
    """How many buckets (of any item) still hold or reserve stock at this warehouse's locations."""
    stmt = (
        select(func.count())
        .select_from(InventoryBalance)
        .join(Location, Location.id == InventoryBalance.location_id)
        .where(
            InventoryBalance.organization_id == org_id,
            Location.warehouse_id == warehouse_id,
            (InventoryBalance.quantity_milli != 0) | (InventoryBalance.reserved_milli != 0),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


def discrepancies_stmt(ctx: ScopeContext, *, open_only: bool = True) -> Select[Any]:
    stmt = select(InventoryDiscrepancy).where(
        InventoryDiscrepancy.organization_id == ctx.organization_id
    )
    if open_only:
        stmt = stmt.where(InventoryDiscrepancy.resolved_at.is_(None))
    return stmt.order_by(InventoryDiscrepancy.detected_at.desc(), InventoryDiscrepancy.id)


async def discrepancies_for_run(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID
) -> list[InventoryDiscrepancy]:
    """The open discrepancies one reconciliation run found or re-saw."""
    stmt = (
        select(InventoryDiscrepancy)
        .where(
            InventoryDiscrepancy.organization_id == org_id,
            InventoryDiscrepancy.run_id == run_id,
            InventoryDiscrepancy.resolved_at.is_(None),
        )
        .order_by(InventoryDiscrepancy.detected_at, InventoryDiscrepancy.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_discrepancy(
    session: AsyncSession, org_id: uuid.UUID, discrepancy_id: uuid.UUID
) -> InventoryDiscrepancy | None:
    stmt = (
        select(InventoryDiscrepancy)
        .where(
            InventoryDiscrepancy.organization_id == org_id,
            InventoryDiscrepancy.id == discrepancy_id,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ warehouses & locations


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


async def default_location(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Location | None:
    """The warehouse's default bin — where single-location stock writes land."""
    stmt = select(Location).where(
        Location.organization_id == org_id,
        Location.warehouse_id == warehouse_id,
        Location.is_default.is_(True),
        Location.deleted_at.is_(None),
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


def inventory_out(item: InventoryItem) -> InventoryItemOut:
    return InventoryItemOut(
        id=str(item.id),
        sku=item.sku,
        name=item.name,
        category=item.category,
        warehouse=item.warehouse,
        on_hand=item.on_hand,
        on_hand_qty=format_qty(item.on_hand_milli),
        reserved_qty=format_qty(item.reserved_milli),
        available_qty=format_qty(item.available_milli),
        reorder_point=item.reorder_point,
        uom=item.uom,
        unit_value_minor=item.unit_value_minor,
        currency=item.currency,
        updated_at=item.stocked_at,
    )


def movement_out(entry: LedgerEntry) -> StockMovementOut:
    move = entry.move
    return StockMovementOut(
        id=str(entry.id),
        kind=move.kind,
        reason=move.reason,
        quantity_delta=whole_units(entry.quantity_milli),
        on_hand_after=whole_units(entry.balance_after_milli),
        quantity=format_qty(entry.quantity_milli),
        balance_after=format_qty(entry.balance_after_milli),
        location_id=str(entry.location_id),
        status=entry.status,
        reference_type=move.reference_type,
        reference_id=str(move.reference_id) if move.reference_id else None,
        note=move.note,
        occurred_on=move.occurred_on,
        created_at=entry.created_at,
        created_by=entry.created_by,
    )


def balance_out(
    balance: InventoryBalance, warehouse_code: str, warehouse_name: str, location_code: str
) -> StockBalanceOut:
    return StockBalanceOut(
        location_id=str(balance.location_id),
        warehouse_code=warehouse_code,
        warehouse=warehouse_name,
        location_code=location_code,
        status=balance.status,
        lot_id=str(balance.lot_id) if balance.lot_id else None,
        roll_id=str(balance.roll_id) if balance.roll_id else None,
        on_hand_qty=format_qty(balance.quantity_milli),
        reserved_qty=format_qty(balance.reserved_milli),
        available_qty=format_qty(balance.quantity_milli - balance.reserved_milli),
    )


def discrepancy_out(row: InventoryDiscrepancy) -> InventoryDiscrepancyOut:
    return InventoryDiscrepancyOut(
        id=str(row.id),
        kind=row.kind,
        inventory_item_id=str(row.inventory_item_id),
        location_id=str(row.location_id),
        status=row.status,
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        stored_qty=format_qty(row.stored_milli),
        expected_qty=format_qty(row.expected_milli),
        detected_at=row.detected_at,
        last_seen_at=row.last_seen_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        resolution_note=row.resolution_note,
    )


def warehouse_out(warehouse: Warehouse) -> WarehouseOut:
    return WarehouseOut(
        id=str(warehouse.id),
        code=warehouse.code,
        name=warehouse.name,
        location=warehouse.location,
    )
