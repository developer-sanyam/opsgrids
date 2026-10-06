"""Lots, rolls, roll split and merge, and the traces over HTTP (inventory Phase 1, I3/I4/I5).

The headline cases: **82.5 m splits into 50 + 32.5 exactly**, the children's weights **sum to the
parent's 14.8 kg exactly**, a retried split replays instead of splitting twice, and the trace
walks child -> parent -> lot -> source document and back.

Authorization and cross-tenant behaviour are `test_tracking_authz.py`'s.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.inventory import ledger
from app.inventory.quantity import parse_qty
from app.models import LedgerEntry, LotGenealogy, OutboxEvent, Roll, RollEvent
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import q
from tests.tracking_fixtures import (
    API,
    LOT_WRITE,
    ROLL_WRITE,
    STOCK_ADJUST,
    STOCK_READ,
    World,
    assert_reconciled,
    bucket_qty,
    build_world,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
LOTS = "/operations/inventory/lots"
ROLLS = "/operations/inventory/rolls"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def call(client, org_id, perm, method, path, body=None, key=None, params=None):  # type: ignore[no-untyped-def]
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal_for(org_id, [perm])):
        return await client.request(method, API + path, json=body, headers=headers, params=params)


async def make_roll(client, org_id, w: World, roll_no: str, primary: str, **extra):  # type: ignore[no-untyped-def]
    """Register a roll in the world's lot and open it into the weaving bin; returns its id."""
    body = {
        "inventory_item_id": str(w.t.fabric),
        "lot_id": str(w.lot),
        "roll_no": roll_no,
        "primary_qty": primary,
        "secondary_qty": "7.2",
        "secondary_uom": "kg",
        "shade": "NAVY",
        **extra,
    }
    made = await call(client, org_id, ROLL_WRITE, "POST", ROLLS, body)
    assert made.status_code == 201, made.text
    roll_id = made.json()["id"]
    opened = await call(
        client,
        org_id,
        STOCK_ADJUST,
        "POST",
        "/operations/inventory/stock/opening",
        {
            "lines": [
                {
                    "inventory_item_id": str(w.t.fabric),
                    "location_id": str(w.t.weaving),
                    "quantity": primary,
                    "lot_id": str(w.lot),
                    "roll_id": roll_id,
                }
            ]
        },
    )
    assert opened.status_code == 201, opened.text
    return roll_id


# ------------------------------------------------------------------ lots


