"""Reservations over HTTP and their expiry (inventory Phase 3; docs/inventory/movements.md).

The promise itself is the kernel's: `ledger.reserve` locks the AVAILABLE bucket, checks
`quantity - reserved >= requested` under that lock and raises `reserved_milli` in the same
transaction, so two concurrent reservations serialise on the row and over-reservation is
impossible (proven by 50 parallel reserves against 10 units in `test_movements_concurrency.py`).
This module adds the meaning around it — what a reservation may be *for*, how long it lasts — and
the sweep that lets a lapsed one go through `ledger.release`, the same door a person's release uses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger, stock_ops
from app.models import InventoryReservation
from app.movements import common
from app.movements import selectors as sel
from app.movements.schemas import ReservationIn
from platform_core.auth import ScopeContext
from platform_core.errors import ProblemError

#: How many lapsed reservations one organization's pass releases at most — a backlog drains over
#: several ticks rather than holding one transaction open for minutes.
EXPIRY_BATCH = 200


async def create_reservation(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ReservationIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
) -> tuple[InventoryReservation, bool]:
    """Reserve AVAILABLE stock in one bucket. Returns `(reservation, replayed)`. The kernel
    refuses with `InsufficientStock` (409, the numbers in `errors`) when there is not enough
    unreserved stock — never a partial reservation."""
    item = (await stock_ops.load_items(session, ctx.organization_id, [payload.inventory_item_id]))[
        payload.inventory_item_id
    ]
    stock_ops.check_tracking(item, payload.lot_id, payload.roll_id)
    milli = stock_ops.parse_positive(payload.quantity, item.uom, "quantity")
    made = await ledger.reserve(
        session,
        ctx,
        item_id=item.id,
        location_id=payload.location_id,
        quantity_milli=milli,
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
        expires_at=payload.expires_at,
        note=payload.note,
        request=request,
    )
    return made.reservation, made.replayed


@dataclass(slots=True)
class ExpiryResult:
    due: int = 0
    expired: int = 0
    skipped: int = 0


async def expire_due(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    now: datetime | None = None,
    limit: int = EXPIRY_BATCH,
) -> ExpiryResult:
    """Release every active reservation of the organization whose `expires_at` has passed.

    Each goes through `ledger.release` — under the item, balance and reservation locks, audited,
    and emitting `stock.released` — keyed `expire:<id>`, so a repeated tick is a replay. A
    reservation that was consumed or released by someone else between the read and the lock is a
    409 from the kernel; it is counted as skipped, not as a failure of the pass. Each release runs
    in its own savepoint, so one that cannot proceed never rolls back the others.
    """
    cutoff = now or datetime.now(UTC)
    ctx = common.system_ctx(organization_id)
    due_ids = list(
        (
            await session.execute(
                select(InventoryReservation.id)
                .where(
                    InventoryReservation.organization_id == organization_id,
                    InventoryReservation.status == "active",
                    InventoryReservation.expires_at.is_not(None),
                    InventoryReservation.expires_at <= cutoff,
                )
                .order_by(InventoryReservation.expires_at, InventoryReservation.id)
                .limit(limit)
            )
        ).scalars()
    )
    result = ExpiryResult(due=len(due_ids))
    for reservation_id in due_ids:
        try:
            async with session.begin_nested():
                reservation = await ledger.release(
                    session, ctx, reservation_id, idempotency_key=f"expire:{reservation_id}"
                )
                if reservation.expired_at is None:
                    reservation.expired_at = cutoff
                stock_ops.stage(
                    session,
                    ctx,
                    action="inventory_reservation.expired",
                    event_type=common.EVT_RESERVATION_EXPIRED,
                    entity="inventory_reservation",
                    entity_id=reservation.id,
                    after={"expires_at": reservation.expires_at, "status": reservation.status},
                    payload={
                        "reservation_id": str(reservation.id),
                        "inventory_item_id": str(reservation.inventory_item_id),
                        "reference_type": reservation.reference_type,
                        "reference_id": str(reservation.reference_id)
                        if reservation.reference_id
                        else None,
                        "expires_at": reservation.expires_at.isoformat()
                        if reservation.expires_at
                        else None,
                    },
                    reason="expired",
                    request=None,
                )
        except ProblemError:
            result.skipped += 1
            continue
        result.expired += 1
    return result


async def reservation_detail(
    session: AsyncSession, ctx: ScopeContext, reservation_id: uuid.UUID
) -> InventoryReservation | None:
    return await sel.get_reservation(session, ctx.organization_id, reservation_id)
