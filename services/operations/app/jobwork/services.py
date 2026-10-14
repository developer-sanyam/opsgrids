"""Writes for job work (Inventory plan Phase 6).

**Order-row-first locking.** Every write that changes an order takes `SELECT … FOR UPDATE` on the
order row before anything else (and, for the writes that touch materials, on the materials in id
order after it) — the same anchor-row-first shape `app.counting.services` and
`app.orders.services` use. That lock is what makes the state machine race-safe (two concurrent
issues of one order: the second sees `ISSUED`), what serialises two receipts against the same
material, and what makes an `Idempotency-Key` replay deterministic: a duplicate waits for the
first, then finds its result.

**Stock moves only through the kernel.** Material going to the vendor is a relocation
`AVAILABLE -> AT_JOB_WORKER` at the same bin; material coming back is a relocation out of
`AT_JOB_WORKER` into `AVAILABLE` (or `QUALITY_HOLD` when the order wants a QC hand-off) and
`REJECTED`; a shortage the vendor reports is an adjustment (`lost`) out of `AT_JOB_WORKER`. All of
it goes through `app.inventory.ledger.post_move` and carries `reference_type="job_work_order"`
so the reconciliation can sum the order's own `AT_JOB_WORKER` rows. Nothing here touches a
balance or a ledger row directly.

**Events after commit.** Every event is staged in the request's transaction through the outbox
and published by the relay only once that transaction commits.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import QuantityError, format_qty, parse_qty, value_minor
from app.jobwork import domain, numbering, selectors
from app.jobwork.schemas import JobWorkMaterialIn, JobWorkReceiptLineIn
from app.models import (
    AuditLog,
    Challan,
    JobWorkMaterial,
    JobWorkOrder,
    JobWorkReceipt,
    OutboxEvent,
)
from platform_core.audit import record_audit
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import actor_for, publish_event
from platform_core.logging import get_logger

log = get_logger("operations.jobwork")

EVT_ISSUED = "operations.jobwork.issued"
EVT_RECEIVED = "operations.jobwork.received"
EVT_OVERDUE = "operations.jobwork.overdue"
EVT_CLOSED = "operations.jobwork.closed"
#: A fire-and-forget request to `services/finance`'s own e-way-bill machinery — never a second
#: implementation here, and never a direct call. Like
#: `operations.dispatch.shipment.eway_requested` before it, this ships **ahead of its consumer**:
#: finance's generator is keyed on an *invoice*, and a delivery challan is not one, so a finance
#: consumer (a challan-keyed generation path) is a documented follow-up, not something this
#: module can fake.
EVT_CHALLAN_EWAY_REQUESTED = "operations.jobwork.challan.eway_requested"
#: The QC hand-off. The quality module is built on another branch; this module neither imports
#: nor waits for it — it parks the returned goods in `QUALITY_HOLD` and says so.
EVT_QC_REQUESTED = "operations.jobwork.receipt.qc_requested"

REFERENCE_TYPE = selectors.REFERENCE_TYPE
_MAX_KEY = 128


class ReconciliationNotBalanced(ConflictProblem):
    """`:close` on an order whose documents do not settle, or whose documents and ledger
    disagree. `errors` carries the figures so the screen can show the gap, not just refuse."""

    problem_type = "jobwork-reconciliation-not-balanced"
    title = "Reconciliation does not balance"


def _hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_key(key: str | None) -> None:
    if key is not None and not 1 <= len(key) <= _MAX_KEY:
        raise ValidationProblem(f"An idempotency key is 1-{_MAX_KEY} characters")


def _kernel_key(order_id: uuid.UUID, key: str, suffix: str) -> str:
    """The ledger's own idempotency key for one move of one request — derived, so a client key of
    any length fits the kernel's limit and two requests never share one."""
    digest = hashlib.sha256(f"{order_id}:{key}".encode()).hexdigest()[:32]
    return f"jw-recv:{digest}:{suffix}"


def _parse_positive(value: str, uom: str, what: str) -> int:
    try:
        milli = parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(f"{what}: {exc}") from exc
    if milli <= 0:
        raise ValidationProblem(f"{what} must be greater than zero")
    return milli


def _parse_non_negative(value: str, uom: str, what: str) -> int:
    try:
        return parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(f"{what}: {exc}") from exc


def _audit(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None,
) -> None:
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        reason=reason,
        request=request,
    )


def _emit(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    event_type: str,
    aggregate_id: uuid.UUID,
    aggregate_type: str = "job_work_order",
    payload: dict[str, Any],
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload={"organization_id": str(ctx.organization_id), **payload},
        actor=actor_for(ctx),
    )


async def _lock_order(
    session: AsyncSession, ctx: ScopeContext, order_id: uuid.UUID
) -> JobWorkOrder:
    order = await selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That job-work order is not part of this organization")
    return order


# ------------------------------------------------------------------ create / edit