class TestLots:
    async def test_create_generates_a_lot_number_and_carries_the_source_document(
        self, client, org_id, world
    ) -> None:
        source = uuid.uuid4()
        response = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {
                "inventory_item_id": str(world.t.fabric),
                "supplier_lot_no": "MILL-77",
                "manufactured_on": "2026-08-01",
                "expires_on": "2028-08-01",
                "source_type": "purchase_order",
                "source_id": str(source),
                "attributes": {"dye_house": "Surat", "shrinkage_pct": 3, "organic": True},
            },
        )
        assert response.status_code == 201, response.text
        lot = response.json()
        assert lot["lot_no"].startswith("LOT-") and len(lot["lot_no"]) == 12
        assert (lot["quality_status"], lot["on_hand_qty"]) == ("PENDING", "0")
        assert lot["source_type"] == "purchase_order" and lot["source_id"] == str(source)
        assert lot["attributes"] == {"dye_house": "Surat", "shrinkage_pct": 3, "organic": True}
        assert (lot["sku"], lot["uom"]) == ("COT-180-NAVY", "m")

    async def test_the_lot_event_matches_its_schema(self, session, org_id, world) -> None:
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.lot.created"
                    )
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )

    async def test_a_duplicate_lot_number_is_a_conflict(self, client, org_id, world) -> None:
        response = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {"inventory_item_id": str(world.t.fabric), "lot_no": world.lot_no},
        )
        assert response.status_code == 409
        assert response.json()["errors"][0]["field"] == "lot_no"

    async def test_an_item_that_does_not_track_lots_is_refused(self, client, org_id, world) -> None:
        response = await call(
            client, org_id, LOT_WRITE, "POST", LOTS, {"inventory_item_id": str(world.t.dye)}
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "not_tracked"

    @pytest.mark.parametrize(
        "extra",
        [
            {"attributes": {"gsm": 180.5}},  # a float is never an attribute value
            {"attributes": {"nested": {"a": 1}}},
            {"attributes": {"bad key": "x"}},
            {"source_type": "purchase_order"},  # without a source_id
            {"manufactured_on": "2026-08-02", "expires_on": "2026-08-01"},
            {"quality_status": "SHIPPED"},
            {"unknown_field": 1},
            {"lot_no": "has space"},
        ],
    )
    async def test_invalid_lots_are_refused_at_the_boundary(
        self, client, org_id, world, extra
    ) -> None:
        body = {"inventory_item_id": str(world.t.fabric), **extra}
        assert (await call(client, org_id, LOT_WRITE, "POST", LOTS, body)).status_code == 422

    async def test_patch_changes_quality_and_refuses_the_immutable_fields(
        self, client, org_id, world
    ) -> None:
        path = f"{LOTS}/{world.lot}"
        ok = await call(
            client,
            org_id,
            LOT_WRITE,
            "PATCH",
            path,
            {"quality_status": "APPROVED", "supplier_lot_no": "S-2"},
        )
        assert ok.status_code == 200, ok.text
        assert (ok.json()["quality_status"], ok.json()["supplier_lot_no"]) == ("APPROVED", "S-2")
        for forbidden in ({"lot_no": "X"}, {"inventory_item_id": str(uuid.uuid4())}):
            response = await call(client, org_id, LOT_WRITE, "PATCH", path, forbidden)
            assert response.status_code == 422
        null = await call(client, org_id, LOT_WRITE, "PATCH", path, {"quality_status": None})
        assert null.status_code == 422

    async def test_lot_on_hand_is_the_sum_of_its_buckets(self, client, org_id, world) -> None:
        response = await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{world.lot}")
        assert response.json()["on_hand_qty"] == "82.5"

    async def test_the_list_is_cursor_paginated_without_gaps_or_repeats(
        self, client, org_id, world
    ) -> None:
        for n in range(6):
            made = await call(
                client,
                org_id,
                LOT_WRITE,
                "POST",
                LOTS,
                {"inventory_item_id": str(world.t.fabric), "lot_no": f"PG-{n}"},
            )
            assert made.status_code == 201
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            page = (await call(client, org_id, STOCK_READ, "GET", LOTS, params=params)).json()
            seen += [lot["lot_no"] for lot in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert pages == 3 and len(seen) == len(set(seen)) == 7
        assert seen[0] == "PG-5" and seen[-1] == world.lot_no  # newest first

    async def test_filters_narrow_the_list(self, client, org_id, world) -> None:
        await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {
                "inventory_item_id": str(world.t.fabric),
                "lot_no": "HOLD-1",
                "quality_status": "ON_HOLD",
            },
        )
        held = await call(
            client, org_id, STOCK_READ, "GET", LOTS, params={"quality_status": "ON_HOLD"}
        )
        assert [lot["lot_no"] for lot in held.json()["items"]] == ["HOLD-1"]
        prefix = await call(client, org_id, STOCK_READ, "GET", LOTS, params={"q": "lot-"})
        assert [lot["lot_no"] for lot in prefix.json()["items"]] == [world.lot_no]

    @pytest.mark.parametrize("params", [{"cursor": "not-a-cursor"}, {"limit": 0}, {"limit": 201}])
    async def test_a_bad_cursor_or_limit_is_a_422_never_a_500(
        self, client, org_id, world, params
    ) -> None:
        response = await call(client, org_id, STOCK_READ, "GET", LOTS, params=params)
        assert response.status_code == 422


class TestLotGenealogy:
    async def _lot(self, client, org_id, w: World, lot_no: str) -> str:
        made = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {"inventory_item_id": str(w.t.fabric), "lot_no": lot_no},
        )
        return str(made.json()["id"])

    async def test_trace_walks_both_ways_through_a_chain(self, client, org_id, world) -> None:
        a = str(world.lot)
        b = await self._lot(client, org_id, world, "LOT-B")
        c = await self._lot(client, org_id, world, "LOT-C")
        for parent, child, relation in ((a, b, "BLEND"), (b, c, "REWORK")):
            link = await call(
                client,
                org_id,
                LOT_WRITE,
                "POST",
                f"{LOTS}/{parent}/genealogy",
                {"child_lot_id": child, "relation": relation, "note": "test"},
            )
            assert link.status_code == 201, link.text
        forward = (await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{a}/trace")).json()
        assert [(n["number"], n["depth"], n["relation"]) for n in forward["descendants"]] == [
            ("LOT-B", 1, "BLEND"),
            ("LOT-C", 2, "REWORK"),
        ]
        assert forward["ancestors"] == []
        backward = (await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{c}/trace")).json()
        assert [(n["number"], n["depth"], n["from_id"]) for n in backward["ancestors"]] == [
            ("LOT-B", 1, c),
            ("LOT-A", 2, b),
        ]
        edges = await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{b}/genealogy")
        assert {e["relation"] for e in edges.json()["items"]} == {"BLEND", "REWORK"}

    async def test_a_link_that_would_close_a_loop_is_refused(self, client, org_id, world) -> None:
        a = str(world.lot)
        b = await self._lot(client, org_id, world, "LOT-B")
        await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{a}/genealogy",
            {"child_lot_id": b, "relation": "BLEND"},
        )
        loop = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{b}/genealogy",
            {"child_lot_id": a, "relation": "BLEND"},
        )
        assert loop.status_code == 409
        again = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{a}/genealogy",
            {"child_lot_id": b, "relation": "BLEND"},
        )
        assert again.status_code == 409
        self_link = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{a}/genealogy",
            {"child_lot_id": a, "relation": "BLEND"},
        )
        assert self_link.status_code == 422

    async def test_the_linked_event_matches_its_schema(
        self, client, session, org_id, world
    ) -> None:
        b = await self._lot(client, org_id, world, "LOT-B")
        await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{world.lot}/genealogy",
            {"child_lot_id": b, "relation": "DERIVED"},
        )
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.lot.linked"
                    )
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )


