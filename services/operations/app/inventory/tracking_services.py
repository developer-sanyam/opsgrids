"""Writes for lots and rolls, including roll split and merge (inventory Phase 1, I3/I4).

Lots and rolls are *records about stock*; the stock itself only ever moves through
`app.inventory.ledger.post_move`. Split and merge are the two operations that change what a roll
*is*, so they do three things in one transaction: create/retire the roll rows, post the stock move
that matches them, and write the `roll_events` / `lot_genealogy` rows that make them traceable.

**Concurrency.** The lock order is roll -> balance, with no exception. Split and merge take their
roll rows `FOR UPDATE` in id order (`tracking_selectors.lock_rolls`), then all of those rolls'
balance rows `FOR UPDATE`, then reach the kernel. The kernel's `post_move` takes every roll its
lines name `FOR SHARE` (id order) *before its first balance lock*, so a plain transfer or status
change of a roll queues on the roll row behind a split/merge instead of holding a balance while
waiting for the roll — which is what would deadlock. Two splits of one roll queue on the roll
row; the second wakes up to a roll that is already `SPLIT` and is refused, or — if it carries the
same `Idempotency-Key` — replays the first. A transfer that loses to a split finds the roll
empty and is refused as `InsufficientStock`/409, never as a database deadlock error.

**One bucket.** A roll is split or merged only when it lives in one bucket: its stock across
*all* buckets must equal what the named bucket holds (ADR-0014 §12), because the children / the
result are created in that one bucket and anything left elsewhere would be stranded on a consumed
roll. After posting, the consumed rolls' ledger lines must end at exactly zero.

**Idempotency.** A keyed split/merge derives the ids of the rolls it creates from
`(organization, key)` (`uuid5`), so a retried request builds byte-identical ledger lines and the
kernel's hash check recognises it. The same key with a different request is a 409, from the kernel.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.inventory import ledger
from app.inventory import tracking_selectors as sel
from app.inventory.ledger import MoveLine, PostedMove
from app.inventory.quantity import QuantityError, canonical_uom_code, format_qty, parse_qty
from app.inventory.stock_ops import load_items, parse_positive, stage
from app.inventory.tracking_schemas import (
    LotGenealogyIn,
    LotIn,
    LotPatch,
    RollIn,
    RollMergeIn,
    RollPatch,
    RollSplitIn,
)
from app.models import (
    InventoryBalance,
    InventoryMove,
    LedgerEntry,
    Location,
    Lot,
    LotGenealogy,
    Roll,
    RollEvent,
)
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

#: `rolls.roll_no` is `VARCHAR(60)`.
MAX_ROLL_NO_LEN = 60

EVT_LOT_CREATED = "operations.inventory.lot.created"
EVT_LOT_UPDATED = "operations.inventory.lot.updated"
EVT_LOT_LINKED = "operations.inventory.lot.linked"
EVT_ROLL_CREATED = "operations.inventory.roll.created"
EVT_ROLL_UPDATED = "operations.inventory.roll.updated"
EVT_ROLL_SPLIT = "operations.inventory.roll.split"
EVT_ROLL_MERGED = "operations.inventory.roll.merged"


def _duplicate(field: str, message: str) -> ConflictProblem:
    return ConflictProblem(
        message, errors=[{"field": field, "code": "duplicate", "message": message}]
    )


def _derived_id(org_id: uuid.UUID, key: str | None, purpose: str) -> uuid.UUID:
    """A fresh id, or — when the request carries an idempotency key — the same id every time for
    the same (organization, key, purpose), so a retry reproduces the first attempt's lines."""
    if key is None:
        return uuid.uuid4()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"opsgrids:{org_id}:{key}:{purpose}")


