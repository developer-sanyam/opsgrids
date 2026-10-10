"""Opening an inspection over a lot or roll (inventory Phase 4 on top of Phase 1 tracking).

The lot and roll an inspection names must exist in the caller's organization and belong to the
item — the same checks a posted move makes — and a bogus id is a 404 / 422 *before* an inspection
row exists, with or without a hold.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models import Inspection
from tests.quality_fixtures import call
from tests.tracking_fixtures import World, bucket_qty, build_world, make_foreign_world

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


def _body(w: World, **over) -> dict:  # type: ignore[no-untyped-def]
    body = {
        "kind": "incoming",
        "item_id": str(w.t.fabric),
        "location_id": str(w.t.weaving),
        "quantity": "40",
        "lot_id": str(w.lot),
        "roll_id": str(w.roll),
    }
    body.update(over)
    return body


async def _open(client, w: World, **over):  # type: ignore[no-untyped-def]
    return await call(client, w.org_id, "POST", "/inspections", json=_body(w, **over))


async def _count(session, org_id) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(func.count())
                .select_from(Inspection)
                .where(Inspection.organization_id == org_id)
            )
        ).scalar_one()


async def test_a_real_lot_and_roll_hold_that_roll_only(client, session, world: World) -> None:
    response = await _open(client, world)
    assert response.status_code == 201, response.text
    held = await bucket_qty(session, world, lot=world.lot, roll=world.roll, status="QUALITY_HOLD")
    assert held == (40_000, 0)


@pytest.mark.parametrize("hold_stock", [True, False])
async def test_an_unknown_lot_is_404_and_leaves_no_inspection(
    client, session, world: World, hold_stock: bool
) -> None:
    response = await _open(
        client, world, lot_id=str(uuid.uuid4()), roll_id=None, hold_stock=hold_stock
    )
    assert response.status_code == 404, response.text
    assert await _count(session, world.org_id) == 0


@pytest.mark.parametrize("hold_stock", [True, False])
async def test_an_unknown_roll_is_404_and_leaves_no_inspection(
    client, session, world: World, hold_stock: bool
) -> None:
    response = await _open(
        client, world, lot_id=None, roll_id=str(uuid.uuid4()), hold_stock=hold_stock
    )
    assert response.status_code == 404, response.text
    assert await _count(session, world.org_id) == 0


async def test_another_organizations_lot_and_roll_are_404(
    client, session, admin_session, world: World
) -> None:
    foreign = await make_foreign_world(admin_session)
    for over in (
        {"lot_id": str(foreign.lot), "roll_id": None},
        {"lot_id": None, "roll_id": str(foreign.roll)},
    ):
        response = await _open(client, world, hold_stock=False, **over)
        assert response.status_code == 404, response.text
    assert await _count(session, world.org_id) == 0


async def test_a_lot_of_a_different_item_is_422(client, session, world: World) -> None:
    response = await _open(
        client, world, item_id=str(world.t.dye), quantity="1", roll_id=None, hold_stock=False
    )
    assert response.status_code == 422, response.text
    assert await _count(session, world.org_id) == 0


async def test_a_roll_of_a_lot_is_not_inspectable_as_lotless_stock(
    client, session, world: World
) -> None:
    response = await _open(client, world, lot_id=None, hold_stock=False)
    assert response.status_code == 422, response.text
    assert await _count(session, world.org_id) == 0
