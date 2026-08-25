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

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    AuditLog,
    InventoryItem,
    OutboxEvent,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
    Warehouse,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `operations.*` because these are the product's own, not the platform's.
EVT_SUPPLIER_CREATED = "operations.procurement.supplier.created"
EVT_WAREHOUSE_CREATED = "operations.inventory.warehouse.created"
EVT_PO_RAISED = "operations.procurement.purchase_order.raised"
EVT_PO_RECEIVED = "operations.procurement.purchase_order.received"
EVT_INVENTORY_CREATED = "operations.inventory.item.created"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


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
    """Create a supplier. `spend_minor` is lifetime spend in integer minor units of `currency`."""
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
        since=since,
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
    """Create a warehouse (stocking location)."""
    if await selectors.get_warehouse_by_code(session, ctx.organization_id, code):
        raise ConflictProblem(f"A warehouse coded {code!r} already exists")

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


async def receive_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    received_date: date | None = None,
    request: Request | None = None,
) -> PurchaseOrder:
    """Mark a purchase order received: move it to `received`, stamp the delivery date and fill in
    each line's `received_qty` to the ordered quantity. Only a draft or approved PO can be
    received — receiving a closed or already-received PO is refused."""
    if po.status not in domain.OPEN_STATUSES:
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be received")

    landed = received_date or datetime.now(UTC).date()
    po.status = "received"
    po.received_date = landed
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)

    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    for line in lines:
        line.received_qty = line.quantity
        line.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.received",
        entity="purchase_order",
        entity_id=po.id,
        after={"number": po.number, "status": "received", "received_date": landed.isoformat()},
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
    units of `currency`; low-stock is derived, never stored."""
    if await selectors.get_inventory_by_sku(session, ctx.organization_id, sku):
        raise ConflictProblem(f"An inventory item with SKU {sku!r} already exists")

    item = InventoryItem(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        sku=sku,
        name=name,
        category=category,
        warehouse=warehouse,
        on_hand=on_hand,
        reorder_point=reorder_point,
        uom=uom,
        unit_value_minor=unit_value_minor,
        currency=currency.upper(),
        stocked_at=stocked_at,
    )
    session.add(item)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.created",
        entity="inventory_item",
        entity_id=item.id,
        after={"sku": sku, "warehouse": warehouse, "on_hand": on_hand},
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
