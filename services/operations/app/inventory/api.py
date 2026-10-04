"""HTTP surface for lots, rolls, stock operations and the stock / ledger / trace reads (inventory
Phase 1, I3-I5).

Mounted by `app.main` **ahead of** `app.api.routes.router`: that router owns the catch-all
`/operations/inventory/{item_id}` routes, and a static segment (`lots`, `rolls`, `stock`,
`ledger`) registered after it would be matched as an item id and answered 422.

Every route declares its permission (`require(...)`, floored at ORG — stock is shared
organization data, no OWN reading exists), the `operations` entitlement, and goes through the
tenant-bound session. Every stock-moving write takes an `Idempotency-Key` and goes through
`app.inventory.ledger.post_move`. A row named by id is always fetched with the organization
filter, so another tenant's id is a 404, never a 403.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.routes import Entitled, IdempotencyKey, SessionDep
from app.inventory import services as inv_services
from app.inventory import stock_ops
from app.inventory import tracking_selectors as sel
from app.inventory import tracking_services as tracking
from app.inventory.pagination import CursorPage, CursorQuery, keyset_page
from app.inventory.tracking_schemas import (
    LedgerRowOut,
    LotGenealogyIn,
    LotGenealogyOut,
    LotIn,
    LotOut,
    LotPatch,
    LotQualityStatus,
    LotTraceOut,
    OpeningStockIn,
    RollEventOut,
    RollIn,
    RollMergeIn,
    RollMergeOut,
    RollOut,
    RollPatch,
    RollSplitIn,
    RollSplitOut,
    RollState,
    RollTraceOut,
    StockMoveOut,
    StockRowOut,
    StockStatusChangeIn,
    StockTransferIn,
)
from app.models import (
    InventoryBalance,
    LedgerEntry,
    Lot,
    LotGenealogy,
    Roll,
    RollEvent,
)
from app.schemas import StockStatus
from platform_core.auth import Scope, ScopeContext, require
from platform_core.errors import NotFoundProblem

router = APIRouter(tags=["inventory"])

LotWrite = Annotated[
    ScopeContext, Depends(require("operations.inventory.lot.write", minimum=Scope.ORG))
]
RollWrite = Annotated[
    ScopeContext, Depends(require("operations.inventory.roll.write", minimum=Scope.ORG))
]
StockRead = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.read", minimum=Scope.ORG))
]
StockTransfer = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.transfer", minimum=Scope.ORG))
]
#: Opening stock is a stock adjustment by another name: it creates quantity out of nothing.
StockAdjust = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.adjust", minimum=Scope.ORG))
]
LocationWrite = Annotated[
    ScopeContext, Depends(require("operations.inventory.location.write", minimum=Scope.ORG))
]


def _was_explicit(payload: object) -> bool:
    """Whether the client sent `occurred_on`, or it is the parse-time default (kept out of the
    idempotency hash so a retry across midnight is still the same request)."""
    return "occurred_on" in getattr(payload, "model_fields_set", set())


def _status_for(response: Response, replayed: bool) -> None:
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED


# ------------------------------------------------------------------ lots


@router.post(
    "/operations/inventory/lots",
    response_model=LotOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a lot",
    dependencies=[Entitled],
)
async def create_lot(
    payload: LotIn, session: SessionDep, ctx: LotWrite, request: Request
) -> LotOut:
    lot = await tracking.create_lot(session, ctx, payload, request=request)
    row = await sel.get_lot_row(session, ctx.organization_id, lot.id)
    assert row is not None
    return sel.lot_out(row)


@router.get(
    "/operations/inventory/lots",
    response_model=CursorPage[LotOut],
    summary="Lots, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_lots(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    quality_status: Annotated[LotQualityStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=60, description="Lot number prefix")] = None,
    expires_before: Annotated[date | None, Query()] = None,
) -> CursorPage[LotOut]:
    stmt = sel.lots_stmt(
        ctx, item_id=item_id, quality_status=quality_status, q=q, expires_before=expires_before
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=Lot.created_at,
        id_col=Lot.id,
        sort_of=lambda r: r[0].created_at,
        id_of=lambda r: r[0].id,
        unique_rows=False,
    )
    return CursorPage(items=[sel.lot_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/operations/inventory/lots/{lot_id}",
    response_model=LotOut,
    summary="One lot",
    dependencies=[Entitled],
)
async def read_lot(lot_id: uuid.UUID, session: SessionDep, ctx: StockRead) -> LotOut:
    row = await sel.get_lot_row(session, ctx.organization_id, lot_id)
    if row is None:
        raise NotFoundProblem("That lot is not part of this organization")
    return sel.lot_out(row)


@router.patch(
    "/operations/inventory/lots/{lot_id}",
    response_model=LotOut,
    summary="Update a lot's supplier number, dates, quality status or attributes",
    dependencies=[Entitled],
)
async def update_lot(
    lot_id: uuid.UUID, payload: LotPatch, session: SessionDep, ctx: LotWrite, request: Request
) -> LotOut:
    lot = await sel.get_lot(session, ctx.organization_id, lot_id)
    if lot is None:
        raise NotFoundProblem("That lot is not part of this organization")
    await tracking.update_lot(session, ctx, lot, payload, request=request)
    row = await sel.get_lot_row(session, ctx.organization_id, lot_id)
    assert row is not None
    return sel.lot_out(row)


@router.post(
    "/operations/inventory/lots/{lot_id}/genealogy",
    response_model=LotGenealogyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record that this lot's material went into another lot",
    dependencies=[Entitled],
)
async def link_lots(
    lot_id: uuid.UUID,
    payload: LotGenealogyIn,
    session: SessionDep,
    ctx: LotWrite,
    request: Request,
) -> LotGenealogyOut:
    edge = await tracking.link_lots(session, ctx, lot_id, payload, request=request)
    return sel.genealogy_out(edge)


@router.get(
    "/operations/inventory/lots/{lot_id}/genealogy",
    response_model=CursorPage[LotGenealogyOut],
    summary="The genealogy edges touching a lot, newest first",
    dependencies=[Entitled],
)
async def list_lot_genealogy(
    lot_id: uuid.UUID, session: SessionDep, ctx: StockRead, params: CursorQuery
) -> CursorPage[LotGenealogyOut]:
    if await sel.get_lot(session, ctx.organization_id, lot_id) is None:
        raise NotFoundProblem("That lot is not part of this organization")
    rows, next_cursor = await keyset_page(
        session,
        sel.lot_genealogy_stmt(ctx, lot_id),
        params,
        sort_col=LotGenealogy.created_at,
        id_col=LotGenealogy.id,
        sort_of=lambda r: r.created_at,
        id_of=lambda r: r.id,
    )
    return CursorPage(items=[sel.genealogy_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/operations/inventory/lots/{lot_id}/trace",
    response_model=LotTraceOut,
    summary="Backward and forward trace of a lot through the genealogy",
    dependencies=[Entitled],
)
async def trace_lot(lot_id: uuid.UUID, session: SessionDep, ctx: StockRead) -> LotTraceOut:
    row = await sel.get_lot_row(session, ctx.organization_id, lot_id)
    if row is None:
        raise NotFoundProblem("That lot is not part of this organization")
    return LotTraceOut(
        lot=sel.lot_out(row),
        ancestors=await sel.trace_lots(session, ctx.organization_id, lot_id, forward=False),
        descendants=await sel.trace_lots(session, ctx.organization_id, lot_id, forward=True),
    )


# ------------------------------------------------------------------ rolls


@router.post(
    "/operations/inventory/rolls",
    response_model=RollOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a roll (it holds no stock until an opening posts into it)",
    dependencies=[Entitled],
)
async def create_roll(
    payload: RollIn, session: SessionDep, ctx: RollWrite, request: Request
) -> RollOut:
    roll = await tracking.create_roll(session, ctx, payload, request=request)
    row = await sel.get_roll_row(session, ctx.organization_id, roll.id)
    assert row is not None
    return sel.roll_out(row)


@router.get(
    "/operations/inventory/rolls",
    response_model=CursorPage[RollOut],
    summary="Rolls, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_rolls(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    lot_id: Annotated[uuid.UUID | None, Query()] = None,
    state: Annotated[RollState | None, Query()] = None,
    shade: Annotated[str | None, Query(max_length=40)] = None,
    grade: Annotated[str | None, Query(max_length=20)] = None,
    q: Annotated[str | None, Query(max_length=60, description="Roll number prefix")] = None,
) -> CursorPage[RollOut]:
    stmt = sel.rolls_stmt(
        ctx, item_id=item_id, lot_id=lot_id, state=state, shade=shade, grade=grade, q=q
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=Roll.created_at,
        id_col=Roll.id,
        sort_of=lambda r: r[0].created_at,
        id_of=lambda r: r[0].id,
        unique_rows=False,
    )
    return CursorPage(items=[sel.roll_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/operations/inventory/rolls/{roll_id}",
    response_model=RollOut,
    summary="One roll",
    dependencies=[Entitled],
)
async def read_roll(roll_id: uuid.UUID, session: SessionDep, ctx: StockRead) -> RollOut:
    row = await sel.get_roll_row(session, ctx.organization_id, roll_id)
    if row is None:
        raise NotFoundProblem("That roll is not part of this organization")
    return sel.roll_out(row)


@router.patch(
    "/operations/inventory/rolls/{roll_id}",
    response_model=RollOut,
    summary="Re-measure or re-classify a roll",
    dependencies=[Entitled],
)
async def update_roll(
    roll_id: uuid.UUID, payload: RollPatch, session: SessionDep, ctx: RollWrite, request: Request
) -> RollOut:
    await tracking.update_roll(session, ctx, roll_id, payload, request=request)
    row = await sel.get_roll_row(session, ctx.organization_id, roll_id)
    assert row is not None
    return sel.roll_out(row)


@router.get(
    "/operations/inventory/rolls/{roll_id}/events",
    response_model=CursorPage[RollEventOut],
    summary="A roll's history, newest first",
    dependencies=[Entitled],
)
async def list_roll_events(
    roll_id: uuid.UUID, session: SessionDep, ctx: StockRead, params: CursorQuery
) -> CursorPage[RollEventOut]:
    if await sel.get_roll_row(session, ctx.organization_id, roll_id) is None:
        raise NotFoundProblem("That roll is not part of this organization")
    rows, next_cursor = await keyset_page(
        session,
        sel.roll_events_stmt(ctx, roll_id),
        params,
        sort_col=RollEvent.occurred_at,
        id_col=RollEvent.id,
        sort_of=lambda r: r.occurred_at,
        id_of=lambda r: r.id,
    )
    return CursorPage(items=[sel.roll_event_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/operations/inventory/rolls/{roll_id}/trace",
    response_model=RollTraceOut,
    summary="Backward and forward trace of a roll — parents, children, lot and the lot's origin",
    dependencies=[Entitled],
)
async def trace_roll(roll_id: uuid.UUID, session: SessionDep, ctx: StockRead) -> RollTraceOut:
    org_id = ctx.organization_id
    row = await sel.get_roll_row(session, org_id, roll_id)
    if row is None:
        raise NotFoundProblem("That roll is not part of this organization")
    roll = row[0]
    lot = None
    lot_ancestors = []
    if roll.lot_id is not None:
        lot_row = await sel.get_lot_row(session, org_id, roll.lot_id)
        lot = sel.lot_out(lot_row) if lot_row else None
        lot_ancestors = await sel.trace_lots(session, org_id, roll.lot_id, forward=False)
    return RollTraceOut(
        roll=sel.roll_out(row),
        lot=lot,
        ancestors=await sel.trace_rolls(session, org_id, roll_id, forward=False),
        descendants=await sel.trace_rolls(session, org_id, roll_id, forward=True),
        lot_ancestors=lot_ancestors,
    )


@router.post(
    "/operations/inventory/rolls/{roll_id}:split",
    response_model=RollSplitOut,
    status_code=status.HTTP_201_CREATED,
    summary="Split a roll into n rolls whose quantities add up exactly",
    dependencies=[Entitled],
)
async def split_roll(
    roll_id: uuid.UUID,
    payload: RollSplitIn,
    response: Response,
    session: SessionDep,
    ctx: RollWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> RollSplitOut:
    posted, parent_id, child_ids = await tracking.split_roll(
        session,
        ctx,
        roll_id,
        payload,
        idempotency_key=idempotency_key,
        occurred_on_explicit=_was_explicit(payload),
        request=request,
    )
    _status_for(response, posted.replayed)
    rows = await sel.roll_rows_by_ids(session, ctx.organization_id, [parent_id, *child_ids])
    return RollSplitOut(
        move=stock_ops.move_out(posted),
        parent=sel.roll_out(rows[parent_id]),
        children=[sel.roll_out(rows[c]) for c in child_ids],
    )


@router.post(
    "/operations/inventory/rolls:merge",
    response_model=RollMergeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Merge rolls of one item, lot and shade into one new roll",
    dependencies=[Entitled],
)
async def merge_rolls(
    payload: RollMergeIn,
    response: Response,
    session: SessionDep,
    ctx: RollWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> RollMergeOut:
    posted, result_id, source_ids = await tracking.merge_rolls(
        session,
        ctx,
        payload,
        idempotency_key=idempotency_key,
        occurred_on_explicit=_was_explicit(payload),
        request=request,
    )
    _status_for(response, posted.replayed)
    rows = await sel.roll_rows_by_ids(session, ctx.organization_id, [result_id, *source_ids])
    return RollMergeOut(
        move=stock_ops.move_out(posted),
        result=sel.roll_out(rows[result_id]),
        sources=[sel.roll_out(rows[s]) for s in source_ids],
    )


# ------------------------------------------------------------------ stock operations


@router.post(
    "/operations/inventory/stock/opening",
    response_model=StockMoveOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post opening stock per location, lot and roll",
    dependencies=[Entitled],
)
async def post_opening_stock(
    payload: OpeningStockIn,
    response: Response,
    session: SessionDep,
    ctx: StockAdjust,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> StockMoveOut:
    posted = await stock_ops.post_opening_stock(
        session,
        ctx,
        payload,
        idempotency_key=idempotency_key,
        occurred_on_explicit=_was_explicit(payload),
        request=request,
    )
    _status_for(response, posted.replayed)
    return stock_ops.move_out(posted)


@router.post(
    "/operations/inventory/stock/transfers",
    response_model=StockMoveOut,
    status_code=status.HTTP_201_CREATED,
    summary="Move stock bin to bin inside one warehouse",
    dependencies=[Entitled],
)
async def transfer_stock(
    payload: StockTransferIn,
    response: Response,
    session: SessionDep,
    ctx: StockTransfer,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> StockMoveOut:
    posted = await stock_ops.transfer_stock(
        session,
        ctx,
        payload,
        idempotency_key=idempotency_key,
        occurred_on_explicit=_was_explicit(payload),
        request=request,
    )
    _status_for(response, posted.replayed)
    return stock_ops.move_out(posted)


@router.post(
    "/operations/inventory/stock/status-changes",
    response_model=StockMoveOut,
    status_code=status.HTTP_201_CREATED,
    summary="Move stock between statuses where it sits (e.g. AVAILABLE -> QUALITY_HOLD)",
    dependencies=[Entitled],
)
async def change_stock_status(
    payload: StockStatusChangeIn,
    response: Response,
    session: SessionDep,
    ctx: StockTransfer,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> StockMoveOut:
    posted = await stock_ops.change_stock_status(
        session,
        ctx,
        payload,
        idempotency_key=idempotency_key,
        occurred_on_explicit=_was_explicit(payload),
        request=request,
    )
    _status_for(response, posted.replayed)
    return stock_ops.move_out(posted)


# ------------------------------------------------------------------ stock and ledger reads


@router.get(
    "/operations/inventory/stock",
    response_model=CursorPage[StockRowOut],
    summary="Stock by bucket — filter by item, warehouse, location, lot, roll or status",
    dependencies=[Entitled],
)
async def list_stock(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    location_id: Annotated[uuid.UUID | None, Query()] = None,
    lot_id: Annotated[uuid.UUID | None, Query()] = None,
    roll_id: Annotated[uuid.UUID | None, Query()] = None,
    stock_status: Annotated[StockStatus | None, Query(alias="status")] = None,
    include_empty: Annotated[bool, Query()] = False,
) -> CursorPage[StockRowOut]:
    stmt = sel.stock_stmt(
        ctx,
        item_id=item_id,
        warehouse_id=warehouse_id,
        location_id=location_id,
        lot_id=lot_id,
        roll_id=roll_id,
        status=stock_status,
        include_empty=include_empty,
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=InventoryBalance.id,
        id_col=InventoryBalance.id,
        sort_of=lambda r: str(r[0].id),
        id_of=lambda r: r[0].id,
        descending=False,
        unique_rows=False,
        parse_sort=uuid.UUID,
        sort_is_time=False,
    )
    return CursorPage(items=[sel.stock_row_out(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/operations/inventory/ledger",
    response_model=CursorPage[LedgerRowOut],
    summary="The organization-wide stock ledger, newest first",
    dependencies=[Entitled],
)
async def list_ledger(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    location_id: Annotated[uuid.UUID | None, Query()] = None,
    lot_id: Annotated[uuid.UUID | None, Query()] = None,
    roll_id: Annotated[uuid.UUID | None, Query()] = None,
    kind: Annotated[
        str | None,
        Query(pattern="^(opening|receipt|adjustment|count|relocation|issue)$"),
    ] = None,
    ledger_status: Annotated[StockStatus | None, Query(alias="status")] = None,
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
) -> CursorPage[LedgerRowOut]:
    stmt = sel.ledger_stmt(
        ctx,
        item_id=item_id,
        location_id=location_id,
        lot_id=lot_id,
        roll_id=roll_id,
        kind=kind,
        status=ledger_status,
        from_date=from_date,
        to_date=to_date,
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=LedgerEntry.created_at,
        id_col=LedgerEntry.id,
        sort_of=lambda r: r[0].created_at,
        id_of=lambda r: r[0].id,
        unique_rows=False,
    )
    return CursorPage(items=[sel.ledger_row_out(r) for r in rows], next_cursor=next_cursor)


# ------------------------------------------------------------------ locations


@router.delete(
    "/operations/locations/{location_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a location — refused while it holds stock or has ledger history",
    dependencies=[Entitled],
)
async def delete_location(
    location_id: uuid.UUID, session: SessionDep, ctx: LocationWrite, request: Request
) -> Response:
    await inv_services.delete_location(session, ctx, location_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
