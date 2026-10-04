"""Reads for inventory and warehouses. Every query filters on `organization_id` explicitly; RLS is
the net, not the filter (CLAUDE.md).

Stock figures come from `inventory_balances` — through `InventoryItem.on_hand_milli` for the
item-level views and directly for the per-bucket view — never from a stored item column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import BigInteger, Select, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.inventory.quantity import QTY_SCALE, format_qty, whole_units
from app.models import (
    InventoryBalance,
    InventoryDiscrepancy,
    InventoryItem,
    InventoryMove,
    InventoryReservation,
    ItemAttribute,
    ItemCategory,
    ItemVariant,
    LedgerEntry,
    Location,
    PurchaseOrder,
    Uom,
    Warehouse,
)
from app.receiving import domain as receiving_domain
from app.schemas import (
    InventoryDiscrepancyOut,
    InventoryItemOut,
    InventoryReservationOut,
    ItemAttributeOut,
    ItemCategoryOut,
    ItemVariantOut,
    LocationOut,
    StockBalanceOut,
    StockMovementOut,
    UomOut,
    WarehouseOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ inventory items


def inventory_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    category: str | None = None,
    item_category_id: uuid.UUID | None = None,
    warehouse: str | None = None,
    low_stock: bool | None = None,
    is_active: bool | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Inventory on hand for the org. Not scope-narrowed: stock is shared org reference data
    (read still requires `item.read`). `low_stock` filters to items at or below reorder point.
    `category` is the fixed procurement bucket; `item_category_id` is the org's own tree
    (ADR-0014 Phase 1) — independent filters, since one item can sit in both."""
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
    if item_category_id:
        stmt = stmt.where(InventoryItem.item_category_id == item_category_id)
    if warehouse:
        stmt = stmt.where(InventoryItem.warehouse == warehouse)
    if low_stock:
        stmt = stmt.where(InventoryItem.on_hand_milli <= InventoryItem.reorder_point * QTY_SCALE)
    if is_active is not None:
        stmt = stmt.where(InventoryItem.is_active == is_active)

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
    """Fetch by SKU — used by the demo seed for idempotency, and by the goods-receipt
    path (`app.receiving.receipts`, locked) to match a PO line against a stocked item."""
    stmt = select(InventoryItem).where(
        InventoryItem.sku == sku,
        InventoryItem.organization_id == org_id,
        InventoryItem.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update(of=InventoryItem)
    return (await session.execute(stmt)).scalar_one_or_none()


async def item_barcode_taken(
    session: AsyncSession,
    org_id: uuid.UUID,
    barcode: str,
    *,
    exclude: uuid.UUID | None = None,
) -> bool:
    """Whether a live item of this organization already carries `barcode`."""
    stmt = select(InventoryItem.id).where(
        InventoryItem.organization_id == org_id,
        InventoryItem.barcode == barcode,
        InventoryItem.deleted_at.is_(None),
    )
    if exclude is not None:
        stmt = stmt.where(InventoryItem.id != exclude)
    return (await session.execute(stmt.limit(1))).first() is not None


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


async def balances_by_id(
    session: AsyncSession, org_id: uuid.UUID, balance_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, InventoryBalance]:
    """A batch of `inventory_balances` rows by id — one query for a whole handover/dispatch
    (N+1 avoidance), the same shape `app.orders.selectors.items_for_lines` uses for items.
    Shared by `app.orders.services.complete_handover` (both the counter and carrier handover
    paths draw against whichever buckets a reservation's own transfer chain last landed stock
    in) rather than living only in `app.dispatch`, since `orders` -> `inventory` is the one
    layering direction ADR-0024 §1 allows for this data."""
    if not balance_ids:
        return {}
    stmt = select(InventoryBalance).where(
        InventoryBalance.organization_id == org_id, InventoryBalance.id.in_(set(balance_ids))
    )
    return {b.id: b for b in (await session.execute(stmt)).scalars().all()}


async def has_stock_history(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    """Whether this item has ever had a balance bucket or a ledger row (M5) — not just whether it
    currently holds non-zero stock. Every quantity on either table was recorded in the item's
    `uom` at the time; changing the unit after even one such row exists would silently reinterpret
    history in a different scale, so the write path refuses the change rather than the read path
    having to guess which unit an old row meant."""
    balance_exists = (
        await session.execute(
            select(InventoryBalance.id)
            .where(
                InventoryBalance.organization_id == org_id,
                InventoryBalance.inventory_item_id == item_id,
            )
            .limit(1)
        )
    ).first()
    if balance_exists is not None:
        return True
    ledger_exists = (
        await session.execute(
            select(LedgerEntry.id)
            .where(LedgerEntry.organization_id == org_id, LedgerEntry.inventory_item_id == item_id)
            .limit(1)
        )
    ).first()
    return ledger_exists is not None


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


# ------------------------------------------------------------------ units of measure


def uoms_stmt(org_id: uuid.UUID) -> Select[Any]:
    """The org's UOM directory: system (registry) units first, then custom ones, each
    alphabetically — the shape a picker wants, registry units at the top."""
    return (
        select(Uom)
        .where(Uom.organization_id == org_id, Uom.deleted_at.is_(None))
        .order_by(Uom.is_system.desc(), Uom.code.asc())
    )


async def get_uom_by_code(session: AsyncSession, org_id: uuid.UUID, code: str) -> Uom | None:
    stmt = select(Uom).where(
        Uom.organization_id == org_id, Uom.code == code, Uom.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_uom(session: AsyncSession, org_id: uuid.UUID, uom_id: uuid.UUID) -> Uom | None:
    stmt = select(Uom).where(
        Uom.id == uom_id, Uom.organization_id == org_id, Uom.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def uom_out(u: Uom) -> UomOut:
    return UomOut(
        id=str(u.id),
        code=u.code,
        name=u.name,
        dimension=u.dimension,
        is_discrete=u.is_discrete,
        is_system=u.is_system,
    )


# ------------------------------------------------------------------ item categories


def item_categories_stmt(org_id: uuid.UUID) -> Select[Any]:
    """The org's category tree, flattened for a list screen to nest client-side — small and
    bounded, like the warehouse directory."""
    return (
        select(ItemCategory)
        .where(ItemCategory.organization_id == org_id, ItemCategory.deleted_at.is_(None))
        .order_by(ItemCategory.name.asc())
    )


async def get_item_category(
    session: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID
) -> ItemCategory | None:
    stmt = select(ItemCategory).where(
        ItemCategory.id == category_id,
        ItemCategory.organization_id == org_id,
        ItemCategory.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_item_category_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> ItemCategory | None:
    stmt = select(ItemCategory).where(
        ItemCategory.code == code,
        ItemCategory.organization_id == org_id,
        ItemCategory.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_item_category_children(
    session: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID
) -> int:
    stmt = (
        select(func.count())
        .select_from(ItemCategory)
        .where(
            ItemCategory.organization_id == org_id,
            ItemCategory.parent_id == category_id,
            ItemCategory.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def count_items_in_category(
    session: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID
) -> int:
    stmt = (
        select(func.count())
        .select_from(InventoryItem)
        .where(
            InventoryItem.organization_id == org_id,
            InventoryItem.item_category_id == category_id,
            InventoryItem.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one()


def category_out(c: ItemCategory) -> ItemCategoryOut:
    return ItemCategoryOut(
        id=str(c.id), code=c.code, name=c.name, parent_id=str(c.parent_id) if c.parent_id else None
    )


# ------------------------------------------------------------------ item attributes


def item_attributes_stmt(org_id: uuid.UUID, item_id: uuid.UUID) -> Select[Any]:
    """One item's typed attributes — small and bounded, read whole like its balances."""
    return (
        select(ItemAttribute)
        .where(ItemAttribute.organization_id == org_id, ItemAttribute.inventory_item_id == item_id)
        .order_by(ItemAttribute.key.asc())
    )


async def item_attributes_for(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> list[ItemAttribute]:
    stmt = item_attributes_stmt(org_id, item_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_item_attribute(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, key: str
) -> ItemAttribute | None:
    stmt = select(ItemAttribute).where(
        ItemAttribute.organization_id == org_id,
        ItemAttribute.inventory_item_id == item_id,
        ItemAttribute.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def attribute_out(a: ItemAttribute) -> ItemAttributeOut:
    return ItemAttributeOut(
        id=str(a.id),
        key=a.key,
        value_type=a.value_type,
        value_text=a.value_text,
        value_number=a.value_number,
        value_boolean=a.value_boolean,
        value_date=a.value_date,
    )


# ------------------------------------------------------------------ item variants


def item_variants_stmt(org_id: uuid.UUID, item_id: uuid.UUID) -> Select[Any]:
    return (
        select(ItemVariant)
        .where(
            ItemVariant.organization_id == org_id,
            ItemVariant.inventory_item_id == item_id,
            ItemVariant.deleted_at.is_(None),
        )
        .order_by(ItemVariant.sku.asc())
    )


async def item_variants_for(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> list[ItemVariant]:
    stmt = item_variants_stmt(org_id, item_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_item_variant(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, variant_id: uuid.UUID
) -> ItemVariant | None:
    stmt = select(ItemVariant).where(
        ItemVariant.id == variant_id,
        ItemVariant.organization_id == org_id,
        ItemVariant.inventory_item_id == item_id,
        ItemVariant.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_item_variant_by_sku(
    session: AsyncSession, org_id: uuid.UUID, sku: str
) -> ItemVariant | None:
    stmt = select(ItemVariant).where(
        ItemVariant.sku == sku,
        ItemVariant.organization_id == org_id,
        ItemVariant.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def variant_out(v: ItemVariant) -> ItemVariantOut:
    return ItemVariantOut(
        id=str(v.id),
        inventory_item_id=str(v.inventory_item_id),
        sku=v.sku,
        name=v.name,
        is_active=v.is_active,
    )


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
            PurchaseOrder.status.in_(("draft", "approved", "partially_received")),
        )
    )
    return (await session.execute(stmt)).scalar_one()


def locations_stmt(org_id: uuid.UUID, warehouse_id: uuid.UUID) -> Select[Any]:
    """A warehouse's location tree, every kind (ADR-0024 §5), ordered for a readable walk:
    pick sequence first (locations with none sort last), then code."""
    return (
        select(Location)
        .where(
            Location.organization_id == org_id,
            Location.warehouse_id == warehouse_id,
            Location.deleted_at.is_(None),
        )
        .order_by(Location.pick_sequence.asc().nulls_last(), Location.code.asc())
    )


async def get_location(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> Location | None:
    stmt = select(Location).where(
        Location.id == location_id,
        Location.organization_id == org_id,
        Location.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_location_by_code(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID, code: str
) -> Location | None:
    stmt = select(Location).where(
        Location.organization_id == org_id,
        Location.warehouse_id == warehouse_id,
        Location.code == code,
        Location.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_location_by_barcode(
    session: AsyncSession, org_id: uuid.UUID, barcode: str
) -> Location | None:
    """Resolve a scanned barcode to its location — org-scoped, exact string match. `None` for a
    barcode that does not exist in this org *and* for one that belongs to another org — a scan
    is refused the same 404 either way, never a 403 that would confirm the barcode is real."""
    stmt = select(Location).where(
        Location.organization_id == org_id,
        Location.barcode == barcode,
        Location.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def descendant_location_ids(
    session: AsyncSession, org_id: uuid.UUID, root_location_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every location at or below `root_location_id` in the warehouse tree (ADR-0026 §6, a count
    session's `ZONE` scope). `Location.parent_id` is a plain adjacency list — the same shape
    `ItemCategory` uses and, per that model's own docstring, never needed a recursive query
    because that tree stays two or three levels deep. A warehouse's zone -> aisle -> rack -> shelf
    -> bin tree does not make the same promise, so this is a real `WITH RECURSIVE`, org-filtered
    at every level (a location's `organization_id` never differs from its parent's, but the filter
    costs nothing and this never trusts a join alone for tenant isolation)."""
    cte_sql = text(
        """
        WITH RECURSIVE descendants AS (
            SELECT id FROM operations.locations
             WHERE id = :root AND organization_id = :org AND deleted_at IS NULL
            UNION ALL
            SELECT l.id
              FROM operations.locations l
              JOIN descendants d ON l.parent_id = d.id
             WHERE l.organization_id = :org AND l.deleted_at IS NULL
        )
        SELECT id FROM descendants
        """
    )
    rows = (await session.execute(cte_sql, {"root": root_location_id, "org": org_id})).all()
    return [row.id for row in rows]


async def balances_in_locations(
    session: AsyncSession,
    org_id: uuid.UUID,
    location_ids: Sequence[uuid.UUID],
    *,
    status: str = "AVAILABLE",
) -> list[InventoryBalance]:
    """Every balance row in a set of locations, one status — a count session's open-time snapshot
    source (ADR-0026 §6). Empty `location_ids` returns no rows rather than every bucket in the
    org; the caller resolving an empty scope is a bug to see immediately, not a full-warehouse
    surprise."""
    if not location_ids:
        return []
    stmt = select(InventoryBalance).where(
        InventoryBalance.organization_id == org_id,
        InventoryBalance.location_id.in_(location_ids),
        InventoryBalance.status == status,
    )
    return list((await session.execute(stmt)).scalars().all())


async def location_has_ledger_history(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> bool:
    """Whether any ledger entry or balance row has ever referenced this location — the gate
    between a plain `location.write` kind/capacity edit and one that needs `layout.publish`
    (ADR-0024 §5). Checking the ledger and the balances covers every mover: a move with no
    surviving balance (fully issued back out) still left a ledger row behind, and a balance
    always traces back to at least one."""
    ledger_stmt = (
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.organization_id == org_id, LedgerEntry.location_id == location_id)
    )
    if (await session.execute(ledger_stmt)).scalar_one():
        return True
    balance_stmt = (
        select(func.count())
        .select_from(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.location_id == location_id,
        )
    )
    return bool((await session.execute(balance_stmt)).scalar_one())


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
        item_category_id=str(item.item_category_id) if item.item_category_id else None,
        secondary_uom_id=str(item.secondary_uom_id) if item.secondary_uom_id else None,
        hsn_code=item.hsn_code,
        track_lot=item.track_lot,
        track_roll=item.track_roll,
        track_expiry=item.track_expiry,
        track_quality=item.track_quality,
        is_active=item.is_active,
        receipt_tolerance_pct=receiving_domain.format_tolerance_pct(item.receipt_tolerance_bp),
        selling_price_minor=item.selling_price_minor,
        selling_price_currency=item.selling_price_currency,
        barcode=item.barcode,
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


def reservation_out(reservation: InventoryReservation) -> InventoryReservationOut:
    # `transferred_milli` (ADR-0024 §6) is no longer open either — it moved to a destination
    # reservation with a pick or pack — so it comes off `remaining` the same way `consumed_milli`
    # and `released_milli` already do. `InventoryReservationOut.status` and `.transferred` both
    # know about it (`app/schemas.py`), so a reservation that closes as `"transferred"` — or one
    # only partially transferred and still `"active"` — renders correctly through this function.
    remaining = (
        reservation.quantity_milli
        - reservation.consumed_milli
        - reservation.released_milli
        - reservation.transferred_milli
    )
    return InventoryReservationOut(
        id=str(reservation.id),
        inventory_item_id=str(reservation.inventory_item_id),
        quantity=format_qty(reservation.quantity_milli),
        consumed=format_qty(reservation.consumed_milli),
        released=format_qty(reservation.released_milli),
        transferred=format_qty(reservation.transferred_milli),
        remaining=format_qty(remaining),
        status=reservation.status,
        reference_type=reservation.reference_type,
        reference_id=str(reservation.reference_id) if reservation.reference_id else None,
    )


def warehouse_out(warehouse: Warehouse) -> WarehouseOut:
    return WarehouseOut(
        id=str(warehouse.id),
        code=warehouse.code,
        name=warehouse.name,
        location=warehouse.location,
    )


def location_out(location: Location) -> LocationOut:
    return LocationOut(
        id=str(location.id),
        warehouse_id=str(location.warehouse_id),
        parent_id=str(location.parent_id) if location.parent_id else None,
        kind=location.kind,
        code=location.code,
        name=location.name,
        is_default=location.is_default,
        barcode=location.barcode,
        pick_sequence=location.pick_sequence,
        max_weight_grams=location.max_weight_grams,
        max_volume_milli=location.max_volume_milli,
        storage_type=location.storage_type,
    )
