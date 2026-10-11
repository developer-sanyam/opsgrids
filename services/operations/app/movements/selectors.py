"""Reads for inventory Phase 3. Every query filters on `organization_id`; a row named by id that
belongs to another tenant is simply absent (the caller answers 404, never 403)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Select, case, cast, func, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import (
    OFF_SITE_STATUSES,
    AdjustmentRequest,
    InventoryBalance,
    InventoryControlSettings,
    InventoryItem,
    InventoryReservation,
    Location,
    StockTransfer,
    StockTransferLine,
    StockTransferTransition,
    Warehouse,
)
from app.movements.schemas import (
    AdjustmentOut,
    ControlSettingsOut,
    InTransitRowOut,
    ReservationDetailOut,
    TransferLineOut,
    TransferOut,
    TransferSummaryOut,
    TransferTransitionOut,
    WarehouseRefOut,
)
from platform_core.auth import ScopeContext


def _id(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


# ------------------------------------------------------------------ settings


def settings_out(row: InventoryControlSettings | None) -> ControlSettingsOut:
    return ControlSettingsOut(
        transfer_approval_threshold=(
            format_qty(row.transfer_approval_threshold_milli)
            if row and row.transfer_approval_threshold_milli is not None
            else None
        ),
        adjustment_approval_threshold=(
            format_qty(row.adjustment_approval_threshold_milli)
            if row and row.adjustment_approval_threshold_milli is not None
            else None
        ),
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


# ------------------------------------------------------------------ transfers


def transfers_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    from_warehouse_id: uuid.UUID | None = None,
    to_warehouse_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = ctx.apply(
        select(StockTransfer).where(StockTransfer.organization_id == ctx.organization_id),
        owner_col=StockTransfer.requested_by_employee_id,
    )
    if status is not None:
        stmt = stmt.where(StockTransfer.status == status)
    if from_warehouse_id is not None:
        stmt = stmt.where(StockTransfer.from_warehouse_id == from_warehouse_id)
    if to_warehouse_id is not None:
        stmt = stmt.where(StockTransfer.to_warehouse_id == to_warehouse_id)
    if item_id is not None:
        stmt = stmt.where(
            select(StockTransferLine.id)
            .where(
                StockTransferLine.organization_id == ctx.organization_id,
                StockTransferLine.transfer_id == StockTransfer.id,
                StockTransferLine.inventory_item_id == item_id,
            )
            .exists()
        )
    if q:
        stmt = stmt.where(func.lower(StockTransfer.number).like(f"{q.lower()}%"))
    return stmt


async def get_transfer(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> StockTransfer | None:
    stmt = select(StockTransfer).where(
        StockTransfer.organization_id == org_id, StockTransfer.id == transfer_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    # `populate_existing`: a row re-read after taking its lock must reflect what the previous
    # lock holder committed, not the identity map's earlier copy.
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def get_transfer_by_approval_request(
    session: AsyncSession, org_id: uuid.UUID, approval_request_id: uuid.UUID
) -> StockTransfer | None:
    return (
        await session.execute(
            select(StockTransfer).where(
                StockTransfer.organization_id == org_id,
                StockTransfer.approval_request_id == approval_request_id,
            )
        )
    ).scalar_one_or_none()


async def transfer_lines(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[StockTransferLine]:
    stmt = (
        select(StockTransferLine)
        .where(
            StockTransferLine.organization_id == org_id,
            StockTransferLine.transfer_id == transfer_id,
        )
        .order_by(StockTransferLine.line_no)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list((await session.execute(stmt.execution_options(populate_existing=True))).scalars())


async def _warehouse_refs(
    session: AsyncSession, org_id: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, WarehouseRefOut]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Warehouse.id, Warehouse.code, Warehouse.name).where(
                Warehouse.organization_id == org_id, Warehouse.id.in_(ids)
            )
        )
    ).all()
    return {r.id: WarehouseRefOut(id=str(r.id), code=r.code, name=r.name) for r in rows}


def _summary(
    t: StockTransfer, refs: dict[uuid.UUID, WarehouseRefOut], line_count: int
) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "number": t.number,
        "status": t.status,
        "from_warehouse": refs[t.from_warehouse_id],
        "to_warehouse": refs[t.to_warehouse_id],
        "line_count": line_count,
        "approval_route": t.approval_route,
        "expected_on": t.expected_on,
        "requested_at": t.requested_at,
        "dispatched_at": t.dispatched_at,
        "completed_at": t.completed_at,
        "created_at": t.created_at,
    }


async def transfer_summaries(
    session: AsyncSession, org_id: uuid.UUID, transfers: list[StockTransfer]
) -> list[TransferSummaryOut]:
    if not transfers:
        return []
    refs = await _warehouse_refs(
        session,
        org_id,
        {t.from_warehouse_id for t in transfers} | {t.to_warehouse_id for t in transfers},
    )
    counts: dict[uuid.UUID, int] = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(StockTransferLine.transfer_id, func.count())
                .where(
                    StockTransferLine.organization_id == org_id,
                    StockTransferLine.transfer_id.in_([t.id for t in transfers]),
                )
                .group_by(StockTransferLine.transfer_id)
            )
        ).all()
    }
    return [TransferSummaryOut(**_summary(t, refs, counts.get(t.id, 0))) for t in transfers]


async def transfer_out(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer: StockTransfer,
    *,
    replayed: bool = False,
) -> TransferOut:
    lines = await transfer_lines(session, org_id, transfer.id)
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({line.inventory_item_id for line in lines}),
                )
            )
        ).scalars()
    }
    loc_ids = {line.from_location_id for line in lines}
    codes = {
        r.id: r.code
        for r in (
            await session.execute(
                select(Location.id, Location.code).where(
                    Location.organization_id == org_id, Location.id.in_(loc_ids)
                )
            )
        ).all()
    }
    transitions = (
        (
            await session.execute(
                select(StockTransferTransition)
                .where(
                    StockTransferTransition.organization_id == org_id,
                    StockTransferTransition.transfer_id == transfer.id,
                )
                .order_by(StockTransferTransition.created_at, StockTransferTransition.id)
            )
        )
        .scalars()
        .all()
    )
    refs = await _warehouse_refs(
        session, org_id, {transfer.from_warehouse_id, transfer.to_warehouse_id}
    )
    line_outs = []
    for line in lines:
        item = items[line.inventory_item_id]
        line_outs.append(
            TransferLineOut(
                id=str(line.id),
                line_no=line.line_no,
                inventory_item_id=str(line.inventory_item_id),
                sku=item.sku,
                item_name=item.name,
                uom=item.uom,
                from_location_id=str(line.from_location_id),
                from_location_code=codes.get(line.from_location_id, ""),
                to_location_id=_id(line.to_location_id),
                in_transit_location_id=_id(line.in_transit_location_id),
                lot_id=_id(line.lot_id),
                roll_id=_id(line.roll_id),
                quantity=format_qty(line.quantity_milli),
                dispatched=format_qty(line.dispatched_milli),
                received=format_qty(line.received_milli),
                shortage=format_qty(line.shortage_milli),
                in_transit=format_qty(
                    line.dispatched_milli - line.received_milli - line.shortage_milli
                ),
            )
        )
    return TransferOut(
        **_summary(transfer, refs, len(lines)),
        note=transfer.note,
        approval_request_id=_id(transfer.approval_request_id),
        approver_name=transfer.approver_name,
        requested_by=transfer.requested_by,
        approved_by=transfer.approved_by,
        approved_at=transfer.approved_at,
        decision_comment=transfer.decision_comment,
        dispatched_by=transfer.dispatched_by,
        cancelled_at=transfer.cancelled_at,
        cancel_reason=transfer.cancel_reason,
        awaiting_approval=transfer.status == "REQUESTED" and transfer.approval_route == "SERVICE",
        lines=line_outs,
        transitions=[
            TransferTransitionOut(
                from_status=t.from_status,
                to_status=t.to_status,
                actor=t.actor,
                note=t.note,
                created_at=t.created_at,
            )
            for t in transitions
        ],
        replayed=replayed,
    )


async def in_transit_rows(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    warehouse_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    limit: int = 500,
) -> tuple[list[InTransitRowOut], bool]:
    """Stock on its way, by item and destination warehouse — with the item's physical on-hand in
    that same warehouse alongside, reported separately (in-transit is owned but not yet there, so
    it is never part of `physical`)."""
    org_id = ctx.organization_id
    in_transit = cast(
        func.coalesce(
            func.sum(
                case((InventoryBalance.status == "IN_TRANSIT", InventoryBalance.quantity_milli))
            ),
            0,
        ),
        BigInteger,
    ).label("in_transit")
    physical = cast(
        func.coalesce(
            func.sum(
                case(
                    (
                        InventoryBalance.status.not_in(OFF_SITE_STATUSES),
                        InventoryBalance.quantity_milli,
                    )
                )
            ),
            0,
        ),
        BigInteger,
    ).label("physical")
    stmt = (
        select(
            InventoryBalance.inventory_item_id,
            InventoryItem.sku,
            InventoryItem.name,
            InventoryItem.uom,
            Location.warehouse_id,
            Warehouse.code,
            in_transit,
            physical,
        )
        .join(Location, Location.id == InventoryBalance.location_id)
        .join(InventoryItem, InventoryItem.id == InventoryBalance.inventory_item_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(
            InventoryBalance.organization_id == org_id,
            Location.organization_id == org_id,
            InventoryItem.organization_id == org_id,
            Warehouse.organization_id == org_id,
        )
        .group_by(
            InventoryBalance.inventory_item_id,
            InventoryItem.sku,
            InventoryItem.name,
            InventoryItem.uom,
            Location.warehouse_id,
            Warehouse.code,
        )
        .having(
            func.coalesce(
                func.sum(
                    case((InventoryBalance.status == "IN_TRANSIT", InventoryBalance.quantity_milli))
                ),
                0,
            )
            > 0
        )
        .order_by(Warehouse.code, InventoryItem.sku)
        .limit(limit + 1)
    )
    if warehouse_id is not None:
        stmt = stmt.where(Location.warehouse_id == warehouse_id)
    if item_id is not None:
        stmt = stmt.where(InventoryBalance.inventory_item_id == item_id)
    rows = (await session.execute(stmt)).all()
    return [
        InTransitRowOut(
            inventory_item_id=str(r.inventory_item_id),
            sku=r.sku,
            item_name=r.name,
            uom=r.uom,
            warehouse_id=str(r.warehouse_id),
            warehouse_code=r.code,
            in_transit=format_qty(int(r.in_transit)),
            physical=format_qty(int(r.physical)),
        )
        for r in rows[:limit]
    ], len(rows) > limit


# ------------------------------------------------------------------ reservations


def reservations_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    status: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    expiring_before: Any | None = None,
) -> Select[Any]:
    stmt = ctx.apply(
        select(InventoryReservation)
        .join(InventoryBalance, InventoryBalance.id == InventoryReservation.balance_id)
        .where(
            InventoryReservation.organization_id == ctx.organization_id,
            InventoryBalance.organization_id == ctx.organization_id,
        ),
        # No employee owner on a reservation: OWN scope matches nothing (NULL), never a sub.
        owner_col=null(),
    )
    if item_id is not None:
        stmt = stmt.where(InventoryReservation.inventory_item_id == item_id)
    if location_id is not None:
        stmt = stmt.where(InventoryBalance.location_id == location_id)
    if status is not None:
        stmt = stmt.where(InventoryReservation.status == status)
    if reference_type is not None:
        stmt = stmt.where(InventoryReservation.reference_type == reference_type)
    if reference_id is not None:
        stmt = stmt.where(InventoryReservation.reference_id == reference_id)
    if expiring_before is not None:
        stmt = stmt.where(
            InventoryReservation.status == "active",
            InventoryReservation.expires_at.is_not(None),
            InventoryReservation.expires_at < expiring_before,
        )
    return stmt


async def get_reservation(
    session: AsyncSession, org_id: uuid.UUID, reservation_id: uuid.UUID
) -> InventoryReservation | None:
    return (
        await session.execute(
            select(InventoryReservation).where(
                InventoryReservation.organization_id == org_id,
                InventoryReservation.id == reservation_id,
            )
        )
    ).scalar_one_or_none()


async def reservation_outs(
    session: AsyncSession,
    org_id: uuid.UUID,
    reservations: list[InventoryReservation],
    *,
    replayed: bool = False,
) -> list[ReservationDetailOut]:
    if not reservations:
        return []
    balances = {
        b.id: b
        for b in (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.id.in_({r.balance_id for r in reservations}),
                )
            )
        ).scalars()
    }
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({r.inventory_item_id for r in reservations}),
                )
            )
        ).scalars()
    }
    codes = {
        r.id: r.code
        for r in (
            await session.execute(
                select(Location.id, Location.code).where(
                    Location.organization_id == org_id,
                    Location.id.in_({b.location_id for b in balances.values()}),
                )
            )
        ).all()
    }
    out = []
    for r in reservations:
        balance = balances[r.balance_id]
        item = items[r.inventory_item_id]
        remaining = (
            r.quantity_milli - r.consumed_milli - r.released_milli - r.transferred_milli
            if r.status == "active"
            else 0
        )
        out.append(
            ReservationDetailOut(
                id=str(r.id),
                inventory_item_id=str(r.inventory_item_id),
                sku=item.sku,
                item_name=item.name,
                uom=item.uom,
                location_id=str(balance.location_id),
                location_code=codes.get(balance.location_id, ""),
                lot_id=_id(balance.lot_id),
                roll_id=_id(balance.roll_id),
                quantity=format_qty(r.quantity_milli),
                consumed=format_qty(r.consumed_milli),
                released=format_qty(r.released_milli),
                transferred=format_qty(r.transferred_milli),
                remaining=format_qty(remaining),
                status=r.status,
                reference_type=r.reference_type,
                reference_id=_id(r.reference_id),
                expires_at=r.expires_at,
                expired_at=r.expired_at,
                note=r.note,
                created_at=r.created_at,
                closed_at=r.closed_at,
                replayed=replayed,
            )
        )
    return out


# ------------------------------------------------------------------ adjustments


def adjustments_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    item_id: uuid.UUID | None = None,
) -> Select[Any]:
    stmt = ctx.apply(
        select(AdjustmentRequest).where(AdjustmentRequest.organization_id == ctx.organization_id),
        owner_col=AdjustmentRequest.requested_by_employee_id,
    )
    if status is not None:
        stmt = stmt.where(AdjustmentRequest.status == status)
    if item_id is not None:
        stmt = stmt.where(AdjustmentRequest.inventory_item_id == item_id)
    return stmt


async def get_adjustment(
    session: AsyncSession,
    org_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> AdjustmentRequest | None:
    stmt = select(AdjustmentRequest).where(
        AdjustmentRequest.organization_id == org_id, AdjustmentRequest.id == adjustment_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def adjustment_outs(
    session: AsyncSession,
    org_id: uuid.UUID,
    rows: list[AdjustmentRequest],
    *,
    replayed: bool = False,
) -> list[AdjustmentOut]:
    if not rows:
        return []
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({r.inventory_item_id for r in rows}),
                )
            )
        ).scalars()
    }
    codes = {
        r.id: r.code
        for r in (
            await session.execute(
                select(Location.id, Location.code).where(
                    Location.organization_id == org_id,
                    Location.id.in_({r.location_id for r in rows}),
                )
            )
        ).all()
    }
    return [
        AdjustmentOut(
            id=str(r.id),
            number=r.number,
            inventory_item_id=str(r.inventory_item_id),
            sku=items[r.inventory_item_id].sku,
            item_name=items[r.inventory_item_id].name,
            uom=items[r.inventory_item_id].uom,
            location_id=str(r.location_id),
            location_code=codes.get(r.location_id, ""),
            stock_status=r.stock_status,
            lot_id=_id(r.lot_id),
            roll_id=_id(r.roll_id),
            quantity_delta=format_qty(r.quantity_delta_milli),
            reason=r.reason,
            note=r.note,
            evidence_document_id=_id(r.evidence_document_id),
            occurred_on=r.occurred_on,
            status=r.status,
            approval_required=r.approval_required,
            approval_request_id=_id(r.approval_request_id),
            approver_name=r.approver_name,
            decided_by_name=r.decided_by_name,
            decided_at=r.decided_at,
            decision_comment=r.decision_comment,
            requested_by=r.requested_by,
            applied_move_id=_id(r.applied_move_id),
            applied_at=r.applied_at,
            failure_reason=r.failure_reason,
            created_at=r.created_at,
            replayed=replayed,
        )
        for r in rows
    ]
