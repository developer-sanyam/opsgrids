"""Writes for fulfilment — workers, pick tasks, offers, exceptions, scans, packing (ADR-0024 §7,
ADR-0025 §2/§3; `docs/fulfilment/plan.md` F3).

Every pick and pack confirmation posts through `app.inventory.ledger.post_move` as a relocation
that **transfers** the reservation (bin -> staging on pick, staging -> pack/dock on pack) — never
an issue, never an unreserve-then-reserve pair (plan F3 item 4).

**Order-row-first locking (ADR-0025 §1).** `confirm_pick` and `resolve_exception` are the two
calls in this module that can move `orders.status` (`IN_FULFILMENT -> READY`, once every task is
settled and at least one is `PICKED`) — a short-pick-then-resolve can complete a task exactly the
way a full confirm does (MAJOR 1 #1, security review). Both take the order row lock *before* the
task row lock, the fixed order ADR-0025 §1 requires. Every other write here only ever touches its
own task/offer/worker row and takes no order lock at all.

**The offer accept/decline/expire race (ADR-0025 §3).** `accept_offer`, `decline_offer` and
`expire_offer` are each a conditional `UPDATE ... WHERE id = :offer AND state =
'OFFERED' ...` — never a locked read followed by a Python-level check. A zero-row update is a 409
(accept/decline) or a silent no-op (expire, which never raises for a business outcome). Both
compare against the database's own `now()`.

**Lock ordering (security review MAJOR 3).** Every write in this module that touches both a task
and its live offer locks the **task row first, then the offer** — `accept_offer`, `decline_offer`
and `expire_offer` peek at the offer unlocked only to learn which task to lock, exactly matching
the order `cancel_task`/`release_task` already used. Reaching for the offer row first (the old
shape) let a dispatcher's cancel and a worker's accept lock the same two rows in opposite orders,
which Postgres can only resolve by aborting one side with a raw `deadlock detected` error — worse
than the clean 409 ADR-0025 §3 promises, not merely a different status code.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.fulfilment import selectors
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import MAX_ABS_MILLI, QuantityError, parse_qty
from app.models import (
    AssignmentOffer,
    AuditLog,
    OrderTransition,
    OutboxEvent,
    Package,
    PackageLine,
    PackagingMaterial,
    PickException,
    PickTask,
    PickTaskLine,
    ScanEvent,
    Worker,
    WorkerPresence,
    WorkerSkill,
    WorkerZoneAccess,
)
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from app.orders.services import EVT_ORDER_READY
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event, schedule_event

EVT_TASK_OFFERED = "operations.fulfilment.task.offered"
EVT_TASK_ACCEPTED = "operations.fulfilment.task.accepted"
EVT_TASK_DECLINED = "operations.fulfilment.task.declined"
EVT_TASK_EXPIRED = "operations.fulfilment.task.expired"
EVT_TASK_ESCALATED = "operations.fulfilment.task.escalated"
EVT_TASK_STARTED = "operations.fulfilment.task.started"
EVT_TASK_COMPLETED = "operations.fulfilment.task.completed"
EVT_PICK_CONFIRMED = "operations.fulfilment.pick.confirmed"
EVT_PICK_EXCEPTION_RAISED = "operations.fulfilment.pick.exception_raised"
EVT_PACK_STARTED = "operations.fulfilment.pack.started"
EVT_PACK_COMPLETED = "operations.fulfilment.pack.completed"
#: The scheduler-relayed target event type for an offer's expiry sweep (ADR-0015; ADR-0025 §3).
#: Must be prefixed `operations.` — the scheduler's own consumer refuses a `target_event_type`
#: that does not start with the producing envelope's own `producer` name.
EVT_OFFER_EXPIRE_REQUESTED = "operations.fulfilment.offer.expire_requested"

#: `app.domain`'s shared vocabulary (MAJOR 1 #2, security review) — `app/orders/services.py`'s
#: `release_hold_order` needs the exact same reading without importing this module (ADR-0024 §1).
_OPEN_TASK_STATUSES = domain.PICK_TASK_OPEN_STATUSES
_SETTLED_TASK_STATUSES = domain.PICK_TASK_SETTLED_STATUSES


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


#: The scope context an automated (consumer-driven) fulfilment write runs under — the offer
#: expiry sweep and the order-cancellation cascade never carry an HTTP principal. `sub` is a
#: stable all-zero UUID so every audit row either writes is attributable to "the platform", the
#: same convention `services/finance/app/services.py::service_ctx` uses.
_FULFILMENT_SYSTEM_PERMISSION = "operations.fulfilment.task.assign"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_FULFILMENT_SYSTEM_PERMISSION}:ORG"],
        entitlements=["operations"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission=_FULFILMENT_SYSTEM_PERMISSION
    )


async def caller_worker(session: AsyncSession, ctx: ScopeContext) -> Worker:
    """The caller's own `workers` row, resolved from `subject_ref` (the membership's
    `employee_id`, ADR-0000 §1) — every OWN-scoped action in this module (`fulfilment.task
    .execute`'s own ceiling, `app/catalogue.py`) is "act as the worker I am", never "act as any
    worker", so this is the one lookup every such action starts from."""
    if ctx.principal.subject_ref is None:
        raise ForbiddenProblem("This account has no worker identity")
    worker = await selectors.get_worker_by_employee_id(
        session, ctx.organization_id, ctx.principal.subject_ref
    )
    if worker is None:
        raise ForbiddenProblem("This account has no worker record in this organization")
    return worker


# ------------------------------------------------------------------ workers


async def create_worker(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    employee_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    display_name: str,
    is_active: bool,
    skills: Sequence[str],
    zone_location_ids: Sequence[uuid.UUID],
    request: Request | None = None,
) -> Worker:
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")
    if await selectors.get_worker_by_employee_id(session, ctx.organization_id, employee_id):
        raise ConflictProblem("That employee already has a worker record")

    worker = Worker(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        employee_id=employee_id,
        warehouse_id=warehouse.id,
        display_name=display_name,
        is_active=is_active,
    )
    session.add(worker)
    await session.flush()
    await _replace_skills(session, ctx, worker, skills)
    await _replace_zone_access(session, ctx, worker, zone_location_ids, warehouse_id=warehouse.id)
    session.add(
        WorkerPresence(organization_id=ctx.organization_id, worker_id=worker.id, status="offline")
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="worker.created",
        entity="worker",
        entity_id=worker.id,
        after={"employee_id": str(employee_id), "warehouse_id": str(warehouse_id)},
        request=request,
    )
    return worker


async def _replace_skills(
    session: AsyncSession, ctx: ScopeContext, worker: Worker, skills: Sequence[str]
) -> None:
    await session.execute(
        delete(WorkerSkill).where(
            WorkerSkill.organization_id == ctx.organization_id, WorkerSkill.worker_id == worker.id
        )
    )
    for skill in dict.fromkeys(s.strip() for s in skills if s.strip()):
        session.add(
            WorkerSkill(organization_id=ctx.organization_id, worker_id=worker.id, skill=skill)
        )


async def _replace_zone_access(
    session: AsyncSession,
    ctx: ScopeContext,
    worker: Worker,
    location_ids: Sequence[uuid.UUID],
    *,
    warehouse_id: uuid.UUID,
) -> None:
    await session.execute(
        delete(WorkerZoneAccess).where(
            WorkerZoneAccess.organization_id == ctx.organization_id,
            WorkerZoneAccess.worker_id == worker.id,
        )
    )
    for location_id in dict.fromkeys(location_ids):
        location = await inv_selectors.get_location(session, ctx.organization_id, location_id)
        if location is None:
            raise NotFoundProblem("That zone location is not part of this organization")
        if location.warehouse_id != warehouse_id:
            raise ConflictProblem("A worker can only be granted zones in their own warehouse")
        session.add(
            WorkerZoneAccess(
                organization_id=ctx.organization_id, worker_id=worker.id, location_id=location.id
            )
        )


async def update_worker(
    session: AsyncSession,
    ctx: ScopeContext,
    worker: Worker,
    *,
    warehouse_id: uuid.UUID | None,
    display_name: str | None,
    is_active: bool | None,
    skills: Sequence[str] | None,
    zone_location_ids: Sequence[uuid.UUID] | None,
    request: Request | None = None,
) -> Worker:
    before = {"warehouse_id": str(worker.warehouse_id), "is_active": worker.is_active}
    if warehouse_id is not None:
        warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
        if warehouse is None:
            raise NotFoundProblem("That warehouse is not part of this organization")
        worker.warehouse_id = warehouse.id
    if display_name is not None:
        worker.display_name = display_name
    if is_active is not None:
        worker.is_active = is_active
    if skills is not None:
        await _replace_skills(session, ctx, worker, skills)
    if zone_location_ids is not None:
        await _replace_zone_access(
            session, ctx, worker, zone_location_ids, warehouse_id=worker.warehouse_id
        )
    worker.updated_by = str(ctx.principal.sub)
    worker.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="worker.updated",
        entity="worker",
        entity_id=worker.id,
        before=before,
        after={"warehouse_id": str(worker.warehouse_id), "is_active": worker.is_active},
        request=request,
    )
    return worker


