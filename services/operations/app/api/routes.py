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

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.inventory import ledger as inv_ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.quantity import QuantityError, parse_qty
from app.models import SCHEMA, PurchaseOrderLine
from app.schemas import (
    CancelIn,
    DiscrepancyResolveIn,
    InventoryDiscrepancyOut,
    InventoryItemIn,
    InventoryItemOut,
    InventoryItemPatch,
    InventoryReservationOut,
    ItemAttributeIn,
    ItemAttributeOut,
    ItemCategoryIn,
    ItemCategoryOut,
    ItemCategoryPatch,
    ItemVariantIn,
    ItemVariantOut,
    ItemVariantPatch,
    LocationIn,
    LocationOut,
    LocationPatch,
    OverviewOut,
    PurchaseOrderIn,
    PurchaseOrderOut,
    PurchaseOrderPatch,
    PurchaseOrderReceiptOut,
    ReceiveIn,
    ReconcileOut,
    ReservationReleaseIn,
    ServiceInfo,
    StockAdjustmentIn,
    StockBalanceOut,
    StockMovementOut,
    SupplierIn,
    SupplierOut,
    SupplierPatch,
    UomOut,
    WarehouseIn,
    WarehouseOut,
    WarehousePatch,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the operations scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers operations scope questions.
scope.register()

router = APIRouter(tags=["operations"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `operations` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("operations"))

PoRead = Annotated[ScopeContext, Depends(require("operations.procurement.purchase_order.read"))]
PoWrite = Annotated[ScopeContext, Depends(require("operations.procurement.purchase_order.write"))]
#: Approving is a commitment step distinct from `purchase_order.write` (which `OPERATIONS_USER`
#: holds at OWN) — floored at ORG so a buyer can never approve their own PO. It is also the
#: prerequisite gate before a PO can be received: `domain.PO_TRANSITIONS` has no `draft ->
#: received` edge, so a buyer holding only `write:OWN` cannot walk around this by receiving
#: straight from draft.
PoApprove = Annotated[
    ScopeContext,
    Depends(require("operations.procurement.purchase_order.approve", minimum=Scope.ORG)),
]
SupplierRead = Annotated[ScopeContext, Depends(require("operations.procurement.supplier.read"))]
SupplierWrite = Annotated[ScopeContext, Depends(require("operations.procurement.supplier.write"))]
InventoryRead = Annotated[ScopeContext, Depends(require("operations.inventory.item.read"))]
InventoryWrite = Annotated[ScopeContext, Depends(require("operations.inventory.item.write"))]
StockAdjust = Annotated[ScopeContext, Depends(require("operations.inventory.stock.adjust"))]
#: Seeing and resolving ledger/balance drift is an org-wide control function, never OWN.
StockReconcile = Annotated[
    ScopeContext,
    Depends(require("operations.inventory.stock.reconcile", minimum=Scope.ORG)),
]
#: A client-chosen key that makes a retried stock write a replay instead of a second posting.
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Retrying with the same key replays the first result instead of posting "
        "again; reusing it for a different request is a 409.",
    ),
]
WarehouseRead = Annotated[ScopeContext, Depends(require("operations.inventory.warehouse.read"))]
WarehouseWrite = Annotated[ScopeContext, Depends(require("operations.inventory.warehouse.write"))]
LocationRead = Annotated[ScopeContext, Depends(require("operations.inventory.location.read"))]
#: The floor for a plain code/name/barcode/pick-sequence edit. Changing `kind` or a capacity
#: column on a location that already has ledger history additionally needs
#: `operations.inventory.layout.publish` — checked in `inv_services.update_location`, against
#: that location's own history, not here (a route-level dependency cannot see the payload).
LocationWrite = Annotated[ScopeContext, Depends(require("operations.inventory.location.write"))]
#: Reservations narrow what everyone else in the org can promise, so there is no OWN reading.
StockReserve = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.reserve", minimum=Scope.ORG))
]


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


@router.post(
    "/operations/suppliers",
    response_model=SupplierOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a supplier",
    dependencies=[Entitled],
)
async def create_supplier(
    payload: SupplierIn, session: SessionDep, ctx: SupplierWrite, request: Request
) -> SupplierOut:
    supplier = await services.create_supplier(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        category=payload.category,
        status=payload.status,
        lead_time_days=payload.lead_time_days,
        currency=payload.currency,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        location=payload.location,
        since=payload.since,
        request=request,
    )
    return selectors.supplier_out(supplier)


