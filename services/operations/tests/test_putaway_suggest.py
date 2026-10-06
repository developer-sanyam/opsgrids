"""`GET /operations/inventory/putaway/suggest` and `GET /operations/inventory/putaway/{move}`, over
HTTP.

The pure rule (`domain.suggest_putaway_bin`) is `test_putaway_domain.py`'s. This is what the route
adds around it: the candidates it reads from the database (live storable bins of **that**
warehouse, holding this item — this lot — or empty), the path it prints, and what it refuses to be
asked. The route and `matched_suggestion` share one rule, so a scan of the suggested bin always
matches.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, update

from app import domain
from app.inventory import stock_ops
from app.inventory import tracking_services as tracking
from app.inventory.tracking_schemas import LotIn
from app.models import InventoryMove, Location, Warehouse
from tests.putaway_fixtures import (
    PUTAWAY,
    STOCK_READ,
    STOCK_TRANSFER,
    PutawayWorld,
    build_putaway_world,
    call,
    line,
    make_location,
    opening,
    put,
    putaway_body,
)
from tests.tracking_fixtures import make_foreign_world

pytestmark = pytest.mark.integration

SUGGEST = f"{PUTAWAY}/suggest"


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def suggest(client, pw: PutawayWorld, **params: Any):  # type: ignore[no-untyped-def]
    params = {
        "inventory_item_id": str(pw.dye),
        "warehouse_id": str(pw.warehouse),
        **{k: v for k, v in params.items() if v is not None},
    }
    return await call(client, pw.org_id, [STOCK_TRANSFER], "GET", SUGGEST, params=params)


async def stock(session, pw: PutawayWorld, item: uuid.UUID, *where: uuid.UUID, **kw: Any) -> None:
    async with session.begin():
        await stock_ops.post_opening_stock(
            session,
            pw.w.t.ctx,
            opening([line(item, loc, "3", **kw) for loc in where]),
            idempotency_key=None,
            occurred_on_explicit=False,
        )


async def label(session, pw: PutawayWorld, code: str, barcode: str | None) -> None:
    """Set (or clear) the barcode of the live location `code` in WH-WEAVE."""
    async with session.begin():
        await session.execute(
            update(Location)
            .where(
                Location.organization_id == pw.org_id,
                Location.warehouse_id == pw.warehouse,
                Location.code == code,
                Location.deleted_at.is_(None),
            )
            .values(barcode=barcode)
        )


async def retire(session, *ids: uuid.UUID) -> None:
    async with session.begin():
        await session.execute(
            update(Location).where(Location.id.in_(ids)).values(deleted_at=datetime.now(UTC))
        )


class TestTheSuggestion:
    async def test_an_empty_bin_with_the_lowest_pick_sequence_is_offered_with_its_path(
        self, client, pw
    ) -> None:
        resp = await suggest(client, pw)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "suggestion": {
                "location_id": str(pw.rack_bin),
                "code": "R1-B01",
                "barcode": "RACK-1-B01",
                "name": "Rack 1 bin 1",
                "path": "RACK-1 / R1-B01",
                "reason": "empty_bin",
            }
        }

    async def test_the_suggestion_carries_the_bins_own_barcode_so_a_worker_can_match_a_scan(
        self, client, session, pw
    ) -> None:
        """Workers lack `location.read`, so the suggestion is how the device learns the label of
        the bin it is asking the worker to scan. The barcode offered is the one a scan resolves."""
        body = (await suggest(client, pw)).json()["suggestion"]
        assert body["barcode"] == "RACK-1-B01"
        scanned = await put(client, pw, putaway_body(pw, to_barcode=body["barcode"]))
        assert scanned.status_code == 201, scanned.text
        assert scanned.json()["to_location"]["id"] == body["location_id"]
        assert scanned.json()["matched_suggestion"] is True

    async def test_a_bin_with_no_barcode_is_never_offered_so_the_suggestion_always_has_one(
        self, client, session, pw
    ) -> None:
        """A put-away resolves its destination by barcode alone: a bin with no label cannot be
        scanned, so offering it would send the worker to a bin the app then cannot store into.
        With every barcoded bin gone, DEFAULT (no barcode) is the only storable bin left — and the
        answer is no suggestion, not DEFAULT."""
        await retire(session, pw.rack_bin, pw.rack_bin2, pw.shelf)
        resp = await suggest(client, pw)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"suggestion": None}

    async def test_a_bin_already_holding_the_item_is_preferred_to_an_emptier_walk_position(
        self, client, session, pw
    ) -> None:
        await stock(session, pw, pw.dye, pw.shelf)  # SHELF-1 has no pick sequence at all
        body = (await suggest(client, pw)).json()["suggestion"]
        assert (body["code"], body["reason"]) == ("SHELF-1", "holds_item")

    async def test_the_lot_narrows_what_holding_the_item_means(self, client, session, pw) -> None:
        """Fabric lot A sits in the shelf. Asking for lot A says shelf; asking for another lot
        does not — it falls through to an empty bin."""
        w = pw.w
        await stock(session, pw, pw.fabric, pw.shelf, lot_id=w.lot, roll_id=w.roll)
        same = await suggest(client, pw, inventory_item_id=str(pw.fabric), lot_id=str(w.lot))
        assert same.json()["suggestion"]["code"] == "SHELF-1"
        assert same.json()["suggestion"]["reason"] == "holds_item"
        unspecified = await suggest(client, pw, inventory_item_id=str(pw.fabric))
        assert unspecified.json()["suggestion"]["reason"] == "holds_item"
        async with session.begin():
            other = await tracking.create_lot(
                session, w.t.ctx, LotIn(inventory_item_id=pw.fabric, lot_no="LOT-B")
            )
            other_id = other.id
        different = await suggest(
            client, pw, inventory_item_id=str(pw.fabric), lot_id=str(other_id)
        )
        assert different.json()["suggestion"]["code"] == "R1-B01"
        assert different.json()["suggestion"]["reason"] == "empty_bin"

    async def test_the_walk_order_is_pick_sequence_then_code(self, client, session, pw) -> None:
        # Take both sequenced bins out of play; what is left has no sequence, so code decides.
        await stock(session, pw, pw.w.t.buttons, pw.rack_bin, pw.rack_bin2)
        await label(session, pw, "DEFAULT", "DEFAULT-BC")  # a bin with no label is never offered
        await label(session, pw, "BIN-B", "BIN-B-BC")
        body = (await suggest(client, pw)).json()["suggestion"]
        # Candidates left: DEFAULT (the warehouse's default bin), BIN-B, SHELF-1 — all unsequenced.
        assert body["code"] == "BIN-B" and body["reason"] == "empty_bin"

    async def test_no_storable_bin_left_is_a_null_suggestion_not_an_error(
        self, client, session, pw
    ) -> None:
        w = pw.w
        await stock(
            session, pw, w.t.buttons, w.t.weaving, w.bin_b, pw.rack_bin, pw.rack_bin2, pw.shelf
        )
        resp = await suggest(client, pw)
        assert resp.status_code == 200
        assert resp.json() == {"suggestion": None}

    async def test_only_the_asked_warehouse_is_searched(self, client, session, pw) -> None:
        in_finishing = await suggest(client, pw, warehouse_id=str(await _finishing(session, pw)))
        # WH-FIN's other storable bin, DEFAULT, has no barcode and so is never offered.
        assert in_finishing.json()["suggestion"]["code"] == "FIN-B01"

    async def test_a_soft_deleted_bin_is_never_offered(self, client, session, pw) -> None:
        async with session.begin():
            await session.execute(
                update(Location)
                .where(Location.id == pw.rack_bin)
                .values(deleted_at=datetime.now(UTC))
            )
        assert (await suggest(client, pw)).json()["suggestion"]["code"] == "R1-B02"

    async def test_receiving_staging_zones_and_racks_are_never_offered(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            await session.execute(
                update(Location)
                .where(Location.id.in_([pw.rack_bin, pw.rack_bin2, pw.shelf, pw.w.bin_b]))
                .values(deleted_at=datetime.now(UTC))
            )
        # Left alive in WH-WEAVE: RCV-1, STG-1 (stocked and not storable), RACK-1, the default bin.
        await label(session, pw, "DEFAULT", "DEFAULT-BC")
        body = (await suggest(client, pw)).json()["suggestion"]
        assert body["code"] == "DEFAULT"  # the one storable bin left

    async def test_the_suggestion_is_exactly_what_a_scan_is_judged_against(
        self, client, session, pw
    ) -> None:
        await stock(session, pw, pw.dye, pw.rack_bin2)  # R1-B02 now holds the item
        offered = (await suggest(client, pw)).json()["suggestion"]
        assert offered["location_id"] == str(pw.rack_bin2)
        barcode = {"R1-B02": "RACK-1-B02"}[offered["code"]]
        matched = await put(client, pw, putaway_body(pw, to_barcode=barcode))
        assert matched.json()["matched_suggestion"] is True
        other = await put(client, pw, putaway_body(pw, to_barcode="SHELF-1-BC"), key="other")
        assert other.json()["matched_suggestion"] is False


class TestOnlyABinAScanCanResolveIsOffered:
    """`POST /putaway` resolves the destination by `to_barcode` and nothing else, so a bin with a
    NULL or empty barcode can never be stored into. It is filtered out *in the query*, before the
    candidate cap: a post-filter would let barcode-less bins at the head of the walk crowd every
    usable one out of the capped list."""

    @pytest.mark.parametrize("barcode", [None, ""])
    async def test_a_bin_with_a_null_or_empty_barcode_is_not_offered_even_first_in_the_walk(
        self, client, session, pw, barcode
    ) -> None:
        await label(session, pw, "R1-B01", barcode)  # pick sequence 1: the best walk position
        body = (await suggest(client, pw)).json()["suggestion"]
        assert (body["code"], body["reason"]) == ("R1-B02", "empty_bin")
        assert body["barcode"] == "RACK-1-B02"

    @pytest.mark.parametrize("barcode", [None, ""])
    async def test_a_barcode_less_bin_already_holding_the_item_does_not_win_the_holding_rule(
        self, client, session, pw, barcode
    ) -> None:
        await label(session, pw, "SHELF-1", barcode)
        await stock(session, pw, pw.dye, pw.shelf)
        body = (await suggest(client, pw)).json()["suggestion"]
        assert (body["code"], body["reason"]) == ("R1-B01", "empty_bin")

    async def test_when_every_storable_bin_lacks_a_barcode_there_is_no_suggestion(
        self, client, session, pw
    ) -> None:
        """Documented outcome: `{"suggestion": null}`, the same answer as "no free bin". The
        warehouse is not an error; there is simply no bin a scan could confirm."""
        for code in ("R1-B01", "R1-B02", "SHELF-1"):
            await label(session, pw, code, None)
        resp = await suggest(client, pw)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"suggestion": None}

    async def test_barcode_less_bins_beyond_the_cap_do_not_hide_the_barcoded_one(
        self, client, session, pw
    ) -> None:
        """More barcode-less bins than the per-category cap, every one ahead of the barcoded bin
        in the walk (pick sequence 0, against its 1). Were the barcode filtered after the cap, the
        candidate list would hold only unusable bins and the answer would be null."""
        for i in range(domain.SUGGEST_CANDIDATE_CAP + 1):
            await make_location(session, pw, kind="bin", code=f"NB-{i:02d}", pick_sequence=0)
        await retire(session, pw.rack_bin2, pw.shelf)
        body = (await suggest(client, pw)).json()["suggestion"]
        assert body is not None
        assert body["code"] == "R1-B01"

    async def test_the_barcode_offered_is_always_one_a_scan_resolves(
        self, client, session, pw
    ) -> None:
        for n in range(3):  # take whichever bin is offered out of play, then ask again
            body = (await suggest(client, pw)).json()["suggestion"]
            assert body is not None
            assert body["barcode"]
            scanned = await put(
                client, pw, putaway_body(pw, to_barcode=body["barcode"]), key=f"scan-{n}"
            )
            assert scanned.status_code == 201, scanned.text
            assert scanned.json()["to_location"]["id"] == body["location_id"]
            await stock(session, pw, pw.w.t.buttons, uuid.UUID(body["location_id"]))


class TestTheCapCannotChangeTheAnswer:
    """The query loads at most `SUGGEST_CANDIDATE_CAP` bins per category and the rule picks the
    first of each, so the SQL order and `domain`'s order must be one total order or the cap changes
    the answer: pick sequence ascending with none last, then the code as plain code points
    (`COLLATE "C"` in SQL, `str` comparison in Python). A locale collation (this test database is
    `en_US.utf8`) puts `a01` before `A-FIRST` and ignores the hyphen in `R1-B02` against `R1B01`;
    code points do neither."""

    async def _crowd(self, session, pw: PutawayWorld, *, stocked: bool) -> None:
        """30 lower-case bins that a locale sorts first, and `A-FIRST`, which code points do. The
        sequenced rack bins are retired so the walk position is decided by the code alone."""
        await retire(session, pw.rack_bin, pw.rack_bin2)
        made = [
            await make_location(session, pw, kind="bin", code=f"a{i:02d}", barcode=f"LC-{i:02d}")
            for i in range(1, 31)
        ]
        made.append(await make_location(session, pw, kind="bin", code="A-FIRST", barcode="UC-1"))
        for code in ("DEFAULT", "BIN-B"):
            await label(session, pw, code, f"{code}-BC")
        if stocked:
            await stock(session, pw, pw.dye, *made)

    @pytest.mark.parametrize("cap", [1, 2, 5, 25, 1000])
    async def test_an_empty_bin_choice_is_the_same_at_every_cap(
        self, client, session, pw, monkeypatch, cap
    ) -> None:
        await self._crowd(session, pw, stocked=False)
        monkeypatch.setattr(domain, "SUGGEST_CANDIDATE_CAP", cap)
        body = (await suggest(client, pw)).json()["suggestion"]
        # Code points: "A-FIRST" < "BIN-B" < "DEFAULT" < "SHELF-1" < "a01" ...
        assert (body["code"], body["reason"]) == ("A-FIRST", "empty_bin")

    @pytest.mark.parametrize("cap", [1, 2, 5, 25, 1000])
    async def test_a_holding_bin_choice_is_the_same_at_every_cap(
        self, client, session, pw, monkeypatch, cap
    ) -> None:
        await self._crowd(session, pw, stocked=True)
        monkeypatch.setattr(domain, "SUGGEST_CANDIDATE_CAP", cap)
        body = (await suggest(client, pw)).json()["suggestion"]
        assert (body["code"], body["reason"]) == ("A-FIRST", "holds_item")

    async def test_the_route_agrees_with_the_pure_rule_over_every_bin_of_the_warehouse(
        self, client, session, pw
    ) -> None:
        """What the route answers (capped, ordered in SQL) is what `domain.suggest_putaway_bin`
        answers over **every** storable barcoded bin of the warehouse, uncapped."""
        await self._crowd(session, pw, stocked=False)
        async with session.begin():
            rows = (
                await session.execute(
                    select(Location.id, Location.code, Location.kind, Location.pick_sequence).where(
                        Location.organization_id == pw.org_id,
                        Location.warehouse_id == pw.warehouse,
                        Location.deleted_at.is_(None),
                        Location.kind.in_(domain.STORABLE_KINDS),
                        Location.barcode.is_not(None),
                    )
                )
            ).all()
        assert len(rows) > domain.SUGGEST_CANDIDATE_CAP
        expected = domain.suggest_putaway_bin(
            [
                domain.PutawayBin(
                    location_id=r.id,
                    code=r.code,
                    kind=r.kind,
                    barcode="x",
                    pick_sequence=r.pick_sequence,
                    holds_item=False,
                    is_empty=True,
                )
                for r in rows
            ]
        )
        assert expected is not None
        body = (await suggest(client, pw)).json()["suggestion"]
        assert body["location_id"] == str(expected.location_id)


async def _finishing(session, pw: PutawayWorld) -> uuid.UUID:
    async with session.begin():
        return (
            await session.execute(
                select(Warehouse.id).where(
                    Warehouse.organization_id == pw.org_id, Warehouse.code == "WH-FIN"
                )
            )
        ).scalar_one()


class TestWhatMayBeAsked:
    async def test_unknown_ids_are_404(self, client, pw) -> None:
        for params in (
            {"inventory_item_id": str(uuid.uuid4())},
            {"warehouse_id": str(uuid.uuid4())},
            {"lot_id": str(uuid.uuid4())},
        ):
            assert (await suggest(client, pw, **params)).status_code == 404, params

    async def test_another_tenants_ids_are_404(self, client, admin_session, pw) -> None:
        foreign = await make_foreign_world(admin_session)
        for params in (
            {"inventory_item_id": str(foreign.t.dye)},
            {"warehouse_id": str(foreign.warehouse_weave)},
            {"lot_id": str(foreign.lot), "inventory_item_id": str(foreign.t.fabric)},
        ):
            assert (await suggest(client, pw, **params)).status_code == 404, params

    async def test_a_lot_of_another_item_is_404(self, client, pw) -> None:
        resp = await suggest(client, pw, lot_id=str(pw.w.lot))  # the fabric's lot, asked of the dye
        assert resp.status_code == 404

    async def test_the_item_and_the_warehouse_are_required_and_ids_are_uuids(
        self, client, pw
    ) -> None:
        bare = await call(client, pw.org_id, [STOCK_TRANSFER], "GET", SUGGEST)
        assert bare.status_code == 422
        assert {e["field"] for e in bare.json()["errors"]} == {"inventory_item_id", "warehouse_id"}
        for params in ({"inventory_item_id": "x"}, {"warehouse_id": "x"}, {"lot_id": "x"}):
            assert (await suggest(client, pw, **params)).status_code == 422

    async def test_the_route_is_not_swallowed_by_the_move_id_route(self, client, pw) -> None:
        """`/putaway/suggest` is registered before `/putaway/{move_id}`; had it not been, "suggest"
        would be read as a move id and answered 422."""
        assert (await suggest(client, pw)).status_code == 200


class TestThePutawayDetail:
    async def test_a_move_that_is_not_a_put_away_has_no_detail(self, client, pw) -> None:
        transfer = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            "/operations/inventory/stock/transfers",
            json={
                "inventory_item_id": str(pw.dye),
                "from_location_id": str(pw.receiving),
                "to_location_id": str(pw.rack_bin),
                "quantity": "1",
            },
        )
        resp = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{transfer.json()['move_id']}"
        )
        assert resp.status_code == 404

    async def test_an_unknown_move_and_a_malformed_id(self, client, pw) -> None:
        unknown = await call(client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{uuid.uuid4()}")
        assert unknown.status_code == 404
        bad = await call(client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/not-a-uuid")
        assert bad.status_code == 422

    async def test_the_detail_names_the_bin_the_worker_and_the_verdict(self, client, pw) -> None:
        made = await put(client, pw, putaway_body(pw, to_barcode="SHELF-1-BC"))
        resp = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{made.json()['move_id']}"
        )
        body = resp.json()
        assert body["to_location"] == {
            "id": str(pw.shelf),
            "code": "SHELF-1",
            "name": "Shelf 1",
            "kind": "shelf",
        }
        assert (body["worker_id"], body["matched_suggestion"], body["has_location"]) == (
            None,
            False,
            False,
        )
        assert body["captured_at"]


async def test_a_put_away_may_carry_an_earlier_date_of_occurrence(client, session, pw) -> None:
    made = await put(client, pw, putaway_body(pw, occurred_on="2026-09-30"))
    assert made.status_code == 201
    async with session.begin():
        move = (
            await session.execute(
                select(InventoryMove).where(InventoryMove.id == made.json()["move_id"])
            )
        ).scalar_one()
    assert str(move.occurred_on) == "2026-09-30"