async def _resolve_locations(
    session: AsyncSession,
    org_id: uuid.UUID,
    ids: set[uuid.UUID],
    *,
    warehouse_id: uuid.UUID | None,
) -> dict[uuid.UUID, uuid.UUID]:
    """`location id -> warehouse id` for live locations in this organization; an unknown one is
    a 404 (it may exist in another tenant, and the answer must not say so)."""
    found: dict[uuid.UUID, uuid.UUID] = {}
    for location_id in ids:
        location = await inv_selectors.get_location(session, org_id, location_id)
        if location is None:
            raise NotFoundProblem(f"Location {location_id} is not part of this organization")
        if warehouse_id is not None and location.warehouse_id != warehouse_id:
            raise ValidationProblem(
                f"Location {location.code} is not in the order's source warehouse"
            )
        found[location_id] = location.warehouse_id
    return found


async def create_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    supplier_id: uuid.UUID,
    process: str,
    due_date: date | None,
    source_warehouse_id: uuid.UUID | None,
    receive_location_id: uuid.UUID | None,
    qc_required: bool,
    notes: str,
    materials: list[JobWorkMaterialIn],
    idempotency_key: str | None,
    request: Request | None = None,
) -> tuple[JobWorkOrder, bool]:
    """A `DRAFT` order and its materials. Returns `(order, replayed)`; a retried
    `Idempotency-Key` with the same body returns the first order, with a different body a 409."""
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    request_hash = _hash(
        {
            "actor": ctx.principal.sub,
            "supplier_id": supplier_id,
            "process": process,
            "due_date": due_date,
            "source_warehouse_id": source_warehouse_id,
            "receive_location_id": receive_location_id,
            "qc_required": qc_required,
            "notes": notes,
            "materials": [m.model_dump(mode="json") for m in materials],
        }
    )
    if idempotency_key is not None:
        # Serialise duplicates of one key: the second waits here, then finds the first's order.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"{org_id}:jobwork_idem:{idempotency_key}"},
        )
        existing = await selectors.get_order_by_key(session, org_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConflictProblem(
                    "That Idempotency-Key was already used for a different job-work order"
                )
            return existing, True

    supplier = await selectors.get_supplier(session, org_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    if (
        source_warehouse_id is not None
        and await inv_selectors.get_warehouse(session, org_id, source_warehouse_id) is None
    ):
        raise NotFoundProblem("That warehouse is not part of this organization")

    items = await selectors.items_by_id(session, org_id, [m.item_id for m in materials])
    for m in materials:
        if m.item_id not in items:
            raise NotFoundProblem(f"Inventory item {m.item_id} is not part of this organization")

    # Default each material's bucket to the item's home bin, then validate every location once.
    chosen: list[uuid.UUID] = []
    for m in materials:
        location_id = m.location_id or items[m.item_id].home_location_id
        if location_id is None:
            raise ValidationProblem(f"{items[m.item_id].sku} has no home location; name one")
        chosen.append(location_id)
    await _resolve_locations(session, org_id, set(chosen), warehouse_id=source_warehouse_id)
    if receive_location_id is not None:
        # The return destination may be a different warehouse (a finishing plant), so it is only
        # checked for existence, not against the source warehouse.
        await _resolve_locations(session, org_id, {receive_location_id}, warehouse_id=None)

    seen: set[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None]] = set()
    parsed: list[tuple[JobWorkMaterialIn, uuid.UUID, int]] = []
    for m, location_id in zip(materials, chosen, strict=True):
        item = items[m.item_id]
        bucket = (m.item_id, location_id, m.lot_id, m.roll_id)
        if bucket in seen:
            raise ValidationProblem(
                f"{item.sku} appears twice for the same location, lot and roll; merge the lines"
            )
        seen.add(bucket)
        parsed.append(
            (m, location_id, _parse_positive(m.quantity, item.uom, f"{item.sku} quantity"))
        )

    # A named lot or roll must exist in this organization and belong to the line's item — the
    # check a posted move makes, answered here as 404 / 422 at drafting time rather than as a
    # refusal when the order is issued. One call for every line: the rolls are share-locked in id
    # order. Negative quantities model the issue out of AVAILABLE (no "cannot receive" check).
    await ledger.validate_tracking(
        session,
        org_id,
        [
            MoveLine(
                item_id=m.item_id,
                location_id=location_id,
                quantity_milli=-milli,
                status="AVAILABLE",
                lot_id=m.lot_id,
                roll_id=m.roll_id,
            )
            for m, location_id, milli in parsed
            if m.lot_id is not None or m.roll_id is not None
        ],
    )

    number = await numbering.next_order_number(session, org_id)
    order = JobWorkOrder(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        number=number,
        supplier_id=supplier_id,
        process=process,
        status="DRAFT",
        due_date=due_date,
        source_warehouse_id=source_warehouse_id,
        receive_location_id=receive_location_id,
        qc_required=qc_required,
        notes=notes,
        version=1,
        idempotency_key=idempotency_key,
        request_hash=request_hash if idempotency_key else None,
        transition_keys={},
    )
    session.add(order)
    await session.flush()
    for line_no, (m, location_id, milli) in enumerate(parsed, start=1):
        item = items[m.item_id]
        session.add(
            JobWorkMaterial(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                order_id=order.id,
                line_no=line_no,
                item_id=m.item_id,
                lot_id=m.lot_id,
                roll_id=m.roll_id,
                location_id=location_id,
                sku=item.sku,
                uom=item.uom,
                unit_value_minor=item.unit_value_minor,
                currency=item.currency,
                quantity_milli=milli,
            )
        )
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.order.created",
        entity="job_work_order",
        entity_id=order.id,
        after={
            "number": number,
            "supplier_id": str(supplier_id),
            "process": process,
            "materials": [
                {"item_id": str(m.item_id), "quantity": format_qty(milli)}
                for m, _loc, milli in parsed
            ],
        },
        request=request,
    )
    return order, False


