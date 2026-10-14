"""Reads for dispatch (ADR-0024 §6; `docs/fulfilment/plan.md` F5), plus the ORM -> wire
projection (`app.selectors`'s own convention: shaping a row for the wire is part of reading it).

Shipments are a shared operational queue like orders, tasks and packages — a dispatcher has to
see every shipment in the org to run the floor, not only the ones they personally created — so
`dispatch.shipment.read`/`.write` are ORG-only in `app/catalogue.py`, and `shipments_stmt` still
takes the caller's `ScopeContext` and calls `ctx.apply()`, the same enforcement point every other
scoped list in this service uses (`app.fulfilment.selectors`'s own module docstring documents the
same reasoning for `packages_stmt`).

The actual *which reservation does dispatch consume* question is no longer answered here
(security review B1/M2): the flat gather now lives in `app.orders.selectors
.leaf_reservations_for_order`/`unpicked_lines_for_order`, read by `app.orders.services
.complete_handover` — the shared consumption step both the counter (`hand_over_order`) and
carrier (`dispatch_order`) handover paths call identically. What stays here is carrier-specific:
whether an order's lines were genuinely *picked and, if packing was started, finished* before a
carrier is ever offered the goods (`unfulfilled_order_lines`) — a stronger, fulfilment-aware check
the counter path has no equivalent of, and so cannot share (`orders` never imports `fulfilment`,
ADR-0024 §1).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fulfilment import selectors as fulfilment_selectors
from app.models import OrderLine, Shipment
from app.schemas import ShipmentOut
from platform_core.auth import ScopeContext


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


# ------------------------------------------------------------------ carrier-only fulfilment
# coverage check (ADR-0024 §6; security review B1/M1) — whether a carrier is being handed a
# genuinely completed pick (and, if packing was started at all, a completed pack).


async def unfulfilled_order_lines(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, lines: list[OrderLine]
) -> tuple[list[tuple[OrderLine, int]], list[tuple[OrderLine, int]]]:
    """For each of the order's own lines: how much was never picked at all — still sitting on the
    line's own accept-time promise, or a pick task cancelled before it finished (security review
    M1) — and how much was picked but left a genuine partial-pack remainder: *some*, not all, of
    what was picked has gone into a package (security review B1's split-pack finding). A line
    never sent to a pack station at all (`packaged_milli == 0`, dispatch draws straight from
    staging) and a line fully packed (`packaged_milli == picked_milli`, dispatch draws from the
    dock, however many packages it took) are both valid; only a pack that started and stopped
    partway through one line is not — that quantity is neither at the dock nor still fully staged,
    and shipping around it would silently drop it.

    Returns `(never_picked, partial_pack)`, each a list of `(order_line, milli_units)` naming
    exactly what is missing — never a bare "cannot dispatch".
    """
    task_lines = await fulfilment_selectors.task_lines_for_order(session, org_id, order_id)
    by_order_line = {tl.order_line_id: tl for tl in task_lines}
    packaged = await fulfilment_selectors.packaged_milli_for_task_lines(
        session, org_id, [tl.id for tl in task_lines]
    )
    never_picked: list[tuple[OrderLine, int]] = []
    partial_pack: list[tuple[OrderLine, int]] = []
    for line in lines:
        task_line = by_order_line.get(line.id)
        if task_line is None:  # pragma: no cover - defensive, create_pick_tasks covers every line
            never_picked.append((line, line.quantity_milli))
            continue
        unpicked = line.quantity_milli - task_line.picked_milli - task_line.short_milli
        if unpicked > 0:
            never_picked.append((line, unpicked))
            continue
        packaged_milli = packaged.get(task_line.id, 0)
        if 0 < packaged_milli < task_line.picked_milli:
            partial_pack.append((line, task_line.picked_milli - packaged_milli))
    return never_picked, partial_pack


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
