"""The stock and ledger read APIs (cursor-paginated, filterable) and location deletion
(inventory Phase 1, I5).

Authorization and cross-tenant behaviour are `test_tracking_authz.py`'s.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.tracking_fixtures import (
    API,
    LOCATION_WRITE,
    STOCK_ADJUST,
    STOCK_READ,
    STOCK_TRANSFER,
    World,
    bucket_qty,
    build_world,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

STOCK = "/operations/inventory/stock"
LEDGER = "/operations/inventory/ledger"
OPENING = "/operations/inventory/stock/opening"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def call(client, org_id, perm, method, path, body=None, params=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, [perm])):
        return await client.request(method, API + path, json=body, params=params)


async def open_dye(client, org_id, w: World, kilos: str, on: str, location=None) -> None:  # type: ignore[no-untyped-def]
    response = await call(
        client,
        org_id,
        STOCK_ADJUST,
        "POST",
        OPENING,
        {
            "lines": [
                {
                    "inventory_item_id": str(w.t.dye),
                    "location_id": str(location or w.t.weaving),
                    "quantity": kilos,
                }
            ],
            "occurred_on": on,
        },
    )
    assert response.status_code == 201, response.text


async def walk(client, org_id, path, params, limit):  # type: ignore[no-untyped-def]
    """Every row of a cursor-paginated list, page by page; returns (rows, pages)."""
    rows: list[dict] = []  # type: ignore[type-arg]
    cursor = None
    pages = 0
    while True:
        page = (
            await call(
                client,
                org_id,
                STOCK_READ,
                "GET",
                path,
                params={**params, "limit": limit, **({"cursor": cursor} if cursor else {})},
            )
        ).json()
        rows += page["items"]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            return rows, pages


class TestStock:
    async def test_stock_by_item_location_lot_and_roll(self, client, org_id, world) -> None:
        w = world
        await open_dye(client, org_id, w, "4", "2026-09-01", location=w.bin_b)
        everything = (await call(client, org_id, STOCK_READ, "GET", STOCK)).json()["items"]
        assert {(r["sku"], r["location_code"], r["on_hand_qty"]) for r in everything} == {
            ("COT-180-NAVY", "DEFAULT", "82.5"),
            ("DYE-RED-01", "BIN-B", "4"),
        }
        by_roll = (
            await call(client, org_id, STOCK_READ, "GET", STOCK, params={"roll_id": str(w.roll)})
        ).json()["items"]
        [row] = by_roll
        assert (row["roll_no"], row["lot_no"], row["status"]) == ("R-1", "LOT-A", "AVAILABLE")
        assert (row["on_hand_qty"], row["reserved_qty"], row["available_qty"]) == (
            "82.5",
            "0",
            "82.5",
        )
        by_lot = (
            await call(client, org_id, STOCK_READ, "GET", STOCK, params={"lot_id": str(w.lot)})
        ).json()["items"]
        assert [r["roll_id"] for r in by_lot] == [str(w.roll)]
        by_location = (
            await call(
                client, org_id, STOCK_READ, "GET", STOCK, params={"location_id": str(w.bin_b)}
            )
        ).json()["items"]
        assert [r["sku"] for r in by_location] == ["DYE-RED-01"]
        by_item = (
            await call(client, org_id, STOCK_READ, "GET", STOCK, params={"item_id": str(w.t.dye)})
        ).json()["items"]
        assert len(by_item) == 1
        by_warehouse = (
            await call(
                client,
                org_id,
                STOCK_READ,
                "GET",
                STOCK,
                params={"warehouse_id": str(w.warehouse_weave)},
            )
        ).json()["items"]
        assert len(by_warehouse) == 2

    async def test_status_filter_and_empty_buckets(self, client, org_id, world) -> None:
        w = world
        moved = await call(
            client,
            org_id,
            STOCK_TRANSFER,
            "POST",
            "/operations/inventory/stock/transfers",
            {
                "inventory_item_id": str(w.t.fabric),
                "from_location_id": str(w.t.weaving),
                "to_location_id": str(w.bin_b),
                "quantity": "82.5",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
        )
        assert moved.status_code == 201
        # The source bucket is empty now: hidden by default, shown on request.
        default = (await call(client, org_id, STOCK_READ, "GET", STOCK)).json()["items"]
        assert [r["location_id"] for r in default] == [str(w.bin_b)]
        with_empty = (
            await call(client, org_id, STOCK_READ, "GET", STOCK, params={"include_empty": "true"})
        ).json()["items"]
        assert len(with_empty) == 2
        held = (
            await call(client, org_id, STOCK_READ, "GET", STOCK, params={"status": "QUALITY_HOLD"})
        ).json()["items"]
        assert held == []
        bad = await call(client, org_id, STOCK_READ, "GET", STOCK, params={"status": "NOPE"})
        assert bad.status_code == 422

    async def test_cursor_pagination_visits_every_bucket_once(self, client, org_id, world) -> None:
        w = world
        for n in range(5):
            await open_dye(client, org_id, w, f"{n + 1}", "2026-09-01")
            # Different statuses make different buckets of the same item and place.
        for status in ("QUALITY_HOLD", "QUARANTINE", "DAMAGED"):
            response = await call(
                client,
                org_id,
                STOCK_ADJUST,
                "POST",
                OPENING,
                {
                    "lines": [
                        {
                            "inventory_item_id": str(w.t.dye),
                            "location_id": str(w.t.weaving),
                            "quantity": "1",
                            "status": status,
                        }
                    ]
                },
            )
            assert response.status_code == 201
        rows, pages = await walk(client, org_id, STOCK, {}, limit=2)
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)) == 5  # roll bucket, dye AVAILABLE, and three holds
        assert pages == 3


class TestLedger:
    async def _history(self, client, org_id, w: World) -> None:
        await open_dye(client, org_id, w, "10", "2026-09-01")
        await open_dye(client, org_id, w, "5", "2026-09-10")
        await open_dye(client, org_id, w, "2", "2026-09-20", location=w.bin_b)

    async def test_the_org_wide_ledger_filters_by_item_location_kind_and_dates(
        self, client, org_id, world
    ) -> None:
        w = world
        await self._history(client, org_id, w)

        async def rows(**params):  # type: ignore[no-untyped-def]
            response = await call(client, org_id, STOCK_READ, "GET", LEDGER, params=params)
            assert response.status_code == 200, response.text
            return response.json()["items"]

        assert len(await rows()) == 4  # the roll's opening and the three dye openings
        dye = await rows(item_id=str(w.t.dye))
        assert [r["quantity"] for r in dye] == ["2", "5", "10"]  # newest first
        assert [
            r["quantity"] for r in await rows(item_id=str(w.t.dye), location_id=str(w.bin_b))
        ] == ["2"]
        window = await rows(item_id=str(w.t.dye), from_date="2026-09-05", to_date="2026-09-15")
        assert [r["quantity"] for r in window] == ["5"]
        inclusive = await rows(item_id=str(w.t.dye), from_date="2026-09-10", to_date="2026-09-10")
        assert [r["quantity"] for r in inclusive] == ["5"]
        assert len(await rows(kind="opening")) == 4
        assert await rows(kind="issue") == []
        assert [r["roll_id"] for r in await rows(roll_id=str(w.roll))] == [str(w.roll)]
        assert [r["lot_id"] for r in await rows(lot_id=str(w.lot))] == [str(w.lot)]
        first = (await rows(item_id=str(w.t.dye)))[-1]
        assert first["reference_type"] == "opening_stock"
        assert (first["sku"], first["uom"], first["location_code"]) == (
            "DYE-RED-01",
            "kg",
            "DEFAULT",
        )
        assert first["balance_after"] == "10"

    @pytest.mark.parametrize(
        "params",
        [
            {"kind": "teleport"},
            {"status": "NOPE"},
            {"from_date": "yesterday"},
            {"item_id": "not-a-uuid"},
        ],
    )
    async def test_bad_filters_are_422(self, client, org_id, world, params) -> None:
        response = await call(client, org_id, STOCK_READ, "GET", LEDGER, params=params)
        assert response.status_code == 422

    async def test_cursor_pagination_is_complete_and_stable(self, client, org_id, world) -> None:
        w = world
        await self._history(client, org_id, w)
        everything = (
            await call(client, org_id, STOCK_READ, "GET", LEDGER, params={"limit": 200})
        ).json()
        assert everything["next_cursor"] is None
        paged, pages = await walk(client, org_id, LEDGER, {}, limit=3)
        assert [r["id"] for r in paged] == [r["id"] for r in everything["items"]]
        assert pages == 2

    async def test_a_forged_cursor_cannot_widen_the_result(self, client, org_id, world) -> None:
        forged = (
            base64.urlsafe_b64encode(
                json.dumps(
                    ["dt", "2999-01-01T00:00:00+00:00", "00000000-0000-0000-0000-000000000000"]
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        # A cursor only moves the window; it carries no tenant, so another tenant sees nothing.
        outsider = principal_for(uuid.uuid4(), [STOCK_READ])
        with as_principal(outsider):
            response = await client.get(API + LEDGER, params={"cursor": forged})
        assert response.status_code == 200 and response.json()["items"] == []
        for junk in ("!!!", "e30", "WyJkdCIsImJhZCIsIngiXQ"):
            bad = await call(client, org_id, STOCK_READ, "GET", LEDGER, params={"cursor": junk})
            assert bad.status_code == 422, junk


class TestDeleteLocation:
    async def test_an_unused_bin_is_deleted_and_disappears(self, client, org_id, world) -> None:
        w = world
        gone = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{w.bin_b}"
        )
        assert gone.status_code == 204
        again = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{w.bin_b}"
        )
        assert again.status_code == 404
        # And stock can no longer be put there.
        refused = await call(
            client,
            org_id,
            STOCK_TRANSFER,
            "POST",
            "/operations/inventory/stock/transfers",
            {
                "inventory_item_id": str(w.t.fabric),
                "from_location_id": str(w.t.weaving),
                "to_location_id": str(w.bin_b),
                "quantity": "1",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
        )
        assert refused.status_code == 404

    async def test_a_bin_holding_stock_is_refused(self, client, org_id, world) -> None:
        w = world
        await open_dye(client, org_id, w, "3", "2026-09-01", location=w.bin_b)
        response = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{w.bin_b}"
        )
        assert response.status_code == 409
        assert "history" in response.json()["detail"]

    async def test_a_bin_that_once_held_stock_keeps_its_history(
        self, client, session, org_id, world
    ) -> None:
        w = world
        await open_dye(client, org_id, w, "3", "2026-09-01", location=w.bin_b)
        moved = await call(
            client,
            org_id,
            STOCK_TRANSFER,
            "POST",
            "/operations/inventory/stock/transfers",
            {
                "inventory_item_id": str(w.t.dye),
                "from_location_id": str(w.bin_b),
                "to_location_id": str(w.t.weaving),
                "quantity": "3",
            },
        )
        assert moved.status_code == 201
        assert (await bucket_qty(session, w, item=w.t.dye, location=w.bin_b))[0] == 0
        response = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{w.bin_b}"
        )
        assert response.status_code == 409  # empty now, but the ledger remembers it

    async def test_the_default_bin_is_refused(self, client, org_id, world) -> None:
        response = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{world.t.weaving}"
        )
        assert response.status_code == 409
        assert "default" in response.json()["detail"]

    async def test_a_bin_with_children_is_refused(self, client, org_id, world) -> None:
        w = world
        child = await call(
            client,
            org_id,
            LOCATION_WRITE,
            "POST",
            f"/operations/warehouses/{w.warehouse_weave}/locations",
            {"kind": "bin", "code": "BIN-CHILD", "name": "Child", "parent_id": str(w.bin_b)},
        )
        assert child.status_code == 201, child.text
        response = await call(
            client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{w.bin_b}"
        )
        assert response.status_code == 409

    async def test_the_deleted_event_matches_its_schema(
        self, client, session, org_id, world
    ) -> None:
        await call(client, org_id, LOCATION_WRITE, "DELETE", f"/operations/locations/{world.bin_b}")
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.location.deleted"
                    )
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )


async def test_a_cursor_from_another_list_or_without_a_zone_is_a_422_never_a_500(
    client, org_id, world
) -> None:
    w = world
    await open_dye(client, org_id, w, "4", "2026-09-01")
    stock_page = (await call(client, org_id, STOCK_READ, "GET", STOCK, params={"limit": 1})).json()
    ledger_page = (
        await call(client, org_id, STOCK_READ, "GET", LEDGER, params={"limit": 1})
    ).json()
    assert stock_page["next_cursor"] and ledger_page["next_cursor"]
    # Each list's cursor is only good for that list.
    on_ledger = await call(
        client, org_id, STOCK_READ, "GET", LEDGER, params={"cursor": stock_page["next_cursor"]}
    )
    on_stock = await call(
        client, org_id, STOCK_READ, "GET", STOCK, params={"cursor": ledger_page["next_cursor"]}
    )
    assert (on_ledger.status_code, on_stock.status_code) == (422, 422)
    naive = (
        base64.urlsafe_b64encode(
            json.dumps(["dt", "2026-01-01T00:00:00", str(uuid.uuid4())]).encode()
        )
        .decode()
        .rstrip("=")
    )
    refused = await call(client, org_id, STOCK_READ, "GET", LEDGER, params={"cursor": naive})
    assert refused.status_code == 422
