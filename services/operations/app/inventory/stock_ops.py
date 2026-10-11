"""Stock operations on top of the posting kernel (inventory Phase 1, I4): opening stock, bin-to-bin
transfer, status change and storage put-away.

**Nothing here writes a quantity.** Each operation builds `MoveLine`s and hands them to
`app.inventory.ledger.post_move` — ADR-0014 §3's only writer — which takes the bucket locks in
its fixed order, refuses a negative or reserved-into result with the numbers
(`InsufficientStock`, §11), and makes a retried `Idempotency-Key` a replay. What this module adds
is the *meaning* of the request (is this transfer inside one warehouse? is a tracked item given
its lot?), the audit row and the outbox event — both staged only for a fresh post, never for a
replay, so a retried request leaves exactly one of each.

Kinds: opening stock is `opening`; a transfer, a status change and a storage put-away are
`relocation` moves (stock conserved: a negative line and a positive one in one move), told apart by
`reference_type` (`bin_transfer`, `status_change`, `putaway`). That keeps the move-kind vocabulary
the ledger's reconciler and filters already know.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.inventory import ledger, putaway_selectors
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine, PostedMove
from app.inventory.putaway_schemas import PutawayIn
from app.inventory.quantity import QuantityError, format_qty, parse_qty
from app.inventory.tracking_schemas import (
    OpeningStockIn,
    StockMoveLineOut,
    StockMoveOut,
    StockStatusChangeIn,
    StockTransferIn,
)
from app.models import (
    AuditLog,
    InventoryItem,
    LedgerEntry,
    Location,
    OutboxEvent,
    PutawayCapture,
    RollEvent,
)
from platform_core.audit import record_audit
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger

EVT_OPENING_POSTED = "operations.inventory.stock.opening_posted"
EVT_STOCK_TRANSFERRED = "operations.inventory.stock.transferred"
EVT_STOCK_STATUS_CHANGED = "operations.inventory.stock.status_changed"
EVT_PUTAWAY_RECORDED = "operations.inventory.putaway.recorded"
#: Needed in addition to `stock.transfer` to take stock out of a hold status.
RELEASE_PERMISSION = "operations.inventory.stock.adjust"
#: A move's `reference_type` for a storage put-away (ADR-0014 §12's move reference vocabulary).
PUTAWAY_REFERENCE_TYPE = "putaway"
#: Prepended to the client's `Idempotency-Key`, so a put-away's key can never collide with another
#: stock operation's. The column holds 128 characters, so a client key may be at most
#: `PUTAWAY_MAX_KEY_LENGTH` of them.
PUTAWAY_KEY_PREFIX = "putaway:"
PUTAWAY_MAX_KEY_LENGTH = 128 - len(PUTAWAY_KEY_PREFIX)
#: Namespace of a put-away move's deterministic `reference_id` (a uuid5 of the client's key).
PUTAWAY_NAMESPACE = uuid.UUID("5f0d2f3a-8f0c-4a63-9b8e-7a1f6c2d9e41")
#: The `PutawayOut.warnings` entry for a `suggested_location_id` that is unknown, deleted or another
#: tenant's: the move stands, the id is not stored, and the text is the same for every cause.
SUGGESTION_IGNORED_WARNING = "suggestion_ignored"

log = get_logger("operations.inventory.putaway")


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
    fresh_buckets_only: bool = False,
) -> PostedMove:
    """Bring existing stock onto the books: one `opening` move, one line per
    (item, location, status, lot, roll). Quantities are parsed against each item's own UOM.

    Opening stock is a way to add stock with no reason or evidence, so it honours the adjustment
    approval threshold: a line that reaches it is refused (use `POST .../adjustments`).
    `fresh_buckets_only` also refuses a bucket that already has ledger history — adding to stock
    that exists is an adjustment, not an opening. Both apply only once the organization has set a
    threshold: without one there is no control to bypass."""
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
    from app.movements import common as movement_common  # movements imports this module
    from app.movements import domain as movement_domain

    settings = await movement_common.get_settings(session, ctx.organization_id)
    threshold = settings.adjustment_approval_threshold_milli if settings else None
    if movement_domain.over_threshold(
        threshold,
        [movement_domain.to_base_milli(ln.quantity_milli, items[ln.item_id].uom) for ln in lines],
    ):
        raise ConflictProblem(
            "Opening stock this size needs approval: use POST /operations/inventory/adjustments "
            "with a reason and the evidence document"
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
    if fresh_buckets_only and threshold is not None:
        for ln in lines:
            used = (
                await session.execute(
                    select(LedgerEntry.id)
                    .where(
                        LedgerEntry.organization_id == ctx.organization_id,
                        LedgerEntry.inventory_item_id == ln.item_id,
                        LedgerEntry.location_id == ln.location_id,
                        LedgerEntry.status == ln.status,
                        LedgerEntry.lot_id.is_not_distinct_from(ln.lot_id),
                        LedgerEntry.roll_id.is_not_distinct_from(ln.roll_id),
                        LedgerEntry.move_id != posted.move.id,
                    )
                    .limit(1)
                )
            ).first()
            if used is not None:
                raise ConflictProblem(
                    "That bucket already has stock history; correct it with an adjustment "
                    "(POST /operations/inventory/adjustments), not opening stock"
                )
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


async def _transfer(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    item: InventoryItem,
    source: Location,
    target: Location,
    milli: int,
    status: str,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
    occurred_on: date,
    note: str,
    idempotency_key: str | None,
    occurred_on_explicit: bool,
    reference_type: str,
    reference_id: uuid.UUID | None,
    request: Request | None,
) -> PostedMove:
    """The ledger half of every bin-to-bin relocation: one `relocation` move (a negative line out
    of `source`, a positive one into `target`, stock conserved) and, for a fresh post of a roll,
    its `MOVED` history row. **Nothing else** — the audit row and the outbox event are the
    caller's to stage, because what the operation *is* (`bin_transfer`, `putaway`) decides which
    ones. `reference_type` and `reference_id` are part of the move's idempotency hash."""
    posted = await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            MoveLine(
                item.id,
                source.id,
                -milli,
                status=status,
                lot_id=lot_id,
                roll_id=roll_id,
            ),
            MoveLine(
                item.id,
                target.id,
                milli,
                status=status,
                lot_id=lot_id,
                roll_id=roll_id,
            ),
        ],
        occurred_on=occurred_on,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    if posted.replayed:
        return posted
    if roll_id is not None:
        session.add(
            RollEvent(
                organization_id=ctx.organization_id,
                roll_id=roll_id,
                kind="MOVED",
                move_id=posted.move.id,
                quantity_milli=milli,
                detail={
                    "from_location_id": str(source.id),
                    "to_location_id": str(target.id),
                    "status": status,
                },
                created_by=str(ctx.principal.sub),
            )
        )
    return posted


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
    posted = await _transfer(
        session,
        ctx,
        item=item,
        source=source,
        target=target,
        milli=milli,
        status=payload.status,
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        occurred_on=payload.occurred_on,
        note=payload.note,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        reference_type="bin_transfer",
        reference_id=None,
        request=request,
    )
    if posted.replayed:
        return posted
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


