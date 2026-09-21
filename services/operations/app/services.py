"""Writes for the operations service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000). Money is integer minor
units throughout: a purchase-order line's `amount_minor` is computed as `quantity *
unit_price_minor` at write time, so the lines foot to the derived PO total by construction.

These writers serve both the HTTP surface (`create_purchase_order`, `receive_purchase_order`) and
the demo seed, which calls the same functions with explicit numbers and dates so the live screens
mirror the fixtures the user already approved.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    AuditLog,
    InventoryItem,
    OutboxEvent,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMovement,
    Supplier,
    Warehouse,
)
from app.schemas import (
    InventoryItemPatch,
    PurchaseOrderPatch,
    SupplierPatch,
    WarehousePatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

# Event names. `operations.*` because these are the product's own, not the platform's.
EVT_SUPPLIER_CREATED = "operations.procurement.supplier.created"
EVT_SUPPLIER_UPDATED = "operations.procurement.supplier.updated"
EVT_SUPPLIER_DELETED = "operations.procurement.supplier.deleted"
EVT_WAREHOUSE_CREATED = "operations.inventory.warehouse.created"
EVT_WAREHOUSE_UPDATED = "operations.inventory.warehouse.updated"
EVT_WAREHOUSE_DELETED = "operations.inventory.warehouse.deleted"
EVT_PO_RAISED = "operations.procurement.purchase_order.raised"
EVT_PO_UPDATED = "operations.procurement.purchase_order.updated"
EVT_PO_DELETED = "operations.procurement.purchase_order.deleted"
EVT_PO_APPROVED = "operations.procurement.purchase_order.approved"
EVT_PO_RECEIVED = "operations.procurement.purchase_order.received"
EVT_PO_CLOSED = "operations.procurement.purchase_order.closed"
EVT_PO_CANCELLED = "operations.procurement.purchase_order.cancelled"
EVT_INVENTORY_CREATED = "operations.inventory.item.created"
EVT_INVENTORY_UPDATED = "operations.inventory.item.updated"
EVT_INVENTORY_DELETED = "operations.inventory.item.deleted"
EVT_STOCK_ADJUSTED = "operations.inventory.stock.adjusted"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def _post_stock_movement(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    kind: str,
    quantity_delta: int,
    reason: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    note: str = "",
    occurred_on: date,
) -> StockMovement:
    """Append one stock movement and update the item's cached `on_hand` balance — the **only**
    place either happens, so `on_hand` can never drift from the sum of its movements.

    The caller is responsible for having locked `item` (`selectors.get_inventory_item(...,
    for_update=True)` or the equivalent) before this runs, so two concurrent adjustments on the
    same item serialise rather than lose one's update. 409s rather than writes anything when the
    result would go negative — the `on_hand_after >= 0` CHECK is a backstop, not the primary
    guard, so the caller gets a clean `ConflictProblem` instead of a raw `IntegrityError`.
    """
    new_on_hand = item.on_hand + quantity_delta
    if new_on_hand < 0:
        raise ConflictProblem(
            f"Adjusting {item.sku} by {quantity_delta} would take on-hand below zero "
            f"({item.on_hand} on hand)"
        )
    movement = StockMovement(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        inventory_item_id=item.id,
        kind=kind,
        reason=reason,
        quantity_delta=quantity_delta,
        on_hand_after=new_on_hand,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        occurred_on=occurred_on,
    )
    session.add(movement)
    item.on_hand = new_on_hand
    item.stocked_at = occurred_on
    await session.flush()
    return movement


async def _bump_supplier(
    session: AsyncSession,
    org_id: uuid.UUID,
    supplier_id: uuid.UUID,
    *,
    open_pos: int = 0,
    spend_minor: int = 0,
) -> Supplier | None:
    """Apply an incremental delta to a supplier's rollups, under a row lock so two concurrent PO
    transitions cannot lose one's update. `open_pos` floors at zero: the seeded figures are
    lifetime counters, not values this delta arithmetic derives from scratch, so a transition
    replayed against a stale start must not go negative. None (a no-op) if the supplier is gone —
    it cannot be, since a PO's `supplier_id` is `ON DELETE RESTRICT`, but a caller must not raise
    over a rollup on the way out of a transition that has already committed its main effect.
    """
    if not open_pos and not spend_minor:
        return await selectors.get_supplier(session, org_id, supplier_id)
    stmt = (
        select(Supplier)
        .where(Supplier.id == supplier_id, Supplier.organization_id == org_id)
        .with_for_update()
    )
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if supplier is None:
        return None
    if open_pos:
        supplier.open_pos = max(0, supplier.open_pos + open_pos)
    if spend_minor:
        supplier.spend_minor = supplier.spend_minor + spend_minor
    await session.flush()
    return supplier


# ------------------------------------------------------------------ suppliers


async def create_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    category: str,
    status: str = "active",
    lead_time_days: int = 0,
    on_time_pct: float | Decimal = 0,
    spend_minor: int = 0,
    currency: str = "INR",
    open_pos: int = 0,
    contact_name: str = "",
    contact_email: str = "",
    location: str = "",
    since: date | None = None,
    request: Request | None = None,
) -> Supplier:
    """Create a supplier. `spend_minor` is lifetime spend in integer minor units of `currency`.
    `since` defaults to today rather than staying null — `SupplierOut.since` is a required field
    over a nullable column, and a row created with none used to be an unconditional 500."""
    if await selectors.get_supplier_by_code(session, ctx.organization_id, code):
        raise ConflictProblem(f"A supplier coded {code!r} already exists")

    supplier = Supplier(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        category=category,
        status=status,
        lead_time_days=lead_time_days,
        on_time_pct=Decimal(str(on_time_pct)),
        spend_minor=spend_minor,
        currency=currency.upper(),
        open_pos=open_pos,
        contact_name=contact_name,
        contact_email=contact_email,
        location=location,
        since=since or date.today(),
    )
    session.add(supplier)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.created",
        entity="supplier",
        entity_id=supplier.id,
        after={"code": code, "category": category, "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_CREATED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "supplier_id": str(supplier.id),
            "code": code,
            "category": category,
        },
        actor=_actor(ctx),
    )
    return supplier


async def update_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    supplier: Supplier,
    payload: SupplierPatch,
    *,
    request: Request | None = None,
) -> Supplier:
    """Patch the mutable supplier fields — including `status`, so this is also how a supplier
    moves `active` <-> `on_hold` <-> `pending`. `code` and `currency` cannot appear here:
    `SupplierPatch` has no such fields, so they are never in `changes`."""
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(supplier, field) for field in changes}
    for field, value in changes.items():
        setattr(supplier, field, value)
    supplier.updated_by = str(ctx.principal.sub)
    await session.flush()

    if "name" in changes:
        # `purchase_orders.supplier_name` is a denormalised copy, not a join — a rename must
        # cascade in the same transaction or every existing PO row keeps the stale name.
        await session.execute(
            update(PurchaseOrder)
            .where(
                PurchaseOrder.organization_id == ctx.organization_id,
                PurchaseOrder.supplier_id == supplier.id,
            )
            .values(supplier_name=changes["name"], updated_by=str(ctx.principal.sub))
        )
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.updated",
        entity="supplier",
        entity_id=supplier.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_UPDATED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "supplier_id": str(supplier.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return supplier


async def delete_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    supplier: Supplier,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a supplier. Refused (409) while it still has a `draft`/`approved` purchase
    order — put it `on_hold` instead of deleting a vendor mid-order. Historic (received/closed/
    cancelled) POs keep their denormalised `supplier_name` and simply 404 on the supplier link."""
    open_count = await selectors.count_open_purchase_orders_for_supplier(
        session, ctx.organization_id, supplier.id
    )
    if open_count:
        raise ConflictProblem(
            f"{supplier.name} has {open_count} open purchase order(s); put it on hold instead "
            "of deleting it"
        )

    before_deleted_at = supplier.deleted_at
    supplier.deleted_at = datetime.now(UTC)
    supplier.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.deleted",
        entity="supplier",
        entity_id=supplier.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": supplier.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_DELETED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={"organization_id": str(ctx.organization_id), "supplier_id": str(supplier.id)},
        actor=_actor(ctx),
    )


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
    """Create a warehouse (stocking location). `name` is a unique join key too — it is what
    `inventory_items.warehouse` and `purchase_orders.warehouse` carry — so a duplicate name is
    refused exactly like a duplicate code."""
    if await selectors.get_warehouse_by_code(session, ctx.organization_id, code):
        raise ConflictProblem(f"A warehouse coded {code!r} already exists")
    if await selectors.get_warehouse_by_name(session, ctx.organization_id, name):
        raise ConflictProblem(f"A warehouse named {name!r} already exists")

    warehouse = Warehouse(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        location=location,
    )
    session.add(warehouse)
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
    inventory item in the same transaction, or every existing row keeps the stale name."""
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != warehouse.name:
        existing = await selectors.get_warehouse_by_name(
            session, ctx.organization_id, changes["name"]
        )
        if existing is not None and existing.id != warehouse.id:
            raise ConflictProblem(f"A warehouse named {changes['name']!r} already exists")

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
    """Soft-delete a warehouse. Refused (409) while it still holds any live inventory item or
    names a `draft`/`approved` purchase order's destination."""
    item_count = await selectors.count_inventory_for_warehouse(
        session, ctx.organization_id, warehouse.name
    )
    po_count = await selectors.count_open_purchase_orders_for_warehouse(
        session, ctx.organization_id, warehouse.name
    )
    if item_count or po_count:
        raise ConflictProblem(
            f"{warehouse.name} still holds {item_count} inventory item(s) and {po_count} open "
            "purchase order(s); move or clear them first"
        )

    before_deleted_at = warehouse.deleted_at
    warehouse.deleted_at = datetime.now(UTC)
    warehouse.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
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
        organization_id=ctx.organization_id,
        event_type=EVT_WAREHOUSE_DELETED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={"organization_id": str(ctx.organization_id), "warehouse_id": str(warehouse.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ purchase orders


async def create_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    number: str,
    supplier_id: uuid.UUID | None = None,
    supplier_code: str | None = None,
    status: str = "draft",
    warehouse: str = "",
    raised_by: str = "",
    raised_by_id: uuid.UUID | None = None,
    currency: str = "INR",
    created_date: date,
    expected_date: date,
    received_date: date | None = None,
    notes: str | None = None,
    category: str | None = None,
    lines: Sequence[Mapping[str, Any]] = (),
    request: Request | None = None,
) -> PurchaseOrder:
    """Raise a purchase order and its lines.

    The supplier may be named by `supplier_id` or by `supplier_code` (the seed uses the code);
    the supplier's name and category are denormalised onto the PO. Each line's `amount_minor` is
    computed as `quantity * unit_price_minor`, so the lines foot to the derived PO total.

    A line mapping is `{sku, description, quantity, uom, unit_price_minor, received_qty}`.
    """
    if await selectors.get_purchase_order_by_number(session, ctx.organization_id, number):
        raise ConflictProblem(f"A purchase order numbered {number!r} already exists")

    supplier: Supplier | None = None
    if supplier_id is not None:
        supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    elif supplier_code is not None:
        supplier = await selectors.get_supplier_by_code(session, ctx.organization_id, supplier_code)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")

    po = PurchaseOrder(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        category=category or supplier.category,
        status=status,
        currency=currency.upper(),
        warehouse=warehouse,
        raised_by=raised_by,
        raised_by_id=raised_by_id,
        created_date=created_date,
        expected_date=expected_date,
        received_date=received_date,
        notes=notes,
    )
    session.add(po)
    await session.flush()

    for spec in lines:
        quantity = int(spec["quantity"])
        unit_price_minor = int(spec["unit_price_minor"])
        session.add(
            PurchaseOrderLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                purchase_order_id=po.id,
                sku=spec["sku"],
                description=spec.get("description", ""),
                quantity=quantity,
                uom=spec.get("uom", ""),
                unit_price_minor=unit_price_minor,
                amount_minor=domain.line_amount_minor(quantity, unit_price_minor),
                received_qty=int(spec.get("received_qty", 0)),
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.raised",
        entity="purchase_order",
        entity_id=po.id,
        after={"number": number, "supplier_id": str(supplier.id), "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_RAISED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": number,
            "supplier_id": str(supplier.id),
            "status": status,
        },
        actor=_actor(ctx),
    )
    return po


async def next_po_number(session: AsyncSession, ctx: ScopeContext) -> str:
    """Race-safe server-assigned `PO-{yyyy}-{seq:04d}`, serialised per organization per year.

    `pg_advisory_xact_lock` on a key derived from the org id blocks a second concurrent create
    until the first commits (or rolls back), so two requests racing for the same org never
    compute the same next sequence — the lock is transaction-scoped and releases automatically.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{ctx.organization_id}:po_number"},
    )
    year = date.today().year
    prefix = f"PO-{year}-"
    # Never filters `deleted_at`: a soft-deleted or cancelled PO's number stays reserved
    # (`uq_purchase_orders_org_number` is intentionally not partial — a number is never reused).
    stmt = select(func.max(PurchaseOrder.number)).where(
        PurchaseOrder.organization_id == ctx.organization_id,
        PurchaseOrder.number.like(f"{prefix}%"),
    )
    current_max = (await session.execute(stmt)).scalar_one_or_none()
    seq = 1
    if current_max:
        try:
            seq = int(current_max.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


async def raise_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    supplier_id: uuid.UUID,
    warehouse: str,
    expected_date: date,
    currency: str | None = None,
    notes: str | None = None,
    raised_by: str = "",
    lines: Sequence[Mapping[str, Any]],
    request: Request | None = None,
) -> PurchaseOrder:
    """The HTTP creation path: `POST /purchase-orders`. Wraps `create_purchase_order` with the
    things a client must never control — the server-assigned number, the supplier lookup and its
    `active` gate, and the `open_pos` rollup. Kept separate from `create_purchase_order` itself
    because the demo seed also calls that writer directly with its own literal historical
    `open_pos` figures, and must not have this rollup applied a second time on top of them."""
    supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    if supplier.status != "active":
        raise ConflictProblem(
            f"Supplier {supplier.code} is {supplier.status} and cannot receive a new purchase order"
        )
    if await selectors.get_warehouse_by_name(session, ctx.organization_id, warehouse) is None:
        raise ValidationProblem(f"{warehouse!r} does not match an existing warehouse")

    number = await next_po_number(session, ctx)
    po = await create_purchase_order(
        session,
        ctx,
        number=number,
        supplier_id=supplier.id,
        status="draft",
        warehouse=warehouse,
        raised_by=raised_by,
        raised_by_id=ctx.subject_ref,
        currency=(currency or supplier.currency),
        created_date=date.today(),
        expected_date=expected_date,
        category=supplier.category,
        notes=notes,
        lines=lines,
        request=request,
    )
    await _bump_supplier(session, ctx.organization_id, supplier.id, open_pos=1)
    return po


async def update_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    payload: PurchaseOrderPatch,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """Edit a **draft** purchase order. `status` never appears on `PurchaseOrderPatch` — every
    transition goes through its own endpoint. When `lines` is present it fully replaces the
    order's lines in this transaction (drafts only, so nothing has been received against them).
    Changing `supplier_id` re-copies `supplier_name`/`category` and moves `open_pos` between the
    two suppliers."""
    if po.status != "draft":
        raise ConflictProblem(
            f"Purchase order {po.number} is {po.status}; only a draft can be edited"
        )

    changes = payload.model_dump(exclude_unset=True)
    lines_payload = changes.pop("lines", None)

    old_supplier_id = po.supplier_id
    new_supplier: Supplier | None = None
    if "supplier_id" in changes and changes["supplier_id"] != po.supplier_id:
        new_supplier = await selectors.get_supplier(
            session, ctx.organization_id, changes["supplier_id"]
        )
        if new_supplier is None:
            raise NotFoundProblem("That supplier is not part of this organization")
        if new_supplier.status != "active":
            raise ConflictProblem(
                f"Supplier {new_supplier.code} is {new_supplier.status} and cannot be assigned "
                "a purchase order"
            )

    if "warehouse" in changes and (
        await selectors.get_warehouse_by_name(session, ctx.organization_id, changes["warehouse"])
        is None
    ):
        raise ValidationProblem(f"{changes['warehouse']!r} does not match an existing warehouse")

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()

    before: dict[str, Any] = {field: getattr(po, field) for field in changes}
    for field, value in changes.items():
        setattr(po, field, value)
    if new_supplier is not None:
        # `changes["supplier_id"]` already set `po.supplier_id` above; the denormalised name and
        # category are not on `PurchaseOrderPatch`, so they are re-copied here explicitly.
        po.supplier_name = new_supplier.name
        po.category = new_supplier.category

    lines_before: list[dict[str, Any]] | None = None
    lines_after: list[dict[str, Any]] | None = None
    if lines_payload is not None:
        existing_lines = await selectors.lines_for(session, ctx.organization_id, po.id)
        lines_before = [
            {"sku": line.sku, "quantity": line.quantity, "unit_price_minor": line.unit_price_minor}
            for line in existing_lines
        ]
        for line in existing_lines:
            await session.delete(line)
        await session.flush()

        lines_after = []
        for spec in lines_payload:
            quantity = int(spec["quantity"])
            unit_price_minor = int(spec["unit_price_minor"])
            session.add(
                PurchaseOrderLine(
                    organization_id=ctx.organization_id,
                    created_by=str(ctx.principal.sub),
                    purchase_order_id=po.id,
                    sku=spec["sku"],
                    description=spec.get("description", ""),
                    quantity=quantity,
                    uom=spec.get("uom", ""),
                    unit_price_minor=unit_price_minor,
                    amount_minor=domain.line_amount_minor(quantity, unit_price_minor),
                    received_qty=0,
                )
            )
            lines_after.append(
                {"sku": spec["sku"], "quantity": quantity, "unit_price_minor": unit_price_minor}
            )

    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    if new_supplier is not None:
        await _bump_supplier(session, ctx.organization_id, old_supplier_id, open_pos=-1)
        await _bump_supplier(session, ctx.organization_id, new_supplier.id, open_pos=1)

    audit_before = dict(before)
    audit_after = dict(changes)
    if lines_before is not None:
        audit_before["lines"] = lines_before
        audit_after["lines"] = lines_after

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.updated",
        entity="purchase_order",
        entity_id=po.id,
        before=audit_before,
        after=audit_after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_UPDATED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "changed": sorted(set(changes) | ({"lines"} if lines_payload is not None else set())),
        },
        actor=_actor(ctx),
    )
    return po


async def delete_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a **draft** purchase order. An approved-or-later PO is a commitment; the path
    off it is cancel, not delete."""
    if po.status != "draft":
        raise ConflictProblem(
            f"Purchase order {po.number} is {po.status}; only a draft can be deleted — cancel it "
            "instead"
        )

    before_deleted_at = po.deleted_at
    po.deleted_at = datetime.now(UTC)
    po.updated_by = str(ctx.principal.sub)
    await session.flush()

    await _bump_supplier(session, ctx.organization_id, po.supplier_id, open_pos=-1)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.deleted",
        entity="purchase_order",
        entity_id=po.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": po.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_DELETED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={"organization_id": str(ctx.organization_id), "purchase_order_id": str(po.id)},
        actor=_actor(ctx),
    )


