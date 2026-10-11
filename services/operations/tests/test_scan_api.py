"""`GET /operations/inventory/scan/{payload}` and the barcode columns it resolves through
(inventory Phase 9, ADR-0027): every entity type, printed barcode vs natural key, bare codes,
ambiguity, authorization, cross-tenant 404, and barcode uniqueness on create / patch.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

import pytest

from tests.helpers import as_principal, principal_for
from tests.receiving_fixtures import PO_WRITE, RECEIPT_WRITE, RECEIVE_PERMS, STOCK_RECEIVE
from tests.tracking_fixtures import (
    API,
    LOT_WRITE,
    ROLL_WRITE,
    STOCK_ADJUST,
    STOCK_READ,
    STOCK_TRANSFER,
    World,
    build_world,
)

pytestmark = pytest.mark.integration

SCAN = "/operations/inventory/scan/"
ITEM_WRITE = "operations.inventory.item.write:ORG"
LOCATION_WRITE = "operations.inventory.location.write:ORG"
COUNT_EXECUTE = "operations.inventory.count.execute:ORG"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def scan(client, org_id, code: str, perms=(STOCK_READ,)):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, list(perms))):
        return await client.get(API + SCAN + quote(code, safe=":"))


async def patch(client, org_id, perm, path, body):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, [perm])):
        return await client.patch(API + path, json=body)


class TestResolve:
    async def test_item_by_sku(self, client, world) -> None:
        response = await scan(client, world.org_id, "og:item:COT-180-NAVY")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["type"] == "item" and body["payload"] == "og:item:COT-180-NAVY"
        assert body["entity"]["inventory_item_id"] == str(world.t.fabric)
        assert body["entity"]["track_roll"] is True and body["entity"]["uom"] == "m"
        assert body["entity"]["on_hand_qty"] == "82.5"

    async def test_the_response_carries_no_cost_or_price(self, client, world) -> None:
        body = (await scan(client, world.org_id, "og:item:COT-180-NAVY")).json()
        text = str(body)
        assert "unit_value" not in text and "selling_price" not in text

    async def test_lot_roll_and_location(self, client, world) -> None:
        lot = (await scan(client, world.org_id, "og:lot:LOT-A")).json()
        assert lot["type"] == "lot" and lot["entity"]["lot_id"] == str(world.lot)
        roll = (await scan(client, world.org_id, "og:roll:R-1")).json()
        assert roll["entity"]["roll_id"] == str(world.roll)
        assert roll["entity"]["lot_no"] == "LOT-A" and roll["entity"]["on_hand_qty"] == "82.5"

    async def test_location_by_barcode(self, client, world) -> None:
        r = await patch(
            client, world.org_id, LOCATION_WRITE, f"/operations/locations/{world.bin_b}",
            {"barcode": "BINB-001"},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        body = (await scan(client, world.org_id, "og:location:BINB-001")).json()
        assert body["type"] == "location" and body["entity"]["location_id"] == str(world.bin_b)

    async def test_a_printed_barcode_resolves_before_the_natural_key(self, client, world) -> None:
        r = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.dye}",
            {"barcode": "8901234567890"},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        assert r.json()["barcode"] == "8901234567890"
        body = (await scan(client, world.org_id, "og:item:8901234567890")).json()
        assert body["entity"]["sku"] == "DYE-RED-01"
        assert body["payload"] == "og:item:8901234567890"
        # ... and a bare (unprefixed) scan of the same code finds it too.
        bare = (await scan(client, world.org_id, "8901234567890")).json()
        assert bare["type"] == "item" and bare["entity"]["sku"] == "DYE-RED-01"

    async def test_a_bare_natural_key_resolves_when_only_one_type_has_it(
        self, client, world
    ) -> None:
        assert (await scan(client, world.org_id, "R-1")).json()["type"] == "roll"

    async def test_surrounding_whitespace_from_a_scanner_is_dropped(self, client, world) -> None:
        response = await scan(client, world.org_id, "  og:lot:LOT-A\n")
        assert response.status_code == 200, response.text

    async def test_an_unknown_code_is_404(self, client, world) -> None:
        assert (await scan(client, world.org_id, "og:item:NOPE")).status_code == 404
        assert (await scan(client, world.org_id, "NOPE-123")).status_code == 404

    async def test_a_malformed_payload_is_422(self, client, world) -> None:
        for bad in ("og:box:X", "og:item:", "og:item:caf\u00e9"):
            response = await scan(client, world.org_id, bad)
            assert response.status_code == 422, (bad, response.text)

    async def test_a_code_matching_two_types_is_ambiguous_and_refused(self, client, world) -> None:
        # A lot numbered like an existing roll: a bare scan of it cannot be told apart.
        with as_principal(principal_for(world.org_id, [LOT_WRITE])):
            made = await client.post(
                API + "/operations/inventory/lots",
                json={"inventory_item_id": str(world.t.fabric), "lot_no": "R-1"},
            )
        assert made.status_code == 201, made.text
        assert (await scan(client, world.org_id, "R-1")).status_code == 409
        # The labelled form is unambiguous.
        assert (await scan(client, world.org_id, "og:roll:R-1")).status_code == 200

    async def test_a_barcode_equal_to_another_rows_natural_key_is_ambiguous(
        self, client, world
    ) -> None:
        r = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.dye}",
            {"barcode": "BTN-14MM"},
        )  # fmt: skip
        assert r.status_code == 200
        assert (await scan(client, world.org_id, "og:item:BTN-14MM")).status_code == 409


class TestCodesWithAwkwardCharacters:
    async def test_a_lot_number_with_a_slash_resolves_through_the_path(self, client, world) -> None:
        with as_principal(principal_for(world.org_id, [LOT_WRITE])):
            made = await client.post(
                API + "/operations/inventory/lots",
                json={"inventory_item_id": str(world.t.fabric), "lot_no": "LOT-2026/10-A"},
            )
        assert made.status_code == 201, made.text
        response = await scan(client, world.org_id, "og:lot:LOT-2026/10-A")
        assert response.status_code == 200, response.text
        assert response.json()["entity"]["lot_no"] == "LOT-2026/10-A"
        assert response.json()["payload"] == "og:lot:LOT-2026/10-A"

    async def test_a_barcode_with_colons_round_trips(self, client, world) -> None:
        r = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.dye}",
            {"barcode": "A:B:C"},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        body = (await scan(client, world.org_id, "og:item:A:B:C")).json()
        assert body["entity"]["sku"] == "DYE-RED-01"


class TestAllowedActions:
    async def test_receive_follows_the_receiving_permissions_not_purchase_order_write(
        self, client, world
    ) -> None:
        async def actions(*perms: str) -> list[str]:
            body = (await scan(client, world.org_id, "og:item:COT-180-NAVY", perms=perms)).json()
            return body["allowed_actions"]

        assert "receive" in await actions(STOCK_READ, *RECEIVE_PERMS)
        assert "receive" not in await actions(STOCK_READ, PO_WRITE)
        assert "receive" not in await actions(STOCK_READ, RECEIPT_WRITE)
        assert "receive" not in await actions(STOCK_READ, STOCK_RECEIVE)

    async def test_actions_follow_the_callers_permissions(self, client, world) -> None:
        bare = (await scan(client, world.org_id, "og:roll:R-1")).json()
        assert bare["allowed_actions"] == ["view"]
        rich = (
            await scan(
                client,
                world.org_id,
                "og:roll:R-1",
                perms=(STOCK_READ, STOCK_TRANSFER, STOCK_ADJUST),
            )
        ).json()
        assert rich["allowed_actions"] == ["view", "transfer", "status_change", "issue"]

    async def test_a_location_offers_count_not_transfer(self, client, world) -> None:
        await patch(
            client, world.org_id, LOCATION_WRITE, f"/operations/locations/{world.bin_b}",
            {"barcode": "BINB-002"},
        )  # fmt: skip
        body = (
            await scan(
                client,
                world.org_id,
                "og:location:BINB-002",
                perms=(STOCK_READ, STOCK_TRANSFER, COUNT_EXECUTE),
            )
        ).json()
        assert body["allowed_actions"] == ["view", "count"]

    async def test_actions_for_unbuilt_phases_need_permissions_nobody_holds_yet(
        self, client, world
    ) -> None:
        body = (
            await scan(
                client,
                world.org_id,
                "og:item:COT-180-NAVY",
                perms=(STOCK_READ, "operations.quality.inspection.inspect:ORG"),
            )
        ).json()
        # The server reports it; the SPA additionally hides it unless the route exists.
        assert "inspect" in body["allowed_actions"]


class TestAuthorization:
    async def test_no_permission_is_403(self, client, world) -> None:
        response = await scan(client, world.org_id, "og:item:COT-180-NAVY", perms=())
        assert response.status_code == 403

    async def test_every_other_inventory_permission_is_403(self, client, world) -> None:
        others = (STOCK_TRANSFER, STOCK_ADJUST, LOT_WRITE, ROLL_WRITE, ITEM_WRITE, COUNT_EXECUTE)
        response = await scan(client, world.org_id, "og:item:COT-180-NAVY", perms=others)
        assert response.status_code == 403

    async def test_another_tenant_gets_404_for_every_type(self, client, world) -> None:
        await patch(
            client, world.org_id, LOCATION_WRITE, f"/operations/locations/{world.bin_b}",
            {"barcode": "BINB-003"},
        )  # fmt: skip
        intruder = uuid.uuid4()
        for code in (
            "og:item:COT-180-NAVY",
            "og:lot:LOT-A",
            "og:roll:R-1",
            "og:location:BINB-003",
            "R-1",
        ):
            response = await scan(client, intruder, code)
            assert response.status_code == 404, (code, response.text)

    async def test_a_sku_two_tenants_share_resolves_each_to_its_own(
        self, client, world, admin_session
    ) -> None:
        other = await build_world(admin_session, uuid.uuid4())
        mine = (await scan(client, world.org_id, "og:item:COT-180-NAVY")).json()
        theirs = (await scan(client, other.org_id, "og:item:COT-180-NAVY")).json()
        assert mine["entity"]["id"] == str(world.t.fabric)
        assert theirs["entity"]["id"] == str(other.t.fabric)
        assert mine["entity"]["id"] != theirs["entity"]["id"]


class TestBarcodeColumns:
    async def test_item_barcode_is_unique_among_live_items(self, client, world) -> None:
        first = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.dye}",
            {"barcode": "EAN-1"},
        )  # fmt: skip
        assert first.status_code == 200
        second = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.buttons}",
            {"barcode": "EAN-1"},
        )  # fmt: skip
        assert second.status_code == 409, second.text
        assert second.json()["errors"][0]["field"] == "barcode"

    async def test_lot_and_roll_barcodes_are_unique_and_returned(self, client, world) -> None:
        with as_principal(principal_for(world.org_id, [LOT_WRITE, ROLL_WRITE])):
            lot = await client.post(
                API + "/operations/inventory/lots",
                json={
                    "inventory_item_id": str(world.t.fabric),
                    "lot_no": "LOT-B",
                    "barcode": "SUP-LOT-9",
                },
            )
            assert lot.status_code == 201 and lot.json()["barcode"] == "SUP-LOT-9"
            dup = await client.post(
                API + "/operations/inventory/lots",
                json={
                    "inventory_item_id": str(world.t.fabric),
                    "lot_no": "LOT-C",
                    "barcode": "SUP-LOT-9",
                },
            )
            assert dup.status_code == 409, dup.text
            roll = await client.post(
                API + "/operations/inventory/rolls",
                json={
                    "inventory_item_id": str(world.t.fabric),
                    "lot_id": str(world.lot),
                    "roll_no": "R-2",
                    "primary_qty": "10",
                    "barcode": "MILL-R-2",
                },
            )
            assert roll.status_code == 201 and roll.json()["barcode"] == "MILL-R-2"
            dup_roll = await client.post(
                API + "/operations/inventory/rolls",
                json={
                    "inventory_item_id": str(world.t.fabric),
                    "lot_id": str(world.lot),
                    "roll_no": "R-3",
                    "primary_qty": "10",
                    "barcode": "MILL-R-2",
                },
            )
            assert dup_roll.status_code == 409, dup_roll.text
        # And both resolve by the printed code.
        assert (await scan(client, world.org_id, "og:lot:SUP-LOT-9")).json()["entity"][
            "lot_no"
        ] == "LOT-B"
        assert (await scan(client, world.org_id, "og:roll:MILL-R-2")).json()["entity"][
            "title"
        ] == "R-2"

    async def test_a_barcode_may_not_contain_spaces(self, client, world) -> None:
        response = await patch(
            client, world.org_id, ITEM_WRITE, f"/operations/inventory/{world.t.dye}",
            {"barcode": "has space"},
        )  # fmt: skip
        assert response.status_code == 422

    async def test_two_tenants_may_use_the_same_barcode(self, client, world, admin_session) -> None:
        other = await build_world(admin_session, uuid.uuid4())
        for w in (world, other):
            r = await patch(
                client, w.org_id, ITEM_WRITE, f"/operations/inventory/{w.t.dye}",
                {"barcode": "SHARED-EAN"},
            )  # fmt: skip
            assert r.status_code == 200, r.text