# ------------------------------------------------------------------ rolls


class TestRolls:
    async def test_create_carries_both_measured_quantities(self, client, org_id, world) -> None:
        response = await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{world.roll}")
        roll = response.json()
        assert (roll["primary_qty"], roll["secondary_qty"], roll["secondary_uom"]) == (
            "82.5",
            "14.8",
            "kg",
        )
        assert (roll["width_mm"], roll["gsm"], roll["shade"], roll["grade"]) == (
            1500,
            180,
            "NAVY",
            "A",
        )
        assert (roll["state"], roll["on_hand_qty"], roll["lot_no"]) == ("ACTIVE", "82.5", "LOT-A")

    @pytest.mark.parametrize(
        "extra",
        [
            {"primary_qty": "0"},
            {"primary_qty": "-1"},
            {"primary_qty": 82.5},
            {"primary_qty": "82.5555"},
            {"secondary_qty": "3"},  # secondary quantity without its unit
            {"secondary_uom": "kg"},  # unit without a quantity
            {"width_mm": 0},
            {"lot_id": None},  # the fabric is lot-tracked: a roll needs its lot
            {"mystery": True},
        ],
    )
    async def test_invalid_rolls_are_refused(self, client, org_id, world, extra) -> None:
        body = {
            "inventory_item_id": str(world.t.fabric),
            "lot_id": str(world.lot),
            "primary_qty": "10",
            **extra,
        }
        response = await call(client, org_id, ROLL_WRITE, "POST", ROLLS, body)
        assert response.status_code == 422, response.text

    async def test_a_duplicate_roll_number_is_a_conflict(self, client, org_id, world) -> None:
        body = {
            "inventory_item_id": str(world.t.fabric),
            "lot_id": str(world.lot),
            "roll_no": world.roll_no,
            "primary_qty": "10",
        }
        assert (await call(client, org_id, ROLL_WRITE, "POST", ROLLS, body)).status_code == 409

    async def test_an_item_that_does_not_track_rolls_is_refused(
        self, client, org_id, world
    ) -> None:
        body = {"inventory_item_id": str(world.t.dye), "primary_qty": "10"}
        assert (await call(client, org_id, ROLL_WRITE, "POST", ROLLS, body)).status_code == 422

    async def test_an_unknown_lot_is_a_404(self, client, org_id, world) -> None:
        body = {
            "inventory_item_id": str(world.t.fabric),
            "lot_id": str(uuid.uuid4()),
            "primary_qty": "10",
        }
        assert (await call(client, org_id, ROLL_WRITE, "POST", ROLLS, body)).status_code == 404

    async def test_patch_remeasures_records_an_event_and_refuses_the_primary(
        self, client, session, org_id, world
    ) -> None:
        path = f"{ROLLS}/{world.roll}"
        ok = await call(
            client,
            org_id,
            ROLL_WRITE,
            "PATCH",
            path,
            {"secondary_qty": "15.1", "secondary_uom": "kg", "grade": "B"},
        )
        assert ok.status_code == 200, ok.text
        assert (ok.json()["secondary_qty"], ok.json()["grade"]) == ("15.1", "B")
        assert ok.json()["primary_qty"] == "82.5"
        refused = await call(client, org_id, ROLL_WRITE, "PATCH", path, {"primary_qty": "1"})
        assert refused.status_code == 422
        unpaired = await call(client, org_id, ROLL_WRITE, "PATCH", path, {"secondary_qty": "1"})
        assert unpaired.status_code == 422
        events = await call(client, org_id, STOCK_READ, "GET", f"{path}/events")
        kinds = [e["kind"] for e in events.json()["items"]]
        assert kinds[0] == "MEASURED" and set(kinds) == {"MEASURED", "OPENING", "CREATED"}