async def _require_one_bucket(
    session: AsyncSession,
    org_id: uuid.UUID,
    roll: Roll,
    balances: list[InventoryBalance],
    *,
    location_id: uuid.UUID,
    status: str,
    verb: str,
) -> int:
    """The quantity the roll's named bucket holds, after checking that is *all* it holds anywhere
    (`balances` is the roll's full, already-locked balance list). 409 naming the other buckets
    otherwise, so the operator knows what to consolidate first."""
    held = sum(
        b.quantity_milli for b in balances if b.location_id == location_id and b.status == status
    )
    elsewhere = [
        b
        for b in balances
        if b.quantity_milli != 0 and not (b.location_id == location_id and b.status == status)
    ]
    if elsewhere:
        code_rows = await session.execute(
            select(Location.id, Location.code).where(
                Location.organization_id == org_id,
                Location.id.in_({b.location_id for b in elsewhere}),
            )
        )
        codes: dict[uuid.UUID, str] = dict(code_rows.tuples().all())
        where = ", ".join(
            f"{format_qty(b.quantity_milli)} at {codes.get(b.location_id, b.location_id)} "
            f"in {b.status}"
            for b in elsewhere
        )
        raise ConflictProblem(
            f"{roll.roll_no} also holds stock outside that bucket ({where}); a roll is {verb} "
            "only when it lives in one bucket — move or status-change the rest into it first",
            errors=[
                {
                    "field": "location_id",
                    "code": "roll_in_several_buckets",
                    "message": f"{roll.roll_no} is spread over several buckets",
                }
            ],
        )
    return held


def _require_consumed(posted: PostedMove, roll_ids: Iterable[uuid.UUID]) -> None:
    """Each consumed roll's outgoing line must leave it at exactly zero, else the whole operation
    is refused (the request's transaction rolls back) rather than leave stock on a dead roll."""
    wanted = set(roll_ids)
    seen: set[uuid.UUID] = set()
    for entry in posted.entries:
        if entry.roll_id in wanted and entry.quantity_milli < 0:
            seen.add(entry.roll_id)
            if entry.balance_after_milli != 0:
                raise ConflictProblem(
                    "The roll still holds stock after the move; refusing to retire it — "
                    "retry once other stock operations on it have finished"
                )
    if seen != wanted:
        raise ConflictProblem("The move did not take every consumed roll's stock out")


async def _move_for_key(
    session: AsyncSession, org_id: uuid.UUID, key: str | None
) -> InventoryMove | None:
    if key is None:
        return None
    return (
        await session.execute(
            select(InventoryMove).where(
                InventoryMove.organization_id == org_id, InventoryMove.idempotency_key == key
            )
        )
    ).scalar_one_or_none()


# ------------------------------------------------------------------ lots


