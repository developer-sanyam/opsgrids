"""`GET /operations/inventory/{item_id}/movements` for an item whose history holds the relocation
moves Phase 1 introduced.

The route's response type once pinned `reference_type` to the single literal `purchase_order`, so
the first bin transfer (or status change, roll split, ... or put-away) of an item turned that item's
movements tab into an HTTP 500 — found by the put-away "no coordinate in any response" sweep, which
reads every route that shows a move. A move's reference is free text (ADR-0014 §12's vocabulary);
the route has to say so.
"""

from __future__ import annotations

import pytest

from tests.putaway_fixtures import (
    PUTAWAY,
    PutawayWorld,
    build_putaway_world,
    call,
    put,
    putaway_body,
)

pytestmark = pytest.mark.integration

ITEM_READ = "operations.inventory.item.read:ORG"
TRANSFERS = "/operations/inventory/stock/transfers"
STATUS = "/operations/inventory/stock/status-changes"
STOCK = ["operations.inventory.stock.transfer:ORG", "operations.inventory.stock.adjust:ORG"]


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def movements(client, pw: PutawayWorld, item) -> list[dict]:  # type: ignore[no-untyped-def, type-arg]
    resp = await call(
        client, pw.org_id, [ITEM_READ], "GET", f"/operations/inventory/{item}/movements"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]  # type: ignore[no-any-return]


async def test_an_item_with_a_bin_transfer_still_lists_its_movements(client, pw) -> None:
    moved = await call(
        client,
        pw.org_id,
        STOCK,
        "POST",
        TRANSFERS,
        json={
            "inventory_item_id": str(pw.dye),
            "from_location_id": str(pw.receiving),
            "to_location_id": str(pw.rack_bin),
            "quantity": "1",
        },
    )
    assert moved.status_code == 201
    rows = await movements(client, pw, pw.dye)
    assert {r["reference_type"] for r in rows} == {"bin_transfer", "opening_stock"}


async def test_an_item_with_a_put_away_still_lists_its_movements(client, pw) -> None:
    await put(client, pw, putaway_body(pw))
    rows = await movements(client, pw, pw.dye)
    assert "putaway" in {r["reference_type"] for r in rows}
    assert {r["kind"] for r in rows} == {"opening", "relocation"}


async def test_an_item_with_a_status_change_still_lists_its_movements(client, pw) -> None:
    changed = await call(
        client,
        pw.org_id,
        STOCK,
        "POST",
        STATUS,
        json={
            "inventory_item_id": str(pw.dye),
            "location_id": str(pw.receiving),
            "from_status": "AVAILABLE",
            "to_status": "QUALITY_HOLD",
            "quantity": "1",
        },
    )
    assert changed.status_code == 201, changed.text
    assert "status_change" in {r["reference_type"] for r in await movements(client, pw, pw.dye)}


async def test_a_tracked_rolls_item_lists_a_put_away_too(client, pw) -> None:
    body = putaway_body(
        pw,
        inventory_item_id=str(pw.fabric),
        quantity="82.5",
        lot_id=str(pw.w.lot),
        roll_id=str(pw.w.roll),
    )
    assert (await put(client, pw, body)).status_code == 201
    assert PUTAWAY  # the route under test is the put-away; the read is the item's own
    assert "putaway" in {r["reference_type"] for r in await movements(client, pw, pw.fabric)}
