"""Writes for quality (plan Phase 4; `docs/inventory/quality.md`).

**Stock moves only through the kernel.** Putting stock on hold, releasing it, rejecting it and
disposing of it are all `app.inventory.ledger.post_move` calls — paired status-change lines in one
move, so the quantity leaves one bucket and enters another in a single transaction and the
reconciler sees no drift. Nothing in this package writes a ledger row or a balance itself.

**One anchor row, locked first.** Every decision locks the `inspections` row `FOR UPDATE` before
reading its status or calling the kernel (which then takes item, balance and reservation locks in
its own fixed order). Two simultaneous decisions on one inspection therefore queue on that row; the
second finds the status already moved and is refused with a 409 — or, if it carries the same
`Idempotency-Key` and the same payload, is answered with the first one's result.

**Idempotency** is layered: the kernel move for each step carries a deterministic key
(`qc:<inspection>:<step>`), so even a lost-update bug could not post stock twice; and the row keeps
the client's key plus a hash of the request, so a retry replays and a different request under the
same key is a 409. Creation is serialised per (organization, key) by a transaction advisory lock.

**Events are staged in the same transaction** (`publish_event` writes the outbox row); the relay
publishes after commit. Every step writes an audit row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.inventory.quantity import QuantityError, format_qty, parse_qty
from app.jobwork import domain as jw_domain
from app.models import (
    AuditLog,
    Inspection,
    InspectionResult,
    InventoryItem,
    Location,
    Lot,
    OutboxEvent,
    QcTemplate,
    QcTemplateParam,
    Warehouse,
)
from app.quality import domain, selectors
from app.quality.schemas import (
    DispositionIn,
    InspectionIn,
    InspectionResultIn,
    QcParamIn,
    QcTemplateIn,
    QcTemplatePatch,
    RejectIn,
    ReleaseIn,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_HOLD = "operations.quality.hold"
EVT_RELEASED = "operations.quality.released"
EVT_REJECTED = "operations.quality.rejected"
EVT_DISPOSED = "operations.quality.disposed"

#: Statuses in which the inspected quantity is still sitting in `QUALITY_HOLD`.
_STOCK_HELD_STATUSES = ("PENDING", "HOLD", "REWORK")


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _check_key(key: str | None) -> None:
    if key is not None and not 1 <= len(key) <= 128:
        raise ValidationProblem("An idempotency key is 1-128 characters")


def _qty(value: str, uom: str, *, what: str = "quantity") -> int:
    try:
        parsed = parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(str(exc)) from exc
    if parsed <= 0:
        raise ValidationProblem(f"The {what} must be greater than zero")
    return parsed


def _bound(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return parse_qty(value, "", allow_negative=True)
    except QuantityError as exc:
        raise ValidationProblem(str(exc)) from exc


async def _advisory_lock(session: AsyncSession, *parts: object) -> None:
    """Serialises concurrent first-use of one idempotency key (a unique index alone would turn the
    loser into an IntegrityError instead of a replay)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": ":".join(str(p) for p in parts)},
    )


# ------------------------------------------------------------------ templates