async def update_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    expected_version: int,
    fields: dict[str, Any],
    clear_due_date: bool,
    request: Request | None = None,
) -> JobWorkOrder:
    """Edit the header of a `DRAFT` order. Materials are immutable once created: a wrong line is
    a cancelled draft and a new one, which keeps the planned quantities the challan will be cut
    from beyond argument."""
    order = await _lock_order(session, ctx, order_id)
    if order.status not in domain.EDITABLE_STATUSES:
        raise ConflictProblem(f"{order.number} is {order.status}; only a draft can be edited")
    if order.version != expected_version:
        raise ConflictProblem(
            f"{order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if "receive_location_id" in fields and fields["receive_location_id"] is not None:
        await _resolve_locations(
            session, ctx.organization_id, {fields["receive_location_id"]}, warehouse_id=None
        )
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for name in ("process", "qc_required", "notes", "receive_location_id"):
        if name in fields and fields[name] is not None:
            before[name] = getattr(order, name)
            setattr(order, name, fields[name])
            after[name] = fields[name]
    if clear_due_date:
        before["due_date"] = order.due_date
        order.due_date = None
        after["due_date"] = None
    elif fields.get("due_date") is not None:
        before["due_date"] = order.due_date
        order.due_date = fields["due_date"]
        after["due_date"] = fields["due_date"]
    order.version += 1
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.order.updated",
        entity="job_work_order",
        entity_id=order.id,
        before=before,
        after=after,
        request=request,
    )
    return order


# ------------------------------------------------------------------ plain transitions


def _check_replay(order: JobWorkOrder, key: str | None, to_status: str) -> bool:
    """True when `key` already produced `to_status` on this order — a retry to replay. A key
    that produced something else is a client bug, refused rather than guessed at."""
    if key is None:
        return False
    previous = order.transition_keys.get(key)
    if previous is None:
        return False
    if previous != to_status:
        raise ConflictProblem("That Idempotency-Key was already used for a different action")
    return True


def _remember(order: JobWorkOrder, key: str | None, to_status: str) -> None:
    if key is not None:
        # A new dict, so SQLAlchemy sees the JSONB column change.
        order.transition_keys = {**order.transition_keys, key: to_status}


def _move(order: JobWorkOrder, ctx: ScopeContext, to_status: str) -> str:
    if not domain.can_transition(order.status, to_status):
        raise ConflictProblem(f"{order.number} is {order.status} and cannot move to {to_status}")
    previous = order.status
    order.status = to_status
    order.version += 1
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    return previous


async def approve_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    reason: str = "",
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> JobWorkOrder:
    _check_key(idempotency_key)
    order = await _lock_order(session, ctx, order_id)
    if _check_replay(order, idempotency_key, "APPROVED"):
        return order
    materials = await selectors.materials_for(session, ctx.organization_id, order.id)
    if not materials:
        raise ConflictProblem(f"{order.number} has no materials to send")
    if await selectors.get_supplier(session, ctx.organization_id, order.supplier_id) is None:
        raise ConflictProblem(f"{order.number}'s supplier no longer exists")
    previous = _move(order, ctx, "APPROVED")
    order.approved_by = str(ctx.principal.sub)
    order.approved_at = datetime.now(UTC)
    _remember(order, idempotency_key, "APPROVED")
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.order.approved",
        entity="job_work_order",
        entity_id=order.id,
        before={"status": previous},
        after={"status": "APPROVED"},
        reason=reason or None,
        request=request,
    )
    return order


async def cancel_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    reason: str = "",
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> JobWorkOrder:
    _check_key(idempotency_key)
    order = await _lock_order(session, ctx, order_id)
    if _check_replay(order, idempotency_key, "CANCELLED"):
        return order
    previous = _move(order, ctx, "CANCELLED")
    order.cancelled_at = datetime.now(UTC)
    _remember(order, idempotency_key, "CANCELLED")
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.order.cancelled",
        entity="job_work_order",
        entity_id=order.id,
        before={"status": previous},
        after={"status": "CANCELLED"},
        reason=reason or None,
        request=request,
    )
    return order


