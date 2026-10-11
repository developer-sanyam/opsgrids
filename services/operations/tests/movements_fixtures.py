"""Shared set-up for the Phase 3 (movements and control) tests: the textile world from
`tracking_fixtures` (two warehouses, a lot- and roll-tracked fabric, dye, buttons), stock put on its
shelves through the real kernel, and a one-line way to call the API as a named principal.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.models import InventoryBalance, OutboxEvent
from platform_core.auth import Principal
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import TODAY, q
from tests.tracking_fixtures import API, World, build_world

__all__ = [
    "API",
    "CONTROL_CONFIGURE",
    "STOCK_ADJUST",
    "STOCK_READ",
    "STOCK_RESERVE",
    "TRANSFER_APPROVE",
    "TRANSFER_DISPATCH",
    "TRANSFER_RECEIVE",
    "TRANSFER_WRITE",
    "World",
    "build_world",
    "call",
    "events",
    "in_transit_total",
    "mover",
    "receive_stock",
    "transfer_body",
]

TRANSFER_WRITE = "operations.inventory.transfer.write:ORG"
TRANSFER_APPROVE = "operations.inventory.transfer.approve:ORG"
TRANSFER_DISPATCH = "operations.inventory.transfer.dispatch:ORG"
TRANSFER_RECEIVE = "operations.inventory.transfer.receive:ORG"
CONTROL_CONFIGURE = "operations.inventory.control.configure:ORG"
STOCK_READ = "operations.inventory.stock.read:ORG"
STOCK_RESERVE = "operations.inventory.stock.reserve:ORG"
STOCK_ADJUST = "operations.inventory.stock.adjust:ORG"


def mover(org_id: uuid.UUID, *perms: str, employee: bool = False) -> Principal:
    """A distinct caller. `employee=True` links it to an employee record (`subject_ref`), which a
    request that must go through the approval service needs."""
    return principal_for(org_id, list(perms), subject_ref=uuid.uuid4() if employee else None)


async def call(
    client: Any,
    who: Principal,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    key: str | None = None,
) -> Any:
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(who):
        return await client.request(method, API + path, json=body, headers=headers)


async def receive_stock(
    session: AsyncSession,
    w: World,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity: str,
    *,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
) -> None:
    """Put `quantity` on a shelf through the kernel, so every test starts from posted stock."""
    async with session.begin():
        await ledger.post_move(
            session,
            w.t.ctx,
            kind="receipt",
            lines=[MoveLine(item_id, location_id, q(quantity), lot_id=lot_id, roll_id=roll_id)],
            occurred_on=TODAY,
        )


def transfer_body(
    w: World,
    *lines: dict[str, Any],
    from_warehouse: uuid.UUID | None = None,
    to_warehouse: uuid.UUID | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A draft from the weaving warehouse to the finishing one. Each `line` is a dict with
    `inventory_item_id`, `from_location_id`, `quantity` (+ optional lot/roll)."""
    return {
        "from_warehouse_id": str(from_warehouse or w.warehouse_weave),
        "to_warehouse_id": str(to_warehouse or w.warehouse_fin),
        "lines": [dict(ln) for ln in lines],
        **extra,
    }


async def in_transit_total(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0)).where(
                        InventoryBalance.organization_id == org_id,
                        InventoryBalance.inventory_item_id == item_id,
                        InventoryBalance.status == "IN_TRANSIT",
                    )
                )
            ).scalar_one()
        )


async def events(
    session: AsyncSession, event_type: str, *, org_id: uuid.UUID | None = None
) -> Sequence[OutboxEvent]:
    async with session.begin():
        stmt = select(OutboxEvent).where(OutboxEvent.event_type == event_type)
        if org_id is not None:
            stmt = stmt.where(OutboxEvent.organization_id == org_id)
        return (await session.execute(stmt.order_by(OutboxEvent.created_at))).scalars().all()
