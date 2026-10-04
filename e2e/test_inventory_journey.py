"""Inventory Phase 1, end to end: item -> lot -> roll -> opening stock -> split -> ledger.

The Operations app's screens make exactly these calls, in this order, with these bodies: a
decimal-string quantity, an `Idempotency-Key` on every stock-moving write, `{items, next_cursor}`
paging on every read. This journey drives them over real HTTP against the real services, so the
contract the frontend was built against is the contract the backend serves, and the two cannot
drift apart behind a stub:

    1  an organization that has not bought Inventory is refused (402) at the gateway
    2  the owner activates Inventory; its roles install; the entitlement reaches the token
    3  a warehouse, a second warehouse, and bins in each
    4  a lot-and-roll-tracked item, a lot, a roll with a measured weight
    5  opening stock into the roll; a retried key replays instead of posting twice
    6  split the roll 50 + 32.5; weight apportioned by floor with the remainder on the last child
    7  an inexact split, a second split, an unmergeable merge, a merge that works
    8  an over-take and a cross-warehouse transfer are refused with the field and code the UI maps
    9  the ledger: paged on the cursor, filtered by roll, kind and date, naming each operation
    10 the trace walks child -> parent; a location with history cannot be deleted

Steps run in file order and share state through the `inv` fixture, because that is what a journey
is: step 6 cannot mean anything if step 5 did not happen.

Not run in CI by default (`-m e2e`, needs `make infra` and a migrated `opsgrids_wt`), like every
journey here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from test_journey import _claims, auth_header, login

pytestmark = pytest.mark.e2e

#: The product this journey activates. Its prefix is entitlement-gated at the gateway.
PRODUCT = "operations"

#: Long enough for product's relay to publish and account's consumer to install the roles on a
#: loaded laptop; short enough that a broken hop fails the suite rather than hanging it.
SETTLE = 45.0

BASE = "/api/v1/operations"


@pytest.fixture(scope="session")
def inv() -> dict[str, Any]:
    """State carried between the ordered steps — tokens, ids, what the last step proved."""
    return {}


def until(what: str, predicate: Callable[[], Any], *, timeout: float = SETTLE) -> Any:
    """Poll `predicate` until it returns something truthy. A condition, never an interval."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout:g}s waiting for {what}. Last saw: {last!r}")


def headers(inv: dict[str, Any], key: str | None = None) -> dict[str, str]:
    """The owner's bearer token, plus an `Idempotency-Key` when the call is a stock-moving write."""
    out = auth_header(inv["token"])
    if key is not None:
        out["Idempotency-Key"] = key
    return out


def errors_of(response: httpx.Response) -> list[dict[str, Any]]:
    body = response.json()
    assert isinstance(body, dict), body
    return list(body.get("errors", []))


def collect(
    client: httpx.Client, inv: dict[str, Any], path: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every row of a cursor-paged read, following `next_cursor` the way "Load more" does."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        query = {**params, "limit": 1, **({"cursor": cursor} if cursor else {})}
        response = client.get(f"{BASE}{path}", params=query, headers=headers(inv))
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text[:300]}"
        page = response.json()
        assert set(page) == {"items", "next_cursor"}, (
            f"{path} is not an {{items, next_cursor}} page"
        )
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows
    raise AssertionError(f"{path} never reached its last page")