# ------------------------------------------------------------------ issue


async def issue_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    vehicle_no: str | None,
    transporter_id: str | None,
    distance_km: int | None,
    note: str,
    idempotency_key: str,
    inter_state: bool = False,
    request: Request | None = None,
) -> tuple[JobWorkOrder, Challan, bool]:
    """Send the materials to the vendor: move stock `AVAILABLE -> AT_JOB_WORKER`, cut the delivery
    challan, and — when the consignment is worth an e-way bill — ask finance to consider one.

    One transaction: the stock move, the challan, the order's new status, the audit rows and both
    events commit together or not at all. Returns `(order, challan, replayed)`.
    """
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    order = await _lock_order(session, ctx, order_id)
    request_hash = _hash(
        {
            "actor": ctx.principal.sub,
            "order_id": order.id,
            "vehicle_no": vehicle_no,
            "transporter_id": transporter_id,
            "distance_km": distance_km,
            "inter_state": inter_state,
            "note": note,
        }
    )
    existing = await selectors.get_challan_by_key(session, org_id, idempotency_key)
    if existing is not None:
        if existing.order_id != order.id or existing.request_hash != request_hash:
            raise ConflictProblem("That Idempotency-Key was already used for a different request")
        return order, existing, True

    if order.status != "APPROVED":
        raise ConflictProblem(f"{order.number} is {order.status}; only an approved order is issued")

    supplier = await selectors.get_supplier(session, org_id, order.supplier_id)
    if supplier is None:
        raise ConflictProblem(f"{order.number}'s supplier no longer exists")
    if supplier.status == "on_hold":
        raise ConflictProblem(f"Supplier {supplier.name} is on hold; material cannot be sent")

    materials = await selectors.materials_for(session, org_id, order.id, for_update=True)
    materials.sort(key=lambda m: m.line_no)
    if not materials:
        raise ConflictProblem(f"{order.number} has no materials to send")
    items = await selectors.items_by_id(session, org_id, [m.item_id for m in materials])
    for m in materials:
        if m.item_id not in items:
            raise ConflictProblem(f"{m.sku} is no longer an active item")

    # Value the consignment at today's item value, one currency only.
    currencies = {items[m.item_id].currency for m in materials}
    if len(currencies) > 1:
        raise ValidationProblem(
            f"Materials are valued in more than one currency ({', '.join(sorted(currencies))}); "
            "split the order so each challan has one"
        )
    currency = currencies.pop()
    lines_snapshot: list[dict[str, Any]] = []
    total_value = 0
    move_lines: list[MoveLine] = []
    for m in materials:
        item = items[m.item_id]
        m.unit_value_minor = item.unit_value_minor
        m.currency = item.currency
        line_value = value_minor(m.quantity_milli, item.unit_value_minor)
        total_value += line_value
        lines_snapshot.append(
            {
                "sku": m.sku,
                "uom": m.uom,
                "quantity": format_qty(m.quantity_milli),
                "unit_value_minor": item.unit_value_minor,
                "value_minor": line_value,
                "lot_id": str(m.lot_id) if m.lot_id else None,
                "roll_id": str(m.roll_id) if m.roll_id else None,
            }
        )
        move_lines.append(
            MoveLine(
                item_id=m.item_id,
                location_id=m.location_id,
                quantity_milli=-m.quantity_milli,
                status="AVAILABLE",
                lot_id=m.lot_id,
                roll_id=m.roll_id,
            )
        )
        move_lines.append(
            MoveLine(
                item_id=m.item_id,
                location_id=m.location_id,
                quantity_milli=m.quantity_milli,
                status="AT_JOB_WORKER",
                lot_id=m.lot_id,
                roll_id=m.roll_id,
            )
        )

    # The kernel is the only writer: it locks the buckets, refuses a shortfall with the numbers
    # (409 `insufficient-stock`), and — keyed on the order — can never post this issue twice.
    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=move_lines,
        occurred_on=domain.ist_today(),
        reference_type=REFERENCE_TYPE,
        reference_id=order.id,
        note=f"Issued to job worker on {order.number}"[:500],
        idempotency_key=f"jw-issue:{order.id}",
        occurred_on_explicit=False,
        request=request,
    )

    for m in materials:
        m.sent_milli = m.quantity_milli
        m.updated_at = datetime.now(UTC)

    number = await numbering.next_challan_number(session, org_id)
    required = domain.eway_required(total_value, currency, inter_state=inter_state)
    now = datetime.now(UTC)
    challan = Challan(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        order_id=order.id,
        challan_number=number,
        kind="DELIVERY",
        issued_at=now,
        supplier_id=order.supplier_id,
        supplier_name=supplier.name,
        process=order.process,
        lines=lines_snapshot,
        total_value_minor=total_value,
        currency=currency,
        vehicle_no=vehicle_no,
        transporter_id=transporter_id,
        distance_km=distance_km,
        eway_status="REQUESTED" if required else "NOT_REQUIRED",
        eway_requested_at=now if required else None,
        move_id=posted.move.id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    session.add(challan)

    previous = _move(order, ctx, "ISSUED")
    order.issued_at = now
    await session.flush()

    _audit(
        session,
        ctx,
        action="jobwork.order.issued",
        entity="job_work_order",
        entity_id=order.id,
        before={"status": previous},
        after={
            "status": "ISSUED",
            "challan_number": number,
            "move_id": str(posted.move.id),
            "total_value_minor": total_value,
        },
        reason=note or None,
        request=request,
    )
    _audit(
        session,
        ctx,
        action="jobwork.challan.issued",
        entity="challan",
        entity_id=challan.id,
        after={"challan_number": number, "order_id": str(order.id)},
        request=request,
    )
    _emit(
        session,
        ctx,
        event_type=EVT_ISSUED,
        aggregate_id=order.id,
        payload={
            "order_id": str(order.id),
            "order_number": order.number,
            "supplier_id": str(order.supplier_id),
            "challan_id": str(challan.id),
            "challan_number": number,
            "due_date": order.due_date.isoformat() if order.due_date else None,
            "total_value_minor": total_value,
            "currency": currency,
            "lines": [
                {
                    "material_id": str(m.id),
                    "item_id": str(m.item_id),
                    "sku": m.sku,
                    "quantity": format_qty(m.quantity_milli),
                    "uom": m.uom,
                    "location_id": str(m.location_id),
                    "lot_id": str(m.lot_id) if m.lot_id else None,
                    "roll_id": str(m.roll_id) if m.roll_id else None,
                }
                for m in materials
            ],
        },
    )
    if required:
        _emit_eway_request(session, ctx, order=order, challan=challan)
    return order, challan, False


def _emit_eway_request(
    session: AsyncSession, ctx: ScopeContext, *, order: JobWorkOrder, challan: Challan
) -> None:
    _emit(
        session,
        ctx,
        event_type=EVT_CHALLAN_EWAY_REQUESTED,
        aggregate_id=challan.id,
        aggregate_type="challan",
        payload={
            "challan_id": str(challan.id),
            "challan_number": challan.challan_number,
            "order_id": str(order.id),
            "order_number": order.number,
            "supplier_id": str(challan.supplier_id),
            "consignment_value_minor": challan.total_value_minor,
            "currency": challan.currency,
            "supply_reason": "job_work",
            "distance_km": challan.distance_km,
            "vehicle_no": challan.vehicle_no,
            "transporter_id": challan.transporter_id,
            "dispatched_at": challan.issued_at.isoformat(),
        },
    )


async def request_eway(
    session: AsyncSession,
    ctx: ScopeContext,
    challan_id: uuid.UUID,
    *,
    distance_km: int,
    vehicle_no: str | None,
    transporter_id: str | None,
    request: Request | None = None,
) -> Challan:
    """Supply the transport facts and (re-)ask finance to consider an e-way bill — for the case
    where they were not known at issue, or the consignment is below the threshold and the
    operator wants one anyway. Only the e-way fields move; the challan itself is frozen."""
    challan = await selectors.get_challan(session, ctx.organization_id, challan_id, for_update=True)
    if challan is None:
        raise NotFoundProblem("That challan is not part of this organization")
    if challan.eway_status == "GENERATED":
        raise ConflictProblem(
            f"{challan.challan_number} already has e-way bill {challan.eway_bill_no}"
        )
    # Idempotent: the same facts on an already-requested challan change nothing, emit nothing.
    if (
        challan.eway_status == "REQUESTED"
        and challan.distance_km == distance_km
        and vehicle_no in (None, challan.vehicle_no)
        and transporter_id in (None, challan.transporter_id)
    ):
        return challan
    order = await selectors.get_order(session, ctx.organization_id, challan.order_id)
    if order is None:
        raise NotFoundProblem("That challan's order is not part of this organization")
    before = {
        "eway_status": challan.eway_status,
        "distance_km": challan.distance_km,
        "vehicle_no": challan.vehicle_no,
    }
    challan.distance_km = distance_km
    if vehicle_no is not None:
        challan.vehicle_no = vehicle_no
    if transporter_id is not None:
        challan.transporter_id = transporter_id
    challan.eway_status = "REQUESTED"
    challan.eway_requested_at = datetime.now(UTC)
    challan.updated_by = str(ctx.principal.sub)
    challan.updated_at = datetime.now(UTC)
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.challan.eway_requested",
        entity="challan",
        entity_id=challan.id,
        before=before,
        after={
            "eway_status": "REQUESTED",
            "distance_km": distance_km,
            "vehicle_no": challan.vehicle_no,
        },
        request=request,
    )
    _emit_eway_request(session, ctx, order=order, challan=challan)
    return challan


