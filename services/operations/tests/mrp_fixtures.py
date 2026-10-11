"""Shared set-up for the MRP tests: the textile factory of `manufacturing_fixtures` with two
published BOMs (a two-level tree), and writers for the demand and supply MRP reads — sales orders,
purchase orders, reservations, production orders — built through the real write paths where there
is one and as plain rows where the module under test only reads them.

Plain module, not conftest, for the reason `helpers.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import ledger
from app.models import (
    Order,
    OrderLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
    Warehouse,
)
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import q
from tests.manufacturing_fixtures import (
    Mfg,
    build_mfg,
    make_bom,
    stock,
)

BASE = "/api/v1/operations/manufacturing/mrp"

P_READ = "operations.manufacturing.mrp.read:ORG"
P_RUN = "operations.manufacturing.mrp.run:ORG"
P_REQ = "operations.manufacturing.mrp.requisition:ORG"
ALL_MRP_PERMS = [P_READ, P_RUN, P_REQ]


def need(component: int, output: int, planned: int, scrap_bps: int = 0) -> int:
    """An independent oracle for one BOM line: exact rational arithmetic, rounded up once."""
    return ceil(Fraction(component * planned * (10_000 + scrap_bps), output * 10_000))


@dataclass
class Plant:
    """`m` plus a two-level tree: 1 SHIRT-01 = 1.2 m PANEL-NAVY + 5 BTN-14MM;
    1 m PANEL-NAVY = 1.234 m COT-180-NAVY (2.5 % scrap) + 0.05 kg DYE-RED-01 (10 % scrap)."""

    m: Mfg
    shirt_bom: str
    panel_bom: str

    @property
    def org_id(self) -> uuid.UUID:
        return self.m.org_id

    @property
    def t(self):  # type: ignore[no-untyped-def]
        return self.m.t


async def build_plant(client, session: AsyncSession, org_id: uuid.UUID) -> Plant:  # type: ignore[no-untyped-def]
    m = await build_mfg(session, org_id)
    shirt = await make_bom(
        client,
        m,
        m.shirt,
        "BOM-SHIRT",
        [(m.panel, "1.2", "0"), (m.t.buttons, "5", "0")],
    )
    panel = await make_bom(
        client,
        m,
        m.panel,
        "BOM-PANEL",
        [(m.t.fabric, "1.234", "2.5"), (m.t.dye, "0.05", "10")],
    )
    return Plant(m, shirt["bom_id"], panel["bom_id"])


async def stock_in(session: AsyncSession, p: Plant, **quantities: str) -> None:
    """Receive stock by short name: fabric, dye, buttons, panel, shirt."""
    items = {
        "fabric": p.t.fabric,
        "dye": p.t.dye,
        "buttons": p.t.buttons,
        "panel": p.m.panel,
        "shirt": p.m.shirt,
    }
    for name, quantity in quantities.items():
        await stock(session, p.m, items[name], quantity)


async def add_sales_order(
    session: AsyncSession,
    org_id: uuid.UUID,
    number: str,
    lines: list[tuple[uuid.UUID, str]],
    *,
    status: str = "PLACED",
) -> uuid.UUID:
    order_id = uuid.uuid4()
    async with session.begin():
        warehouse = (
            (
                await session.execute(
                    select(Warehouse)
                    .where(Warehouse.organization_id == org_id)
                    .order_by(Warehouse.code)
                )
            )
            .scalars()
            .first()
        )
        assert warehouse is not None
        session.add(
            Order(
                id=order_id,
                organization_id=org_id,
                number=number,
                channel="manual",
                status=status,
                version=0,
                warehouse_id=warehouse.id,
            )
        )
        await session.flush()
        for item_id, quantity in lines:
            session.add(
                OrderLine(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    order_id=order_id,
                    inventory_item_id=item_id,
                    quantity_milli=q(quantity),
                )
            )
    return order_id


async def add_purchase_order(
    session: AsyncSession,
    org_id: uuid.UUID,
    number: str,
    lines: list[tuple[Any, ...]],
    *,
    status: str = "approved",
) -> uuid.UUID:
    """`lines` are `(sku, quantity, received_qty)` — PO lines are whole units — with an optional
    fourth dict of column overrides (`uom`, `received_milli`, `short_closed`). `received_milli`
    defaults to `received_qty` whole units, as a real receipt would have left it."""
    po_id = uuid.uuid4()
    async with session.begin():
        supplier = (
            (await session.execute(select(Supplier).where(Supplier.organization_id == org_id)))
            .scalars()
            .first()
        )
        if supplier is None:
            supplier = Supplier(
                id=uuid.uuid4(),
                organization_id=org_id,
                code="SUP-1",
                name="Supplier",
                category="raw_materials",
            )
            session.add(supplier)
            await session.flush()
        session.add(
            PurchaseOrder(
                id=po_id,
                organization_id=org_id,
                number=number,
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                category="raw_materials",
                status=status,
                created_date=date(2026, 10, 1),
                expected_date=date(2026, 10, 20),
            )
        )
        await session.flush()
        for sku, quantity, received, *rest in lines:
            extra: dict[str, Any] = {"uom": "pcs", "received_milli": received * 1000}
            extra.update(rest[0] if rest else {})
            session.add(
                PurchaseOrderLine(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    purchase_order_id=po_id,
                    sku=sku,
                    quantity=quantity,
                    received_qty=received,
                    **extra,
                )
            )
    return po_id


async def reserve_for(
    session: AsyncSession,
    p: Plant,
    item: uuid.UUID,
    quantity: str,
    *,
    reference_type: str | None = "manual_hold",
    reference_id: uuid.UUID | None = None,
) -> None:
    async with session.begin():
        await ledger.reserve(
            session,
            p.t.ctx,
            item_id=item,
            location_id=p.t.weaving,
            quantity_milli=q(quantity),
            reference_type=reference_type,
            reference_id=reference_id or uuid.uuid4(),
        )


# ------------------------------------------------------------------ HTTP


async def mrp(  # type: ignore[no-untyped-def]
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
    principal = principal_for(
        org_id, ALL_MRP_PERMS if perms is None else perms, subject_ref=uuid.uuid4()
    )
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal):
        return await client.request(
            method, f"{BASE}{path}", json=json, headers=headers, params=params
        )


async def mrp_ok(client, org_id, method, path, *, expect: int = 200, **kw) -> Any:  # type: ignore[no-untyped-def]
    response = await mrp(client, org_id, method, path, **kw)
    assert response.status_code == expect, (
        f"{method} {path}: {response.status_code} {response.text}"
    )
    return response.json() if response.content else None


async def start_run(client, org_id, *, expect: int = 201, **body: Any) -> dict:  # type: ignore[no-untyped-def]
    return await mrp_ok(client, org_id, "POST", "/runs", expect=expect, json=body)


async def shortages(client, org_id, run_id: str, **params: Any) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    page = await mrp_ok(
        client,
        org_id,
        "GET",
        f"/runs/{run_id}/shortages",
        params={"size": 100, **params},
    )
    return {row["sku"]: row for row in page["items"]}
