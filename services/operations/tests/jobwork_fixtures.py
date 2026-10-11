"""Shared set-up for the job-work tests (Inventory plan Phase 6): a small textile mill — a
supplier (the dyer), a priced fabric item held in metres with 100 m on the shelf, a second bin to
receive returned goods into — plus the HTTP helpers every test drives the flow through. Real
write paths and real permission checks, no service-layer shortcuts for the thing under test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.ledger import MoveLine
from app.models import SCHEMA
from platform_core.auth import Scope
from tests.helpers import as_principal, ctx_for, principal_for, same_caller
from tests.order_fixtures import Shop, build_shop

JW_READ = "operations.jobwork.order.read"
JW_WRITE = "operations.jobwork.order.write"
JW_ISSUE = "operations.jobwork.order.issue"
JW_RECEIVE = "operations.jobwork.order.receive"
JW_APPROVE = "operations.jobwork.order.approve"
ALL_JW = [JW_READ, JW_WRITE, JW_ISSUE, JW_RECEIVE, JW_APPROVE]
ALL_JW_ORG = [f"{p}:ORG" for p in ALL_JW]

BASE = "/api/v1/operations/jobwork"


@dataclass
class Mill:
    org_id: uuid.UUID
    shop: Shop
    supplier_id: uuid.UUID
    fabric: uuid.UUID  # FAB-GREIGE, metres, Rs 400.00/m, 100 m at its home bin
    thread: uuid.UUID  # THR-001, pcs, Rs 50.00 each, 200 pcs at its home bin
    home: uuid.UUID  # the fabric's (and thread's) home bin
    recv: uuid.UUID  # a second bin in the same warehouse
    warehouse_id: uuid.UUID


async def build_mill(
    session: AsyncSession, org_id: uuid.UUID, *, fabric_m: str = "100", on_hold: bool = False
) -> Mill:
    shop = await build_shop(session, org_id, stock=1)
    ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
    supplier_ctx = ctx_for(org_id, "operations.procurement.supplier.write", Scope.ORG)
    async with session.begin():
        warehouse = await inv_selectors.get_warehouse(session, org_id, shop.warehouse_id)
        assert warehouse is not None
        fabric = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="FAB-GREIGE",
            name="Greige fabric",
            category="raw_materials",
            warehouse=warehouse.name,
            uom="m",
            unit_value_minor=400_00,
            currency="INR",
        )
        thread = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="THR-001",
            name="Sewing thread",
            category="raw_materials",
            warehouse=warehouse.name,
            uom="pcs",
            on_hand=200,
            unit_value_minor=50_00,
            currency="INR",
        )
        assert fabric.home_location_id is not None
        home = fabric.home_location_id
        recv = await inv_services.create_location(
            session, ctx, warehouse, kind="bin", code="RECV-1", name="Returns bin"
        )
        await ledger.post_move(
            session,
            ctx,
            kind="opening",
            lines=[
                MoveLine(
                    item_id=fabric.id,
                    location_id=home,
                    quantity_milli=int(float(fabric_m) * 1000),
                )
            ],
            occurred_on=date.today(),
            reference_type="test_seed",
            reference_id=uuid.uuid4(),
        )
        supplier = await services.create_supplier(
            session,
            supplier_ctx,
            code="SUP-DYE",
            name="Rainbow Dyers",
            category="services",
            status="on_hold" if on_hold else "active",
        )
    return Mill(
        org_id=org_id,
        shop=shop,
        supplier_id=supplier.id,
        fabric=fabric.id,
        thread=thread.id,
        home=home,
        recv=recv.id,
        warehouse_id=shop.warehouse_id,
    )


def key() -> str:
    return uuid.uuid4().hex


async def call(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    method: str,
    path: str,
    *,
    perms: list[str] | None = None,
    json: Any = None,
    idem: str | None = None,
):
    """One request as a principal holding `perms` (default: every job-work permission at ORG)."""
    use = perms if perms is not None else ALL_JW_ORG
    principal = principal_for(org_id, use, subject_ref=uuid.uuid4(), sub=same_caller(org_id, use))
    headers = {"Idempotency-Key": idem} if idem else {}
    with as_principal(principal):
        return await client.request(method, f"{BASE}{path}", json=json, headers=headers)


def order_body(mill: Mill, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "supplier_id": str(mill.supplier_id),
        "process": "Dyeing",
        "due_date": None,
        "receive_location_id": str(mill.recv),
        "qc_required": False,
        "notes": "",
        "materials": [{"item_id": str(mill.fabric), "quantity": "82.5"}],
    }
    body.update(overrides)
    return body


async def create_order(client, mill: Mill, **overrides: Any) -> dict:  # type: ignore[no-untyped-def]
    response = await call(
        client, mill.org_id, "POST", "/orders", json=order_body(mill, **overrides), idem=key()
    )
    assert response.status_code == 201, response.text
    return response.json()


async def approve(client, mill: Mill, order_id: str) -> dict:  # type: ignore[no-untyped-def]
    response = await call(client, mill.org_id, "POST", f"/orders/{order_id}:approve")
    assert response.status_code == 200, response.text
    return response.json()


async def issue(  # type: ignore[no-untyped-def]
    client, mill: Mill, order_id: str, *, body: dict | None = None, idem: str | None = None
) -> dict:
    response = await call(
        client,
        mill.org_id,
        "POST",
        f"/orders/{order_id}:issue",
        json=body or {},
        idem=idem or key(),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def issued_order(client, mill: Mill, **overrides: Any) -> dict:  # type: ignore[no-untyped-def]
    order = await create_order(client, mill, **overrides)
    await approve(client, mill, order["id"])
    return await issue(client, mill, order["id"])


def receipt_line(
    material_id: str, *, received="0", rejected="0", shortage="0", **extra: Any
) -> dict:
    return {
        "material_id": material_id,
        "received": received,
        "rejected": rejected,
        "shortage": shortage,
        **extra,
    }


async def receive(  # type: ignore[no-untyped-def]
    client, mill: Mill, order_id: str, lines: list[dict], *, idem: str | None = None, **extra: Any
):
    return await call(
        client,
        mill.org_id,
        "POST",
        f"/orders/{order_id}:receive",
        json={"lines": lines, **extra},
        idem=idem or key(),
    )


async def balance_milli(
    session: AsyncSession, item_id: uuid.UUID, location_id: uuid.UUID, status: str
) -> int:
    # Its own transaction, closed before returning: the request chokepoint opens one per request
    # on this same session, and "a transaction is already begun" is what a left-open read causes.
    async with session.begin():
        row = (
            await session.execute(
                text(
                    f"""
                    SELECT COALESCE(SUM(quantity_milli), 0) FROM {SCHEMA}.inventory_balances
                    WHERE inventory_item_id = :i AND location_id = :l AND status = :s
                    """  # noqa: S608
                ),
                {"i": item_id, "l": location_id, "s": status},
            )
        ).scalar_one()
    return int(row)
