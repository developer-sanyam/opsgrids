"""HTTP surface for inventory Phase 3 — transfers, reservations, adjustments, the in-transit
report and the approval thresholds (docs/inventory/movements.md).

Mounted by `app.main` **ahead of** `app.api.routes.router`, whose catch-all
`/operations/inventory/{item_id}` would otherwise swallow `transfers`, `reservations`,
`adjustments`, `in-transit` and `control-settings` as an item id and answer 422.

Every route declares its permission (`require(...)`, floored at ORG — transfers, reservations and
adjustments are shared organization stock), the `operations` entitlement, and goes through the
tenant-bound session. Every list takes its scope from `ctx.apply` and is keyset-paginated. A row
named by id is always fetched with the organization filter, so another tenant's id is a 404, never
a 403. Every stock-moving write goes through `app.inventory.ledger` and takes an
`Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import Entitled, IdempotencyKey, SessionDep
from app.inventory.pagination import CursorPage, CursorQuery, keyset_page
from app.models import AdjustmentRequest, InventoryReservation, StockTransfer
from app.movements import adjustments, common, reservations, transfers
from app.movements import selectors as sel
from app.movements.schemas import (
    AdjustmentCancelIn,
    AdjustmentIn,
    AdjustmentOut,
    AdjustmentStatus,
    ControlSettingsIn,
    ControlSettingsOut,
    InTransitRowOut,
    ReservationDetailOut,
    ReservationIn,
    ReservationReference,
    ReservationStatus,
    TransferCancelIn,
    TransferDecisionIn,
    TransferDispatchIn,
    TransferIn,
    TransferOut,
    TransferPatch,
    TransferReceiveIn,
    TransferStatus,
    TransferSummaryOut,
)
from platform_core.auth import Scope, ScopeContext, require
from platform_core.errors import NotFoundProblem

router = APIRouter(tags=["inventory-movements"])

StockRead = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.read", minimum=Scope.ORG))
]
StockReserve = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.reserve", minimum=Scope.ORG))
]
StockAdjust = Annotated[
    ScopeContext, Depends(require("operations.inventory.stock.adjust", minimum=Scope.ORG))
]
TransferWrite = Annotated[
    ScopeContext, Depends(require("operations.inventory.transfer.write", minimum=Scope.ORG))
]
TransferApprove = Annotated[
    ScopeContext, Depends(require("operations.inventory.transfer.approve", minimum=Scope.ORG))
]
TransferDispatch = Annotated[
    ScopeContext, Depends(require("operations.inventory.transfer.dispatch", minimum=Scope.ORG))
]
TransferReceive = Annotated[
    ScopeContext, Depends(require("operations.inventory.transfer.receive", minimum=Scope.ORG))
]
ControlConfigure = Annotated[
    ScopeContext, Depends(require("operations.inventory.control.configure", minimum=Scope.ORG))
]


def _status_for(response: Response, replayed: bool) -> None:
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED


# ------------------------------------------------------------------ control settings


@router.get(
    "/operations/inventory/control-settings",
    response_model=ControlSettingsOut,
    summary="The quantity thresholds above which transfers and adjustments need approval",
    dependencies=[Entitled],
)
async def get_control_settings(session: SessionDep, ctx: StockRead) -> ControlSettingsOut:
    return sel.settings_out(await common.get_settings(session, ctx.organization_id))


@router.put(
    "/operations/inventory/control-settings",
    response_model=ControlSettingsOut,
    summary="Set (or clear) the approval thresholds",
    dependencies=[Entitled],
)
async def put_control_settings(
    payload: ControlSettingsIn, session: SessionDep, ctx: ControlConfigure, request: Request
) -> ControlSettingsOut:
    row = await common.set_thresholds(
        session,
        ctx,
        transfer_milli=common.parse_threshold(
            payload.transfer_approval_threshold, "transfer_approval_threshold"
        ),
        adjustment_milli=common.parse_threshold(
            payload.adjustment_approval_threshold, "adjustment_approval_threshold"
        ),
        request=request,
    )
    return sel.settings_out(row)


# ------------------------------------------------------------------ transfers


async def _transfer_response(
    session: AsyncSession, ctx: ScopeContext, transfer: StockTransfer, *, replayed: bool = False
) -> TransferOut:
    return await sel.transfer_out(session, ctx.organization_id, transfer, replayed=replayed)


async def _existing_transfer(
    session: AsyncSession, ctx: ScopeContext, transfer_id: uuid.UUID
) -> StockTransfer:
    transfer = await sel.get_transfer(session, ctx.organization_id, transfer_id)
    if transfer is None:
        raise NotFoundProblem("That transfer is not part of this organization")
    return transfer


@router.post(
    "/operations/inventory/transfers",
    response_model=TransferOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft an inter-warehouse transfer",
    dependencies=[Entitled],
)
async def create_transfer(
    payload: TransferIn,
    session: SessionDep,
    ctx: TransferWrite,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> TransferOut:
    transfer, replayed = await transfers.create_transfer(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    _status_for(response, replayed)
    return await _transfer_response(session, ctx, transfer, replayed=replayed)


@router.get(
    "/operations/inventory/transfers",
    response_model=CursorPage[TransferSummaryOut],
    summary="Transfers, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_transfers(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    transfer_status: Annotated[TransferStatus | None, Query(alias="status")] = None,
    from_warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    to_warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=30, description="Transfer number prefix")] = None,
) -> CursorPage[TransferSummaryOut]:
    stmt = sel.transfers_stmt(
        ctx,
        status=transfer_status,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        item_id=item_id,
        q=q,
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=StockTransfer.created_at,
        id_col=StockTransfer.id,
        sort_of=lambda r: r.created_at,
        id_of=lambda r: r.id,
    )
    return CursorPage[TransferSummaryOut](
        items=await sel.transfer_summaries(session, ctx.organization_id, rows),
        next_cursor=next_cursor,
    )


@router.get(
    "/operations/inventory/in-transit",
    response_model=list[InTransitRowOut],
    summary="Stock on its way, by item and destination — physical on-hand reported separately",
    dependencies=[Entitled],
)
async def in_transit_report(
    session: SessionDep,
    ctx: StockRead,
    response: Response,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> list[InTransitRowOut]:
    rows, truncated = await sel.in_transit_rows(
        session, ctx, warehouse_id=warehouse_id, item_id=item_id, limit=limit
    )
    if truncated:  # narrow with warehouse_id / item_id to see the rest
        response.headers["X-Result-Truncated"] = "true"
    return rows


@router.get(
    "/operations/inventory/transfers/{transfer_id}",
    response_model=TransferOut,
    summary="One transfer with its lines and history",
    dependencies=[Entitled],
)
async def read_transfer(transfer_id: uuid.UUID, session: SessionDep, ctx: StockRead) -> TransferOut:
    transfer = await _existing_transfer(session, ctx, transfer_id)
    return await _transfer_response(session, ctx, transfer)


@router.patch(
    "/operations/inventory/transfers/{transfer_id}",
    response_model=TransferOut,
    summary="Edit a draft transfer",
    dependencies=[Entitled],
)
async def patch_transfer(
    transfer_id: uuid.UUID,
    payload: TransferPatch,
    session: SessionDep,
    ctx: TransferWrite,
    request: Request,
) -> TransferOut:
    transfer = await transfers.update_draft(session, ctx, transfer_id, payload, request=request)
    return await _transfer_response(session, ctx, transfer)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:request",
    response_model=TransferOut,
    summary="Request approval of a draft transfer",
    dependencies=[Entitled],
)
async def request_transfer(
    transfer_id: uuid.UUID, session: SessionDep, ctx: TransferWrite, request: Request
) -> TransferOut:
    transfer = await transfers.request_transfer(session, ctx, transfer_id, request=request)
    return await _transfer_response(session, ctx, transfer)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:approve",
    response_model=TransferOut,
    summary="Approve a requested transfer directly (not the requester's own)",
    dependencies=[Entitled],
)
async def approve_transfer(
    transfer_id: uuid.UUID,
    session: SessionDep,
    ctx: TransferApprove,
    request: Request,
    payload: TransferDecisionIn | None = None,
) -> TransferOut:
    transfer = await transfers.approve_transfer(
        session, ctx, transfer_id, payload or TransferDecisionIn(), request=request
    )
    return await _transfer_response(session, ctx, transfer)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:reject",
    response_model=TransferOut,
    summary="Reject a requested transfer directly",
    dependencies=[Entitled],
)
async def reject_transfer(
    transfer_id: uuid.UUID,
    payload: TransferDecisionIn,
    session: SessionDep,
    ctx: TransferApprove,
    request: Request,
) -> TransferOut:
    transfer = await transfers.reject_transfer(session, ctx, transfer_id, payload, request=request)
    return await _transfer_response(session, ctx, transfer)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:cancel",
    response_model=TransferOut,
    summary="Cancel a transfer that has not been dispatched",
    dependencies=[Entitled],
)
async def cancel_transfer(
    transfer_id: uuid.UUID,
    payload: TransferCancelIn,
    session: SessionDep,
    ctx: TransferWrite,
    request: Request,
) -> TransferOut:
    transfer = await transfers.cancel_transfer(session, ctx, transfer_id, payload, request=request)
    return await _transfer_response(session, ctx, transfer)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:dispatch",
    response_model=TransferOut,
    summary="Dispatch an approved transfer: stock leaves the source for in-transit",
    dependencies=[Entitled],
)
async def dispatch_transfer(
    transfer_id: uuid.UUID,
    session: SessionDep,
    ctx: TransferDispatch,
    request: Request,
    payload: TransferDispatchIn | None = None,
    idempotency_key: IdempotencyKey = None,
) -> TransferOut:
    transfer, replayed = await transfers.dispatch_transfer(
        session,
        ctx,
        transfer_id,
        payload or TransferDispatchIn(),
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _transfer_response(session, ctx, transfer, replayed=replayed)


@router.post(
    "/operations/inventory/transfers/{transfer_id}:receive",
    response_model=TransferOut,
    summary="Receive stock into the destination warehouse; declare any shortage",
    dependencies=[Entitled],
)
async def receive_transfer(
    transfer_id: uuid.UUID,
    payload: TransferReceiveIn,
    session: SessionDep,
    ctx: TransferReceive,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> TransferOut:
    transfer, replayed = await transfers.receive_transfer(
        session, ctx, transfer_id, payload, idempotency_key=idempotency_key, request=request
    )
    return await _transfer_response(session, ctx, transfer, replayed=replayed)


# ------------------------------------------------------------------ reservations


async def _reservation_response(
    session: AsyncSession,
    ctx: ScopeContext,
    reservation: InventoryReservation,
    *,
    replayed: bool = False,
) -> ReservationDetailOut:
    (out,) = await sel.reservation_outs(
        session, ctx.organization_id, [reservation], replayed=replayed
    )
    return out


@router.post(
    "/operations/inventory/reservations",
    response_model=ReservationDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve available stock for a sales order, a production order, or by hand",
    dependencies=[Entitled],
)
async def create_reservation(
    payload: ReservationIn,
    session: SessionDep,
    ctx: StockReserve,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ReservationDetailOut:
    reservation, replayed = await reservations.create_reservation(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    _status_for(response, replayed)
    return await _reservation_response(session, ctx, reservation, replayed=replayed)


@router.get(
    "/operations/inventory/reservations",
    response_model=CursorPage[ReservationDetailOut],
    summary="Reservations, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_reservations(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    location_id: Annotated[uuid.UUID | None, Query()] = None,
    reservation_status: Annotated[ReservationStatus | None, Query(alias="status")] = None,
    reference_type: Annotated[ReservationReference | None, Query()] = None,
    reference_id: Annotated[uuid.UUID | None, Query()] = None,
    expiring_before: Annotated[datetime | None, Query()] = None,
) -> CursorPage[ReservationDetailOut]:
    stmt = sel.reservations_stmt(
        ctx,
        item_id=item_id,
        location_id=location_id,
        status=reservation_status,
        reference_type=reference_type,
        reference_id=reference_id,
        expiring_before=expiring_before,
    )
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=InventoryReservation.created_at,
        id_col=InventoryReservation.id,
        sort_of=lambda r: r.created_at,
        id_of=lambda r: r.id,
    )
    return CursorPage[ReservationDetailOut](
        items=await sel.reservation_outs(session, ctx.organization_id, rows),
        next_cursor=next_cursor,
    )


@router.get(
    "/operations/inventory/reservations/{reservation_id}",
    response_model=ReservationDetailOut,
    summary="One reservation",
    dependencies=[Entitled],
)
async def read_reservation(
    reservation_id: uuid.UUID, session: SessionDep, ctx: StockRead
) -> ReservationDetailOut:
    reservation = await sel.get_reservation(session, ctx.organization_id, reservation_id)
    if reservation is None:
        raise NotFoundProblem("That reservation is not part of this organization")
    return await _reservation_response(session, ctx, reservation)


# ------------------------------------------------------------------ adjustments


async def _adjustment_response(
    session: AsyncSession, ctx: ScopeContext, row: AdjustmentRequest, *, replayed: bool = False
) -> AdjustmentOut:
    (out,) = await sel.adjustment_outs(session, ctx.organization_id, [row], replayed=replayed)
    return out


@router.post(
    "/operations/inventory/adjustments",
    response_model=AdjustmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Adjust stock with a reason and evidence — held for approval above the threshold",
    dependencies=[Entitled],
)
async def create_adjustment(
    payload: AdjustmentIn,
    session: SessionDep,
    ctx: StockAdjust,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> AdjustmentOut:
    row, replayed = await adjustments.create_adjustment(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif row.status == "PENDING_APPROVAL":
        response.status_code = status.HTTP_202_ACCEPTED
    return await _adjustment_response(session, ctx, row, replayed=replayed)


@router.get(
    "/operations/inventory/adjustments",
    response_model=CursorPage[AdjustmentOut],
    summary="Adjustments and their approval state, newest first (cursor-paginated)",
    dependencies=[Entitled],
)
async def list_adjustments(
    session: SessionDep,
    ctx: StockRead,
    params: CursorQuery,
    adjustment_status: Annotated[AdjustmentStatus | None, Query(alias="status")] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
) -> CursorPage[AdjustmentOut]:
    stmt = sel.adjustments_stmt(ctx, status=adjustment_status, item_id=item_id)
    rows, next_cursor = await keyset_page(
        session,
        stmt,
        params,
        sort_col=AdjustmentRequest.created_at,
        id_col=AdjustmentRequest.id,
        sort_of=lambda r: r.created_at,
        id_of=lambda r: r.id,
    )
    return CursorPage[AdjustmentOut](
        items=await sel.adjustment_outs(session, ctx.organization_id, rows),
        next_cursor=next_cursor,
    )


@router.get(
    "/operations/inventory/adjustments/{adjustment_id}",
    response_model=AdjustmentOut,
    summary="One adjustment",
    dependencies=[Entitled],
)
async def read_adjustment(
    adjustment_id: uuid.UUID, session: SessionDep, ctx: StockRead
) -> AdjustmentOut:
    row = await sel.get_adjustment(session, ctx.organization_id, adjustment_id)
    if row is None:
        raise NotFoundProblem("That adjustment is not part of this organization")
    return await _adjustment_response(session, ctx, row)


@router.post(
    "/operations/inventory/adjustments/{adjustment_id}:cancel",
    response_model=AdjustmentOut,
    summary="Withdraw an adjustment that is still waiting for approval",
    dependencies=[Entitled],
)
async def cancel_adjustment(
    adjustment_id: uuid.UUID,
    session: SessionDep,
    ctx: StockAdjust,
    request: Request,
    payload: AdjustmentCancelIn | None = None,
) -> AdjustmentOut:
    row = await adjustments.cancel_adjustment(
        session, ctx, adjustment_id, payload or AdjustmentCancelIn(), request=request
    )
    return await _adjustment_response(session, ctx, row)