async def approve_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """`draft -> approved`. Bumps the supplier's lifetime `spend_minor` by the PO's derived
    total — only when the PO's currency matches the supplier's; a mismatch is skipped and noted
    in the audit row rather than silently mixing currencies in one running total."""
    if not domain.can_transition_po(po.status, "approved"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be approved")

    po.status = "approved"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    total = domain.po_total_minor(lines)
    supplier = await selectors.get_supplier(session, ctx.organization_id, po.supplier_id)
    spend_applied = supplier is not None and supplier.currency == po.currency
    if spend_applied:
        await _bump_supplier(session, ctx.organization_id, po.supplier_id, spend_minor=total)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.approved",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "approved", "spend_applied": spend_applied},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_APPROVED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "spend_applied": spend_applied,
        },
        actor=_actor(ctx),
    )
    return po


async def receive_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    received_date: date | None = None,
    request: Request | None = None,
) -> PurchaseOrder:
    """Mark a purchase order received: move it to `received`, stamp the delivery date, fill in
    each line's `received_qty` to the ordered quantity, and post the stock. Only a draft or
    approved PO can be received — receiving a closed, already-received or cancelled PO is
    refused.

    Each line whose `sku` matches a live inventory item in the org gets a `receipt` movement of
    `+quantity`, referencing this PO. A line with no matching SKU is a service/logistics line,
    not an error — it posts nothing, and its SKU is listed in `unmatched_skus` on the audit row
    and the event payload so that is visible rather than silent.
    """
    if not domain.can_transition_po(po.status, "received"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be received")

    was_draft = po.status == "draft"
    landed = received_date or datetime.now(UTC).date()
    po.status = "received"
    po.received_date = landed
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)

    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    unmatched_skus: list[str] = []
    matched_lines: list[dict[str, Any]] = []
    for line in lines:
        line.received_qty = line.quantity
        line.updated_by = str(ctx.principal.sub)
        item = await selectors.get_inventory_by_sku(
            session, ctx.organization_id, line.sku, for_update=True
        )
        if item is None:
            unmatched_skus.append(line.sku)
            continue
        movement = await _post_stock_movement(
            session,
            ctx,
            item,
            kind="receipt",
            quantity_delta=line.quantity,
            reference_type="purchase_order",
            reference_id=po.id,
            occurred_on=landed,
        )
        matched_lines.append(
            {"sku": line.sku, "movement_id": str(movement.id), "quantity": line.quantity}
        )
    await session.flush()

    total = domain.po_total_minor(lines)
    supplier = await selectors.get_supplier(session, ctx.organization_id, po.supplier_id)
    # A PO approved earlier already had its spend committed at approve time; only a PO received
    # straight from `draft` (approval skipped) commits it here, and only in the PO's own currency.
    spend_delta = (
        total if was_draft and supplier is not None and supplier.currency == po.currency else 0
    )
    await _bump_supplier(
        session, ctx.organization_id, po.supplier_id, open_pos=-1, spend_minor=spend_delta
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.received",
        entity="purchase_order",
        entity_id=po.id,
        after={
            "number": po.number,
            "status": "received",
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched_skus,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_RECEIVED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched_skus,
            "matched_lines": matched_lines,
        },
        actor=_actor(ctx),
    )
    return po