# ------------------------------------------------------------------ split


def split_body(w: World, *quantities: str, **extra) -> dict:  # type: ignore[type-arg]
    return {
        "location_id": str(w.t.weaving),
        "children": [{"quantity": quantity} for quantity in quantities],
        **extra,
    }


class TestSplit:
    async def test_82_5_metres_splits_into_50_and_32_5_exactly(
        self, client, session, org_id, world
    ) -> None:
        w = world
        response = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{w.roll}:split",
            split_body(w, "50", "32.5"),
        )
        assert response.status_code == 201, response.text
        out = response.json()
        first, second = out["children"]
        assert (first["primary_qty"], second["primary_qty"]) == ("50", "32.5")
        assert (first["secondary_qty"], second["secondary_qty"]) == ("8.969", "5.831")
        # The headline guarantee, checked in integers: exact on both measures.
        assert parse_qty(first["primary_qty"]) + parse_qty(second["primary_qty"]) == q("82.5")
        assert parse_qty(first["secondary_qty"]) + parse_qty(second["secondary_qty"]) == q("14.8")
        assert [c["roll_no"] for c in out["children"]] == ["R-1-1", "R-1-2"]
        assert out["parent"]["state"] == "SPLIT"
        assert all(c["parent_roll_id"] == str(w.roll) for c in out["children"])
        assert all(
            (c["lot_id"], c["shade"], c["gsm"]) == (str(w.lot), "NAVY", 180)
            for c in out["children"]
        )
        assert out["move"]["kind"] == "relocation"
        assert out["move"]["reference_type"] == "roll_split"

        parent_bucket = await bucket_qty(session, w, lot=w.lot, roll=w.roll)
        assert parent_bucket == (0, 0)
        for child, expected in zip(out["children"], ("50", "32.5"), strict=True):
            bucket = await bucket_qty(session, w, lot=w.lot, roll=uuid.UUID(child["id"]))
            assert bucket[0] == q(expected)
        await assert_reconciled(session, org_id)

    async def test_it_writes_roll_events_genealogy_audit_and_valid_events(
        self, client, session, org_id, world
    ) -> None:
        w = world
        out = (
            await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{w.roll}:split",
                split_body(w, "50", "32.5"),
            )
        ).json()
        async with session.begin():
            parent_events = (
                (await session.execute(select(RollEvent).where(RollEvent.roll_id == w.roll)))
                .scalars()
                .all()
            )
            child_events = (
                (await session.execute(select(RollEvent).where(RollEvent.kind == "SPLIT_CHILD")))
                .scalars()
                .all()
            )
            genealogy = (await session.execute(select(LotGenealogy))).scalars().all()
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "operations.inventory.roll.split"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "SPLIT" in {e.kind for e in parent_events}
        assert {e.related_roll_id for e in child_events} == {w.roll}
        assert len(child_events) == 2
        [edge] = genealogy
        assert (edge.relation, edge.parent_lot_id, edge.child_lot_id) == ("SPLIT", w.lot, w.lot)
        assert edge.move_id == uuid.UUID(out["move"]["move_id"])
        [event] = events
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )

    async def test_a_split_that_does_not_account_for_the_whole_roll_is_refused(
        self, client, session, org_id, world
    ) -> None:
        w = world
        for quantities in (("50", "32.4"), ("50", "32.6"), ("82.5", "0.001")):
            response = await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{w.roll}:split",
                split_body(w, *quantities),
            )
            assert response.status_code == 422, (quantities, response.text)
        # Nothing changed.
        assert (await bucket_qty(session, w, lot=w.lot, roll=w.roll))[0] == q("82.5")
        async with session.begin():
            rolls = (
                await session.execute(
                    select(func.count()).select_from(Roll).where(Roll.organization_id == org_id)
                )
            ).scalar_one()
        assert rolls == 1
        await assert_reconciled(session, org_id)

    @pytest.mark.parametrize(
        "quantities",
        [
            ("0.001", "82.499"),
            ("27.5", "27.5", "27.5"),
            ("10", "20", "30", "22.5"),
            ("41.25", "41.25"),
        ],
    )
    async def test_children_always_sum_to_the_parent_on_both_measures(
        self, client, session, org_id, world, quantities
    ) -> None:
        w = world
        out = (
            await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{w.roll}:split",
                split_body(w, *quantities),
            )
        ).json()
        assert sum(parse_qty(c["primary_qty"]) for c in out["children"]) == q("82.5")
        assert sum(parse_qty(c["secondary_qty"]) for c in out["children"]) == q("14.8")
        await assert_reconciled(session, org_id)

    async def test_custom_child_roll_numbers_and_a_collision(self, client, org_id, world) -> None:
        w = world
        body = split_body(w, "50", "32.5")
        body["children"][0]["roll_no"] = "CUT-A"
        body["children"][1]["roll_no"] = w.roll_no  # taken by the parent
        clash = await call(client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{w.roll}:split", body)
        assert clash.status_code == 409
        body["children"][1]["roll_no"] = "CUT-B"
        ok = await call(client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{w.roll}:split", body)
        assert [c["roll_no"] for c in ok.json()["children"]] == ["CUT-A", "CUT-B"]

    async def test_a_roll_splits_once(self, client, org_id, world) -> None:
        w = world
        path = f"{ROLLS}/{w.roll}:split"
        assert (
            await call(client, org_id, ROLL_WRITE, "POST", path, split_body(w, "50", "32.5"))
        ).status_code == 201
        second = await call(client, org_id, ROLL_WRITE, "POST", path, split_body(w, "40", "42.5"))
        assert second.status_code == 409
        assert "SPLIT" in second.json()["detail"]

    async def test_a_retry_with_the_same_key_replays_and_creates_no_second_set(
        self, client, session, org_id, world
    ) -> None:
        w = world
        path = f"{ROLLS}/{w.roll}:split"
        body = split_body(w, "50", "32.5")
        first = await call(client, org_id, ROLL_WRITE, "POST", path, body, key="split-1")
        again = await call(client, org_id, ROLL_WRITE, "POST", path, body, key="split-1")
        assert (first.status_code, again.status_code) == (201, 200), again.text
        assert again.json()["move"]["replayed"] is True
        assert [c["id"] for c in again.json()["children"]] == [
            c["id"] for c in first.json()["children"]
        ]
        async with session.begin():
            rolls = (
                await session.execute(
                    select(func.count()).select_from(Roll).where(Roll.organization_id == org_id)
                )
            ).scalar_one()
            splits = (
                await session.execute(
                    select(func.count()).select_from(RollEvent).where(RollEvent.kind == "SPLIT")
                )
            ).scalar_one()
        assert (rolls, splits) == (3, 1)
        different = await call(
            client, org_id, ROLL_WRITE, "POST", path, split_body(w, "40", "42.5"), key="split-1"
        )
        assert different.status_code == 409
        await assert_reconciled(session, org_id)

    async def test_a_reserved_roll_is_refused_naming_the_reservation_and_nothing_changes(
        self, client, session, org_id, world
    ) -> None:
        w = world
        async with session.begin():
            made = await ledger.reserve(
                session,
                w.t.ctx,
                item_id=w.t.fabric,
                location_id=w.t.weaving,
                quantity_milli=q("10"),
                lot_id=w.lot,
                roll_id=w.roll,
                reference_type="sales_order",
                reference_id=uuid.uuid4(),
            )
            reservation_id = made.reservation.id
        response = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{w.roll}:split",
            split_body(w, "50", "32.5"),
        )
        assert response.status_code == 409
        assert response.json()["errors"][0]["reservations"][0]["reservation_id"] == str(
            reservation_id
        )
        # The refusal rolled the whole operation back: the roll is whole and no children exist.
        got = await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{w.roll}")
        assert got.json()["state"] == "ACTIVE"
        listed = await call(client, org_id, STOCK_READ, "GET", ROLLS)
        assert len(listed.json()["items"]) == 1
        await assert_reconciled(session, org_id)

    async def test_a_roll_holding_nothing_at_that_place_is_refused(
        self, client, org_id, world
    ) -> None:
        body = split_body(world, "50", "32.5", location_id=str(world.bin_b))
        response = await call(
            client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{world.roll}:split", body
        )
        assert response.status_code == 409

    async def test_unweighed_rolls_give_unweighed_children(self, client, org_id, world) -> None:
        w = world
        unweighed = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            ROLLS,
            {
                "inventory_item_id": str(w.t.fabric),
                "lot_id": str(w.lot),
                "roll_no": "R-NOWT",
                "primary_qty": "10",
            },
        )
        roll_id = unweighed.json()["id"]
        await call(
            client,
            org_id,
            STOCK_ADJUST,
            "POST",
            "/operations/inventory/stock/opening",
            {
                "lines": [
                    {
                        "inventory_item_id": str(w.t.fabric),
                        "location_id": str(w.t.weaving),
                        "quantity": "10",
                        "lot_id": str(w.lot),
                        "roll_id": roll_id,
                    }
                ]
            },
        )
        out = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            f"{ROLLS}/{roll_id}:split",
            split_body(w, "4", "6"),
        )
        assert [c["secondary_qty"] for c in out.json()["children"]] == [None, None]