async def set_presence(
    session: AsyncSession,
    ctx: ScopeContext,
    worker: Worker,
    *,
    status: str,
    request: Request | None = None,
) -> WorkerPresence:
    if status not in domain.WORKER_PRESENCE_STATES:
        raise ValidationProblem(f"{status!r} is not a recognised presence state")
    presence = await selectors.get_presence(
        session, ctx.organization_id, worker.id, for_update=True
    )
    now = datetime.now(UTC)
    if presence is None:
        presence = WorkerPresence(
            organization_id=ctx.organization_id, worker_id=worker.id, status=status
        )
        session.add(presence)
    else:
        presence.status = status
    presence.last_seen_at = now
    presence.updated_at = now
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="worker.presence_set",
        entity="worker",
        entity_id=worker.id,
        after={"status": status},
        request=request,
    )
    return presence


# ------------------------------------------------------------------ pick task creation (ADR-0025
# §1: ACCEPTED -> IN_FULFILMENT fires when the first task is created)


async def create_pick_tasks_for_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    order_id: uuid.UUID,
    order_version: int,
    request: Request | None = None,
) -> list[PickTask]:
    """Derive every pick task an accepted order needs from its lines, grouped by zone
    (`app.fulfilment.selectors.resolve_zone_for_location`), and move the order `ACCEPTED ->
    IN_FULFILMENT` in the same transaction (ADR-0025 §1's order-row-first rule: the order is
    locked before anything about its tasks is written)."""
    org_id = ctx.organization_id
    order = await order_selectors.get_order(session, org_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    if order.version != order_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {order_version}; "
            "reload it and try again"
        )
    # Security review BLOCKER 3: `can_transition_order(status, "IN_FULFILMENT")` alone also
    # answers `True` for `ON_HOLD` (the ADR's own `ON_HOLD -> IN_FULFILMENT` release edge) — a
    # held order must not be able to spawn new tasks through this door. Only a genuinely accepted
    # order may start fulfilment; `ON_HOLD -> IN_FULFILMENT` is exclusively
    # `release_hold_order`'s own recomputation.
    if order.status != "ACCEPTED":
        raise ConflictProblem(
            f"Order {order.number} is {order.status} and cannot start fulfilment; only an "
            "accepted order can"
        )
    already = (
        (await session.execute(selectors.pick_tasks_stmt(org_id, order_id=order.id)))
        .scalars()
        .first()
    )
    if already is not None:
        raise ConflictProblem(f"Order {order.number} already has pick tasks")

    lines = await order_selectors.lines_for(session, org_id, order.id)
    if not lines:
        raise ConflictProblem(f"Order {order.number} has no lines to pick")
    items = await order_selectors.items_for_lines(
        session, org_id, [line.inventory_item_id for line in lines]
    )
    locations = await order_selectors.locations_for_items(session, org_id, list(items.values()))
    reservations = await selectors.reservations_by_order_line(session, org_id, order.id)
    resolved_settings = await order_selectors.resolve_settings(session, org_id, order.warehouse_id)
    staging = await selectors.resolve_kind_location(
        session, org_id, order.warehouse_id, ("staging",)
    )
    if staging is None:
        raise ConflictProblem(
            "This warehouse has no staging location; create one (kind=staging) before "
            "fulfilment can start"
        )

    zone_cache: dict[uuid.UUID, uuid.UUID | None] = {}
    groups: dict[uuid.UUID | None, list[Any]] = {}
    for line in lines:
        item = items.get(line.inventory_item_id)
        if item is None or item.home_location_id is None:
            name = item.sku if item else str(line.inventory_item_id)
            raise ConflictProblem(f"{name} has no stocking location; it cannot be picked")
        source = locations.get(item.home_location_id)
        if source is None:
            raise ConflictProblem(f"{item.sku}'s stocking location no longer exists")
        if item.home_location_id not in zone_cache:
            zone = await selectors.resolve_zone_for_location(session, org_id, item.home_location_id)
            zone_cache[item.home_location_id] = zone.id if zone else None
        groups.setdefault(zone_cache[item.home_location_id], []).append((line, item, source))

    created: list[PickTask] = []
    for zone_id, group in groups.items():
        task = PickTask(
            organization_id=org_id,
            created_by=str(ctx.principal.sub),
            order_id=order.id,
            warehouse_id=order.warehouse_id,
            zone_location_id=zone_id,
            status="CREATED",
            round=0,
            max_rounds=resolved_settings.offer_rounds,
            offer_timeout_seconds=resolved_settings.offer_timeout_seconds,
        )
        session.add(task)
        await session.flush()
        for line, item, source in group:
            reservation = reservations.get(line.id)
            if reservation is None:
                raise ConflictProblem(
                    f"{item.sku} has no reservation from this order's accept; it cannot be picked"
                )
            session.add(
                PickTaskLine(
                    organization_id=org_id,
                    created_by=str(ctx.principal.sub),
                    pick_task_id=task.id,
                    order_line_id=line.id,
                    inventory_item_id=item.id,
                    source_location_id=source.id,
                    destination_location_id=staging.id,
                    reservation_id=reservation.id,
                    quantity_milli=line.quantity_milli,
                    sequence=source.pick_sequence,
                )
            )
        created.append(task)
    await session.flush()

    before_status = order.status
    order.status = "IN_FULFILMENT"
    order.version += 1
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    session.add(
        OrderTransition(
            organization_id=org_id,
            order_id=order.id,
            from_status=before_status,
            to_status="IN_FULFILMENT",
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="order.fulfilment_started",
        entity="order",
        entity_id=order.id,
        after={"status": "IN_FULFILMENT", "task_count": len(created)},
        request=request,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="pick_task.created",
        entity="order",
        entity_id=order.id,
        after={"task_ids": [str(t.id) for t in created]},
        request=request,
    )
    # No outbox event for the order's own `IN_FULFILMENT` transition — the plan's event
    # catalogue (docs/fulfilment/plan.md §5) has no matching `in_fulfilment` event, the same
    # precedent `app/orders/services.py::release_hold_order` sets for `ON_HOLD`'s own release.
    return created


async def _maybe_ready_order(
    session: AsyncSession, ctx: ScopeContext, order: Any, *, request: Request | None = None
) -> None:
    """`IN_FULFILMENT -> READY` (ADR-0025 §1): fires once no task on the order is open and at
    least one is `PICKED`/`CLOSED`. Called from `confirm_pick` **after** the order row is already
    locked by that call — never acquires its own lock, so two workers finishing the two zones of
    one order at the same moment serialise on the order row and only the second confirmation moves
    it (ADR-0025 §1's own worked example).

    Also runs the two READY-triggered facts Fulfilment plan F4 adds
    (`app.orders.services.handle_order_ready`: issues a pickup code if the org requires one,
    requests an invoice from finance) in this same transaction — `app/orders/` is imported here,
    never the other way (ADR-0024 §1), the same direction `EVT_ORDER_READY` above is already
    imported from."""
    tasks = (
        (await session.execute(selectors.pick_tasks_stmt(ctx.organization_id, order_id=order.id)))
        .scalars()
        .all()
    )
    has_open = any(t.status in _OPEN_TASK_STATUSES for t in tasks)
    has_settled = any(t.status in _SETTLED_TASK_STATUSES for t in tasks)
    if has_open or not has_settled:
        return
    # Security review BLOCKER 3: `can_transition_order(status, "READY")` alone also answers
    # `True` for `ON_HOLD` (the ADR's own `ON_HOLD -> READY` release edge) — a task finishing
    # while its order is held must not silently carry the order to `READY` too. ADR-0025 §1 is
    # explicit: "the order stays ON_HOLD ... on release the recomputation ... sends it to READY".
    # Only an order genuinely `IN_FULFILMENT` may complete through this door.
    if order.status != "IN_FULFILMENT":
        return
    before = order.status
    order.status = "READY"
    order.version += 1
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=before,
            to_status="READY",
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.ready",
        entity="order",
        entity_id=order.id,
        after={"status": "READY"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_READY,
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "order_id": str(order.id),
            "number": order.number,
        },
        actor=_actor(ctx),
    )
    await order_services.handle_order_ready(session, ctx, order, request=request)


