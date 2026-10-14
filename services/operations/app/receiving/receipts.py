"""Posting a goods receipt (inventory Phase 2) — the only way a purchase order's goods reach stock.

**One transaction, one lock order.** The purchase order row is locked first, then its lines
(by id), then the stocked items (by id); the ledger kernel then takes the bucket locks in its own
fixed order. Two receipts against one PO therefore *serialise on the PO*: the second one reads the
first one's cumulative figure, so two deliveries can never both squeeze under the tolerance and
together exceed it. Nothing here writes a quantity — the stock is one `receipt` move through
`app.inventory.ledger.post_move`, the kernel's only writer (ADR-0014 §3).

**The decision, per line** (`app.receiving.domain`): the quantity entered is converted to the
item's stock unit exactly (a box of 12 is 12 pieces, via `uom_conversions`); the cumulative
received may not exceed `ordered + floor(ordered * tolerance_bp / 10_000)`; below the ordered
quantity it is a `PARTIAL` receipt (more expected) or, with `close_short`, a `SHORT` one (the line
stops holding the order open); at it, `EXACT`; above it within tolerance, `OVER`. The purchase
order becomes `partially_received` or `received` accordingly, along `PO_TRANSITIONS`.

**Tracking.** A lot-tracked item's stock goes into a lot (an existing one the caller names, or a
new one — generated number unless given — carrying the supplier's lot number and the dates); a
roll-tracked item's delivery is described roll by roll (primary quantity in the stock unit, the
measured secondary quantity, width/GSM/shade/grade) and the rolls must add up to the line's
quantity exactly. An item that tracks quality — or a line sent with `hold` — is received into
`QUALITY_HOLD`, not `AVAILABLE`.

**Idempotency.** The receipt header carries the client's `Idempotency-Key` under a unique index, and
is inserted (`ON CONFLICT DO NOTHING`) before anything else is written, so two simultaneous
requests with one key cannot both post: the loser waits for the winner's commit and replays it. The
same key with a different body is a 409. Lots and rolls are created only after that point.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain as po_domain
from app import services as po_services
from app.inventory import ledger
from app.inventory import tracking_selectors as track_sel
from app.inventory import tracking_services as tracking
from app.inventory.ledger import MoveLine, PostedMove
from app.inventory.quantity import QuantityError, canonical_uom_code, format_qty, parse_qty
from app.inventory.tracking_schemas import LotIn, RollIn
from app.jobwork import domain as jw_domain
from app.models import (
    AuditLog,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryItem,
    OutboxEvent,
    PurchaseOrder,
    PurchaseOrderLine,
)
from app.receiving import domain, selectors
from app.receiving.schemas import GoodsReceiptIn, ReceiptLineIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import actor_for, publish_event

EVT_STOCK_RECEIVED = "operations.stock.received"

#: A PO in one of these states takes a goods receipt.
RECEIVABLE_PO_STATUSES = ("approved", "partially_received")


def _field_error(index: int, name: str, code: str, message: str) -> ValidationProblem:
    return ValidationProblem(
        message,
        errors=[{"field": f"lines[{index}].{name}", "code": code, "message": message}],
    )


def request_hash(payload: GoodsReceiptIn) -> str:
    """A stable digest of what the caller asked for — compared when a key is reused."""
    data = payload.model_dump(mode="json")
    if "received_on" not in payload.model_fields_set:
        # A server-side default ("today") is not something the client asked for: a legitimate
        # retry that lands on the other side of midnight is still the same request.
        data.pop("received_on")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class _Line:
    """One receipt line, fully decided before anything is written."""

    index: int
    spec: ReceiptLineIn
    po_line: PurchaseOrderLine
    item: InventoryItem | None
    stock_uom: str
    ordered: int
    tolerance_bp: int
    entered_milli: int
    entered_uom: str
    received: int
    prior: int
    cumulative: int
    outcome: str
    status: str | None
    location_id: uuid.UUID | None
    unit_cost_minor: int | None
    roll_milli: list[int] = field(default_factory=list)
    # Filled in as the line is written:
    lot_id: uuid.UUID | None = None
    supplier_lot_no: str | None = None
    roll_ids: list[uuid.UUID] = field(default_factory=list)


async def _next_receipt_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    """Race-safe `GRN-{yyyy}-{seq:04d}`, serialised per organization like the PO number."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{org_id}:grn_number"},
    )
    prefix = f"GRN-{jw_domain.ist_today().year}-"
    seq = await selectors.next_sequence(session, GoodsReceipt, org_id, prefix)
    return f"{prefix}{seq:04d}"


