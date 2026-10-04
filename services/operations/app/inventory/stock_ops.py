"""Stock operations on top of the posting kernel (inventory Phase 1, I4): opening stock, bin-to-bin
transfer and status change.

**Nothing here writes a quantity.** Each operation builds `MoveLine`s and hands them to
`app.inventory.ledger.post_move` — ADR-0014 §3's only writer — which takes the bucket locks in
its fixed order, refuses a negative or reserved-into result with the numbers
(`InsufficientStock`, §11), and makes a retried `Idempotency-Key` a replay. What this module adds
is the *meaning* of the request (is this transfer inside one warehouse? is a tracked item given
its lot?), the audit row and the outbox event — both staged only for a fresh post, never for a
replay, so a retried request leaves exactly one of each.

Kinds: opening stock is `opening`; a transfer and a status change are `relocation` moves (stock
conserved: a negative line and a positive one in one move), told apart by `reference_type`
(`bin_transfer`, `status_change`). That keeps the move-kind vocabulary the ledger's reconciler and
filters already know.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine, PostedMove
from app.inventory.quantity import QuantityError, format_qty, parse_qty
from app.inventory.tracking_schemas import (
    OpeningStockIn,
    StockMoveLineOut,
    StockMoveOut,
    StockStatusChangeIn,
    StockTransferIn,
)
from app.models import AuditLog, InventoryItem, OutboxEvent, RollEvent
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_OPENING_POSTED = "operations.inventory.stock.opening_posted"
EVT_STOCK_TRANSFERRED = "operations.inventory.stock.transferred"
EVT_STOCK_STATUS_CHANGED = "operations.inventory.stock.status_changed"


def actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def parse_positive(value: str, uom: str, field: str) -> int:
    """A wire quantity -> positive milli-units in the item's UOM. Never rounds; refuses a fraction
    on a discrete unit (the parse does both). A malformed or non-positive quantity is a 422 that
    names the field."""
    try:
        milli = parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": field, "code": "invalid_quantity", "message": str(exc)}]
        ) from exc
    if milli <= 0:
        raise ValidationProblem(
            "A quantity here must be greater than zero",
            errors=[{"field": field, "code": "invalid_quantity", "message": "must be > 0"}],
        )
    return milli


async def load_items(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, InventoryItem]:
    """Live items of this organization, one query. An id from anywhere else is a 404 — the same
    answer as one that does not exist, never a hint it lives in another tenant."""
    wanted = set(ids)
    rows = (
        (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_(wanted),
                    InventoryItem.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    found = {item.id: item for item in rows}
    if missing := wanted - found.keys():
        raise NotFoundProblem(f"Inventory item {sorted(map(str, missing))[0]} is not in this org")
    return found


def move_out(posted: PostedMove) -> StockMoveOut:
    move = posted.move
    return StockMoveOut(
        move_id=str(move.id),
        kind=move.kind,
        reference_type=move.reference_type,
        reference_id=str(move.reference_id) if move.reference_id else None,
        occurred_on=move.occurred_on,
        replayed=posted.replayed,
        lines=[
            StockMoveLineOut(
                ledger_entry_id=str(e.id),
                inventory_item_id=str(e.inventory_item_id),
                location_id=str(e.location_id),
                status=e.status,
                lot_id=str(e.lot_id) if e.lot_id else None,
                roll_id=str(e.roll_id) if e.roll_id else None,
                quantity=format_qty(e.quantity_milli),
                balance_after=format_qty(e.balance_after_milli),
            )
            for e in posted.entries
        ],
    )


def stage(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    action: str,
    event_type: str,
    entity: str,
    entity_id: uuid.UUID,
    after: dict[str, Any],
    payload: dict[str, Any],
    reason: str | None,
    request: Request | None,
) -> None:
    """The audit row and the outbox event for one operation, in the caller's transaction."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity=entity,
        entity_id=entity_id,
        after=after,
        reason=reason or None,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type=entity,
        aggregate_id=entity_id,
        payload={"organization_id": str(ctx.organization_id), **payload},
        actor=actor(ctx),
    )