# ------------------------------------------------------------------ receive


def _add_line(
    acc: dict[tuple[str, str, str, str, str], tuple[MoveLine, int]],
    *,
    material: JobWorkMaterial,
    location_id: uuid.UUID,
    status: str,
    milli: int,
) -> None:
    """Accumulate one bucket's change. Two materials of one receipt can land in the same bucket
    (same item, bin, lot and roll), and a kernel move touches each bucket at most once."""
    key = (
        str(material.item_id),
        str(location_id),
        status,
        str(material.lot_id),
        str(material.roll_id),
    )
    _line, total = acc.get(key, (None, 0))
    acc[key] = (
        MoveLine(
            item_id=material.item_id,
            location_id=location_id,
            quantity_milli=0,
            status=status,
            lot_id=material.lot_id,
            roll_id=material.roll_id,
        ),
        total + milli,
    )


def _lines(acc: dict[tuple[str, str, str, str, str], tuple[MoveLine, int]]) -> list[MoveLine]:
    return [
        MoveLine(
            item_id=line.item_id,
            location_id=line.location_id,
            quantity_milli=total,
            status=line.status,
            lot_id=line.lot_id,
            roll_id=line.roll_id,
        )
        for line, total in acc.values()
        if total != 0
    ]


async def _check_shortage_threshold(
    session: AsyncSession, ctx: ScopeContext, shorts: list[tuple[str, int]]
) -> None:
    """A declared shortage writes stock off the vendor bucket: past the adjustment threshold it
    needs the stock adjust permission at ORG as well as the right to receive (as quality scrap)."""
    if not shorts:
        return
    from app.movements import common as movement_common
    from app.movements import domain as movement_domain

    settings = await movement_common.get_settings(session, ctx.organization_id)
    threshold = settings.adjustment_approval_threshold_milli if settings else None
    if not movement_domain.over_threshold(
        threshold, [movement_domain.to_base_milli(milli, uom) for uom, milli in shorts]
    ):
        return
    held = ctx.principal.scope_for("operations.inventory.stock.adjust")
    if held is None or held.rank < Scope.ORG.rank:
        raise ForbiddenProblem(
            "Writing off this much needs the stock adjust permission: it is above the "
            "adjustment approval threshold"
        )


