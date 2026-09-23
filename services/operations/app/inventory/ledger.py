"""The stock posting kernel — the **only** code that writes the ledger, the balances or the
reservations (ADR-0014 §3).

`post_move` is one transaction's worth of stock change: a header row (`inventory_moves`), one
append-only ledger row per bucket touched, and each bucket's balance updated under its own row
lock, all in the caller's transaction — `get_tenant_session` opens it and commits it with the
request, so the ledger and the balances either both change or neither does. Its audit row and its
outbox event are staged in the same transaction; the relay publishes the event only after commit.

Concurrency is the database's, not ours:

* **Balances are locked, in a fixed order.** Each line's bucket row is created if missing
  (`INSERT … ON CONFLICT DO NOTHING`) and then taken `FOR UPDATE`, sorted by (item, location,
  status, lot, roll) — so two multi-line moves over the same buckets queue rather than deadlock,
  and every read-check-write on a balance happens under its lock. There is no lost update.
* **Reservations are locked after their balance**, never before, everywhere. The kernel never
  writes an `inventory_items` row; callers that lock an item do so *before* calling in.
* **Idempotency is a unique index.** A keyed post inserts its header with `ON CONFLICT DO
  NOTHING`; a concurrent duplicate blocks on the first transaction's index entry, and once that
  commits it finds the header and replays it instead of posting again. Same key, different
  request: 409.
* The `CHECK`s on `inventory_balances` (no negative quantity, reserved within quantity) are the
  backstop. The checks here come first so the caller gets numbers instead of an IntegrityError.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory.quantity import MAX_ABS_MILLI, describe, format_qty
from app.models import (
    STOCK_AGGREGATES,
    STOCK_STATUSES,
    AuditLog,
    InventoryBalance,
    InventoryItem,
    InventoryMove,
    InventoryReservation,
    InventoryReservationRelease,
    LedgerEntry,
    Location,
    OutboxEvent,
    Warehouse,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

#: What a move can be. `count` is the only kind whose line may be a zero change.
MOVE_KINDS = ("opening", "receipt", "adjustment", "count", "relocation", "issue")

EVT_STOCK_POSTED = "operations.inventory.stock.posted"
EVT_STOCK_RESERVED = "operations.inventory.stock.reserved"
EVT_STOCK_RELEASED = "operations.inventory.stock.released"

_NO_ID = uuid.UUID(int=0)


class InsufficientStock(ConflictProblem):
    """A move or reservation that would take a bucket below zero, or into reserved stock. The
    `errors` entry carries the numbers so a client can say *why*, not just *no*."""

    problem_type = "insufficient-stock"
    title = "Not enough stock"


@dataclass(frozen=True, slots=True)
class MoveLine:
    """One bucket's change inside a move.

    `quantity_milli` is the signed change in milli-units of the item's UOM. A `count` line gives
    `counted_milli` instead — the physical figure — and the kernel computes the change under the
    bucket's lock, so a count can never be computed from a stale read.

    `reservation_id` lets an outbound line draw on a reservation of the same bucket: that much of
    the line comes out of reserved stock (and closes the reservation when it is used up); any
    excess comes out of unreserved available stock.

    `unit_cost_currency` names the ISO currency `unit_cost_minor` is in — the currency the
    receipt was actually priced in (a purchase order's, say), never the item's own `currency` by
    assumption and never converted between the two (ADR-0014 §8, revised). The two travel
    together: one without the other is refused.
    """

    item_id: uuid.UUID
    location_id: uuid.UUID
    quantity_milli: int = 0
    counted_milli: int | None = None
    status: str = "AVAILABLE"
    lot_id: uuid.UUID | None = None
    roll_id: uuid.UUID | None = None
    unit_cost_minor: int | None = None
    unit_cost_currency: str | None = None
    reservation_id: uuid.UUID | None = None

    def bucket(self) -> tuple[str, str, str, str, str]:
        return (
            str(self.item_id),
            str(self.location_id),
            self.status,
            str(self.lot_id or _NO_ID),
            str(self.roll_id or _NO_ID),
        )


@dataclass(slots=True)
class PostedMove:
    move: InventoryMove
    entries: list[LedgerEntry] = field(default_factory=list)
    #: True when an idempotency key matched an earlier post: nothing new was written.
    replayed: bool = False


# ------------------------------------------------------------------ helpers


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_lines(kind: str, lines: Sequence[MoveLine]) -> None:
    if kind not in MOVE_KINDS:
        raise ValidationProblem(f"{kind!r} is not a stock move kind")
    if not lines:
        raise ValidationProblem("A stock move needs at least one line")
    for line in lines:
        if line.status not in STOCK_STATUSES:
            raise ValidationProblem(f"{line.status!r} is not a stock status")
        if line.counted_milli is not None:
            if kind != "count":
                raise ValidationProblem("Only a count line may give a counted quantity")
            if not _is_int(line.counted_milli) or line.counted_milli < 0:
                raise ValidationProblem("A counted quantity is a non-negative integer of milli")
            if line.counted_milli > MAX_ABS_MILLI:
                raise ValidationProblem("That quantity is too large")
            if line.reservation_id is not None:
                raise ValidationProblem("A count cannot draw on a reservation")
            continue
        if not _is_int(line.quantity_milli):
            # A float here is a bug upstream, not a user error: refuse loudly, never round.
            raise ValidationProblem("A quantity must be an integer of milli-units")
        if line.quantity_milli == 0:
            raise ValidationProblem("A stock move line must change the quantity")
        if abs(line.quantity_milli) > MAX_ABS_MILLI:
            raise ValidationProblem("That quantity is too large")
        if line.reservation_id is not None and line.quantity_milli > 0:
            raise ValidationProblem("Only an outbound line can draw on a reservation")
        if line.unit_cost_minor is not None and (
            not _is_int(line.unit_cost_minor) or line.unit_cost_minor < 0
        ):
            raise ValidationProblem("A unit cost is a non-negative integer of minor units")
        if (line.unit_cost_minor is None) != (line.unit_cost_currency is None):
            raise ValidationProblem(
                "A unit cost and its currency must be given together, or not at all"
            )
        if line.unit_cost_currency is not None and (
            len(line.unit_cost_currency) != 3 or not line.unit_cost_currency.isalpha()
        ):
            raise ValidationProblem("A unit cost currency is a 3-letter ISO code")
    if len({line.bucket() for line in lines}) != len(lines):
        raise ValidationProblem("A stock move touches each bucket at most once")


async def _items(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, InventoryItem]:
    wanted = set(ids)
    rows = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.organization_id == org_id,
                InventoryItem.id.in_(wanted),
                InventoryItem.deleted_at.is_(None),
            )
        )
    ).scalars()
    found = {item.id: item for item in rows}
    if missing := wanted - found.keys():
        raise NotFoundProblem(f"Inventory item {sorted(map(str, missing))[0]} is not in this org")
    return found


async def _locations(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Live locations in live warehouses → a `WH-CODE/BIN` label for messages."""
    wanted = set(ids)
    rows = await session.execute(
        select(Location.id, Warehouse.code, Location.code)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(
            Location.organization_id == org_id,
            Location.id.in_(wanted),
            Location.deleted_at.is_(None),
            Warehouse.deleted_at.is_(None),
        )
    )
    found = {loc_id: f"{wh}/{code}" for loc_id, wh, code in rows}
    if missing := wanted - found.keys():
        raise NotFoundProblem(f"Location {sorted(map(str, missing))[0]} is not in this org")
    return found


async def lock_bucket(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    status: str = "AVAILABLE",
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    create: bool = True,
) -> InventoryBalance | None:
    """The bucket's balance row, locked `FOR UPDATE` — created at zero first when `create`.

    `populate_existing` matters: a balance already in the session's identity map would otherwise
    come back with the values it had when first loaded, not the ones the lock just waited for.
    """
    key = {
        "organization_id": org_id,
        "inventory_item_id": item_id,
        "location_id": location_id,
        "status": status,
        "lot_id": lot_id,
        "roll_id": roll_id,
    }
    if create:
        await session.execute(
            pg_insert(InventoryBalance)
            .values(id=uuid.uuid4(), quantity_milli=0, reserved_milli=0, **key)
            .on_conflict_do_nothing(
                index_elements=[
                    "organization_id",
                    "inventory_item_id",
                    "location_id",
                    "status",
                    "lot_id",
                    "roll_id",
                ]
            )
        )
    stmt = (
        select(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.inventory_item_id == item_id,
            InventoryBalance.location_id == location_id,
            InventoryBalance.status == status,
            InventoryBalance.lot_id.is_not_distinct_from(lot_id),
            InventoryBalance.roll_id.is_not_distinct_from(roll_id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _lock_reservation(
    session: AsyncSession, org_id: uuid.UUID, reservation_id: uuid.UUID
) -> InventoryReservation | None:
    stmt = (
        select(InventoryReservation)
        .where(
            InventoryReservation.organization_id == org_id,
            InventoryReservation.id == reservation_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _refresh_items(session: AsyncSession, item_ids: Iterable[uuid.UUID]) -> None:
    """Re-read the stock aggregates of any of these items the session already holds, so a caller
    that renders the item after posting sees the new figures rather than the pre-move ones."""
    wanted = set(item_ids)
    for obj in list(session.identity_map.values()):
        if isinstance(obj, InventoryItem) and obj.id in wanted:
            await session.refresh(obj, list(STOCK_AGGREGATES))


async def _blocking_reservations(
    session: AsyncSession, org_id: uuid.UUID, balance_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The active reservations holding a bucket's `reserved_milli` — named so a shortfall that is
    really "enough stock, but it is promised" can be answered with *which* promises stand in the
    way (ADR-0014 §11), rather than only the number they add up to. Oldest first: the order a
    person would naturally release them in to make room."""
    stmt = (
        select(InventoryReservation)
        .where(
            InventoryReservation.organization_id == org_id,
            InventoryReservation.balance_id == balance_id,
            InventoryReservation.status == "active",
        )
        .order_by(InventoryReservation.created_at, InventoryReservation.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "reservation_id": str(r.id),
            "reference_type": r.reference_type,
            "reference_id": str(r.reference_id) if r.reference_id else None,
            "quantity": format_qty(r.quantity_milli - r.consumed_milli - r.released_milli),
            "quantity_milli": r.quantity_milli - r.consumed_milli - r.released_milli,
        }
        for r in rows
    ]


def _shortfall(
    item: InventoryItem,
    where: str,
    status: str,
    *,
    wanted: int,
    on_hand: int,
    reserved: int,
    reservations: list[dict[str, Any]] | None = None,
) -> InsufficientStock:
    uom = item.uom
    available = on_hand - reserved
    blocked_by_reservations = bool(reservations)
    detail = (
        f"Cannot take {describe(wanted, uom)} of {item.sku} from {where}"
        f"{'' if status == 'AVAILABLE' else f' ({status})'}: "
        f"{describe(on_hand, uom)} on hand, {describe(reserved, uom)} reserved, "
        f"so {describe(max(available, 0), uom)} available"
    )
    if blocked_by_reservations:
        detail += ". Release the reservation(s) blocking it, then try again"
    return InsufficientStock(
        detail,
        errors=[
            {
                "sku": item.sku,
                "location": where,
                "status": status,
                "uom": uom,
                "requested": format_qty(wanted),
                "on_hand": format_qty(on_hand),
                "reserved": format_qty(reserved),
                "available": format_qty(max(available, 0)),
                "reservations": reservations or [],
            }
        ],
    )


# ------------------------------------------------------------------ post_move


async def post_move(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    kind: str,
    lines: Sequence[MoveLine],
    occurred_on: date,
    reason: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    note: str = "",
    idempotency_key: str | None = None,
    occurred_on_explicit: bool = True,
    request: Request | None = None,
) -> PostedMove:
    """Post one stock move. See the module docstring for the guarantees.

    Raises `ValidationProblem` (malformed lines), `NotFoundProblem` (an item or location outside
    the caller's organization — never a hint that it exists elsewhere), `InsufficientStock` (a
    bucket would go negative or into reserved stock), and `ConflictProblem` (an idempotency key
    reused for a different request, or a reservation that does not fit).

    `occurred_on_explicit=False` leaves `occurred_on` out of the idempotency hash: it names a
    caller whose `occurred_on` is a server-side default (e.g. "today", computed fresh on every
    call) rather than something the client sent, so a legitimate retry that lands on the other
    side of midnight is still the same request. `occurred_on` is still stored on the move header
    either way — only whether it counts toward "is this a replay or a different request" changes.
    """
    _validate_lines(kind, lines)
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
        raise ValidationProblem("An idempotency key is 1-128 characters")
    org_id = ctx.organization_id

    items = await _items(session, org_id, (line.item_id for line in lines))
    where = await _locations(session, org_id, (line.location_id for line in lines))

    request_hash = _request_hash(
        {
            "kind": kind,
            "reason": reason,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "note": note,
            **({"occurred_on": occurred_on} if occurred_on_explicit else {}),
            "lines": sorted(
                (
                    *line.bucket(),
                    line.quantity_milli,
                    line.counted_milli,
                    line.unit_cost_minor,
                    line.unit_cost_currency,
                    str(line.reservation_id),
                )
                for line in lines
            ),
        }
    )

    move_id = uuid.uuid4()
    header = pg_insert(InventoryMove).values(
        id=move_id,
        organization_id=org_id,
        kind=kind,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        occurred_on=occurred_on,
        idempotency_key=idempotency_key,
        request_hash=request_hash if idempotency_key else None,
        created_by=str(ctx.principal.sub),
    )
    if idempotency_key is not None:
        header = header.on_conflict_do_nothing(
            index_elements=["organization_id", "idempotency_key"],
            index_where=InventoryMove.idempotency_key.is_not(None),
        )
    inserted = (await session.execute(header.returning(InventoryMove.id))).scalar_one_or_none()
    if inserted is None:
        return await _replay(session, org_id, idempotency_key or "", request_hash)
    move = (
        await session.execute(select(InventoryMove).where(InventoryMove.id == move_id))
    ).scalar_one()

    posted = PostedMove(move=move)
    for line in sorted(lines, key=MoveLine.bucket):
        item = items[line.item_id]
        balance = await lock_bucket(
            session,
            org_id,
            item_id=line.item_id,
            location_id=line.location_id,
            status=line.status,
            lot_id=line.lot_id,
            roll_id=line.roll_id,
        )
        assert balance is not None  # created above if it did not exist
        on_hand, reserved = balance.quantity_milli, balance.reserved_milli
        delta = (
            line.counted_milli - on_hand if line.counted_milli is not None else line.quantity_milli
        )

        drawn = 0
        reservation: InventoryReservation | None = None
        if line.reservation_id is not None:
            reservation = await _lock_reservation(session, org_id, line.reservation_id)
            if reservation is None or reservation.balance_id != balance.id:
                raise ConflictProblem("That reservation is not on this stock")
            if reservation.status != "active":
                raise ConflictProblem(f"That reservation is {reservation.status}")
            drawn = min(
                -delta,
                reservation.quantity_milli
                - reservation.consumed_milli
                - reservation.released_milli,
            )

        new_on_hand = on_hand + delta
        new_reserved = reserved - drawn
        if new_on_hand < 0 or (delta < 0 and new_on_hand < new_reserved):
            # Enough is physically there but reserved stock stands in the way — every outbound
            # line (issue, relocation, a status change's paired move) and a count line are
            # refused identically (ADR-0014 §11): name what is blocking it rather than only the
            # number, so a caller can release the reservation and retry instead of guessing.
            blocking = (
                await _blocking_reservations(session, org_id, balance.id)
                if new_on_hand >= 0
                else []
            )
            raise _shortfall(
                item,
                where[line.location_id],
                line.status,
                wanted=-delta - drawn,
                on_hand=on_hand,
                reserved=reserved,
                reservations=blocking,
            )

        now = datetime.now(UTC)
        balance.quantity_milli = new_on_hand
        balance.reserved_milli = new_reserved
        balance.updated_at = now
        if reservation is not None and drawn:
            reservation.consumed_milli += drawn
            if (
                reservation.consumed_milli + reservation.released_milli
                == reservation.quantity_milli
            ):
                # Fully spoken for — by consumption, by release, or a mix of both. `consumed >
                # 0` here (this branch only runs when `drawn` was non-zero) so "consumed" is
                # always the right terminal status even if a release closed the remainder.
                reservation.status = "consumed"
                reservation.closed_at = now
                reservation.closed_by = str(ctx.principal.sub)

        entry = LedgerEntry(
            organization_id=org_id,
            move_id=move_id,
            inventory_item_id=line.item_id,
            location_id=line.location_id,
            status=line.status,
            lot_id=line.lot_id,
            roll_id=line.roll_id,
            quantity_milli=delta,
            balance_after_milli=new_on_hand,
            unit_cost_minor=line.unit_cost_minor if delta > 0 else None,
            unit_cost_currency=line.unit_cost_currency if delta > 0 else None,
            created_by=str(ctx.principal.sub),
        )
        session.add(entry)
        posted.entries.append(entry)
    # Deliberately no write to `inventory_items` here: that would take the item's row lock
    # *after* the balance locks, the opposite order from the item-level routes (which lock the
    # item first), and two opposite lock orders are a deadlock waiting for load.
    await session.flush()

    summary = [
        {
            "ledger_entry_id": str(e.id),
            "inventory_item_id": str(e.inventory_item_id),
            "sku": items[e.inventory_item_id].sku,
            "uom": items[e.inventory_item_id].uom,
            "location_id": str(e.location_id),
            "location": where[e.location_id],
            "status": e.status,
            "lot_id": str(e.lot_id) if e.lot_id else None,
            "roll_id": str(e.roll_id) if e.roll_id else None,
            "quantity": format_qty(e.quantity_milli),
            "quantity_milli": e.quantity_milli,
            "balance_after": format_qty(e.balance_after_milli),
            "balance_after_milli": e.balance_after_milli,
        }
        for e in posted.entries
    ]
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="inventory_move.posted",
        entity="inventory_move",
        entity_id=move_id,
        after={
            "kind": kind,
            "reason": reason,
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id else None,
            "lines": summary,
        },
        reason=note or None,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_STOCK_POSTED,
        aggregate_type="inventory_move",
        aggregate_id=move_id,
        payload={
            "organization_id": str(org_id),
            "move_id": str(move_id),
            "kind": kind,
            "reason": reason,
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id else None,
            "occurred_on": occurred_on.isoformat(),
            "lines": summary,
        },
        actor=_actor(ctx),
    )
    await _refresh_items(session, items)
    return posted


async def _replay(
    session: AsyncSession, org_id: uuid.UUID, key: str, request_hash: str
) -> PostedMove:
    move = (
        await session.execute(
            select(InventoryMove).where(
                InventoryMove.organization_id == org_id,
                InventoryMove.idempotency_key == key,
            )
        )
    ).scalar_one()
    if move.request_hash != request_hash:
        raise ConflictProblem(
            "That idempotency key was already used for a different stock move; "
            "use a new key for a new request"
        )
    entries = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.organization_id == org_id, LedgerEntry.move_id == move.id)
                .order_by(LedgerEntry.created_at, LedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    return PostedMove(move=move, entries=list(entries), replayed=True)


# ------------------------------------------------------------------ reservations


@dataclass(slots=True)
class ReservationResult:
    reservation: InventoryReservation
    replayed: bool = False


async def reserve(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity_milli: int,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> ReservationResult:
    """Promise available stock to a demand without moving it.

    Locks the AVAILABLE bucket, checks `quantity - reserved >= requested` under that lock, and
    raises the bucket's `reserved_milli` in the same statement's transaction — so two concurrent
    reservations serialise on the row and the second sees the first's claim. A keyed retry is
    looked up *after* the lock, so a concurrent duplicate finds the committed original.
    """
    if not _is_int(quantity_milli) or quantity_milli <= 0:
        raise ValidationProblem("A reservation is a positive integer of milli-units")
    org_id = ctx.organization_id
    item = (await _items(session, org_id, [item_id]))[item_id]
    where = (await _locations(session, org_id, [location_id]))[location_id]

    balance = await lock_bucket(
        session,
        org_id,
        item_id=item_id,
        location_id=location_id,
        lot_id=lot_id,
        roll_id=roll_id,
        create=False,
    )
    if idempotency_key is not None:
        existing = (
            await session.execute(
                select(InventoryReservation).where(
                    InventoryReservation.organization_id == org_id,
                    InventoryReservation.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            same = (
                existing.inventory_item_id == item_id
                and balance is not None
                and existing.balance_id == balance.id
                and existing.quantity_milli == quantity_milli
                and existing.reference_id == reference_id
            )
            if not same:
                raise ConflictProblem(
                    "That idempotency key was already used for a different reservation"
                )
            return ReservationResult(existing, replayed=True)

    on_hand = balance.quantity_milli if balance else 0
    reserved = balance.reserved_milli if balance else 0
    if balance is None or on_hand - reserved < quantity_milli:
        raise _shortfall(
            item, where, "AVAILABLE", wanted=quantity_milli, on_hand=on_hand, reserved=reserved
        )

    balance.reserved_milli = reserved + quantity_milli
    balance.updated_at = datetime.now(UTC)
    reservation = InventoryReservation(
        organization_id=org_id,
        balance_id=balance.id,
        inventory_item_id=item_id,
        quantity_milli=quantity_milli,
        consumed_milli=0,
        status="active",
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        created_by=str(ctx.principal.sub),
    )
    session.add(reservation)
    await session.flush()

    detail = {
        "reservation_id": str(reservation.id),
        "inventory_item_id": str(item_id),
        "sku": item.sku,
        "location": where,
        "quantity": format_qty(quantity_milli),
        "quantity_milli": quantity_milli,
        "reference_type": reference_type,
        "reference_id": str(reference_id) if reference_id else None,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="inventory_reservation.created",
        entity="inventory_reservation",
        entity_id=reservation.id,
        after=detail,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_STOCK_RESERVED,
        aggregate_type="inventory_reservation",
        aggregate_id=reservation.id,
        payload={"organization_id": str(org_id), **detail},
        actor=_actor(ctx),
    )
    await _refresh_items(session, [item_id])
    return ReservationResult(reservation)


async def release(
    session: AsyncSession,
    ctx: ScopeContext,
    reservation_id: uuid.UUID,
    *,
    quantity_milli: int | None = None,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> InventoryReservation:
    """Give back all (`quantity_milli=None`) or part of a reservation's open remainder
    (ADR-0014 §11) — the documented resolution when a count or an outbound move (issue,
    relocation, a status change's paired move) is refused because it would eat into reserved
    stock: release the reservation, or enough of it, then retry.

    Releasing an already-released reservation is a no-op (a retry is safe); releasing a fully
    consumed one, or more than is still open, is a 409. `idempotency_key` makes a retried release
    a replay rather than a second one: each key that actually released something is recorded in
    `inventory_reservation_releases`, looked up under the reservation's own row lock (so a
    concurrent duplicate blocks on that lock rather than racing the lookup) — the same key with
    the same quantity replays, the same key with a different one is refused.
    """
    org_id = ctx.organization_id
    found = (
        await session.execute(
            select(InventoryReservation.balance_id).where(
                InventoryReservation.organization_id == org_id,
                InventoryReservation.id == reservation_id,
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFoundProblem("That reservation is not part of this organization")
    if quantity_milli is not None and (not _is_int(quantity_milli) or quantity_milli <= 0):
        raise ValidationProblem("A release quantity is a positive integer of milli-units")

    # Balance first, then the reservation: the one lock order every path in this module uses.
    balance = (
        await session.execute(
            select(InventoryBalance)
            .where(InventoryBalance.organization_id == org_id, InventoryBalance.id == found)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    reservation = await _lock_reservation(session, org_id, reservation_id)
    assert reservation is not None

    request_hash = _request_hash({"quantity_milli": quantity_milli}) if idempotency_key else None
    if idempotency_key is not None:
        existing_release = (
            await session.execute(
                select(InventoryReservationRelease).where(
                    InventoryReservationRelease.organization_id == org_id,
                    InventoryReservationRelease.reservation_id == reservation_id,
                    InventoryReservationRelease.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing_release is not None:
            if existing_release.request_hash != request_hash:
                raise ConflictProblem(
                    "That idempotency key was already used for a different release"
                )
            return reservation  # replay: already applied, nothing to redo

    if reservation.status == "released":
        return reservation
    if reservation.status == "consumed":
        raise ConflictProblem("That reservation has been fully consumed")

    open_remaining = (
        reservation.quantity_milli - reservation.consumed_milli - reservation.released_milli
    )
    if quantity_milli is not None and quantity_milli > open_remaining:
        raise ConflictProblem(
            f"Only {format_qty(open_remaining)} is still open on that reservation; "
            f"cannot release {format_qty(quantity_milli)}"
        )
    released_now = open_remaining if quantity_milli is None else quantity_milli

    now = datetime.now(UTC)
    balance.reserved_milli -= released_now
    balance.updated_at = now
    reservation.released_milli += released_now
    fully_released = (
        reservation.consumed_milli + reservation.released_milli == reservation.quantity_milli
    )
    if fully_released:
        reservation.status = "released"
        reservation.closed_at = now
        reservation.closed_by = str(ctx.principal.sub)
    if idempotency_key is not None:
        session.add(
            InventoryReservationRelease(
                organization_id=org_id,
                reservation_id=reservation_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash or "",
                released_milli=released_now,
            )
        )
    await session.flush()

    detail = {
        "reservation_id": str(reservation.id),
        "inventory_item_id": str(reservation.inventory_item_id),
        "released": format_qty(released_now),
        "released_milli": released_now,
        "remaining": format_qty(open_remaining - released_now),
        "remaining_milli": open_remaining - released_now,
        "fully_released": fully_released,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="inventory_reservation.released",
        entity="inventory_reservation",
        entity_id=reservation.id,
        before={"status": "active"},
        after={"status": reservation.status, **detail},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_STOCK_RELEASED,
        aggregate_type="inventory_reservation",
        aggregate_id=reservation.id,
        payload={"organization_id": str(org_id), **detail},
        actor=_actor(ctx),
    )
    await _refresh_items(session, [reservation.inventory_item_id])
    return reservation