# ------------------------------------------------------------------ offers (ADR-0025 §3)


def _has_permission(ctx: ScopeContext, code: str) -> bool:
    return ctx.principal.has(code)


async def offer_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: PickTask,
    *,
    worker_id: uuid.UUID,
    request: Request | None = None,
) -> AssignmentOffer:
    """`CREATED -> OFFERED` or `ESCALATED -> OFFERED` — a dispatcher offering a task to one
    worker. Manual assignment is the only path into `OFFERED` in F3 (no auto-assign engine); a
    manager assigning after escalation uses this exact same call (ADR-0025 §2)."""
    org_id = ctx.organization_id
    if not domain.can_transition_pick_task(task.status, "OFFERED"):
        raise ConflictProblem(f"Task {task.id} is {task.status} and cannot be offered")
    # Security review BLOCKER 3: ADR-0025 §1 — "A hold stops new offers on the order's tasks."
    # Nothing else in this call ever looked at the order at all.
    order = await order_selectors.get_order(session, org_id, task.order_id)
    if order is None:  # pragma: no cover - defensive, the task's own FK guarantees this
        raise NotFoundProblem("That task's order is not part of this organization")
    if order.status == "ON_HOLD":
        raise ConflictProblem(
            f"Order {order.number} is on hold; no new offers until it is released"
        )
    worker = await selectors.get_worker(session, org_id, worker_id)
    if worker is None:
        raise NotFoundProblem("That worker is not part of this organization")
    if not worker.is_active:
        raise ConflictProblem("That worker is not active")
    if worker.warehouse_id != task.warehouse_id:
        raise ConflictProblem("That worker is not assigned to this task's warehouse")
    if task.zone_location_id is not None and not await selectors.has_zone_access(
        session, org_id, worker.id, task.zone_location_id
    ):
        raise ConflictProblem("That worker has no zone access for this task")
    if await selectors.get_live_offer(session, org_id, task.id) is not None:
        raise ConflictProblem("This task already has a live offer")

    now = datetime.now(UTC)
    offer = AssignmentOffer(
        organization_id=org_id,
        task_id=task.id,
        worker_id=worker.id,
        round=task.round + 1,
        state="OFFERED",
        score_breakdown={"kind": "manual", "assigned_by": str(ctx.principal.sub)},
        offered_at=now,
        expires_at=now + timedelta(seconds=task.offer_timeout_seconds),
    )
    session.add(offer)
    before_status = task.status
    task.status = "OFFERED"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = now
    await session.flush()

    schedule_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        due_at=offer.expires_at,
        idempotency_key=f"fulfilment-offer-expiry:{offer.id}",
        target_event_type=EVT_OFFER_EXPIRE_REQUESTED,
        target_aggregate_type="assignment_offer",
        target_aggregate_id=offer.id,
        target_payload={"offer_id": str(offer.id), "task_id": str(task.id)},
        target_actor=_actor(ctx),
    )
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="pick_task.offered",
        entity="pick_task",
        entity_id=task.id,
        before={"status": before_status},
        after={"status": "OFFERED", "worker_id": str(worker.id), "round": offer.round},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_TASK_OFFERED,
        aggregate_type="pick_task",
        aggregate_id=task.id,
        payload={
            "organization_id": str(org_id),
            "task_id": str(task.id),
            "offer_id": str(offer.id),
            "worker_id": str(worker.id),
            "round": offer.round,
            "expires_at": offer.expires_at.isoformat(),
        },
        actor=_actor(ctx),
    )
    return offer