# ------------------------------------------------------------------ storage put-away


@dataclass(slots=True)
class PutawayResult:
    """What a put-away did, for the route to turn into `PutawayOut`. Holds no coordinates."""

    posted: PostedMove
    source: Location
    target: Location
    matched_suggestion: bool | None
    geo_recorded: bool
    warnings: list[str]


def _putaway_refusal(field: str, code: str, message: str, detail: str) -> ValidationProblem:
    return ValidationProblem(detail, errors=[{"field": field, "code": code, "message": message}])


async def putaway_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: PutawayIn,
    *,
    idempotency_key: str,
    occurred_on_explicit: bool,
    request: Request | None = None,
) -> PutawayResult:
    """Store stock from a receiving or staging bin into the bin whose barcode the worker scanned,
    and keep the proof — who, the bin they scanned, the bin the server would have suggested and,
    when the device gave one, where they stood.

    It is a bin-to-bin `relocation` like `transfer_stock` (the same `_transfer`, the same kernel,
    the same refusal of reserved stock), told apart by `reference_type="putaway"`, and it stages
    `operations.inventory.putaway.recorded` **instead of** `stock.transferred`: the audit row and
    the event carry no coordinates, only whether a fix was kept (`has_location`). The kernel still
    stages its own `inventory_move.posted` audit row and `stock.posted` event, as for any move.

    **The bin is the location of record; the GPS fix never decides where stock is** and never
    blocks the move. It is validated here, before anything posts (a CHECK failing after the post
    would roll the stock move back); a bad one is dropped with a warning; and the capture row is
    written in a savepoint, so even failing to write it leaves the move standing.

    `suggested_location_id` is the device's note of what it showed, not a reference the move
    depends on: one that is unknown, deleted or another tenant's is ignored (stored NULL, warning
    `suggestion_ignored`), never a 404 — a put-away queued offline must not park over a bin that
    has since been deleted. `matched_suggestion` is still the server's own verdict.

    Idempotency: the key is namespaced (`putaway:<key>`) and the move's `reference_id` is a uuid5
    of it — deterministic, because the reference is part of the request hash. The geo,
    `captured_at` and `suggested_location_id` are not in the hash: a retry that differs only in
    those replays the original and writes nothing."""
    org_id = ctx.organization_id
    items = await load_items(session, org_id, [payload.inventory_item_id])
    item = items[payload.inventory_item_id]
    source = await inv_selectors.get_location(session, org_id, payload.from_location_id)
    if source is None:
        raise NotFoundProblem(f"Location {payload.from_location_id} is not in this org")
    # Resolved here, server-side: a worker holds no `location.read`. A barcode that is unknown,
    # another tenant's, or only ever belonged to a deleted bin is one and the same 404.
    target = await inv_selectors.get_location_by_barcode(session, org_id, payload.to_barcode)
    if target is None:
        raise NotFoundProblem("That barcode does not resolve to a location in this organization")

    if source.id == target.id:
        raise _putaway_refusal(
            "to_barcode",
            "same_location",
            "the scanned bin is the bin the stock is already in",
            "The scanned bin is the bin the stock is already in",
        )
    if source.kind not in domain.PUTAWAY_SOURCE_KINDS:
        raise _putaway_refusal(
            "from_location_id",
            "source_not_receiving_or_staging",
            "storage put-away starts from a receiving or staging bin",
            "Storage put-away takes stock from a receiving or staging bin; use a bin transfer "
            "to move stock between storage bins",
        )
    if target.kind not in domain.STORABLE_KINDS:
        raise _putaway_refusal(
            "to_barcode",
            "destination_not_storable",
            "stock is stored in a bin or on a shelf",
            f"Stock is put away into a bin or a shelf, not a {target.kind}",
        )
    if source.warehouse_id != target.warehouse_id:
        raise _putaway_refusal(
            "to_barcode",
            "different_warehouse",
            "not in the source warehouse",
            "A put-away stays inside one warehouse; moving stock between warehouses needs an "
            "inter-warehouse transfer",
        )
    check_tracking(item, payload.lot_id, payload.roll_id)
    milli = parse_positive(payload.quantity, item.uom, "quantity")

    # Everything the proof needs is settled before the move posts.
    warnings: list[str] = []
    # `suggested_location_id` is only what the device *showed* — never a reason to refuse a move
    # that is otherwise valid (a put-away queued offline may name a bin deleted since). An id that
    # is unknown, soft-deleted or another tenant's is ignored, stored as NULL, with one and the same
    # warning for all three: the response must not say whether the id exists elsewhere.
    suggested: Location | None = None
    if payload.suggested_location_id is not None:
        suggested = await inv_selectors.get_location(session, org_id, payload.suggested_location_id)
        if suggested is None:
            warnings.append(SUGGESTION_IGNORED_WARNING)
    fix, dropped = domain.validate_geo(payload.lat_micro, payload.lng_micro, payload.accuracy_m)
    if dropped is not None:
        warnings.append(f"geo_dropped:{dropped}")
    captured_at, adjusted = domain.resolve_captured_at(payload.captured_at, datetime.now(UTC))
    if adjusted:
        warnings.append("captured_at_adjusted")
    # What the server would have suggested, judged against the state *before* this move empties
    # or fills a bin.
    suggestion = await putaway_selectors.suggest(
        session, org_id, warehouse_id=target.warehouse_id, item_id=item.id, lot_id=payload.lot_id
    )
    matched = None if suggestion is None else suggestion.location_id == target.id

    posted = await _transfer(
        session,
        ctx,
        item=item,
        source=source,
        target=target,
        milli=milli,
        status="AVAILABLE",
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        occurred_on=payload.occurred_on,
        note=payload.note,
        idempotency_key=f"{PUTAWAY_KEY_PREFIX}{idempotency_key}",
        occurred_on_explicit=occurred_on_explicit,
        reference_type=PUTAWAY_REFERENCE_TYPE,
        reference_id=uuid.uuid5(PUTAWAY_NAMESPACE, idempotency_key),
        request=request,
    )
    if posted.replayed:
        capture = await putaway_selectors.get_capture(session, org_id, posted.move.id)
        log.info("putaway.replayed", move_id=str(posted.move.id))
        return PutawayResult(
            posted=posted,
            source=source,
            target=target,
            matched_suggestion=capture.matched_suggestion if capture else None,
            geo_recorded=capture is not None and capture.lat_micro is not None,
            warnings=[],
        )

    if dropped is not None:
        log.warning("putaway.geo_dropped", move_id=str(posted.move.id), reason=dropped)
    worker_id = await putaway_selectors.worker_id_for_subject(
        session, org_id, ctx.principal.subject_ref
    )
    capture_written = False
    # Flush what the move staged *first*: a savepoint that rolled back over a flush of its own
    # would otherwise take the roll's `MOVED` row (still pending) down with the capture.
    await session.flush()
    try:
        async with session.begin_nested():
            session.add(
                PutawayCapture(
                    organization_id=org_id,
                    move_id=posted.move.id,
                    location_id=target.id,
                    suggested_location_id=suggested.id if suggested else None,
                    worker_id=worker_id,
                    created_by=ctx.principal.sub,
                    lat_micro=fix.lat_micro if fix else None,
                    lng_micro=fix.lng_micro if fix else None,
                    accuracy_m=fix.accuracy_m if fix else None,
                    captured_at=captured_at,
                    matched_suggestion=matched,
                )
            )
            await session.flush()
        capture_written = True
    except SQLAlchemyError as exc:
        # The class name only: a database error's text can quote the failing row, coordinates
        # included. The stock move stands; the response says the proof was not kept.
        log.error("putaway.capture_failed", move_id=str(posted.move.id), error=type(exc).__name__)
        warnings.append("geo_not_recorded")
    geo_recorded = capture_written and fix is not None

    detail = {
        "move_id": str(posted.move.id),
        "inventory_item_id": str(item.id),
        "from_location_id": str(source.id),
        "to_location_id": str(target.id),
        "status": "AVAILABLE",
        "lot_id": str(payload.lot_id) if payload.lot_id else None,
        "roll_id": str(payload.roll_id) if payload.roll_id else None,
        "quantity": format_qty(milli),
        "matched_suggestion": matched,
        "has_location": geo_recorded,
    }
    stage(
        session,
        ctx,
        action="putaway.recorded",
        event_type=EVT_PUTAWAY_RECORDED,
        entity="inventory_move",
        entity_id=posted.move.id,
        after=detail,
        payload=detail,
        reason=payload.note,
        request=request,
    )
    return PutawayResult(
        posted=posted,
        source=source,
        target=target,
        matched_suggestion=matched,
        geo_recorded=geo_recorded,
        warnings=warnings,
    )


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
    any other outbound line.

    **Direction gate (ADR-0014 §12).** `stock.transfer` may only move stock *into* a hold. Taking
    it *out* of one (REJECTED, DAMAGED, QUARANTINE, QUALITY_HOLD, BLOCKED) into AVAILABLE or WIP
    also needs `stock.adjust` at ORG scope — otherwise a floor worker could launder rejected
    stock back into sellable stock. Checked before anything is read or locked."""
    if domain.is_hold_release(payload.from_status, payload.to_status):
        adjust = ctx.principal.scope_for(RELEASE_PERMISSION)
        if adjust is None or adjust.rank < Scope.ORG.rank:
            raise ForbiddenProblem(
                f"Releasing stock from {payload.from_status} to {payload.to_status} needs "
                f"{RELEASE_PERMISSION} as well as the right to transfer stock"
            )
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
