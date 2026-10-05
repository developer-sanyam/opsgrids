"""Find-stock (`GET /operations/inventory/locate`) and the floor's published layout
(`GET /operations/floors/{floor_id}/published-layout`).

The two answer the storekeeper's question from the same place — a floor's **published** layout,
never a draft, never the `Location.floor_id` shortcut that a publish sets and nothing clears — so
the cases that matter are shared: a draft or an archived version is invisible, a bin that is not
placed itself borrows its nearest placed container (marked as such), a soft-deleted thing is
skipped, and a caller without `layout.read` learns where stock *is* but not where on the floor.

Authorization and cross-tenant behaviour of both routes is `test_putaway_authz.py`'s.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select, update

from app.catalogue import OPERATIONS_ROLE_TEMPLATES
from app.inventory import putaway_selectors, stock_ops
from app.inventory import tracking_services as tracking
from app.inventory.tracking_schemas import RollIn
from app.models import (
    InventoryBalance,
    LayoutVersion,
    Location,
    LocationPlacement,
    Warehouse,
    WarehouseFloor,
)
from tests.helpers import principal_for
from tests.putaway_fixtures import (
    LAYOUT_READ,
    STOCK_READ,
    PutawayWorld,
    build_putaway_world,
    call,
    line,
    make_location,
    opening,
    place,
)
from tests.tracking_fixtures import make_foreign_world

pytestmark = pytest.mark.integration

LOCATE = "/operations/inventory/locate"
BOTH = [STOCK_READ, LAYOUT_READ]


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def stock_dye(session, pw: PutawayWorld, *locations: uuid.UUID, quantity: str = "3") -> None:
    async with session.begin():
        await stock_ops.post_opening_stock(
            session,
            pw.w.t.ctx,
            opening([line(pw.dye, loc, quantity) for loc in locations]),
            idempotency_key=None,
            occurred_on_explicit=False,
        )


async def locate(client, pw: PutawayWorld, perms: list[str] | None = None, **params: Any):  # type: ignore[no-untyped-def]
    params = {"inventory_item_id": str(pw.dye), **params}
    return await call(
        client, pw.org_id, perms if perms is not None else BOTH, "GET", LOCATE, params=params
    )


def by_code(resp) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    assert resp.status_code == 200, resp.text
    return {r["code"]: r for r in resp.json()["results"]}


# ------------------------------------------------------------------ where on the floor


class TestPlacement:
    async def test_a_bin_placed_on_the_published_layout_is_found_there(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            floor_id, version_id = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor_name="Ground",
                placements={pw.rack_bin: (1000, 2000, 800, 600)},
            )
            await session.execute(
                update(LocationPlacement)
                .where(LocationPlacement.location_id == pw.rack_bin)
                .values(z_mm=1500, height_mm=2200)
            )
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"] == {
            "layout_version_id": str(version_id),
            "x_mm": 1000,
            "y_mm": 2000,
            "z_mm": 1500,
            "width_mm": 800,
            "depth_mm": 600,
            "height_mm": 2200,
            "source": "own",
            "ancestor_location_id": None,
        }
        assert (row["floor_id"], row["floor_name"]) == (str(floor_id), "Ground")
        assert row["placement_reason"] is None
        assert (row["warehouse_id"], row["warehouse_name"]) == (str(pw.warehouse), "Weaving Store")
        assert (row["kind"], row["name"], row["status"], row["quantity"]) == (
            "bin",
            "Rack 1 bin 1",
            "AVAILABLE",
            "3",
        )

    async def test_an_unplaced_bin_borrows_its_containers_place_and_says_so(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"]["source"] == "ancestor"
        assert row["placement"]["ancestor_location_id"] == str(pw.rack)
        assert (row["placement"]["x_mm"], row["placement"]["width_mm"]) == (0, 3000)
        assert row["floor_id"] is not None and row["placement_reason"] is None
        assert row["path"] == "RACK-1 / R1-B01"

    async def test_a_bin_placed_itself_beats_a_placed_container(self, client, session, pw) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200), pw.rack_bin: (100, 100, 400, 400)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        placement = by_code(await locate(client, pw))["R1-B01"]["placement"]
        assert placement["source"] == "own" and placement["x_mm"] == 100
        assert placement["ancestor_location_id"] is None

    async def test_the_nearest_placed_container_wins(self, client, session, pw) -> None:
        zone = await make_location(session, pw, kind="zone", code="ZN-9")
        rack = await make_location(session, pw, kind="rack", code="RK-9", parent_id=zone)
        bin_ = await make_location(session, pw, kind="bin", code="BN-9", parent_id=rack)
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={zone: (0, 0, 9000, 9000), rack: (500, 500, 2000, 800)},
            )
        await stock_dye(session, pw, bin_)
        row = by_code(await locate(client, pw))["BN-9"]
        assert row["placement"]["ancestor_location_id"] == str(rack)
        assert row["placement"]["x_mm"] == 500
        assert row["path"] == "ZN-9 / RK-9 / BN-9"

    async def test_a_bin_with_no_placed_container_is_unplaced_when_the_warehouse_has_a_plan(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
        await stock_dye(session, pw, pw.w.bin_b)  # BIN-B hangs off no placed container
        row = by_code(await locate(client, pw))["BIN-B"]
        assert row["placement"] is None and row["placement_reason"] == "unplaced"
        assert row["floor_id"] is None and row["floor_name"] is None

    async def test_a_warehouse_with_no_published_layout_says_so(self, client, session, pw) -> None:
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"] is None and row["placement_reason"] == "no_published_layout"

    async def test_a_draft_layout_never_surfaces(self, client, session, pw) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                status="draft",
                placements={pw.rack_bin: (1, 2, 3, 4), pw.rack: (5, 6, 7, 8)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"] is None
        assert row["placement_reason"] == "no_published_layout"  # a draft is no plan at all

    async def test_a_draft_beside_a_published_version_is_ignored(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, published = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack_bin: (10, 20, 30, 40)},
            )
            floor = await session.get(WarehouseFloor, floor_id)
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor=floor,
                version_number=2,
                status="draft",
                placements={pw.rack_bin: (999, 999, 999, 999)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        placement = by_code(await locate(client, pw))["R1-B01"]["placement"]
        assert (placement["x_mm"], placement["layout_version_id"]) == (10, str(published))

    async def test_an_archived_version_is_not_the_published_one(self, client, session, pw) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                status="archived",
                placements={pw.rack_bin: (1, 2, 3, 4)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"] is None and row["placement_reason"] == "no_published_layout"

    async def test_a_bin_dropped_from_the_published_layout_is_not_where_the_floor_id_says(
        self, client, session, pw
    ) -> None:
        """`Location.floor_id` is set on a publish and never cleared: a bin the *current* layout no
        longer places must not still be reported on that floor."""
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
            # What a past publish left behind on the bin itself.
            await session.execute(
                update(Location).where(Location.id == pw.w.bin_b).values(floor_id=floor_id)
            )
        await stock_dye(session, pw, pw.w.bin_b)
        row = by_code(await locate(client, pw))["BIN-B"]
        assert row["placement"] is None and row["floor_id"] is None

    async def test_a_soft_deleted_floor_does_not_count(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack_bin: (1, 2, 3, 4)},
            )
            await session.execute(
                update(WarehouseFloor)
                .where(WarehouseFloor.id == floor_id)
                .values(deleted_at=datetime.now(UTC))
            )
        await stock_dye(session, pw, pw.rack_bin)
        assert by_code(await locate(client, pw))["R1-B01"]["placement"] is None

    async def test_a_soft_deleted_container_ends_the_chain(self, client, session, pw) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
            await session.execute(
                update(Location).where(Location.id == pw.rack).values(deleted_at=datetime.now(UTC))
            )
        await stock_dye(session, pw, pw.rack_bin)
        row = by_code(await locate(client, pw))["R1-B01"]
        assert row["placement"] is None
        assert row["path"] == "R1-B01"  # the deleted rack is not part of where it is

    async def test_a_location_on_two_floors_published_layouts_is_one_row_the_latest_wins(
        self, client, session, pw
    ) -> None:
        now = datetime.now(UTC)
        async with session.begin():
            _, older = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor_number=1,
                floor_name="Old floor",
                published_at=now - timedelta(days=3),
                placements={pw.rack_bin: (1, 1, 1, 1)},
            )
            newer_floor, newer = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor_number=2,
                floor_name="New floor",
                published_at=now - timedelta(days=1),
                placements={pw.rack_bin: (2, 2, 2, 2)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        resp = await locate(client, pw)
        rows = [r for r in resp.json()["results"] if r["code"] == "R1-B01"]
        assert len(rows) == 1  # not one row per placement
        row = rows[0]
        assert row["placement"]["layout_version_id"] == str(newer) != str(older)
        assert row["floor_id"] == str(newer_floor) and row["floor_name"] == "New floor"

    async def test_geometry_is_integer_millimetres_not_floats(self, client, session, pw) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack_bin: (1234, 5678, 910, 1112)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        placement = by_code(await locate(client, pw))["R1-B01"]["placement"]
        for key in ("x_mm", "y_mm", "width_mm", "depth_mm"):
            assert type(placement[key]) is int


# ------------------------------------------------------------------ without layout.read


class TestWithoutLayoutRead:
    async def test_stock_without_layout_read_gets_where_but_not_where_on_the_floor(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor_name="Ground",
                placements={pw.rack_bin: (31415, 27182, 800, 600)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        resp = await locate(client, pw, [STOCK_READ])
        row = by_code(resp)["R1-B01"]
        assert row["placement"] is None and row["placement_reason"] == "no_permission"
        assert row["floor_id"] is None and row["floor_name"] is None
        # Still told where the stock is.
        assert (row["code"], row["path"], row["quantity"]) == ("R1-B01", "RACK-1 / R1-B01", "3")
        # …and not a trace of the geometry anywhere in the response.
        for number in ("31415", "27182", "Ground"):
            assert number not in resp.text

    @pytest.mark.parametrize(
        ("role", "sees_geometry"),
        [
            ("OPERATIONS_ADMIN", True),
            ("OPERATIONS_MANAGER", True),
            ("OPERATIONS_WORKER", False),  # reads stock, holds no layout.read
        ],
    )
    async def test_the_real_roles(self, client, session, pw, role, sees_geometry) -> None:
        async with session.begin():
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack_bin: (1, 2, 3, 4)},
            )
        await stock_dye(session, pw, pw.rack_bin)
        template = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == role)
        perms = [f"{g.code}:{g.scope}" for g in template.grants]
        who = principal_for(pw.org_id, perms, subject_ref=uuid.uuid4())
        resp = await call(
            client,
            pw.org_id,
            [],
            "GET",
            LOCATE,
            params={"inventory_item_id": str(pw.dye)},
            principal=who,
        )
        row = by_code(resp)["R1-B01"]
        if sees_geometry:
            assert row["placement"]["x_mm"] == 1 and row["placement_reason"] is None
        else:
            assert row["placement"] is None and row["placement_reason"] == "no_permission"
            assert row["floor_id"] is None


# ------------------------------------------------------------------ which stock, which bins


class TestWhichBins:
    async def test_one_row_per_bin_and_status_in_a_stable_order(self, client, session, pw) -> None:
        async with session.begin():
            await stock_ops.post_opening_stock(
                session,
                pw.w.t.ctx,
                opening(
                    [
                        line(pw.dye, pw.rack_bin, "4"),
                        line(pw.dye, pw.rack_bin, "2", status="QUALITY_HOLD"),
                        line(pw.dye, pw.shelf, "1"),
                    ]
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        resp = await locate(client, pw)
        rows = [(r["code"], r["status"], r["quantity"]) for r in resp.json()["results"]]
        # Warehouse name, bin code, status — the receiving and staging bins hold dye already.
        assert rows == [
            ("R1-B01", "AVAILABLE", "4"),
            ("R1-B01", "QUALITY_HOLD", "2"),
            ("RCV-1", "AVAILABLE", "20"),
            ("SHELF-1", "AVAILABLE", "1"),
            ("STG-1", "AVAILABLE", "10"),
        ]
        assert resp.json()["truncated"] is False

    async def test_the_lots_and_rolls_in_one_bin_are_summed(self, client, session, pw) -> None:
        w = pw.w
        async with session.begin():
            second = await tracking.create_roll(
                session,
                w.t.ctx,
                RollIn(
                    inventory_item_id=w.t.fabric,
                    lot_id=w.lot,
                    roll_no="R-2",
                    primary_qty="59",
                    width_mm=1500,
                    gsm=180,
                ),
            )
            await stock_ops.post_opening_stock(
                session,
                w.t.ctx,
                opening([line(w.t.fabric, pw.receiving, "59", lot_id=w.lot, roll_id=second.id)]),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
            second_id = second.id
        total = await locate(client, pw, inventory_item_id=str(pw.fabric))
        assert [(r["code"], r["quantity"]) for r in total.json()["results"]] == [("RCV-1", "141.5")]
        one_roll = await locate(client, pw, inventory_item_id=str(pw.fabric), roll_id=str(w.roll))
        assert [r["quantity"] for r in one_roll.json()["results"]] == ["82.5"]
        other_roll = await locate(
            client, pw, inventory_item_id=str(pw.fabric), roll_id=str(second_id)
        )
        assert [r["quantity"] for r in other_roll.json()["results"]] == ["59"]
        by_lot = await locate(client, pw, inventory_item_id=str(pw.fabric), lot_id=str(w.lot))
        assert [r["quantity"] for r in by_lot.json()["results"]] == ["141.5"]

    async def test_a_warehouse_filter_narrows_to_that_warehouse(self, client, session, pw) -> None:
        await stock_dye(session, pw, pw.other_warehouse_bin)
        everything = await locate(client, pw)
        assert {r["warehouse_name"] for r in everything.json()["results"]} == {
            "Weaving Store",
            "Finishing",
        }
        only = await locate(client, pw, warehouse_id=str(pw.warehouse))
        assert {r["warehouse_name"] for r in only.json()["results"]} == {"Weaving Store"}
        finishing = await _finishing(session, pw)
        other = await locate(client, pw, warehouse_id=str(finishing))
        assert [r["code"] for r in other.json()["results"]] == ["FIN-B01"]

    async def test_stock_that_is_not_in_a_bin_on_the_floor_is_left_out(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            for status in ("IN_TRANSIT", "AT_JOB_WORKER"):
                session.add(
                    InventoryBalance(
                        organization_id=pw.org_id,
                        inventory_item_id=pw.dye,
                        location_id=pw.rack_bin,
                        status=status,
                        quantity_milli=7_000,
                        reserved_milli=0,
                    )
                )
        resp = await locate(client, pw)
        assert "R1-B01" not in by_code(resp)

    async def test_an_emptied_bin_is_no_longer_found(self, client, session, pw) -> None:
        # Take all 20 kg out of receiving: its bucket row stays, at zero.
        await call(
            client,
            pw.org_id,
            ["operations.inventory.stock.transfer:ORG"],
            "POST",
            "/operations/inventory/putaway",
            json={
                "inventory_item_id": str(pw.dye),
                "from_location_id": str(pw.receiving),
                "to_barcode": "RACK-1-B01",
                "quantity": "20",
            },
            key="empty-1",
        )
        rows = by_code(await locate(client, pw))
        assert "RCV-1" not in rows and rows["R1-B01"]["quantity"] == "20"

    async def test_a_soft_deleted_bin_is_not_found(self, client, session, pw) -> None:
        async with session.begin():
            await session.execute(
                update(Location)
                .where(Location.id == pw.staging)
                .values(deleted_at=datetime.now(UTC))
            )
        assert "STG-1" not in by_code(await locate(client, pw))

    async def test_an_item_with_no_stock_is_an_empty_answer_not_an_error(self, client, pw) -> None:
        resp = await locate(client, pw, inventory_item_id=str(pw.w.t.buttons))
        assert resp.status_code == 200
        assert resp.json() == {"results": [], "truncated": False}

    async def test_more_bins_than_the_cap_say_truncated(
        self, client, session, pw, monkeypatch
    ) -> None:
        await stock_dye(
            session, pw, pw.rack_bin, pw.rack_bin2, pw.shelf
        )  # + receiving, staging = 5
        monkeypatch.setattr(putaway_selectors, "LOCATE_MAX_ROWS", 4)
        capped = await locate(client, pw)
        assert (len(capped.json()["results"]), capped.json()["truncated"]) == (4, True)
        monkeypatch.setattr(putaway_selectors, "LOCATE_MAX_ROWS", 5)
        exact = await locate(client, pw)
        assert (len(exact.json()["results"]), exact.json()["truncated"]) == (5, False)

    async def test_the_number_of_queries_does_not_grow_with_the_number_of_bins(
        self, client, session, pw
    ) -> None:
        async def statements_for() -> int:
            seen: list[str] = []
            sync_engine = session.bind.sync_engine  # type: ignore[union-attr]

            def count_it(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
                seen.append(statement)

            event.listen(sync_engine, "before_cursor_execute", count_it)
            try:
                await locate(client, pw)
            finally:
                event.remove(sync_engine, "before_cursor_execute", count_it)
            return len(seen)

        await stock_dye(session, pw, pw.rack_bin)
        few = await statements_for()
        extra = [
            await make_location(session, pw, kind="bin", code=f"MANY-{i:02d}", parent_id=pw.rack)
            for i in range(25)
        ]
        await stock_dye(session, pw, *extra)
        many = await statements_for()
        assert many == few, f"{few} statements for 3 bins, {many} for 28"


async def _finishing(session, pw: PutawayWorld) -> uuid.UUID:
    async with session.begin():
        return (
            await session.execute(
                select(Warehouse.id).where(
                    Warehouse.organization_id == pw.org_id, Warehouse.code == "WH-FIN"
                )
            )
        ).scalar_one()


# ------------------------------------------------------------------ what may be asked


class TestWhatMayBeAsked:
    async def test_an_unknown_item_is_a_404(self, client, pw) -> None:
        assert (await locate(client, pw, inventory_item_id=str(uuid.uuid4()))).status_code == 404

    async def test_a_lot_or_roll_of_another_item_is_a_404(self, client, session, pw) -> None:
        # The fabric's lot and roll asked about the dye.
        for param, value in (("lot_id", pw.w.lot), ("roll_id", pw.w.roll)):
            resp = await locate(client, pw, **{param: str(value)})
            assert resp.status_code == 404, (param, resp.text)

    async def test_an_unknown_warehouse_is_a_404(self, client, pw) -> None:
        assert (await locate(client, pw, warehouse_id=str(uuid.uuid4()))).status_code == 404

    async def test_another_tenants_item_lot_roll_and_warehouse_are_404(
        self, client, admin_session, pw
    ) -> None:
        foreign = await make_foreign_world(admin_session)
        for params in (
            {"inventory_item_id": str(foreign.t.dye)},
            {"inventory_item_id": str(foreign.t.fabric), "lot_id": str(foreign.lot)},
            {"inventory_item_id": str(foreign.t.fabric), "roll_id": str(foreign.roll)},
            {"warehouse_id": str(foreign.warehouse_weave)},
        ):
            resp = await locate(client, pw, **params)
            assert resp.status_code == 404, (params, resp.text)

    async def test_another_tenants_stock_is_never_in_the_answer(
        self, client, session, admin_session, pw
    ) -> None:
        """The caller's own item id, asked by a tenant that holds the same stock elsewhere: only
        the caller's bins come back."""
        foreign = await make_foreign_world(admin_session)
        assert foreign.org_id != pw.org_id
        mine = by_code(await locate(client, pw))
        assert set(mine) == {"RCV-1", "STG-1"}
        theirs = await call(
            client,
            foreign.org_id,
            BOTH,
            "GET",
            LOCATE,
            params={"inventory_item_id": str(pw.dye)},
        )
        assert theirs.status_code == 404  # an item that is not theirs is not a hint it exists

    async def test_the_item_is_required(self, client, pw) -> None:
        resp = await call(client, pw.org_id, BOTH, "GET", LOCATE)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["field"] == "inventory_item_id"

    @pytest.mark.parametrize(
        "params",
        [
            {"inventory_item_id": "nope"},
            {"lot_id": "nope"},
            {"roll_id": "nope"},
            {"warehouse_id": "nope"},
        ],
    )
    async def test_a_malformed_id_is_a_422(self, client, pw, params) -> None:
        assert (await locate(client, pw, **params)).status_code == 422