async def _fail_round(
    session: AsyncSession,
    ctx: ScopeContext,
    task: PickTask,
    *,
    from_status: str,
    action: str,
    event_type: str,
    request: Request | None,
) -> None:
    """The shared tail of a decline, an expiry and a pre-start release (ADR-0025 §2): the round
    counter advances, and the task lands on `CREATED` — or, if that spends the last round, straight
    on to `ESCALATED` in the same transaction (`app.domain.PICK_TASK_TRANSITIONS`'s own docstring
    explains why there is no direct edge for that second hop)."""
    org_id = ctx.organization_id
    task.round += 1
    exhausted = task.round >= task.max_rounds
    if not domain.can_transition_pick_task(from_status, "CREATED"):  # pragma: no cover - defensive
        raise ConflictProblem(f"Task {task.id} cannot fail its round from {from_status}")
    task.status = "CREATED"
    if exhausted and domain.can_transition_pick_task("CREATED", "ESCALATED"):
        task.status = "ESCALATED"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="pick_task",
        entity_id=task.id,
        after={"status": task.status, "round": task.round, "escalated": exhausted},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=event_type,
        aggregate_type="pick_task",
        aggregate_id=task.id,
        payload={"organization_id": str(org_id), "task_id": str(task.id), "round": task.round},
        actor=_actor(ctx),
    )
    if exhausted:
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="pick_task.escalated",
            entity="pick_task",
            entity_id=task.id,
            after={"status": task.status, "round": task.round},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_TASK_ESCALATED,
            aggregate_type="pick_task",
            aggregate_id=task.id,
            payload={"organization_id": str(org_id), "task_id": str(task.id), "round": task.round},
            actor=_actor(ctx),
        )


async def accept_offer(
    session: AsyncSession, ctx: ScopeContext, offer_id: uuid.UUID, *, request: Request | None = None
) -> AssignmentOffer:
    """The exact conditional update ADR-0025 §3 specifies. A zero-row update is a 409 — the offer
    expired, was withdrawn, was already answered, or is not the caller's — never a 500.

    **Lock ordering (security review MAJOR 3).** The task row is locked *before* the offer's
    conditional update runs — the same order `cancel_task`/`release_task` already use — so a
    dispatcher cancelling this task while the worker taps Accept can never deadlock (Postgres
    would otherwise abort one side with a raw `deadlock detected` error on a genuine race, a 500,
    not the clean 409 this function promises). The offer itself is only ever peeked at, unlocked,
    to learn which task to lock; the conditional `UPDATE ... WHERE state = 'OFFERED'` is still the
    one statement that actually decides the race, exactly as ADR-0025 §3 specifies.
    """
    org_id = ctx.organization_id
    offer = await selectors.get_offer(session, org_id, offer_id)
    if offer is None:
        raise NotFoundProblem("That offer is not part of this organization")
    worker = await caller_worker(session, ctx)
    ctx.assert_can_touch(owner_id=worker.employee_id if offer.worker_id == worker.id else None)

    task = await selectors.get_pick_task(session, org_id, offer.task_id, for_update=True)
    if task is None:  # pragma: no cover - defensive, the offer's own FK guarantees this
        raise NotFoundProblem("That task is not part of this organization")

    result = await session.execute(
        update(AssignmentOffer)
        .where(
            AssignmentOffer.id == offer_id,
            AssignmentOffer.organization_id == org_id,
            AssignmentOffer.worker_id == worker.id,
            AssignmentOffer.state == "OFFERED",
            AssignmentOffer.expires_at > func.now(),
        )
        .values(state="ACCEPTED", responded_at=func.now())
        .returning(AssignmentOffer.id)
    )
    if result.scalar_one_or_none() is None:
        raise ConflictProblem("That offer is no longer yours to accept")

    if task.status != "OFFERED":  # pragma: no cover - defensive, see docstring
        raise ConflictProblem("That task is no longer in a state to be accepted")
    task.status = "ACCEPTED"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = datetime.now(UTC)
    await session.flush()

    refreshed = await selectors.get_offer(session, org_id, offer_id)
    assert refreshed is not None
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="pick_task.offer_accepted",
        entity="assignment_offer",
        entity_id=offer_id,
        after={"state": "ACCEPTED", "task_id": str(offer.task_id)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_TASK_ACCEPTED,
        aggregate_type="pick_task",
        aggregate_id=offer.task_id,
        payload={
            "organization_id": str(org_id),
            "task_id": str(offer.task_id),
            "offer_id": str(offer_id),
            "worker_id": str(worker.id),
        },
        actor=_actor(ctx),
    )
    return refreshed


async def decline_offer(
    session: AsyncSession, ctx: ScopeContext, offer_id: uuid.UUID, *, request: Request | None = None
) -> AssignmentOffer:
    """**Lock ordering (security review MAJOR 3)**: the task row is locked before the offer's
    conditional update, the same reasoning `accept_offer`'s own docstring gives."""
    org_id = ctx.organization_id
    offer = await selectors.get_offer(session, org_id, offer_id)
    if offer is None:
        raise NotFoundProblem("That offer is not part of this organization")
    worker = await caller_worker(session, ctx)
    ctx.assert_can_touch(owner_id=worker.employee_id if offer.worker_id == worker.id else None)

    task = await selectors.get_pick_task(session, org_id, offer.task_id, for_update=True)
    if task is None:  # pragma: no cover - defensive, the offer's own FK guarantees this
        raise NotFoundProblem("That task is not part of this organization")

    result = await session.execute(
        update(AssignmentOffer)
        .where(
            AssignmentOffer.id == offer_id,
            AssignmentOffer.organization_id == org_id,
            AssignmentOffer.worker_id == worker.id,
            AssignmentOffer.state == "OFFERED",
            AssignmentOffer.expires_at > func.now(),
        )
        .values(state="DECLINED", responded_at=func.now())
        .returning(AssignmentOffer.id)
    )
    if result.scalar_one_or_none() is None:
        raise ConflictProblem("That offer is no longer yours to decline")

    await _fail_round(
        session,
        ctx,
        task,
        from_status="OFFERED",
        action="pick_task.offer_declined",
        event_type=EVT_TASK_DECLINED,
        request=request,
    )
    refreshed = await selectors.get_offer(session, org_id, offer_id)
    assert refreshed is not None
    return refreshed


async def release_task(
    session: AsyncSession, ctx: ScopeContext, task: PickTask, *, request: Request | None = None
) -> PickTask:
    """`ACCEPTED -> CREATED` (or `-> ESCALATED`) — the worker hands the task back before starting
    (ADR-0025 §2), "treated as a failed round, same exclusion" as a decline."""
    if task.status != "ACCEPTED":
        raise ConflictProblem(f"Task {task.id} is {task.status}, not accepted")
    worker = await caller_worker(session, ctx)
    live = await selectors.get_live_offer(session, ctx.organization_id, task.id)
    if live is None or live.worker_id != worker.id or live.state != "ACCEPTED":
        raise ForbiddenProblem("This task is not currently yours to release")

    result = await session.execute(
        update(AssignmentOffer)
        .where(
            AssignmentOffer.id == live.id,
            AssignmentOffer.organization_id == ctx.organization_id,
            AssignmentOffer.worker_id == worker.id,
            AssignmentOffer.state == "ACCEPTED",
        )
        .values(state="RELEASED", responded_at=func.now())
        .returning(AssignmentOffer.id)
    )
    if result.scalar_one_or_none() is None:
        raise ConflictProblem("That task is no longer yours to release")

    await _fail_round(
        session,
        ctx,
        task,
        from_status="ACCEPTED",
        action="pick_task.released_before_start",
        event_type=EVT_TASK_DECLINED,
        request=request,
    )
    return task


