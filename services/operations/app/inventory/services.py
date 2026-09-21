"""Writes for inventory items, warehouses and stock.

Same platform shape as `app/services.py`: change the rows, record the audit entry, stage the
event — all in the caller's transaction. Every stock change goes through
`app.inventory.ledger.post_move`; nothing here writes a quantity.

The item-level API predates locations: an item has one `home_location_id` (its warehouse's
default bin), and opening stock, adjustments, counts and PO receipts post to that bucket. That
compatibility layer refuses — rather than guesses — whenever stock has moved somewhere it cannot
see (a count of an item with stock in two places would otherwise overwrite one of them).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import QTY_SCALE, format_qty, whole_units
from app.inventory.reconcile import ReconcileReport, reconcile_organization
from app.models import (
    STOCK_AGGREGATES,
    AuditLog,
    InventoryBalance,
    InventoryDiscrepancy,
    InventoryItem,
    Location,
    OutboxEvent,
    PurchaseOrder,
    Warehouse,
)
from app.schemas import InventoryItemPatch, WarehousePatch
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, ValidationProblem
from platform_core.events import publish_event

EVT_WAREHOUSE_CREATED = "operations.inventory.warehouse.created"
EVT_WAREHOUSE_UPDATED = "operations.inventory.warehouse.updated"
EVT_WAREHOUSE_DELETED = "operations.inventory.warehouse.deleted"
EVT_INVENTORY_CREATED = "operations.inventory.item.created"
EVT_INVENTORY_UPDATED = "operations.inventory.item.updated"
EVT_INVENTORY_DELETED = "operations.inventory.item.deleted"
EVT_STOCK_ADJUSTED = "operations.inventory.stock.adjusted"
EVT_DISCREPANCY_RESOLVED = "operations.inventory.discrepancy.resolved"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _duplicate(field: str, message: str) -> ConflictProblem:
    return ConflictProblem(
        message, errors=[{"field": field, "code": "duplicate", "message": message}]
    )


async def _home_for(session: AsyncSession, org_id: uuid.UUID, warehouse_name: str) -> Location:
    warehouse = await inv_selectors.get_warehouse_by_name(session, org_id, warehouse_name)
    if warehouse is None:
        raise ValidationProblem(f"{warehouse_name!r} does not match an existing warehouse")
    home = await inv_selectors.default_location(session, org_id, warehouse.id)
    if home is None:  # every warehouse gets one at creation; a missing one is a data bug
        raise ConflictProblem(f"{warehouse_name} has no default bin to hold stock")
    return home


async def _flush_item(session: AsyncSession, item: InventoryItem) -> None:
    """Flush a changed item and re-read its stock aggregates. A flush that UPDATEs the row
    expires SQL-expression column properties (`on_hand_milli`), and under asyncio an expired
    attribute cannot lazy-load — so every writer that touches the row reloads them here, once."""
    await session.flush()
    await session.refresh(item, list(STOCK_AGGREGATES))


def _home(item: InventoryItem) -> uuid.UUID:
    if item.home_location_id is None:
        raise ConflictProblem(f"{item.sku} has no warehouse to hold stock; set one first")
    return item.home_location_id


# ------------------------------------------------------------------ warehouses


async def create_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    location: str = "",
    request: Request | None = None,
) -> Warehouse:
    """Create a warehouse (stocking location) and its default bin. `name` is a unique join key too
    — it is what `inventory_items.warehouse` and `purchase_orders.warehouse` carry — so a
    duplicate name is refused exactly like a duplicate code."""
    if await inv_selectors.get_warehouse_by_code(session, ctx.organization_id, code):
        raise _duplicate("code", f"A warehouse coded {code!r} already exists")
    if await inv_selectors.get_warehouse_by_name(session, ctx.organization_id, name):
        raise _duplicate("name", f"A warehouse named {name!r} already exists")

    warehouse = Warehouse(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        location=location,
    )
    session.add(warehouse)
    await session.flush()
    session.add(
        Location(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            warehouse_id=warehouse.id,
            kind="bin",
            code="DEFAULT",
            name="Default bin",
            is_default=True,
        )
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="warehouse.created",
        entity="warehouse",
        entity_id=warehouse.id,
        after={"code": code, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WAREHOUSE_CREATED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "warehouse_id": str(warehouse.id),
            "code": code,
        },
        actor=_actor(ctx),
    )
    return warehouse


async def update_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    payload: WarehousePatch,
    *,
    request: Request | None = None,
) -> Warehouse:
    """Patch `name`/`location`. `code` is immutable — `WarehousePatch` carries no such field.
    A rename cascades to the denormalised `warehouse` string on every live purchase order and
    inventory item in the same transaction, or every existing row keeps the stale name. Stock is
    unaffected: it sits at locations, which a rename does not touch."""
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != warehouse.name:
        existing = await inv_selectors.get_warehouse_by_name(
            session, ctx.organization_id, changes["name"]
        )
        if existing is not None and existing.id != warehouse.id:
            raise _duplicate("name", f"A warehouse named {changes['name']!r} already exists")

    old_name = warehouse.name
    before = {field: getattr(warehouse, field) for field in changes}
    for field, value in changes.items():
        setattr(warehouse, field, value)
    warehouse.updated_by = str(ctx.principal.sub)
    await session.flush()

    if "name" in changes and changes["name"] != old_name:
        new_name = changes["name"]
        await session.execute(
            update(PurchaseOrder)
            .where(
                PurchaseOrder.organization_id == ctx.organization_id,
                PurchaseOrder.warehouse == old_name,
            )
            .values(warehouse=new_name, updated_by=str(ctx.principal.sub))
        )
        await session.execute(
            update(InventoryItem)
            .where(
                InventoryItem.organization_id == ctx.organization_id,
                InventoryItem.warehouse == old_name,
            )
            .values(warehouse=new_name, updated_by=str(ctx.principal.sub))
        )
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="warehouse.updated",
        entity="warehouse",
        entity_id=warehouse.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WAREHOUSE_UPDATED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "warehouse_id": str(warehouse.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return warehouse


async def delete_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a warehouse and its locations. Refused (409) while it still holds any live
    inventory item, any stock or reservation at any of its locations, or names a
    `draft`/`approved` purchase order's destination."""
    org_id = ctx.organization_id
    item_count = await inv_selectors.count_inventory_for_warehouse(session, org_id, warehouse.name)
    po_count = await inv_selectors.count_open_purchase_orders_for_warehouse(
        session, org_id, warehouse.name
    )
    if item_count or po_count:
        raise ConflictProblem(
            f"{warehouse.name} still holds {item_count} inventory item(s) and {po_count} open "
            "purchase order(s); move or clear them first"
        )
    if await inv_selectors.stock_at_warehouse(session, org_id, warehouse.id):
        raise ConflictProblem(f"{warehouse.name} still holds stock; move or clear it first")

    before_deleted_at = warehouse.deleted_at
    now = datetime.now(UTC)
    warehouse.deleted_at = now
    warehouse.updated_by = str(ctx.principal.sub)
    await session.execute(
        update(Location)
        .where(
            Location.organization_id == org_id,
            Location.warehouse_id == warehouse.id,
            Location.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_by=str(ctx.principal.sub))
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="warehouse.deleted",
        entity="warehouse",
        entity_id=warehouse.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": warehouse.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_WAREHOUSE_DELETED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={"organization_id": str(org_id), "warehouse_id": str(warehouse.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ inventory items


async def create_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    sku: str,
    name: str,
    category: str,
    warehouse: str = "",
    on_hand: int = 0,
    reorder_point: int = 0,
    uom: str = "",
    unit_value_minor: int = 0,
    currency: str = "INR",
    stocked_at: date | None = None,
    request: Request | None = None,
) -> InventoryItem:
    """Create an inventory item at its warehouse's default bin. `unit_value_minor` is per-unit
    stock value in integer minor units of `currency`; low-stock is derived, never stored.
    `stocked_at` defaults to today (`InventoryItemOut.updated_at` is a required field).

    `on_hand` — the opening quantity, in whole units — is never written anywhere directly: a
    non-zero one becomes an `opening` move through the ledger, so the item's stock is the sum of
    its ledger from the moment the row exists.
    """
    if await inv_selectors.get_inventory_by_sku(session, ctx.organization_id, sku):
        raise ConflictProblem(f"An inventory item with SKU {sku!r} already exists")
    home = await _home_for(session, ctx.organization_id, warehouse) if warehouse else None

    opening_date = stocked_at or date.today()
    item = InventoryItem(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        sku=sku,
        name=name,
        category=category,
        warehouse=warehouse,
        home_location_id=home.id if home else None,
        reorder_point=reorder_point,
        uom=uom,
        unit_value_minor=unit_value_minor,
        currency=currency.upper(),
        stocked_at=opening_date,
    )
    session.add(item)
    await _flush_item(session, item)

    if on_hand:
        await ledger.post_move(
            session,
            ctx,
            kind="opening",
            lines=[
                MoveLine(
                    item_id=item.id,
                    location_id=_home(item),
                    quantity_milli=on_hand * QTY_SCALE,
                    unit_cost_minor=unit_value_minor,
                )
            ],
            occurred_on=opening_date,
            request=request,
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.created",
        entity="inventory_item",
        entity_id=item.id,
        after={"sku": sku, "warehouse": warehouse, "on_hand": item.on_hand},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_CREATED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "sku": sku,
        },
        actor=_actor(ctx),
    )
    return item


