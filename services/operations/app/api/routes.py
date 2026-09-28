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
from app.dispatch import selectors as dispatch_selectors
from app.dispatch import services as dispatch_services
from app.fulfilment import selectors as fulfilment_selectors
from app.fulfilment import services as fulfilment_services
from app.inventory import ledger as inv_ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.quantity import QuantityError, parse_qty
from app.models import SCHEMA, LayoutVersion, Order, PurchaseOrderLine, WarehouseFloor
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from app.schemas import (
    AssignmentOfferOut,
    CancelIn,
    CreatePickTasksIn,
    DiscrepancyResolveIn,
    ExceptionResolveIn,
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
    LayoutGeometryIn,
    LayoutGeometryOut,
    LayoutValidationOut,
    LayoutVersionCreateIn,
    LayoutVersionOut,
    LocationIn,
    LocationOut,
    LocationPatch,
    OfferIn,
    OrderCancelIn,
    OrderHandoverIn,
    OrderHoldIn,
    OrderIn,
    OrderOut,
    OrderVersionIn,
    OverviewOut,
    PackageIn,
    PackageOut,
    PackagingMaterialIn,
    PackagingMaterialOut,
    PickConfirmIn,
    PickTaskOut,
    PurchaseOrderIn,
    PurchaseOrderOut,
    PurchaseOrderPatch,
    PurchaseOrderReceiptOut,
    QuoteIn,
    QuoteOut,
    ReceiveIn,
    ReconcileOut,
    ReservationReleaseIn,
    RouteOut,
    ScanIn,
    ServiceInfo,
    ShipmentAdvanceIn,
    ShipmentIn,
    ShipmentOut,
    ShipmentRefusalIn,
    StockAdjustmentIn,
    StockBalanceOut,
    StockMovementOut,
    SupplierIn,
    SupplierOut,
    SupplierPatch,
    TaskCancelIn,
    TaskReleaseIn,
    UomOut,
    WarehouseFloorIn,
    WarehouseFloorOut,
    WarehouseFloorPatch,
    WarehouseIn,
    WarehouseOut,
    WarehousePatch,
    WorkerIn,
    WorkerOut,
    WorkerPatch,
    WorkerPresenceIn,
)
from app.warehouse import selectors as wh_selectors
from app.warehouse import services as wh_services
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_session, get_tenant_session
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

# ------------------------------------------------------------------ warehouse geometry (plan §2/
# F7): floors, versioned layouts, the walk graph and routing over it.

FloorRead = Annotated[ScopeContext, Depends(require("operations.warehouse.floor.read"))]
FloorWrite = Annotated[ScopeContext, Depends(require("operations.warehouse.floor.write"))]
LayoutRead = Annotated[ScopeContext, Depends(require("operations.warehouse.layout.read"))]
LayoutWrite = Annotated[ScopeContext, Depends(require("operations.warehouse.layout.write"))]
#: Making a draft the floor's current layout, or rolling back to a prior published one — every
#: worker's pick routing and the F9 3D viewer read whatever is published, so this is a
#: platform-wide change the instant it commits, not a private edit.
LayoutPublish = Annotated[
    ScopeContext, Depends(require("operations.warehouse.layout.publish", minimum=Scope.ORG))
]

# ------------------------------------------------------------------ orders (ADR-0024, ADR-0025)

#: `accept` alone uses the raw, unwrapped session: `order_services.accept_order`'s claim/call/
#: record shape (`docs/io-outside-transactions.md`) needs the credit check to run between two
#: separate transactions on one session, with no transaction open while it runs. Binding the
#: whole route in `get_tenant_session`'s one request-long transaction — every other route here
#: does, and should — would hold that transaction open across the network call, exactly the
#: pattern the guard flags (`services/lead/app/api/routes.py`'s `:convert` draws the same line).
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Orders are a shared operational queue (`app.catalogue`'s own reasoning), not a per-person
#: document — like `SupplierRead`/`WarehouseRead`/`InventoryRead`, no `minimum=` is set here
#: because the catalogue itself offers no OWN grant to floor. `accept`/`cancel` below set it
#: explicitly anyway, matching `StockReserve`/`StockReconcile`'s stricter, defense-in-depth
#: treatment of the two genuinely consequential order actions (committing and un-committing
#: stock), not the plain-CRUD ones.
OrderRead = Annotated[ScopeContext, Depends(require("operations.orders.order.read"))]
OrderCreate = Annotated[ScopeContext, Depends(require("operations.orders.order.create"))]
OrderWrite = Annotated[ScopeContext, Depends(require("operations.orders.order.write"))]
OrderAccept = Annotated[
    ScopeContext, Depends(require("operations.orders.order.accept", minimum=Scope.ORG))
]
OrderCancel = Annotated[
    ScopeContext, Depends(require("operations.orders.order.cancel", minimum=Scope.ORG))
]
OrderHandOver = Annotated[
    ScopeContext, Depends(require("operations.orders.order.hand_over", minimum=Scope.ORG))
]

# ------------------------------------------------------------------ fulfilment (ADR-0024 §7,
# ADR-0025 §2/§3)

#: N3 (security review): floored at ORG, the same defense-in-depth shape `OrderAccept`/
#: `OrderCancel` already use on top of a catalogue whose own ceiling is already ORG — belt and
#: suspenders, in case a custom role ever names this permission at a narrower scope.
TaskRead = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.task.read", minimum=Scope.ORG))
]
#: No `minimum=` — the catalogue's own `max_scope` already ceilings every grant at `OWN`
#: (`app/catalogue.py`), so this can never resolve wider than "my own offered/accepted task"
#: regardless of the role it was granted from.
TaskExecute = Annotated[ScopeContext, Depends(require("operations.fulfilment.task.execute"))]
#: Creating tasks, offering, reassigning, and cancelling one nobody has started yet — ordinary
#: dispatcher work, floored at ORG like `StockReserve`/`OrderAccept`.
TaskAssign = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.task.assign", minimum=Scope.ORG))
]
#: Cancelling a task already in a worker's hands, or force-closing a picked one — a stronger
#: permission on edges that ARE in `PICK_TASK_TRANSITIONS`, never a way around the map.
TaskOverride = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.task.override", minimum=Scope.ORG))
]
#: N3 (security review): same defense-in-depth floor as `TaskRead`, above.
WorkerRead = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.worker.read", minimum=Scope.ORG))
]
#: No floor — a worker holding this at OWN may still set their own presence
#: (`ScopeContext.assert_can_touch` guards the object). Roster-management routes (create, patch)
#: set `minimum=Scope.ORG` explicitly, the same defense-in-depth shape `OrderAccept`/`OrderCancel`
#: use on top of a catalogue default that already offers OWN.
WorkerWrite = Annotated[ScopeContext, Depends(require("operations.fulfilment.worker.write"))]
WorkerRoster = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.worker.write", minimum=Scope.ORG))
]
#: N3 (security review): same defense-in-depth floor as `TaskRead`, above.
PackRead = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.pack.read", minimum=Scope.ORG))
]
PackExecute = Annotated[
    ScopeContext, Depends(require("operations.fulfilment.pack.execute", minimum=Scope.ORG))
]