# ------------------------------------------------------------------ scan / pick / exception


async def record_scan(
    session: AsyncSession,
    ctx: ScopeContext,
    task: PickTask,
    *,
    barcode: str,
    location_id: uuid.UUID | None,
    inventory_item_id: uuid.UUID | None,
    request: Request | None = None,
) -> ScanEvent:
    """Record one barcode scan against a task. **Security review BLOCKER 2**: `location_id` and
    `inventory_item_id` are plain single-column FKs with no RLS-visible tenant check at the
    Postgres level (ADR-0000 §1's chokepoint is the query, not the constraint), so each is
    resolved through its own org-scoped selector before it is ever written — never inserted
    straight from the request body. A caller's org must resolve them itself; an id that exists
    only in another organization gets the exact same refusal as one that does not exist anywhere,
    never a 200-vs-404 split that would let a caller probe another tenant's ids (ADR-0000 §1).

    Goes one step further than "exists in my org": a location must be this task's own warehouse,
    and an item must be one of this task's own lines — a scan is otherwise a no-op signal from a
    barcode gun, never a pointer to any location or item a caller can merely see.
    """
    worker = await caller_worker(session, ctx)
    live = await selectors.get_live_offer(session, ctx.organization_id, task.id)
    if live is None or live.worker_id != worker.id or live.state != "ACCEPTED":
        raise ForbiddenProblem("This task is not currently yours to scan against")

    resolved_location_id = location_id
    if location_id is not None:
        location = await inv_selectors.get_location(session, ctx.organization_id, location_id)
        if location is None or location.warehouse_id != task.warehouse_id:
            raise NotFoundProblem("That location is not part of this organization")
        resolved_location_id = location.id

    resolved_item_id = inventory_item_id
    if inventory_item_id is not None:
        item = await inv_selectors.get_inventory_item(
            session, ctx.organization_id, inventory_item_id
        )
        if item is None:
            raise NotFoundProblem("That inventory item is not part of this organization")
        task_lines = await selectors.lines_for_task(session, ctx.organization_id, task.id)
        if item.id not in {line.inventory_item_id for line in task_lines}:
            raise NotFoundProblem("That inventory item is not part of this organization")
        resolved_item_id = item.id

    scan = ScanEvent(
        organization_id=ctx.organization_id,
        task_id=task.id,
        worker_id=worker.id,
        location_id=resolved_location_id,
        inventory_item_id=resolved_item_id,
        barcode=barcode,
    )
    session.add(scan)

    if task.status == "ACCEPTED" and domain.can_transition_pick_task("ACCEPTED", "IN_PROGRESS"):
        before = task.status
        task.status = "IN_PROGRESS"
        task.updated_by = str(ctx.principal.sub)
        task.updated_at = datetime.now(UTC)
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="pick_task.started",
            entity="pick_task",
            entity_id=task.id,
            before={"status": before},
            after={"status": "IN_PROGRESS"},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_TASK_STARTED,
            aggregate_type="pick_task",
            aggregate_id=task.id,
            payload={"organization_id": str(ctx.organization_id), "task_id": str(task.id)},
            actor=_actor(ctx),
        )
    await session.flush()
    return scan


def _parse_line_quantity(quantity: str | None, item_uom: str, remaining_milli: int) -> int:
    if quantity is None:
        return remaining_milli
    try:
        parsed = parse_qty(quantity, item_uom)
    except QuantityError as exc:
        raise ValidationProblem(str(exc)) from exc
    if parsed <= 0 or parsed > MAX_ABS_MILLI:
        raise ValidationProblem("A pick quantity must be positive")
    return parsed