async def update_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    payload: InventoryItemPatch,
    *,
    request: Request | None = None,
) -> InventoryItem:
    """Patch the item's card. `sku`, `currency` and `on_hand` never appear on
    `InventoryItemPatch` — `sku` is the join key a PO line matches on, `currency` is the unit
    the stock value accumulated in, and stock only ever moves through the ledger.

    Changing `warehouse` moves the item's home. Stock in the old home bucket moves with it, as a
    `relocation` move (out of the old bin, into the new) — before ADR-0014 the stock was just a
    number with a warehouse label, and relabelling it moved it. `item` must be locked by the
    caller. Refused while any of that stock is reserved: a reservation is a promise about stock
    at a place.
    """
    changes = payload.model_dump(exclude_unset=True)
    new_home: Location | None = None
    if "warehouse" in changes and changes["warehouse"] != item.warehouse:
        new_home = await _home_for(session, ctx.organization_id, changes["warehouse"])
    elif "warehouse" in changes:
        await _home_for(session, ctx.organization_id, changes["warehouse"])

    before = {field: getattr(item, field) for field in changes}
    old_home = item.home_location_id
    for field, value in changes.items():
        setattr(item, field, value)
    if new_home is not None and new_home.id != old_home:
        item.home_location_id = new_home.id
        if old_home is not None:
            await _relocate_home_stock(session, ctx, item, old_home, new_home.id, request)
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await _flush_item(session, item)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.updated",
        entity="inventory_item",
        entity_id=item.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_UPDATED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return item