# ------------------------------------------------------------------ dispatch (ADR-0024 §6;
# `docs/fulfilment/plan.md` F5)

#: N3 (security review) shape: floored at ORG, the same defense-in-depth belt-and-suspenders
#: `TaskRead`/`PackRead` already use on top of a catalogue whose own ceiling is already ORG.
DispatchRead = Annotated[
    ScopeContext, Depends(require("operations.dispatch.shipment.read", minimum=Scope.ORG))
]
#: The carrier handover is as consequential as `OrderHandOver` (it moves the order through the
#: same billing gate) — floored at ORG the same way.
DispatchWrite = Annotated[
    ScopeContext, Depends(require("operations.dispatch.shipment.write", minimum=Scope.ORG))
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
        selling_price_minor=payload.selling_price_minor,
        selling_price_currency=payload.selling_price_currency,
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


# ------------------------------------------------------------------ warehouse geometry (plan §2/
# F7): floors, versioned layouts, the walk graph and routing over it.


async def _get_floor_or_404(
    session: AsyncSession, ctx: ScopeContext, floor_id: uuid.UUID
) -> WarehouseFloor:
    floor = await wh_selectors.get_floor(session, ctx.organization_id, floor_id)
    if floor is None:
        raise NotFoundProblem("That floor is not part of this organization")
    return floor


async def _get_version_and_floor_or_404(
    session: AsyncSession, ctx: ScopeContext, version_id: uuid.UUID, *, for_update: bool = False
) -> tuple[LayoutVersion, WarehouseFloor]:
    version = await wh_selectors.get_layout_version(
        session, ctx.organization_id, version_id, for_update=for_update
    )
    if version is None:
        raise NotFoundProblem("That layout version is not part of this organization")
    floor = await _get_floor_or_404(session, ctx, version.warehouse_floor_id)
    return version, floor


@router.get(
    "/operations/warehouses/{warehouse_id}/floors",
    response_model=Page[WarehouseFloorOut],
    summary="A warehouse's floors",
    dependencies=[Entitled],
)
async def list_floors(
    warehouse_id: uuid.UUID, session: SessionDep, ctx: FloorRead, params: PageQuery
) -> Page[WarehouseFloorOut]:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    stmt = wh_selectors.floors_stmt(ctx.organization_id, warehouse_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([wh_selectors.floor_out(f) for f in rows], total=total, params=params)


@router.post(
    "/operations/warehouses/{warehouse_id}/floors",
    response_model=WarehouseFloorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a floor to a warehouse",
    dependencies=[Entitled],
)
async def create_floor(
    warehouse_id: uuid.UUID,
    payload: WarehouseFloorIn,
    session: SessionDep,
    ctx: FloorWrite,
    request: Request,
) -> WarehouseFloorOut:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    floor = await wh_services.create_floor(
        session,
        ctx,
        warehouse,
        floor_number=payload.floor_number,
        name=payload.name,
        request=request,
    )
    return wh_selectors.floor_out(floor)


@router.get(
    "/operations/warehouse-floors/{floor_id}",
    response_model=WarehouseFloorOut,
    summary="One floor",
    dependencies=[Entitled],
)
async def read_floor(floor_id: uuid.UUID, session: SessionDep, ctx: FloorRead) -> WarehouseFloorOut:
    floor = await _get_floor_or_404(session, ctx, floor_id)
    return wh_selectors.floor_out(floor)


@router.patch(
    "/operations/warehouse-floors/{floor_id}",
    response_model=WarehouseFloorOut,
    summary="Update a floor",
    dependencies=[Entitled],
)
async def update_floor(
    floor_id: uuid.UUID,
    payload: WarehouseFloorPatch,
    session: SessionDep,
    ctx: FloorWrite,
    request: Request,
) -> WarehouseFloorOut:
    floor = await _get_floor_or_404(session, ctx, floor_id)
    updated = await wh_services.update_floor(session, ctx, floor, payload, request=request)
    return wh_selectors.floor_out(updated)


@router.get(
    "/operations/warehouse-floors/{floor_id}/layout-versions",
    response_model=Page[LayoutVersionOut],
    summary="A floor's layout version history — draft, published and archived alike",
    dependencies=[Entitled],
)
async def list_layout_versions(
    floor_id: uuid.UUID, session: SessionDep, ctx: LayoutRead, params: PageQuery
) -> Page[LayoutVersionOut]:
    floor = await _get_floor_or_404(session, ctx, floor_id)
    stmt = wh_selectors.layout_versions_stmt(ctx.organization_id, floor.id)
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [wh_selectors.layout_version_out(v) for v in rows], total=total, params=params
    )


@router.post(
    "/operations/warehouse-floors/{floor_id}/layout-versions",
    response_model=LayoutVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new draft layout version for a floor",
    dependencies=[Entitled],
)
async def create_layout_version(
    floor_id: uuid.UUID,
    payload: LayoutVersionCreateIn,
    session: SessionDep,
    ctx: LayoutWrite,
    request: Request,
) -> LayoutVersionOut:
    floor = await _get_floor_or_404(session, ctx, floor_id)
    version = await wh_services.open_draft_version(
        session,
        ctx,
        floor,
        label=payload.label,
        clone_from_published=payload.clone_from_published,
        request=request,
    )
    return wh_selectors.layout_version_out(version)


async def _geometry_out(
    session: AsyncSession, ctx: ScopeContext, version: LayoutVersion
) -> LayoutGeometryOut:
    org_id = ctx.organization_id
    placements = await wh_selectors.placements_for_version(session, org_id, version.id)
    nodes = await wh_selectors.nodes_for_version(session, org_id, version.id)
    edges = await wh_selectors.edges_for_version(session, org_id, version.id)
    locations = await wh_selectors.locations_by_id(
        session, org_id, {p.location_id for p in placements}
    )
    code_by_node_id = {n.id: n.code for n in nodes}
    return LayoutGeometryOut(
        version=wh_selectors.layout_version_out(version),
        placements=[
            wh_selectors.placement_out(p, locations[p.location_id])
            for p in placements
            if p.location_id in locations
        ],
        walk_nodes=[wh_selectors.walk_node_out(n) for n in nodes],
        walk_edges=[wh_selectors.walk_edge_out(e, code_by_node_id) for e in edges],
    )


@router.get(
    "/operations/layout-versions/{version_id}",
    response_model=LayoutGeometryOut,
    summary="One layout version's full geometry — placements and walk graph",
    dependencies=[Entitled],
)
async def read_layout_version(
    version_id: uuid.UUID, session: SessionDep, ctx: LayoutRead
) -> LayoutGeometryOut:
    version, _floor = await _get_version_and_floor_or_404(session, ctx, version_id)
    return await _geometry_out(session, ctx, version)


@router.put(
    "/operations/layout-versions/{version_id}/geometry",
    response_model=LayoutGeometryOut,
    summary="Replace a draft's placements and walk graph — the canvas editor's \"save draft\"",
    dependencies=[Entitled],
)
async def save_layout_geometry(
    version_id: uuid.UUID,
    payload: LayoutGeometryIn,
    session: SessionDep,
    ctx: LayoutWrite,
    request: Request,
) -> LayoutGeometryOut:
    version, floor = await _get_version_and_floor_or_404(session, ctx, version_id, for_update=True)
    updated = await wh_services.save_draft_geometry(
        session, ctx, floor, version, payload, request=request
    )
    return await _geometry_out(session, ctx, updated)


@router.delete(
    "/operations/layout-versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Discard a draft layout version",
    dependencies=[Entitled],
)
async def discard_layout_version(
    version_id: uuid.UUID, session: SessionDep, ctx: LayoutWrite, request: Request
) -> Response:
    version, _floor = await _get_version_and_floor_or_404(session, ctx, version_id, for_update=True)
    await wh_services.discard_draft_version(session, ctx, version, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/operations/layout-versions/{version_id}/validate",
    response_model=LayoutValidationOut,
    summary="Check a layout for overlapping locations or disconnected walk nodes, without "
    "publishing",
    dependencies=[Entitled],
)
async def validate_layout_version(
    version_id: uuid.UUID, session: SessionDep, ctx: LayoutRead
) -> LayoutValidationOut:
    version, _floor = await _get_version_and_floor_or_404(session, ctx, version_id)
    return await wh_selectors.validate_layout(session, ctx.organization_id, version)


@router.post(
    "/operations/layout-versions/{version_id}/publish",
    response_model=LayoutVersionOut,
    summary="Publish a draft (or roll back to a prior published version)",
    dependencies=[Entitled],
)
async def publish_layout_version(
    version_id: uuid.UUID, session: SessionDep, ctx: LayoutPublish, request: Request
) -> LayoutVersionOut:
    version, floor = await _get_version_and_floor_or_404(session, ctx, version_id, for_update=True)
    published = await wh_services.publish_layout_version(
        session, ctx, floor, version, request=request
    )
    return wh_selectors.layout_version_out(published)


@router.get(
    "/operations/warehouse-floors/{floor_id}/route",
    response_model=RouteOut,
    summary="Shortest walk path between two locations on a floor's published layout",
    dependencies=[Entitled],
)
async def compute_route(
    floor_id: uuid.UUID,
    session: SessionDep,
    ctx: LayoutRead,
    from_location_id: Annotated[uuid.UUID, Query()],
    to_location_id: Annotated[uuid.UUID, Query()],
) -> RouteOut:
    floor = await _get_floor_or_404(session, ctx, floor_id)
    plan = await wh_selectors.find_route(
        session,
        ctx.organization_id,
        floor,
        from_location_id=from_location_id,
        to_location_id=to_location_id,
    )
    return RouteOut(
        from_location_id=str(from_location_id),
        to_location_id=str(to_location_id),
        node_ids=[str(n) for n in plan.node_ids],
        distance_mm=plan.distance_mm,
    )


# ------------------------------------------------------------------ orders (ADR-0024, ADR-0025)


async def _order_out(session: AsyncSession, ctx: ScopeContext, order: Order) -> OrderOut:
    lines = await order_selectors.lines_for(session, ctx.organization_id, order.id)
    items = await order_selectors.items_for_lines(
        session, ctx.organization_id, [line.inventory_item_id for line in lines]
    )
    return order_selectors.order_out(order, lines=lines, items=items)


@router.post(
    "/operations/orders/quote",
    response_model=QuoteOut,
    summary="Price a set of lines against a warehouse, read-only (posts nothing)",
    dependencies=[Entitled],
)
async def quote_order(payload: QuoteIn, session: SessionDep, ctx: OrderRead) -> QuoteOut:
    warehouse = await inv_selectors.get_warehouse(
        session, ctx.organization_id, payload.warehouse_id
    )
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    return await order_selectors.quote_preview(session, ctx.organization_id, payload)


@router.get(
    "/operations/orders",
    response_model=Page[OrderOut],
    summary="Orders, filterable by status and channel",
    dependencies=[Entitled],
)
async def list_orders(
    session: SessionDep,
    ctx: OrderRead,
    params: PageQuery,
    order_status: Annotated[str | None, Query(alias="status")] = None,
    channel: Annotated[str | None, Query()] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[OrderOut]:
    stmt = order_selectors.orders_stmt(
        ctx.organization_id, status=order_status, channel=channel, warehouse_id=warehouse_id
    )
    rows, total = await paginate(session, stmt, params)
    by_order = await order_selectors.lines_map(session, ctx.organization_id, [r.id for r in rows])
    item_ids = [line.inventory_item_id for lines in by_order.values() for line in lines]
    items = await order_selectors.items_for_lines(session, ctx.organization_id, item_ids)
    out = [
        order_selectors.order_out(order, lines=by_order.get(order.id, []), items=items)
        for order in rows
    ]
    return Page.build(out, total=total, params=params)


@router.post(
    "/operations/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft order",
    dependencies=[Entitled],
)
async def create_order(
    payload: OrderIn, session: SessionDep, ctx: OrderCreate, request: Request
) -> OrderOut:
    order = await order_services.create_order(
        session,
        ctx,
        channel=payload.channel,
        warehouse_id=payload.warehouse_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        currency=payload.currency,
        lines=[line.model_dump() for line in payload.lines],
        request=request,
    )
    return await _order_out(session, ctx, order)


@router.get(
    "/operations/orders/{order_id}",
    response_model=OrderOut,
    summary="One order, with its lines",
    dependencies=[Entitled],
)
async def read_order(order_id: uuid.UUID, session: SessionDep, ctx: OrderRead) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    return await _order_out(session, ctx, order)


@router.post(
    "/operations/orders/{order_id}/place",
    response_model=OrderOut,
    summary="Place a draft order: prices every line, reserves nothing",
    dependencies=[Entitled],
)
async def place_order(
    order_id: uuid.UUID,
    payload: OrderVersionIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    await order_services.place_order(
        session, ctx, order, expected_version=payload.version, request=request
    )
    return await _order_out(session, ctx, order)


@router.post(
    "/operations/orders/{order_id}/accept",
    response_model=OrderOut,
    summary=(
        "Accept a placed order: reserve every line atomically "
        "(credit check not yet implemented — pending F4 billing)"
    ),
    dependencies=[Entitled],
)
async def accept_order(
    order_id: uuid.UUID,
    payload: OrderVersionIn,
    session: RawSessionDep,
    ctx: OrderAccept,
    request: Request,
) -> OrderOut:
    await order_services.accept_order(
        session, ctx, order_id, expected_version=payload.version, request=request
    )
    order, lines, items = await order_services.load_order_for_response(session, ctx, order_id)
    return order_selectors.order_out(order, lines=lines, items=items)


@router.post(
    "/operations/orders/{order_id}/hold",
    response_model=OrderOut,
    summary="Put an order on hold (reason required)",
    dependencies=[Entitled],
)
async def hold_order(
    order_id: uuid.UUID,
    payload: OrderHoldIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    await order_services.hold_order(
        session,
        ctx,
        order,
        expected_version=payload.version,
        reason=payload.reason,
        request=request,
    )
    return await _order_out(session, ctx, order)


@router.post(
    "/operations/orders/{order_id}/release",
    response_model=OrderOut,
    summary="Release a held order (target recomputed from its own facts)",
    dependencies=[Entitled],
)
async def release_order(
    order_id: uuid.UUID,
    payload: OrderVersionIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    await order_services.release_hold_order(
        session, ctx, order, expected_version=payload.version, request=request
    )
    return await _order_out(session, ctx, order)


@router.post(
    "/operations/orders/{order_id}/cancel",
    response_model=OrderOut,
    summary="Cancel an order and release every open reservation it holds",
    dependencies=[Entitled],
)
async def cancel_order(
    order_id: uuid.UUID,
    payload: OrderCancelIn,
    session: SessionDep,
    ctx: OrderCancel,
    request: Request,
) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    await order_services.cancel_order(
        session,
        ctx,
        order,
        expected_version=payload.version,
        reason=payload.reason,
        request=request,
    )
    return await _order_out(session, ctx, order)


@router.post(
    "/operations/orders/{order_id}/hand-over",
    response_model=OrderOut,
    summary=(
        "Hand an order over at the counter once the billing gate passes "
        "(also confirms delivery, ADR-0025 §1)"
    ),
    dependencies=[Entitled],
)
async def hand_over_order(
    order_id: uuid.UUID,
    payload: OrderHandoverIn,
    session: SessionDep,
    ctx: OrderHandOver,
    request: Request,
) -> OrderOut:
    order = await order_selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    await order_services.hand_over_order(
        session,
        ctx,
        order,
        expected_version=payload.version,
        pickup_code=payload.pickup_code,
        payment_confirmed=payload.payment_confirmed,
        request=request,
    )
    return await _order_out(session, ctx, order)


# ------------------------------------------------------------------ fulfilment: workers


async def _worker_out(session: AsyncSession, ctx: ScopeContext, worker) -> object:  # type: ignore[no-untyped-def]
    skills = await fulfilment_selectors.skills_for(session, ctx.organization_id, [worker.id])
    presence = await fulfilment_selectors.get_presence(session, ctx.organization_id, worker.id)
    zone_location_ids: list[str] | None = None
    if ctx.principal.has("operations.fulfilment.worker.location_read"):
        zones = await fulfilment_selectors.zone_access_for(session, ctx.organization_id, worker.id)
        zone_location_ids = [str(z) for z in zones]
    return fulfilment_selectors.worker_out(
        worker,
        skills=skills.get(worker.id, []),
        presence=presence,
        zone_location_ids=zone_location_ids,
    )


@router.get(
    "/operations/fulfilment/workers",
    response_model=Page[WorkerOut],
    summary="The worker roster, with presence",
    dependencies=[Entitled],
)
async def list_workers(
    session: SessionDep,
    ctx: WorkerRead,
    params: PageQuery,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    worker_status: Annotated[bool | None, Query(alias="is_active")] = None,
    q: Annotated[str | None, Query(description="Display name")] = None,
) -> Page[WorkerOut]:
    stmt = fulfilment_selectors.workers_stmt(
        ctx, warehouse_id=warehouse_id, is_active=worker_status, q=q
    )
    rows, total = await paginate(session, stmt, params)
    worker_ids = [w.id for w in rows]
    skills = await fulfilment_selectors.skills_for(session, ctx.organization_id, worker_ids)
    presence = await fulfilment_selectors.presence_for(session, ctx.organization_id, worker_ids)
    has_location_read = ctx.principal.has("operations.fulfilment.worker.location_read")
    out = []
    for w in rows:
        zone_location_ids: list[str] | None = None
        if has_location_read:
            zones = await fulfilment_selectors.zone_access_for(session, ctx.organization_id, w.id)
            zone_location_ids = [str(z) for z in zones]
        out.append(
            fulfilment_selectors.worker_out(
                w,
                skills=skills.get(w.id, []),
                presence=presence.get(w.id),
                zone_location_ids=zone_location_ids,
            )
        )
    return Page.build(out, total=total, params=params)


@router.post(
    "/operations/fulfilment/workers",
    response_model=WorkerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a worker",
    dependencies=[Entitled],
)
async def create_worker(
    payload: WorkerIn, session: SessionDep, ctx: WorkerRoster, request: Request
) -> object:
    worker = await fulfilment_services.create_worker(
        session,
        ctx,
        employee_id=payload.employee_id,
        warehouse_id=payload.warehouse_id,
        display_name=payload.display_name,
        is_active=payload.is_active,
        skills=payload.skills,
        zone_location_ids=payload.zone_location_ids,
        request=request,
    )
    return await _worker_out(session, ctx, worker)


@router.get(
    "/operations/fulfilment/workers/{worker_id}",
    response_model=WorkerOut,
    summary="One worker",
    dependencies=[Entitled],
)
async def read_worker(worker_id: uuid.UUID, session: SessionDep, ctx: WorkerRead) -> object:
    worker = await fulfilment_selectors.get_worker(session, ctx.organization_id, worker_id)
    if worker is None:
        raise NotFoundProblem("That worker is not part of this organization")
    return await _worker_out(session, ctx, worker)


@router.patch(
    "/operations/fulfilment/workers/{worker_id}",
    response_model=WorkerOut,
    summary="Edit a worker's warehouse, active flag, skills or zone access",
    dependencies=[Entitled],
)
async def update_worker(
    worker_id: uuid.UUID,
    payload: WorkerPatch,
    session: SessionDep,
    ctx: WorkerRoster,
    request: Request,
) -> object:
    worker = await fulfilment_selectors.get_worker(session, ctx.organization_id, worker_id)
    if worker is None:
        raise NotFoundProblem("That worker is not part of this organization")
    updated = await fulfilment_services.update_worker(
        session,
        ctx,
        worker,
        warehouse_id=payload.warehouse_id,
        display_name=payload.display_name,
        is_active=payload.is_active,
        skills=payload.skills,
        zone_location_ids=payload.zone_location_ids,
        request=request,
    )
    return await _worker_out(session, ctx, updated)


@router.post(
    "/operations/fulfilment/workers/{worker_id}/presence",
    response_model=WorkerOut,
    summary="Set a worker online/offline — a worker may only set their own",
    dependencies=[Entitled],
)
async def set_worker_presence(
    worker_id: uuid.UUID,
    payload: WorkerPresenceIn,
    session: SessionDep,
    ctx: WorkerWrite,
    request: Request,
) -> object:
    worker = await fulfilment_selectors.get_worker(session, ctx.organization_id, worker_id)
    if worker is None:
        raise NotFoundProblem("That worker is not part of this organization")
    ctx.assert_can_touch(owner_id=worker.employee_id)
    await fulfilment_services.set_presence(
        session, ctx, worker, status=payload.status, request=request
    )
    return await _worker_out(session, ctx, worker)


# ------------------------------------------------------------------ fulfilment: pick tasks


async def _task_out(session: AsyncSession, ctx: ScopeContext, task) -> PickTaskOut:  # type: ignore[no-untyped-def]
    lines = await fulfilment_selectors.lines_for_task(session, ctx.organization_id, task.id)
    items = await order_selectors.items_for_lines(
        session, ctx.organization_id, [line.inventory_item_id for line in lines]
    )
    line_outs = [
        fulfilment_selectors.pick_task_line_out(
            line, sku=items[line.inventory_item_id].sku if line.inventory_item_id in items else ""
        )
        for line in lines
    ]
    offers = await fulfilment_selectors.offers_for_task(session, ctx.organization_id, task.id)
    exceptions = await fulfilment_selectors.exceptions_for_task(
        session, ctx.organization_id, task.id
    )
    return fulfilment_selectors.pick_task_out(
        task,
        lines=line_outs,
        offers=[fulfilment_selectors.assignment_offer_out(o) for o in offers],
        exceptions=[fulfilment_selectors.pick_exception_out(e) for e in exceptions],
    )


@router.get(
    "/operations/fulfilment/tasks",
    response_model=Page[PickTaskOut],
    summary="Pick tasks — the dispatcher board",
    dependencies=[Entitled],
)
async def list_tasks(
    session: SessionDep,
    ctx: TaskRead,
    params: PageQuery,
    task_status: Annotated[str | None, Query(alias="status")] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    order_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[PickTaskOut]:
    stmt = fulfilment_selectors.pick_tasks_stmt(
        ctx.organization_id, status=task_status, warehouse_id=warehouse_id, order_id=order_id
    )
    # N3 (security review): `ctx.apply` is the enforcement point, applied here rather than
    # inside `pick_tasks_stmt` itself — that selector is also called from
    # `app.fulfilment.services` with whichever permission's `ScopeContext` the calling *action*
    # holds (often a worker's `task.execute:OWN`), which must always see every task on an order
    # regardless of that ctx's own scope; only this list read is a caller-facing list of tasks in
    # the `task.read` sense. No permission in this catalogue ever grants `task.read` below `ORG`,
    # so `owner_col=None` — `Scope.OWN`, if ever reached despite `TaskRead`'s own floor, resolves
    # to zero rows here, never the whole org's.
    stmt = ctx.apply(stmt, owner_col=None)
    rows, total = await paginate(session, stmt, params)
    task_ids = [t.id for t in rows]
    lines_map = await fulfilment_selectors.lines_map_for_tasks(
        session, ctx.organization_id, task_ids
    )
    item_ids = [line.inventory_item_id for lines in lines_map.values() for line in lines]
    items = await order_selectors.items_for_lines(session, ctx.organization_id, item_ids)
    offers_map = await fulfilment_selectors.offers_map_for_tasks(
        session, ctx.organization_id, task_ids
    )
    exceptions_map = await fulfilment_selectors.exceptions_map_for_tasks(
        session, ctx.organization_id, task_ids
    )
    out = [
        fulfilment_selectors.pick_task_out(
            task,
            lines=[
                fulfilment_selectors.pick_task_line_out(
                    line,
                    sku=items[line.inventory_item_id].sku
                    if line.inventory_item_id in items
                    else "",
                )
                for line in lines_map.get(task.id, [])
            ],
            offers=[
                fulfilment_selectors.assignment_offer_out(o) for o in offers_map.get(task.id, [])
            ],
            exceptions=[
                fulfilment_selectors.pick_exception_out(e) for e in exceptions_map.get(task.id, [])
            ],
        )
        for task in rows
    ]
    return Page.build(out, total=total, params=params)


@router.get(
    "/operations/fulfilment/tasks/{task_id}",
    response_model=PickTaskOut,
    summary="One pick task, with its lines, offer history and exceptions",
    dependencies=[Entitled],
)
async def read_task(task_id: uuid.UUID, session: SessionDep, ctx: TaskRead) -> PickTaskOut:
    task = await fulfilment_selectors.get_pick_task(session, ctx.organization_id, task_id)
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks",
    response_model=list[PickTaskOut],
    status_code=status.HTTP_201_CREATED,
    summary="Derive an accepted order's pick tasks (moves it ACCEPTED -> IN_FULFILMENT)",
    dependencies=[Entitled],
)
async def create_tasks(
    payload: CreatePickTasksIn, session: SessionDep, ctx: TaskAssign, request: Request
) -> list[PickTaskOut]:
    tasks = await fulfilment_services.create_pick_tasks_for_order(
        session,
        ctx,
        order_id=payload.order_id,
        order_version=payload.order_version,
        request=request,
    )
    return [await _task_out(session, ctx, t) for t in tasks]


@router.post(
    "/operations/fulfilment/tasks/{task_id}/offers",
    response_model=AssignmentOfferOut,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Offer a task to a worker — manual assignment, and the override path for a live offer "
        "auto-assignment already made (needs task.override)"
    ),
    dependencies=[Entitled],
)
async def offer_task(
    task_id: uuid.UUID, payload: OfferIn, session: SessionDep, ctx: TaskAssign, request: Request
) -> AssignmentOfferOut:
    task = await fulfilment_selectors.get_pick_task(
        session, ctx.organization_id, task_id, for_update=True
    )
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    # N4 (security review) shape — see `cancel_task`'s own comment: `.has(...)` is true for a
    # grant at *any* scope, but `task.override` is a stronger, org-wide permission, so this checks
    # the scope itself rather than mere possession.
    has_override = ctx.principal.scope_for("operations.fulfilment.task.override") == Scope.ORG
    offer = await fulfilment_services.offer_task(
        session,
        ctx,
        task,
        worker_id=payload.worker_id,
        has_override=has_override,
        request=request,
    )
    return fulfilment_selectors.assignment_offer_out(offer)


@router.post(
    "/operations/fulfilment/offers/{offer_id}/accept",
    response_model=AssignmentOfferOut,
    summary="Accept an offer — the exact ADR-0025 §3 conditional update; a zero-row update is 409",
    dependencies=[Entitled],
)
async def accept_offer(
    offer_id: uuid.UUID, session: SessionDep, ctx: TaskExecute, request: Request
) -> AssignmentOfferOut:
    offer = await fulfilment_services.accept_offer(session, ctx, offer_id, request=request)
    return fulfilment_selectors.assignment_offer_out(offer)


@router.post(
    "/operations/fulfilment/offers/{offer_id}/decline",
    response_model=AssignmentOfferOut,
    summary="Decline an offer",
    dependencies=[Entitled],
)
async def decline_offer(
    offer_id: uuid.UUID, session: SessionDep, ctx: TaskExecute, request: Request
) -> AssignmentOfferOut:
    offer = await fulfilment_services.decline_offer(session, ctx, offer_id, request=request)
    return fulfilment_selectors.assignment_offer_out(offer)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/release",
    response_model=PickTaskOut,
    summary="Hand an accepted task back before starting it",
    dependencies=[Entitled],
)
async def release_task(
    task_id: uuid.UUID,
    payload: TaskReleaseIn,
    session: SessionDep,
    ctx: TaskExecute,
    request: Request,
) -> PickTaskOut:
    task = await fulfilment_selectors.get_pick_task(
        session, ctx.organization_id, task_id, for_update=True
    )
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    await fulfilment_services.release_task(session, ctx, task, request=request)
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/scan",
    response_model=PickTaskOut,
    summary="Record a barcode scan (the first one starts the task)",
    dependencies=[Entitled],
)
async def scan_task(
    task_id: uuid.UUID, payload: ScanIn, session: SessionDep, ctx: TaskExecute, request: Request
) -> PickTaskOut:
    task = await fulfilment_selectors.get_pick_task(
        session, ctx.organization_id, task_id, for_update=True
    )
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    await fulfilment_services.record_scan(
        session,
        ctx,
        task,
        barcode=payload.barcode,
        location_id=payload.location_id,
        inventory_item_id=payload.inventory_item_id,
        request=request,
    )
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/lines/{line_id}/pick",
    response_model=PickTaskOut,
    summary="Confirm a pick line — transfers the reservation; a short pick raises an exception",
    dependencies=[Entitled],
)
async def confirm_pick(
    task_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: PickConfirmIn,
    session: SessionDep,
    ctx: TaskExecute,
    request: Request,
) -> PickTaskOut:
    task, _line = await fulfilment_services.confirm_pick(
        session,
        ctx,
        task_id,
        line_id,
        quantity=payload.quantity,
        reason=payload.reason,
        note=payload.note,
        request=request,
    )
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/exceptions/{exception_id}/resolve",
    response_model=PickTaskOut,
    summary="Resolve an exception and resume picking",
    dependencies=[Entitled],
)
async def resolve_exception(
    task_id: uuid.UUID,
    exception_id: uuid.UUID,
    payload: ExceptionResolveIn,
    session: SessionDep,
    ctx: TaskExecute,
    request: Request,
) -> PickTaskOut:
    task, _exception = await fulfilment_services.resolve_exception(
        session, ctx, task_id, exception_id, note=payload.note, request=request
    )
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/cancel",
    response_model=PickTaskOut,
    summary="Cancel a task (in-flight tasks additionally need task.override)",
    dependencies=[Entitled],
)
async def cancel_task(
    task_id: uuid.UUID,
    payload: TaskCancelIn,
    session: SessionDep,
    ctx: TaskAssign,
    request: Request,
) -> PickTaskOut:
    task = await fulfilment_selectors.get_pick_task(
        session, ctx.organization_id, task_id, for_update=True
    )
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    # N4 (security review): `.has(...)` is true for a grant at *any* scope; `task.override` is
    # the stronger, org-wide permission ADR-0025 §2 describes ("a stronger permission on edges
    # that ARE in the map"), so this must check the scope itself, the same way `close`'s own
    # `TaskOverride` dependency (`minimum=Scope.ORG`) already floors it.
    # N4 (security review): `.has(...)` is true for a grant at *any* scope; `task.override` is
    # the stronger, org-wide permission ADR-0025 §2 describes ("a stronger permission on edges
    # that ARE in the map"), so this must check the scope itself, the same way `close`'s own
    # `TaskOverride` dependency (`minimum=Scope.ORG`) already floors it.
    has_override = ctx.principal.scope_for("operations.fulfilment.task.override") == Scope.ORG
    await fulfilment_services.cancel_task(
        session, ctx, task, reason=payload.reason, has_override=has_override, request=request
    )
    return await _task_out(session, ctx, task)


