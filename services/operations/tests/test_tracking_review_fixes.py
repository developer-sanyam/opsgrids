"""Regression tests for the defects two independent reviews found in the Phase 1 slice. Each
class names the finding it pins; every test here failed before its fix.

* one-bucket invariant for split/merge (a roll with stock in several buckets is refused);
* keyed split/merge retries replay even after the roll state they created has moved on;
* derived child roll numbers longer than the column are a 422;
* `unit_cost_minor` beyond the cap is a 422, never a database overflow;
* the lot genealogy loop check sees the whole chain, not the first 25 hops.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from sqlalchemy import select

from app.models import Roll
from tests.test_lots_rolls_api import (
    LOTS,
    MERGE,
    ROLLS,
    call,
    make_roll,
    merge_body,
    split_body,
)
from tests.tracking_fixtures import (
    LOT_WRITE,
    ROLL_WRITE,
    STOCK_ADJUST,
    STOCK_TRANSFER,
    World,
    assert_reconciled,
    build_world,
)

pytestmark = pytest.mark.integration

TRANSFERS = "/operations/inventory/stock/transfers"
STATUS = "/operations/inventory/stock/status-changes"
OPENING = "/operations/inventory/stock/opening"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def roll_state(session, roll_id: str) -> str:  # type: ignore[no-untyped-def]
    async with session.begin():
        return str(
            (await session.execute(select(Roll.state).where(Roll.id == roll_id))).scalar_one()
        )


async def move_to_bin_b(client, org_id, w: World, roll_id, quantity: str):  # type: ignore[no-untyped-def]
    response = await call(
        client,
        org_id,
        STOCK_TRANSFER,
        "POST",
        TRANSFERS,
        {
            "inventory_item_id": str(w.t.fabric),
            "from_location_id": str(w.t.weaving),
            "to_location_id": str(w.bin_b),
            "quantity": quantity,
            "lot_id": str(w.lot),
            "roll_id": str(roll_id),
        },
    )
    assert response.status_code == 201, response.text


class TestOneBucketInvariant:
    async def test_a_roll_with_stock_in_another_bin_is_not_split(
        self, client, session, org_id, world
    ) -> None:
        w = world
        await move_to_bin_b(client, org_id, w, w.roll, "10")
        refused = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{w.roll}:split",
            split_body(w, "40", "32.5"),
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["errors"][0]["code"] == "roll_in_several_buckets"
        assert "BIN-B" in refused.json()["detail"]
        assert await roll_state(session, w.roll) == "ACTIVE"
        await assert_reconciled(session, org_id)

    async def test_a_roll_with_a_quality_hold_portion_is_not_split(
        self, client, session, org_id, world
    ) -> None:
        w = world
        held = await call(
            client,
            org_id,
            STOCK_TRANSFER,
            "POST",
            STATUS,
            {
                "inventory_item_id": str(w.t.fabric),
                "location_id": str(w.t.weaving),
                "from_status": "AVAILABLE",
                "to_status": "QUALITY_HOLD",
                "quantity": "5",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
        )
        assert held.status_code == 201
        refused = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{w.roll}:split",
            split_body(w, "40", "37.5"),
        )
        assert refused.status_code == 409
        assert "QUALITY_HOLD" in refused.json()["detail"]
        assert await roll_state(session, w.roll) == "ACTIVE"

    async def test_once_the_roll_lives_in_one_bucket_again_it_splits(
        self, client, session, org_id, world
    ) -> None:
        w = world
        await move_to_bin_b(client, org_id, w, w.roll, "10")
        back = await call(
            client,
            org_id,
            STOCK_TRANSFER,
            "POST",
            TRANSFERS,
            {
                "inventory_item_id": str(w.t.fabric),
                "from_location_id": str(w.bin_b),
                "to_location_id": str(w.t.weaving),
                "quantity": "10",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
        )
        assert back.status_code == 201
        ok = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{w.roll}:split",
            split_body(w, "50", "32.5"),
        )
        assert ok.status_code == 201, ok.text
        await assert_reconciled(session, org_id)

    async def test_a_source_roll_with_stock_elsewhere_is_not_merged(
        self, client, session, org_id, world
    ) -> None:
        w = world
        second = await make_roll(client, org_id, w, "R-2", "40")
        await move_to_bin_b(client, org_id, w, second, "15")
        refused = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            MERGE,
            merge_body(w, str(w.roll), second),
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["errors"][0]["code"] == "roll_in_several_buckets"
        assert await roll_state(session, second) == "ACTIVE"
        assert await roll_state(session, w.roll) == "ACTIVE"
        await assert_reconciled(session, org_id)


class TestKeyedRetriesReplay:
    async def test_a_keyed_split_retried_after_a_child_was_consumed_still_replays(
        self, client, session, org_id, world
    ) -> None:
        w = world
        path = f"{ROLLS}/{w.roll}:split"
        body = split_body(w, "50", "32.5")
        first = await call(client, org_id, ROLL_WRITE, "POST", path, body, key="split-k")
        assert first.status_code == 201, first.text
        child = first.json()["children"][0]["id"]
        # The first child is itself split: it is now SPLIT and holds nothing.
        again_split = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{child}:split",
            split_body(w, "20", "30"),
        )
        assert again_split.status_code == 201, again_split.text
        retry = await call(client, org_id, ROLL_WRITE, "POST", path, body, key="split-k")
        assert retry.status_code == 200, retry.text
        assert retry.json()["move"]["replayed"] is True
        assert retry.json()["children"][0]["id"] == child
        await assert_reconciled(session, org_id)

    async def test_a_keyed_merge_retried_after_the_result_was_consumed_still_replays(
        self, client, session, org_id, world
    ) -> None:
        w = world
        second = await make_roll(client, org_id, w, "R-2", "40")
        body = merge_body(w, str(w.roll), second)
        first = await call(client, org_id, ROLL_WRITE, "POST", MERGE, body, key="merge-k")
        assert first.status_code == 201, first.text
        result = first.json()["result"]["id"]
        consumed = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{result}:split",
            split_body(w, "100", "22.5"),
        )
        assert consumed.status_code == 201, consumed.text
        retry = await call(client, org_id, ROLL_WRITE, "POST", MERGE, body, key="merge-k")
        assert retry.status_code == 200, retry.text
        assert retry.json()["move"]["replayed"] is True

    async def test_the_same_key_for_a_different_request_is_an_idempotency_mismatch(
        self, client, org_id, world
    ) -> None:
        w = world
        path = f"{ROLLS}/{w.roll}:split"
        first = await call(
            client, org_id, ROLL_WRITE, "POST", path, split_body(w, "50", "32.5"), key="k-1"
        )
        assert first.status_code == 201
        different = await call(
            client, org_id, ROLL_WRITE, "POST", path, split_body(w, "40", "42.5"), key="k-1"
        )
        assert different.status_code == 409
        assert "idempotency key" in different.json()["detail"]


class TestDerivedRollNumbers:
    async def test_a_derived_child_number_over_sixty_characters_is_a_422(
        self, client, session, org_id, world
    ) -> None:
        w = world
        long_parent = await make_roll(client, org_id, w, "R" * 59, "10")
        refused = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{long_parent}:split",
            split_body(w, "4", "6"),
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["errors"][0]["code"] == "too_long"
        assert await roll_state(session, long_parent) == "ACTIVE"
        named = split_body(w, "4", "6")
        named["children"][0]["roll_no"] = "SHORT-A"
        named["children"][1]["roll_no"] = "SHORT-B"
        ok = await call(client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{long_parent}:split", named)
        assert ok.status_code == 201, ok.text

    async def test_a_sixty_character_derived_number_is_fine(self, client, org_id, world) -> None:
        w = world
        parent = await make_roll(client, org_id, w, "R" * 58, "10")
        ok = await call(
            client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{parent}:split", split_body(w, "4", "6")
        )
        assert ok.status_code == 201, ok.text
        assert max(len(c["roll_no"]) for c in ok.json()["children"]) == 60


class TestUnitCostBound:
    @pytest.mark.parametrize("cost", [10**15 + 1, 10**19, 2**63])
    async def test_an_absurd_unit_cost_is_a_422_not_a_500(
        self, client, org_id, world, cost
    ) -> None:
        w = world
        refused = await call(
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
                        "unit_cost_minor": cost,
                        "unit_cost_currency": "INR",
                    }
                ]
            },
        )
        assert refused.status_code == 422, refused.text

    async def test_the_cap_itself_is_accepted(self, client, org_id, world) -> None:
        w = world
        ok = await call(
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
                        "quantity": "1000000",
                        "unit_cost_minor": 10**15,
                        "unit_cost_currency": "INR",
                    }
                ]
            },
        )
        assert ok.status_code == 201, ok.text


class TestGenealogyLoopCheckIsUncapped:
    async def test_a_closing_edge_over_a_chain_longer_than_the_trace_cap_is_refused(
        self, client, org_id, world
    ) -> None:
        w = world
        lots = [str(w.lot)]
        for n in range(1, 31):
            made = await call(
                client,
                org_id,
                LOT_WRITE,
                "POST",
                LOTS,
                {"inventory_item_id": str(w.t.fabric), "lot_no": f"CHAIN-{n}"},
            )
            assert made.status_code == 201, made.text
            lots.append(made.json()["id"])
        for parent, child in pairwise(lots):
            linked = await call(
                client,
                org_id,
                LOT_WRITE,
                "POST",
                f"{LOTS}/{parent}/genealogy",
                {"child_lot_id": child, "relation": "BLEND"},
            )
            assert linked.status_code == 201, linked.text
        # 30 hops from first to last: beyond the display cap of 25.
        closing = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{lots[-1]}/genealogy",
            {"child_lot_id": lots[0], "relation": "BLEND"},
        )
        assert closing.status_code == 409, closing.text
        assert "loop" in closing.json()["detail"]
        # And a link that is not a loop over the same long chain is still allowed.
        sideways = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{lots[0]}/genealogy",
            {"child_lot_id": lots[-1], "relation": "DERIVED"},
        )
        assert sideways.status_code == 201, sideways.text
