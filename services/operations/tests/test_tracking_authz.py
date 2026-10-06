"""Negative authorization and cross-tenant isolation for **every** route the lots / rolls / stock
operation / stock read surface adds (inventory Phase 1, I3-I6).

One table drives all of it, so a route added without a row here is visible in review:

* a caller with **no** permission is 403;
* a caller holding **every other** permission this surface defines — everything but the one the
  route needs — is 403, so a permission cannot be borrowed from a sibling route;
* a caller from **another organization**, holding exactly the right permission, is 404 on every
  route that names a row (or a row of another tenant in its body), and gets an **empty page** —
  never the first tenant's data — from every list.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models import LedgerEntry, Lot, Roll
from tests.helpers import as_principal, principal_for
from tests.tracking_fixtures import (
    ALL_PERMS,
    API,
    LOCATION_WRITE,
    LOT_WRITE,
    ROLL_WRITE,
    STOCK_ADJUST,
    STOCK_READ,
    STOCK_TRANSFER,
    World,
    build_world,
)

pytestmark = pytest.mark.integration

Spec = tuple[str, str, str, Callable[[World], Any], str]


def _routes() -> dict[str, Spec]:
    """name -> (method, path, permission, json body factory or None, `404` | `empty`)."""

    def none(_w: World) -> None:
        return None

    return {
        "create_lot": (
            "POST",
            "/operations/inventory/lots",
            LOT_WRITE,
            lambda w: {"inventory_item_id": str(w.t.fabric), "lot_no": "LOT-NEW"},
            "404",
        ),
        "list_lots": ("GET", "/operations/inventory/lots", STOCK_READ, none, "empty"),
        "read_lot": ("GET", "/operations/inventory/lots/{lot}", STOCK_READ, none, "404"),
        "update_lot": (
            "PATCH",
            "/operations/inventory/lots/{lot}",
            LOT_WRITE,
            lambda w: {"quality_status": "ON_HOLD"},
            "404",
        ),
        "link_lots": (
            "POST",
            "/operations/inventory/lots/{lot}/genealogy",
            LOT_WRITE,
            lambda w: {"child_lot_id": str(uuid.uuid4()), "relation": "BLEND"},
            "404",
        ),
        "list_genealogy": (
            "GET",
            "/operations/inventory/lots/{lot}/genealogy",
            STOCK_READ,
            none,
            "404",
        ),
        "trace_lot": ("GET", "/operations/inventory/lots/{lot}/trace", STOCK_READ, none, "404"),
        "create_roll": (
            "POST",
            "/operations/inventory/rolls",
            ROLL_WRITE,
            lambda w: {
                "inventory_item_id": str(w.t.fabric),
                "lot_id": str(w.lot),
                "primary_qty": "10",
            },
            "404",
        ),
        "list_rolls": ("GET", "/operations/inventory/rolls", STOCK_READ, none, "empty"),
        "read_roll": ("GET", "/operations/inventory/rolls/{roll}", STOCK_READ, none, "404"),
        "update_roll": (
            "PATCH",
            "/operations/inventory/rolls/{roll}",
            ROLL_WRITE,
            lambda w: {"grade": "B"},
            "404",
        ),
        "list_roll_events": (
            "GET",
            "/operations/inventory/rolls/{roll}/events",
            STOCK_READ,
            none,
            "404",
        ),
        "trace_roll": ("GET", "/operations/inventory/rolls/{roll}/trace", STOCK_READ, none, "404"),
        "split_roll": (
            "POST",
            "/operations/inventory/rolls/{roll}:split",
            ROLL_WRITE,
            lambda w: {
                "location_id": str(w.t.weaving),
                "children": [{"quantity": "50"}, {"quantity": "32.5"}],
            },
            "404",
        ),
        "merge_rolls": (
            "POST",
            "/operations/inventory/rolls:merge",
            ROLL_WRITE,
            lambda w: {
                "roll_ids": [str(w.roll), str(uuid.uuid4())],
                "location_id": str(w.t.weaving),
            },
            "404",
        ),
        "opening_stock": (
            "POST",
            "/operations/inventory/stock/opening",
            STOCK_ADJUST,
            lambda w: {
                "lines": [
                    {
                        "inventory_item_id": str(w.t.dye),
                        "location_id": str(w.t.weaving),
                        "quantity": "5",
                    }
                ]
            },
            "404",
        ),
        "transfer_stock": (
            "POST",
            "/operations/inventory/stock/transfers",
            STOCK_TRANSFER,
            lambda w: {
                "inventory_item_id": str(w.t.fabric),
                "from_location_id": str(w.t.weaving),
                "to_location_id": str(w.bin_b),
                "quantity": "10",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
            "404",
        ),
        "change_status": (
            "POST",
            "/operations/inventory/stock/status-changes",
            STOCK_TRANSFER,
            lambda w: {
                "inventory_item_id": str(w.t.fabric),
                "location_id": str(w.t.weaving),
                "from_status": "AVAILABLE",
                "to_status": "QUALITY_HOLD",
                "quantity": "10",
                "lot_id": str(w.lot),
                "roll_id": str(w.roll),
            },
            "404",
        ),
        "list_stock": ("GET", "/operations/inventory/stock", STOCK_READ, none, "empty"),
        "list_ledger": ("GET", "/operations/inventory/ledger", STOCK_READ, none, "empty"),
        "delete_location": (
            "DELETE",
            "/operations/locations/{bin_b}",
            LOCATION_WRITE,
            none,
            "404",
        ),
    }


ROUTES = _routes()


def _url(path: str, w: World) -> str:
    return API + path.format(lot=w.lot, roll=w.roll, bin_b=w.bin_b)


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def _call(client, principal, name: str, w: World):  # type: ignore[no-untyped-def]
    method, path, _perm, body, _expect = ROUTES[name]
    with as_principal(principal):
        return await client.request(method, _url(path, w), json=body(w))


@pytest.mark.parametrize("name", sorted(ROUTES))
class TestEveryRoute:
    async def test_no_permission_is_forbidden(self, client, world, name) -> None:
        response = await _call(client, principal_for(world.org_id, []), name, world)
        assert response.status_code == 403, response.text

    async def test_every_other_permission_is_still_forbidden(self, client, world, name) -> None:
        needed = ROUTES[name][2]
        others = [p for p in ALL_PERMS if p != needed]
        response = await _call(client, principal_for(world.org_id, others), name, world)
        assert response.status_code == 403, response.text

    async def test_another_tenant_gets_404_or_an_empty_page(self, client, world, name) -> None:
        needed, expect = ROUTES[name][2], ROUTES[name][4]
        intruder = principal_for(uuid.uuid4(), [needed])
        response = await _call(client, intruder, name, world)
        if expect == "empty":
            assert response.status_code == 200, response.text
            assert response.json()["items"] == []
        else:
            assert response.status_code == 404, response.text


WRITE_ROUTES = sorted(name for name, spec in ROUTES.items() if spec[0] != "GET")


@pytest.mark.parametrize("name", WRITE_ROUTES)
class TestRefusedWritesChangeNothing:
    async def test_a_cross_tenant_write_leaves_the_owners_data_alone(
        self, client, session, world, name
    ) -> None:
        needed = ROUTES[name][2]
        await _call(client, principal_for(uuid.uuid4(), [needed]), name, world)
        async with session.begin():
            counts = [
                (
                    await session.execute(
                        select(func.count()).select_from(m).where(m.organization_id == world.org_id)
                    )
                ).scalar_one()
                for m in (Lot, Roll, LedgerEntry)
            ]
        # The fixture's one lot, one roll and the roll's one opening ledger row — nothing more.
        assert counts == [1, 1, 1]