@router.patch(
    "/operations/suppliers/{supplier_id}",
    response_model=SupplierOut,
    summary="Update a supplier",
    dependencies=[Entitled],
)
async def update_supplier(
    supplier_id: uuid.UUID,
    payload: SupplierPatch,
    session: SessionDep,
    ctx: SupplierWrite,
    request: Request,
) -> SupplierOut:
    supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    updated = await services.update_supplier(session, ctx, supplier, payload, request=request)
    return selectors.supplier_out(updated)


@router.delete(
    "/operations/suppliers/{supplier_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a supplier",
    dependencies=[Entitled],
)
async def delete_supplier(
    supplier_id: uuid.UUID, session: SessionDep, ctx: SupplierWrite, request: Request
) -> Response:
    supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    await services.delete_supplier(session, ctx, supplier, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    sort: Annotated[str | None, Query(description="expected | created | number")] = None,
) -> Page[PurchaseOrderOut]:
    stmt = selectors.purchase_orders_stmt(
        ctx, q=q, status=po_status, category=category, supplier_id=supplier_id, sort=sort
    )
    rows, total = await paginate(session, stmt, params)
    by_po = await selectors.lines_map(session, ctx.organization_id, [r.id for r in rows])
    items = [selectors.purchase_order_out(po, lines=by_po.get(po.id, [])) for po in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/operations/purchase-orders",
    response_model=PurchaseOrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a purchase order",
    dependencies=[Entitled],
)
async def create_purchase_order(
    payload: PurchaseOrderIn, session: SessionDep, ctx: PoWrite, request: Request
) -> PurchaseOrderOut:
    po = await services.raise_purchase_order(
        session,
        ctx,
        supplier_id=payload.supplier_id,
        warehouse=payload.warehouse,
        expected_date=payload.expected_date,
        currency=payload.currency,
        notes=payload.notes,
        raised_by=payload.raised_by,
        lines=[line.model_dump() for line in payload.lines],
        request=request,
    )
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    return selectors.purchase_order_out(po, lines=lines)


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