def check_tracking(
    item: InventoryItem, lot_id: uuid.UUID | None, roll_id: uuid.UUID | None
) -> None:
    """A tracked item's stock must say which lot / roll it is; an untracked item's must not (a
    lot id on an item that does not track lots would be a bucket nobody can look up)."""
    for flag, given, noun in (
        (item.track_lot, lot_id, "lot"),
        (item.track_roll, roll_id, "roll"),
    ):
        if flag and given is None:
            raise ValidationProblem(
                f"{item.sku} is tracked by {noun}; say which {noun} this stock is",
                errors=[{"field": f"{noun}_id", "code": "required", "message": f"{noun} required"}],
            )
        if not flag and given is not None:
            raise ValidationProblem(
                f"{item.sku} is not tracked by {noun}; enable {noun} tracking on the item first",
                errors=[{"field": f"{noun}_id", "code": "not_tracked", "message": "not tracked"}],
            )


# ------------------------------------------------------------------ opening stock


async def post_opening_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: OpeningStockIn,
    *,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> PostedMove:
    """Bring existing stock onto the books: one `opening` move, one line per
    (item, location, status, lot, roll). Quantities are parsed against each item's own UOM."""
    items = await load_items(
        session, ctx.organization_id, (ln.inventory_item_id for ln in payload.lines)
    )
    lines: list[MoveLine] = []
    for index, line in enumerate(payload.lines):
        item = items[line.inventory_item_id]
        check_tracking(item, line.lot_id, line.roll_id)
        lines.append(
            MoveLine(
                item_id=item.id,
                location_id=line.location_id,
                quantity_milli=parse_positive(line.quantity, item.uom, f"lines[{index}].quantity"),
                status=line.status,
                lot_id=line.lot_id,
                roll_id=line.roll_id,
                unit_cost_minor=line.unit_cost_minor,
                unit_cost_currency=line.unit_cost_currency.upper()
                if line.unit_cost_currency
                else None,
            )
        )
    posted = await ledger.post_move(
        session,
        ctx,
        kind="opening",
        lines=lines,
        occurred_on=payload.occurred_on,
        reference_type="opening_stock",
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    if posted.replayed:
        return posted
    for entry in posted.entries:
        if entry.roll_id is not None:
            session.add(
                RollEvent(
                    organization_id=ctx.organization_id,
                    roll_id=entry.roll_id,
                    kind="OPENING",
                    move_id=posted.move.id,
                    quantity_milli=entry.quantity_milli,
                    detail={"location_id": str(entry.location_id), "status": entry.status},
                    created_by=str(ctx.principal.sub),
                )
            )
    stage(
        session,
        ctx,
        action="stock.opening_posted",
        event_type=EVT_OPENING_POSTED,
        entity="inventory_move",
        entity_id=posted.move.id,
        after={"line_count": len(lines)},
        payload={"move_id": str(posted.move.id), "line_count": len(lines)},
        reason=payload.note,
        request=request,
    )
    return posted


# ------------------------------------------------------------------ bin-to-bin transfer


async def transfer_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: StockTransferIn,
    *,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> PostedMove:
    """Move stock from one location to another **in the same warehouse**, immediately. Moving it
    out of a bucket whose stock is reserved is the kernel's refusal (`InsufficientStock`, naming
    the reservations in the way — ADR-0014 §11); inter-warehouse movement needs the in-transit
    bucket and its document, which is Phase 3."""
    org_id = ctx.organization_id
    items = await load_items(session, org_id, [payload.inventory_item_id])
    item = items[payload.inventory_item_id]
    source = await inv_selectors.get_location(session, org_id, payload.from_location_id)
    target = await inv_selectors.get_location(session, org_id, payload.to_location_id)
    if source is None:
        raise NotFoundProblem(f"Location {payload.from_location_id} is not in this org")
    if target is None:
        raise NotFoundProblem(f"Location {payload.to_location_id} is not in this org")
    if source.warehouse_id != target.warehouse_id:
        raise ValidationProblem(
            "A bin-to-bin transfer stays inside one warehouse; moving stock between warehouses "
            "needs an inter-warehouse transfer",
            errors=[
                {
                    "field": "to_location_id",
                    "code": "different_warehouse",
                    "message": "not in the source warehouse",
                }
            ],
        )
    milli = parse_positive(payload.quantity, item.uom, "quantity")
    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            MoveLine(
                item.id,
                source.id,
                -milli,
                status=payload.status,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            ),
            MoveLine(
                item.id,
                target.id,
                milli,
                status=payload.status,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            ),
        ],
        occurred_on=payload.occurred_on,
        reference_type="bin_transfer",
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    if posted.replayed:
        return posted
    if payload.roll_id is not None:
        session.add(
            RollEvent(
                organization_id=org_id,
                roll_id=payload.roll_id,
                kind="MOVED",
                move_id=posted.move.id,
                quantity_milli=milli,
                detail={
                    "from_location_id": str(source.id),
                    "to_location_id": str(target.id),
                    "status": payload.status,
                },
                created_by=str(ctx.principal.sub),
            )
        )
    detail = {
        "move_id": str(posted.move.id),
        "inventory_item_id": str(item.id),
        "from_location_id": str(source.id),
        "to_location_id": str(target.id),
        "status": payload.status,
        "lot_id": str(payload.lot_id) if payload.lot_id else None,
        "roll_id": str(payload.roll_id) if payload.roll_id else None,
        "quantity": format_qty(milli),
    }
    stage(
        session,
        ctx,
        action="stock.transferred",
        event_type=EVT_STOCK_TRANSFERRED,
        entity="inventory_move",
        entity_id=posted.move.id,
        after=detail,
        payload=detail,
        reason=payload.note,
        request=request,
    )
    return posted