@router.post(
    "/operations/fulfilment/tasks/{task_id}/close",
    response_model=PickTaskOut,
    summary="Force-close a picked task (override — F3 has no automatic close path yet)",
    dependencies=[Entitled],
)
async def close_task(
    task_id: uuid.UUID, session: SessionDep, ctx: TaskOverride, request: Request
) -> PickTaskOut:
    task = await fulfilment_selectors.get_pick_task(
        session, ctx.organization_id, task_id, for_update=True
    )
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    await fulfilment_services.close_task(session, ctx, task, request=request)
    return await _task_out(session, ctx, task)


# ------------------------------------------------------------------ fulfilment: packing


@router.get(
    "/operations/fulfilment/packaging-materials",
    response_model=Page[PackagingMaterialOut],
    summary="Packaging materials (box sizes, weights)",
    dependencies=[Entitled],
)
async def list_packaging_materials(
    session: SessionDep, ctx: PackRead, params: PageQuery
) -> Page[PackagingMaterialOut]:
    stmt = fulfilment_selectors.packaging_materials_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [fulfilment_selectors.packaging_material_out(m) for m in rows], total=total, params=params
    )


@router.post(
    "/operations/fulfilment/packaging-materials",
    response_model=PackagingMaterialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a packaging material",
    dependencies=[Entitled],
)
async def create_packaging_material(
    payload: PackagingMaterialIn, session: SessionDep, ctx: PackExecute, request: Request
) -> PackagingMaterialOut:
    material = await fulfilment_services.create_packaging_material(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        length_mm=payload.length_mm,
        width_mm=payload.width_mm,
        height_mm=payload.height_mm,
        max_weight_grams=payload.max_weight_grams,
        tare_weight_grams=payload.tare_weight_grams,
        is_active=payload.is_active,
        request=request,
    )
    return fulfilment_selectors.packaging_material_out(material)