async def close_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """`received -> closed`. The terminal, ordinary end of the lifecycle — nothing else changes."""
    if not domain.can_transition_po(po.status, "closed"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be closed")

    po.status = "closed"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.closed",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "closed"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_CLOSED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
        },
        actor=_actor(ctx),
    )
    return po


async def cancel_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    reason: str,
    request: Request | None = None,
) -> PurchaseOrder:
    """`draft|approved -> cancelled`. The terminal, unordinary end: an approved PO that will
    never arrive must not stay "open" forever, inflating `open_pos` and the late-delivery
    attention queue. Reverses the `approve` spend commitment if the PO had made it; a PO
    cancelled straight from `draft` never committed spend, so there is nothing to reverse."""
    if not domain.can_transition_po(po.status, "cancelled"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be cancelled")

    was_approved = po.status == "approved"
    po.status = "cancelled"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    total = domain.po_total_minor(await selectors.lines_for(session, ctx.organization_id, po.id))
    supplier = await selectors.get_supplier(session, ctx.organization_id, po.supplier_id)
    spend_reversed = was_approved and supplier is not None and supplier.currency == po.currency
    spend_delta = -total if spend_reversed else 0
    await _bump_supplier(
        session, ctx.organization_id, po.supplier_id, open_pos=-1, spend_minor=spend_delta
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.cancelled",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "cancelled", "reason": reason, "spend_reversed": spend_reversed},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_CANCELLED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "reason": reason,
        },
        actor=_actor(ctx),
    )
    return po


