"""MRP writes: create and execute a run, and turn shortages into a requisition request.

A run is created in one transaction and either executed in it (small organizations) or left
`QUEUED` for the worker sweep (`app.mrp.sweep`), which executes it under the tenant policy in its
own transaction. Either way the stored result is the same: `mrp_demands` (what was planned from),
`mrp_shortages` + `mrp_shortage_lines` (what is missing and why), a content hash of the result.

MRP never touches stock: it reads balances, reservations, POs and production orders, and writes
only its own tables. The requisition request is an **event**
(`operations.mrp.requisition_requested`) plus a plain-UUID linkage on each shortage; purchase
requisitions (inventory Phase 2) consume it when they land.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.config import settings
from app.inventory.quantity import format_qty
from app.manufacturing import common
from app.manufacturing.selectors import items_by_id
from app.models import AuditLog, OutboxEvent, PurchaseRequisition
from app.mrp import domain, selectors
from app.mrp.models import MrpDemand, MrpRun, MrpShortage, MrpShortageLine
from app.mrp.schemas import (
    MrpRunIn,
    RequisitionRequestIn,
    RequisitionRequestLineOut,
    RequisitionRequestOut,
)
from app.receiving import requisitions as requisitions_service
from app.receiving.schemas import RequisitionIn, RequisitionLineIn
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_RUN_COMPLETED = "operations.mrp.run_completed"
EVT_REQUISITION_REQUESTED = "operations.mrp.requisition_requested"
#: What the consumer acts under: the requisition request's own permission.
REQUISITION_PERMISSION = "operations.manufacturing.mrp.requisition"


class MrpFailure(Exception):
    """A run could not be computed (a cyclic BOM). The message is shown to the user."""


# ------------------------------------------------------------------ audit + events


def _audit(
    session: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    *,
    action: str,
    entity_id: uuid.UUID,
    after: dict[str, Any],
    request: Request | None = None,
) -> None:
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=actor_id,
        action=action,
        entity="mrp_run",
        entity_id=entity_id,
        after=after,
        request=request,
    )


def _emit(
    session: AsyncSession,
    org_id: uuid.UUID,
    actor: dict[str, Any],
    *,
    event_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=event_type,
        aggregate_type="mrp_run",
        aggregate_id=aggregate_id,
        payload={"organization_id": str(org_id), **payload},
        actor=actor,
    )


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


# ------------------------------------------------------------------ create


async def _next_seq(session: AsyncSession, org_id: uuid.UUID) -> int:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"mrp-seq:{org_id}"}
    )
    return (
        await session.execute(
            select(func.coalesce(func.max(MrpRun.seq), 0)).where(MrpRun.organization_id == org_id)
        )
    ).scalar_one() + 1


async def create_run(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: MrpRunIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> tuple[MrpRun, bool]:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope="mrp.run.create", payload=payload.model_dump(mode="json")
    )
    if claim.replay and claim.result_id is not None:
        existing = await selectors.get_run(session, ctx, claim.result_id)
        if existing is not None:
            return existing, True

    manual: list[dict[str, Any]] = []
    for line in payload.manual_demands:
        item = await common.require_item(session, org_id, line.item_id)
        manual.append(
            {
                "item_id": str(item.id),
                "quantity_milli": common.qty(line.quantity, item.uom, field="quantity"),
                "due_date": line.due_date.isoformat() if line.due_date else None,
                "note": line.note,
            }
        )

    pairs = {(s.component_item_id, s.substitute_item_id) for s in payload.substitutes}
    if len({c for c, _ in pairs}) != len(pairs):
        raise ValidationProblem("choose at most one substitute per component")
    if pairs:
        for component_id, substitute_id in pairs:
            await common.require_item(session, org_id, component_id)
            await common.require_item(session, org_id, substitute_id)
        declared = await selectors.declared_substitute_pairs(session, org_id, pairs)
        missing = pairs - declared
        if missing:
            raise ValidationProblem(
                "No BOM line declares that substitute for that component; add it as a "
                "substitute on the BOM first"
            )

    sources = list(payload.sources)
    plan_date = payload.plan_date or common.today()
    limit = settings().mrp_sync_max_demands
    execution = "SYNC"
    if payload.mode == "worker":
        execution = "WORKER"
    else:
        lines, materials = await selectors.count_open_demand(session, org_id)
        total = (
            (lines if "SALES_ORDER" in sources else 0)
            + (materials if "PRODUCTION_ORDER" in sources else 0)
            + len(manual)
        )
        if total > limit:
            if payload.mode == "sync":
                raise ValidationProblem(
                    f"This run plans from {total} demand lines, over the {limit} that may run "
                    "inside a request; use mode 'auto' or 'worker' so the worker runs it"
                )
            execution = "WORKER"

    seq = await _next_seq(session, org_id)
    run = MrpRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        seq=seq,
        number=f"MRP-{seq:05d}",
        status="QUEUED",
        execution=execution,
        plan_date=plan_date,
        params={
            "sources": sources,
            "manual_demands": manual,
            "substitutes": [
                {"component_item_id": str(c), "substitute_item_id": str(s)}
                for c, s in sorted(pairs)
            ],
        },
        notes=payload.notes,
        requested_by=common.user_ref(ctx),
        created_by=common.user_ref(ctx),
    )
    session.add(run)
    await session.flush()
    _audit(
        session,
        org_id,
        ctx.principal.sub,
        action="mrp_run.created",
        entity_id=run.id,
        after={"number": run.number, "execution": execution, "sources": sources},
        request=request,
    )
    if execution == "SYNC":
        try:
            await execute_run(session, run, actor=common.actor(ctx), actor_id=ctx.principal.sub)
        except MrpFailure as exc:
            raise ConflictProblem(str(exc)) from exc
    claim.done(run.id)
    return run, False


# ------------------------------------------------------------------ execute


def _fail_message(exc: domain.MrpCycleError, sku_of: dict[str, str]) -> str:
    return "The bill of materials loops back on itself: " + " -> ".join(
        sku_of.get(i, i) for i in exc.path
    )


async def execute_run(
    session: AsyncSession,
    run: MrpRun,
    *,
    actor: dict[str, Any],
    actor_id: uuid.UUID | None,
) -> None:
    """Collect demand and supply, plan, and store the result on `run` (which becomes
    `COMPLETED`). Runs in the caller's transaction, which must be bound to the run's tenant.
    Raises `MrpFailure` for a cyclic BOM; nothing is written before that point."""
    org_id = run.organization_id
    params = run.params
    sources: list[str] = list(params.get("sources", []))

    rows: list[selectors.DemandRow] = []
    if "SALES_ORDER" in sources:
        rows += await selectors.collect_sales_demand(session, org_id)
    if "PRODUCTION_ORDER" in sources:
        rows += await selectors.collect_production_demand(session, org_id)
    if "MANUAL" in sources:
        for m in params.get("manual_demands", []):
            due = m.get("due_date")
            rows.append(
                selectors.DemandRow(
                    "MANUAL",
                    None,
                    "manual",
                    uuid.UUID(m["item_id"]),
                    int(m["quantity_milli"]),
                    0,
                    date.fromisoformat(due) if due else None,
                    m.get("note", ""),
                )
            )

    facts = await items_by_id(session, org_id, {r.item_id for r in rows})
    rows = [r for r in rows if r.item_id in facts and not facts[r.item_id].deleted]
    rows.sort(key=lambda r: (r.source_type, r.source_ref, str(r.source_id or ""), str(r.item_id)))

    choices = {
        uuid.UUID(s["component_item_id"]): uuid.UUID(s["substitute_item_id"])
        for s in params.get("substitutes", [])
    }
    boms = await selectors.load_boms(
        session, org_id, {r.item_id for r in rows}, run.plan_date, choices
    )
    reachable = {r.item_id for r in rows} | {uuid.UUID(i) for i in boms}
    supplies = await selectors.supply_for(session, org_id, reachable)
    empty = domain.Supply()

    try:
        result = domain.plan(
            [
                domain.Peg(
                    r.source_type,
                    str(r.source_id) if r.source_id else None,
                    r.source_ref,
                    r.open_milli,
                    r.due_date,
                )
                for r in rows
            ],
            [str(r.item_id) for r in rows],
            lambda item: supplies.get(uuid.UUID(item), empty),
            lambda item: boms.get(item),
        )
    except domain.MrpCycleError as exc:
        names = await items_by_id(session, org_id, {uuid.UUID(i) for i in exc.path})
        raise MrpFailure(_fail_message(exc, {str(k): v.sku for k, v in names.items()})) from exc

    for r in rows:
        session.add(
            MrpDemand(
                id=uuid.uuid4(),
                organization_id=org_id,
                run_id=run.id,
                source_type=r.source_type,
                source_id=r.source_id,
                source_ref=r.source_ref,
                item_id=r.item_id,
                requested_milli=r.requested_milli,
                reserved_milli=r.reserved_milli,
                quantity_milli=r.open_milli,
                due_date=r.due_date,
                note=r.note,
            )
        )
    for plan in result.shortages:
        shortage_id = uuid.uuid4()
        assert plan.action is not None
        session.add(
            MrpShortage(
                id=shortage_id,
                organization_id=org_id,
                run_id=run.id,
                item_id=uuid.UUID(plan.item_id),
                action=plan.action,
                level=plan.level,
                gross_milli=plan.gross_milli,
                on_hand_milli=plan.supply.on_hand_milli,
                reserved_milli=plan.supply.reserved_milli,
                open_po_milli=plan.supply.open_po_milli,
                open_production_milli=plan.supply.open_production_milli,
                shortage_milli=plan.net_milli,
                need_by=plan.need_by,
            )
        )
        await session.flush()
        for peg in plan.pegs:
            session.add(
                MrpShortageLine(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    run_id=run.id,
                    shortage_id=shortage_id,
                    source_type=peg.source_type,
                    source_id=uuid.UUID(peg.source_id) if peg.source_id else None,
                    source_ref=peg.source_ref,
                    parent_item_id=uuid.UUID(peg.parent_item_id) if peg.parent_item_id else None,
                    quantity_milli=peg.quantity_milli,
                    due_date=peg.due,
                )
            )

    run.status = "COMPLETED"
    run.demand_count = len(rows)
    run.shortage_count = len(result.shortages)
    run.items_planned = len(result.items)
    run.result_hash = domain.result_digest(result)
    run.error = ""
    run.completed_at = datetime.now(UTC)
    await session.flush()

    _audit(
        session,
        org_id,
        actor_id,
        action="mrp_run.completed",
        entity_id=run.id,
        after={
            "number": run.number,
            "demands": run.demand_count,
            "shortages": run.shortage_count,
            "result_hash": run.result_hash,
        },
    )
    _emit(
        session,
        org_id,
        actor,
        event_type=EVT_RUN_COMPLETED,
        aggregate_id=run.id,
        payload={
            "run_id": str(run.id),
            "number": run.number,
            "execution": run.execution,
            "plan_date": run.plan_date.isoformat(),
            "demand_count": run.demand_count,
            "items_planned": run.items_planned,
            "shortage_count": run.shortage_count,
            "buy_count": sum(1 for s in result.shortages if s.action == "BUY"),
            "make_count": sum(1 for s in result.shortages if s.action == "MAKE"),
            "result_hash": run.result_hash,
        },
    )


# ------------------------------------------------------------------ the worker's entry


@dataclass(slots=True)
class WorkerReport:
    completed: int = 0
    failed: int = 0


async def process_queued_runs(
    session: AsyncSession, org_id: uuid.UUID, *, limit: int = 5
) -> WorkerReport:
    """Execute this organization's queued runs, oldest first, in the caller's transaction (bound
    to `org_id`). `SKIP LOCKED`: two workers never take the same run. Each run executes in its
    own savepoint, so one bad run becomes `FAILED` with its reason and the next still runs."""
    report = WorkerReport()
    runs = (
        (
            await session.execute(
                select(MrpRun)
                .where(MrpRun.organization_id == org_id, MrpRun.status == "QUEUED")
                .order_by(MrpRun.seq)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        run_id = run.id
        requested_by = run.requested_by
        actor = {"type": "user", "id": requested_by, "membership_id": None}
        try:
            async with session.begin_nested():
                await execute_run(session, run, actor=actor, actor_id=_uuid_or_none(requested_by))
            report.completed += 1
        except Exception as exc:
            message = str(exc) if isinstance(exc, MrpFailure) else f"{type(exc).__name__}: {exc}"
            await session.execute(
                update(MrpRun)
                .where(MrpRun.id == run_id, MrpRun.organization_id == org_id)
                .values(
                    status="FAILED",
                    error=message[:500],
                    completed_at=datetime.now(UTC),
                )
            )
            session.expire(run)
            _audit(
                session,
                org_id,
                _uuid_or_none(requested_by),
                action="mrp_run.failed",
                entity_id=run_id,
                after={"error": message[:500]},
            )
            report.failed += 1
    await session.flush()
    return report


# ------------------------------------------------------------------ requisition request


async def request_requisition(
    session: AsyncSession,
    ctx: ScopeContext,
    run_id: uuid.UUID,
    payload: RequisitionRequestIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> RequisitionRequestOut:
    org_id = ctx.organization_id
    run = await selectors.get_run(session, ctx, run_id)
    if run is None:
        raise NotFoundProblem("That MRP run is not part of this organization")
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"mrp.requisition:{run_id}",
        payload=payload.model_dump(mode="json"),
    )
    if claim.replay and claim.result_id is not None:
        rows = list(
            (
                await session.execute(
                    select(MrpShortage)
                    .where(
                        MrpShortage.organization_id == org_id,
                        MrpShortage.run_id == run_id,
                        MrpShortage.requisition_request_id == claim.result_id,
                    )
                    .order_by(MrpShortage.level, MrpShortage.item_id)
                )
            )
            .scalars()
            .all()
        )
        return await _request_out(session, org_id, run, claim.result_id, rows, replayed=True)

    if run.status != "COMPLETED":
        raise ConflictProblem(
            f"Run {run.number} is {run.status}; only a completed run can be requested from"
        )

    ids = payload.shortage_ids
    rows = await selectors.shortages_by_ids(session, ctx, run_id, ids)
    if ids is not None:
        found = {r.id for r in rows}
        if set(ids) - found:
            raise NotFoundProblem("A shortage in the request is not part of this run")
        made = [r for r in rows if r.action != "BUY"]
        if made:
            raise ValidationProblem(
                "Only purchased items can be requisitioned; a produced item is made with a "
                "production order"
            )
        done = [r for r in rows if r.requisition_request_id is not None]
        if done:
            raise ConflictProblem("A shortage in the request was already requisitioned")
    else:
        rows = [r for r in rows if r.action == "BUY" and r.requisition_request_id is None]
        if not rows:
            raise ConflictProblem("There is nothing left in this run to requisition")
    # an item an earlier run's request has already put in purchasing's hands is not asked for twice
    on_its_way = await selectors.items_already_requested(
        session, org_id, run_id, (r.item_id for r in rows)
    )
    if ids is not None:
        if any(r.item_id in on_its_way for r in rows):
            raise ConflictProblem(
                "A shortage in the request is already being requisitioned from an earlier run"
            )
    else:
        rows = [r for r in rows if r.item_id not in on_its_way]
        if not rows:
            raise ConflictProblem("There is nothing left in this run to requisition")
    rows.sort(key=lambda r: (r.level, r.item_id))

    request_id = uuid.uuid4()
    now = datetime.now(UTC)
    for r in rows:
        r.requisition_request_id = request_id
        r.requisition_requested_at = now
    await session.flush()

    out = await _request_out(session, org_id, run, request_id, rows, replayed=False)
    _emit(
        session,
        org_id,
        common.actor(ctx),
        event_type=EVT_REQUISITION_REQUESTED,
        aggregate_id=run.id,
        payload={
            "request_id": str(request_id),
            "run_id": str(run.id),
            "run_number": run.number,
            "note": payload.note,
            "requested_by": common.user_ref(ctx),
            "requested_by_ref": str(ctx.subject_ref) if ctx.subject_ref else None,
            "lines": [ln.model_dump(mode="json") for ln in out.lines],
        },
    )
    _audit(
        session,
        org_id,
        ctx.principal.sub,
        action="mrp_run.requisition_requested",
        entity_id=run.id,
        after={"request_id": str(request_id), "shortages": [str(r.id) for r in rows]},
        request=request,
    )
    claim.done(request_id)
    return out


async def _request_out(
    session: AsyncSession,
    org_id: uuid.UUID,
    run: MrpRun,
    request_id: uuid.UUID,
    rows: list[MrpShortage],
    *,
    replayed: bool,
) -> RequisitionRequestOut:
    items = await items_by_id(session, org_id, {r.item_id for r in rows})
    return RequisitionRequestOut(
        request_id=str(request_id),
        run_id=str(run.id),
        run_number=run.number,
        replayed=replayed,
        lines=[
            RequisitionRequestLineOut(
                shortage_id=str(r.id),
                item_id=str(r.item_id),
                sku=items[r.item_id].sku if r.item_id in items else "",
                uom=items[r.item_id].uom if r.item_id in items else "",
                quantity=format_qty(r.shortage_milli),
                need_by=r.need_by,
            )
            for r in rows
        ],
    )


# ------------------------------------------------------------------ the request's consumer


def _request_marker(request_id: uuid.UUID) -> str:
    return f"MRP request {request_id}"


async def requisition_for_request(
    session: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any]
) -> PurchaseRequisition | None:
    """Turn an `operations.mrp.requisition_requested` payload into one DRAFT purchase requisition
    that purchasing reviews, submits and converts as usual. Idempotent on the request id, kept in
    the requisition's own `mrp_request_id` column (the reason text is free for users to edit and
    is never consulted): a redelivery, even under a new event id, finds it and does
    nothing. Attributed to the planner who asked (audit actor, and the requester id for OWN scope
    when the event carries their directory reference); a payload without one falls to the system.
    Returns the requisition made, or None when it already existed."""
    request_id = uuid.UUID(str(payload["request_id"]))
    marker = _request_marker(request_id)
    made = (
        await session.execute(
            select(PurchaseRequisition.id).where(
                PurchaseRequisition.organization_id == org_id,
                PurchaseRequisition.mrp_request_id == request_id,
            )
        )
    ).first()
    if made is not None:
        return None
    ctx = ScopeContext(
        principal=Principal(
            sub=_uuid_or_none(payload.get("requested_by")) or uuid.UUID(int=0),
            organization_id=org_id,
            subject_ref=_uuid_or_none(payload.get("requested_by_ref")),
            perms=[f"{REQUISITION_PERMISSION}:ORG"],
            entitlements=["operations"],
        ),
        scope=Scope.ORG,
        permission=REQUISITION_PERMISSION,
    )
    needs = [date.fromisoformat(ln["need_by"]) for ln in payload["lines"] if ln.get("need_by")]
    note = f": {payload['note']}" if payload.get("note") else ""
    req = await requisitions_service.create_requisition(
        session,
        ctx,
        RequisitionIn(
            needed_by=min(needs) if needs else None,
            reason=f"{marker} (run {payload['run_number']}){note}"[:2000],
            requested_by=f"MRP run {payload['run_number']}",
            lines=[
                RequisitionLineIn(
                    inventory_item_id=uuid.UUID(ln["item_id"]),
                    quantity=ln["quantity"],
                    note=f"Shortage in {payload['run_number']}",
                )
                for ln in payload["lines"]
            ],
        ),
    )
    req.mrp_request_id = request_id
    await session.flush()
    await session.execute(
        update(MrpShortage)
        .where(
            MrpShortage.organization_id == org_id,
            MrpShortage.requisition_request_id == request_id,
        )
        .values(requisition_id=req.id)
    )
    return req