async def confirm_pick(
    session: AsyncSession,
    ctx: ScopeContext,
    task_id: uuid.UUID,
    line_id: uuid.UUID,
    *,
    quantity: str | None,
    reason: str | None,
    note: str,
    request: Request | None = None,
) -> tuple[PickTask, PickTaskLine]:
    """Confirm (all or part of) one task line. A confirmation always posts through `post_move` as
    a relocation transferring the line's own reservation (never an issue). Confirming less than
    the line's remaining request raises a `pick_exceptions` row for the shortfall and pauses the
    task at `EXCEPTION` — it never silently drops the difference (plan F3's exit criterion).

    **Locks the order row before the task row** (ADR-0025 §1) — this is the one fulfilment write
    that can move `orders.status` (`IN_FULFILMENT -> READY`), so two workers finishing the two
    zones of one order at the same moment serialise here, and it is the second confirmation that
    reads every task settled and moves the order.
    """
    org_id = ctx.organization_id
    peek = await selectors.get_pick_task(session, org_id, task_id)
    if peek is None:
        raise NotFoundProblem("That task is not part of this organization")
    order = await order_selectors.get_order(session, org_id, peek.order_id, for_update=True)
    if order is None:  # pragma: no cover - defensive, the task's own FK guarantees this
        raise NotFoundProblem("That task's order is not part of this organization")
    task = await selectors.get_pick_task(session, org_id, task_id, for_update=True)
    assert task is not None

    worker = await caller_worker(session, ctx)
    live = await selectors.get_live_offer(session, org_id, task.id)
    if live is None or live.worker_id != worker.id or live.state != "ACCEPTED":
        raise ForbiddenProblem("This task is not currently yours to work")
    if task.status not in ("ACCEPTED", "IN_PROGRESS"):
        raise ConflictProblem(f"Task {task.id} is {task.status}; lines cannot be confirmed")

    if task.status == "ACCEPTED":
        task.status = "IN_PROGRESS"
        task.updated_by = str(ctx.principal.sub)
        task.updated_at = datetime.now(UTC)
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="pick_task.started",
            entity="pick_task",
            entity_id=task.id,
            after={"status": "IN_PROGRESS"},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_TASK_STARTED,
            aggregate_type="pick_task",
            aggregate_id=task.id,
            payload={"organization_id": str(org_id), "task_id": str(task.id)},
            actor=_actor(ctx),
        )

    line = await selectors.get_task_line(session, org_id, line_id, for_update=True)
    if line is None or line.pick_task_id != task.id:
        raise NotFoundProblem("That line is not part of this task")
    remaining = line.quantity_milli - line.picked_milli - line.short_milli
    if remaining <= 0:
        raise ConflictProblem("This line has already been confirmed")

    items = await order_selectors.items_for_lines(session, org_id, [line.inventory_item_id])
    item = items.get(line.inventory_item_id)
    if item is None:  # pragma: no cover - defensive
        raise ConflictProblem("This line's item no longer exists")

    requested = _parse_line_quantity(quantity, item.uom, remaining)
    if requested > remaining:
        raise ConflictProblem(
            f"Only {remaining} milli-units remain on this line; cannot confirm {requested}"
        )
    shortfall = remaining - requested
    if shortfall > 0 and reason is None:
        raise ValidationProblem("A short pick needs a reason")

    if requested > 0:
        if line.reservation_id is None:  # pragma: no cover - defensive
            raise ConflictProblem("This line has no reservation to transfer")
        await ledger.post_move(
            session,
            ctx,
            kind="relocation",
            lines=[
                MoveLine(
                    item_id=line.inventory_item_id,
                    location_id=line.source_location_id,
                    quantity_milli=-requested,
                    transfer_reservation_id=line.reservation_id,
                ),
                MoveLine(
                    item_id=line.inventory_item_id,
                    location_id=line.destination_location_id,
                    quantity_milli=requested,
                ),
            ],
            occurred_on=date.today(),
            reference_type="pick_task_line",
            reference_id=line.id,
            idempotency_key=f"pick:{line.id}",
            occurred_on_explicit=False,
            request=request,
        )
        line.picked_milli += requested
        line.updated_by = str(ctx.principal.sub)
        line.updated_at = datetime.now(UTC)
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="pick_task_line.confirmed",
            entity="pick_task_line",
            entity_id=line.id,
            after={"picked": requested, "picked_milli": line.picked_milli},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_PICK_CONFIRMED,
            aggregate_type="pick_task_line",
            aggregate_id=line.id,
            payload={
                "organization_id": str(org_id),
                "task_id": str(task.id),
                "task_line_id": str(line.id),
                "quantity_milli": requested,
            },
            actor=_actor(ctx),
        )

    if shortfall > 0:
        assert reason is not None
        exc = PickException(
            organization_id=org_id,
            task_id=task.id,
            task_line_id=line.id,
            reason=reason,
            quantity_milli=shortfall,
            note=note,
            status="open",
            raised_by=str(ctx.principal.sub),
        )
        session.add(exc)
        line.short_milli += shortfall
        # Security review MAJOR 1 #3: the shortfall is never coming — this line's remaining
        # request is now fully accounted for between `picked_milli` and `short_milli`
        # (`domain.pick_task_line_settled`), so the quantity that was never picked must not sit
        # reserved at the source bin forever. Released through the kernel's own `release` path,
        # the same one `app.orders.services.cancel_order` uses, never a hand-rolled balance edit.
        if line.reservation_id is None:  # pragma: no cover - defensive, see the model's docstring
            raise ConflictProblem("This line has no reservation to release the shortfall from")
        await ledger.release(
            session,
            ctx,
            line.reservation_id,
            quantity_milli=shortfall,
            idempotency_key=f"pick-short:{line.id}",
            request=request,
        )
        if not domain.can_transition_pick_task(task.status, "EXCEPTION"):
            raise ConflictProblem(f"Task {task.id} cannot raise an exception from {task.status}")
        task.status = "EXCEPTION"
        task.updated_by = str(ctx.principal.sub)
        task.updated_at = datetime.now(UTC)
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="pick_task.exception_raised",
            entity="pick_task",
            entity_id=task.id,
            after={"reason": reason, "short_milli": shortfall, "task_line_id": str(line.id)},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_PICK_EXCEPTION_RAISED,
            aggregate_type="pick_task",
            aggregate_id=task.id,
            payload={
                "organization_id": str(org_id),
                "task_id": str(task.id),
                "task_line_id": str(line.id),
                "reason": reason,
                "quantity_milli": shortfall,
            },
            actor=_actor(ctx),
        )
    else:
        await session.flush()
        all_lines = await selectors.lines_for_task(session, org_id, task.id)
        all_settled = all(
            domain.pick_task_line_settled(
                quantity_milli=item_line.quantity_milli,
                picked_milli=item_line.picked_milli,
                short_milli=item_line.short_milli,
            )
            for item_line in all_lines
        )
        if all_settled and domain.can_transition_pick_task(task.status, "PICKED"):
            task.status = "PICKED"
            task.updated_by = str(ctx.principal.sub)
            task.updated_at = datetime.now(UTC)
            await session.flush()
            record_audit(
                session,
                AuditLog,
                organization_id=org_id,
                actor_id=ctx.principal.sub,
                action="pick_task.completed",
                entity="pick_task",
                entity_id=task.id,
                after={"status": "PICKED"},
                request=request,
            )
            publish_event(
                session,
                OutboxEvent,
                organization_id=org_id,
                event_type=EVT_TASK_COMPLETED,
                aggregate_type="pick_task",
                aggregate_id=task.id,
                payload={"organization_id": str(org_id), "task_id": str(task.id)},
                actor=_actor(ctx),
            )
            await _maybe_ready_order(session, ctx, order, request=request)

    await session.flush()
    return task, line


async def resolve_exception(
    session: AsyncSession,
    ctx: ScopeContext,
    task_id: uuid.UUID,
    exception_id: uuid.UUID,
    *,
    note: str,
    request: Request | None = None,
) -> tuple[PickTask, PickException]:
    """Resolve one open exception and resume picking (`EXCEPTION -> IN_PROGRESS`).

    **Ownership (security review BLOCKER 1)**: this is a worker-scoped action exactly like
    `confirm_pick`/`record_scan` — the caller's own `workers` row must hold this task's live
    `ACCEPTED` offer, checked with `caller_worker`/`get_live_offer`, never `task.execute:OWN`
    alone. A caller with no relationship to the task is refused with the same 403
    (`ForbiddenProblem`) every other worker-scoped action in this module uses for "not yours".

    **Order-row-first locking (ADR-0025 §1)**, the same shape `confirm_pick` documents: this is
    now a second call in this module that can move `orders.status` (`IN_FULFILMENT -> READY`),
    since resolving the *last* open exception can complete the task in the same transaction
    (MAJOR 1 #1 below) — so the order row is locked before the task row, in that fixed order.

    **Completion tail (security review MAJOR 1 #1)**: a short pick already accounts for its
    shortfall (`line.short_milli`), so once the exception that paused the task is resolved, every
    line may already be settled by quantity — in which case the task must complete (`PICKED`) and
    the order re-checked (`_maybe_ready_order`) right here, exactly as `confirm_pick`'s own
    settle-and-complete tail does, or the task sits `IN_PROGRESS` forever with no way back in (a
    re-confirm of the already-settled line 409s: "This line has already been confirmed").
    """
    org_id = ctx.organization_id
    peek = await selectors.get_pick_task(session, org_id, task_id)
    if peek is None:
        raise NotFoundProblem("That task is not part of this organization")
    order = await order_selectors.get_order(session, org_id, peek.order_id, for_update=True)
    if order is None:  # pragma: no cover - defensive, the task's own FK guarantees this
        raise NotFoundProblem("That task's order is not part of this organization")
    task = await selectors.get_pick_task(session, org_id, task_id, for_update=True)
    assert task is not None

    worker = await caller_worker(session, ctx)
    live = await selectors.get_live_offer(session, org_id, task.id)
    if live is None or live.worker_id != worker.id or live.state != "ACCEPTED":
        raise ForbiddenProblem("This task is not currently yours to resolve")

    exception = await selectors.get_exception(session, org_id, exception_id, for_update=True)
    if exception is None or exception.task_id != task.id:
        raise NotFoundProblem("That exception is not part of this task")
    if exception.status != "open":
        raise ConflictProblem("That exception is already resolved")
    if not domain.can_transition_pick_task(task.status, "IN_PROGRESS"):
        raise ConflictProblem(f"Task {task.id} is {task.status} and cannot resume picking")

    exception.status = "resolved"
    exception.resolved_by = str(ctx.principal.sub)
    exception.resolved_at = datetime.now(UTC)
    if note:
        exception.note = f"{exception.note}\n{note}" if exception.note else note
    task.status = "IN_PROGRESS"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="pick_exception.resolved",
        entity="pick_exception",
        entity_id=exception.id,
        after={"status": "resolved"},
        request=request,
    )

    all_lines = await selectors.lines_for_task(session, org_id, task.id)
    all_settled = all(
        domain.pick_task_line_settled(
            quantity_milli=line.quantity_milli,
            picked_milli=line.picked_milli,
            short_milli=line.short_milli,
        )
        for line in all_lines
    )
    if all_settled and domain.can_transition_pick_task(task.status, "PICKED"):
        task.status = "PICKED"
        task.updated_by = str(ctx.principal.sub)
        task.updated_at = datetime.now(UTC)
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="pick_task.completed",
            entity="pick_task",
            entity_id=task.id,
            after={"status": "PICKED"},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_TASK_COMPLETED,
            aggregate_type="pick_task",
            aggregate_id=task.id,
            payload={"organization_id": str(org_id), "task_id": str(task.id)},
            actor=_actor(ctx),
        )
        await _maybe_ready_order(session, ctx, order, request=request)

    await session.flush()
    return task, exception