# ------------------------------------------------------------------ inventory


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
    """Create an inventory item. `unit_value_minor` is per-unit stock value in integer minor
    units of `currency`; low-stock is derived, never stored. `stocked_at` defaults to today
    rather than staying null (`InventoryItemOut.updated_at` is a required field over a nullable
    column — a row created with neither used to be an unconditional 500).

    `on_hand` — the seed's existing keyword for the opening quantity — is never written as a
    bare column: the row starts at zero and, if non-zero, an `opening` movement through
    `_post_stock_movement` brings it up, so `on_hand` is the sum of its movements from the
    moment the row exists rather than overwritten-in-place state.
    """
    if await selectors.get_inventory_by_sku(session, ctx.organization_id, sku):
        raise ConflictProblem(f"An inventory item with SKU {sku!r} already exists")

    opening_date = stocked_at or date.today()
    item = InventoryItem(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        sku=sku,
        name=name,
        category=category,
        warehouse=warehouse,
        on_hand=0,
        reorder_point=reorder_point,
        uom=uom,
        unit_value_minor=unit_value_minor,
        currency=currency.upper(),
        stocked_at=opening_date,
    )
    session.add(item)
    await session.flush()

    if on_hand:
        await _post_stock_movement(
            session,
            ctx,
            item,
            kind="opening",
            quantity_delta=on_hand,
            occurred_on=opening_date,
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
    the stock value accumulated in, and `on_hand` may only move through a movement."""
    changes = payload.model_dump(exclude_unset=True)
    if "warehouse" in changes and (
        await selectors.get_warehouse_by_name(session, ctx.organization_id, changes["warehouse"])
        is None
    ):
        raise ValidationProblem(f"{changes['warehouse']!r} does not match an existing warehouse")

    before = {field: getattr(item, field) for field in changes}
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await session.flush()

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


async def delete_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete an inventory item. Refused (409) while `on_hand != 0` — value must never
    vanish without a movement; adjust stock to zero first. Movements stay, unaffected."""
    if item.on_hand != 0:
        raise ConflictProblem(
            f"{item.sku} still has {item.on_hand} on hand; adjust stock to zero first"
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
    request: Request | None = None,
) -> InventoryItem:
    """Adjust (`quantity_delta`) or count (`counted_on_hand`) one item's stock. A count's delta
    is `counted_on_hand - on_hand`, recorded even when it is zero — a zero-delta count is still a
    fact, and is what moves `stocked_at`. `item` must already be locked by the caller
    (`selectors.get_inventory_item(..., for_update=True)`), and `_post_stock_movement` answers
    409 rather than writing anything if the result would go negative.
    """
    effective_date = occurred_on or date.today()
    before_on_hand = item.on_hand
    if counted_on_hand is not None:
        kind = "count"
        delta = counted_on_hand - item.on_hand
    else:
        kind = "adjustment"
        delta = quantity_delta if quantity_delta is not None else 0

    movement = await _post_stock_movement(
        session,
        ctx,
        item,
        kind=kind,
        quantity_delta=delta,
        reason=reason,
        note=note,
        occurred_on=effective_date,
    )
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await session.flush()

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
            "movement_id": str(movement.id),
            "quantity_delta": delta,
            "on_hand_after": item.on_hand,
            "kind": kind,
        },
        actor=_actor(ctx),
    )
    return item