async def _relocate_home_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    old_home: uuid.UUID,
    new_home: uuid.UUID,
    request: Request | None,
) -> None:
    balance = await ledger.lock_bucket(
        session, ctx.organization_id, item_id=item.id, location_id=old_home, create=False
    )
    if balance is None or balance.quantity_milli == 0:
        return
    if balance.reserved_milli:
        raise ConflictProblem(
            f"{item.sku} has reserved stock at its current warehouse; release the reservations "
            "before moving it"
        )
    moving = balance.quantity_milli
    await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            MoveLine(item_id=item.id, location_id=old_home, quantity_milli=-moving),
            MoveLine(item_id=item.id, location_id=new_home, quantity_milli=moving),
        ],
        occurred_on=date.today(),
        note=f"Warehouse changed to {item.warehouse}",
        request=request,
    )


async def delete_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete an inventory item. Refused (409) while it holds any stock or reservation
    anywhere — value must never vanish without a move; adjust stock to zero first. Its ledger
    stays, unaffected."""
    held = (
        await session.execute(
            select(
                cast(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0), BigInteger),
                cast(func.coalesce(func.sum(InventoryBalance.reserved_milli), 0), BigInteger),
            ).where(
                InventoryBalance.organization_id == ctx.organization_id,
                InventoryBalance.inventory_item_id == item.id,
            )
        )
    ).one()
    held_milli, reserved_milli = held
    if reserved_milli:
        raise ConflictProblem(
            f"{item.sku} still has {format_qty(reserved_milli)} reserved; release it first"
        )
    if held_milli:
        raise ConflictProblem(
            f"{item.sku} still has {format_qty(held_milli)} on hand; adjust stock to zero first"
        )

    before_deleted_at = item.deleted_at
    item.deleted_at = datetime.now(UTC)
    item.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.deleted",
        entity="inventory_item",
        entity_id=item.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": item.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_DELETED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={"organization_id": str(ctx.organization_id), "inventory_item_id": str(item.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ stock (item-level API)


async def adjust_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    quantity_delta: int | None = None,
    counted_on_hand: int | None = None,
    reason: str | None = None,
    note: str = "",
    occurred_on: date | None = None,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> InventoryItem:
    """Adjust (`quantity_delta`) or count (`counted_on_hand`) one item's stock, in whole units, at
    its home bucket. A count's change is computed under the bucket's lock and recorded even when
    it is zero — a zero-change count is still a fact, and is what moves `stocked_at`.

    `item` must already be locked by the caller. A count is refused while the item holds stock
    anywhere but its home bucket: the whole-item figure it asserts would silently overwrite
    stock it cannot see. A retried request with the same `idempotency_key` changes nothing and
    answers with the item as it stands.
    """
    effective_date = occurred_on or date.today()
    home = _home(item)
    before_on_hand = item.on_hand
    if counted_on_hand is not None:
        if await inv_selectors.stock_outside_home(session, item):
            raise ConflictProblem(
                f"{item.sku} holds stock outside {item.warehouse}'s default bin, so a single "
                "count cannot describe it; count each location instead"
            )
        kind = "count"
        line = MoveLine(
            item_id=item.id, location_id=home, counted_milli=counted_on_hand * QTY_SCALE
        )
    else:
        kind = "adjustment"
        line = MoveLine(
            item_id=item.id, location_id=home, quantity_milli=(quantity_delta or 0) * QTY_SCALE
        )

    posted = await ledger.post_move(
        session,
        ctx,
        kind=kind,
        lines=[line],
        reason=reason,
        note=note,
        occurred_on=effective_date,
        idempotency_key=idempotency_key,
        request=request,
    )
    if posted.replayed:
        return item

    entry = posted.entries[0]
    delta = whole_units(entry.quantity_milli)
    item.stocked_at = effective_date
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await _flush_item(session, item)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.stock_adjusted",
        entity="inventory_item",
        entity_id=item.id,
        before={"on_hand": before_on_hand},
        after={"on_hand": item.on_hand, "delta": delta, "kind": kind, "reason": reason},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_STOCK_ADJUSTED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "sku": item.sku,
            "movement_id": str(entry.id),
            "quantity_delta": delta,
            "on_hand_after": item.on_hand,
            "kind": kind,
        },
        actor=_actor(ctx),
    )
    return item


async def receive_into_home(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    quantity: int,
    purchase_order_id: uuid.UUID,
    occurred_on: date,
    unit_cost_minor: int | None = None,
    request: Request | None = None,
) -> uuid.UUID:
    """Post a purchase-order line's receipt (whole units) into the item's home bucket; returns
    the ledger entry id. `item` must be locked by the caller."""
    posted = await ledger.post_move(
        session,
        ctx,
        kind="receipt",
        lines=[
            MoveLine(
                item_id=item.id,
                location_id=_home(item),
                quantity_milli=quantity * QTY_SCALE,
                unit_cost_minor=unit_cost_minor,
            )
        ],
        reference_type="purchase_order",
        reference_id=purchase_order_id,
        occurred_on=occurred_on,
        request=request,
    )
    item.stocked_at = occurred_on
    await _flush_item(session, item)
    return posted.entries[0].id


# ------------------------------------------------------------------ reconciliation


async def reconcile_now(
    session: AsyncSession, ctx: ScopeContext, *, request: Request | None = None
) -> ReconcileReport:
    """Run the nightly check for the caller's organization, now. Records findings; repairs
    nothing. Audited, because who asked and what they were told is part of the story."""
    report = await reconcile_organization(session, ctx.organization_id)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory.reconciled",
        entity="inventory_reconcile_run",
        entity_id=report.run_id,
        after={
            "buckets_checked": report.buckets_checked,
            "findings": len(report.findings),
            "new_findings": len(report.new),
        },
        request=request,
    )
    return report


async def resolve_discrepancy(
    session: AsyncSession,
    ctx: ScopeContext,
    discrepancy: InventoryDiscrepancy,
    *,
    note: str,
    request: Request | None = None,
) -> InventoryDiscrepancy:
    """Close a discrepancy once a person has dealt with its cause. This records the decision; it
    changes no stock. If the drift is still there, the next reconciliation opens a new one."""
    if discrepancy.resolved_at is not None:
        raise ConflictProblem("That discrepancy is already resolved")
    discrepancy.resolved_at = datetime.now(UTC)
    discrepancy.resolved_by = str(ctx.principal.sub)
    discrepancy.resolution_note = note
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_discrepancy.resolved",
        entity="inventory_discrepancy",
        entity_id=discrepancy.id,
        before={"resolved_at": None},
        after={"resolved_at": discrepancy.resolved_at, "note": note},
        reason=note,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DISCREPANCY_RESOLVED,
        aggregate_type="inventory_discrepancy",
        aggregate_id=discrepancy.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "discrepancy_id": str(discrepancy.id),
            "kind": discrepancy.kind,
            "note": note,
        },
        actor=_actor(ctx),
    )
    return discrepancy