# ------------------------------------------------------------------ cancel / close


async def cancel_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: PickTask,
    *,
    reason: str,
    has_override: bool,
    request: Request | None = None,
) -> PickTask:
    if not domain.can_transition_pick_task(task.status, "CANCELLED"):
        raise ConflictProblem(f"Task {task.id} is {task.status} and cannot be cancelled")
    if task.status in domain.PICK_TASK_OVERRIDE_REQUIRED_FOR_CANCEL and not has_override:
        raise ForbiddenProblem(
            f"Cancelling a task that is {task.status} needs operations.fulfilment.task.override"
        )

    live = await selectors.get_live_offer(session, ctx.organization_id, task.id)
    if live is not None:
        await session.execute(
            update(AssignmentOffer)
            .where(
                AssignmentOffer.id == live.id,
                AssignmentOffer.organization_id == ctx.organization_id,
                AssignmentOffer.state.in_(("OFFERED", "ACCEPTED")),
            )
            .values(state="WITHDRAWN", responded_at=func.now())
        )

    # M1 (security review): whatever this task's own lines never got to pick must be given back
    # to available stock, not left stuck on the order's original accept-time reservation forever.
    # Without this, cancelling a task while it is still `CREATED` (which needs no override, just
    # `task.assign`) let the order reach `READY`/dispatch with a line that was never picked *and*
    # left its reservation permanently open — the order could never be cancelled either, once
    # `dispatch_order`'s own coverage check (B1/M1) refuses to draw from it.
    org_id = ctx.organization_id
    lines = await selectors.lines_for_task(session, org_id, task.id)
    released_lines: list[str] = []
    for line in lines:
        remaining = line.quantity_milli - line.picked_milli - line.short_milli
        if remaining <= 0 or line.reservation_id is None:
            continue
        await ledger.release(
            session,
            ctx,
            line.reservation_id,
            quantity_milli=remaining,
            idempotency_key=f"pick-cancel:{line.id}",
            request=request,
        )
        released_lines.append(str(line.id))

    before = task.status
    task.status = "CANCELLED"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="pick_task.cancelled",
        entity="pick_task",
        entity_id=task.id,
        before={"status": before},
        after={
            "status": "CANCELLED",
            "reason": reason,
            "released_task_lines": released_lines,
        },
        request=request,
    )
    return task


async def close_task(
    session: AsyncSession, ctx: ScopeContext, task: PickTask, *, request: Request | None = None
) -> PickTask:
    """`PICKED -> CLOSED`, forced by hand (`operations.fulfilment.task.override`). F3 ships no
    automatic close-on-handover or close-on-putaway path yet (both are later phases), so this is
    the only door into `CLOSED` today — a real edge in `PICK_TASK_TRANSITIONS`, not a bypass of
    it, per the override permission's own rule (ADR-0025 §2)."""
    if not domain.can_transition_pick_task(task.status, "CLOSED"):
        raise ConflictProblem(f"Task {task.id} is {task.status} and cannot be closed")
    before = task.status
    task.status = "CLOSED"
    task.updated_by = str(ctx.principal.sub)
    task.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="pick_task.closed",
        entity="pick_task",
        entity_id=task.id,
        before={"status": before},
        after={"status": "CLOSED"},
        request=request,
    )
    return task


# ------------------------------------------------------------------ packaging


async def create_packaging_material(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    length_mm: int | None,
    width_mm: int | None,
    height_mm: int | None,
    max_weight_grams: int | None,
    tare_weight_grams: int | None,
    is_active: bool,
    request: Request | None = None,
) -> PackagingMaterial:
    if await selectors.get_packaging_material_by_code(session, ctx.organization_id, code):
        raise ConflictProblem(f"A packaging material coded {code!r} already exists")
    material = PackagingMaterial(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        max_weight_grams=max_weight_grams,
        tare_weight_grams=tare_weight_grams,
        is_active=is_active,
    )
    session.add(material)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="packaging_material.created",
        entity="packaging_material",
        entity_id=material.id,
        after={"code": code, "name": name},
        request=request,
    )
    return material