@router.patch(
    "/operations/purchase-orders/{po_id}",
    response_model=PurchaseOrderOut,
    summary="Edit a draft purchase order (and, optionally, replace its lines)",
    dependencies=[Entitled],
)
async def update_purchase_order(
    po_id: uuid.UUID,
    payload: PurchaseOrderPatch,
    session: SessionDep,
    ctx: PoWrite,
    request: Request,
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    updated = await services.update_purchase_order(session, ctx, po, payload, request=request)
    lines = await selectors.lines_for(session, ctx.organization_id, updated.id)
    return selectors.purchase_order_out(updated, lines=lines)


@router.delete(
    "/operations/purchase-orders/{po_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft purchase order",
    dependencies=[Entitled],
)
async def delete_purchase_order(
    po_id: uuid.UUID, session: SessionDep, ctx: PoWrite, request: Request
) -> Response:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    await services.delete_purchase_order(session, ctx, po, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/operations/purchase-orders/{po_id}/approve",
    response_model=PurchaseOrderOut,
    summary="Approve a draft purchase order",
    dependencies=[Entitled],
)
async def approve_purchase_order(
    po_id: uuid.UUID, session: SessionDep, ctx: PoApprove, request: Request
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    await services.approve_purchase_order(session, ctx, po, request=request)
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    return selectors.purchase_order_out(po, lines=lines)


@router.post(
    "/operations/purchase-orders/{po_id}/receive",
    response_model=PurchaseOrderReceiptOut,
    summary="Receive an approved purchase order (marks it received, fills received quantities "
    "and posts stock)",
    dependencies=[Entitled],
)
async def receive_purchase_order(
    po_id: uuid.UUID,
    session: SessionDep,
    ctx: PoWrite,
    request: Request,
    payload: ReceiveIn | None = None,
) -> PurchaseOrderReceiptOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    received_date = payload.received_date if payload is not None else None
    unmatched: list[str] = []
    await services.receive_purchase_order(
        session, ctx, po, received_date=received_date, request=request, unmatched_out=unmatched
    )
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    out = selectors.purchase_order_out(po, lines=lines)
    return PurchaseOrderReceiptOut(**out.model_dump(), unmatched_skus=unmatched)


@router.post(
    "/operations/purchase-orders/{po_id}/close",
    response_model=PurchaseOrderOut,
    summary="Close a received purchase order",
    dependencies=[Entitled],
)
async def close_purchase_order(
    po_id: uuid.UUID, session: SessionDep, ctx: PoWrite, request: Request
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    await services.close_purchase_order(session, ctx, po, request=request)
    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    return selectors.purchase_order_out(po, lines=lines)


@router.post(
    "/operations/purchase-orders/{po_id}/cancel",
    response_model=PurchaseOrderOut,
    summary="Cancel a draft or approved purchase order",
    dependencies=[Entitled],
)
async def cancel_purchase_order(
    po_id: uuid.UUID,
    payload: CancelIn,
    session: SessionDep,
    ctx: PoWrite,
    request: Request,
) -> PurchaseOrderOut:
    po = await selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    ctx.assert_can_touch(owner_id=po.raised_by_id)
    await services.cancel_purchase_order(session, ctx, po, reason=payload.reason, request=request)
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
    item_category_id: Annotated[uuid.UUID | None, Query()] = None,
    warehouse: Annotated[str | None, Query()] = None,
    low_stock: Annotated[
        bool | None, Query(description="Only items at/under reorder point")
    ] = None,
    is_active: Annotated[bool | None, Query()] = None,
    sort: Annotated[str | None, Query(description="on_hand | value | sku")] = None,
) -> Page[InventoryItemOut]:
    stmt = inv_selectors.inventory_stmt(
        ctx,
        q=q,
        category=category,
        item_category_id=item_category_id,
        warehouse=warehouse,
        low_stock=low_stock,
        is_active=is_active,
        sort=sort,
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.inventory_out(i) for i in rows], total=total, params=params)


@router.post(
    "/operations/inventory",
    response_model=InventoryItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an inventory item",
    dependencies=[Entitled],
)
async def create_inventory_item(
    payload: InventoryItemIn, session: SessionDep, ctx: InventoryWrite, request: Request
) -> InventoryItemOut:
    if (
        await inv_selectors.get_warehouse_by_name(session, ctx.organization_id, payload.warehouse)
        is None
    ):
        raise ValidationProblem(f"{payload.warehouse!r} does not match an existing warehouse")
    item = await inv_services.create_inventory_item(
        session,
        ctx,
        sku=payload.sku,
        name=payload.name,
        category=payload.category,
        warehouse=payload.warehouse,
        on_hand=payload.opening_on_hand,
        reorder_point=payload.reorder_point,
        uom=payload.uom,
        unit_value_minor=payload.unit_value_minor,
        currency=payload.currency,
        item_category_id=payload.item_category_id,
        secondary_uom_id=payload.secondary_uom_id,
        hsn_code=payload.hsn_code,
        track_lot=payload.track_lot,
        track_roll=payload.track_roll,
        track_expiry=payload.track_expiry,
        track_quality=payload.track_quality,
        is_active=payload.is_active,
        request=request,
    )
    return inv_selectors.inventory_out(item)


# Registered before `/operations/inventory/{item_id}`: a literal segment must win over the
# UUID-typed parameter, or `discrepancies`, `uoms` and `categories` are parsed as an item id and
# answered with a 422.


@router.get(
    "/operations/inventory/uoms",
    response_model=Page[UomOut],
    summary="The org's UOM directory (registry units first, then custom ones)",
    dependencies=[Entitled],
)
async def list_uoms(session: SessionDep, ctx: InventoryRead, params: PageQuery) -> Page[UomOut]:
    await inv_services.ensure_seed_uoms(session, ctx.organization_id, str(ctx.principal.sub))
    stmt = inv_selectors.uoms_stmt(ctx.organization_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.uom_out(u) for u in rows], total=total, params=params)


# ------------------------------------------------------------------ item categories


