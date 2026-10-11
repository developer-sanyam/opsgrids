"""Purchase requisitions (inventory Phase 2): request -> approve -> convert to a purchase order.

Every function follows the platform shape: change the rows, write the audit row, stage the outbox
event — all in the caller's transaction. A decision (submit, approve, reject, cancel, convert)
locks the requisition row first and re-reads its status under that lock, so two people deciding
the same requisition cannot both succeed; the state machine is
`app.receiving.domain.REQUISITION_TRANSITIONS`.

Converting is the only place a requisition touches a purchase order, and it goes through the PO
module's own writer (`raise_purchase_order`), so the supplier-active check, the server-assigned
number and the open-PO rollup all apply exactly as they do for a hand-raised PO.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors as po_selectors
from app import services as po_services
from app.inventory import quantity
from app.inventory import selectors as inv_selectors
from app.inventory.stock_ops import load_items
from app.models import (
    AuditLog,
    InventoryItem,
    OutboxEvent,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)
from app.receiving import domain, selectors
from app.receiving.schemas import (
    RequisitionConvertIn,
    RequisitionIn,
    RequisitionLineIn,
    RequisitionPatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event

EVT_REQ_CREATED = "operations.procurement.purchase_requisition.created"
EVT_REQ_SUBMITTED = "operations.procurement.purchase_requisition.submitted"
EVT_REQ_APPROVED = "operations.procurement.purchase_requisition.approved"
EVT_REQ_REJECTED = "operations.procurement.purchase_requisition.rejected"
EVT_REQ_CONVERTED = "operations.procurement.purchase_requisition.converted"
EVT_REQ_CANCELLED = "operations.procurement.purchase_requisition.cancelled"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def _next_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"{org_id}:pr_number"}
    )
    prefix = f"PR-{date.today().year}-"
    seq = await selectors.next_sequence(session, PurchaseRequisition, org_id, prefix)
    return f"{prefix}{seq:04d}"


def _stage(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    *,
    action: str,
    event_type: str,
    extra: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None,
) -> None:
    detail = {"number": req.number, "status": req.status, **(extra or {})}
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="purchase_requisition",
        entity_id=req.id,
        after=detail,
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type="purchase_requisition",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "requisition_id": str(req.id),
            **detail,
        },
        actor=_actor(ctx),
    )


async def _validate_lines(
    session: AsyncSession, org_id: uuid.UUID, lines: list[RequisitionLineIn]
) -> dict[uuid.UUID, InventoryItem]:
    items = await load_items(session, org_id, (ln.inventory_item_id for ln in lines))
    for index, ln in enumerate(lines):
        try:
            milli = quantity.parse_qty(ln.quantity, items[ln.inventory_item_id].uom)
        except quantity.QuantityError as exc:
            raise ValidationProblem(
                str(exc),
                errors=[
                    {
                        "field": f"lines[{index}].quantity",
                        "code": "invalid_quantity",
                        "message": str(exc),
                    }
                ],
            ) from exc
        if milli <= 0:
            raise ValidationProblem(
                "A requested quantity must be greater than zero",
                errors=[
                    {
                        "field": f"lines[{index}].quantity",
                        "code": "invalid_quantity",
                        "message": "must be > 0",
                    }
                ],
            )
        if ln.suggested_supplier_id is not None and (
            await po_selectors.get_supplier(session, org_id, ln.suggested_supplier_id) is None
        ):
            raise NotFoundProblem("That supplier is not part of this organization")
    return items


def _add_lines(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    lines: list[RequisitionLineIn],
    items: dict[uuid.UUID, InventoryItem],
) -> None:
    for position, ln in enumerate(lines, start=1):
        session.add(
            PurchaseRequisitionLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                requisition_id=req.id,
                line_no=position,
                inventory_item_id=ln.inventory_item_id,
                quantity_milli=quantity.parse_qty(ln.quantity, items[ln.inventory_item_id].uom),
                suggested_supplier_id=ln.suggested_supplier_id,
                est_unit_price_minor=ln.est_unit_price_minor,
                note=ln.note,
            )
        )


async def _check_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID | None
) -> None:
    if (
        warehouse_id is not None
        and await inv_selectors.get_warehouse(session, org_id, warehouse_id) is None
    ):
        raise NotFoundProblem("That warehouse is not part of this organization")


async def create_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: RequisitionIn,
    *,
    request: Request | None = None,
) -> PurchaseRequisition:
    org_id = ctx.organization_id
    await _check_warehouse(session, org_id, payload.warehouse_id)
    items = await _validate_lines(session, org_id, payload.lines)
    req = PurchaseRequisition(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        number=await _next_number(session, org_id),
        status="DRAFT",
        requested_by_id=ctx.subject_ref,
        requested_by=payload.requested_by,
        warehouse_id=payload.warehouse_id,
        needed_by=payload.needed_by,
        reason=payload.reason,
    )
    session.add(req)
    await session.flush()
    _add_lines(session, ctx, req, payload.lines, items)
    await session.flush()
    _stage(
        session,
        ctx,
        req,
        action="purchase_requisition.created",
        event_type=EVT_REQ_CREATED,
        extra={"line_count": len(payload.lines)},
        request=request,
    )
    return req


async def update_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    payload: RequisitionPatch,
    *,
    request: Request | None = None,
) -> PurchaseRequisition:
    req = await _lock(session, ctx, req)
    if req.status != "DRAFT":
        raise ConflictProblem(
            f"Requisition {req.number} is {req.status}; only a draft can be edited"
        )
    changes = payload.model_dump(exclude_unset=True)
    lines = payload.lines
    changes.pop("lines", None)
    if "warehouse_id" in changes:
        await _check_warehouse(session, ctx.organization_id, changes["warehouse_id"])
    before = {field: str(getattr(req, field)) for field in changes}
    for field, value in changes.items():
        setattr(req, field, value)
    if lines is not None:
        items = await _validate_lines(session, ctx.organization_id, lines)
        await session.execute(
            delete(PurchaseRequisitionLine).where(
                PurchaseRequisitionLine.organization_id == ctx.organization_id,
                PurchaseRequisitionLine.requisition_id == req.id,
            )
        )
        _add_lines(session, ctx, req, lines, items)
    req.updated_by = str(ctx.principal.sub)
    req.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_requisition.updated",
        entity="purchase_requisition",
        entity_id=req.id,
        before=before,
        after={**{k: str(v) for k, v in changes.items()}, "lines_replaced": lines is not None},
        request=request,
    )
    return req


async def _lock(
    session: AsyncSession, ctx: ScopeContext, req: PurchaseRequisition
) -> PurchaseRequisition:
    """Re-read the requisition under its row lock — the status checked next is the committed one."""
    locked = (
        await session.execute(
            select(PurchaseRequisition)
            .where(
                PurchaseRequisition.id == req.id,
                PurchaseRequisition.organization_id == ctx.organization_id,
                PurchaseRequisition.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        raise NotFoundProblem("That requisition is not part of this organization")
    return locked


async def _move(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    new_status: str,
    *,
    verb: str,
    note: str | None,
    event_type: str,
    decision: bool,
    request: Request | None,
) -> PurchaseRequisition:
    req = await _lock(session, ctx, req)
    if not domain.can_transition_requisition(req.status, new_status):
        raise ConflictProblem(f"Requisition {req.number} is {req.status} and cannot be {verb}")
    if decision and req.created_by == str(ctx.principal.sub):
        raise ForbiddenProblem("A requester cannot decide their own requisition")
    now = datetime.now(UTC)
    req.status = new_status
    req.updated_by = str(ctx.principal.sub)
    req.updated_at = now
    if new_status == "SUBMITTED":
        req.submitted_at = now
    if decision:
        req.decided_at = now
        req.decided_by = str(ctx.principal.sub)
        req.decision_note = note
    elif note:
        req.decision_note = note
    await session.flush()
    _stage(
        session,
        ctx,
        req,
        action=f"purchase_requisition.{verb}",
        event_type=event_type,
        extra={"note": note} if note else None,
        reason=note,
        request=request,
    )
    return req


async def submit_requisition(
    session: AsyncSession, ctx: ScopeContext, req: PurchaseRequisition, *, request: Request | None
) -> PurchaseRequisition:
    lines = await selectors.requisition_lines(session, ctx.organization_id, req.id)
    if not lines:
        raise ValidationProblem("A requisition needs at least one line before it is submitted")
    return await _move(
        session,
        ctx,
        req,
        "SUBMITTED",
        verb="submitted",
        note=None,
        event_type=EVT_REQ_SUBMITTED,
        decision=False,
        request=request,
    )


async def approve_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    *,
    note: str | None,
    request: Request | None,
) -> PurchaseRequisition:
    return await _move(
        session,
        ctx,
        req,
        "APPROVED",
        verb="approved",
        note=note,
        event_type=EVT_REQ_APPROVED,
        decision=True,
        request=request,
    )


async def reject_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    *,
    reason: str,
    request: Request | None,
) -> PurchaseRequisition:
    return await _move(
        session,
        ctx,
        req,
        "REJECTED",
        verb="rejected",
        note=reason,
        event_type=EVT_REQ_REJECTED,
        decision=True,
        request=request,
    )


async def cancel_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    *,
    reason: str | None,
    request: Request | None,
) -> PurchaseRequisition:
    return await _move(
        session,
        ctx,
        req,
        "CANCELLED",
        verb="cancelled",
        note=reason,
        event_type=EVT_REQ_CANCELLED,
        decision=False,
        request=request,
    )


async def convert_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    req: PurchaseRequisition,
    payload: RequisitionConvertIn,
    *,
    request: Request | None = None,
) -> tuple[PurchaseRequisition, PurchaseOrder]:
    """APPROVED -> CONVERTED: one draft purchase order with this supplier, a line per requisition
    line. Prices come from `payload.prices` (per line id) or the requisition's own estimate, else 0
    — a draft PO is where a buyer finishes pricing. A PO line is a whole number of units, so a
    fractional requisition quantity is refused with the line named."""
    org_id = ctx.organization_id
    req = await _lock(session, ctx, req)
    if req.status != "APPROVED":
        raise ConflictProblem(
            f"Requisition {req.number} is {req.status}; only an approved requisition can be "
            "converted"
        )
    lines = await selectors.requisition_lines(session, org_id, req.id)
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({ln.inventory_item_id for ln in lines}),
                    InventoryItem.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    prices = {p.line_id: p.unit_price_minor for p in payload.prices}
    unknown = set(prices) - {ln.id for ln in lines}
    if unknown:
        raise ValidationProblem(
            "A price names a line that is not on this requisition",
            errors=[
                {
                    "field": "prices",
                    "code": "unknown_line",
                    "message": str(sorted(map(str, unknown))[0]),
                }
            ],
        )
    warehouse = payload.warehouse
    if warehouse is None and req.warehouse_id is not None:
        wh = await inv_selectors.get_warehouse(session, org_id, req.warehouse_id)
        warehouse = wh.name if wh else None
    if warehouse is None:
        raise ValidationProblem(
            "Name the warehouse the purchase order delivers to",
            errors=[{"field": "warehouse", "code": "required", "message": "warehouse required"}],
        )
    po_lines: list[dict[str, Any]] = []
    for ln in lines:
        item = items.get(ln.inventory_item_id)
        if item is None:
            raise NotFoundProblem(f"Item {ln.inventory_item_id} is no longer in this org")
        if ln.quantity_milli % quantity.QTY_SCALE:
            raise ValidationProblem(
                f"{item.sku}: a purchase order line is a whole number of {item.uom}; requisition "
                f"quantity {quantity.format_qty(ln.quantity_milli)} is not whole",
                errors=[
                    {
                        "field": f"lines[{ln.line_no - 1}].quantity",
                        "code": "not_whole",
                        "message": "not a whole number of units",
                    }
                ],
            )
        po_lines.append(
            {
                "sku": item.sku,
                "description": item.name,
                "quantity": ln.quantity_milli // quantity.QTY_SCALE,
                "uom": item.uom,
                "unit_price_minor": prices.get(ln.id, ln.est_unit_price_minor or 0),
            }
        )
    notes = f"From requisition {req.number}."
    if payload.notes:
        notes = f"{notes} {payload.notes}"
    po = await po_services.raise_purchase_order(
        session,
        ctx,
        supplier_id=payload.supplier_id,
        warehouse=warehouse,
        expected_date=payload.expected_date,
        currency=payload.currency,
        notes=notes,
        raised_by=req.requested_by,
        lines=po_lines,
        request=request,
    )
    req.status = "CONVERTED"
    req.converted_po_id = po.id
    req.updated_by = str(ctx.principal.sub)
    req.updated_at = datetime.now(UTC)
    await session.flush()
    _stage(
        session,
        ctx,
        req,
        action="purchase_requisition.converted",
        event_type=EVT_REQ_CONVERTED,
        extra={"purchase_order_id": str(po.id), "po_number": po.number},
        request=request,
    )
    return req, po
