"""Adjustments with a reason, evidence and — above the organization's threshold — approval
(inventory Phase 3; docs/inventory/movements.md).

Every adjustment made here is an `AdjustmentRequest` row, and applying it is one `adjustment`
move through `ledger.post_move`, never a write of a quantity. Under the threshold (or with none set)
it applies in the caller's own transaction. At or over it, it is held `PENDING_APPROVAL` and an
`operations.inventory.adjustment.requested` event asks the approval service (ADR-0006) to route it
to the requester's manager chain; the answer returns as `core.approval.request.decided`, and
`apply_approval_decision` posts the move (as the system, under a fixed idempotency key, so a
redelivered decision cannot post twice) or records why it could not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger, stock_ops
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import format_qty
from app.models import AdjustmentRequest, InventoryItem, StockTransferLine
from app.movements import common
from app.movements import selectors as sel
from app.movements.domain import (
    ADJUSTMENT_TRANSITIONS,
    assert_transition,
    format_number,
    over_threshold,
    to_base_milli,
)
from app.movements.schemas import AdjustmentCancelIn, AdjustmentIn
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ProblemError, ValidationProblem


def _now() -> datetime:
    return datetime.now(UTC)


async def create_adjustment(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: AdjustmentIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
    stock_status: str | None = None,
    transfer_line_id: uuid.UUID | None = None,
) -> tuple[AdjustmentRequest, bool]:
    """Returns `(row, replayed)`. `row.status` is `APPLIED` (applied now) or `PENDING_APPROVAL`.

    `stock_status` overrides the payload's on-site status for the one caller allowed to adjust an
    off-site bucket (a transfer shortage in the IN_TRANSIT bucket). `evidence_document_id` is
    required above the threshold but only recorded, not verified: operations has no client for the
    document service, so the approver sees the id and opens the document themselves.
    shortcut: existence in the org is unchecked, upgrade when a document lookup contract exists."""
    org_id = ctx.organization_id
    digest = common.request_hash(payload.model_dump(mode="json"))
    if idempotency_key is not None:
        await common.serialise_key(session, org_id, idempotency_key)
        existing = (
            await session.execute(
                sel.adjustments_stmt(ctx).where(
                    AdjustmentRequest.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # A key is its author's (see `transfers.create_transfer`).
            if existing.request_hash != digest or existing.requested_by != str(ctx.principal.sub):
                raise ConflictProblem(
                    "That idempotency key was already used for a different adjustment"
                )
            return existing, True

    item = (await stock_ops.load_items(session, org_id, [payload.inventory_item_id]))[
        payload.inventory_item_id
    ]
    location = await inv_selectors.get_location(session, org_id, payload.location_id)
    if location is None:
        raise NotFoundProblem(f"Location {payload.location_id} is not in this organization")
    stock_ops.check_tracking(item, payload.lot_id, payload.roll_id)
    delta = common.parse_signed(payload.quantity_delta, item.uom, "quantity_delta")

    settings = await common.get_settings(session, org_id)
    threshold = settings.adjustment_approval_threshold_milli if settings else None
    needs_approval = over_threshold(threshold, [to_base_milli(delta, item.uom)])
    if needs_approval:
        if payload.evidence_document_id is None:
            raise ValidationProblem(
                "An adjustment this size needs approval, and approval needs evidence: attach "
                "the supporting document",
                errors=[
                    {
                        "field": "evidence_document_id",
                        "code": "required",
                        "message": "required above the approval threshold",
                    }
                ],
            )
        if ctx.subject_ref is None:
            raise ConflictProblem(
                "This adjustment needs approval, and your account is not linked to an employee "
                "record the approval can be routed from"
            )

    number = format_number("ADJ", await common.next_number(session, org_id, "adjustment"))
    row = AdjustmentRequest(
        organization_id=org_id,
        number=number,
        inventory_item_id=item.id,
        location_id=location.id,
        stock_status=stock_status or payload.status,
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        quantity_delta_milli=delta,
        transfer_line_id=transfer_line_id,
        reason=payload.reason,
        note=payload.note,
        evidence_document_id=payload.evidence_document_id,
        occurred_on=payload.occurred_on,
        status="PENDING_APPROVAL",
        approval_required=needs_approval,
        requested_by=str(ctx.principal.sub),
        requested_by_employee_id=ctx.subject_ref,
        idempotency_key=idempotency_key,
        request_hash=digest if idempotency_key else None,
    )
    session.add(row)
    await session.flush()

    if not needs_approval:
        await _apply(session, ctx, row, item, request=request)
        return row, False

    stock_ops.stage(
        session,
        ctx,
        action="adjustment_request.requested",
        event_type=common.EVT_ADJUSTMENT_REQUESTED,
        entity="adjustment_request",
        entity_id=row.id,
        after={"number": number, "delta": format_qty(delta), "reason": row.reason},
        payload={
            "adjustment_id": str(row.id),
            "number": number,
            "approval_required": True,
            "employee_id": str(ctx.subject_ref),
            "inventory_item_id": str(item.id),
            "evidence_document_id": str(row.evidence_document_id),
            # Structured facts only (ADR-0006 §3): the free text goes in `reason`, never here.
            "summary": {
                "adjustment_number": number,
                "sku": item.sku,
                "quantity_delta": format_qty(delta),
                "uom": item.uom,
                "location": location.code,
                "reason_code": row.reason,
            },
            "reason": row.note or None,
        },
        reason=row.note,
        request=request,
    )
    return row, False


async def _apply(
    session: AsyncSession,
    ctx: ScopeContext,
    row: AdjustmentRequest,
    item: InventoryItem,
    *,
    request: Request | None,
) -> None:
    """Post the move and mark the request APPLIED. Raises whatever the kernel raises (an
    `InsufficientStock`, say) — the caller decides whether that fails a request or a whole call."""
    assert_transition(ADJUSTMENT_TRANSITIONS, row.status, "APPLIED", noun="adjustment")
    posted = await ledger.post_move(
        session,
        ctx,
        kind="adjustment",
        lines=[
            MoveLine(
                item_id=row.inventory_item_id,
                location_id=row.location_id,
                quantity_milli=row.quantity_delta_milli,
                status=row.stock_status,
                lot_id=row.lot_id,
                roll_id=row.roll_id,
            )
        ],
        reason=row.reason,
        note=row.note or f"Adjustment {row.number}",
        occurred_on=row.occurred_on,
        reference_type="adjustment_request",
        reference_id=row.id,
        # One posting per request, ever — the redelivery guard behind the status check.
        idempotency_key=f"adjustment-request:{row.id}",
        occurred_on_explicit=False,
        request=request,
    )
    row.status = "APPLIED"
    row.applied_move_id = posted.move.id
    row.applied_at = _now()
    row.updated_at = _now()
    await session.flush()
    _emit_applied(session, ctx, row, item, decision="approved", request=request)


def _emit_applied(
    session: AsyncSession,
    ctx: ScopeContext,
    row: AdjustmentRequest,
    item: InventoryItem | None,
    *,
    decision: str,
    request: Request | None,
) -> None:
    """The adjustment reached a final state. `outcome` is the approval service's own return-leg
    vocabulary (ADR-0006 §12: `applied` | `failed`, whichever way the decision went); `decision`
    is what was decided (`approved` | `rejected`, `approved` for an adjustment that needed no
    approval)."""
    outcome = "failed" if row.status == "FAILED" else "applied"
    payload: dict[str, Any] = {
        "adjustment_id": str(row.id),
        "number": row.number,
        "status": row.status,
        "outcome": outcome,
        "decision": decision,
        "request_type": common.REQUEST_TYPE_ADJUSTMENT,
        "approval_request_id": str(row.approval_request_id) if row.approval_request_id else None,
        "inventory_item_id": str(row.inventory_item_id),
        "location_id": str(row.location_id),
        "quantity_delta": format_qty(row.quantity_delta_milli),
        "reason_code": row.reason,
        "move_id": str(row.applied_move_id) if row.applied_move_id else None,
        "failure_reason": row.failure_reason or None,
    }
    stock_ops.stage(
        session,
        ctx,
        action=f"adjustment_request.{row.status.lower()}",
        event_type=common.EVT_ADJUSTMENT_APPLIED,
        entity="adjustment_request",
        entity_id=row.id,
        after={"status": row.status, "outcome": outcome, "sku": item.sku if item else None},
        payload=payload,
        reason=row.note or None,
        request=request,
    )


async def cancel_adjustment(
    session: AsyncSession,
    ctx: ScopeContext,
    adjustment_id: uuid.UUID,
    payload: AdjustmentCancelIn,
    *,
    request: Request | None = None,
) -> AdjustmentRequest:
    """Withdraw an adjustment still waiting for approval. The cancellation event tells the
    approval service to withdraw its request too."""
    row = await sel.get_adjustment(session, ctx.organization_id, adjustment_id, for_update=True)
    if row is None:
        raise NotFoundProblem("That adjustment is not part of this organization")
    assert_transition(ADJUSTMENT_TRANSITIONS, row.status, "CANCELLED", noun="adjustment")
    row.status = "CANCELLED"
    row.cancelled_by = str(ctx.principal.sub)
    row.cancelled_at = _now()
    row.updated_at = _now()
    row.decision_comment = payload.reason[:500]
    stock_ops.stage(
        session,
        ctx,
        action="adjustment_request.cancelled",
        event_type=common.EVT_ADJUSTMENT_CANCELLED,
        entity="adjustment_request",
        entity_id=row.id,
        after={"reason": payload.reason},
        payload={
            "adjustment_id": str(row.id),
            "number": row.number,
            "approval_request_id": str(row.approval_request_id)
            if row.approval_request_id
            else None,
            "employee_id": str(row.requested_by_employee_id)
            if row.requested_by_employee_id
            else None,
            "was_status": "PENDING_APPROVAL",
            "reason": payload.reason or None,
        },
        reason=payload.reason or None,
        request=request,
    )
    await session.flush()
    await _release_transfer_line(session, ctx, row, written_off=False)
    return row


async def _release_transfer_line(
    session: AsyncSession, ctx: ScopeContext, row: AdjustmentRequest, *, written_off: bool
) -> None:
    """A transfer-shortage request reached a final state: settle or release its line."""
    if row.transfer_line_id is not None:
        from app.movements import transfers  # transfers imports this module

        await transfers.settle_pending_shortage(session, ctx, row, written_off=written_off)


# ------------------------------------------------------------------ the approval service's answer


async def record_approval_opened(
    session: AsyncSession,
    org_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    *,
    approval_request_id: uuid.UUID,
    approver_name: str | None,
) -> bool:
    row = await sel.get_adjustment(session, org_id, adjustment_id, for_update=True)
    if row is None or row.status != "PENDING_APPROVAL" or not row.approval_required:
        return False
    row.approval_request_id = approval_request_id
    row.approver_name = (approver_name or "")[:200] or None
    return True


async def apply_approval_decision(
    session: AsyncSession,
    org_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    *,
    outcome: str,
    approval_request_id: uuid.UUID,
    decided_by_name: str | None,
    comment: str | None,
) -> str | None:
    """`core.approval.request.decided`: post the adjustment on approval, close it on rejection.

    Returns what became of the request — `approved` (applied), `rejected`, or `failed` (approved,
    but the stock could not take it: another movement drained the bucket in the meantime) — or
    `None` when there is nothing to act on (unknown, or no longer pending). The kernel's refusal is
    caught inside a savepoint so one unpostable request records its reason and never poisons the
    consumer's transaction; the approval service is told either way (`adjustment.applied`)."""
    row = await sel.get_adjustment(session, org_id, adjustment_id, for_update=True)
    if row is None or row.status != "PENDING_APPROVAL" or not row.approval_required:
        return None
    if outcome not in ("approved", "rejected"):
        return None
    ctx = common.system_ctx(org_id)
    row.approval_request_id = approval_request_id
    row.decided_by_name = (decided_by_name or "")[:200] or None
    row.decided_at = _now()
    row.decision_comment = (comment or "")[:500]

    if outcome == "rejected":
        assert_transition(ADJUSTMENT_TRANSITIONS, row.status, "REJECTED", noun="adjustment")
        row.status = "REJECTED"
        row.updated_at = _now()
        await session.flush()
        _emit_applied(session, ctx, row, None, decision="rejected", request=None)
        await _release_transfer_line(session, ctx, row, written_off=False)
        return "rejected"

    if row.transfer_line_id is not None:
        # receive_transfer locks transfer -> lines -> balances; take the same order before _apply
        # locks the IN_TRANSIT balance, or a decision and a receipt on one transfer deadlock.
        from app.movements import transfers  # transfers imports this module

        transfer_id = (
            await session.execute(
                select(StockTransferLine.transfer_id).where(
                    StockTransferLine.organization_id == org_id,
                    StockTransferLine.id == row.transfer_line_id,
                )
            )
        ).scalar_one_or_none()
        if transfer_id is not None:
            await transfers.lock_transfer_and_lines(session, org_id, transfer_id)
    try:
        item: InventoryItem | None = (
            await stock_ops.load_items(session, org_id, [row.inventory_item_id])
        )[row.inventory_item_id]
    except NotFoundProblem:
        item = None  # deleted while the request waited: recorded as FAILED below, not retried
    try:
        async with session.begin_nested():
            if item is None:
                raise ConflictProblem("That inventory item no longer exists")
            await _apply(session, ctx, row, item, request=None)
    except ProblemError as exc:
        await session.refresh(row)
        row.approval_request_id = approval_request_id
        row.decided_by_name = (decided_by_name or "")[:200] or None
        row.decided_at = _now()
        row.decision_comment = (comment or "")[:500]
        row.status = "FAILED"
        row.failure_reason = (getattr(exc, "detail", None) or str(exc))[:500]
        row.updated_at = _now()
        await session.flush()
        _emit_applied(session, ctx, row, item, decision="approved", request=None)
        await _release_transfer_line(session, ctx, row, written_off=False)
        return "failed"
    await _release_transfer_line(session, ctx, row, written_off=True)
    return "approved"