async def create_lot(
    session: AsyncSession, ctx: ScopeContext, payload: LotIn, *, request: Request | None = None
) -> Lot:
    org_id = ctx.organization_id
    item = (await load_items(session, org_id, [payload.inventory_item_id]))[
        payload.inventory_item_id
    ]
    if not item.track_lot:
        raise ValidationProblem(
            f"{item.sku} is not tracked by lot; enable lot tracking on the item first",
            errors=[{"field": "inventory_item_id", "code": "not_tracked", "message": "no lots"}],
        )
    lot_no = payload.lot_no or f"LOT-{uuid.uuid4().hex[:8].upper()}"
    if payload.barcode is not None and await sel.barcode_taken(
        session, Lot, org_id, payload.barcode
    ):
        raise _duplicate("barcode", f"Barcode {payload.barcode!r} is already in use")
    lot_id = uuid.uuid4()
    inserted = (
        await session.execute(
            pg_insert(Lot)
            .values(
                id=lot_id,
                organization_id=org_id,
                inventory_item_id=item.id,
                lot_no=lot_no,
                supplier_lot_no=payload.supplier_lot_no,
                barcode=payload.barcode,
                manufactured_on=payload.manufactured_on,
                received_on=payload.received_on,
                expires_on=payload.expires_on,
                quality_status=payload.quality_status,
                source_type=payload.source_type,
                source_id=payload.source_id,
                attributes=payload.attributes,
                created_by=str(ctx.principal.sub),
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "lot_no"])
            .returning(Lot.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        raise _duplicate("lot_no", f"A lot numbered {lot_no!r} already exists")
    lot = await sel.get_lot(session, org_id, lot_id)
    assert lot is not None
    stage(
        session,
        ctx,
        action="lot.created",
        event_type=EVT_LOT_CREATED,
        entity="lot",
        entity_id=lot_id,
        after={
            "lot_no": lot_no,
            "inventory_item_id": str(item.id),
            "quality_status": payload.quality_status,
            "source_type": payload.source_type,
            "source_id": str(payload.source_id) if payload.source_id else None,
        },
        payload={"lot_id": str(lot_id), "lot_no": lot_no, "inventory_item_id": str(item.id)},
        reason=None,
        request=request,
    )
    return lot


async def update_lot(
    session: AsyncSession,
    ctx: ScopeContext,
    lot: Lot,
    payload: LotPatch,
    *,
    request: Request | None = None,
) -> Lot:
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    locked = await sel.get_lot(session, ctx.organization_id, lot.id, for_update=True)
    assert locked is not None
    lot = locked
    manufactured = changes.get("manufactured_on", lot.manufactured_on)
    expires = changes.get("expires_on", lot.expires_on)
    if manufactured and expires and expires < manufactured:
        raise ValidationProblem("expires_on is before manufactured_on")
    if changes.get("barcode") is None:
        changes.pop("barcode", None)
    elif changes["barcode"] != lot.barcode and await sel.barcode_taken(
        session, Lot, ctx.organization_id, changes["barcode"], exclude=lot.id
    ):
        raise _duplicate("barcode", f"Barcode {changes['barcode']!r} is already in use")
    before = {field: getattr(lot, field) for field in changes}
    for field, value in changes.items():
        setattr(lot, field, value)
    lot.updated_by = str(ctx.principal.sub)
    await session.flush()
    await session.refresh(lot)
    stage(
        session,
        ctx,
        action="lot.updated",
        event_type=EVT_LOT_UPDATED,
        entity="lot",
        entity_id=lot.id,
        after={"before": before, "changes": changes},
        payload={"lot_id": str(lot.id), "changed": sorted(changes)},
        reason=None,
        request=request,
    )
    return lot


async def link_lots(
    session: AsyncSession,
    ctx: ScopeContext,
    parent_lot_id: uuid.UUID,
    payload: LotGenealogyIn,
    *,
    request: Request | None = None,
) -> LotGenealogy:
    """Record that `parent`'s material went into `child` (a blend, a rework, a derived lot).
    Refused if it would make a lot its own ancestor — the trace is a walk over a DAG, and the
    cycle check and the insert run under one per-organization advisory lock so two concurrent
    links cannot each pass the check and together close a loop."""
    org_id = ctx.organization_id
    child_lot_id = payload.child_lot_id
    if parent_lot_id == child_lot_id:
        raise ValidationProblem("A lot cannot be its own parent")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"lot_genealogy:{org_id}"}
    )
    parent = await sel.get_lot(session, org_id, parent_lot_id)
    child = await sel.get_lot(session, org_id, child_lot_id)
    if parent is None or child is None:
        raise NotFoundProblem("That lot is not part of this organization")
    # Uncapped (the display trace stops at MAX_TRACE_DEPTH; this check must not).
    if await sel.lot_reaches(session, org_id, child_lot_id, parent_lot_id):
        raise ConflictProblem(
            f"{parent.lot_no} already descends from {child.lot_no}; linking them would make a loop"
        )
    existing = (
        await session.execute(
            select(LotGenealogy.id).where(
                LotGenealogy.organization_id == org_id,
                LotGenealogy.parent_lot_id == parent_lot_id,
                LotGenealogy.child_lot_id == child_lot_id,
                LotGenealogy.relation == payload.relation,
            )
        )
    ).first()
    if existing is not None:
        raise ConflictProblem("That link already exists")
    edge = LotGenealogy(
        organization_id=org_id,
        parent_lot_id=parent_lot_id,
        child_lot_id=child_lot_id,
        relation=payload.relation,
        note=payload.note,
        created_by=str(ctx.principal.sub),
    )
    session.add(edge)
    await session.flush()
    stage(
        session,
        ctx,
        action="lot.linked",
        event_type=EVT_LOT_LINKED,
        entity="lot",
        entity_id=parent_lot_id,
        after={
            "child_lot_id": str(child_lot_id),
            "relation": payload.relation,
            "note": payload.note,
        },
        payload={
            "parent_lot_id": str(parent_lot_id),
            "child_lot_id": str(child_lot_id),
            "relation": payload.relation,
        },
        reason=payload.note,
        request=request,
    )
    return edge


# ------------------------------------------------------------------ rolls