@router.get(
    "/operations/inventory/categories",
    response_model=Page[ItemCategoryOut],
    summary="The item master's own category tree",
    dependencies=[Entitled],
)
async def list_item_categories(
    session: SessionDep, ctx: InventoryRead, params: PageQuery
) -> Page[ItemCategoryOut]:
    stmt = inv_selectors.item_categories_stmt(ctx.organization_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.category_out(c) for c in rows], total=total, params=params)


@router.post(
    "/operations/inventory/categories",
    response_model=ItemCategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a category",
    dependencies=[Entitled],
)
async def create_item_category(
    payload: ItemCategoryIn, session: SessionDep, ctx: InventoryWrite, request: Request
) -> ItemCategoryOut:
    category = await inv_services.create_item_category(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        parent_id=payload.parent_id,
        request=request,
    )
    return inv_selectors.category_out(category)


@router.get(
    "/operations/inventory/categories/{category_id}",
    response_model=ItemCategoryOut,
    summary="One category",
    dependencies=[Entitled],
)
async def read_item_category(
    category_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> ItemCategoryOut:
    category = await inv_selectors.get_item_category(session, ctx.organization_id, category_id)
    if category is None:
        raise NotFoundProblem("That category is not part of this organization")
    return inv_selectors.category_out(category)


@router.patch(
    "/operations/inventory/categories/{category_id}",
    response_model=ItemCategoryOut,
    summary="Update a category",
    dependencies=[Entitled],
)
async def update_item_category(
    category_id: uuid.UUID,
    payload: ItemCategoryPatch,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> ItemCategoryOut:
    category = await inv_selectors.get_item_category(session, ctx.organization_id, category_id)
    if category is None:
        raise NotFoundProblem("That category is not part of this organization")
    updated = await inv_services.update_item_category(
        session, ctx, category, payload, request=request
    )
    return inv_selectors.category_out(updated)


@router.delete(
    "/operations/inventory/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a category",
    dependencies=[Entitled],
)
async def delete_item_category(
    category_id: uuid.UUID, session: SessionDep, ctx: InventoryWrite, request: Request
) -> Response:
    category = await inv_selectors.get_item_category(session, ctx.organization_id, category_id)
    if category is None:
        raise NotFoundProblem("That category is not part of this organization")
    await inv_services.delete_item_category(session, ctx, category, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/operations/inventory/reconcile",
    response_model=ReconcileOut,
    summary="Check this organization's stock ledger against its balances now",
    dependencies=[Entitled],
)
async def reconcile_inventory(
    session: SessionDep, ctx: StockReconcile, request: Request
) -> ReconcileOut:
    report = await inv_services.reconcile_now(session, ctx, request=request)
    rows = await inv_selectors.discrepancies_for_run(session, ctx.organization_id, report.run_id)
    return ReconcileOut(
        run_id=str(report.run_id),
        buckets_checked=report.buckets_checked,
        findings=len(report.findings),
        new_findings=len(report.new),
        discrepancies=[inv_selectors.discrepancy_out(row) for row in rows],
    )


@router.get(
    "/operations/inventory/discrepancies",
    response_model=Page[InventoryDiscrepancyOut],
    summary="Ledger/balance drift the reconciler found, newest first",
    dependencies=[Entitled],
)
async def list_discrepancies(
    session: SessionDep,
    ctx: StockReconcile,
    params: PageQuery,
    include_resolved: Annotated[bool, Query()] = False,
) -> Page[InventoryDiscrepancyOut]:
    stmt = inv_selectors.discrepancies_stmt(ctx, open_only=not include_resolved)
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [inv_selectors.discrepancy_out(row) for row in rows], total=total, params=params
    )


@router.post(
    "/operations/inventory/discrepancies/{discrepancy_id}/resolve",
    response_model=InventoryDiscrepancyOut,
    summary="Record what was done about a discrepancy (changes no stock)",
    dependencies=[Entitled],
)
async def resolve_discrepancy(
    discrepancy_id: uuid.UUID,
    payload: DiscrepancyResolveIn,
    session: SessionDep,
    ctx: StockReconcile,
    request: Request,
) -> InventoryDiscrepancyOut:
    row = await inv_selectors.get_discrepancy(session, ctx.organization_id, discrepancy_id)
    if row is None:
        raise NotFoundProblem("That discrepancy is not part of this organization")
    resolved = await inv_services.resolve_discrepancy(
        session, ctx, row, note=payload.note, request=request
    )
    return inv_selectors.discrepancy_out(resolved)


@router.get(
    "/operations/inventory/{item_id}",
    response_model=InventoryItemOut,
    summary="One inventory item",
    dependencies=[Entitled],
)
async def read_inventory_item(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> InventoryItemOut:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    return inv_selectors.inventory_out(item)


@router.patch(
    "/operations/inventory/{item_id}",
    response_model=InventoryItemOut,
    summary="Update an inventory item's card",
    dependencies=[Entitled],
)
async def update_inventory_item(
    item_id: uuid.UUID,
    payload: InventoryItemPatch,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> InventoryItemOut:
    # Locked: a warehouse change moves the item's stock, and must not race an adjustment.
    item = await inv_selectors.get_inventory_item(
        session, ctx.organization_id, item_id, for_update=True
    )
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    updated = await inv_services.update_inventory_item(session, ctx, item, payload, request=request)
    return inv_selectors.inventory_out(updated)


@router.delete(
    "/operations/inventory/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an inventory item",
    dependencies=[Entitled],
)
async def delete_inventory_item(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryWrite, request: Request
) -> Response:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    await inv_services.delete_inventory_item(session, ctx, item, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/operations/inventory/{item_id}/adjustments",
    response_model=InventoryItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Adjust or count an item's stock on hand",
    dependencies=[Entitled],
)
async def adjust_stock(
    item_id: uuid.UUID,
    payload: StockAdjustmentIn,
    session: SessionDep,
    ctx: StockAdjust,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InventoryItemOut:
    item = await inv_selectors.get_inventory_item(
        session, ctx.organization_id, item_id, for_update=True
    )
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    updated = await inv_services.adjust_stock(
        session,
        ctx,
        item,
        quantity_delta=payload.quantity_delta,
        counted_on_hand=payload.counted_on_hand,
        reason=payload.reason,
        note=payload.note,
        occurred_on=payload.occurred_on,
        # `occurred_on` defaults to today at parse time when the client omits it; keeping a
        # defaulted value out of the idempotency hash means a retry that lands on the other side
        # of midnight is still recognised as the same request rather than a false 409.
        occurred_on_explicit="occurred_on" in payload.model_fields_set,
        idempotency_key=idempotency_key,
        request=request,
    )
    return inv_selectors.inventory_out(updated)


@router.get(
    "/operations/inventory/{item_id}/balances",
    response_model=list[StockBalanceOut],
    summary="Where an item's stock is, and in what state (one row per bucket)",
    dependencies=[Entitled],
)
async def list_balances(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> list[StockBalanceOut]:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    rows = await inv_selectors.balances_for_item(session, ctx.organization_id, item_id)
    return [inv_selectors.balance_out(*row) for row in rows]


@router.get(
    "/operations/inventory/{item_id}/movements",
    response_model=Page[StockMovementOut],
    summary="An item's stock movement history, newest first",
    dependencies=[Entitled],
)
async def list_movements(
    item_id: uuid.UUID,
    session: SessionDep,
    ctx: InventoryRead,
    params: PageQuery,
    kind: Annotated[str | None, Query(description="opening | receipt | adjustment | count")] = None,
) -> Page[StockMovementOut]:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    stmt = inv_selectors.movements_stmt(ctx, item_id, kind=kind)
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.movement_out(m) for m in rows], total=total, params=params)


# ------------------------------------------------------------------ item attributes


@router.get(
    "/operations/inventory/{item_id}/attributes",
    response_model=list[ItemAttributeOut],
    summary="An item's typed attributes (GSM, width, shade, …)",
    dependencies=[Entitled],
)
async def list_item_attributes(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> list[ItemAttributeOut]:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    rows = await inv_selectors.item_attributes_for(session, ctx.organization_id, item_id)
    return [inv_selectors.attribute_out(a) for a in rows]


@router.put(
    "/operations/inventory/{item_id}/attributes/{key}",
    response_model=ItemAttributeOut,
    summary="Create or overwrite one typed attribute",
    dependencies=[Entitled],
)
async def set_item_attribute(
    item_id: uuid.UUID,
    key: Annotated[str, Path(min_length=1, max_length=60)],
    payload: ItemAttributeIn,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> ItemAttributeOut:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    attribute = await inv_services.set_item_attribute(
        session, ctx, item, key, payload, request=request
    )
    return inv_selectors.attribute_out(attribute)


@router.delete(
    "/operations/inventory/{item_id}/attributes/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a typed attribute",
    dependencies=[Entitled],
)
async def delete_item_attribute(
    item_id: uuid.UUID,
    key: Annotated[str, Path(min_length=1, max_length=60)],
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> Response:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    await inv_services.delete_item_attribute(session, ctx, item, key, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ item variants


@router.get(
    "/operations/inventory/{item_id}/variants",
    response_model=list[ItemVariantOut],
    summary="An item's purchasable/stockable variants",
    dependencies=[Entitled],
)
async def list_item_variants(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> list[ItemVariantOut]:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    rows = await inv_selectors.item_variants_for(session, ctx.organization_id, item_id)
    return [inv_selectors.variant_out(v) for v in rows]


@router.post(
    "/operations/inventory/{item_id}/variants",
    response_model=ItemVariantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a variant",
    dependencies=[Entitled],
)
async def create_item_variant(
    item_id: uuid.UUID,
    payload: ItemVariantIn,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> ItemVariantOut:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    variant = await inv_services.create_item_variant(
        session, ctx, item, sku=payload.sku, name=payload.name, request=request
    )
    return inv_selectors.variant_out(variant)


@router.patch(
    "/operations/inventory/{item_id}/variants/{variant_id}",
    response_model=ItemVariantOut,
    summary="Update a variant",
    dependencies=[Entitled],
)
async def update_item_variant(
    item_id: uuid.UUID,
    variant_id: uuid.UUID,
    payload: ItemVariantPatch,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> ItemVariantOut:
    variant = await inv_selectors.get_item_variant(
        session, ctx.organization_id, item_id, variant_id
    )
    if variant is None:
        raise NotFoundProblem("That variant is not part of this item")
    updated = await inv_services.update_item_variant(
        session, ctx, variant, payload, request=request
    )
    return inv_selectors.variant_out(updated)


@router.delete(
    "/operations/inventory/{item_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a variant",
    dependencies=[Entitled],
)
async def delete_item_variant(
    item_id: uuid.UUID,
    variant_id: uuid.UUID,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> Response:
    variant = await inv_selectors.get_item_variant(
        session, ctx.organization_id, item_id, variant_id
    )
    if variant is None:
        raise NotFoundProblem("That variant is not part of this item")
    await inv_services.delete_item_variant(session, ctx, variant, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ reservations


@router.post(
    "/operations/inventory/reservations/{reservation_id}:release",
    response_model=InventoryReservationOut,
    summary="Give back all or part of a reservation's open remainder (ADR-0014 §11)",
    dependencies=[Entitled],
)
async def release_reservation(
    reservation_id: uuid.UUID,
    session: SessionDep,
    ctx: StockReserve,
    request: Request,
    payload: ReservationReleaseIn | None = None,
    idempotency_key: IdempotencyKey = None,
) -> InventoryReservationOut:
    quantity_milli: int | None = None
    if payload is not None and payload.quantity is not None:
        try:
            quantity_milli = parse_qty(payload.quantity)
        except QuantityError as exc:
            raise ValidationProblem(str(exc)) from exc
    released = await inv_ledger.release(
        session,
        ctx,
        reservation_id,
        quantity_milli=quantity_milli,
        idempotency_key=idempotency_key,
        request=request,
    )
    return inv_selectors.reservation_out(released)


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
    stmt = inv_selectors.warehouses_stmt(ctx.organization_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.warehouse_out(w) for w in rows], total=total, params=params)


@router.post(
    "/operations/warehouses",
    response_model=WarehouseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a warehouse",
    dependencies=[Entitled],
)
async def create_warehouse(
    payload: WarehouseIn, session: SessionDep, ctx: WarehouseWrite, request: Request
) -> WarehouseOut:
    warehouse = await inv_services.create_warehouse(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        location=payload.location,
        request=request,
    )
    return inv_selectors.warehouse_out(warehouse)


@router.patch(
    "/operations/warehouses/{warehouse_id}",
    response_model=WarehouseOut,
    summary="Update a warehouse",
    dependencies=[Entitled],
)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehousePatch,
    session: SessionDep,
    ctx: WarehouseWrite,
    request: Request,
) -> WarehouseOut:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    updated = await inv_services.update_warehouse(session, ctx, warehouse, payload, request=request)
    return inv_selectors.warehouse_out(updated)


@router.delete(
    "/operations/warehouses/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a warehouse",
    dependencies=[Entitled],
)
async def delete_warehouse(
    warehouse_id: uuid.UUID, session: SessionDep, ctx: WarehouseWrite, request: Request
) -> Response:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    await inv_services.delete_warehouse(session, ctx, warehouse, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ locations (ADR-0024 §5)


@router.get(
    "/operations/warehouses/{warehouse_id}/locations",
    response_model=Page[LocationOut],
    summary="A warehouse's location tree",
    dependencies=[Entitled],
)
async def list_locations(
    warehouse_id: uuid.UUID, session: SessionDep, ctx: LocationRead, params: PageQuery
) -> Page[LocationOut]:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    stmt = inv_selectors.locations_stmt(ctx.organization_id, warehouse_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([inv_selectors.location_out(loc) for loc in rows], total=total, params=params)


@router.post(
    "/operations/warehouses/{warehouse_id}/locations",
    response_model=LocationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a location to a warehouse",
    dependencies=[Entitled],
)
async def create_location(
    warehouse_id: uuid.UUID,
    payload: LocationIn,
    session: SessionDep,
    ctx: LocationWrite,
    request: Request,
) -> LocationOut:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    location = await inv_services.create_location(
        session,
        ctx,
        warehouse,
        kind=payload.kind,
        code=payload.code,
        name=payload.name,
        parent_id=payload.parent_id,
        barcode=payload.barcode,
        pick_sequence=payload.pick_sequence,
        max_weight_grams=payload.max_weight_grams,
        max_volume_milli=payload.max_volume_milli,
        storage_type=payload.storage_type,
        request=request,
    )
    return inv_selectors.location_out(location)


@router.get(
    "/operations/locations/by-barcode/{barcode}",
    response_model=LocationOut,
    summary="Resolve a scanned barcode to its location",
    dependencies=[Entitled],
)
async def read_location_by_barcode(
    barcode: Annotated[str, Path(min_length=1, max_length=64)],
    session: SessionDep,
    ctx: LocationRead,
) -> LocationOut:
    location = await inv_selectors.get_location_by_barcode(session, ctx.organization_id, barcode)
    if location is None:
        raise NotFoundProblem("That barcode does not resolve to a location in this organization")
    return inv_selectors.location_out(location)


@router.patch(
    "/operations/locations/{location_id}",
    response_model=LocationOut,
    summary="Update a location",
    dependencies=[Entitled],
)
async def update_location(
    location_id: uuid.UUID,
    payload: LocationPatch,
    session: SessionDep,
    ctx: LocationWrite,
    request: Request,
) -> LocationOut:
    location = await inv_selectors.get_location(session, ctx.organization_id, location_id)
    if location is None:
        raise NotFoundProblem("That location is not part of this organization")
    updated = await inv_services.update_location(session, ctx, location, payload, request=request)
    return inv_selectors.location_out(updated)


# ------------------------------------------------------------------ overview


@router.get(
    "/operations/overview",
    response_model=OverviewOut,
    summary="Procurement & inventory overview rollups",
    dependencies=[Entitled],
)
async def overview(session: SessionDep, ctx: PoRead) -> OverviewOut:
    pos = await selectors.all_purchase_orders(session, ctx)
    inventory = await inv_selectors.all_inventory(session, ctx.organization_id)
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
        out_of_stock_count=domain.out_of_stock_count(inventory),
        on_time_pct=domain.on_time_delivery_pct(pos, totals),
        currency=currency,
        spend_trend=domain.spend_trend(pos, totals),
        spend_by_category=domain.spend_by_category(pos, totals),
        po_status_mix=domain.po_status_mix(pos),
        top_suppliers=domain.top_suppliers(suppliers),
        attention=domain.attention_items(pos, inventory, today),
    )
