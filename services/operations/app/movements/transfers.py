"""Inter-warehouse transfers (inventory Phase 3; docs/inventory/movements.md).

A transfer is a **document** whose stock effects are two ledger moves, both through
`ledger.post_move` — this module never writes a quantity:

* **dispatch** — one `relocation` move: each line leaves its source bin (`AVAILABLE`) and lands in
  the `IN_TRANSIT` bucket of the destination warehouse's default bin, lot and roll intact.
  Physical on-hand falls at the source immediately; the stock is *not* in the destination's
  physical figure until it is received (`IN_TRANSIT` is reported separately — ADR-0014 §3).
* **receive** — a `relocation` out of `IN_TRANSIT` into the chosen destination bin, per line, any
  number of times; and, for what never arrived, an `adjustment` out of `IN_TRANSIT` (reason
  `lost`) so the in-transit figure always drains to exactly zero. The transfer is RECEIVED when
  every line has `received + shortage == dispatched`.

Lock order, everywhere: the transfer row, then (inside the kernel) items, balances, reservations.
Nothing here takes a balance lock first, so a transfer cannot deadlock against an issue or a count.
Two dispatches of one transfer serialise on the transfer row; the second sees `IN_TRANSIT` and is
refused. The dispatch move also carries a fixed idempotency key, so even a path that bypassed the
status check could not post it twice.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger, stock_ops
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import format_qty
from app.models import (
    AdjustmentRequest,
    InventoryItem,
    InventoryMove,
    Location,
    Lot,
    Roll,
    RollEvent,
    StockTransfer,
    StockTransferLine,
    StockTransferTransition,
    Warehouse,
)
from app.movements import adjustments, common
from app.movements import selectors as sel
from app.movements.domain import (
    TRANSFER_TRANSITIONS,
    assert_transition,
    format_number,
    merge_amounts,
    over_threshold,
    to_base_milli,
    transfer_route,
)
from app.movements.schemas import (
    AdjustmentIn,
    TransferCancelIn,
    TransferDecisionIn,
    TransferDispatchIn,
    TransferIn,
    TransferLineIn,
    TransferPatch,
    TransferReceiveIn,
)
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

_Bucket = tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None]


def _now() -> datetime:
    return datetime.now(UTC)


def _actor_str(ctx: ScopeContext) -> str:
    return str(ctx.principal.sub)


# ------------------------------------------------------------------ building a draft


async def _require_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID, field: str
) -> Warehouse:
    warehouse = await inv_selectors.get_warehouse(session, org_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem(f"Warehouse {warehouse_id} ({field}) is not in this organization")
    return warehouse


async def _check_lot_roll(
    session: AsyncSession,
    org_id: uuid.UUID,
    item: InventoryItem,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
    field: str,
) -> None:
    """Fail at drafting, not at dispatch: the lot and roll must exist here and belong to the
    item. (The kernel checks the same at posting; this just gives the draft's author the answer
    while they can still change the line.)"""
    if lot_id is not None:
        lot = (
            await session.execute(
                select(Lot).where(Lot.organization_id == org_id, Lot.id == lot_id)
            )
        ).scalar_one_or_none()
        if lot is None:
            raise NotFoundProblem(f"Lot {lot_id} is not in this organization")
        if lot.inventory_item_id != item.id:
            raise ValidationProblem(
                f"Lot {lot.lot_no} is not a lot of {item.sku}",
                errors=[
                    {"field": f"{field}.lot_id", "code": "wrong_item", "message": "wrong item"}
                ],
            )
    if roll_id is not None:
        roll = (
            await session.execute(
                select(Roll).where(Roll.organization_id == org_id, Roll.id == roll_id)
            )
        ).scalar_one_or_none()
        if roll is None:
            raise NotFoundProblem(f"Roll {roll_id} is not in this organization")
        if roll.inventory_item_id != item.id:
            raise ValidationProblem(
                f"Roll {roll.roll_no} is not a roll of {item.sku}",
                errors=[
                    {"field": f"{field}.roll_id", "code": "wrong_item", "message": "wrong item"}
                ],
            )


async def _validated_lines(
    session: AsyncSession,
    org_id: uuid.UUID,
    from_warehouse_id: uuid.UUID,
    lines: list[TransferLineIn],
) -> list[dict[str, Any]]:
    items = await stock_ops.load_items(session, org_id, (ln.inventory_item_id for ln in lines))
    wanted = {ln.from_location_id for ln in lines}
    locations = {
        loc.id: loc
        for loc in (
            await session.execute(
                select(Location).where(
                    Location.organization_id == org_id,
                    Location.id.in_(wanted),
                    Location.deleted_at.is_(None),
                )
            )
        ).scalars()
    }
    seen: set[_Bucket] = set()
    built: list[dict[str, Any]] = []
    for index, ln in enumerate(lines):
        field = f"lines[{index}]"
        location = locations.get(ln.from_location_id)
        if location is None:
            raise NotFoundProblem(f"Location {ln.from_location_id} is not in this organization")
        if location.warehouse_id != from_warehouse_id:
            raise ValidationProblem(
                f"Location {location.code} is not in the source warehouse",
                errors=[
                    {
                        "field": f"{field}.from_location_id",
                        "code": "wrong_warehouse",
                        "message": "not in the source warehouse",
                    }
                ],
            )
        item = items[ln.inventory_item_id]
        stock_ops.check_tracking(item, ln.lot_id, ln.roll_id)
        await _check_lot_roll(session, org_id, item, ln.lot_id, ln.roll_id, field)
        bucket: _Bucket = (item.id, location.id, ln.lot_id, ln.roll_id)
        if bucket in seen:
            raise ValidationProblem(
                f"{item.sku} from {location.code} appears twice; combine the lines",
                errors=[{"field": field, "code": "duplicate_line", "message": "duplicate"}],
            )
        seen.add(bucket)
        built.append(
            {
                "item_id": item.id,
                "from_location_id": location.id,
                "lot_id": ln.lot_id,
                "roll_id": ln.roll_id,
                "quantity_milli": stock_ops.parse_positive(
                    ln.quantity, item.uom, f"{field}.quantity"
                ),
            }
        )
    return built


def _add_transition(
    session: AsyncSession,
    transfer: StockTransfer,
    *,
    from_status: str | None,
    to_status: str,
    actor: str,
    note: str = "",
) -> None:
    session.add(
        StockTransferTransition(
            organization_id=transfer.organization_id,
            transfer_id=transfer.id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            note=note[:500],
        )
    )


def _step(
    session: AsyncSession,
    transfer: StockTransfer,
    target: str,
    *,
    actor: str,
    note: str = "",
) -> None:
    """Move the document one allowed step and record it."""
    assert_transition(TRANSFER_TRANSITIONS, transfer.status, target, noun="transfer")
    previous = transfer.status
    transfer.status = target
    transfer.updated_at = _now()
    _add_transition(
        session, transfer, from_status=previous, to_status=target, actor=actor, note=note
    )


async def create_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: TransferIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
) -> tuple[StockTransfer, bool]:
    """A DRAFT transfer. A retried `Idempotency-Key` returns the same transfer (`replayed`); the
    same key with a different body is a 409."""
    org_id = ctx.organization_id
    digest = common.request_hash(payload.model_dump(mode="json"))
    if idempotency_key is not None:
        await common.serialise_key(session, org_id, idempotency_key)
        existing = (
            await session.execute(
                select(StockTransfer).where(
                    StockTransfer.organization_id == org_id,
                    StockTransfer.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # A key is its author's: another person sending the same key and body is not a retry,
            # and must not be handed the first person's document.
            if existing.request_hash != digest or existing.created_by != _actor_str(ctx):
                raise ConflictProblem(
                    "That idempotency key was already used for a different transfer"
                )
            return existing, True

    await _require_warehouse(session, org_id, payload.from_warehouse_id, "from_warehouse_id")
    await _require_warehouse(session, org_id, payload.to_warehouse_id, "to_warehouse_id")
    lines = await _validated_lines(session, org_id, payload.from_warehouse_id, payload.lines)

    number = format_number("TRF", await common.next_number(session, org_id, "transfer"))
    transfer = StockTransfer(
        organization_id=org_id,
        number=number,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        status="DRAFT",
        note=payload.note,
        expected_on=payload.expected_on,
        idempotency_key=idempotency_key,
        request_hash=digest if idempotency_key else None,
        created_by=_actor_str(ctx),
    )
    session.add(transfer)
    await session.flush()
    for line_no, spec in enumerate(lines, start=1):
        session.add(
            StockTransferLine(
                organization_id=org_id,
                transfer_id=transfer.id,
                line_no=line_no,
                inventory_item_id=spec["item_id"],
                from_location_id=spec["from_location_id"],
                lot_id=spec["lot_id"],
                roll_id=spec["roll_id"],
                quantity_milli=spec["quantity_milli"],
            )
        )
    _add_transition(session, transfer, from_status=None, to_status="DRAFT", actor=_actor_str(ctx))
    await session.flush()
    common.audit_only(
        session,
        ctx,
        action="stock_transfer.created",
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"number": number, "line_count": len(lines), "status": "DRAFT"},
        reason=payload.note,
        request=request,
    )
    return transfer, False


async def _locked(
    session: AsyncSession, org_id: uuid.UUID, transfer_id: uuid.UUID
) -> StockTransfer:
    transfer = await sel.get_transfer(session, org_id, transfer_id, for_update=True)
    if transfer is None:
        raise NotFoundProblem("That transfer is not part of this organization")
    return transfer


async def update_draft(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    patch: TransferPatch,
    *,
    request: Request | None = None,
) -> StockTransfer:
    org_id = ctx.organization_id
    transfer = await _locked(session, org_id, transfer_id)
    if transfer.status != "DRAFT":
        raise ConflictProblem(f"A transfer that is {transfer.status} can no longer be edited")
    fields = patch.model_fields_set
    if "to_warehouse_id" in fields and patch.to_warehouse_id is not None:
        if patch.to_warehouse_id == transfer.from_warehouse_id:
            raise ValidationProblem("The destination must differ from the source warehouse")
        await _require_warehouse(session, org_id, patch.to_warehouse_id, "to_warehouse_id")
        transfer.to_warehouse_id = patch.to_warehouse_id
    if "note" in fields and patch.note is not None:
        transfer.note = patch.note
    if "expected_on" in fields:
        transfer.expected_on = patch.expected_on
    if patch.lines is not None:
        specs = await _validated_lines(session, org_id, transfer.from_warehouse_id, patch.lines)
        await session.execute(
            delete(StockTransferLine).where(
                StockTransferLine.organization_id == org_id,
                StockTransferLine.transfer_id == transfer.id,
            )
        )
        for line_no, spec in enumerate(specs, start=1):
            session.add(
                StockTransferLine(
                    organization_id=org_id,
                    transfer_id=transfer.id,
                    line_no=line_no,
                    inventory_item_id=spec["item_id"],
                    from_location_id=spec["from_location_id"],
                    lot_id=spec["lot_id"],
                    roll_id=spec["roll_id"],
                    quantity_milli=spec["quantity_milli"],
                )
            )
    transfer.updated_at = _now()
    await session.flush()
    common.audit_only(
        session,
        ctx,
        action="stock_transfer.updated",
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"fields": sorted(fields), "replaced_lines": patch.lines is not None},
        reason=None,
        request=request,
    )
    return transfer


# ------------------------------------------------------------------ request / approve / cancel


def _transfer_event(transfer: StockTransfer, **extra: Any) -> dict[str, Any]:
    return {
        "transfer_id": str(transfer.id),
        "number": transfer.number,
        "from_warehouse_id": str(transfer.from_warehouse_id),
        "to_warehouse_id": str(transfer.to_warehouse_id),
        **extra,
    }


async def request_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> StockTransfer:
    """DRAFT -> REQUESTED, and routed (`domain.transfer_route`): approved at once (`AUTO`), left
    for a person with `inventory.transfer.approve` (`DIRECT`), or handed to the approval service
    as a `operations.inventory.transfer.requested` event (`SERVICE`)."""
    org_id = ctx.organization_id
    transfer = await _locked(session, org_id, transfer_id)
    assert_transition(TRANSFER_TRANSITIONS, transfer.status, "REQUESTED", noun="transfer")
    lines = await sel.transfer_lines(session, org_id, transfer.id)
    if not lines:
        raise ValidationProblem("A transfer needs at least one line before it is requested")
    settings = await common.get_settings(session, org_id)
    threshold = settings.transfer_approval_threshold_milli if settings else None
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({ln.inventory_item_id for ln in lines}),
                )
            )
        ).scalars()
    }
    route = transfer_route(
        threshold,
        [to_base_milli(ln.quantity_milli, items[ln.inventory_item_id].uom) for ln in lines],
    )
    if route == "SERVICE" and ctx.subject_ref is None:
        raise ConflictProblem(
            "This transfer needs approval, and your account is not linked to an employee "
            "record the approval can be routed from"
        )
    refs = {
        w.id: w
        for w in (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == org_id,
                    Warehouse.id.in_({transfer.from_warehouse_id, transfer.to_warehouse_id}),
                )
            )
        ).scalars()
    }
    actor = _actor_str(ctx)
    _step(session, transfer, "REQUESTED", actor=actor)
    transfer.approval_route = route
    transfer.requested_by = actor
    transfer.requested_by_employee_id = ctx.subject_ref
    transfer.requested_at = _now()
    largest = max(lines, key=lambda ln: ln.quantity_milli)
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.requested",
        event_type=common.EVT_TRANSFER_REQUESTED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"route": route, "number": transfer.number},
        payload=_transfer_event(
            transfer,
            approval_route=route,
            approval_required=route == "SERVICE",
            employee_id=str(ctx.subject_ref) if route == "SERVICE" and ctx.subject_ref else None,
            line_count=len(lines),
            # Structured, enumerable facts only (ADR-0006 §3: no free text in the summary).
            summary={
                "transfer_number": transfer.number,
                "from_warehouse": refs[transfer.from_warehouse_id].code,
                "to_warehouse": refs[transfer.to_warehouse_id].code,
                "line_count": len(lines),
                "largest_line": (
                    f"{format_qty(largest.quantity_milli)} {items[largest.inventory_item_id].uom}"
                    f" {items[largest.inventory_item_id].sku}"
                ),
            },
            reason=transfer.note or None,
        ),
        reason=transfer.note,
        request=request,
    )
    if route == "AUTO":
        _approve(session, ctx, transfer, approved_by="auto", comment="under the threshold")
    await session.flush()
    return transfer


def _approve(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer: StockTransfer,
    *,
    approved_by: str,
    comment: str,
    approver_name: str | None = None,
) -> None:
    _step(session, transfer, "APPROVED", actor=approved_by, note=comment)
    transfer.approved_by = approved_by
    transfer.approved_at = _now()
    transfer.decision_comment = comment[:500]
    if approver_name is not None:
        transfer.approver_name = approver_name
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.approved",
        event_type=common.EVT_TRANSFER_APPROVED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"route": transfer.approval_route, "approved_by": approved_by},
        payload=_transfer_event(
            transfer, approval_route=transfer.approval_route, approved_by=approved_by
        ),
        reason=comment or None,
        request=None,
    )


async def approve_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    payload: TransferDecisionIn,
    *,
    request: Request | None = None,
) -> StockTransfer:
    """The direct sign-off. Not the requester's own, and not for a transfer the approval service
    is deciding."""
    transfer = await _locked(session, ctx.organization_id, transfer_id)
    _assert_direct(transfer, ctx, "approve")
    _approve(
        session,
        ctx,
        transfer,
        approved_by=_actor_str(ctx),
        comment=payload.comment,
    )
    await session.flush()
    return transfer


async def reject_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    payload: TransferDecisionIn,
    *,
    request: Request | None = None,
) -> StockTransfer:
    if not payload.comment.strip():
        raise ValidationProblem(
            "A rejection must say why",
            errors=[{"field": "comment", "code": "required", "message": "required"}],
        )
    transfer = await _locked(session, ctx.organization_id, transfer_id)
    _assert_direct(transfer, ctx, "reject")
    _reject(session, ctx, transfer, rejected_by=_actor_str(ctx), comment=payload.comment)
    await session.flush()
    return transfer


def _reject(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer: StockTransfer,
    *,
    rejected_by: str,
    comment: str,
    approver_name: str | None = None,
) -> None:
    _step(session, transfer, "REJECTED", actor=rejected_by, note=comment)
    transfer.approved_by = rejected_by
    transfer.approved_at = _now()
    transfer.decision_comment = comment[:500]
    if approver_name is not None:
        transfer.approver_name = approver_name
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.rejected",
        event_type=common.EVT_TRANSFER_REJECTED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"route": transfer.approval_route, "rejected_by": rejected_by},
        payload=_transfer_event(
            transfer, approval_route=transfer.approval_route, rejected_by=rejected_by
        ),
        reason=comment,
        request=None,
    )


def _assert_direct(transfer: StockTransfer, ctx: ScopeContext, verb: str) -> None:
    if transfer.status != "REQUESTED":
        raise ConflictProblem(f"A transfer that is {transfer.status} cannot be {verb}d")
    if transfer.approval_route != "DIRECT":
        raise ConflictProblem(
            "This transfer is decided through the approval service, not here"
            if transfer.approval_route == "SERVICE"
            else f"This transfer needs no direct {verb}"
        )
    if transfer.requested_by == _actor_str(ctx):
        raise ConflictProblem("The person who requested a transfer cannot approve or reject it")


async def cancel_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    payload: TransferCancelIn,
    *,
    request: Request | None = None,
) -> StockTransfer:
    """Before dispatch only. Once stock is in transit it is received (or its shortage written
    off); nothing moves backwards silently (ADR-0014 §6)."""
    transfer = await _locked(session, ctx.organization_id, transfer_id)
    if transfer.status == "IN_TRANSIT":
        raise ConflictProblem("The stock is in transit; receive it, or declare what is short")
    was = transfer.status
    _step(session, transfer, "CANCELLED", actor=_actor_str(ctx), note=payload.reason)
    transfer.cancelled_by = _actor_str(ctx)
    transfer.cancelled_at = _now()
    transfer.cancel_reason = payload.reason
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.cancelled",
        event_type=common.EVT_TRANSFER_CANCELLED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"was_status": was, "reason": payload.reason},
        payload=_transfer_event(
            transfer,
            was_status=was,
            approval_route=transfer.approval_route,
            reason=payload.reason,
        ),
        reason=payload.reason,
        request=request,
    )
    await session.flush()
    return transfer


# ------------------------------------------------------------------ the approval service's answer


async def record_approval_opened(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    *,
    approval_request_id: uuid.UUID,
    approver_name: str | None,
) -> bool:
    """`core.approval.request.opened`: remember which request is deciding this transfer, and who
    it is waiting on. A no-op once the transfer is past REQUESTED."""
    transfer = await _locked_or_none(session, org_id, transfer_id)
    if transfer is None or transfer.status != "REQUESTED" or transfer.approval_route != "SERVICE":
        return False
    transfer.approval_request_id = approval_request_id
    transfer.approver_name = (approver_name or "")[:200] or None
    return True


async def _locked_or_none(
    session: AsyncSession, org_id: uuid.UUID, transfer_id: uuid.UUID
) -> StockTransfer | None:
    return await sel.get_transfer(session, org_id, transfer_id, for_update=True)


async def apply_approval_decision(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    *,
    outcome: str,
    approval_request_id: uuid.UUID,
    decided_by_name: str | None,
    comment: str | None,
) -> str | None:
    """`core.approval.request.decided` -> the transfer's own state machine. Returns the outcome
    applied (`approved` | `rejected`), or `None` for a decision with nothing to act on (an unknown
    transfer, or one no longer REQUESTED — a redelivery, or cancelled while it was deciding).
    Publishes `transfer.applied`, the return leg the approval service records (ADR-0006 §12)."""
    transfer = await _locked_or_none(session, org_id, transfer_id)
    if transfer is None or transfer.status != "REQUESTED" or transfer.approval_route != "SERVICE":
        return None
    ctx = common.system_ctx(org_id)
    transfer.approval_request_id = approval_request_id
    by = "approval-service"
    note = (comment or "")[:500]
    if outcome == "approved":
        _approve(
            session, ctx, transfer, approved_by=by, comment=note, approver_name=decided_by_name
        )
    elif outcome == "rejected":
        _reject(session, ctx, transfer, rejected_by=by, comment=note, approver_name=decided_by_name)
    else:
        return None
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.approval_applied",
        event_type=common.EVT_TRANSFER_APPLIED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"decision": outcome, "approval_request_id": str(approval_request_id)},
        payload={
            "transfer_id": str(transfer.id),
            "approval_request_id": str(approval_request_id),
            "request_type": common.REQUEST_TYPE_TRANSFER,
            # ADR-0006 §12's return-leg vocabulary: the decision was applied to the transfer
            # (whichever way it went); `decision` says which way.
            "outcome": "applied",
            "decision": outcome,
        },
        reason=note or None,
        request=None,
    )
    await session.flush()
    return outcome


# ------------------------------------------------------------------ dispatch


async def _in_transit_location(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Location:
    location = await inv_selectors.default_location(session, org_id, warehouse_id)
    if location is None:
        raise ConflictProblem(
            "The destination warehouse has no default bin to hold stock in transit"
        )
    return location


def _roll_event(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    roll_id: uuid.UUID,
    move_id: uuid.UUID,
    quantity_milli: int,
    detail: dict[str, Any],
) -> None:
    session.add(
        RollEvent(
            organization_id=ctx.organization_id,
            roll_id=roll_id,
            kind="MOVED",
            move_id=move_id,
            quantity_milli=quantity_milli,
            detail=detail,
            created_by=_actor_str(ctx),
        )
    )


async def dispatch_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    payload: TransferDispatchIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
) -> tuple[StockTransfer, bool]:
    """APPROVED -> IN_TRANSIT. Returns `(transfer, replayed)`.

    A repeat of an already-dispatched transfer is a 409 — unless the caller dispatched it and
    sent an `Idempotency-Key`, which says "this is my retry": then it is answered with the
    transfer as it now stands, `replayed`."""
    org_id = ctx.organization_id
    transfer = await _locked(session, org_id, transfer_id)
    if transfer.status != "APPROVED":
        if (
            idempotency_key is not None
            and transfer.status in ("IN_TRANSIT", "RECEIVED")
            and transfer.dispatched_by == _actor_str(ctx)
        ):
            if payload.expected_on is not None and payload.expected_on != transfer.expected_on:
                raise ConflictProblem("That transfer was dispatched with a different expected date")
            return transfer, True
        raise ConflictProblem(f"A transfer that is {transfer.status} cannot be dispatched")
    lines = await sel.transfer_lines(session, org_id, transfer.id, for_update=True)
    in_transit = await _in_transit_location(session, org_id, transfer.to_warehouse_id)

    move_lines = [
        MoveLine(
            item_id=ln.inventory_item_id,
            location_id=ln.from_location_id,
            quantity_milli=-ln.quantity_milli,
            status="AVAILABLE",
            lot_id=ln.lot_id,
            roll_id=ln.roll_id,
        )
        for ln in lines
    ]
    inbound = merge_amounts(
        ((ln.inventory_item_id, ln.lot_id, ln.roll_id), ln.quantity_milli) for ln in lines
    )
    move_lines.extend(
        MoveLine(
            item_id=item_id,
            location_id=in_transit.id,
            quantity_milli=amount,
            status="IN_TRANSIT",
            lot_id=lot_id,
            roll_id=roll_id,
        )
        for (item_id, lot_id, roll_id), amount in inbound.items()
    )
    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=move_lines,
        occurred_on=date.today(),
        reference_type="stock_transfer",
        reference_id=transfer.id,
        note=payload.note or f"Dispatch of {transfer.number}",
        # One dispatch per transfer, ever: the kernel's idempotency index is the last line of
        # defence behind the status check above.
        idempotency_key=f"transfer:{transfer.id}:dispatch",
        occurred_on_explicit=False,
        request=request,
    )
    if posted.replayed:
        raise ConflictProblem("That transfer has already been dispatched")

    for ln in lines:
        ln.dispatched_milli = ln.quantity_milli
        ln.in_transit_location_id = in_transit.id
        if ln.roll_id is not None:
            _roll_event(
                session,
                ctx,
                roll_id=ln.roll_id,
                move_id=posted.move.id,
                quantity_milli=ln.quantity_milli,
                detail={
                    "transfer_id": str(transfer.id),
                    "phase": "dispatch",
                    "from_location_id": str(ln.from_location_id),
                    "to_location_id": str(in_transit.id),
                    "status": "IN_TRANSIT",
                },
            )
    _step(session, transfer, "IN_TRANSIT", actor=_actor_str(ctx), note=payload.note)
    transfer.dispatched_by = _actor_str(ctx)
    transfer.dispatched_at = _now()
    if payload.expected_on is not None:
        transfer.expected_on = payload.expected_on
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.dispatched",
        event_type=common.EVT_TRANSFER_DISPATCHED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"move_id": str(posted.move.id), "line_count": len(lines)},
        payload=_transfer_event(
            transfer,
            move_id=str(posted.move.id),
            in_transit_location_id=str(in_transit.id),
            lines=[
                {
                    "line_id": str(ln.id),
                    "inventory_item_id": str(ln.inventory_item_id),
                    "lot_id": str(ln.lot_id) if ln.lot_id else None,
                    "roll_id": str(ln.roll_id) if ln.roll_id else None,
                    "quantity": format_qty(ln.quantity_milli),
                }
                for ln in lines
            ],
        ),
        reason=payload.note or None,
        request=request,
    )
    await session.flush()
    return transfer, False


# ------------------------------------------------------------------ receive


def _key_digests(key: str, payload: TransferReceiveIn) -> tuple[str, str]:
    """`(key hash, body hash)`. A client key may be up to 128 characters and the derived keys
    (`transfer:<id>:receive:<khash>:<bhash>`, plus `:<line>` on a held shortage) must fit in 128,
    so it is hashed; the body digest rides in the derived key so a replay is recognised and a
    reused key with another body is not mistaken for one."""
    return (
        hashlib.sha256(key.encode()).hexdigest()[:16],
        common.request_hash(payload.model_dump(mode="json"))[:16],
    )


async def _prior_receipt(
    session: AsyncSession,
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    key: str,
    payload: TransferReceiveIn,
    actor: str,
) -> bool:
    """Whether this caller already posted this exact receipt under this client key. A key already
    used by someone else, or for another body, is a 409 — never a silent replay."""
    khash, bhash = _key_digests(key, payload)
    stem = f"transfer:{transfer_id}:"
    seen: list[tuple[str, str | None]] = []
    for entity, key_col, who_col in (
        (InventoryMove, InventoryMove.idempotency_key, InventoryMove.created_by),
        (AdjustmentRequest, AdjustmentRequest.idempotency_key, AdjustmentRequest.requested_by),
    ):
        rows = await session.execute(
            select(key_col, who_col).where(
                entity.organization_id == org_id,
                key_col.like(f"{stem}receive:{khash}:%")
                | key_col.like(f"{stem}shortage:{khash}:%"),
            )
        )
        seen.extend((k, w) for k, w in rows.all())
    if not seen:
        return False
    if any(k.split(":")[4] != bhash or w != actor for k, w in seen):
        raise ConflictProblem("That idempotency key was already used for a different receipt")
    return True


async def receive_transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    transfer_id: uuid.UUID,
    payload: TransferReceiveIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
) -> tuple[StockTransfer, bool]:
    """Account for stock arriving: `received` goes into the destination bin, `shortage` is
    written off from the in-transit bucket. Partial receipts are normal — the transfer stays
    IN_TRANSIT until every line is settled."""
    org_id = ctx.organization_id
    transfer = await _locked(session, org_id, transfer_id)
    if idempotency_key is not None and await _prior_receipt(
        session, org_id, transfer.id, idempotency_key, payload, _actor_str(ctx)
    ):
        return transfer, True
    if transfer.dispatched_by == _actor_str(ctx):
        raise ConflictProblem("A transfer cannot be received by the person who dispatched it")
    if transfer.status != "IN_TRANSIT":
        raise ConflictProblem(f"A transfer that is {transfer.status} cannot be received")

    lines = {
        ln.id: ln for ln in await sel.transfer_lines(session, org_id, transfer.id, for_update=True)
    }
    items = await stock_ops.load_items(
        session, org_id, {ln.inventory_item_id for ln in lines.values()}
    )
    default_bin = await _in_transit_location(session, org_id, transfer.to_warehouse_id)
    custom = {
        loc.id: loc
        for loc in (
            await session.execute(
                select(Location).where(
                    Location.organization_id == org_id,
                    Location.id.in_(
                        {rl.to_location_id for rl in payload.lines if rl.to_location_id}
                    ),
                    Location.deleted_at.is_(None),
                )
            )
        ).scalars()
    }

    plan: list[tuple[StockTransferLine, InventoryItem, int, int, uuid.UUID]] = []
    for index, rl in enumerate(payload.lines):
        line = lines.get(rl.line_id)
        if line is None:
            raise NotFoundProblem(f"Line {rl.line_id} is not part of this transfer")
        item = items[line.inventory_item_id]
        field = f"lines[{index}]"
        received = common.parse_non_negative(rl.received_quantity, item.uom, f"{field}.received")
        short = common.parse_non_negative(rl.shortage_quantity, item.uom, f"{field}.shortage")
        if received + short == 0:
            raise ValidationProblem(
                f"{item.sku}: nothing received and nothing short",
                errors=[{"field": field, "code": "empty", "message": "nothing to record"}],
            )
        remaining = line.dispatched_milli - line.received_milli - line.shortage_milli
        if received + short > remaining:
            raise ConflictProblem(
                f"{item.sku}: only {format_qty(remaining)} is still in transit; cannot account "
                f"for {format_qty(received + short)}"
            )
        if rl.to_location_id is not None:
            target = custom.get(rl.to_location_id)
            if target is None:
                raise NotFoundProblem(f"Location {rl.to_location_id} is not in this organization")
            if target.warehouse_id != transfer.to_warehouse_id:
                raise ValidationProblem(
                    f"Location {target.code} is not in the destination warehouse",
                    errors=[
                        {
                            "field": f"{field}.to_location_id",
                            "code": "wrong_warehouse",
                            "message": "not in the destination warehouse",
                        }
                    ],
                )
            to_location = target.id
        else:
            to_location = line.to_location_id or default_bin.id
        plan.append((line, item, received, short, to_location))

    today = date.today()
    stem = None
    if idempotency_key is not None:
        khash, bhash = _key_digests(idempotency_key, payload)
        stem = f"{khash}:{bhash}"
    receipt_key = f"transfer:{transfer.id}:receive:{stem}" if stem else None
    shortage_key = f"transfer:{transfer.id}:shortage:{stem}" if stem else None
    receipt_move = None
    shortage_move = None

    outflow = merge_amounts(
        (
            (line.inventory_item_id, line.in_transit_location_id, line.lot_id, line.roll_id),
            -received,
        )
        for line, _item, received, _short, _to in plan
        if received
    )
    inflow = merge_amounts(
        ((line.inventory_item_id, to_location, line.lot_id, line.roll_id), received)
        for line, _item, received, _short, to_location in plan
        if received
    )
    if outflow:
        receipt_lines = [
            MoveLine(item_id, loc, amount, status="IN_TRANSIT", lot_id=lot, roll_id=roll)
            for (item_id, loc, lot, roll), amount in outflow.items()
            if loc is not None
        ] + [
            MoveLine(item_id, loc, amount, status="AVAILABLE", lot_id=lot, roll_id=roll)
            for (item_id, loc, lot, roll), amount in inflow.items()
        ]
        receipt_move = await ledger.post_move(
            session,
            ctx,
            kind="relocation",
            lines=receipt_lines,
            occurred_on=today,
            reference_type="stock_transfer_receipt",
            reference_id=transfer.id,
            note=payload.note or f"Receipt of {transfer.number}",
            idempotency_key=receipt_key,
            occurred_on_explicit=False,
            request=request,
        )
        if receipt_move.replayed:
            return transfer, True

    # A shortage is a write-off. One that reaches the adjustment threshold is not ours to post:
    # it becomes an adjustment request (reason, evidence, approval) and the stock stays in transit
    # until it is decided. Such a shortage is NOT booked on the line below, so the line and the
    # transfer stay open while the units are still in transit; if approval rejects it, they stay
    # in the in-transit report for a person to resolve. shortcut: nothing reopens or closes the
    # line when the request is decided, upgrade when a transfer-aware approval hook exists.
    settings = await common.get_settings(session, org_id)
    threshold = settings.adjustment_approval_threshold_milli if settings else None
    direct: list[tuple[StockTransferLine, int]] = []
    pending: set[uuid.UUID] = set()
    for line, item, _received, short, _to in plan:
        if not short:
            continue
        if not over_threshold(threshold, [to_base_milli(short, item.uom)]):
            direct.append((line, short))
            continue
        pending.add(line.id)
        assert line.in_transit_location_id is not None
        await adjustments.create_adjustment(
            session,
            ctx,
            AdjustmentIn(
                inventory_item_id=line.inventory_item_id,
                location_id=line.in_transit_location_id,
                quantity_delta=format_qty(-short),
                reason="lost",
                lot_id=line.lot_id,
                roll_id=line.roll_id,
                note=payload.note or f"Shortage on {transfer.number}",
                evidence_document_id=payload.evidence_document_id,
            ),
            idempotency_key=f"{shortage_key}:{line.id}" if shortage_key else None,
            request=request,
            stock_status="IN_TRANSIT",
        )
    shortages = merge_amounts(
        (
            (line.inventory_item_id, line.in_transit_location_id, line.lot_id, line.roll_id),
            -short,
        )
        for line, short in direct
    )
    if shortages:
        shortage_move = await ledger.post_move(
            session,
            ctx,
            kind="adjustment",
            lines=[
                MoveLine(item_id, loc, amount, status="IN_TRANSIT", lot_id=lot, roll_id=roll)
                for (item_id, loc, lot, roll), amount in shortages.items()
                if loc is not None
            ],
            reason="lost",
            occurred_on=today,
            reference_type="stock_transfer_shortage",
            reference_id=transfer.id,
            note=payload.note or f"Shortage on {transfer.number}",
            idempotency_key=shortage_key,
            occurred_on_explicit=False,
            request=request,
        )
        if shortage_move.replayed:
            return transfer, True
        common.audit_only(
            session,
            ctx,
            action="stock_transfer.shortage_written_off",
            entity="stock_transfer",
            entity_id=transfer.id,
            after={
                "move_id": str(shortage_move.move.id),
                "lines": [
                    {"line_id": str(ln.id), "shortage": format_qty(short)} for ln, short in direct
                ],
            },
            reason=payload.note or None,
            request=request,
        )

    settled: list[dict[str, Any]] = []
    for line, _item, received, short, to_location in plan:
        if line.id in pending:
            short = 0  # awaiting an adjustment decision: still in transit, not written off
        line.received_milli += received
        line.shortage_milli += short
        if received:
            line.to_location_id = to_location
            if line.roll_id is not None and receipt_move is not None:
                _roll_event(
                    session,
                    ctx,
                    roll_id=line.roll_id,
                    move_id=receipt_move.move.id,
                    quantity_milli=received,
                    detail={
                        "transfer_id": str(transfer.id),
                        "phase": "receive",
                        "to_location_id": str(to_location),
                        "status": "AVAILABLE",
                    },
                )
        settled.append(
            {
                "line_id": str(line.id),
                "inventory_item_id": str(line.inventory_item_id),
                "received": format_qty(received),
                "shortage": format_qty(short),
            }
        )
    complete = all(
        ln.received_milli + ln.shortage_milli == ln.dispatched_milli for ln in lines.values()
    )
    if complete:
        _step(session, transfer, "RECEIVED", actor=_actor_str(ctx), note=payload.note)
        transfer.completed_at = _now()
    transfer.updated_at = _now()
    stock_ops.stage(
        session,
        ctx,
        action="stock_transfer.received",
        event_type=common.EVT_TRANSFER_RECEIVED,
        entity="stock_transfer",
        entity_id=transfer.id,
        after={"complete": complete, "lines": settled},
        payload=_transfer_event(
            transfer,
            complete=complete,
            receipt_move_id=str(receipt_move.move.id) if receipt_move else None,
            shortage_move_id=str(shortage_move.move.id) if shortage_move else None,
            lines=settled,
        ),
        reason=payload.note or None,
        request=request,
    )
    await session.flush()
    return transfer, False