async def receive_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    lines: list[JobWorkReceiptLineIn],
    received_on: date | None,
    note: str,
    idempotency_key: str,
    request: Request | None = None,
) -> tuple[JobWorkOrder, list[JobWorkReceipt], bool]:
    """Take material back from the vendor, in whole or in part.

    Each line accounts for some of one material's outstanding quantity as good, rejected, or
    short. Good goes to `AVAILABLE` (or `QUALITY_HOLD` when the order asked for a QC hand-off),
    rejected to `REJECTED`, both at the receive location; short is written off the vendor's
    bucket. A line that asks for more than is outstanding is refused with the numbers, under the
    material's lock — so two receipts racing for the last of a quantity cannot both win.
    """
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    order = await _lock_order(session, ctx, order_id)
    request_hash = _hash(
        {
            "actor": ctx.principal.sub,
            "order_id": order.id,
            "received_on": received_on,
            "note": note,
            "lines": sorted(
                json.dumps(line.model_dump(mode="json"), sort_keys=True) for line in lines
            ),
        }
    )
    existing = await selectors.receipts_by_key(session, org_id, order.id, idempotency_key)
    if existing:
        if any(r.request_hash != request_hash for r in existing):
            raise ConflictProblem("That Idempotency-Key was already used for a different receipt")
        return order, existing, True

    if order.status not in domain.RECEIVABLE_STATUSES:
        raise ConflictProblem(
            f"{order.number} is {order.status}; material can only be received while it is at "
            "the vendor"
        )
    if len({line.material_id for line in lines}) != len(lines):
        raise ValidationProblem("A receipt names each material at most once")

    materials = await selectors.materials_for(session, org_id, order.id, for_update=True)
    by_id = {m.id: m for m in materials}
    parsed: list[tuple[JobWorkReceiptLineIn, JobWorkMaterial, int, int, int, uuid.UUID]] = []
    refusals: list[dict[str, Any]] = []
    for line in lines:
        material = by_id.get(line.material_id)
        if material is None:
            raise NotFoundProblem(f"Material {line.material_id} is not on this order")
        good = _parse_non_negative(line.received, material.uom, f"{material.sku} received")
        rejected = _parse_non_negative(line.rejected, material.uom, f"{material.sku} rejected")
        short = _parse_non_negative(line.shortage, material.uom, f"{material.sku} shortage")
        if good + rejected + short <= 0:
            raise ValidationProblem(
                f"{material.sku}: give a received, rejected or shortage quantity"
            )
        outstanding = (
            material.sent_milli
            - material.received_milli
            - material.rejected_milli
            - material.shortage_milli
        )
        if good + rejected + short > outstanding:
            refusals.append(
                {
                    "material_id": str(material.id),
                    "sku": material.sku,
                    "requested": format_qty(good + rejected + short),
                    "outstanding": format_qty(outstanding),
                }
            )
        location_id = line.location_id or order.receive_location_id or material.location_id
        parsed.append((line, material, good, rejected, short, location_id))
    if refusals:
        first = refusals[0]
        raise ConflictProblem(
            f"{first['sku']}: {first['requested']} is more than the {first['outstanding']} "
            "still at the vendor"
            + (f" (and {len(refusals) - 1} more line(s))" if len(refusals) > 1 else ""),
            errors=refusals,
        )
    await _resolve_locations(
        session, org_id, {location_id for *_rest, location_id in parsed}, warehouse_id=None
    )

    await _check_shortage_threshold(
        session,
        ctx,
        [(material.uom, short) for _l, material, _g, _r, short, _loc in parsed if short],
    )
    good_status = "QUALITY_HOLD" if order.qc_required else "AVAILABLE"
    returns: dict[tuple[str, str, str, str, str], tuple[MoveLine, int]] = {}
    shortages: dict[tuple[str, str, str, str, str], tuple[MoveLine, int]] = {}
    for _line, material, good, rejected, short, location_id in parsed:
        if good + rejected:
            _add_line(
                returns,
                material=material,
                location_id=material.location_id,
                status="AT_JOB_WORKER",
                milli=-(good + rejected),
            )
        if good:
            _add_line(
                returns, material=material, location_id=location_id, status=good_status, milli=good
            )
        if rejected:
            _add_line(
                returns,
                material=material,
                location_id=location_id,
                status="REJECTED",
                milli=rejected,
            )
        if short:
            _add_line(
                shortages,
                material=material,
                location_id=material.location_id,
                status="AT_JOB_WORKER",
                milli=-short,
            )

    # Two moves follow (the return, then the declared shortage), each locking its own rolls. Lock
    # and validate every roll of both up front, in id order, ahead of the first balance lock — a
    # shortage-only line's roll would otherwise be locked after the return move already holds
    # balances, the reverse of the roll -> balance order a concurrent split or merge relies on.
    await ledger.validate_tracking(session, org_id, [*_lines(returns), *_lines(shortages)])

    when = received_on or domain.ist_today()
    if received_on is not None:
        issued_on = domain.ist_today(order.issued_at) if order.issued_at else received_on
        if problem := domain.received_on_problem(received_on, domain.ist_today(), issued_on):
            raise ValidationProblem(problem)
    return_move = None
    short_move = None
    if returns:
        return_move = await ledger.post_move(
            session,
            ctx,
            kind="relocation",
            lines=_lines(returns),
            occurred_on=when,
            reference_type=REFERENCE_TYPE,
            reference_id=order.id,
            note=f"Received from job worker on {order.number}"[:500],
            idempotency_key=_kernel_key(order.id, idempotency_key, "ret"),
            request=request,
        )
    if shortages:
        short_move = await ledger.post_move(
            session,
            ctx,
            kind="adjustment",
            reason="lost",
            lines=_lines(shortages),
            occurred_on=when,
            reference_type=REFERENCE_TYPE,
            reference_id=order.id,
            note=f"Shortage declared on job work {order.number}"[:500],
            idempotency_key=_kernel_key(order.id, idempotency_key, "short"),
            request=request,
        )

    receipt_no = await numbering.next_receipt_number(session, org_id, today=when)
    receipts: list[JobWorkReceipt] = []
    event_lines: list[dict[str, Any]] = []
    qc_lines: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for _line, material, good, rejected, short, location_id in parsed:
        material.received_milli += good
        material.rejected_milli += rejected
        material.shortage_milli += short
        material.updated_at = now
        receipt = JobWorkReceipt(
            organization_id=org_id,
            order_id=order.id,
            material_id=material.id,
            receipt_no=receipt_no,
            received_milli=good,
            rejected_milli=rejected,
            shortage_milli=short,
            location_id=location_id,
            good_status=good_status,
            qc_handoff=order.qc_required and (good + rejected) > 0,
            received_on=when,
            note=note,
            return_move_id=return_move.move.id if return_move and (good + rejected) else None,
            shortage_move_id=short_move.move.id if short_move and short else None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=str(ctx.principal.sub),
        )
        session.add(receipt)
        receipts.append(receipt)
        entry = {
            "material_id": str(material.id),
            "item_id": str(material.item_id),
            "sku": material.sku,
            "uom": material.uom,
            "received": format_qty(good),
            "rejected": format_qty(rejected),
            "shortage": format_qty(short),
            "location_id": str(location_id),
            "lot_id": str(material.lot_id) if material.lot_id else None,
            "roll_id": str(material.roll_id) if material.roll_id else None,
        }
        event_lines.append(entry)
        if order.qc_required and (good + rejected) > 0:
            qc_lines.append({**entry, "status": good_status})
    await session.flush()

    outstanding_total = sum(
        m.sent_milli - m.received_milli - m.rejected_milli - m.shortage_milli for m in materials
    )
    previous = order.status
    target = "RECEIVED" if outstanding_total == 0 else "PARTIALLY_RECEIVED"
    if target != order.status:
        _move(order, ctx, target)
    else:
        order.version += 1
        order.updated_by = str(ctx.principal.sub)
        order.updated_at = now
    await session.flush()

    _audit(
        session,
        ctx,
        action="jobwork.order.received",
        entity="job_work_order",
        entity_id=order.id,
        before={"status": previous},
        after={
            "status": order.status,
            "receipt_no": receipt_no,
            "lines": event_lines,
            "outstanding": format_qty(outstanding_total),
        },
        reason=note or None,
        request=request,
    )
    _emit(
        session,
        ctx,
        event_type=EVT_RECEIVED,
        aggregate_id=order.id,
        payload={
            "order_id": str(order.id),
            "order_number": order.number,
            "supplier_id": str(order.supplier_id),
            "receipt_no": receipt_no,
            "received_on": when.isoformat(),
            "fully_received": outstanding_total == 0,
            "outstanding": format_qty(outstanding_total),
            "qc_required": order.qc_required,
            "lines": event_lines,
        },
    )
    if qc_lines:
        _emit(
            session,
            ctx,
            event_type=EVT_QC_REQUESTED,
            aggregate_id=order.id,
            payload={
                "order_id": str(order.id),
                "order_number": order.number,
                "supplier_id": str(order.supplier_id),
                "receipt_no": receipt_no,
                "source": "job_work",
                "lines": qc_lines,
            },
        )
    return order, receipts, False


