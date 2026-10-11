"""Shared set-up for the manufacturing tests: a small textile factory built through the real write
paths, and thin HTTP helpers that drive BOMs and production orders through the real routes — no
service-layer shortcuts, the same reasoning `counting_fixtures.py` gives.

Plain module, not conftest, for the reason `helpers.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import ledger
from app.inventory import services as inv_services
from app.inventory import tracking_services as tracking
from app.inventory.ledger import MoveLine
from app.inventory.tracking_schemas import LotIn, RollIn
from app.models import InventoryItem
from tests.helpers import as_principal, principal_for, same_caller
from tests.inventory_fixtures import TODAY, Textile, build_textile, q

BASE = "/api/v1/operations/manufacturing"

P_BOM_READ = "operations.manufacturing.bom.read:ORG"
P_BOM_WRITE = "operations.manufacturing.bom.write:ORG"
P_BOM_PUBLISH = "operations.manufacturing.bom.publish:ORG"
P_ORD_READ = "operations.manufacturing.production_order.read:ORG"
P_ORD_WRITE = "operations.manufacturing.production_order.write:ORG"
P_ORD_APPROVE = "operations.manufacturing.production_order.approve:ORG"
P_ORD_ISSUE = "operations.manufacturing.production_order.issue:ORG"
P_ORD_COMPLETE = "operations.manufacturing.production_order.complete:ORG"

ALL_PERMS = [
    P_BOM_READ,
    P_BOM_WRITE,
    P_BOM_PUBLISH,
    P_ORD_READ,
    P_ORD_WRITE,
    P_ORD_APPROVE,
    P_ORD_ISSUE,
    P_ORD_COMPLETE,
]


@dataclass
class Mfg:
    """A textile org: fabric (m), dye (kg), buttons (pcs) bought; a shirt (pcs) and a dyed panel
    (m) made. Two bins (weaving, finishing) in two warehouses."""

    t: Textile
    shirt: uuid.UUID
    panel: uuid.UUID

    @property
    def org_id(self) -> uuid.UUID:
        return self.t.org_id


async def build_mfg(session: AsyncSession, org_id: uuid.UUID) -> Mfg:
    t = await build_textile(session, org_id)
    async with session.begin():
        shirt = await inv_services.create_inventory_item(
            session,
            t.ctx,
            sku="SHIRT-01",
            name="Cotton shirt",
            category="finished_goods",
            warehouse="Finishing",
            uom="pcs",
        )
        panel = await inv_services.create_inventory_item(
            session,
            t.ctx,
            sku="PANEL-NAVY",
            name="Dyed navy panel",
            category="components",
            warehouse="Weaving Store",
            uom="m",
        )
    return Mfg(t=t, shirt=shirt.id, panel=panel.id)


async def make_lot(
    session: AsyncSession, m: Mfg, item: uuid.UUID, lot_no: str, *, track_roll: bool = False
) -> uuid.UUID:
    """A real lot of `item` (switching on lot tracking, and roll tracking when asked): since
    Phase 1 the ledger's lot and roll columns are foreign keys, so a made-up id cannot be
    stocked."""
    async with session.begin():
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == m.org_id, InventoryItem.id == item)
            .values(track_lot=True, **({"track_roll": True} if track_roll else {}))
        )
        lot = await tracking.create_lot(
            session, m.t.ctx, LotIn(inventory_item_id=item, lot_no=lot_no)
        )
    return lot.id


async def make_roll(
    session: AsyncSession, m: Mfg, item: uuid.UUID, lot: uuid.UUID, roll_no: str, primary: str
) -> uuid.UUID:
    """A real, registered (not yet stocked) roll of `item` in `lot` — see `make_lot`."""
    async with session.begin():
        roll = await tracking.create_roll(
            session,
            m.t.ctx,
            RollIn(inventory_item_id=item, lot_id=lot, roll_no=roll_no, primary_qty=primary),
        )
    return roll.id


async def stock(
    session: AsyncSession,
    m: Mfg,
    item: uuid.UUID,
    quantity: str,
    *,
    location: uuid.UUID | None = None,
    lot: uuid.UUID | None = None,
    roll: uuid.UUID | None = None,
) -> None:
    """Receive `quantity` of `item` into AVAILABLE at a location (default: the weaving bin)."""
    async with session.begin():
        await ledger.post_move(
            session,
            m.t.ctx,
            kind="receipt",
            lines=[
                MoveLine(
                    item,
                    location or m.t.weaving,
                    q(quantity),
                    lot_id=lot,
                    roll_id=roll,
                )
            ],
            occurred_on=TODAY,
        )


async def call(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    method: str,
    path: str,
    *,
    perms: list[str] | None = None,
    json: Any = None,
    key: str | None = None,
    params: dict[str, Any] | None = None,
):
    use = ALL_PERMS if perms is None else perms
    principal = principal_for(
        org_id, use, subject_ref=uuid.uuid4(), sub=same_caller(org_id, use)
    )
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal):
        return await client.request(
            method,
            f"{BASE}{path}",
            json=json,
            headers=headers,
            params=params,
        )


async def ok(client, org_id, method, path, *, expect: int = 200, **kw) -> Any:  # type: ignore[no-untyped-def]
    response = await call(client, org_id, method, path, **kw)
    assert response.status_code == expect, (
        f"{method} {path}: {response.status_code} {response.text}"
    )
    return response.json() if response.content else None


async def make_bom(  # type: ignore[no-untyped-def]
    client,
    m: Mfg,
    item: uuid.UUID,
    code: str,
    lines: list[tuple[uuid.UUID, str, str]],
    *,
    output: str = "1",
    publish: bool = True,
    effective_from: str | None = None,
) -> dict:
    """A BOM for `item` with `(component, quantity, scrap_percent)` lines, published by default.
    Returns the published version detail plus `bom_id`."""
    bom = await ok(
        client,
        m.org_id,
        "POST",
        "/boms",
        expect=201,
        json={
            "code": code,
            "name": f"{code} recipe",
            "item_id": str(item),
            "output_quantity": output,
        },
    )
    version = bom["versions"][0]
    if effective_from:
        await ok(
            client,
            m.org_id,
            "PATCH",
            f"/boms/{bom['id']}/versions/{version['id']}",
            json={"effective_from": effective_from},
        )
    for component, quantity, scrap in lines:
        detail = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{bom['id']}/versions/{version['id']}/components",
            expect=201,
            json={
                "component_item_id": str(component),
                "quantity": quantity,
                "scrap_percent": scrap,
            },
        )
    if publish:
        detail = await ok(
            client, m.org_id, "POST", f"/boms/{bom['id']}/versions/{version['id']}/publish"
        )
    else:
        detail = await ok(client, m.org_id, "GET", f"/boms/{bom['id']}/versions/{version['id']}")
    return {**detail, "bom_id": bom["id"]}


async def make_order(  # type: ignore[no-untyped-def]
    client,
    m: Mfg,
    item: uuid.UUID,
    quantity: str,
    *,
    output_location: uuid.UUID | None = None,
    approve: bool = True,
    **extra: Any,
) -> dict:
    order = await ok(
        client,
        m.org_id,
        "POST",
        "/orders",
        expect=201,
        json={
            "item_id": str(item),
            "quantity": quantity,
            "output_location_id": str(output_location or m.t.finishing),
            **extra,
        },
    )
    if approve:
        order = await ok(client, m.org_id, "POST", f"/orders/{order['id']}/approve")
    return order


def material(order: dict, sku: str) -> dict:
    return next(x for x in order["materials"] if x["sku"] == sku)