class TestTheInventoryJourney:
    """Ordered. Each step depends on the one before it."""

    # ------------------------------------------------------------------ 1-2. entitlement

    def test_01_an_organization_that_has_not_bought_inventory_is_refused(
        self, client: httpx.Client, inventory_tenant: dict[str, Any], inv: dict[str, Any]
    ) -> None:
        inv["token"] = login(
            client,
            inventory_tenant["email"],
            inventory_tenant["password"],
            inventory_tenant["org_id"],
        )
        for path in (
            "/inventory/stock",
            "/inventory/ledger",
            "/inventory/lots",
            "/inventory/rolls",
        ):
            response = client.get(f"{BASE}{path}", headers=headers(inv))
            assert response.status_code == 402, (
                f"{path} answered {response.status_code} for an organization that never "
                "activated Inventory; the gateway's entitlement gate is not in front of it"
            )

    def test_02_the_owner_activates_inventory_and_its_roles_reach_the_token(
        self, client: httpx.Client, inventory_tenant: dict[str, Any], inv: dict[str, Any]
    ) -> None:
        response = client.post(
            "/api/v1/products/activations", json={"product_key": PRODUCT}, headers=headers(inv)
        )
        assert response.status_code == 201, (
            f"activation failed: {response.status_code} {response.text[:400]}. A 422 usually "
            f"means the registry still has {PRODUCT!r} marked 'soon' — see _seed_product.py."
        )

        def role_installed() -> bool:
            listing = client.get("/api/v1/roles", headers=headers(inv))
            if listing.status_code == 401:
                # Installing the roles changed the organization's grants, so the token minted
                # before it is stale (ADR-0000 §4). Re-sign-in once, as a client does — not on
                # every poll: anonymous sign-ins are rate-limited per client address.
                inv["token"] = login(
                    client,
                    inventory_tenant["email"],
                    inventory_tenant["password"],
                    inventory_tenant["org_id"],
                )
                return False
            return listing.status_code == 200 and "OPERATIONS_ADMIN" in {
                r["code"] for r in listing.json()
            }

        until(
            "Inventory's OPERATIONS_ADMIN role to be installed by account-service", role_installed
        )

        # A fresh sign-in: `ent` is computed at sign-in from what product-service says the
        # organization has, and this asserts what auth put in.
        inv["token"] = login(
            client,
            inventory_tenant["email"],
            inventory_tenant["password"],
            inventory_tenant["org_id"],
        )
        assert PRODUCT in _claims(inv["token"])["ent"]

    # ------------------------------------------------------------------ 3. places

    def test_03_two_warehouses_with_bins(self, client: httpx.Client, inv: dict[str, Any]) -> None:
        run = uuid.uuid4().hex[:6].upper()
        inv["run"] = run
        for key, name in (("a", "Bhiwandi"), ("b", "Surat")):
            warehouse = client.post(
                f"{BASE}/warehouses",
                headers=headers(inv),
                json={"code": f"{key.upper()}{run}", "name": f"{name} DC {run}"},
            )
            assert warehouse.status_code == 201, warehouse.text[:300]
            inv[f"wh_{key}"] = warehouse.json()
            for bin_code in ("01", "02"):
                location = client.post(
                    f"{BASE}/warehouses/{warehouse.json()['id']}/locations",
                    headers=headers(inv),
                    json={
                        "kind": "bin",
                        "code": f"{key.upper()}-{bin_code}-{run}",
                        "name": f"Bin {bin_code}",
                    },
                )
                assert location.status_code == 201, location.text[:300]
                inv[f"bin_{key}{bin_code}"] = location.json()

    # ------------------------------------------------------------------ 4. item, lot, roll

    def test_04_a_tracked_item_a_lot_and_a_roll_with_a_measured_weight(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        run = inv["run"]
        item = client.post(
            f"{BASE}/inventory",
            headers=headers(inv),
            json={
                "sku": f"FAB-{run}",
                "name": "Cotton poplin",
                "category": "raw_materials",
                "warehouse": inv["wh_a"]["name"],
                "uom": "m",
                "track_lot": True,
                "track_roll": True,
            },
        )
        assert item.status_code == 201, item.text[:300]
        inv["item"] = item.json()

        # A roll of a lot-tracked item without its lot is refused with the field the UI maps.
        no_lot = client.post(
            f"{BASE}/inventory/rolls",
            headers=headers(inv, str(uuid.uuid4())),
            json={"inventory_item_id": inv["item"]["id"], "primary_qty": "82.5"},
        )
        assert no_lot.status_code == 422
        assert {"field": "lot_id", "code": "required"}.items() <= errors_of(no_lot)[0].items()

        for label, shade in (("a", "Indigo"), ("b", "Red")):
            lot = client.post(
                f"{BASE}/inventory/lots",
                headers=headers(inv, str(uuid.uuid4())),
                json={
                    "inventory_item_id": inv["item"]["id"],
                    "lot_no": f"LOT-{label.upper()}-{run}",
                },
            )
            assert lot.status_code == 201, lot.text[:300]
            inv[f"lot_{label}"] = lot.json()
            roll = client.post(
                f"{BASE}/inventory/rolls",
                headers=headers(inv, str(uuid.uuid4())),
                json={
                    "inventory_item_id": inv["item"]["id"],
                    "lot_id": lot.json()["id"],
                    "roll_no": f"R-{label.upper()}-{run}",
                    "primary_qty": "82.5" if label == "a" else "40",
                    "secondary_qty": "14.8" if label == "a" else "7",
                    "secondary_uom": "kg",
                    "width_mm": 1500,
                    "gsm": 120,
                    "shade": shade,
                },
            )
            assert roll.status_code == 201, roll.text[:300]
            assert roll.json()["primary_qty"] == ("82.5" if label == "a" else "40")
            inv[f"roll_{label}"] = roll.json()

        duplicate = client.post(
            f"{BASE}/inventory/rolls",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": inv["item"]["id"],
                "lot_id": inv["lot_a"]["id"],
                "roll_no": inv["roll_a"]["roll_no"],
                "primary_qty": "1",
            },
        )
        assert duplicate.status_code == 409
        assert errors_of(duplicate)[0]["field"] == "roll_no"
        assert errors_of(duplicate)[0]["code"] == "duplicate"

    # ------------------------------------------------------------------ 5. opening stock

    def test_05_opening_stock_posts_once_and_a_retried_key_replays(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        def opening(
            roll: dict[str, Any], lot: dict[str, Any], quantity: str, key: str
        ) -> httpx.Response:
            return client.post(
                f"{BASE}/inventory/stock/opening",
                headers=headers(inv, key),
                json={
                    "lines": [
                        {
                            "inventory_item_id": inv["item"]["id"],
                            "location_id": inv["bin_a01"]["id"],
                            "quantity": quantity,
                            "lot_id": lot["id"],
                            "roll_id": roll["id"],
                        }
                    ],
                    "occurred_on": date.today().isoformat(),
                },
            )

        # A tracked item must name its lot and roll: the server's refusal, which the dialog maps.
        bare = client.post(
            f"{BASE}/inventory/stock/opening",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "lines": [
                    {
                        "inventory_item_id": inv["item"]["id"],
                        "location_id": inv["bin_a01"]["id"],
                        "quantity": "10",
                    }
                ]
            },
        )
        assert bare.status_code == 422
        assert errors_of(bare)[0]["field"] in {"lot_id", "roll_id"}
        assert errors_of(bare)[0]["code"] == "required"

        key = str(uuid.uuid4())
        first = opening(inv["roll_a"], inv["lot_a"], "82.5", key)
        assert first.status_code == 201, first.text[:300]
        assert first.json()["replayed"] is False
        assert first.json()["lines"][0]["quantity"] == "82.5"
        assert first.json()["lines"][0]["balance_after"] == "82.5"

        replay = opening(inv["roll_a"], inv["lot_a"], "82.5", key)
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["move_id"] == first.json()["move_id"]

        # The same key with a different quantity is a different request wearing the same name.
        clash = opening(inv["roll_a"], inv["lot_a"], "83", key)
        assert clash.status_code == 409

        second = opening(inv["roll_b"], inv["lot_b"], "40", str(uuid.uuid4()))
        assert second.status_code == 201, second.text[:300]

        stock = client.get(
            f"{BASE}/inventory/stock", params={"item_id": inv["item"]["id"]}, headers=headers(inv)
        )
        assert stock.status_code == 200
        held = {r["roll_no"]: r["on_hand_qty"] for r in stock.json()["items"]}
        assert held == {inv["roll_a"]["roll_no"]: "82.5", inv["roll_b"]["roll_no"]: "40"}, (
            "the retried key posted twice, or the clash posted at all"
        )

    # ------------------------------------------------------------------ 6-7. split and merge

    def test_06_split_a_roll_and_the_weight_follows_by_floor(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        key = str(uuid.uuid4())
        body = {
            "location_id": inv["bin_a01"]["id"],
            "children": [{"quantity": "50"}, {"quantity": "32.5"}],
            "occurred_on": date.today().isoformat(),
        }
        response = client.post(
            f"{BASE}/inventory/rolls/{inv['roll_a']['id']}:split",
            headers=headers(inv, key),
            json=body,
        )
        assert response.status_code == 201, response.text[:400]
        out = response.json()
        assert out["parent"]["state"] == "SPLIT"
        children = out["children"]
        assert [c["primary_qty"] for c in children] == ["50", "32.5"]
        assert [c["on_hand_qty"] for c in children] == ["50", "32.5"]
        # 14.8 kg over 50 + 32.5 m: floor(14.8 * 50 / 82.5) = 8.969, the last child takes 5.831.
        assert [c["secondary_qty"] for c in children] == ["8.969", "5.831"]
        assert {c["parent_roll_id"] for c in children} == {inv["roll_a"]["id"]}
        inv["children"] = children
        inv["split_move"] = out["move"]["move_id"]

        replay = client.post(
            f"{BASE}/inventory/rolls/{inv['roll_a']['id']}:split",
            headers=headers(inv, key),
            json=body,
        )
        assert replay.status_code == 200
        assert replay.json()["move"]["replayed"] is True
        assert [c["id"] for c in replay.json()["children"]] == [c["id"] for c in children], (
            "a retried split minted new roll ids instead of replaying the first attempt's"
        )

    def test_07_refusals_the_dialogs_map_and_a_merge_that_works(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        # A roll splits once: the second split finds it SPLIT.
        again = client.post(
            f"{BASE}/inventory/rolls/{inv['roll_a']['id']}:split",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "location_id": inv["bin_a01"]["id"],
                "children": [{"quantity": "1"}, {"quantity": "2"}],
            },
        )
        assert again.status_code == 409, again.text[:300]

        # Children that do not add up exactly: the field and code the dialog puts under "Children".
        inexact = client.post(
            f"{BASE}/inventory/rolls/{inv['roll_b']['id']}:split",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "location_id": inv["bin_a01"]["id"],
                "children": [{"quantity": "25"}, {"quantity": "10"}],
            },
        )
        assert inexact.status_code == 422, inexact.text[:300]
        assert errors_of(inexact)[0]["field"] == "children"
        assert errors_of(inexact)[0]["code"] == "split_total"

        # Different lot and shade: not mergeable, and the server says so on `roll_ids`.
        c1, c2 = inv["children"]
        unmergeable = client.post(
            f"{BASE}/inventory/rolls:merge",
            headers=headers(inv, str(uuid.uuid4())),
            json={"roll_ids": [c1["id"], inv["roll_b"]["id"]], "location_id": inv["bin_a01"]["id"]},
        )
        assert unmergeable.status_code == 422, unmergeable.text[:300]
        assert errors_of(unmergeable)[0]["field"] == "roll_ids"
        assert errors_of(unmergeable)[0]["code"] == "not_mergeable"

        # The two children share item, lot, shade and bucket: they merge back into one roll.
        merged = client.post(
            f"{BASE}/inventory/rolls:merge",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "roll_ids": [c1["id"], c2["id"]],
                "location_id": inv["bin_a01"]["id"],
                "roll_no": f"R-M-{inv['run']}",
            },
        )
        assert merged.status_code == 201, merged.text[:400]
        assert merged.json()["result"]["on_hand_qty"] == "82.5"
        assert {s["state"] for s in merged.json()["sources"]} == {"MERGED"}
        inv["merged"] = merged.json()["result"]

    def test_08_an_over_take_and_a_cross_warehouse_transfer_are_refused_in_the_uis_terms(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        item = inv["item"]["id"]
        merged = inv["merged"]

        over = client.post(
            f"{BASE}/inventory/stock/transfers",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": item,
                "from_location_id": inv["bin_a01"]["id"],
                "to_location_id": inv["bin_a02"]["id"],
                "quantity": "500",
                "lot_id": merged["lot_id"],
                "roll_id": merged["id"],
            },
        )
        assert over.status_code == 409, over.text[:300]
        body = over.json()
        assert body["type"].endswith("insufficient-stock")
        shortfall = body["errors"][0]
        assert shortfall["requested"] == "500"
        assert shortfall["on_hand"] == "82.5"
        assert shortfall["available"] == "82.5"
        assert shortfall["reservations"] == []

        elsewhere = client.post(
            f"{BASE}/inventory/stock/transfers",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": item,
                "from_location_id": inv["bin_a01"]["id"],
                "to_location_id": inv["bin_b01"]["id"],
                "quantity": "10",
                "lot_id": merged["lot_id"],
                "roll_id": merged["id"],
            },
        )
        assert elsewhere.status_code == 422, elsewhere.text[:300]
        assert errors_of(elsewhere)[0]["field"] == "to_location_id"
        assert errors_of(elsewhere)[0]["code"] == "different_warehouse"

        moved = client.post(
            f"{BASE}/inventory/stock/transfers",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": item,
                "from_location_id": inv["bin_a01"]["id"],
                "to_location_id": inv["bin_a02"]["id"],
                "quantity": "12.25",
                "lot_id": merged["lot_id"],
                "roll_id": merged["id"],
            },
        )
        assert moved.status_code == 201, moved.text[:300]

        held = client.post(
            f"{BASE}/inventory/stock/status-changes",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": item,
                "location_id": inv["bin_a02"]["id"],
                "from_status": "AVAILABLE",
                "to_status": "QUALITY_HOLD",
                "quantity": "2.25",
                "lot_id": merged["lot_id"],
                "roll_id": merged["id"],
            },
        )
        assert held.status_code == 201, held.text[:300]

        # A JSON number for a quantity never reaches the domain: the schema refuses the type.
        float_qty = client.post(
            f"{BASE}/inventory/stock/transfers",
            headers=headers(inv, str(uuid.uuid4())),
            json={
                "inventory_item_id": item,
                "from_location_id": inv["bin_a01"]["id"],
                "to_location_id": inv["bin_a02"]["id"],
                "quantity": 1.5,
            },
        )
        assert float_qty.status_code == 422

    # ------------------------------------------------------------------ 9. the ledger

    def test_09_the_ledger_pages_on_the_cursor_and_filters_by_roll_kind_and_date(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        parent = inv["roll_a"]["id"]
        rows = collect(client, inv, "/inventory/ledger", {"roll_id": parent})
        assert [r["kind"] for r in rows] == ["relocation", "opening"], (
            "newest first: the split took the opening stock back out of the parent"
        )
        assert rows[0]["reference_type"] == "roll_split"
        assert rows[0]["quantity"] == "-82.5"
        assert rows[0]["balance_after"] == "0"
        assert rows[1]["quantity"] == "82.5"

        # The pages are the whole list, once each: paging at one row a time loses and repeats none.
        everything = collect(client, inv, "/inventory/ledger", {"item_id": inv["item"]["id"]})
        assert len({r["id"] for r in everything}) == len(everything)
        relocations = [r for r in everything if r["kind"] == "relocation"]
        assert {r["reference_type"] for r in relocations} >= {
            "roll_split",
            "roll_merge",
            "bin_transfer",
            "status_change",
        }

        only_openings = collect(
            client, inv, "/inventory/ledger", {"item_id": inv["item"]["id"], "kind": "opening"}
        )
        assert {r["kind"] for r in only_openings} == {"opening"}
        assert len(only_openings) == 2

        today = date.today()
        in_range = collect(
            client,
            inv,
            "/inventory/ledger",
            {
                "item_id": inv["item"]["id"],
                "from_date": today.isoformat(),
                "to_date": today.isoformat(),
            },
        )
        assert len(in_range) == len(everything)
        out_of_range = collect(
            client,
            inv,
            "/inventory/ledger",
            {
                "item_id": inv["item"]["id"],
                "from_date": (today - timedelta(days=30)).isoformat(),
                "to_date": (today - timedelta(days=29)).isoformat(),
            },
        )
        assert out_of_range == []

        bad_cursor = client.get(
            f"{BASE}/inventory/ledger", params={"cursor": "not-a-cursor"}, headers=headers(inv)
        )
        assert bad_cursor.status_code == 422, "a forged cursor must be refused, not trusted"

    # ------------------------------------------------------------------ 10. trace and delete

    def test_10_the_trace_walks_the_lineage_and_a_location_with_history_cannot_be_deleted(
        self, client: httpx.Client, inv: dict[str, Any]
    ) -> None:
        child = inv["children"][0]
        trace = client.get(f"{BASE}/inventory/rolls/{child['id']}/trace", headers=headers(inv))
        assert trace.status_code == 200, trace.text[:300]
        assert inv["roll_a"]["id"] in {n["id"] for n in trace.json()["ancestors"]}
        assert inv["merged"]["id"] in {n["id"] for n in trace.json()["descendants"]}
        assert trace.json()["lot"]["lot_no"] == inv["lot_a"]["lot_no"]

        events = collect(client, inv, f"/inventory/rolls/{inv['roll_a']['id']}/events", {})
        assert {e["kind"] for e in events} >= {"SPLIT"}

        lot_trace = client.get(
            f"{BASE}/inventory/lots/{inv['lot_a']['id']}/trace", headers=headers(inv)
        )
        assert lot_trace.status_code == 200
        assert lot_trace.json()["lot"]["on_hand_qty"] == "82.5"

        # The location the stock sat in has ledger history, so its history is kept (409, with the
        # reason the dialog shows verbatim). An unused bin goes.
        refused = client.delete(
            f"{BASE}/locations/{inv['bin_a01']['id']}", headers=headers(inv, str(uuid.uuid4()))
        )
        assert refused.status_code == 409, refused.text[:300]
        assert "ledger history" in refused.json()["detail"]

        removed = client.delete(
            f"{BASE}/locations/{inv['bin_b02']['id']}", headers=headers(inv, str(uuid.uuid4()))
        )
        assert removed.status_code == 204, removed.text[:300]