# ------------------------------------------------------------------ close


async def close_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    reason: str = "",
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> JobWorkOrder:
    """Settle the order. **Refused unless the reconciliation balances**: every material's
    outstanding is zero and the ledger's `AT_JOB_WORKER` position for this order agrees with the
    documents. The check runs here, under the order and material locks, from the ledger itself —
    not from the status, which is only a summary of it."""
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    order = await _lock_order(session, ctx, order_id)
    if _check_replay(order, idempotency_key, "CLOSED"):
        return order
    if order.status in ("CLOSED", "CANCELLED", "DRAFT", "APPROVED"):
        raise ConflictProblem(f"{order.number} is {order.status} and cannot be closed")
    materials = await selectors.materials_for(session, org_id, order.id, for_update=True)
    result, _ledger = await selectors.reconciliation(session, org_id, order, materials)
    if not result.balanced:
        figures = {
            "total_sent": format_qty(result.total_sent),
            "total_received": format_qty(result.total_received),
            "total_rejected": format_qty(result.total_rejected),
            "total_shortage": format_qty(result.total_shortage),
            "total_outstanding": format_qty(result.total_outstanding),
        }
        why = (
            f"{format_qty(result.total_outstanding)} is still unaccounted for at the vendor"
            if not result.documents_settled
            else "the documents and the stock ledger disagree"
        )
        raise ReconciliationNotBalanced(
            f"{order.number} cannot be closed: {why}",
            errors=[
                {
                    **figures,
                    "documents_settled": result.documents_settled,
                    "ledger_consistent": result.ledger_consistent,
                    "drift": [
                        {"item_id": item_id, "documents": format_qty(d), "ledger": format_qty(g)}
                        for item_id, (d, g) in sorted(result.drift.items())
                    ],
                }
            ],
        )
    previous = _move(order, ctx, "CLOSED")
    order.closed_at = datetime.now(UTC)
    _remember(order, idempotency_key, "CLOSED")
    await session.flush()
    _audit(
        session,
        ctx,
        action="jobwork.order.closed",
        entity="job_work_order",
        entity_id=order.id,
        before={"status": previous},
        after={
            "status": "CLOSED",
            "total_sent": format_qty(result.total_sent),
            "total_received": format_qty(result.total_received),
            "total_rejected": format_qty(result.total_rejected),
            "total_shortage": format_qty(result.total_shortage),
        },
        reason=reason or None,
        request=request,
    )
    _emit(
        session,
        ctx,
        event_type=EVT_CLOSED,
        aggregate_id=order.id,
        payload={
            "order_id": str(order.id),
            "order_number": order.number,
            "supplier_id": str(order.supplier_id),
            "total_sent": format_qty(result.total_sent),
            "total_received": format_qty(result.total_received),
            "total_rejected": format_qty(result.total_rejected),
            "total_shortage": format_qty(result.total_shortage),
        },
    )
    return order
