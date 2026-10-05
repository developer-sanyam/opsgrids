"""A thin end-to-end pass over the backend-core pieces of storage put-away, find-stock, the
published floor layout and the warehouse map — enough to prove each piece runs through the real
routes. The exhaustive matrix (validation, idempotency, geo table, authz, cross-tenant, audit
scans) lives in the dedicated test modules, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.inventory import services as inv_services
from app.inventory import stock_ops
from app.inventory.tracking_schemas import OpeningLineIn, OpeningStockIn
from app.models import (
    AuditLog,
    InventoryMove,
    LayoutVersion,
    LocationPlacement,
    OutboxEvent,
    PutawayCapture,
    Warehouse,
    WarehouseFloor,
)
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import TODAY
from tests.tracking_fixtures import API, STOCK_READ, STOCK_TRANSFER, World, build_world

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

REVEAL_PERM = "operations.inventory.putaway.location_read:ORG"
REVEAL_AMR = ["pwd", "otp"]  # the reveal route also demands a session that gave a one-time code
LAYOUT_READ = "operations.warehouse.layout.read:ORG"
WAREHOUSE_READ = "operations.inventory.warehouse.read:ORG"
WAREHOUSE_WRITE = "operations.inventory.warehouse.write:ORG"
FLOOR_WRITE = "operations.warehouse.floor.write:ORG"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def _receiving_and_rack(session, w: World):  # type: ignore[no-untyped-def]
    """A receiving bin holding 20 kg of dye, and a barcoded rack bin to put it into."""
    async with session.begin():
        warehouse = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == w.org_id, Warehouse.code == "WH-WEAVE"
                )
            )
        ).scalar_one()
        receiving = await inv_services.create_location(
            session, w.t.ctx, warehouse, kind="receiving", code="RCV-1", name="Receiving"
        )
        rack_bin = await inv_services.create_location(
            session,
            w.t.ctx,
            warehouse,
            kind="bin",
            code="R1-B01",
            name="Rack 1 bin 1",
            barcode="RACK-1-B01",
            pick_sequence=1,
        )
        await stock_ops.post_opening_stock(
            session,
            w.t.ctx,
            OpeningStockIn(
                lines=[
                    OpeningLineIn(
                        inventory_item_id=w.t.dye, location_id=receiving.id, quantity="20"
                    )
                ],
                occurred_on=TODAY,
            ),
            idempotency_key=None,
            occurred_on_explicit=False,
        )
        return receiving.id, rack_bin.id, warehouse.id


def body(w: World, receiving, **extra) -> dict:  # type: ignore[type-arg, no-untyped-def]
    return {
        "inventory_item_id": str(w.t.dye),
        "from_location_id": str(receiving),
        "to_barcode": "RACK-1-B01",
        "quantity": "5",
        **extra,
    }


async def test_putaway_end_to_end(client, session, org_id, world) -> None:
    w = world
    receiving, rack_bin, warehouse_id = await _receiving_and_rack(session, w)

    with as_principal(principal_for(org_id, [STOCK_TRANSFER])):
        suggest = await client.get(
            f"{API}/operations/inventory/putaway/suggest",
            params={"inventory_item_id": str(w.t.dye), "warehouse_id": str(warehouse_id)},
        )
    assert suggest.status_code == 200
    assert suggest.json()["suggestion"]["location_id"] == str(rack_bin)
    assert suggest.json()["suggestion"]["reason"] == "empty_bin"

    with as_principal(principal_for(org_id, [STOCK_TRANSFER])):
        first = await client.post(
            f"{API}/operations/inventory/putaway",
            json=body(
                w,
                receiving,
                suggested_location_id=str(rack_bin),
                lat_micro=12_971_600,
                lng_micro=77_594_600,
                accuracy_m=8,
            ),
            headers={"Idempotency-Key": "pa-1"},
        )
        again = await client.post(
            f"{API}/operations/inventory/putaway",
            json=body(w, receiving),
            headers={"Idempotency-Key": "pa-1"},
        )
    assert first.status_code == 201, first.text
    assert again.status_code == 200, again.text
    out = first.json()
    assert out["matched_suggestion"] is True and out["geo_recorded"] is True
    assert out["warnings"] == [] and out["to_location"]["code"] == "R1-B01"
    assert "lat_micro" not in first.text and "12971600" not in first.text
    assert again.json()["move_id"] == out["move_id"] and again.json()["replayed"] is True

    async with session.begin():
        move = (
            await session.execute(select(InventoryMove).where(InventoryMove.id == out["move_id"]))
        ).scalar_one()
        captures = (
            await session.execute(select(func.count()).select_from(PutawayCapture))
        ).scalar_one()
        putaway_events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.putaway.recorded"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert move.reference_type == "putaway" and move.kind == "relocation"
    assert captures == 1 and len(putaway_events) == 1
    assert "lat_micro" not in putaway_events[0].payload
    validate_event_schema(
        putaway_events[0].event_type,
        1,
        dict(putaway_events[0].payload),
        contracts_root=CONTRACTS_ROOT,
    )

    with as_principal(principal_for(org_id, [STOCK_READ])):
        detail = await client.get(f"{API}/operations/inventory/putaway/{out['move_id']}")
        located = await client.get(
            f"{API}/operations/inventory/locate", params={"inventory_item_id": str(w.t.dye)}
        )
        refused = await client.post(
            f"{API}/operations/inventory/putaway/{out['move_id']}/location:reveal"
        )
    assert detail.status_code == 200 and detail.json()["has_location"] is True
    assert "lat_micro" not in detail.text
    assert refused.status_code == 403
    with as_principal(principal_for(org_id, [STOCK_TRANSFER])):
        holds = await client.get(
            f"{API}/operations/inventory/putaway/suggest",
            params={"inventory_item_id": str(w.t.dye), "warehouse_id": str(warehouse_id)},
        )
    assert holds.json()["suggestion"]["reason"] == "holds_item"
    assert holds.json()["suggestion"]["path"] == "R1-B01"
    rows = {r["code"]: r for r in located.json()["results"]}
    assert rows["R1-B01"]["quantity"] == "5.000" or rows["R1-B01"]["quantity"] == "5"
    assert rows["R1-B01"]["placement_reason"] == "no_permission"

    with as_principal(principal_for(org_id, [REVEAL_PERM], amr=REVEAL_AMR)):
        revealed = await client.post(
            f"{API}/operations/inventory/putaway/{out['move_id']}/location:reveal"
        )
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "no-store"
    assert (revealed.json()["lat_micro"], revealed.json()["lng_micro"]) == (12_971_600, 77_594_600)
    async with session.begin():
        reads = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            )
            .scalars()
            .all()
        )
    assert len(reads) == 1 and reads[0].after == {
        "fields": ["lat_micro", "lng_micro", "accuracy_m"]
    }


async def test_putaway_refuses_a_source_that_is_not_receiving_or_staging(
    client, session, org_id, world
) -> None:
    w = world
    _, _, _ = await _receiving_and_rack(session, w)
    with as_principal(principal_for(org_id, [STOCK_TRANSFER])):
        resp = await client.post(
            f"{API}/operations/inventory/putaway",
            json=body(w, w.t.weaving),
            headers={"Idempotency-Key": "pa-bad"},
        )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["code"] == "source_not_receiving_or_staging"


async def test_published_layout_of_an_unpublished_floor_and_the_warehouse_map(
    client, session, org_id, world
) -> None:
    w = world
    with as_principal(principal_for(org_id, [FLOOR_WRITE])):
        floor = await client.post(
            f"{API}/operations/warehouses/{w.warehouse_weave}/floors",
            json={"floor_number": 1, "name": "Ground"},
        )
    assert floor.status_code == 201
    with as_principal(principal_for(org_id, [LAYOUT_READ])):
        layout = await client.get(f"{API}/operations/floors/{floor.json()['id']}/published-layout")
    assert layout.json() == {
        "floor_id": floor.json()["id"],
        "warehouse_id": str(w.warehouse_weave),
        "floor_name": "Ground",
        "published": False,
        "layout_version_id": None,
        "placements": [],
    }

    with as_principal(principal_for(org_id, [WAREHOUSE_WRITE])):
        patched = await client.patch(
            f"{API}/operations/warehouses/{w.warehouse_weave}",
            json={"lat_micro": 12_971_600, "lng_micro": 77_594_600},
        )
        half = await client.patch(
            f"{API}/operations/warehouses/{w.warehouse_weave}", json={"lat_micro": 95_000_000}
        )
    assert patched.status_code == 200 and patched.json()["lat_micro"] == 12_971_600
    assert half.status_code == 422
    with as_principal(principal_for(org_id, [WAREHOUSE_READ])):
        site_map = await client.get(f"{API}/operations/warehouses/map")
    assert site_map.status_code == 200
    data = site_map.json()
    assert [s["code"] for s in data["sites"]] == ["WH-WEAVE"]
    assert data["sites"][0]["floor_count"] == 1
    assert [s["code"] for s in data["without_coordinates"]] == ["WH-FIN"]


async def test_locate_joins_the_published_layout_and_falls_back_to_an_ancestor(
    client, session, org_id, world
) -> None:
    w = world
    async with session.begin():
        warehouse = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == w.org_id, Warehouse.code == "WH-WEAVE"
                )
            )
        ).scalar_one()
        rack = await inv_services.create_location(
            session, w.t.ctx, warehouse, kind="rack", code="RACK-1", name="Rack 1"
        )
        placed_bin = await inv_services.create_location(
            session,
            w.t.ctx,
            warehouse,
            kind="bin",
            code="R1-B01",
            name="B1",
            parent_id=str(rack.id),
        )
        loose_bin = await inv_services.create_location(
            session, w.t.ctx, warehouse, kind="bin", code="Z-LOOSE", name="Loose"
        )
        floor = WarehouseFloor(
            organization_id=w.org_id, warehouse_id=warehouse.id, floor_number=1, name="Ground"
        )
        session.add(floor)
        await session.flush()
        published = LayoutVersion(
            organization_id=w.org_id,
            warehouse_floor_id=floor.id,
            version_number=1,
            status="published",
        )
        draft = LayoutVersion(
            organization_id=w.org_id,
            warehouse_floor_id=floor.id,
            version_number=2,
            status="draft",
        )
        session.add_all([published, draft])
        await session.flush()
        session.add_all(
            [
                LocationPlacement(
                    organization_id=w.org_id,
                    layout_version_id=published.id,
                    location_id=rack.id,
                    x_mm=1000,
                    y_mm=2000,
                    width_mm=3000,
                    depth_mm=1200,
                ),
                # A draft's placement of the loose bin must never surface.
                LocationPlacement(
                    organization_id=w.org_id,
                    layout_version_id=draft.id,
                    location_id=loose_bin.id,
                    x_mm=0,
                    y_mm=0,
                    width_mm=500,
                    depth_mm=500,
                ),
            ]
        )
        for loc in (placed_bin.id, loose_bin.id):
            await stock_ops.post_opening_stock(
                session,
                w.t.ctx,
                OpeningStockIn(
                    lines=[OpeningLineIn(inventory_item_id=w.t.dye, location_id=loc, quantity="3")],
                    occurred_on=TODAY,
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        ids = (floor.id, published.id, rack.id)

    with as_principal(principal_for(org_id, [STOCK_READ, LAYOUT_READ])):
        located = await client.get(
            f"{API}/operations/inventory/locate", params={"inventory_item_id": str(w.t.dye)}
        )
    assert located.status_code == 200, located.text
    rows = {r["code"]: r for r in located.json()["results"]}
    by_ancestor = rows["R1-B01"]
    assert by_ancestor["path"] == "RACK-1 / R1-B01"
    assert by_ancestor["placement"]["source"] == "ancestor"
    assert by_ancestor["placement"]["ancestor_location_id"] == str(ids[2])
    assert by_ancestor["placement"]["layout_version_id"] == str(ids[1])
    assert by_ancestor["floor_id"] == str(ids[0]) and by_ancestor["placement_reason"] is None
    assert rows["Z-LOOSE"]["placement"] is None
    assert rows["Z-LOOSE"]["placement_reason"] == "unplaced"