# ------------------------------------------------------------------ merge


MERGE = "/operations/inventory/rolls:merge"


def merge_body(w: World, *roll_ids: str, **extra) -> dict:  # type: ignore[type-arg]
    return {"roll_ids": list(roll_ids), "location_id": str(w.t.weaving), **extra}


class TestMerge:
    async def test_same_item_lot_and_shade_merge_into_one_roll(
        self, client, session, org_id, world
    ) -> None:
        w = world
        second = await make_roll(client, org_id, w, "R-2", "40")
        response = await call(
            client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(w, str(w.roll), second)
        )
        assert response.status_code == 201, response.text
        out = response.json()
        result = out["result"]
        assert (result["primary_qty"], result["secondary_qty"], result["state"]) == (
            "122.5",
            "22",  # 14.8 + 7.2
            "ACTIVE",
        )
        assert (result["lot_id"], result["shade"]) == (str(w.lot), "NAVY")
        assert {s["state"] for s in out["sources"]} == {"MERGED"}
        assert out["move"]["reference_type"] == "roll_merge"
        assert (await bucket_qty(session, w, lot=w.lot, roll=w.roll))[0] == 0
        assert (await bucket_qty(session, w, lot=w.lot, roll=uuid.UUID(result["id"])))[0] == q(
            "122.5"
        )
        await assert_reconciled(session, org_id)

        trace = (
            await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{result['id']}/trace")
        ).json()
        assert {n["number"] for n in trace["ancestors"]} == {"R-1", "R-2"}
        assert {n["relation"] for n in trace["ancestors"]} == {"MERGE"}
        forward = (await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{w.roll}/trace")).json()
        assert [n["id"] for n in forward["descendants"]] == [result["id"]]

    async def test_the_merged_event_matches_its_schema(
        self, client, session, org_id, world
    ) -> None:
        second = await make_roll(client, org_id, world, "R-2", "40")
        await call(
            client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(world, str(world.roll), second)
        )
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.roll.merged"
                    )
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )

    async def test_a_different_shade_is_refused_and_nothing_changes(
        self, client, session, org_id, world
    ) -> None:
        w = world
        other = await make_roll(client, org_id, w, "R-RED", "40", shade="RED")
        response = await call(
            client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(w, str(w.roll), other)
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "not_mergeable"
        assert (await bucket_qty(session, w, lot=w.lot, roll=w.roll))[0] == q("82.5")
        await assert_reconciled(session, org_id)

    async def test_a_different_lot_is_refused(self, client, org_id, world) -> None:
        w = world
        lot_b = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {"inventory_item_id": str(w.t.fabric), "lot_no": "LOT-B"},
        )
        made = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            ROLLS,
            {
                "inventory_item_id": str(w.t.fabric),
                "lot_id": lot_b.json()["id"],
                "roll_no": "R-B",
                "primary_qty": "10",
                "shade": "NAVY",
            },
        )
        await call(
            client,
            org_id,
            STOCK_ADJUST,
            "POST",
            "/operations/inventory/stock/opening",
            {
                "lines": [
                    {
                        "inventory_item_id": str(w.t.fabric),
                        "location_id": str(w.t.weaving),
                        "quantity": "10",
                        "lot_id": lot_b.json()["id"],
                        "roll_id": made.json()["id"],
                    }
                ]
            },
        )
        response = await call(
            client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(w, str(w.roll), made.json()["id"])
        )
        assert response.status_code == 422

    async def test_a_consumed_roll_cannot_be_merged(self, client, org_id, world) -> None:
        w = world
        second = await make_roll(client, org_id, w, "R-2", "40")
        third = await make_roll(client, org_id, w, "R-3", "10")
        assert (
            await call(
                client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(w, str(w.roll), second)
            )
        ).status_code == 201
        again = await call(
            client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(w, str(w.roll), third)
        )
        assert again.status_code == 409

    async def test_a_retry_with_the_same_key_replays(self, client, session, org_id, world) -> None:
        w = world
        second = await make_roll(client, org_id, w, "R-2", "40")
        body = merge_body(w, str(w.roll), second)
        first = await call(client, org_id, ROLL_WRITE, "POST", MERGE, body, key="merge-1")
        again = await call(client, org_id, ROLL_WRITE, "POST", MERGE, body, key="merge-1")
        assert (first.status_code, again.status_code) == (201, 200), again.text
        assert again.json()["result"]["id"] == first.json()["result"]["id"]
        async with session.begin():
            merges = (
                await session.execute(
                    select(func.count())
                    .select_from(RollEvent)
                    .where(RollEvent.kind == "MERGE_RESULT")
                )
            ).scalar_one()
        assert merges == 2  # one per source, not four
        third = await make_roll(client, org_id, w, "R-3", "5")
        different = await call(
            client,
            org_id,
            ROLL_WRITE,
            "POST",
            MERGE,
            merge_body(w, str(w.roll), third),
            key="merge-1",
        )
        assert different.status_code == 409
        await assert_reconciled(session, org_id)

    async def test_duplicate_or_single_rolls_are_refused(self, client, org_id, world) -> None:
        roll = str(world.roll)
        for ids in ([roll, roll], [roll]):
            response = await call(
                client, org_id, ROLL_WRITE, "POST", MERGE, merge_body(world, *ids)
            )
            assert response.status_code == 422


