"""`GET /operations/inventory/stock` carries the kind of each row's location (`location_kind`).

The worker app's put-away screen lists stock at receiving and staging only. It reads that off the
stock list, and a worker holds `stock.read` but not `location.read`, so the kind has to ride on the
row itself. The selector already joins `locations`; this checks the join's `kind` reaches the wire,
that it is the *row's own* location's, that a caller with nothing but `stock.read` sees it, and that
it costs no extra query.
"""

from __future__ import annotations

import uuid
from typing import Any, get_args

import pytest
from sqlalchemy import event

from app.inventory import stock_ops
from app.schemas import LocationKind
from tests.putaway_fixtures import (
    STOCK_READ,
    PutawayWorld,
    build_putaway_world,
    call,
    line,
    make_location,
    opening,
)

pytestmark = pytest.mark.integration

STOCK = "/operations/inventory/stock"


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def stock_at(session, pw: PutawayWorld, *where: uuid.UUID) -> None:
    async with session.begin():
        await stock_ops.post_opening_stock(
            session,
            pw.w.t.ctx,
            opening([line(pw.dye, loc, "3") for loc in where]),
            idempotency_key=None,
            occurred_on_explicit=False,
        )


async def read_stock(client, pw: PutawayWorld, **params: Any):  # type: ignore[no-untyped-def]
    """The stock list as a caller holding `stock.read` and nothing else — a worker's grant."""
    return await call(
        client, pw.org_id, [STOCK_READ], "GET", STOCK, params={"limit": 200, **params}
    )


class TestLocationKindOnTheStockRow:
    async def test_each_row_carries_the_kind_of_its_own_location(self, client, session, pw) -> None:
        await stock_at(session, pw, pw.rack_bin, pw.shelf)
        resp = await read_stock(client, pw)
        assert resp.status_code == 200, resp.text
        kinds = {r["location_code"]: r["location_kind"] for r in resp.json()["items"]}
        assert kinds == {
            "RCV-1": "receiving",
            "STG-1": "staging",
            "R1-B01": "bin",
            "SHELF-1": "shelf",
        }

    async def test_the_value_is_the_kind_the_location_row_actually_has(
        self, client, session, pw
    ) -> None:
        """Every kind in the vocabulary, one stocked location each, compared with what the
        location was created as — not with a table the test and the code could share a typo in."""
        made = {
            kind: await make_location(session, pw, kind=kind, code=f"K-{kind}".upper()[:40])
            for kind in get_args(LocationKind)
            if kind not in {"receiving", "staging", "bin", "shelf"}
        }
        await stock_at(session, pw, *made.values())
        by_location = {
            r["location_id"]: r["location_kind"]
            for r in (await read_stock(client, pw)).json()["items"]
        }
        for kind, location_id in made.items():
            assert by_location[str(location_id)] == kind

    async def test_a_caller_with_only_stock_read_sees_it_and_nothing_else_is_added(
        self, client, session, pw
    ) -> None:
        """The kind is not a new disclosure (the worker already reads the location's code), and the
        row gains exactly that one key."""
        resp = await read_stock(client, pw, location_id=str(pw.receiving))
        [row] = [r for r in resp.json()["items"] if r["sku"] == "DYE-RED-01"]
        assert row["location_kind"] == "receiving"
        assert set(row) == {
            "id",
            "inventory_item_id",
            "sku",
            "item_name",
            "uom",
            "warehouse_id",
            "warehouse_code",
            "location_id",
            "location_code",
            "location_kind",
            "status",
            "lot_id",
            "lot_no",
            "roll_id",
            "roll_no",
            "on_hand_qty",
            "reserved_qty",
            "available_qty",
        }

    async def test_a_row_of_a_filtered_list_still_carries_it(self, client, session, pw) -> None:
        await stock_at(session, pw, pw.rack_bin)
        resp = await read_stock(client, pw, warehouse_id=str(pw.warehouse), item_id=str(pw.dye))
        assert {r["location_kind"] for r in resp.json()["items"]} == {"receiving", "staging", "bin"}

    async def test_the_number_of_queries_does_not_grow_with_the_rows_or_their_kinds(
        self, client, session, pw
    ) -> None:
        async def statements() -> int:
            seen: list[str] = []
            sync_engine = session.bind.sync_engine  # type: ignore[union-attr]

            def count_it(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
                seen.append(statement)

            event.listen(sync_engine, "before_cursor_execute", count_it)
            try:
                resp = await read_stock(client, pw)
                assert resp.status_code == 200, resp.text
            finally:
                event.remove(sync_engine, "before_cursor_execute", count_it)
            return len(seen)

        few = await statements()
        extra = [
            await make_location(
                session, pw, kind=("bin", "shelf", "quarantine")[i % 3], code=f"MANY-{i:02d}"
            )
            for i in range(24)
        ]
        await stock_at(session, pw, *extra)
        many = await statements()
        assert many == few, f"{few} statements for 3 rows, {many} for 27"
