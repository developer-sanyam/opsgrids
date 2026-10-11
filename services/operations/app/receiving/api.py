"""HTTP surface for receiving (inventory Phase 2): purchase requisitions, goods receipts, the
receiving view of a purchase order, and per-item unit conversions.

Mounted by `app.main` ahead of `app.api.routes.router` (see `app.inventory.api` for why order
matters there). Every route declares its permission and the `operations` entitlement and goes
through the tenant-bound session; a row named by id is fetched with the organization filter, so
another tenant's id is a 404, never a 403. Requisitions have an OWN reading (the ones I raised) —
`ctx.apply` on lists, `assert_can_touch` on a named row. Receipts and conversions are org-wide.

Posting a goods receipt needs **both** `procurement.receipt.write` (the document) and
`inventory.stock.receive` (putting stock on the books), floored at ORG, and an `Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app import selectors as po_selectors
from app.api.routes import (
    Entitled,
    InventoryRead,
    InventoryWrite,
    PoWrite,
    SessionDep,
)
from app.inventory import selectors as inv_selectors
from app.inventory.pagination import CursorPage, CursorQuery, keyset_page
from app.models import GoodsReceipt
from app.receiving import conversions, receipts, requisitions, selectors
from app.receiving.schemas import (
    GoodsReceiptIn,
    GoodsReceiptOut,
    GoodsReceiptSummaryOut,
    ReceivingContextOut,
    RequisitionConvertIn,
    RequisitionDecisionIn,
    RequisitionIn,
    RequisitionOut,
    RequisitionPatch,
    RequisitionRejectIn,
    RequisitionStatus,
    RequisitionSummaryOut,
    UomConversionIn,
    UomConversionOut,
    UomConversionPatch,
)
from app.schemas import PurchaseOrderOut
from platform_core.auth import Scope, ScopeContext, require
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["receiving"])

#: A receipt posts stock, so a retry must be a replay: the header is not optional here.
RequiredIdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, description="Required."),
]

ReqRead = Annotated[ScopeContext, Depends(require("operations.procurement.requisition.read"))]
ReqWrite = Annotated[ScopeContext, Depends(require("operations.procurement.requisition.write"))]
#: A commitment step, distinct from raising one: floored at ORG so a requester can never approve
#: their own request.
ReqApprove = Annotated[
    ScopeContext,
    Depends(require("operations.procurement.requisition.approve", minimum=Scope.ORG)),
]
ReceiptRead = Annotated[
    ScopeContext, Depends(require("operations.procurement.receipt.read", minimum=Scope.ORG))
]
ReceiptWrite = Annotated[
    ScopeContext, Depends(require("operations.procurement.receipt.write", minimum=Scope.ORG))
]
StockReceive = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.receive", minimum=Scope.ORG))
]


# ------------------------------------------------------------------ unit conversions


@router.get(
    "/operations/inventory/{item_id}/uom-conversions",
    response_model=list[UomConversionOut],
    summary="An item's own unit conversions (1 box = 12 pcs)",
    dependencies=[Entitled],
)
async def list_uom_conversions(
    item_id: uuid.UUID, session: SessionDep, ctx: InventoryRead
) -> list[UomConversionOut]:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    return await selectors.list_conversions(session, ctx.organization_id, item)


@router.post(
    "/operations/inventory/{item_id}/uom-conversions",
    response_model=UomConversionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record that one box (or carton, bundle, ...) of this item is n of its own unit",
    dependencies=[Entitled],
)
async def create_uom_conversion(
    item_id: uuid.UUID,
    payload: UomConversionIn,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> UomConversionOut:
    item = await inv_selectors.get_inventory_item(
        session, ctx.organization_id, item_id, for_update=True
    )
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    row, uom = await conversions.create_conversion(session, ctx, item, payload, request=request)
    return selectors.conversion_out(row, uom, item)


@router.patch(
    "/operations/inventory/uom-conversions/{conversion_id}",
    response_model=UomConversionOut,
    summary="Change a conversion's ratio",
    dependencies=[Entitled],
)
async def update_uom_conversion(
    conversion_id: uuid.UUID,
    payload: UomConversionPatch,
    session: SessionDep,
    ctx: InventoryWrite,
    request: Request,
) -> UomConversionOut:
    found = await selectors.get_conversion(
        session, ctx.organization_id, conversion_id, for_update=True
    )
    if found is None:
        raise NotFoundProblem("That unit conversion is not part of this organization")
    row, uom, item = found
    row = await conversions.update_conversion(
        session, ctx, row, uom, item, payload, request=request
    )
    return selectors.conversion_out(row, uom, item)


@router.delete(
    "/operations/inventory/uom-conversions/{conversion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a conversion (refused while an open purchase order orders in that unit)",
    dependencies=[Entitled],
)
async def delete_uom_conversion(
    conversion_id: uuid.UUID, session: SessionDep, ctx: InventoryWrite, request: Request
) -> Response:
    found = await selectors.get_conversion(
        session, ctx.organization_id, conversion_id, for_update=True
    )
    if found is None:
        raise NotFoundProblem("That unit conversion is not part of this organization")
    row, uom, item = found
    await conversions.delete_conversion(session, ctx, row, uom, item, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ purchase requisitions


async def _requisition(session: SessionDep, ctx: ScopeContext, requisition_id: uuid.UUID):  # type: ignore[no-untyped-def]
    req = await selectors.get_requisition(session, ctx.organization_id, requisition_id)
    if req is None:
        raise NotFoundProblem("That requisition is not part of this organization")
    ctx.assert_can_touch(owner_id=req.requested_by_id)
    return req


@router.get(
    "/operations/purchase-requisitions",
    response_model=Page[RequisitionSummaryOut],
    summary="Purchase requisitions, newest first",
    dependencies=[Entitled],
)
async def list_requisitions(
    session: SessionDep,
    ctx: ReqRead,
    params: PageQuery,
    req_status: Annotated[RequisitionStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(description="Number or requester")] = None,
) -> Page[RequisitionSummaryOut]:
    stmt = selectors.requisitions_stmt(ctx, status=req_status, q=q)
    rows, total = await paginate(session, stmt, params)
    items = await selectors.requisition_summaries(session, ctx.organization_id, rows)
    return Page.build(items, total=total, params=params)


@router.post(
    "/operations/purchase-requisitions",
    response_model=RequisitionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a purchase requisition (starts as a draft)",
    dependencies=[Entitled],
)
async def create_requisition(
    payload: RequisitionIn, session: SessionDep, ctx: ReqWrite, request: Request
) -> RequisitionOut:
    req = await requisitions.create_requisition(session, ctx, payload, request=request)
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.get(
    "/operations/purchase-requisitions/{requisition_id}",
    response_model=RequisitionOut,
    summary="One requisition, with its lines",
    dependencies=[Entitled],
)
async def read_requisition(
    requisition_id: uuid.UUID, session: SessionDep, ctx: ReqRead
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.patch(
    "/operations/purchase-requisitions/{requisition_id}",
    response_model=RequisitionOut,
    summary="Edit a draft requisition (and, optionally, replace its lines)",
    dependencies=[Entitled],
)
async def update_requisition(
    requisition_id: uuid.UUID,
    payload: RequisitionPatch,
    session: SessionDep,
    ctx: ReqWrite,
    request: Request,
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    req = await requisitions.update_requisition(session, ctx, req, payload, request=request)
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.post(
    "/operations/purchase-requisitions/{requisition_id}/submit",
    response_model=RequisitionOut,
    summary="Submit a draft requisition for approval",
    dependencies=[Entitled],
)
async def submit_requisition(
    requisition_id: uuid.UUID, session: SessionDep, ctx: ReqWrite, request: Request
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    req = await requisitions.submit_requisition(session, ctx, req, request=request)
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.post(
    "/operations/purchase-requisitions/{requisition_id}/approve",
    response_model=RequisitionOut,
    summary="Approve a submitted requisition",
    dependencies=[Entitled],
)
async def approve_requisition(
    requisition_id: uuid.UUID,
    payload: RequisitionDecisionIn,
    session: SessionDep,
    ctx: ReqApprove,
    request: Request,
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    req = await requisitions.approve_requisition(
        session, ctx, req, note=payload.note, request=request
    )
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.post(
    "/operations/purchase-requisitions/{requisition_id}/reject",
    response_model=RequisitionOut,
    summary="Reject a submitted requisition (a reason is required)",
    dependencies=[Entitled],
)
async def reject_requisition(
    requisition_id: uuid.UUID,
    payload: RequisitionRejectIn,
    session: SessionDep,
    ctx: ReqApprove,
    request: Request,
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    req = await requisitions.reject_requisition(
        session, ctx, req, reason=payload.reason, request=request
    )
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.post(
    "/operations/purchase-requisitions/{requisition_id}/cancel",
    response_model=RequisitionOut,
    summary="Cancel a requisition that has not been converted",
    dependencies=[Entitled],
)
async def cancel_requisition(
    requisition_id: uuid.UUID,
    payload: RequisitionDecisionIn,
    session: SessionDep,
    ctx: ReqWrite,
    request: Request,
) -> RequisitionOut:
    req = await _requisition(session, ctx, requisition_id)
    req = await requisitions.cancel_requisition(
        session, ctx, req, reason=payload.note, request=request
    )
    return await selectors.requisition_out(session, ctx.organization_id, req)


@router.post(
    "/operations/purchase-requisitions/{requisition_id}/convert",
    response_model=PurchaseOrderOut,
    summary="Convert an approved requisition into a draft purchase order",
    dependencies=[Entitled],
)
async def convert_requisition(
    requisition_id: uuid.UUID,
    payload: RequisitionConvertIn,
    session: SessionDep,
    ctx: ReqWrite,
    po_ctx: PoWrite,
    request: Request,
) -> PurchaseOrderOut:
    """Needs `requisition.write` **and** `purchase_order.write`: this is a buyer turning an
    approval into a commitment-to-be, and it creates a purchase order."""
    req = await _requisition(session, ctx, requisition_id)
    _, po = await requisitions.convert_requisition(session, po_ctx, req, payload, request=request)
    lines = await po_selectors.lines_for(session, ctx.organization_id, po.id)
    return po_selectors.purchase_order_out(po, lines=lines)


# ------------------------------------------------------------------ goods receipts


@router.get(
    "/operations/purchase-orders/{po_id}/receiving",
    response_model=ReceivingContextOut,
    summary="What can still be received against a purchase order, per line",
    dependencies=[Entitled],
)
async def read_receiving_context(
    po_id: uuid.UUID, session: SessionDep, ctx: ReceiptRead
) -> ReceivingContextOut:
    po = await po_selectors.get_purchase_order(session, ctx.organization_id, po_id)
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    return await selectors.receiving_context(session, ctx.organization_id, po)


@router.post(
    "/operations/goods-receipts",
    response_model=GoodsReceiptOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post a delivery against a purchase order (partial, short or over within tolerance)",
    dependencies=[Entitled],
)
async def post_goods_receipt(
    payload: GoodsReceiptIn,
    response: Response,
    session: SessionDep,
    ctx: ReceiptWrite,
    stock_ctx: StockReceive,
    request: Request,
    idempotency_key: RequiredIdempotencyKey,
) -> GoodsReceiptOut:
    del stock_ctx  # its job is the permission check
    receipt, _move, replayed, _entries = await receipts.post_goods_receipt(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return await selectors.receipt_out(session, ctx.organization_id, receipt, replayed=replayed)


@router.get(
    "/operations/goods-receipts",
    response_model=CursorPage[GoodsReceiptSummaryOut],
    summary="Goods receipts, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_goods_receipts(
    session: SessionDep,
    ctx: ReceiptRead,
    params: CursorQuery,
    purchase_order_id: Annotated[uuid.UUID | None, Query()] = None,
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=80, description="Number or delivery note")] = None,
) -> CursorPage[GoodsReceiptSummaryOut]:
    stmt = selectors.receipts_stmt(
        ctx, purchase_order_id=purchase_order_id, supplier_id=supplier_id, q=q
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=GoodsReceipt.created_at,
        id_col=GoodsReceipt.id,
        sort_of=lambda r: r.created_at,
        id_of=lambda r: r.id,
    )
    items = await selectors.receipt_summaries(session, ctx.organization_id, rows)
    return CursorPage(items=items, next_cursor=next_cursor)


@router.get(
    "/operations/goods-receipts/{receipt_id}",
    response_model=GoodsReceiptOut,
    summary="One goods receipt, with its lines, lots and rolls",
    dependencies=[Entitled],
)
async def read_goods_receipt(
    receipt_id: uuid.UUID, session: SessionDep, ctx: ReceiptRead
) -> GoodsReceiptOut:
    receipt = await selectors.get_receipt(session, ctx.organization_id, receipt_id)
    if receipt is None:
        raise NotFoundProblem("That goods receipt is not part of this organization")
    return await selectors.receipt_out(session, ctx.organization_id, receipt)