async def _package_out(session: AsyncSession, ctx: ScopeContext, package) -> PackageOut:  # type: ignore[no-untyped-def]
    lines = await fulfilment_selectors.lines_for_package(session, ctx.organization_id, package.id)
    items = await order_selectors.items_for_lines(
        session, ctx.organization_id, [line.inventory_item_id for line in lines]
    )
    return fulfilment_selectors.package_out(
        package,
        lines=[
            fulfilment_selectors.package_line_out(
                line,
                sku=items[line.inventory_item_id].sku if line.inventory_item_id in items else "",
            )
            for line in lines
        ],
    )


@router.get(
    "/operations/fulfilment/packages",
    response_model=Page[PackageOut],
    summary="Packages",
    dependencies=[Entitled],
)
async def list_packages(
    session: SessionDep,
    ctx: PackRead,
    params: PageQuery,
    order_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[PackageOut]:
    stmt = fulfilment_selectors.packages_stmt(ctx, order_id=order_id)
    rows, total = await paginate(session, stmt, params)
    out = [await _package_out(session, ctx, p) for p in rows]
    return Page.build(out, total=total, params=params)


@router.get(
    "/operations/fulfilment/packages/{package_id}",
    response_model=PackageOut,
    summary="One package, with its lines",
    dependencies=[Entitled],
)
async def read_package(package_id: uuid.UUID, session: SessionDep, ctx: PackRead) -> PackageOut:
    package = await fulfilment_selectors.get_package(session, ctx.organization_id, package_id)
    if package is None:
        raise NotFoundProblem("That package is not part of this organization")
    return await _package_out(session, ctx, package)


@router.post(
    "/operations/fulfilment/packages",
    response_model=PackageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm a package: posts the staging -> pack/dock relocation in one call",
    dependencies=[Entitled],
)
async def confirm_package(
    payload: PackageIn, session: SessionDep, ctx: PackExecute, request: Request
) -> PackageOut:
    package = await fulfilment_services.confirm_package(
        session,
        ctx,
        order_id=payload.order_id,
        packaging_material_id=payload.packaging_material_id,
        destination_location_id=payload.destination_location_id,
        weight_grams=payload.weight_grams,
        lines=payload.lines,
        request=request,
    )
    return await _package_out(session, ctx, package)


# ------------------------------------------------------------------ dispatch (ADR-0024 §6;
# `docs/fulfilment/plan.md` F5)


@router.get(
    "/operations/dispatch/shipments",
    response_model=Page[ShipmentOut],
    summary="Shipments, filterable by order, warehouse and status",
    dependencies=[Entitled],
)
async def list_shipments(
    session: SessionDep,
    ctx: DispatchRead,
    params: PageQuery,
    order_id: Annotated[uuid.UUID | None, Query()] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    shipment_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[ShipmentOut]:
    stmt = dispatch_selectors.shipments_stmt(
        ctx, order_id=order_id, warehouse_id=warehouse_id, status=shipment_status
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [dispatch_selectors.shipment_out(s) for s in rows], total=total, params=params
    )


@router.get(
    "/operations/dispatch/shipments/{shipment_id}",
    response_model=ShipmentOut,
    summary="One shipment",
    dependencies=[Entitled],
)
async def read_shipment(
    shipment_id: uuid.UUID, session: SessionDep, ctx: DispatchRead
) -> ShipmentOut:
    shipment = await dispatch_selectors.get_shipment(session, ctx.organization_id, shipment_id)
    if shipment is None:
        raise NotFoundProblem("That shipment is not part of this organization")
    return dispatch_selectors.shipment_out(shipment)


@router.post(
    "/operations/dispatch/shipments",
    response_model=ShipmentOut,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Hand an order to a carrier once the billing gate passes "
        "(also confirms delivery, ADR-0025 §1) — the delivery/courier counterpart to "
        "POST /orders/{id}/hand-over"
    ),
    dependencies=[Entitled],
)
async def dispatch_order(
    payload: ShipmentIn, session: SessionDep, ctx: DispatchWrite, request: Request
) -> ShipmentOut:
    order = await order_selectors.get_order(
        session, ctx.organization_id, payload.order_id, for_update=True
    )
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    shipment = await dispatch_services.dispatch_order(
        session,
        ctx,
        order,
        expected_version=payload.order_version,
        carrier_name=payload.carrier_name,
        tracking_reference=payload.tracking_reference,
        payment_confirmed=payload.payment_confirmed,
        request=request,
    )
    return dispatch_selectors.shipment_out(shipment)


async def _transition_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    to_status: str,
    reason: str = "",
    tracking_reference: str | None = None,
    request: Request,
) -> ShipmentOut:
    shipment = await dispatch_selectors.get_shipment(
        session, ctx.organization_id, shipment_id, for_update=True
    )
    if shipment is None:
        raise NotFoundProblem("That shipment is not part of this organization")
    await dispatch_services.transition_shipment(
        session,
        ctx,
        shipment,
        to_status=to_status,
        reason=reason,
        tracking_reference=tracking_reference,
        request=request,
    )
    return dispatch_selectors.shipment_out(shipment)


