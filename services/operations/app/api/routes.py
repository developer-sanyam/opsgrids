"""HTTP surface for the operations service.

Mounted behind the gateway's `/api/v1/operations` prefix, so every route path here begins
`/operations/...`. The read endpoints are the priority — they are what flips the Operations SPA
from fixtures to live — and each returns exactly the shape
`frontend/product-operations/src/api/types.ts` declares.

Every route declares the permission it needs and is guarded by `require_product("operations")` as
defence in depth behind the gateway's own entitlement gate. Reads go through `selectors`, writes
through `services`; a router never builds its own query. Every route takes the tenant-bound
session and opens no transaction of its own — the chokepoint owns it.

Object-level scope is re-applied on every route that names a single row: filtering the list and
forgetting the detail is the named vulnerability, so a caller who can only see the POs they raised
is refused another's by id.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, PurchaseOrderLine
from app.schemas import (
    InventoryItemOut,
    OverviewOut,
    PurchaseOrderOut,
    ServiceInfo,
    SupplierOut,
    WarehouseOut,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the operations scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers operations scope questions.
scope.register()

router = APIRouter(tags=["operations"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `operations` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("operations"))

PoRead = Annotated[ScopeContext, Depends(require("operations.procurement.purchase_order.read"))]
PoWrite = Annotated[ScopeContext, Depends(require("operations.procurement.purchase_order.write"))]
SupplierRead = Annotated[ScopeContext, Depends(require("operations.procurement.supplier.read"))]
InventoryRead = Annotated[ScopeContext, Depends(require("operations.inventory.item.read"))]
WarehouseRead = Annotated[ScopeContext, Depends(require("operations.inventory.warehouse.read"))]


@router.get("/operations/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ suppliers


@router.get(
    "/operations/suppliers",
    response_model=Page[SupplierOut],
    summary="The supplier directory",
    dependencies=[Entitled],
)
async def list_suppliers(
    session: SessionDep,
    ctx: SupplierRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Code, name, contact or location")] = None,
    category: Annotated[str | None, Query()] = None,
    supplier_status: Annotated[str | None, Query(alias="status")] = None,
    sort: Annotated[str | None, Query(description="spend | on_time | lead_time | name")] = None,
) -> Page[SupplierOut]:
    stmt = selectors.suppliers_stmt(ctx, q=q, category=category, status=supplier_status, sort=sort)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.supplier_out(s) for s in rows], total=total, params=params)


@router.get(
    "/operations/suppliers/{supplier_id}",
    response_model=SupplierOut,
    summary="One supplier",
    dependencies=[Entitled],
)
async def read_supplier(
    supplier_id: uuid.UUID, session: SessionDep, ctx: SupplierRead
) -> SupplierOut:
    supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    return selectors.supplier_out(supplier)


# ------------------------------------------------------------------ purchase orders


@router.get(
    "/operations/purchase-orders",
    response_model=Page[PurchaseOrderOut],
    summary="Purchase orders, with their line items",
    dependencies=[Entitled],
)
async def list_purchase_orders(
    session: SessionDep,
    ctx: PoRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Number, supplier, raiser or warehouse")] = None,
    po_status: Annotated[str | None, Query(alias="status")] = None,
    category: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query(description="expected | created | number")] = None,
) -> Page[PurchaseOrderOut]:
    stmt = selectors.purchase_orders_stmt(ctx, q=q, status=po_status, category=category, sort=sort)
    rows, total = await paginate(session, stmt, params)
    by_po = await selectors.lines_map(session, ctx.organization_id, [r.id for r in rows])
    items = [selectors.purchase_order_out(po, lines=by_po.get(po.id, [])) for po in rows]
    return Page.build(items, total=total, params=params)


@router.get(
    "/operations/purchase-orders/{po_id}",
    response_model=PurchaseOrderOut,
    summary="One purchase order, with its line items",
    dependencies=[Entitled],
)
async def read_purchase_order(
    po_id: uuid.UUID, session: SessionDep, ctx: PoRead
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    return selectors.purchase_order_out(po, lines=lines)


@router.post(
    "/operations/purchase-orders/{po_id}/receive",
    response_model=PurchaseOrderOut,
    summary="Receive a purchase order (marks it received and fills received quantities)",
    dependencies=[Entitled],
)
async def receive_purchase_order(
    po_id: uuid.UUID, session: SessionDep, ctx: PoWrite, request: Request
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    await services.receive_purchase_order(session, ctx, po, request=request)
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    return selectors.purchase_order_out(po, lines=lines)


# ------------------------------------------------------------------ inventory


@router.get(
    "/operations/inventory",
    response_model=Page[InventoryItemOut],
    summary="Inventory on hand (low-stock is derived, and labelled — never colour alone)",
    dependencies=[Entitled],
)
async def list_inventory(
    session: SessionDep,
    ctx: InventoryRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="SKU, name or warehouse")] = None,
    category: Annotated[str | None, Query()] = None,
    warehouse: Annotated[str | None, Query()] = None,
    low_stock: Annotated[
        bool | None, Query(description="Only items at/under reorder point")
    ] = None,
    sort: Annotated[str | None, Query(description="on_hand | value | sku")] = None,
) -> Page[InventoryItemOut]:
    stmt = selectors.inventory_stmt(
        ctx, q=q, category=category, warehouse=warehouse, low_stock=low_stock, sort=sort
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.inventory_out(i) for i in rows], total=total, params=params)


# ------------------------------------------------------------------ warehouses (reference)


@router.get(
    "/operations/warehouses",
    response_model=Page[WarehouseOut],
    summary="The org's warehouses",
    dependencies=[Entitled],
)
async def list_warehouses(
    session: SessionDep, ctx: WarehouseRead, params: PageQuery
) -> Page[WarehouseOut]:
    stmt = selectors.warehouses_stmt(ctx.organization_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.warehouse_out(w) for w in rows], total=total, params=params)


# ------------------------------------------------------------------ overview


@router.get(
    "/operations/overview",
    response_model=OverviewOut,
    summary="Procurement & inventory overview rollups",
    dependencies=[Entitled],
)
async def overview(session: SessionDep, ctx: PoRead) -> OverviewOut:
    pos = await selectors.all_purchase_orders(session, ctx)
    inventory = await selectors.all_inventory(session, ctx.organization_id)
    suppliers = await selectors.all_suppliers(session, ctx.organization_id)
    lines = await selectors.all_lines(session, ctx.organization_id)

    # PO id → its derived total (minor units), from the lines that foot to it.
    lines_by_po: dict[uuid.UUID, list[PurchaseOrderLine]] = {}
    for line in lines:
        lines_by_po.setdefault(line.purchase_order_id, []).append(line)
    totals = {str(po.id): domain.po_total_minor(lines_by_po.get(po.id, [])) for po in pos}

    currency = suppliers[0].currency if suppliers else "INR"
    today = date.today()

    return OverviewOut(
        open_pos=domain.open_po_count(pos),
        inventory_value_minor=domain.inventory_value_minor(inventory),
        low_stock_count=domain.low_stock_count(inventory),
        on_time_pct=domain.weighted_on_time_pct(suppliers),
        currency=currency,
        spend_trend=domain.spend_trend(pos, totals),
        spend_by_category=domain.spend_by_category(pos, totals),
        po_status_mix=domain.po_status_mix(pos),
        top_suppliers=domain.top_suppliers(suppliers),
        attention=domain.attention_items(pos, inventory, today),
    )
