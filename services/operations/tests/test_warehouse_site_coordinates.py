"""A warehouse's position on a map: `lat_micro` / `lng_micro` on create, patch and read, and
`GET /operations/warehouses/map`.

These are **site** coordinates — a business address, approved to be stored and shown — not a
worker's position (that is `test_putaway_audit.py`'s walls). They are integer micro-degrees, both
set or both null, each in range; a PATCH is judged on the **merged** state (the persisted half
counts), an explicit `null` for both clears the site, and the database CHECKs are the backstop for
any writer that skips Python.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    InventoryBalance,
    Location,
    Warehouse,
    WarehouseFloor,
)
from tests.putaway_fixtures import (
    WAREHOUSE_READ,
    WAREHOUSE_WRITE,
    PutawayWorld,
    audit_rows,
    build_putaway_world,
    call,
    outbox_rows,
)

pytestmark = pytest.mark.integration

WAREHOUSES = "/operations/warehouses"
LAT, LNG = 18_520_430, 73_856_744  # a site: Pune, to six places
LAT2, LNG2 = 12_971_599, 77_594_563


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def patch(client, pw: PutawayWorld, body: dict[str, Any], warehouse: uuid.UUID | None = None):  # type: ignore[no-untyped-def]
    return await call(
        client,
        pw.org_id,
        [WAREHOUSE_WRITE],
        "PATCH",
        f"{WAREHOUSES}/{warehouse or pw.warehouse}",
        json=body,
    )


async def stored(session, pw: PutawayWorld) -> tuple[int | None, int | None]:
    async with session.begin():
        wh = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == pw.org_id, Warehouse.id == pw.warehouse
                )
            )
        ).scalar_one()
        return wh.lat_micro, wh.lng_micro


async def set_site(client, pw: PutawayWorld, lat: int | None = LAT, lng: int | None = LNG) -> None:
    resp = await patch(client, pw, {"lat_micro": lat, "lng_micro": lng})
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ create


class TestCreate:
    async def test_a_warehouse_is_created_with_its_position(self, client, session, pw) -> None:
        resp = await call(
            client,
            pw.org_id,
            [WAREHOUSE_WRITE],
            "POST",
            WAREHOUSES,
            json={"code": "WH-NEW", "name": "New Depot", "lat_micro": LAT, "lng_micro": LNG},
        )
        assert resp.status_code == 201, resp.text
        assert (resp.json()["lat_micro"], resp.json()["lng_micro"]) == (LAT, LNG)

    async def test_a_warehouse_without_a_position_reads_back_null_not_zero(
        self, client, session, pw
    ) -> None:
        resp = await call(
            client,
            pw.org_id,
            [WAREHOUSE_WRITE],
            "POST",
            WAREHOUSES,
            json={"code": "W2", "name": "W2"},
        )
        assert resp.status_code == 201
        assert (resp.json()["lat_micro"], resp.json()["lng_micro"]) == (None, None)

    @pytest.mark.parametrize(
        "coords",
        [
            {"lat_micro": LAT},
            {"lng_micro": LNG},
            {"lat_micro": LAT, "lng_micro": None},
            {"lat_micro": 90_000_001, "lng_micro": 0},
            {"lat_micro": 0, "lng_micro": -180_000_001},
            {"lat_micro": 12.97, "lng_micro": 77.59},
            {"lat_micro": "12971600", "lng_micro": "77594600"},
            {"lat_micro": True, "lng_micro": 1},
        ],
        ids=[
            "lat-only",
            "lng-only",
            "lng-null",
            "lat-range",
            "lng-range",
            "floats",
            "strings",
            "bool",
        ],
    )
    async def test_bad_coordinates_are_a_422_and_create_nothing(
        self, client, session, pw, coords
    ) -> None:
        resp = await call(
            client,
            pw.org_id,
            [WAREHOUSE_WRITE],
            "POST",
            WAREHOUSES,
            json={"code": "WH-BAD", "name": "Bad Depot", **coords},
        )
        assert resp.status_code == 422, resp.text
        async with session.begin():
            made = (
                await session.execute(
                    select(Warehouse).where(
                        Warehouse.organization_id == pw.org_id, Warehouse.code == "WH-BAD"
                    )
                )
            ).scalar_one_or_none()
        assert made is None

    async def test_the_inclusive_edges_are_accepted(self, client, pw) -> None:
        resp = await call(
            client,
            pw.org_id,
            [WAREHOUSE_WRITE],
            "POST",
            WAREHOUSES,
            json={
                "code": "WH-EDGE",
                "name": "Edge",
                "lat_micro": -90_000_000,
                "lng_micro": 180_000_000,
            },
        )
        assert resp.status_code == 201, resp.text


# ------------------------------------------------------------------ patch: the merged state


class TestPatchJudgesTheMergedState:
    async def test_setting_both_from_nothing(self, client, session, pw) -> None:
        resp = await patch(client, pw, {"lat_micro": LAT, "lng_micro": LNG})
        assert resp.status_code == 200 and (resp.json()["lat_micro"], resp.json()["lng_micro"]) == (
            LAT,
            LNG,
        )
        assert await stored(session, pw) == (LAT, LNG)

    async def test_changing_only_the_latitude_keeps_the_persisted_longitude(
        self, client, session, pw
    ) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, {"lat_micro": LAT2})
        assert resp.status_code == 200, resp.text
        assert await stored(session, pw) == (LAT2, LNG)

    async def test_changing_only_the_longitude_keeps_the_persisted_latitude(
        self, client, session, pw
    ) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, {"lng_micro": LNG2})
        assert resp.status_code == 200, resp.text
        assert await stored(session, pw) == (LAT, LNG2)

    @pytest.mark.parametrize("half", [{"lat_micro": LAT}, {"lng_micro": LNG}])
    async def test_one_half_onto_a_site_with_no_position_is_refused_and_nothing_persists(
        self, client, session, pw, half
    ) -> None:
        resp = await patch(client, pw, half)
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"][0]["code"] == "invalid_coordinates"
        assert await stored(session, pw) == (None, None)

    @pytest.mark.parametrize("half", [{"lat_micro": None}, {"lng_micro": None}])
    async def test_nulling_one_half_of_a_placed_site_is_refused(
        self, client, session, pw, half
    ) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, half)
        assert resp.status_code == 422, resp.text
        assert await stored(session, pw) == (LAT, LNG)

    async def test_an_explicit_null_for_both_clears_the_site(self, client, session, pw) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, {"lat_micro": None, "lng_micro": None})
        assert resp.status_code == 200, resp.text
        assert (resp.json()["lat_micro"], resp.json()["lng_micro"]) == (None, None)
        assert await stored(session, pw) == (None, None)

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"lat_micro": 90_000_001}, "lat_micro"),
            ({"lat_micro": -90_000_001, "lng_micro": 0}, "lat_micro"),
            ({"lng_micro": 180_000_001}, "lng_micro"),
            ({"lat_micro": 0, "lng_micro": -180_000_001}, "lng_micro"),
        ],
    )
    async def test_an_out_of_range_value_is_refused_even_beside_a_valid_half(
        self, client, session, pw, body, field
    ) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, body)
        assert resp.status_code == 422, resp.text
        assert field in {e["field"] for e in resp.json()["errors"]}
        assert await stored(session, pw) == (LAT, LNG)

    async def test_both_out_of_range_names_both_fields(self, client, session, pw) -> None:
        resp = await patch(client, pw, {"lat_micro": 95_000_000, "lng_micro": 190_000_000})
        assert resp.status_code == 422
        assert {e["field"] for e in resp.json()["errors"]} == {"lat_micro", "lng_micro"}

    @pytest.mark.parametrize(
        "bad",
        [
            {"lat_micro": 12.97, "lng_micro": 77.59},
            {"lat_micro": 12_971_600.0, "lng_micro": 77_594_600},
            {"lat_micro": "12971600", "lng_micro": "77594600"},
            {"lat_micro": True, "lng_micro": 1},
            {"lat_micro": [1], "lng_micro": 1},
        ],
    )
    async def test_a_value_that_is_not_an_integer_is_a_422(self, client, session, pw, bad) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, bad)
        assert resp.status_code == 422, resp.text
        assert await stored(session, pw) == (LAT, LNG)

    async def test_the_inclusive_edges_are_accepted(self, client, session, pw) -> None:
        for lat, lng in ((90_000_000, 180_000_000), (-90_000_000, -180_000_000)):
            resp = await patch(client, pw, {"lat_micro": lat, "lng_micro": lng})
            assert resp.status_code == 200, resp.text
        assert await stored(session, pw) == (-90_000_000, -180_000_000)

    async def test_an_unrelated_patch_leaves_the_position_alone(self, client, session, pw) -> None:
        await set_site(client, pw)
        resp = await patch(client, pw, {"location": "Plot 4, Hinjewadi"})
        assert resp.status_code == 200
        assert (resp.json()["lat_micro"], resp.json()["lng_micro"]) == (LAT, LNG)
        assert await stored(session, pw) == (LAT, LNG)

    async def test_a_rename_and_a_move_in_one_patch(self, client, session, pw) -> None:
        resp = await patch(
            client, pw, {"name": "Weaving Store II", "lat_micro": LAT, "lng_micro": LNG}
        )
        assert resp.status_code == 200, resp.text
        assert await stored(session, pw) == (LAT, LNG)

    async def test_a_refused_patch_changes_nothing_at_all(self, client, session, pw) -> None:
        """A rename that collides, beside a position that would have been fine: neither lands."""
        await set_site(client, pw)
        resp = await patch(client, pw, {"name": "Finishing", "lat_micro": LAT2, "lng_micro": LNG2})
        assert resp.status_code == 409, resp.text
        assert await stored(session, pw) == (LAT, LNG)

    async def test_a_code_is_still_immutable(self, client, pw) -> None:
        resp = await patch(client, pw, {"code": "NEW", "lat_micro": LAT, "lng_micro": LNG})
        assert resp.status_code == 422

    async def test_the_position_is_read_back_on_the_list(self, client, pw) -> None:
        await set_site(client, pw)
        listed = await call(client, pw.org_id, [WAREHOUSE_READ], "GET", WAREHOUSES)
        mine = next(w for w in listed.json()["items"] if w["id"] == str(pw.warehouse))
        assert (mine["lat_micro"], mine["lng_micro"]) == (LAT, LNG)
        other = next(w for w in listed.json()["items"] if w["code"] == "WH-FIN")
        assert (other["lat_micro"], other["lng_micro"]) == (None, None)

    async def test_a_change_of_position_is_audited_and_announced_by_field_name(
        self, client, session, pw
    ) -> None:
        await patch(client, pw, {"lat_micro": LAT, "lng_micro": LNG})
        [row] = await audit_rows(session, pw.org_id, action="warehouse.updated")
        assert str(row.entity_id) == str(pw.warehouse)
        assert row.before == {"lat_micro": None, "lng_micro": None}
        [event] = await outbox_rows(
            session, pw.org_id, event_type="operations.inventory.warehouse.updated"
        )
        assert event.payload["changed"] == ["lat_micro", "lng_micro"]
        assert str(LAT) not in str(event.payload) and str(LNG) not in str(event.payload)


# ------------------------------------------------------------------ the database backstop


class TestTheWarehouseChecks:
    @pytest.mark.parametrize(
        ("set_clause", "constraint"),
        [
            ("lat_micro = 1, lng_micro = NULL", "ck_warehouses_coords_paired"),
            ("lat_micro = NULL, lng_micro = 1", "ck_warehouses_coords_paired"),
            ("lat_micro = 90000001, lng_micro = 0", "ck_warehouses_lat_in_range"),
            ("lat_micro = -90000001, lng_micro = 0", "ck_warehouses_lat_in_range"),
            ("lat_micro = 0, lng_micro = 180000001", "ck_warehouses_lng_in_range"),
            ("lat_micro = 0, lng_micro = -180000001", "ck_warehouses_lng_in_range"),
        ],
    )
    async def test_a_raw_update_that_breaks_a_check_is_refused_by_name(
        self, session, pw, set_clause, constraint
    ) -> None:
        with pytest.raises(IntegrityError, match=constraint):
            async with session.begin():
                await session.execute(
                    text(f"UPDATE operations.warehouses SET {set_clause} WHERE id = :id"),  # noqa: S608
                    {"id": pw.warehouse},
                )

    async def test_the_edges_the_checks_allow_are_accepted(self, session, pw) -> None:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE operations.warehouses SET lat_micro = -90000000, lng_micro = 180000000 "
                    "WHERE id = :id"
                ),
                {"id": pw.warehouse},
            )
        assert await stored(session, pw) == (-90_000_000, 180_000_000)


# ------------------------------------------------------------------ the map


async def map_of(client, pw: PutawayWorld, org: uuid.UUID | None = None) -> dict[str, Any]:
    resp = await call(client, org or pw.org_id, [WAREHOUSE_READ], "GET", f"{WAREHOUSES}/map")
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


class TestTheMap:
    async def test_placed_sites_and_the_ones_still_to_place_are_listed_apart(
        self, client, session, pw
    ) -> None:
        await set_site(client, pw)
        body = await map_of(client, pw)
        assert set(body) == {"sites", "without_coordinates"}
        [site] = body["sites"]
        assert (site["id"], site["code"], site["name"]) == (
            str(pw.warehouse),
            "WH-WEAVE",
            "Weaving Store",
        )
        assert (site["lat_micro"], site["lng_micro"]) == (LAT, LNG)
        assert set(site) == {
            "id",
            "code",
            "name",
            "location",
            "lat_micro",
            "lng_micro",
            "floor_count",
            "stocked_location_count",
        }
        assert [w["code"] for w in body["without_coordinates"]] == ["WH-FIN"]
        assert set(body["without_coordinates"][0]) == {"id", "code", "name", "location"}

    async def test_with_no_coordinates_anywhere_every_warehouse_is_to_place(
        self, client, pw
    ) -> None:
        body = await map_of(client, pw)
        assert body["sites"] == []
        assert sorted(w["code"] for w in body["without_coordinates"]) == ["WH-FIN", "WH-WEAVE"]

    async def test_the_map_route_is_not_swallowed_by_the_warehouse_id_route(
        self, client, pw
    ) -> None:
        resp = await call(client, pw.org_id, [WAREHOUSE_READ], "GET", f"{WAREHOUSES}/map")
        assert resp.status_code == 200  # not 422 ("map" is no uuid)

    async def test_floors_count_only_live_ones(self, client, session, pw) -> None:
        await set_site(client, pw)
        async with session.begin():
            for number, deleted in ((1, None), (2, None), (3, datetime.now(UTC))):
                session.add(
                    WarehouseFloor(
                        organization_id=pw.org_id,
                        warehouse_id=pw.warehouse,
                        floor_number=number,
                        name=f"F{number}",
                        deleted_at=deleted,
                    )
                )
        assert (await map_of(client, pw))["sites"][0]["floor_count"] == 2

    async def test_stocked_locations_count_bins_holding_stock_on_site_now(
        self, client, session, pw
    ) -> None:
        await set_site(client, pw)
        # build_putaway_world stocks RCV-1 and STG-1 (dye) and RCV-1 (the fabric roll) in WH-WEAVE.
        assert (await map_of(client, pw))["sites"][0]["stocked_location_count"] == 2
        async with session.begin():
            session.add(  # in transit: not in a bin on the site
                InventoryBalance(
                    organization_id=pw.org_id,
                    inventory_item_id=pw.dye,
                    location_id=pw.rack_bin,
                    status="IN_TRANSIT",
                    quantity_milli=5_000,
                    reserved_milli=0,
                )
            )
            session.add(  # a zero balance is a bucket that was emptied, not stock
                InventoryBalance(
                    organization_id=pw.org_id,
                    inventory_item_id=pw.dye,
                    location_id=pw.rack_bin2,
                    status="AVAILABLE",
                    quantity_milli=0,
                    reserved_milli=0,
                )
            )
        assert (await map_of(client, pw))["sites"][0]["stocked_location_count"] == 2
        async with session.begin():
            await session.execute(
                update(Location)
                .where(Location.id == pw.staging)
                .values(deleted_at=datetime.now(UTC))
            )
        assert (await map_of(client, pw))["sites"][0]["stocked_location_count"] == 1

    async def test_each_placed_site_counts_its_own_bins_and_an_unplaced_one_is_left_out(
        self, client, session, pw
    ) -> None:
        """Only the placed sites' bins are probed (the count is for a marker popup), and each site
        gets its own figure, not a share of another's."""
        async with session.begin():
            finished = (
                await session.execute(
                    select(Location.warehouse_id).where(Location.id == pw.other_warehouse_bin)
                )
            ).scalar_one()
            session.add(
                InventoryBalance(
                    organization_id=pw.org_id,
                    inventory_item_id=pw.dye,
                    location_id=pw.other_warehouse_bin,
                    status="AVAILABLE",
                    quantity_milli=3_000,
                    reserved_milli=0,
                )
            )
        await set_site(client, pw)
        body = await map_of(client, pw)
        assert [(s["code"], s["stocked_location_count"]) for s in body["sites"]] == [
            ("WH-WEAVE", 2)
        ]
        assert [w["code"] for w in body["without_coordinates"]] == ["WH-FIN"]
        placed = await patch(client, pw, {"lat_micro": LAT2, "lng_micro": LNG2}, finished)
        assert placed.status_code == 200, placed.text
        counts = {
            s["code"]: s["stocked_location_count"] for s in (await map_of(client, pw))["sites"]
        }
        assert counts == {"WH-WEAVE": 2, "WH-FIN": 1}

    async def test_a_soft_deleted_warehouse_is_on_neither_list(self, client, session, pw) -> None:
        await set_site(client, pw)
        async with session.begin():
            await session.execute(
                update(Warehouse)
                .where(Warehouse.id == pw.warehouse)
                .values(deleted_at=datetime.now(UTC))
            )
        body = await map_of(client, pw)
        assert body["sites"] == []
        assert [w["code"] for w in body["without_coordinates"]] == ["WH-FIN"]

    async def test_another_tenants_sites_are_never_listed(
        self, client, session, admin_session, pw
    ) -> None:
        await set_site(client, pw)
        other = uuid.uuid4()
        elsewhere = await build_putaway_world(admin_session, other)
        async with admin_session.begin():
            await admin_session.execute(
                update(Warehouse)
                .where(Warehouse.id == elsewhere.warehouse)
                .values(lat_micro=LAT2, lng_micro=LNG2)
            )
        mine = await map_of(client, pw)
        theirs = await map_of(client, pw, other)
        assert [s["id"] for s in mine["sites"]] == [str(pw.warehouse)]
        assert [s["id"] for s in theirs["sites"]] == [str(elsewhere.warehouse)]

    async def test_the_counts_are_three_queries_however_many_sites_there_are(
        self, client, session, pw
    ) -> None:
        async def statements() -> int:
            seen: list[str] = []
            engine = session.bind.sync_engine  # type: ignore[union-attr]

            def count_it(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
                seen.append(statement)

            event.listen(engine, "before_cursor_execute", count_it)
            try:
                await map_of(client, pw)
            finally:
                event.remove(engine, "before_cursor_execute", count_it)
            return len(seen)

        # No site has a position yet, so there is nothing to count and the grouped stocked-bin
        # query is not issued; the first placed site brings it, and then it stays one query.
        none_placed = await statements()
        await set_site(client, pw)
        few = await statements()
        assert few == none_placed + 1
        async with session.begin():
            for i in range(12):
                session.add(
                    Warehouse(
                        organization_id=pw.org_id,
                        code=f"SITE-{i}",
                        name=f"Site {i}",
                        location="",
                        lat_micro=LAT + i,
                        lng_micro=LNG + i,
                    )
                )
        assert await statements() == few