# ------------------------------------------------------------------ the published layout of a floor


def layout_url(floor_id: Any) -> str:
    return f"/operations/floors/{floor_id}/published-layout"


async def read_layout(client, pw: PutawayWorld, floor_id: Any, perms: list[str] | None = None):  # type: ignore[no-untyped-def]
    return await call(client, pw.org_id, perms or [LAYOUT_READ], "GET", layout_url(floor_id))


class TestPublishedLayout:
    async def test_a_floor_with_no_layout_at_all_is_not_an_error(self, client, session, pw) -> None:
        async with session.begin():
            floor = WarehouseFloor(
                organization_id=pw.org_id, warehouse_id=pw.warehouse, floor_number=3, name="Mezz"
            )
            session.add(floor)
            await session.flush()
            floor_id = floor.id
        resp = await read_layout(client, pw, floor_id)
        assert resp.status_code == 200
        assert resp.json() == {
            "floor_id": str(floor_id),
            "warehouse_id": str(pw.warehouse),
            "floor_name": "Mezz",
            "published": False,
            "layout_version_id": None,
            "placements": [],
        }

    async def test_a_draft_only_floor_is_not_published(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                status="draft",
                placements={pw.rack: (1, 2, 3, 4)},
            )
        resp = await read_layout(client, pw, floor_id)
        assert resp.json()["published"] is False and resp.json()["placements"] == []
        assert resp.json()["layout_version_id"] is None

    async def test_the_published_version_lists_each_placement_with_its_location(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            floor_id, version_id = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor_name="Ground",
                placements={
                    pw.rack_bin2: (700, 0, 600, 600),
                    pw.rack: (0, 0, 3000, 1200),
                    pw.rack_bin: (100, 0, 600, 600),
                },
            )
            await session.execute(
                update(LocationPlacement)
                .where(LocationPlacement.location_id == pw.rack)
                .values(z_mm=0, height_mm=2400)
            )
        resp = await read_layout(client, pw, floor_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["published"], body["layout_version_id"], body["floor_name"]) == (
            True,
            str(version_id),
            "Ground",
        )
        # Ordered by location code: R1-B01, R1-B02, RACK-1.
        assert [p["code"] for p in body["placements"]] == ["R1-B01", "R1-B02", "RACK-1"]
        rack = next(p for p in body["placements"] if p["code"] == "RACK-1")
        assert rack == {
            "location_id": str(pw.rack),
            "code": "RACK-1",
            "name": "Rack 1",
            "kind": "rack",
            "parent_id": None,
            "x_mm": 0,
            "y_mm": 0,
            "z_mm": 0,
            "width_mm": 3000,
            "depth_mm": 1200,
            "height_mm": 2400,
        }
        first_bin = body["placements"][0]
        assert first_bin["parent_id"] == str(pw.rack) and first_bin["kind"] == "bin"
        assert first_bin["z_mm"] is None and first_bin["height_mm"] is None

    async def test_only_the_published_versions_placements_are_read(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
            floor = await session.get(WarehouseFloor, floor_id)
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor=floor,
                version_number=2,
                status="draft",
                placements={pw.rack_bin: (1, 1, 1, 1), pw.rack_bin2: (2, 2, 2, 2)},
            )
            await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor=floor,
                version_number=3,
                status="archived",
                placements={pw.shelf: (3, 3, 3, 3)},
            )
        resp = await read_layout(client, pw, floor_id)
        assert [p["code"] for p in resp.json()["placements"]] == ["RACK-1"]

    async def test_a_soft_deleted_location_is_skipped(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200), pw.shelf: (4000, 0, 500, 500)},
            )
            await session.execute(
                update(Location).where(Location.id == pw.shelf).values(deleted_at=datetime.now(UTC))
            )
        resp = await read_layout(client, pw, floor_id)
        assert [p["code"] for p in resp.json()["placements"]] == ["RACK-1"]

    async def test_a_republish_replaces_what_the_floor_shows(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, first = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (0, 0, 3000, 1200)},
            )
        assert (await read_layout(client, pw, floor_id)).json()["layout_version_id"] == str(first)
        async with session.begin():
            floor = await session.get(WarehouseFloor, floor_id)
            await session.execute(
                update(LayoutVersion).where(LayoutVersion.id == first).values(status="archived")
            )
            _, second = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                floor=floor,
                version_number=2,
                placements={pw.shelf: (0, 0, 500, 500)},
            )
        body = (await read_layout(client, pw, floor_id)).json()
        assert body["layout_version_id"] == str(second)
        assert [p["code"] for p in body["placements"]] == ["SHELF-1"]

    async def test_an_unknown_or_deleted_floor_is_a_404(self, client, session, pw) -> None:
        assert (await read_layout(client, pw, uuid.uuid4())).status_code == 404
        async with session.begin():
            floor_id, _ = await place(
                session, pw.org_id, warehouse_id=pw.warehouse, placements={pw.rack: (0, 0, 1, 1)}
            )
            await session.execute(
                update(WarehouseFloor)
                .where(WarehouseFloor.id == floor_id)
                .values(deleted_at=datetime.now(UTC))
            )
        assert (await read_layout(client, pw, floor_id)).status_code == 404

    async def test_a_malformed_floor_id_is_a_422(self, client, pw) -> None:
        resp = await call(client, pw.org_id, [LAYOUT_READ], "GET", layout_url("nope"))
        assert resp.status_code == 422

    async def test_geometry_is_integer_millimetres(self, client, session, pw) -> None:
        async with session.begin():
            floor_id, _ = await place(
                session,
                pw.org_id,
                warehouse_id=pw.warehouse,
                placements={pw.rack: (12, 34, 56, 78)},
            )
        for p in (await read_layout(client, pw, floor_id)).json()["placements"]:
            for key in ("x_mm", "y_mm", "width_mm", "depth_mm"):
                assert type(p[key]) is int