async def _lock_purchase_order(
    session: AsyncSession, org_id: uuid.UUID, po_id: uuid.UUID
) -> tuple[PurchaseOrder, dict[uuid.UUID, PurchaseOrderLine]]:
    po = (
        await session.execute(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.id == po_id,
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.deleted_at.is_(None),
            )
            .with_for_update()
            # The row may already be in this session's identity map (the route, or a replayed
            # earlier statement, loaded it before the lock was won): re-read it, or the cumulative
            # figures below would be the stale ones the lock exists to protect against.
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if po is None:
        raise NotFoundProblem("That purchase order is not part of this organization")
    lines = (
        (
            await session.execute(
                select(PurchaseOrderLine)
                .where(
                    PurchaseOrderLine.organization_id == org_id,
                    PurchaseOrderLine.purchase_order_id == po.id,
                    PurchaseOrderLine.deleted_at.is_(None),
                )
                .order_by(PurchaseOrderLine.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return po, {ln.id: ln for ln in lines}


async def _lock_items(
    session: AsyncSession, org_id: uuid.UUID, skus: set[str]
) -> dict[str, InventoryItem]:
    rows = (
        (
            await session.execute(
                select(InventoryItem)
                .where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.sku.in_(skus),
                    InventoryItem.deleted_at.is_(None),
                )
                .order_by(InventoryItem.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {i.sku: i for i in rows}


def _check_po_receivable(po: PurchaseOrder) -> None:
    if po.status in RECEIVABLE_PO_STATUSES:
        return
    if po.status == "draft":
        raise ConflictProblem(
            f"Purchase order {po.number} is draft and must be approved before it can be received"
        )
    raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be received")


def _plan_tracking(line: _Line, *, auto_tracking: bool) -> None:
    """The tracking rules for one line that brings stock in: which of lot / rolls / status /
    location it needs, refused (422) when the caller sent what the item does not track or left out
    what it requires."""
    item, spec, i = line.item, line.spec, line.index
    if item is None:
        for name, given in (
            ("lot", spec.lot is not None),
            ("rolls", bool(spec.rolls)),
            ("location_id", spec.location_id is not None),
        ):
            if given:
                raise _field_error(
                    i,
                    name,
                    "not_stocked",
                    f"{line.po_line.sku} is not a stocked item (a service line); it takes no "
                    f"{name.replace('_id', '')}",
                )
        return

    if line.received == 0:
        return

    if not item.track_lot and spec.lot is not None:
        raise _field_error(
            i,
            "lot",
            "not_tracked",
            f"{item.sku} is not tracked by lot; enable lot tracking on the item first",
        )
    new_lot = item.track_lot and (spec.lot is None or spec.lot.lot_id is None)
    if (
        new_lot
        and item.track_expiry
        and not auto_tracking
        and (spec.lot is None or spec.lot.expires_on is None)
    ):
        raise _field_error(
            i, "lot.expires_on", "required", f"{item.sku} tracks expiry; give the lot's expires_on"
        )

    if not item.track_roll and spec.rolls:
        raise _field_error(
            i,
            "rolls",
            "not_tracked",
            f"{item.sku} is not tracked by roll; enable roll tracking on the item first",
        )
    if item.track_roll:
        if spec.rolls:
            try:
                milli = [parse_qty(r.primary_qty, line.stock_uom) for r in spec.rolls]
            except QuantityError as exc:
                raise _field_error(i, "rolls", "invalid_quantity", str(exc)) from exc
            if any(m <= 0 for m in milli):
                raise _field_error(i, "rolls", "invalid_quantity", "A roll's length must be > 0")
            if sum(milli) != line.received:
                raise _field_error(
                    i,
                    "rolls",
                    "rolls_do_not_add_up",
                    f"The rolls add up to {format_qty(sum(milli))} {line.stock_uom} but the line "
                    f"receives {format_qty(line.received)} {line.stock_uom}",
                )
            line.roll_milli = milli
        elif auto_tracking:
            line.roll_milli = [line.received]
        else:
            raise _field_error(
                i,
                "rolls",
                "required",
                f"{item.sku} is tracked by roll; describe each roll of this delivery",
            )


async def post_goods_receipt(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: GoodsReceiptIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
    auto_tracking: bool = False,
) -> tuple[GoodsReceipt, PostedMove | None, bool, dict[uuid.UUID, uuid.UUID]]:
    """Post one delivery. Returns `(receipt, move, replayed, entry_by_line)` — `move` is `None`
    for a replay or when no line brought stock in; `entry_by_line` maps a receipt line's PO-line id
    to its first ledger entry (the legacy event wants one).

    `auto_tracking` is for the legacy whole-order wrapper only: a roll-tracked item with no roll
    breakdown gets one roll of the whole quantity instead of a 422.
    """
    org_id = ctx.organization_id
    digest = request_hash(payload)
    if idempotency_key is not None:
        existing = await selectors.get_receipt_by_key(session, org_id, idempotency_key)
        if existing is not None:
            return _replayed(existing, digest)

    po, po_lines = await _lock_purchase_order(session, org_id, payload.purchase_order_id)
    if idempotency_key is not None:
        # The same key may have been posting while this request waited for the PO lock. Look
        # again now that the lock is ours (the winner has committed): the answer is its replay,
        # not "the order is already received" or "over the limit" that the winner's own receipt
        # may have just made true.
        existing = await selectors.get_receipt_by_key(session, org_id, idempotency_key)
        if existing is not None:
            return _replayed(existing, digest)
    _check_po_receivable(po)
    if payload.received_on < po.created_date:
        raise ValidationProblem(
            f"received_on {payload.received_on} is before {po.number} was raised on "
            f"{po.created_date}"
        )
    items = await _lock_items(session, org_id, {ln.sku for ln in po_lines.values()})
    ratios = await selectors.conversions_for_items(session, org_id, [i.id for i in items.values()])

    # ---- decide every line (pure reads + arithmetic; nothing written yet) ----
    cumulative: dict[uuid.UUID, int] = {pid: ln.received_milli for pid, ln in po_lines.items()}
    closed: dict[uuid.UUID, bool] = {pid: ln.short_closed for pid, ln in po_lines.items()}
    plan: list[_Line] = []
    for index, spec in enumerate(payload.lines):
        po_line = po_lines.get(spec.purchase_order_line_id)
        if po_line is None:
            raise NotFoundProblem(
                f"Purchase order line {spec.purchase_order_line_id} is not on {po.number}"
            )
        item = items.get(po_line.sku)
        stock_uom = item.uom if item else po_line.uom
        item_ratios = ratios.get(item.id, {}) if item else {}
        ordered = selectors.ordered_milli(po_line, stock_uom, item_ratios)
        entered_uom = canonical_uom_code(spec.uom) if spec.uom else canonical_uom_code(stock_uom)
        try:
            entered = parse_qty(spec.quantity, entered_uom)
            received = (
                domain.to_stock_milli(entered, entered_uom, stock_uom, item_ratios)
                if stock_uom
                else entered
            )
        except QuantityError as exc:
            raise _field_error(index, "quantity", "invalid_quantity", str(exc)) from exc

        if closed[po_line.id] and (received > 0 or spec.close_short):
            raise ConflictProblem(
                f"Line {po_line.sku} of {po.number} was closed short and takes no further receipt"
            )
        tolerance_bp = item.receipt_tolerance_bp if item else 0
        prior = cumulative[po_line.id]
        total = prior + received
        limit = domain.tolerance_limit_milli(ordered, tolerance_bp)
        if total > limit:
            raise _field_error(
                index,
                "quantity",
                "over_receipt",
                f"{po_line.sku}: {format_qty(received)} {stock_uom} on top of "
                f"{format_qty(prior)} already received against {format_qty(ordered)} ordered is "
                f"over the limit of {format_qty(limit)} "
                f"({domain.format_tolerance_pct(tolerance_bp)}% tolerance)",
            )
        if spec.close_short and total >= ordered:
            raise _field_error(
                index,
                "close_short",
                "already_complete",
                f"{po_line.sku} is already fully received; there is nothing to close short",
            )
        outcome = domain.classify_outcome(
            ordered_milli=ordered, cumulative_milli=total, close_short=spec.close_short
        )
        status: str | None = None
        location_id: uuid.UUID | None = None
        cost: int | None = None
        if item is not None and received > 0:
            status = "QUALITY_HOLD" if (item.track_quality or spec.hold) else "AVAILABLE"
            location_id = spec.location_id or item.home_location_id
            if location_id is None:
                raise _field_error(
                    index,
                    "location_id",
                    "required",
                    f"{item.sku} has no home location; say where this stock goes",
                )
            cost = domain.unit_cost_per_stock_unit(
                po_line.unit_price_minor, po_line.uom or stock_uom, stock_uom, item_ratios
            )
        line = _Line(
            index=index,
            spec=spec,
            po_line=po_line,
            item=item,
            stock_uom=stock_uom,
            ordered=ordered,
            tolerance_bp=tolerance_bp,
            entered_milli=entered,
            entered_uom=entered_uom,
            received=received,
            prior=prior,
            cumulative=total,
            outcome=outcome,
            status=status,
            location_id=location_id,
            unit_cost_minor=cost,
        )
        _plan_tracking(line, auto_tracking=auto_tracking)
        cumulative[po_line.id] = total
        closed[po_line.id] = closed[po_line.id] or spec.close_short
        plan.append(line)

    # ---- the purchase order after this receipt ----
    states = [
        domain.LineState(
            ordered_milli=selectors.ordered_milli(
                ln,
                items[ln.sku].uom if ln.sku in items else ln.uom,
                ratios.get(items[ln.sku].id, {}) if ln.sku in items else {},
            ),
            received_milli=cumulative[pid],
            short_closed=closed[pid],
        )
        for pid, ln in po_lines.items()
    ]
    new_status = domain.po_status_after(po.status, states)
    if new_status != po.status and not po_domain.can_transition_po(po.status, new_status):
        raise ConflictProblem(
            f"Purchase order {po.number} cannot move from {po.status} to {new_status}"
        )

    # ---- the header, ahead of every write: this is the idempotency arbiter ----
    receipt_id = uuid.uuid4()
    number = await _next_receipt_number(session, org_id)
    header = pg_insert(GoodsReceipt).values(
        id=receipt_id,
        organization_id=org_id,
        number=number,
        purchase_order_id=po.id,
        supplier_id=po.supplier_id,
        received_on=payload.received_on,
        delivery_note=payload.delivery_note,
        note=payload.note,
        status="POSTED",
        idempotency_key=idempotency_key,
        request_hash=digest if idempotency_key else None,
        created_by=str(ctx.principal.sub),
    )
    if idempotency_key is not None:
        header = header.on_conflict_do_nothing(
            index_elements=["organization_id", "idempotency_key"],
            index_where=GoodsReceipt.idempotency_key.is_not(None),
        )
    inserted = (await session.execute(header.returning(GoodsReceipt.id))).scalar_one_or_none()
    if inserted is None:
        existing = await selectors.get_receipt_by_key(session, org_id, idempotency_key or "")
        assert existing is not None
        return _replayed(existing, digest)
    receipt = await selectors.get_receipt(session, org_id, receipt_id)
    assert receipt is not None

    # ---- lots and rolls ----
    for line in plan:
        if line.item is None or line.received == 0:
            continue
        await _create_tracking(session, ctx, line, receipt, request=request)

    # ---- the stock: one receipt move ----
    move_lines = _move_lines(plan, po.currency)
    posted: PostedMove | None = None
    entry_for_bucket: dict[tuple[Any, ...], uuid.UUID] = {}
    if move_lines:
        posted = await ledger.post_move(
            session,
            ctx,
            kind="receipt",
            lines=move_lines,
            occurred_on=payload.received_on,
            reference_type="goods_receipt",
            reference_id=receipt_id,
            note=payload.delivery_note or "",
            idempotency_key=None,
            request=request,
        )
        for entry in posted.entries:
            entry_for_bucket[
                (
                    entry.inventory_item_id,
                    entry.location_id,
                    entry.status,
                    entry.lot_id,
                    entry.roll_id,
                )
            ] = entry.id
        for line in plan:
            if line.item is not None and line.received > 0:
                line.item.stocked_at = payload.received_on

    # ---- the receipt lines, the PO lines and the PO ----
    entry_by_line: dict[uuid.UUID, uuid.UUID] = {}
    for line in plan:
        session.add(
            GoodsReceiptLine(
                organization_id=org_id,
                goods_receipt_id=receipt_id,
                line_no=line.index + 1,
                purchase_order_line_id=line.po_line.id,
                inventory_item_id=line.item.id if line.item else None,
                location_id=line.location_id,
                stock_status=line.status,
                lot_id=line.lot_id,
                supplier_lot_no=line.supplier_lot_no,
                roll_ids=[str(r) for r in line.roll_ids],
                entered_milli=line.entered_milli,
                entered_uom=line.entered_uom,
                received_milli=line.received,
                ordered_milli=line.ordered,
                prior_received_milli=line.prior,
                cumulative_milli=line.cumulative,
                tolerance_bp=line.tolerance_bp,
                close_short=line.spec.close_short,
                outcome=line.outcome,
                unit_cost_minor=line.unit_cost_minor if line.received > 0 else None,
                unit_cost_currency=po.currency if line.unit_cost_minor is not None else None,
            )
        )
        if line.item is not None and line.received > 0:
            first_roll = line.roll_ids[0] if line.roll_ids else None
            entry_id = entry_for_bucket.get(
                (line.item.id, line.location_id, line.status, line.lot_id, first_roll)
            )
            if entry_id is not None:
                entry_by_line.setdefault(line.po_line.id, entry_id)
    for pid, po_line in po_lines.items():
        if cumulative[pid] != po_line.received_milli or closed[pid] != po_line.short_closed:
            po_line.received_milli = cumulative[pid]
            po_line.received_qty = cumulative[pid] // 1000
            po_line.short_closed = closed[pid]
            po_line.updated_by = str(ctx.principal.sub)
    if new_status != po.status:
        po.status = new_status
        if new_status == "received":
            po.received_date = payload.received_on
    po.updated_by = str(ctx.principal.sub)
    await session.flush()
    if new_status == "received":
        # Receiving closes out the supplier's open-PO count — once, on the transition.
        await po_services._bump_supplier(session, org_id, po.supplier_id, open_pos=-1)

    _stage(session, ctx, receipt, po, plan, posted, request=request)
    if new_status == "received":
        _emit_po_received(
            session,
            ctx,
            po,
            landed=payload.received_on,
            unmatched=sorted(ln.sku for ln in po_lines.values() if ln.sku not in items),
            matched=[
                {
                    "sku": line.po_line.sku,
                    "movement_id": str(entry_by_line[line.po_line.id]),
                    "quantity": line.po_line.received_milli // 1000,
                    "quantity_exact": format_qty(line.po_line.received_milli),
                }
                for line in plan
                if line.po_line.id in entry_by_line
            ],
            request=request,
        )
    return receipt, posted, False, entry_by_line


def _emit_po_received(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    landed: date,
    unmatched: list[str],
    matched: list[dict[str, Any]],
    request: Request | None,
) -> None:
    """The legacy `purchase_order.received` audit row and event, now emitted by whichever path
    receives the *last* line — the single-shot endpoint, or a goods receipt that completes the
    order. `matched_lines` are the lines of the completing receipt."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.received",
        entity="purchase_order",
        entity_id=po.id,
        after={
            "number": po.number,
            "status": "received",
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=po_services.EVT_PO_RECEIVED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched,
            "matched_lines": matched,
        },
        actor=actor_for(ctx),
    )


def _replayed(
    existing: GoodsReceipt, digest: str
) -> tuple[GoodsReceipt, PostedMove | None, bool, dict[uuid.UUID, uuid.UUID]]:
    if existing.request_hash != digest:
        raise ConflictProblem(
            "That Idempotency-Key was already used for a different goods receipt",
            errors=[
                {
                    "field": "Idempotency-Key",
                    "code": "idempotency_key_reused",
                    "message": "same key, different request",
                }
            ],
        )
    return existing, None, True, {}


async def _create_tracking(
    session: AsyncSession,
    ctx: ScopeContext,
    line: _Line,
    receipt: GoodsReceipt,
    *,
    request: Request | None,
) -> None:
    """Create (or resolve) the lot and the rolls of one line that brings stock in."""
    item = line.item
    assert item is not None
    org_id = ctx.organization_id
    held = line.status != "AVAILABLE"
    spec_lot = line.spec.lot
    if item.track_lot:
        if spec_lot is not None and spec_lot.lot_id is not None:
            lot = await track_sel.get_lot(session, org_id, spec_lot.lot_id)
            if lot is None:
                raise NotFoundProblem(f"Lot {spec_lot.lot_id} is not in this org")
            if lot.inventory_item_id != item.id:
                raise _field_error(
                    line.index, "lot.lot_id", "wrong_item", "That lot belongs to a different item"
                )
            line.lot_id = lot.id
            line.supplier_lot_no = lot.supplier_lot_no
        else:
            created = await tracking.create_lot(
                session,
                ctx,
                LotIn(
                    inventory_item_id=item.id,
                    lot_no=spec_lot.lot_no if spec_lot else None,
                    supplier_lot_no=spec_lot.supplier_lot_no if spec_lot else None,
                    manufactured_on=spec_lot.manufactured_on if spec_lot else None,
                    received_on=receipt.received_on,
                    expires_on=spec_lot.expires_on if spec_lot else None,
                    quality_status="PENDING" if held else "APPROVED",
                    source_type="goods_receipt",
                    source_id=receipt.id,
                ),
                request=request,
            )
            line.lot_id = created.id
            line.supplier_lot_no = spec_lot.supplier_lot_no if spec_lot else None
    if item.track_roll:
        specs = line.spec.rolls
        for position, milli in enumerate(line.roll_milli):
            roll_spec = specs[position] if specs else None
            roll = await tracking.create_roll(
                session,
                ctx,
                RollIn(
                    inventory_item_id=item.id,
                    lot_id=line.lot_id,
                    roll_no=roll_spec.roll_no if roll_spec else None,
                    primary_qty=format_qty(milli),
                    secondary_qty=roll_spec.secondary_qty if roll_spec else None,
                    secondary_uom=roll_spec.secondary_uom if roll_spec else None,
                    width_mm=roll_spec.width_mm if roll_spec else None,
                    gsm=roll_spec.gsm if roll_spec else None,
                    shade=roll_spec.shade if roll_spec else None,
                    grade=roll_spec.grade if roll_spec else None,
                ),
                request=request,
            )
            line.roll_ids.append(roll.id)


def _move_lines(plan: list[_Line], currency: str) -> list[MoveLine]:
    """The receipt move's lines: one per roll, or one per (item, location, status, lot). The kernel
    allows each bucket once per move, so two receipt lines landing in the same bucket are merged —
    quantities add, and the cost is the quantity-weighted mean (half-to-even)."""
    merged: dict[tuple[Any, ...], list[tuple[int, int | None]]] = {}
    for line in plan:
        if line.item is None or line.received == 0:
            continue
        assert line.location_id is not None and line.status is not None
        parts: list[tuple[uuid.UUID | None, int]]
        if line.roll_ids:
            parts = [(rid, m) for rid, m in zip(line.roll_ids, line.roll_milli, strict=True)]
        else:
            parts = [(None, line.received)]
        for roll_id, milli in parts:
            key = (line.item.id, line.location_id, line.status, line.lot_id, roll_id)
            merged.setdefault(key, []).append((milli, line.unit_cost_minor))
    out: list[MoveLine] = []
    for (item_id, location_id, status, lot_id, roll_id), pieces in merged.items():
        total = sum(q for q, _ in pieces)
        costs = [(q, c) for q, c in pieces if c is not None]
        cost = domain.weighted_unit_cost(costs) if costs else None
        out.append(
            MoveLine(
                item_id=item_id,
                location_id=location_id,
                quantity_milli=total,
                status=status,
                lot_id=lot_id,
                roll_id=roll_id,
                unit_cost_minor=cost,
                unit_cost_currency=currency if cost is not None else None,
            )
        )
    return out


def _stage(
    session: AsyncSession,
    ctx: ScopeContext,
    receipt: GoodsReceipt,
    po: PurchaseOrder,
    plan: list[_Line],
    posted: PostedMove | None,
    *,
    request: Request | None,
) -> None:
    """The audit row and the outbox event(s) for a freshly posted receipt (never for a replay)."""
    org_id = ctx.organization_id
    lines = [
        {
            "purchase_order_line_id": str(ln.po_line.id),
            "sku": ln.po_line.sku,
            "inventory_item_id": str(ln.item.id) if ln.item else None,
            "location_id": str(ln.location_id) if ln.location_id else None,
            "status": ln.status,
            # Snapshot: the quality hand-off acts on what was true at receipt, not at consumption.
            "track_quality": bool(ln.item and ln.item.track_quality),
            "lot_id": str(ln.lot_id) if ln.lot_id else None,
            "roll_ids": [str(r) for r in ln.roll_ids],
            "received_qty": format_qty(ln.received),
            "cumulative_qty": format_qty(ln.cumulative),
            "ordered_qty": format_qty(ln.ordered),
            "outcome": ln.outcome,
            "close_short": ln.spec.close_short,
        }
        for ln in plan
    ]
    summary = {
        "number": receipt.number,
        "purchase_order_id": str(po.id),
        "po_number": po.number,
        "po_status": po.status,
        "move_id": str(posted.move.id) if posted else None,
        "lines": lines,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="goods_receipt.posted",
        entity="goods_receipt",
        entity_id=receipt.id,
        after=summary,
        reason=receipt.note,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_STOCK_RECEIVED,
        aggregate_type="goods_receipt",
        aggregate_id=receipt.id,
        payload={
            "organization_id": str(org_id),
            "goods_receipt_id": str(receipt.id),
            "supplier_id": str(receipt.supplier_id),
            "received_on": receipt.received_on.isoformat(),
            **summary,
        },
        actor=actor_for(ctx),
    )


# ------------------------------------------------------------------ the legacy whole-order receipt


async def receive_whole_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    received_date: date | None,
    request: Request | None,
    unmatched_out: list[str] | None,
) -> PurchaseOrder:
    """`POST /purchase-orders/{id}/receive`, kept as a thin wrapper over the goods-receipt path:
    receive **every line's outstanding quantity** in one receipt. A line with no stocked item posts
    no stock and is listed as unmatched; a lot- or roll-tracked item gets a generated lot (and one
    roll of the whole quantity). It stays the same endpoint with the same answers — approved is a
    prerequisite, a draft is refused with the same message — and still emits the legacy
    `purchase_order.received` event."""
    _check_po_receivable(po)
    po, po_lines = await _lock_purchase_order(session, ctx.organization_id, po.id)
    _check_po_receivable(po)
    items = await _lock_items(session, ctx.organization_id, {ln.sku for ln in po_lines.values()})
    ratios = await selectors.conversions_for_items(
        session, ctx.organization_id, [i.id for i in items.values()]
    )
    landed = received_date or jw_domain.ist_today()

    specs: list[ReceiptLineIn] = []
    unmatched: list[str] = []
    for pid, po_line in sorted(po_lines.items(), key=lambda kv: (kv[1].created_at, kv[0])):
        item = items.get(po_line.sku)
        if item is None:
            unmatched.append(po_line.sku)
        stock_uom = item.uom if item else po_line.uom
        ordered = selectors.ordered_milli(
            po_line, stock_uom, ratios.get(item.id, {}) if item else {}
        )
        outstanding = ordered - po_line.received_milli
        if po_line.short_closed or outstanding <= 0:
            continue
        specs.append(ReceiptLineIn(purchase_order_line_id=pid, quantity=format_qty(outstanding)))

    if specs:
        await post_goods_receipt(
            session,
            ctx,
            GoodsReceiptIn(purchase_order_id=po.id, received_on=landed, lines=specs),
            idempotency_key=None,
            request=request,
            auto_tracking=True,
        )
    else:
        # Nothing outstanding anywhere (every line already received or closed short): the order
        # simply completes, as it always did.
        po.status = "received"
        po.received_date = landed
        po.updated_by = str(ctx.principal.sub)
        await session.flush()
        await po_services._bump_supplier(session, ctx.organization_id, po.supplier_id, open_pos=-1)
        _emit_po_received(
            session, ctx, po, landed=landed, unmatched=unmatched, matched=[], request=request
        )
    if unmatched_out is not None:
        unmatched_out.extend(unmatched)
    return po