# ------------------------------------------------------------------ trace


class TestRollTrace:
    async def test_child_to_parent_to_lot_to_source_and_back(
        self, client, session, org_id, world
    ) -> None:
        w = world
        source = uuid.uuid4()
        purchase_lot = await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            LOTS,
            {
                "inventory_item_id": str(w.t.fabric),
                "lot_no": "LOT-SRC",
                "source_type": "purchase_order",
                "source_id": str(source),
            },
        )
        # Roll R-1's lot (LOT-A) was blended from the purchased lot.
        await call(
            client,
            org_id,
            LOT_WRITE,
            "POST",
            f"{LOTS}/{purchase_lot.json()['id']}/genealogy",
            {"child_lot_id": str(w.lot), "relation": "BLEND"},
        )
        out = (
            await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{w.roll}:split",
                split_body(w, "50", "32.5"),
            )
        ).json()
        child = out["children"][0]

        up = (await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{child['id']}/trace")).json()
        assert [n["number"] for n in up["ancestors"]] == ["R-1"]
        assert up["ancestors"][0]["relation"] == "SPLIT"
        assert up["lot"]["lot_no"] == "LOT-A"
        assert [n["number"] for n in up["lot_ancestors"]] == ["LOT-SRC"]
        lot_up = (await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{w.lot}/trace")).json()
        assert lot_up["ancestors"][0]["number"] == "LOT-SRC"
        source_lot = (
            await call(client, org_id, STOCK_READ, "GET", f"{LOTS}/{purchase_lot.json()['id']}")
        ).json()
        assert (source_lot["source_type"], source_lot["source_id"]) == (
            "purchase_order",
            str(source),
        )

        down = (await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{w.roll}/trace")).json()
        assert [n["number"] for n in down["descendants"]] == ["R-1-1", "R-1-2"]
        assert {n["lot_no"] for n in down["descendants"]} == {"LOT-A"}

    async def test_a_second_generation_split_traces_through_both(
        self, client, org_id, world
    ) -> None:
        w = world
        first = (
            await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{w.roll}:split",
                split_body(w, "50", "32.5"),
            )
        ).json()
        grandchild_parent = first["children"][0]["id"]
        second = (
            await call(
                client,
                org_id,
                ROLL_WRITE,
                "POST",
                f"{ROLLS}/{grandchild_parent}:split",
                split_body(w, "20", "30"),
            )
        ).json()
        leaf = second["children"][1]["id"]
        up = (await call(client, org_id, STOCK_READ, "GET", f"{ROLLS}/{leaf}/trace")).json()
        assert [(n["number"], n["depth"]) for n in up["ancestors"]] == [("R-1-1", 1), ("R-1", 2)]
        # Weights stay exact through two generations: the leaves of R-1-1 sum to its own.
        parent_weight = parse_qty(first["children"][0]["secondary_qty"])
        assert sum(parse_qty(c["secondary_qty"]) for c in second["children"]) == parent_weight


async def test_ledger_rows_carry_the_split_reference(client, session, org_id, world) -> None:
    w = world
    await call(
        client, org_id, ROLL_WRITE, "POST", f"{ROLLS}/{w.roll}:split", split_body(w, "50", "32.5")
    )
    async with session.begin():
        rows = (
            await session.execute(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.organization_id == org_id)
            )
        ).scalar_one()
    assert rows == 1 + 3  # the opening, then the parent out and two children in
    page = await call(
        client,
        org_id,
        STOCK_READ,
        "GET",
        "/operations/inventory/ledger",
        params={"kind": "relocation"},
    )
    assert {row["reference_type"] for row in page.json()["items"]} == {"roll_split"}