# ------------------------------------------------------------------ status change


async def change_stock_status(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: StockStatusChangeIn,
    *,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> PostedMove:
    """Move stock between statuses where it sits (AVAILABLE -> QUALITY_HOLD, a hold released, a
    roll written off as DAMAGED): a paired move, so the physical total never changes and history
    keeps the state change (ADR-0014 §4). Taking reserved stock out of AVAILABLE is refused like
    any other outbound line."""
    items = await load_items(session, ctx.organization_id, [payload.inventory_item_id])
    item = items[payload.inventory_item_id]
    milli = parse_positive(payload.quantity, item.uom, "quantity")
    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            MoveLine(
                item.id,
                payload.location_id,
                -milli,
                status=payload.from_status,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            ),
            MoveLine(
                item.id,
                payload.location_id,
                milli,
                status=payload.to_status,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            ),
        ],
        occurred_on=payload.occurred_on,
        reference_type="status_change",
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    if posted.replayed:
        return posted
    if payload.roll_id is not None:
        session.add(
            RollEvent(
                organization_id=ctx.organization_id,
                roll_id=payload.roll_id,
                kind="STATUS_CHANGED",
                move_id=posted.move.id,
                quantity_milli=milli,
                detail={
                    "location_id": str(payload.location_id),
                    "from_status": payload.from_status,
                    "to_status": payload.to_status,
                },
                created_by=str(ctx.principal.sub),
            )
        )
    detail = {
        "move_id": str(posted.move.id),
        "inventory_item_id": str(item.id),
        "location_id": str(payload.location_id),
        "from_status": payload.from_status,
        "to_status": payload.to_status,
        "lot_id": str(payload.lot_id) if payload.lot_id else None,
        "roll_id": str(payload.roll_id) if payload.roll_id else None,
        "quantity": format_qty(milli),
    }
    stage(
        session,
        ctx,
        action="stock.status_changed",
        event_type=EVT_STOCK_STATUS_CHANGED,
        entity="inventory_move",
        entity_id=posted.move.id,
        after=detail,
        payload=detail,
        reason=payload.note,
        request=request,
    )
    return posted