def _validated_params(params: list[QcParamIn]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for position, p in enumerate(params):
        name = p.name.strip()
        if not name:
            raise ValidationProblem("A parameter needs a name")
        if name.lower() in seen:
            raise ValidationProblem(f"Parameter {name!r} appears twice")
        seen.add(name.lower())
        lo, hi = _bound(p.min), _bound(p.max)
        if lo is not None and hi is not None and lo > hi:
            raise ValidationProblem(f"{name}: the minimum is above the maximum")
        out.append(
            {
                "position": position,
                "name": name,
                "kind": p.kind,
                "unit": p.unit,
                "min_milli": lo,
                "max_milli": hi,
                "is_critical": p.is_critical,
            }
        )
    return out


async def _item_in_org(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> InventoryItem:
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.organization_id == org_id,
                InventoryItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundProblem("That item is not part of this organization")
    return item


async def _location_in_org(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> Location:
    location = (
        await session.execute(
            select(Location)
            .join(Warehouse, Warehouse.id == Location.warehouse_id)
            .where(
                Location.id == location_id,
                Location.organization_id == org_id,
                Location.deleted_at.is_(None),
                Warehouse.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if location is None:
        raise NotFoundProblem("That location is not part of this organization")
    return location


async def create_template(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: QcTemplateIn,
    *,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> QcTemplate:
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    request_hash = _hash(payload.model_dump(mode="json"))
    if idempotency_key is not None:
        await _advisory_lock(session, org_id, "qc_template", idempotency_key)
        existing = await selectors.get_template_by_create_key(session, org_id, idempotency_key)
        if existing is not None:
            if existing.create_hash != request_hash:
                raise ConflictProblem("That Idempotency-Key was used for a different request")
            return existing
    if payload.item_id is not None:
        await _item_in_org(session, org_id, payload.item_id)
    code = payload.code.strip()
    if await selectors.get_template_by_code(session, org_id, code) is not None:
        raise ConflictProblem(f"A QC template with code {code!r} already exists")
    params = _validated_params(payload.params)

    template = QcTemplate(
        organization_id=org_id,
        code=code,
        name=payload.name.strip(),
        kind=payload.kind,
        item_id=payload.item_id,
        description=payload.description,
        is_active=payload.is_active,
        create_key=idempotency_key,
        create_hash=request_hash if idempotency_key else None,
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
    )
    session.add(template)
    await session.flush()
    for p in params:
        session.add(
            QcTemplateParam(
                organization_id=org_id,
                template_id=template.id,
                created_by=str(ctx.principal.sub),
                updated_by=str(ctx.principal.sub),
                **p,
            )
        )
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="qc_template.created",
        entity="qc_template",
        entity_id=template.id,
        after={"code": template.code, "kind": template.kind, "params": len(params)},
        request=request,
    )
    await session.refresh(template)  # server-default timestamps, loaded here not lazily later
    return template


async def update_template(
    session: AsyncSession,
    ctx: ScopeContext,
    template: QcTemplate,
    payload: QcTemplatePatch,
    *,
    request: Request | None = None,
) -> QcTemplate:
    org_id = ctx.organization_id
    before = {"name": template.name, "is_active": template.is_active}
    if payload.name is not None:
        template.name = payload.name.strip()
    if payload.description is not None:
        template.description = payload.description
    if payload.is_active is not None:
        template.is_active = payload.is_active
    params_replaced = False
    if payload.params is not None:
        params = _validated_params(payload.params)
        await session.execute(
            delete(QcTemplateParam).where(
                QcTemplateParam.organization_id == org_id,
                QcTemplateParam.template_id == template.id,
            )
        )
        for p in params:
            session.add(
                QcTemplateParam(
                    organization_id=org_id,
                    template_id=template.id,
                    created_by=str(ctx.principal.sub),
                    updated_by=str(ctx.principal.sub),
                    **p,
                )
            )
        params_replaced = True
    template.updated_by = str(ctx.principal.sub)
    template.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="qc_template.updated",
        entity="qc_template",
        entity_id=template.id,
        before=before,
        after={
            "name": template.name,
            "is_active": template.is_active,
            "params_replaced": params_replaced,
        },
        request=request,
    )
    return template


async def delete_template(
    session: AsyncSession,
    ctx: ScopeContext,
    template: QcTemplate,
    *,
    request: Request | None = None,
) -> None:
    """Soft delete. Inspections already judged against it keep their snapshotted results."""
    template.deleted_at = datetime.now(UTC)
    template.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="qc_template.deleted",
        entity="qc_template",
        entity_id=template.id,
        before={"code": template.code},
        request=request,
    )


# ------------------------------------------------------------------ stock moves


async def _post(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    *,
    step: str,
    kind: str,
    reason: str,
    lines: list[tuple[str, int]],
    note: str,
    request: Request | None,
) -> uuid.UUID:
    """One kernel move for one step, `(status, signed milli)` lines at the inspection's bucket."""
    posted = await ledger.post_move(
        session,
        ctx,
        kind=kind,
        lines=[
            MoveLine(
                item_id=inspection.item_id,
                location_id=inspection.location_id,
                status=status,
                quantity_milli=milli,
                lot_id=inspection.lot_id,
                roll_id=inspection.roll_id,
            )
            for status, milli in lines
        ],
        occurred_on=jw_domain.ist_today(),
        reason=reason,
        reference_type="inspection",
        reference_id=inspection.id,
        note=note[:500],
        idempotency_key=f"qc:{inspection.id}:{step}",
        occurred_on_explicit=False,
        request=request,
    )
    return posted.move.id


def _event_base(ctx: ScopeContext, inspection: Inspection) -> dict[str, Any]:
    return {
        "organization_id": str(ctx.organization_id),
        "inspection_id": str(inspection.id),
        "item_id": str(inspection.item_id),
        "location_id": str(inspection.location_id),
        "lot_id": str(inspection.lot_id) if inspection.lot_id else None,
        "roll_id": str(inspection.roll_id) if inspection.roll_id else None,
        "quantity_milli": inspection.quantity_milli,
    }


def _publish(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type="inspection",
        aggregate_id=inspection.id,
        payload={**_event_base(ctx, inspection), **payload},
        actor=_actor(ctx),
    )


def _audit(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    action: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="inspection",
        entity_id=inspection.id,
        before=before,
        after=after,
        reason=reason or None,
        request=request,
    )


# ------------------------------------------------------------------ inspections


async def _open_claims(
    session: AsyncSession, org_id: uuid.UUID, inspection: Inspection | None, **bucket: Any
) -> int:
    """Quantity other still-open inspections already claim from one `QUALITY_HOLD` bucket."""
    stmt = select(func.coalesce(func.sum(Inspection.quantity_milli), 0)).where(
        Inspection.organization_id == org_id,
        Inspection.deleted_at.is_(None),
        Inspection.status.in_(_STOCK_HELD_STATUSES),
        Inspection.item_id == bucket["item_id"],
        Inspection.location_id == bucket["location_id"],
        Inspection.lot_id.is_not_distinct_from(bucket["lot_id"]),
        Inspection.roll_id.is_not_distinct_from(bucket["roll_id"]),
    )
    if inspection is not None:
        stmt = stmt.where(Inspection.id != inspection.id)
    return int((await session.execute(stmt)).scalar_one())


async def create_inspection(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: InspectionIn,
    *,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    request_hash = _hash(payload.model_dump(mode="json"))
    if idempotency_key is not None:
        await _advisory_lock(session, org_id, "inspection", idempotency_key)
        existing = await selectors.get_inspection_by_create_key(session, org_id, idempotency_key)
        if existing is not None:
            if existing.create_hash != request_hash:
                raise ConflictProblem("That Idempotency-Key was used for a different request")
            return existing

    item = await _item_in_org(session, org_id, payload.item_id)
    location = await _location_in_org(session, org_id, payload.location_id)
    quantity_milli = _qty(payload.quantity, item.uom)

    if payload.template_id is not None:
        template = await selectors.get_template(session, org_id, payload.template_id)
        if template is None:
            raise NotFoundProblem("That QC template is not part of this organization")
        if not template.is_active:
            raise ConflictProblem("That QC template is inactive")
        if template.kind != payload.kind:
            raise ValidationProblem(
                f"That template is for {template.kind} inspections, not {payload.kind}"
            )
        if template.item_id is not None and template.item_id != item.id:
            raise ValidationProblem("That template is for a different item")

    # The lot and roll must exist in this organization and belong to the item — the same check a
    # posted move makes (and the one place the roll is share-locked ahead of any balance), run
    # here so a bogus id is a 404 / 422 before an inspection row exists, with or without a hold.
    await ledger.validate_tracking(
        session,
        org_id,
        [
            MoveLine(
                item_id=item.id,
                location_id=location.id,
                status="QUALITY_HOLD",
                quantity_milli=quantity_milli,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            )
        ],
    )

    inspection = Inspection(
        organization_id=org_id,
        kind=payload.kind,
        status="PENDING",
        template_id=payload.template_id,
        item_id=item.id,
        location_id=location.id,
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        supplier_id=payload.supplier_id,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        quantity_milli=quantity_milli,
        notes=payload.notes,
        create_key=idempotency_key,
        create_hash=request_hash if idempotency_key else None,
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
    )
    session.add(inspection)
    await session.flush()

    if payload.hold_stock:
        inspection.hold_move_id = await _post(
            session,
            ctx,
            inspection,
            step="hold",
            kind="relocation",
            reason="quality_hold",
            lines=[("AVAILABLE", -quantity_milli), ("QUALITY_HOLD", quantity_milli)],
            note=f"Held for inspection {inspection.id}",
            request=request,
        )
        _publish(
            session,
            ctx,
            inspection,
            EVT_HOLD,
            {"reason": "inspection_opened", "move_id": str(inspection.hold_move_id)},
        )
    else:
        bucket = {
            "item_id": item.id,
            "location_id": location.id,
            "lot_id": payload.lot_id,
            "roll_id": payload.roll_id,
        }
        balance = await ledger.lock_bucket(
            session,
            org_id,
            item_id=item.id,
            location_id=location.id,
            status="QUALITY_HOLD",
            lot_id=payload.lot_id,
            roll_id=payload.roll_id,
            create=False,
        )
        on_hold = balance.quantity_milli if balance is not None else 0
        claimed = await _open_claims(session, org_id, inspection, **bucket)
        if on_hold - claimed < quantity_milli:
            raise ConflictProblem(
                f"Only {format_qty(max(on_hold - claimed, 0))} of that item is on quality hold "
                f"and not already under inspection; cannot inspect {format_qty(quantity_milli)}"
            )
    await session.flush()
    _audit(
        session,
        ctx,
        inspection,
        "inspection.created",
        after={
            "kind": inspection.kind,
            "quantity_milli": quantity_milli,
            "held": payload.hold_stock,
            "hold_move_id": str(inspection.hold_move_id) if inspection.hold_move_id else None,
        },
        request=request,
    )
    await session.refresh(inspection)  # server-default timestamps, loaded here not lazily later
    return inspection


def _recorded_name_set(results: list[InspectionResult]) -> set[str]:
    return {r.param_name.lower() for r in results}


async def record_results(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    entries: list[InspectionResultIn],
    *,
    request: Request | None = None,
) -> list[InspectionResult]:
    """Record (or re-record) readings. Replace-by-parameter-name, so it is naturally idempotent."""
    org_id = ctx.organization_id
    if inspection.status not in domain.RESULT_STATUSES:
        raise ConflictProblem(
            f"Inspection is {inspection.status}; results can only change while it is "
            "PENDING or on HOLD"
        )
    names = [e.param_name.strip().lower() for e in entries]
    if len(set(names)) != len(names):
        raise ValidationProblem("A parameter can appear only once per request")

    template_params: dict[str, QcTemplateParam] = {}
    if inspection.template_id is not None:
        params = await selectors.params_for_templates(session, org_id, [inspection.template_id])
        template_params = {p.name.lower(): p for p in params[inspection.template_id]}

    existing = {
        r.param_name.lower(): r
        for r in (await selectors.results_for(session, org_id, [inspection.id]))[inspection.id]
    }
    next_position = len(existing)
    out: list[InspectionResult] = []
    now = datetime.now(UTC)
    for entry in entries:
        key = entry.param_name.strip().lower()
        tp = template_params.get(key)
        if inspection.template_id is not None and tp is None:
            raise ValidationProblem(f"{entry.param_name!r} is not a parameter of this template")
        if tp is not None:
            spec = domain.ParamSpec(tp.kind, tp.min_milli, tp.max_milli)
            snapshot = {
                "param_id": tp.id,
                "param_name": tp.name,
                "param_kind": tp.kind,
                "unit": tp.unit,
                "min_milli": tp.min_milli,
                "max_milli": tp.max_milli,
                "is_critical": tp.is_critical,
                "position": tp.position,
            }
        else:
            # An ad-hoc reading on an inspection with no template: a pass/fail observation only —
            # a number with no bounds would have nothing to be judged against.
            spec = domain.ParamSpec("pass_fail")
            snapshot = {
                "param_id": None,
                "param_name": entry.param_name.strip(),
                "param_kind": "pass_fail",
                "unit": "",
                "min_milli": None,
                "max_milli": None,
                "is_critical": False,
                "position": existing[key].position if key in existing else next_position,
            }
        value_milli: int | None = None
        if spec.kind == "numeric":
            if entry.value is None:
                raise ValidationProblem(f"{snapshot['param_name']}: a numeric reading is required")
            value_milli = _bound(entry.value)
        elif entry.passed is None:
            raise ValidationProblem(f"{snapshot['param_name']}: a pass/fail verdict is required")
        try:
            passed = domain.evaluate_param(spec, value_milli=value_milli, passed=entry.passed)
        except ValueError as exc:  # pragma: no cover - guarded above
            raise ValidationProblem(str(exc)) from exc

        row = existing.get(key)
        if row is None:
            row = InspectionResult(
                organization_id=org_id,
                inspection_id=inspection.id,
                created_by=str(ctx.principal.sub),
                **snapshot,
                value_milli=value_milli,
                passed=passed,
                note=entry.note,
                recorded_by=str(ctx.principal.sub),
            )
            session.add(row)
            next_position += 1
        else:
            for attr, val in snapshot.items():
                setattr(row, attr, val)
            row.value_milli = value_milli
            row.passed = passed
            row.note = entry.note
            row.recorded_by = str(ctx.principal.sub)
            row.updated_by = str(ctx.principal.sub)
            row.updated_at = now
        out.append(row)

    inspection.updated_by = str(ctx.principal.sub)
    inspection.updated_at = now
    await session.flush()
    _audit(
        session,
        ctx,
        inspection,
        "inspection.results_recorded",
        after={
            "recorded": len(out),
            "failed": [r.param_name for r in out if not r.passed],
        },
        request=request,
    )
    return out


def _replay_or_none(
    inspection: Inspection, *, key: str | None, request_hash: str, field: str = "decision"
) -> bool:
    """True when this exact request already ran (answer with the stored result); raises when the
    key was used for something else; False when the key is new."""
    if key is None:
        return False
    stored_key = getattr(inspection, f"{field}_key")
    if stored_key != key:
        return False
    if getattr(inspection, f"{field}_hash") != request_hash:
        raise ConflictProblem("That Idempotency-Key was used for a different request")
    return True


async def _sync_lot(session: AsyncSession, ctx: ScopeContext, inspection: Inspection) -> None:
    """Keep the inspected lot's `quality_status` in step with all the inspections over it (a
    roll-tracked receipt opens one per roll). Called after every status change, so it is a no-op
    write when nothing moved."""
    if inspection.lot_id is None:
        return
    org_id = ctx.organization_id
    # Sibling inspections hold different balance buckets, so the kernel locks do not serialise
    # them. Take the lot row first: the status read below then sees whatever a sibling committed.
    await session.execute(
        select(Lot.id)
        .where(Lot.organization_id == org_id, Lot.id == inspection.lot_id)
        .with_for_update()
    )
    statuses = (
        await session.execute(
            select(Inspection.status).where(
                Inspection.organization_id == org_id, Inspection.lot_id == inspection.lot_id
            )
        )
    ).scalars()
    await session.execute(
        update(Lot)
        .where(Lot.organization_id == org_id, Lot.id == inspection.lot_id)
        .values(
            quality_status=domain.lot_quality_status(statuses),
            updated_by=str(ctx.principal.sub),
        )
    )


def _move_to(
    inspection: Inspection,
    new_status: str,
    ctx: ScopeContext,
    *,
    key: str | None,
    request_hash: str,
) -> str:
    if not domain.can_transition_inspection(inspection.status, new_status):
        raise ConflictProblem(f"Inspection cannot move {inspection.status} -> {new_status}")
    before = inspection.status
    inspection.status = new_status
    inspection.updated_by = str(ctx.principal.sub)
    inspection.updated_at = datetime.now(UTC)
    if key is not None:
        inspection.decision_key = key
        inspection.decision_hash = request_hash
    return before


async def _simple_transition(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    *,
    to_status: str,
    action: str,
    note: str,
    idempotency_key: str | None,
    request: Request | None,
) -> Inspection:
    _check_key(idempotency_key)
    request_hash = _hash({"action": action, "note": note})
    if _replay_or_none(inspection, key=idempotency_key, request_hash=request_hash):
        return inspection
    before = _move_to(inspection, to_status, ctx, key=idempotency_key, request_hash=request_hash)
    await session.flush()
    await _sync_lot(session, ctx, inspection)
    _audit(
        session,
        ctx,
        inspection,
        action,
        before={"status": before},
        after={"status": to_status},
        reason=note,
        request=request,
    )
    return inspection


async def hold(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    *,
    note: str,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """`PENDING -> HOLD`: needs a second look. The stock is already on quality hold; this records
    the decision to keep it there and tells the rest of the system (event)."""
    already = _replay_or_none(
        inspection,
        key=idempotency_key,
        request_hash=_hash({"action": "inspection.hold", "note": note}),
    )
    await _simple_transition(
        session,
        ctx,
        inspection,
        to_status="HOLD",
        action="inspection.hold",
        note=note,
        idempotency_key=idempotency_key,
        request=request,
    )
    if not already:
        _publish(session, ctx, inspection, EVT_HOLD, {"reason": "decision_hold", "move_id": None})
    return inspection


async def rework(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    *,
    note: str,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """`-> REWORK`: sent back for correction; the stock stays on quality hold."""
    return await _simple_transition(
        session,
        ctx,
        inspection,
        to_status="REWORK",
        action="inspection.rework",
        note=note,
        idempotency_key=idempotency_key,
        request=request,
    )


async def reinspect(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    *,
    note: str,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """`REWORK -> PENDING`: the reworked stock is ready to be measured again. Earlier readings are
    cleared — they described the stock as it was before rework."""
    already = _replay_or_none(
        inspection,
        key=idempotency_key,
        request_hash=_hash({"action": "inspection.reinspect", "note": note}),
    )
    await _simple_transition(
        session,
        ctx,
        inspection,
        to_status="PENDING",
        action="inspection.reinspect",
        note=note,
        idempotency_key=idempotency_key,
        request=request,
    )
    if not already:
        await session.execute(
            delete(InspectionResult).where(
                InspectionResult.organization_id == ctx.organization_id,
                InspectionResult.inspection_id == inspection.id,
            )
        )
    return inspection


async def release(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    payload: ReleaseIn,
    *,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """Release held stock to `AVAILABLE` (`PASSED`), or a part of it with the remainder rejected
    (`PARTIAL_PASS`). One kernel move: `QUALITY_HOLD -q`, `AVAILABLE +accepted`, and when partial,
    `<bucket> +rejected`."""
    _check_key(idempotency_key)
    org_id = ctx.organization_id
    request_hash = _hash({"action": "release", **payload.model_dump(mode="json")})
    if _replay_or_none(inspection, key=idempotency_key, request_hash=request_hash):
        return inspection
    if inspection.status not in ("PENDING", "HOLD"):
        raise ConflictProblem(f"Inspection is {inspection.status}; it cannot be released")

    item = await _item_in_org(session, org_id, inspection.item_id)
    accepted_milli = (
        None
        if payload.accepted is None
        else _qty(payload.accepted, item.uom, what="accepted quantity")
    )
    try:
        accepted, rejected, new_status = domain.split_quantity(
            inspection.quantity_milli, accepted_milli
        )
    except ValueError as exc:
        raise ValidationProblem(str(exc)) from exc

    results = (await selectors.results_for(session, org_id, [inspection.id]))[inspection.id]
    judged = [domain.Judged(r.passed, r.is_critical) for r in results]
    if inspection.template_id is not None:
        template = await selectors.get_template(session, org_id, inspection.template_id)
        if template is not None:
            required = {
                p.name.lower()
                for p in (await selectors.params_for_templates(session, org_id, [template.id]))[
                    template.id
                ]
            }
            missing = required - _recorded_name_set(results)
            if missing:
                raise ConflictProblem(
                    f"{len(missing)} template parameter(s) have no recorded result; "
                    "record every reading before releasing"
                )
    if not domain.can_release(judged):
        raise ConflictProblem(
            "A critical parameter failed; this stock cannot be released — reject it, "
            "send it for rework or keep it on hold"
        )
    if domain.has_noncritical_failure(judged) and not payload.note.strip():
        raise ValidationProblem(
            "A non-critical parameter failed; releasing anyway needs a note saying why"
        )

    before = _move_to(inspection, new_status, ctx, key=idempotency_key, request_hash=request_hash)
    lines = [("QUALITY_HOLD", -inspection.quantity_milli), ("AVAILABLE", accepted)]
    if rejected:
        lines.append((payload.rejected_bucket, rejected))
    inspection.decision_move_id = await _post(
        session,
        ctx,
        inspection,
        step="decision",
        kind="relocation",
        reason="quality_release" if not rejected else "quality_partial_release",
        lines=lines,
        note=payload.note or f"Inspection {inspection.id} {new_status}",
        request=request,
    )
    inspection.accepted_milli = accepted
    inspection.rejected_milli = rejected
    inspection.rejected_bucket = payload.rejected_bucket if rejected else None
    inspection.decided_by = str(ctx.principal.sub)
    inspection.decided_at = datetime.now(UTC)
    inspection.decision_note = payload.note
    await session.flush()
    await _sync_lot(session, ctx, inspection)
    _audit(
        session,
        ctx,
        inspection,
        "inspection.released",
        before={"status": before},
        after={
            "status": new_status,
            "accepted_milli": accepted,
            "rejected_milli": rejected,
            "move_id": str(inspection.decision_move_id),
        },
        reason=payload.note,
        request=request,
    )
    _publish(
        session,
        ctx,
        inspection,
        EVT_RELEASED,
        {
            "status": new_status,
            "accepted_milli": accepted,
            "move_id": str(inspection.decision_move_id),
        },
    )
    if rejected:
        _publish(
            session,
            ctx,
            inspection,
            EVT_REJECTED,
            {
                "status": new_status,
                "rejected_milli": rejected,
                "bucket": payload.rejected_bucket,
                "reason": payload.note,
                "move_id": str(inspection.decision_move_id),
            },
        )
    return inspection


async def reject(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    payload: RejectIn,
    *,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """Reject all the held stock (`FAILED`): `QUALITY_HOLD -> REJECTED` (or `QUARANTINE`).
    Disposing of it (return to vendor / scrap) is the separate `dispose` step."""
    _check_key(idempotency_key)
    request_hash = _hash({"action": "reject", **payload.model_dump(mode="json")})
    if _replay_or_none(inspection, key=idempotency_key, request_hash=request_hash):
        return inspection
    reason = payload.reason.strip()
    if not reason:
        raise ValidationProblem("Rejecting stock needs a reason")
    before = _move_to(inspection, "FAILED", ctx, key=idempotency_key, request_hash=request_hash)
    inspection.decision_move_id = await _post(
        session,
        ctx,
        inspection,
        step="decision",
        kind="relocation",
        reason="quality_reject",
        lines=[
            ("QUALITY_HOLD", -inspection.quantity_milli),
            (payload.bucket, inspection.quantity_milli),
        ],
        note=reason,
        request=request,
    )
    inspection.accepted_milli = 0
    inspection.rejected_milli = inspection.quantity_milli
    inspection.rejected_bucket = payload.bucket
    inspection.decided_by = str(ctx.principal.sub)
    inspection.decided_at = datetime.now(UTC)
    inspection.decision_note = reason
    await session.flush()
    await _sync_lot(session, ctx, inspection)
    _audit(
        session,
        ctx,
        inspection,
        "inspection.rejected",
        before={"status": before},
        after={
            "status": "FAILED",
            "bucket": payload.bucket,
            "move_id": str(inspection.decision_move_id),
        },
        reason=reason,
        request=request,
    )
    _publish(
        session,
        ctx,
        inspection,
        EVT_REJECTED,
        {
            "status": "FAILED",
            "rejected_milli": inspection.quantity_milli,
            "bucket": payload.bucket,
            "reason": reason,
            "move_id": str(inspection.decision_move_id),
        },
    )
    return inspection


async def dispose(
    session: AsyncSession,
    ctx: ScopeContext,
    inspection: Inspection,
    payload: DispositionIn,
    *,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> Inspection:
    """The follow-on for rejected stock: take it out of the rejected bucket entirely, as a return
    to the vendor (an `issue`) or a scrap (an `adjustment`). All-or-nothing over the rejected
    quantity, once."""
    _check_key(idempotency_key)
    request_hash = _hash({"action": "dispose", **payload.model_dump(mode="json")})
    if _replay_or_none(
        inspection, key=idempotency_key, request_hash=request_hash, field="disposition"
    ):
        return inspection
    if inspection.status not in domain.DISPOSABLE_STATUSES or not inspection.rejected_milli:
        raise ConflictProblem("Only stock a rejection left behind can be disposed of")
    if inspection.disposition != "NONE":
        raise ConflictProblem(f"That stock was already disposed of ({inspection.disposition})")
    assert inspection.rejected_bucket is not None  # set with rejected_milli

    rtv = payload.action == "RETURN_TO_VENDOR"
    inspection.disposition_move_id = await _post(
        session,
        ctx,
        inspection,
        step="disposition",
        kind="issue" if rtv else "adjustment",
        reason="return_to_vendor" if rtv else "scrap",
        lines=[(inspection.rejected_bucket, -inspection.rejected_milli)],
        note=payload.note or payload.reference or f"Inspection {inspection.id} {payload.action}",
        request=request,
    )
    inspection.disposition = payload.action
    inspection.disposition_note = payload.note
    inspection.disposition_reference = payload.reference
    inspection.disposed_by = str(ctx.principal.sub)
    inspection.disposed_at = datetime.now(UTC)
    inspection.disposition_key = idempotency_key
    inspection.disposition_hash = request_hash if idempotency_key else None
    inspection.updated_by = str(ctx.principal.sub)
    inspection.updated_at = datetime.now(UTC)
    await session.flush()
    await _sync_lot(session, ctx, inspection)
    _audit(
        session,
        ctx,
        inspection,
        "inspection.disposed",
        after={
            "disposition": payload.action,
            "quantity_milli": inspection.rejected_milli,
            "reference": payload.reference,
            "move_id": str(inspection.disposition_move_id),
        },
        reason=payload.note,
        request=request,
    )
    _publish(
        session,
        ctx,
        inspection,
        EVT_DISPOSED,
        {
            "action": payload.action,
            "disposed_milli": inspection.rejected_milli,
            "bucket": inspection.rejected_bucket,
            "reference": payload.reference,
            "supplier_id": str(inspection.supplier_id) if inspection.supplier_id else None,
            "move_id": str(inspection.disposition_move_id),
        },
    )
    return inspection