async def confirm_package(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    order_id: uuid.UUID,
    packaging_material_id: uuid.UUID | None,
    destination_location_id: uuid.UUID,
    weight_grams: int | None,
    lines: Sequence[Any],
    request: Request | None = None,
) -> Package:
    """One call opens a package, packs it and posts the staging -> pack/dock relocation,
    transferring each line's own destination reservation onward (ADR-0024 §6) — F3 has no
    separate open/add-lines/confirm sequence (`Package`'s own docstring).

    Locks the order row first (security review B1) — not because this call moves `orders.status`
    itself (it doesn't), but because it must refuse packing onto an order that already left
    (`HANDED_OVER`/`DELIVERED` or beyond): without the lock, a pack confirmation could race a
    dispatch and land after the order's own goods were already handed over, creating packages and
    reservation transfers dispatch never accounts for."""
    org_id = ctx.organization_id
    order = await order_selectors.get_order(session, org_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That order is not part of this organization")
    if order.status not in ("IN_FULFILMENT", "READY"):
        raise ConflictProblem(f"Order {order.number} is {order.status}; it cannot be packed")
    destination = await inv_selectors.get_location(session, org_id, destination_location_id)
    if destination is None:
        raise NotFoundProblem("That destination location is not part of this organization")
    if destination.warehouse_id != order.warehouse_id:
        raise ConflictProblem("The destination location is not in this order's warehouse")
    material = None
    if packaging_material_id is not None:
        material = await selectors.get_packaging_material(session, org_id, packaging_material_id)
        if material is None:
            raise NotFoundProblem("That packaging material is not part of this organization")

    line_ids = [line.pick_task_line_id for line in lines]
    if len(set(line_ids)) != len(line_ids):
        raise ValidationProblem("A package cannot name the same pick-task line twice")

    task_lines: dict[uuid.UUID, PickTaskLine] = {}
    for line_id in sorted(line_ids):
        task_line = await selectors.get_task_line(session, org_id, line_id, for_update=True)
        if task_line is None:
            raise NotFoundProblem("That pick-task line is not part of this organization")
        task_lines[line_id] = task_line

    packaged_so_far = await selectors.packaged_milli_for_task_lines(session, org_id, line_ids)
    items = await order_selectors.items_for_lines(
        session, org_id, [tl.inventory_item_id for tl in task_lines.values()]
    )

    move_lines: list[MoveLine] = []
    to_create: list[tuple[PickTaskLine, int]] = []
    for spec in lines:
        task_line = task_lines[spec.pick_task_line_id]
        task = await selectors.get_pick_task(session, org_id, task_line.pick_task_id)
        if task is None or task.order_id != order.id:
            raise ConflictProblem("That pick-task line is not part of this order")
        item = items[task_line.inventory_item_id]
        qty = _parse_line_quantity(spec.quantity, item.uom, task_line.picked_milli)
        already = packaged_so_far.get(task_line.id, 0)
        available = task_line.picked_milli - already
        if qty <= 0 or qty > available:
            raise ConflictProblem(
                f"Only {available} milli-units of {item.sku} are staged and not yet packed"
            )
        destination_reservation = await selectors.destination_reservation_for(
            session, org_id, task_line
        )
        if destination_reservation is None:
            raise ConflictProblem(
                f"{item.sku} has no staged reservation to pack from — was it actually picked?"
            )
        move_lines.append(
            MoveLine(
                item_id=task_line.inventory_item_id,
                location_id=task_line.destination_location_id,
                quantity_milli=-qty,
                transfer_reservation_id=destination_reservation.id,
            )
        )
        move_lines.append(
            MoveLine(
                item_id=task_line.inventory_item_id,
                location_id=destination.id,
                quantity_milli=qty,
            )
        )
        to_create.append((task_line, qty))

    package = Package(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        order_id=order.id,
        packaging_material_id=material.id if material else None,
        destination_location_id=destination.id,
        status="OPEN",
        weight_grams=weight_grams,
    )
    session.add(package)
    await session.flush()
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_PACK_STARTED,
        aggregate_type="package",
        aggregate_id=package.id,
        payload={
            "organization_id": str(org_id),
            "package_id": str(package.id),
            "order_id": str(order.id),
        },
        actor=_actor(ctx),
    )

    await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=move_lines,
        occurred_on=date.today(),
        reference_type="package",
        reference_id=package.id,
        idempotency_key=f"pack:{package.id}",
        occurred_on_explicit=False,
        request=request,
    )

    for task_line, qty in to_create:
        session.add(
            PackageLine(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                package_id=package.id,
                pick_task_line_id=task_line.id,
                inventory_item_id=task_line.inventory_item_id,
                quantity_milli=qty,
            )
        )
    package.status = "PACKED"
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="package.confirmed",
        entity="package",
        entity_id=package.id,
        after={"status": "PACKED", "line_count": len(to_create)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_PACK_COMPLETED,
        aggregate_type="package",
        aggregate_id=package.id,
        payload={
            "organization_id": str(org_id),
            "package_id": str(package.id),
            "order_id": str(order.id),
        },
        actor=_actor(ctx),
    )
    return package


# ------------------------------------------------------------------ event-driven (ADR-0024 §1:
# fulfilment consumes orders' events, orders never imports fulfilment)


async def expire_offer(session: AsyncSession, ctx: ScopeContext, *, offer_id: uuid.UUID) -> bool:
    """`SET state = 'EXPIRED' WHERE id = :offer AND state = 'OFFERED'` — the exact ADR-0025 §3
    conditional update. Returns whether it actually fired (an accept that won the race means this
    is a no-op, not an error).

    **Lock ordering (security review MAJOR 3)**: the offer is only peeked at, unlocked, to learn
    its task; the task row is then locked *before* the conditional update runs, the same order
    `accept_offer`'s own docstring explains (and the same order needed here even though expiry
    never raises — the deadlock this avoids is a DB-level abort, which would otherwise still take
    down whichever side loses it, expiry included).
    """
    org_id = ctx.organization_id
    peek = await selectors.get_offer(session, org_id, offer_id)
    if peek is None:
        return False

    task = await selectors.get_pick_task(session, org_id, peek.task_id, for_update=True)
    if task is None:  # pragma: no cover - defensive
        return False

    result = await session.execute(
        update(AssignmentOffer)
        .where(
            AssignmentOffer.id == offer_id,
            AssignmentOffer.organization_id == org_id,
            AssignmentOffer.state == "OFFERED",
        )
        .values(state="EXPIRED", responded_at=func.now())
        .returning(AssignmentOffer.id)
    )
    if result.scalar_one_or_none() is None:
        return False

    await _fail_round(
        session,
        ctx,
        task,
        from_status="OFFERED",
        action="pick_task.offer_expired",
        event_type=EVT_TASK_EXPIRED,
        request=None,
    )
    return True


async def cancel_tasks_for_order(
    session: AsyncSession, ctx: ScopeContext, *, order_id: uuid.UUID
) -> list[uuid.UUID]:
    """The fulfilment half of ADR-0025 §1's cancellation fan-out: every open pick task on a
    cancelled order moves to `CANCELLED`, and its live offer (if any) to `WITHDRAWN`. Reached only
    from `app.consumers.order_cancelled`, over the outbox — `app.orders.services.cancel_order`
    never calls into this module directly (ADR-0024 §1). A task already `PICKED` is left as-is: a
    picked task is not cancellable (ADR-0025 §2), and the putaway that would reverse it is a later
    phase's addition (not part of F3's own table)."""
    org_id = ctx.organization_id
    tasks = (
        (await session.execute(selectors.pick_tasks_stmt(org_id, order_id=order_id)))
        .scalars()
        .all()
    )
    cancelled_ids: list[uuid.UUID] = []
    for task in tasks:
        if task.status not in _OPEN_TASK_STATUSES:
            continue
        locked = await selectors.get_pick_task(session, org_id, task.id, for_update=True)
        assert locked is not None
        # N1 (security review): re-check under the lock — a task can settle (-> PICKED) between
        # the unlocked read above and this row lock. Skip it, don't raise: a raise here is
        # uncaught (this only ever runs from `app.consumers.order_cancelled`) and would
        # dead-letter the whole cancellation event for a task that no longer needs cancelling.
        if locked.status not in _OPEN_TASK_STATUSES:
            continue
        await cancel_task(
            session,
            ctx,
            locked,
            reason="Order cancelled",
            has_override=True,
            request=None,
        )
        cancelled_ids.append(locked.id)
    return cancelled_ids
