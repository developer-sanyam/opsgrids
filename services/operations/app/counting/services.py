"""Writes for cycle counting (ADR-0026, `docs/fulfilment/plan.md` F8).

**Warehouse-row-first locking (ADR-0026 §5).** `open_session` takes `Warehouse`'s own row lock
before checking whether it already has a live count session — the same anchor-row-first pattern
`app.orders.services.accept_order` uses on the order row and `app.fulfilment.services
.confirm_pick` uses on the order row before its task. The partial unique index
(`uq_count_sessions_org_warehouse_live`) is the real, database-enforced guarantee; the lock is
what turns a would-be unique-violation into a clean, named `ConflictProblem` instead of a raw
constraint error surfacing from a race.

**Approving a discrepancy posts through the kernel, unchanged (ADR-0026 §3).**
`review_line` calls `app.inventory.ledger.post_move(kind="count", ...)` exactly the way
`app.inventory.services.adjust_stock` already does for a single-item recount — same idempotency
convention (`f"count:{line.id}"`), same "the kernel computes the delta from the live balance
under its own lock, never from a snapshot" guarantee. No kernel change was needed for this phase.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.counting import selectors
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import MAX_ABS_MILLI, QuantityError, parse_qty
from app.models import (
    AuditLog,
    CountLine,
    CountSession,
    InventoryItem,
    Location,
    OutboxEvent,
    Warehouse,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_SESSION_OPENED = "operations.inventory.count_session.opened"
EVT_SESSION_REVIEW_STARTED = "operations.inventory.count_session.review_started"
EVT_SESSION_CLOSED = "operations.inventory.count_session.closed"
EVT_SESSION_CANCELLED = "operations.inventory.count_session.cancelled"
EVT_LINE_COUNTED = "operations.inventory.count_line.counted"
EVT_DISCREPANCY_APPROVED = "operations.inventory.count_line.discrepancy_approved"
EVT_DISCREPANCY_REJECTED = "operations.inventory.count_line.discrepancy_rejected"

#: A count session's own scope location list is capped the same way `PackageIn.lines` and
#: `CreatePickTasksIn`-adjacent payloads are — a plainly abusive request size, not a real
#: warehouse's location count.
_MAX_SCOPE_LOCATIONS = 5000


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def lock_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> Warehouse:
    stmt = (
        select(Warehouse)
        .where(
            Warehouse.id == warehouse_id,
            Warehouse.organization_id == org_id,
            Warehouse.deleted_at.is_(None),
        )
        .with_for_update()
    )
    warehouse = (await session.execute(stmt)).scalar_one_or_none()
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    return warehouse


async def _resolve_scope_locations(
    session: AsyncSession,
    org_id: uuid.UUID,
    warehouse: Warehouse,
    *,
    scope_kind: str,
    zone_location_id: uuid.UUID | None,
    location_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID]:
    if scope_kind == "WAREHOUSE":
        rows = (
            await session.execute(
                select(Location.id).where(
                    Location.organization_id == org_id,
                    Location.warehouse_id == warehouse.id,
                    Location.deleted_at.is_(None),
                )
            )
        ).all()
        return [row.id for row in rows]

    if scope_kind == "ZONE":
        if zone_location_id is None:
            raise ValidationProblem("scope_kind ZONE needs zone_location_id")
        zone = await inv_selectors.get_location(session, org_id, zone_location_id)
        if zone is None or zone.warehouse_id != warehouse.id:
            raise NotFoundProblem("That zone location is not part of this warehouse")
        return await inv_selectors.descendant_location_ids(session, org_id, zone_location_id)

    if scope_kind == "LOCATIONS":
        if not location_ids:
            raise ValidationProblem("scope_kind LOCATIONS needs at least one location_id")
        if len(location_ids) > _MAX_SCOPE_LOCATIONS:
            raise ValidationProblem(f"At most {_MAX_SCOPE_LOCATIONS} locations per session")
        rows = (
            await session.execute(
                select(Location.id).where(
                    Location.organization_id == org_id,
                    Location.warehouse_id == warehouse.id,
                    Location.id.in_(location_ids),
                    Location.deleted_at.is_(None),
                )
            )
        ).all()
        found = {row.id for row in rows}
        missing = set(location_ids) - found
        if missing:
            raise NotFoundProblem(
                f"{len(missing)} location(s) are not part of warehouse {warehouse.name}"
            )
        return list(location_ids)

    raise ValidationProblem(f"{scope_kind!r} is not a count scope")


async def open_session(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    warehouse_id: uuid.UUID,
    scope_kind: str,
    zone_location_id: uuid.UUID | None,
    location_ids: list[uuid.UUID] | None,
    label: str,
    request: Request | None = None,
) -> CountSession:
    org_id = ctx.organization_id
    warehouse = await lock_warehouse(session, org_id, warehouse_id)
    live = await selectors.get_live_session_for_warehouse(session, org_id, warehouse.id)
    if live is not None:
        raise ConflictProblem(
            f"{warehouse.name} already has a count session in progress ({live.status}) — "
            "close or cancel it before opening another"
        )

    scope_locations = await _resolve_scope_locations(
        session,
        org_id,
        warehouse,
        scope_kind=scope_kind,
        zone_location_id=zone_location_id,
        location_ids=location_ids,
    )

    count_session = CountSession(
        organization_id=org_id,
        warehouse_id=warehouse.id,
        scope_kind=scope_kind,
        zone_location_id=zone_location_id if scope_kind == "ZONE" else None,
        location_ids=[str(x) for x in (location_ids or [])] if scope_kind == "LOCATIONS" else None,
        status="ACTIVE",
        label=label,
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
    )
    session.add(count_session)
    await session.flush()

    balances = await inv_selectors.balances_in_locations(
        session, org_id, scope_locations, status="AVAILABLE"
    )
    for balance in balances:
        session.add(
            CountLine(
                organization_id=org_id,
                session_id=count_session.id,
                inventory_item_id=balance.inventory_item_id,
                location_id=balance.location_id,
                status=balance.status,
                lot_id=balance.lot_id,
                roll_id=balance.roll_id,
                is_unlisted=False,
                expected_milli=balance.quantity_milli,
                created_by=str(ctx.principal.sub),
                updated_by=str(ctx.principal.sub),
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="count_session.opened",
        entity="count_session",
        entity_id=count_session.id,
        after={
            "warehouse_id": str(warehouse.id),
            "scope_kind": scope_kind,
            "line_count": len(balances),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_SESSION_OPENED,
        aggregate_type="count_session",
        aggregate_id=count_session.id,
        payload={
            "organization_id": str(org_id),
            "session_id": str(count_session.id),
            "warehouse_id": str(warehouse.id),
            "scope_kind": scope_kind,
            "line_count": len(balances),
        },
        actor=_actor(ctx),
    )
    return count_session


async def _transition(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    *,
    to_status: str,
    action: str,
    event_type: str,
    reason: str | None = None,
    request: Request | None = None,
) -> CountSession:
    if not domain.can_transition_count_session(count_session.status, to_status):
        raise ConflictProblem(
            f"Count session {count_session.id} cannot move {count_session.status} -> {to_status}"
        )
    before_status = count_session.status
    count_session.status = to_status
    count_session.updated_by = str(ctx.principal.sub)
    count_session.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="count_session",
        entity_id=count_session.id,
        before={"status": before_status},
        after={"status": to_status, "reason": reason} if reason else {"status": to_status},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type="count_session",
        aggregate_id=count_session.id,
        payload={"organization_id": str(ctx.organization_id), "session_id": str(count_session.id)},
        actor=_actor(ctx),
    )
    return count_session


async def start_review(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    *,
    request: Request | None = None,
) -> CountSession:
    return await _transition(
        session,
        ctx,
        count_session,
        to_status="REVIEW",
        action="count_session.review_started",
        event_type=EVT_SESSION_REVIEW_STARTED,
        request=request,
    )


async def close_session(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    *,
    request: Request | None = None,
) -> CountSession:
    _, _, pending = await selectors.session_counters(session, ctx.organization_id, count_session.id)
    if pending:
        raise ConflictProblem(f"{pending} line(s) still need review before this session can close")
    return await _transition(
        session,
        ctx,
        count_session,
        to_status="CLOSED",
        action="count_session.closed",
        event_type=EVT_SESSION_CLOSED,
        request=request,
    )


async def cancel_session(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    *,
    reason: str,
    request: Request | None = None,
) -> CountSession:
    return await _transition(
        session,
        ctx,
        count_session,
        to_status="CANCELLED",
        action="count_session.cancelled",
        event_type=EVT_SESSION_CANCELLED,
        reason=reason,
        request=request,
    )


def _parse_counted(value: str, item_uom: str) -> int:
    try:
        parsed = parse_qty(value, item_uom)
    except QuantityError as exc:
        raise ValidationProblem(str(exc)) from exc
    if parsed < 0 or parsed > MAX_ABS_MILLI:
        raise ValidationProblem("A counted quantity must be zero or a positive number")
    return parsed


async def _get_item(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
    stmt = select(InventoryItem).where(
        InventoryItem.id == item_id,
        InventoryItem.organization_id == org_id,
        InventoryItem.deleted_at.is_(None),
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise NotFoundProblem("That item is not part of this organization")
    return item


async def submit_count(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    line: CountLine,
    *,
    counted: str,
    count_method: str,
    scanned_barcode: str | None,
    request: Request | None = None,
) -> CountLine:
    if count_session.status != "ACTIVE":
        raise ConflictProblem(
            f"Count session {count_session.id} is {count_session.status}; not accepting counts"
        )
    if line.session_id != count_session.id:
        raise NotFoundProblem("That line is not part of this session")
    if line.counted_at is not None:
        raise ConflictProblem("This line has already been counted")

    item = await _get_item(session, ctx.organization_id, line.inventory_item_id)
    counted_milli = _parse_counted(counted, item.uom)

    line.counted_milli = counted_milli
    line.count_method = count_method
    line.scanned_barcode = scanned_barcode
    line.counted_by = str(ctx.principal.sub)
    line.counted_at = datetime.now(UTC)
    line.review_status = "PENDING" if counted_milli != line.expected_milli else "NONE"
    line.updated_by = str(ctx.principal.sub)
    line.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="count_line.counted",
        entity="count_line",
        entity_id=line.id,
        before={"expected_milli": line.expected_milli},
        after={"counted_milli": counted_milli, "review_status": line.review_status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LINE_COUNTED,
        aggregate_type="count_line",
        aggregate_id=line.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "session_id": str(count_session.id),
            "line_id": str(line.id),
            "counted_milli": counted_milli,
            "expected_milli": line.expected_milli,
        },
        actor=_actor(ctx),
    )
    return line


async def add_unlisted_line(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    *,
    inventory_item_id: uuid.UUID,
    location_id: uuid.UUID,
    status: str,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
    counted: str,
    count_method: str,
    scanned_barcode: str | None,
    request: Request | None = None,
) -> CountLine:
    if count_session.status != "ACTIVE":
        raise ConflictProblem(
            f"Count session {count_session.id} is {count_session.status}; not accepting counts"
        )
    org_id = ctx.organization_id

    location = await inv_selectors.get_location(session, org_id, location_id)
    if location is None or location.warehouse_id != count_session.warehouse_id:
        raise NotFoundProblem("That location is not part of this session's warehouse")
    # Not just "in the warehouse" — in the session's own declared scope. A ZONE or LOCATIONS
    # session only ever promises to snapshot and review a subset of the warehouse; an unlisted
    # find outside that subset belongs to a session that actually covers it.
    warehouse = await inv_selectors.get_warehouse(session, org_id, count_session.warehouse_id)
    assert warehouse is not None  # the session's own FK guarantees this
    scope_locations = await _resolve_scope_locations(
        session,
        org_id,
        warehouse,
        scope_kind=count_session.scope_kind,
        zone_location_id=count_session.zone_location_id,
        location_ids=(
            [uuid.UUID(x) for x in count_session.location_ids]
            if count_session.location_ids
            else None
        ),
    )
    if location_id not in scope_locations:
        raise NotFoundProblem("That location is outside this session's own scope")
    item = await _get_item(session, org_id, inventory_item_id)

    existing = await selectors.get_line_by_bucket(
        session,
        org_id,
        count_session.id,
        inventory_item_id=inventory_item_id,
        location_id=location_id,
        status=status,
        lot_id=lot_id,
        roll_id=roll_id,
    )
    if existing is not None:
        raise ConflictProblem(
            "This bucket is already tracked by this session — count that line instead"
        )

    counted_milli = _parse_counted(counted, item.uom)
    line = CountLine(
        organization_id=org_id,
        session_id=count_session.id,
        inventory_item_id=inventory_item_id,
        location_id=location_id,
        status=status,
        lot_id=lot_id,
        roll_id=roll_id,
        is_unlisted=True,
        expected_milli=0,
        counted_milli=counted_milli,
        count_method=count_method,
        scanned_barcode=scanned_barcode,
        counted_by=str(ctx.principal.sub),
        counted_at=datetime.now(UTC),
        review_status="PENDING" if counted_milli != 0 else "NONE",
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
    )
    session.add(line)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="count_line.added_unlisted",
        entity="count_line",
        entity_id=line.id,
        after={"counted_milli": counted_milli, "location_id": str(location_id)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LINE_COUNTED,
        aggregate_type="count_line",
        aggregate_id=line.id,
        payload={
            "organization_id": str(org_id),
            "session_id": str(count_session.id),
            "line_id": str(line.id),
            "counted_milli": counted_milli,
            "expected_milli": 0,
            "is_unlisted": True,
        },
        actor=_actor(ctx),
    )
    return line


async def review_line(
    session: AsyncSession,
    ctx: ScopeContext,
    count_session: CountSession,
    line: CountLine,
    *,
    decision: str,
    note: str,
    request: Request | None = None,
) -> CountLine:
    """Approve or reject one `PENDING` discrepancy. Approving posts through the kernel in the
    same call (module docstring) — the balance lands on `line.counted_milli` exactly, computed by
    `post_move` from the *live* balance under its own lock, not from `line.expected_milli`'s
    snapshot (ADR-0026 §6)."""
    if count_session.status != "REVIEW":
        raise ConflictProblem(
            f"Count session {count_session.id} is {count_session.status}; not under review"
        )
    if line.session_id != count_session.id:
        raise NotFoundProblem("That line is not part of this session")
    if line.review_status != "PENDING":
        raise ConflictProblem(f"This line is {line.review_status}, not pending review")
    if decision not in ("approve", "reject"):
        raise ValidationProblem(f"{decision!r} is not a review decision")
    if decision == "reject" and not note.strip():
        raise ValidationProblem("Rejecting a discrepancy needs a note")

    org_id = ctx.organization_id
    posted_move_id: uuid.UUID | None = None
    if decision == "approve":
        assert line.counted_milli is not None  # PENDING is only ever set alongside a count
        posted = await ledger.post_move(
            session,
            ctx,
            kind="count",
            lines=[
                MoveLine(
                    item_id=line.inventory_item_id,
                    location_id=line.location_id,
                    status=line.status,
                    lot_id=line.lot_id,
                    roll_id=line.roll_id,
                    counted_milli=line.counted_milli,
                )
            ],
            occurred_on=date.today(),
            # `InventoryMove.reason` is `String(30)` — a short code, not a sentence. The session
            # is already named by `reference_type`/`reference_id` below (`count_line` -> this
            # line -> its own `session_id`); the manager's own text goes in `note`, which has room.
            reason="cycle_count",
            reference_type="count_line",
            reference_id=line.id,
            note=f"Count session {count_session.id}. {note}".strip(),
            idempotency_key=f"count:{line.id}",
            occurred_on_explicit=False,
            request=request,
        )
        posted_move_id = posted.move.id
        line.posted_move_id = posted_move_id

    line.review_status = "APPROVED" if decision == "approve" else "REJECTED"
    line.reviewed_by = str(ctx.principal.sub)
    line.reviewed_at = datetime.now(UTC)
    line.review_note = note
    line.updated_by = str(ctx.principal.sub)
    line.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="count_line.discrepancy_approved"
        if decision == "approve"
        else "count_line.discrepancy_rejected",
        entity="count_line",
        entity_id=line.id,
        before={"expected_milli": line.expected_milli, "counted_milli": line.counted_milli},
        after={
            "review_status": line.review_status,
            "posted_move_id": str(posted_move_id) if posted_move_id else None,
        },
        reason=note or None,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_DISCREPANCY_APPROVED if decision == "approve" else EVT_DISCREPANCY_REJECTED,
        aggregate_type="count_line",
        aggregate_id=line.id,
        payload={
            "organization_id": str(org_id),
            "session_id": str(count_session.id),
            "line_id": str(line.id),
            "counted_milli": line.counted_milli,
            "expected_milli": line.expected_milli,
            **({"posted_move_id": str(posted_move_id)} if posted_move_id else {}),
        },
        actor=_actor(ctx),
    )
    return line
