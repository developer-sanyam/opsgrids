"""Reads for dispatch (ADR-0024 §6; `docs/fulfilment/plan.md` F5), plus the ORM -> wire
projection (`app.selectors`'s own convention: shaping a row for the wire is part of reading it).

Shipments are a shared operational queue like orders, tasks and packages — a dispatcher has to
see every shipment in the org to run the floor, not only the ones they personally created — so
`dispatch.shipment.read`/`.write` are ORG-only in `app/catalogue.py`, and `shipments_stmt` still
takes the caller's `ScopeContext` and calls `ctx.apply()`, the same enforcement point every other
scoped list in this service uses (`app.fulfilment.selectors`'s own module docstring documents the
same reasoning for `packages_stmt`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fulfilment import selectors as fulfilment_selectors
from app.models import InventoryBalance, InventoryReservation, Shipment
from app.schemas import ShipmentOut
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem

#: A transfer chain (accept's reservation -> a pick's staging reservation -> a pack's dock
#: reservation) is at most two hops deep in this build. A bound, not an unbounded walk: a chain
#: longer than this is a bug in the transfer bookkeeping, not a deeper warehouse flow F5 supports.
_MAX_TRANSFER_HOPS = 8


def shipments_stmt(
    ctx: ScopeContext,
    *,
    order_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Any]:
    """N3 (security review) shape: `ctx.apply` is the enforcement point, on top of the route's
    own `minimum=Scope.ORG` floor — see the module docstring."""
    stmt = select(Shipment).where(
        Shipment.organization_id == ctx.organization_id, Shipment.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if order_id:
        stmt = stmt.where(Shipment.order_id == order_id)
    if warehouse_id:
        stmt = stmt.where(Shipment.warehouse_id == warehouse_id)
    if status:
        stmt = stmt.where(Shipment.status == status)
    return stmt.order_by(Shipment.created_at.desc())


async def get_shipment(
    session: AsyncSession, org_id: uuid.UUID, shipment_id: uuid.UUID, *, for_update: bool = False
) -> Shipment | None:
    stmt = select(Shipment).where(
        Shipment.id == shipment_id,
        Shipment.organization_id == org_id,
        Shipment.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_shipment_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> Shipment | None:
    stmt = select(Shipment).where(
        Shipment.organization_id == org_id,
        Shipment.order_id == order_id,
        Shipment.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ the pick/pack transfer chain
# (ADR-0024 §6) — where a dispatch issue actually draws from


async def _reservation_transferred_from(
    session: AsyncSession, org_id: uuid.UUID, source_reservation_id: uuid.UUID
) -> InventoryReservation | None:
    stmt = select(InventoryReservation).where(
        InventoryReservation.organization_id == org_id,
        InventoryReservation.transferred_from_reservation_id == source_reservation_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def leaf_reservation_for(
    session: AsyncSession, org_id: uuid.UUID, reservation: InventoryReservation
) -> InventoryReservation:
    """Walks `transferred_from_reservation_id` forward from `reservation` — an order line's own
    accept-time promise — to wherever it currently sits (ADR-0024 §6): unchanged if nothing has
    moved it yet, the staging reservation a pick opened if picked but not (yet) packed, or the
    pack/dock reservation a pack opened if packed. Dispatch draws against whatever this returns,
    never a fixed two-hop assumption that would break if a step is skipped or the chain runs
    deeper than pick-then-pack.
    """
    current = reservation
    for _ in range(_MAX_TRANSFER_HOPS):
        child = await _reservation_transferred_from(session, org_id, current.id)
        if child is None:
            return current
        current = child
    raise ConflictProblem(  # pragma: no cover - defensive, a bug in the transfer chain, not data
        f"Reservation {reservation.id}'s transfer chain is unexpectedly deep"
    )


async def leaf_reservations_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> dict[uuid.UUID, InventoryReservation]:
    """Every one of `order_id`'s own lines, mapped to the reservation currently holding its
    promise — the end of whatever chain of transfers (pick, pack) has moved it since accept. A
    line whose reservation was entirely released (a full short-pick, never picked at all) still
    has an entry here; its own `leaf`'s open remainder is simply zero, which
    `app.dispatch.services.dispatch_order` reads and skips rather than treating as missing.
    """
    by_line = await fulfilment_selectors.reservations_by_order_line(session, org_id, order_id)
    out: dict[uuid.UUID, InventoryReservation] = {}
    for line_id, reservation in by_line.items():
        out[line_id] = await leaf_reservation_for(session, org_id, reservation)
    return out


async def balances_by_id(
    session: AsyncSession, org_id: uuid.UUID, balance_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, InventoryBalance]:
    """A batch of `inventory_balances` rows by id — one query for a whole dispatch (N+1
    avoidance), the same shape `app.orders.selectors.items_for_lines` uses for items."""
    if not balance_ids:
        return {}
    stmt = select(InventoryBalance).where(
        InventoryBalance.organization_id == org_id, InventoryBalance.id.in_(set(balance_ids))
    )
    return {b.id: b for b in (await session.execute(stmt)).scalars().all()}


# ------------------------------------------------------------------ projections (ORM -> wire)


def shipment_out(shipment: Shipment) -> ShipmentOut:
    return ShipmentOut(
        id=str(shipment.id),
        order_id=str(shipment.order_id),
        warehouse_id=str(shipment.warehouse_id),
        status=shipment.status,
        carrier_name=shipment.carrier_name,
        tracking_reference=shipment.tracking_reference,
        failure_reason=shipment.failure_reason,
        delivered_at=shipment.delivered_at,
        created_at=shipment.created_at,
        updated_at=shipment.updated_at,
    )