@router.post(
    "/operations/dispatch/shipments/{shipment_id}/in-transit",
    response_model=ShipmentOut,
    summary="Mark a shipment in transit (or retry a failed attempt)",
    dependencies=[Entitled],
)
async def shipment_in_transit(
    shipment_id: uuid.UUID,
    payload: ShipmentAdvanceIn,
    session: SessionDep,
    ctx: DispatchWrite,
    request: Request,
) -> ShipmentOut:
    return await _transition_shipment(
        shipment_id,
        session,
        ctx,
        to_status="IN_TRANSIT",
        tracking_reference=payload.tracking_reference,
        request=request,
    )


@router.post(
    "/operations/dispatch/shipments/{shipment_id}/deliver",
    response_model=ShipmentOut,
    summary="Confirm delivery",
    dependencies=[Entitled],
)
async def shipment_deliver(
    shipment_id: uuid.UUID,
    payload: ShipmentAdvanceIn,
    session: SessionDep,
    ctx: DispatchWrite,
    request: Request,
) -> ShipmentOut:
    return await _transition_shipment(
        shipment_id,
        session,
        ctx,
        to_status="DELIVERED",
        tracking_reference=payload.tracking_reference,
        request=request,
    )


@router.post(
    "/operations/dispatch/shipments/{shipment_id}/fail",
    response_model=ShipmentOut,
    summary="Record a failed delivery attempt (reason required)",
    dependencies=[Entitled],
)
async def shipment_fail(
    shipment_id: uuid.UUID,
    payload: ShipmentRefusalIn,
    session: SessionDep,
    ctx: DispatchWrite,
    request: Request,
) -> ShipmentOut:
    return await _transition_shipment(
        shipment_id, session, ctx, to_status="FAILED", reason=payload.reason, request=request
    )


@router.post(
    "/operations/dispatch/shipments/{shipment_id}/return",
    response_model=ShipmentOut,
    summary="Record a shipment returned to the business (reason required)",
    dependencies=[Entitled],
)
async def shipment_return(
    shipment_id: uuid.UUID,
    payload: ShipmentRefusalIn,
    session: SessionDep,
    ctx: DispatchWrite,
    request: Request,
) -> ShipmentOut:
    return await _transition_shipment(
        shipment_id, session, ctx, to_status="RETURNED", reason=payload.reason, request=request
    )


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