def _parse_secondary(qty: str | None, uom: str | None) -> tuple[int | None, str | None]:
    if qty is None or uom is None:
        return None, None
    code = canonical_uom_code(uom)
    try:
        return parse_qty(qty, code), code
    except QuantityError as exc:
        raise ValidationProblem(
            str(exc),
            errors=[{"field": "secondary_qty", "code": "invalid_quantity", "message": str(exc)}],
        ) from exc


async def create_roll(
    session: AsyncSession, ctx: ScopeContext, payload: RollIn, *, request: Request | None = None
) -> Roll:
    """Register a roll. It holds no stock yet — an opening (or later receipt) posts into its
    bucket — but it is a real row the ledger's foreign keys can name."""
    org_id = ctx.organization_id
    item = (await load_items(session, org_id, [payload.inventory_item_id]))[
        payload.inventory_item_id
    ]
    if not item.track_roll:
        raise ValidationProblem(
            f"{item.sku} is not tracked by roll; enable roll tracking on the item first",
            errors=[{"field": "inventory_item_id", "code": "not_tracked", "message": "no rolls"}],
        )
    if payload.lot_id is None and item.track_lot:
        raise ValidationProblem(
            f"{item.sku} is tracked by lot; a roll of it belongs to a lot",
            errors=[{"field": "lot_id", "code": "required", "message": "lot required"}],
        )
    if payload.lot_id is not None:
        lot = await sel.get_lot(session, org_id, payload.lot_id)
        if lot is None:
            raise NotFoundProblem(f"Lot {payload.lot_id} is not in this org")
        if lot.inventory_item_id != item.id:
            raise ValidationProblem("That lot belongs to a different item")
    primary = parse_positive(payload.primary_qty, item.uom, "primary_qty")
    secondary, secondary_uom = _parse_secondary(payload.secondary_qty, payload.secondary_uom)
    roll_no = payload.roll_no or f"ROLL-{uuid.uuid4().hex[:8].upper()}"
    if payload.barcode is not None and await sel.barcode_taken(
        session, Roll, org_id, payload.barcode
    ):
        raise _duplicate("barcode", f"Barcode {payload.barcode!r} is already in use")
    roll_id = uuid.uuid4()
    inserted = (
        await session.execute(
            pg_insert(Roll)
            .values(
                id=roll_id,
                organization_id=org_id,
                inventory_item_id=item.id,
                lot_id=payload.lot_id,
                roll_no=roll_no,
                state="ACTIVE",
                primary_milli=primary,
                secondary_milli=secondary,
                secondary_uom=secondary_uom,
                width_mm=payload.width_mm,
                gsm=payload.gsm,
                shade=payload.shade,
                grade=payload.grade,
                barcode=payload.barcode,
                created_by=str(ctx.principal.sub),
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "roll_no"])
            .returning(Roll.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        raise _duplicate("roll_no", f"A roll numbered {roll_no!r} already exists")
    session.add(
        RollEvent(
            organization_id=org_id,
            roll_id=roll_id,
            kind="CREATED",
            quantity_milli=primary,
            secondary_milli=secondary,
            detail={"roll_no": roll_no},
            created_by=str(ctx.principal.sub),
        )
    )
    roll = (await sel.lock_rolls(session, org_id, [roll_id]))[roll_id]
    stage(
        session,
        ctx,
        action="roll.created",
        event_type=EVT_ROLL_CREATED,
        entity="roll",
        entity_id=roll_id,
        after={
            "roll_no": roll_no,
            "inventory_item_id": str(item.id),
            "lot_id": str(payload.lot_id) if payload.lot_id else None,
            "primary_qty": format_qty(primary),
            "secondary_qty": format_qty(secondary) if secondary is not None else None,
            "secondary_uom": secondary_uom,
        },
        payload={
            "roll_id": str(roll_id),
            "roll_no": roll_no,
            "inventory_item_id": str(item.id),
            "lot_id": str(payload.lot_id) if payload.lot_id else None,
        },
        reason=None,
        request=request,
    )
    return roll


async def update_roll(
    session: AsyncSession,
    ctx: ScopeContext,
    roll_id: uuid.UUID,
    payload: RollPatch,
    *,
    request: Request | None = None,
) -> Roll:
    org_id = ctx.organization_id
    roll = (await sel.lock_rolls(session, org_id, [roll_id])).get(roll_id)
    if roll is None:
        raise NotFoundProblem("That roll is not part of this organization")
    if roll.state != "ACTIVE":
        raise ConflictProblem(f"{roll.roll_no} is {roll.state}; a consumed roll is history")
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    before = {
        "secondary_qty": format_qty(roll.secondary_milli)
        if roll.secondary_milli is not None
        else None,
        "secondary_uom": roll.secondary_uom,
        "width_mm": roll.width_mm,
        "gsm": roll.gsm,
        "shade": roll.shade,
        "grade": roll.grade,
    }
    if "secondary_qty" in changes:
        milli, code = _parse_secondary(changes.pop("secondary_qty"), changes.pop("secondary_uom"))
        roll.secondary_milli, roll.secondary_uom = milli, code
    if changes.get("barcode") is None:
        changes.pop("barcode", None)
    elif changes["barcode"] != roll.barcode and await sel.barcode_taken(
        session, Roll, org_id, changes["barcode"], exclude=roll.id
    ):
        raise _duplicate("barcode", f"Barcode {changes['barcode']!r} is already in use")
    for field in ("width_mm", "gsm", "shade", "grade", "barcode"):
        if field in changes:
            setattr(roll, field, changes[field])
    roll.updated_by = str(ctx.principal.sub)
    await session.flush()
    await session.refresh(roll)
    after = {
        "secondary_qty": format_qty(roll.secondary_milli)
        if roll.secondary_milli is not None
        else None,
        "secondary_uom": roll.secondary_uom,
        "width_mm": roll.width_mm,
        "gsm": roll.gsm,
        "shade": roll.shade,
        "grade": roll.grade,
    }
    changed = sorted(k for k in after if after[k] != before[k])
    session.add(
        RollEvent(
            organization_id=org_id,
            roll_id=roll.id,
            kind="MEASURED",
            secondary_milli=roll.secondary_milli,
            detail={"before": before, "after": after},
            created_by=str(ctx.principal.sub),
        )
    )
    stage(
        session,
        ctx,
        action="roll.updated",
        event_type=EVT_ROLL_UPDATED,
        entity="roll",
        entity_id=roll.id,
        after={"before": before, "after": after},
        payload={"roll_id": str(roll.id), "changed": changed},
        reason=None,
        request=request,
    )
    return roll


# ------------------------------------------------------------------ split


async def split_roll(
    session: AsyncSession,
    ctx: ScopeContext,
    roll_id: uuid.UUID,
    payload: RollSplitIn,
    *,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> tuple[PostedMove, uuid.UUID, list[uuid.UUID]]:
    """One roll into n, exactly. Returns the posted move, the parent id and the children's ids.

    The children's quantities must add up to what the roll's bucket holds, to the milli-unit; the
    roll's measured weight is apportioned in proportion with the rounding `domain.split_secondary`
    documents (floor, remainder to the last child), so the children's weights sum to the parent's
    exactly. The parent's stock leaves and the children's arrives in one `relocation` move — and
    a roll whose stock is reserved is refused by the kernel, with the reservations named.
    """
    org_id = ctx.organization_id
    # The roll lock first, then everything else — see the module docstring.
    parent = (await sel.lock_rolls(session, org_id, [roll_id])).get(roll_id)
    if parent is None:
        raise NotFoundProblem("That roll is not part of this organization")
    child_ids = [
        _derived_id(org_id, idempotency_key, f"split:{roll_id}:{i}")
        for i in range(len(payload.children))
    ]
    items = await load_items(session, org_id, [parent.inventory_item_id])
    item = items[parent.inventory_item_id]
    quantities = [
        parse_positive(child.quantity, item.uom, f"children[{i}].quantity")
        for i, child in enumerate(payload.children)
    ]
    total = sum(quantities)
    lines = [
        MoveLine(
            item.id,
            payload.location_id,
            -total,
            status=payload.status,
            lot_id=parent.lot_id,
            roll_id=parent.id,
        ),
        *(
            MoveLine(
                item.id,
                payload.location_id,
                quantity_milli,
                status=payload.status,
                lot_id=parent.lot_id,
                roll_id=child_id,
            )
            for quantity_milli, child_id in zip(quantities, child_ids, strict=True)
        ),
    ]
    replay = await _move_for_key(session, org_id, idempotency_key) is not None
    if replay:
        # A retry: the first attempt already split this roll, so the checks below (it is ACTIVE,
        # it holds `total`) no longer hold and must not run. The kernel's hash check decides
        # whether this really is the same request.
        posted = await ledger.post_move(
            session,
            ctx,
            kind="relocation",
            lines=lines,
            occurred_on=payload.occurred_on,
            reference_type="roll_split",
            reference_id=parent.id,
            note=payload.note,
            idempotency_key=idempotency_key,
            occurred_on_explicit=occurred_on_explicit,
            request=request,
        )
        return posted, parent.id, child_ids

    if not domain.can_transition_roll(parent.state, "SPLIT"):
        raise ConflictProblem(f"{parent.roll_no} is {parent.state}; only an active roll splits")
    # Every balance row of the roll, locked after the roll itself (roll -> balance), so the
    # one-bucket check below cannot be invalidated by a concurrent transfer.
    parent_balances = (await sel.lock_roll_balances(session, org_id, [parent.id]))[parent.id]
    held = await _require_one_bucket(
        session,
        org_id,
        parent,
        parent_balances,
        location_id=payload.location_id,
        status=payload.status,
        verb="split",
    )
    if held == 0:
        raise ConflictProblem(
            f"{parent.roll_no} holds nothing at that location in {payload.status}"
        )
    try:
        domain.validate_split(held, quantities)
        weights = domain.split_secondary(parent.secondary_milli, quantities)
    except domain.RollOperationError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": "children", "code": "split_total", "message": str(exc)}]
        ) from exc

    roll_nos = [
        child.roll_no or f"{parent.roll_no}-{i + 1}" for i, child in enumerate(payload.children)
    ]
    for i, roll_no in enumerate(roll_nos):
        if len(roll_no) > MAX_ROLL_NO_LEN:
            # Only a derived number can be this long (an explicit one is capped by the schema).
            raise ValidationProblem(
                f"The derived number {roll_no!r} is {len(roll_no)} characters; a roll number is "
                f"at most {MAX_ROLL_NO_LEN}. Name the children explicitly.",
                errors=[
                    {
                        "field": f"children[{i}].roll_no",
                        "code": "too_long",
                        "message": f"derived roll number exceeds {MAX_ROLL_NO_LEN} characters",
                    }
                ],
            )
    if len(set(roll_nos)) != len(roll_nos):
        raise _duplicate("children", "Two children share a roll number")
    if taken := await sel.existing_roll_nos(session, org_id, roll_nos):
        raise _duplicate("children", f"A roll numbered {sorted(taken)[0]!r} already exists")

    for child_id, roll_no, quantity_milli, weight in zip(
        child_ids, roll_nos, quantities, weights, strict=True
    ):
        session.add(
            Roll(
                id=child_id,
                organization_id=org_id,
                inventory_item_id=parent.inventory_item_id,
                lot_id=parent.lot_id,
                roll_no=roll_no,
                state="ACTIVE",
                primary_milli=quantity_milli,
                secondary_milli=weight,
                secondary_uom=parent.secondary_uom if weight is not None else None,
                width_mm=parent.width_mm,
                gsm=parent.gsm,
                shade=parent.shade,
                grade=parent.grade,
                parent_roll_id=parent.id,
                created_by=str(ctx.principal.sub),
            )
        )
    parent.state = "SPLIT"
    parent.updated_by = str(ctx.principal.sub)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The pre-check above ran under the roll lock, but a roll registered by another request
        # in the meantime can still take one of these numbers; say so instead of a 500.
        raise _duplicate("children", "A child roll number was taken by another request") from exc

    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=lines,
        occurred_on=payload.occurred_on,
        reference_type="roll_split",
        reference_id=parent.id,
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    _require_consumed(posted, [parent.id])
    move_id = posted.move.id
    session.add(
        RollEvent(
            organization_id=org_id,
            roll_id=parent.id,
            kind="SPLIT",
            move_id=move_id,
            quantity_milli=total,
            secondary_milli=parent.secondary_milli,
            detail={"children": [str(c) for c in child_ids]},
            created_by=str(ctx.principal.sub),
        )
    )
    for child_id, quantity_milli, weight in zip(child_ids, quantities, weights, strict=True):
        session.add(
            RollEvent(
                organization_id=org_id,
                roll_id=child_id,
                kind="SPLIT_CHILD",
                related_roll_id=parent.id,
                move_id=move_id,
                quantity_milli=quantity_milli,
                secondary_milli=weight,
                created_by=str(ctx.principal.sub),
            )
        )
    if parent.lot_id is not None:
        session.add(
            LotGenealogy(
                organization_id=org_id,
                parent_lot_id=parent.lot_id,
                child_lot_id=parent.lot_id,
                relation="SPLIT",
                move_id=move_id,
                quantity_milli=total,
                created_by=str(ctx.principal.sub),
            )
        )
    await session.flush()
    stage(
        session,
        ctx,
        action="roll.split",
        event_type=EVT_ROLL_SPLIT,
        entity="roll",
        entity_id=parent.id,
        after={
            "move_id": str(move_id),
            "quantity": format_qty(total),
            "children": [
                {
                    "roll_id": str(c),
                    "roll_no": n,
                    "quantity": format_qty(q),
                    "secondary_qty": format_qty(w) if w is not None else None,
                }
                for c, n, q, w in zip(child_ids, roll_nos, quantities, weights, strict=True)
            ],
        },
        payload={
            "parent_roll_id": str(parent.id),
            "child_roll_ids": [str(c) for c in child_ids],
            "move_id": str(move_id),
        },
        reason=payload.note,
        request=request,
    )
    return posted, parent.id, child_ids


# ------------------------------------------------------------------ merge


async def merge_rolls(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: RollMergeIn,
    *,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> tuple[PostedMove, uuid.UUID, list[uuid.UUID]]:
    """n rolls into one new roll. Same item, same lot, same shade, all `ACTIVE` and all holding
    stock in the named bucket, or it is refused. Each source's whole balance leaves and their sum
    arrives on the result, whose weight is the sum of the sources' weights (only if every source
    was weighed). Returns the move, the result's id and the sources' ids."""
    org_id = ctx.organization_id
    source_ids = sorted(set(payload.roll_ids))
    locked = await sel.lock_rolls(session, org_id, source_ids)
    if len(locked) != len(source_ids):
        raise NotFoundProblem("A roll named in the merge is not part of this organization")
    sources = [locked[i] for i in source_ids]
    result_id = _derived_id(org_id, idempotency_key, f"merge:{','.join(map(str, source_ids))}")
    item = (await load_items(session, org_id, [sources[0].inventory_item_id]))[
        sources[0].inventory_item_id
    ]

    prior = await _move_for_key(session, org_id, idempotency_key)
    if prior is not None:
        # A retry. The sources' balances are gone, so the lines are rebuilt from the first
        # attempt's own ledger rows — after checking this request names the same rolls, bucket
        # and result, since the kernel can only compare the lines it is handed.
        entries = (
            (
                await session.execute(
                    select(LedgerEntry).where(
                        LedgerEntry.organization_id == org_id, LedgerEntry.move_id == prior.id
                    )
                )
            )
            .scalars()
            .all()
        )
        outbound = {e.roll_id for e in entries if e.quantity_milli < 0}
        inbound = [e for e in entries if e.quantity_milli > 0]
        same = (
            prior.reference_type == "roll_merge"
            and outbound == set(source_ids)
            and len(inbound) == 1
            and inbound[0].roll_id == result_id
            and all(
                e.location_id == payload.location_id and e.status == payload.status for e in entries
            )
        )
        if not same:
            raise ConflictProblem(
                "That idempotency key was already used for a different stock move; "
                "use a new key for a new request"
            )
        posted = await ledger.post_move(
            session,
            ctx,
            kind="relocation",
            lines=[
                MoveLine(
                    e.inventory_item_id,
                    e.location_id,
                    e.quantity_milli,
                    status=e.status,
                    lot_id=e.lot_id,
                    roll_id=e.roll_id,
                )
                for e in entries
            ],
            occurred_on=payload.occurred_on,
            reference_type="roll_merge",
            reference_id=result_id,
            note=payload.note,
            idempotency_key=idempotency_key,
            occurred_on_explicit=occurred_on_explicit,
            request=request,
        )
        return posted, result_id, source_ids

    for roll in sources:
        if not domain.can_transition_roll(roll.state, "MERGED"):
            raise ConflictProblem(f"{roll.roll_no} is {roll.state}; only active rolls merge")
    try:
        facts = domain.merge_facts(
            [
                domain.RollFacts(
                    item_id=r.inventory_item_id,
                    lot_id=r.lot_id,
                    shade=r.shade,
                    width_mm=r.width_mm,
                    gsm=r.gsm,
                    grade=r.grade,
                    secondary_milli=r.secondary_milli,
                    secondary_uom=r.secondary_uom,
                )
                for r in sources
            ]
        )
    except domain.RollOperationError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": "roll_ids", "code": "not_mergeable", "message": str(exc)}]
        ) from exc

    held: dict[uuid.UUID, int] = {}
    # All of every source's balance rows, locked after the roll rows (roll -> balance).
    source_balances = await sel.lock_roll_balances(session, org_id, source_ids)
    for roll in sources:
        balance = await _require_one_bucket(
            session,
            org_id,
            roll,
            source_balances[roll.id],
            location_id=payload.location_id,
            status=payload.status,
            verb="merged",
        )
        if balance == 0:
            raise ConflictProblem(
                f"{roll.roll_no} holds nothing at that location in {payload.status}"
            )
        held[roll.id] = balance
    total = sum(held.values())

    roll_no = payload.roll_no or f"ROLL-{uuid.uuid4().hex[:8].upper()}"
    if await sel.existing_roll_nos(session, org_id, [roll_no]):
        raise _duplicate("roll_no", f"A roll numbered {roll_no!r} already exists")
    session.add(
        Roll(
            id=result_id,
            organization_id=org_id,
            inventory_item_id=item.id,
            lot_id=facts.lot_id,
            roll_no=roll_no,
            state="ACTIVE",
            primary_milli=total,
            secondary_milli=facts.secondary_milli,
            secondary_uom=facts.secondary_uom,
            width_mm=facts.width_mm,
            gsm=facts.gsm,
            shade=facts.shade,
            grade=facts.grade,
            created_by=str(ctx.principal.sub),
        )
    )
    for roll in sources:
        roll.state = "MERGED"
        roll.updated_by = str(ctx.principal.sub)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate("roll_no", f"A roll numbered {roll_no!r} already exists") from exc

    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            *(
                MoveLine(
                    item.id,
                    payload.location_id,
                    -held[roll.id],
                    status=payload.status,
                    lot_id=roll.lot_id,
                    roll_id=roll.id,
                )
                for roll in sources
            ),
            MoveLine(
                item.id,
                payload.location_id,
                total,
                status=payload.status,
                lot_id=facts.lot_id,
                roll_id=result_id,
            ),
        ],
        occurred_on=payload.occurred_on,
        reference_type="roll_merge",
        reference_id=result_id,
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    _require_consumed(posted, source_ids)
    move_id = posted.move.id
    for roll in sources:
        session.add(
            RollEvent(
                organization_id=org_id,
                roll_id=roll.id,
                kind="MERGED",
                related_roll_id=result_id,
                move_id=move_id,
                quantity_milli=held[roll.id],
                secondary_milli=roll.secondary_milli,
                created_by=str(ctx.principal.sub),
            )
        )
        session.add(
            RollEvent(
                organization_id=org_id,
                roll_id=result_id,
                kind="MERGE_RESULT",
                related_roll_id=roll.id,
                move_id=move_id,
                quantity_milli=held[roll.id],
                secondary_milli=roll.secondary_milli,
                created_by=str(ctx.principal.sub),
            )
        )
    if facts.lot_id is not None:
        session.add(
            LotGenealogy(
                organization_id=org_id,
                parent_lot_id=facts.lot_id,
                child_lot_id=facts.lot_id,
                relation="MERGE",
                move_id=move_id,
                quantity_milli=total,
                created_by=str(ctx.principal.sub),
            )
        )
    await session.flush()
    stage(
        session,
        ctx,
        action="roll.merged",
        event_type=EVT_ROLL_MERGED,
        entity="roll",
        entity_id=result_id,
        after={
            "move_id": str(move_id),
            "quantity": format_qty(total),
            "roll_no": roll_no,
            "sources": [str(s) for s in source_ids],
        },
        payload={
            "source_roll_ids": [str(s) for s in source_ids],
            "result_roll_id": str(result_id),
            "move_id": str(move_id),
        },
        reason=payload.note,
        request=request,
    )
    return posted, result_id, source_ids
