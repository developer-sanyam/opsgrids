"""The adjustment-approval threshold also binds the other writers of stock: opening stock (single
and bulk) and quality scrap. Below the threshold, or with none set, nothing changes."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import InventoryMove
from tests import test_bulk_import_api as bulk
from tests import test_stock_ops_api as ops
from tests.movements_fixtures import CONTROL_CONFIGURE, call, mover
from tests.quality_fixtures import QcShop, build_qc_shop, open_ok
from tests.quality_fixtures import call as qc_call
from tests.tracking_fixtures import World, build_world

pytestmark = pytest.mark.integration

ADJUST = "operations.inventory.stock.adjust:ORG"
STOCK_TRANSFER = "operations.inventory.stock.transfer:ORG"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


@pytest.fixture
async def qc(session, org_id) -> QcShop:
    return await build_qc_shop(session, org_id, stock=100)


async def configure(client, org_id, threshold: str | None) -> None:  # type: ignore[no-untyped-def]
    resp = await call(
        client,
        mover(org_id, CONTROL_CONFIGURE),
        "PUT",
        "/operations/inventory/control-settings",
        {"adjustment_approval_threshold": threshold},
    )
    assert resp.status_code == 200, resp.text


class TestOpeningStock:
    async def test_a_line_at_or_over_the_threshold_is_refused_and_points_to_adjustments(
        self, client, org_id, world
    ) -> None:
        await configure(client, org_id, "20")
        over = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world, quantity="25")
        )
        assert over.status_code == 409, over.text
        assert "/operations/inventory/adjustments" in over.text
        under = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world, quantity="10")
        )
        assert under.status_code == 201, under.text

    async def test_opening_into_a_bucket_that_already_has_history_is_refused_but_replays(
        self, client, org_id, world
    ) -> None:
        await configure(client, org_id, "1000")
        first = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world), key="open-1"
        )
        assert first.status_code == 201, first.text
        replay = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world), key="open-1"
        )
        assert replay.status_code == 200, replay.text
        again = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world), key="open-2"
        )
        assert again.status_code == 409, again.text

    async def test_the_bulk_import_cannot_open_stock_over_the_threshold_either(
        self, client, org_id, world
    ) -> None:
        await configure(client, org_id, "20")
        rows = [bulk.stock_row("DYE-RED-01", qty="25")]
        resp = await bulk.run(client, org_id, "opening_stock", rows, mode="commit", key="b-1")
        assert resp.status_code in (200, 409, 422), resp.text
        assert "needs approval" in resp.text, resp.text
        assert resp.json().get("created", 0) == 0, resp.text

    async def test_a_replay_of_an_accepted_opening_survives_the_threshold_being_lowered(
        self, client, org_id, world
    ) -> None:
        body = ops.opening_body(world, quantity="25")
        first = await ops.post(client, org_id, ADJUST, ops.OPENING, body, key="open-late")
        assert first.status_code == 201, first.text
        await configure(client, org_id, "20")
        replay = await ops.post(client, org_id, ADJUST, ops.OPENING, body, key="open-late")
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True

    async def test_the_bulk_import_refuses_a_bucket_that_already_has_history(
        self, client, org_id, world
    ) -> None:
        await configure(client, org_id, "1000")
        # A bucket emptied again still has ledger history; the single-record route refuses it.
        first = await ops.post(client, org_id, ADJUST, ops.OPENING, ops.opening_body(world))
        assert first.status_code == 201, first.text
        moved = await ops.post(
            client,
            org_id,
            STOCK_TRANSFER,
            ops.TRANSFERS,
            {
                "inventory_item_id": str(world.t.dye),
                "from_location_id": str(world.t.weaving),
                "to_location_id": str(world.bin_b),
                "quantity": "12.5",
            },
        )
        assert moved.status_code == 201, moved.text
        rows = [bulk.stock_row("DYE-RED-01", location="DEFAULT", qty="5")]
        resp = await bulk.run(client, org_id, "opening_stock", rows, mode="commit", key="b-2")
        assert "already has stock history" in resp.text, resp.text
        assert resp.json().get("created", 0) == 0, resp.text

    async def test_a_client_key_cannot_squat_an_internal_ledger_key(
        self, client, session, org_id, world
    ) -> None:
        squat = "transfer:00000000-0000-0000-0000-000000000001:dispatch"
        first = await ops.post(
            client, org_id, ADJUST, ops.OPENING, ops.opening_body(world), key=squat
        )
        assert first.status_code == 201, first.text
        async with session.begin():
            keys = (
                (
                    await session.execute(
                        select(InventoryMove.idempotency_key).where(
                            InventoryMove.organization_id == org_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert squat not in keys
        stored = [k for k in keys if k]  # the seed's own moves carry no key
        assert stored and all(k.startswith("client:") for k in stored)


class TestQualityScrap:
    async def _rejected(self, client, qc: QcShop, org_id) -> str:  # type: ignore[no-untyped-def]
        await configure(client, org_id, "5")
        insp = await open_ok(client, qc, quantity="10")
        done = await qc_call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/reject", json={"reason": "r"}
        )
        assert done.status_code == 200, done.text
        return str(insp["id"])

    async def test_scrapping_over_the_threshold_needs_the_adjust_permission(
        self, client, org_id, qc: QcShop
    ) -> None:
        from tests.quality_fixtures import ALL_PERMS

        iid = await self._rejected(client, qc, org_id)
        path = f"/inspections/{iid}/disposition"
        refused = await qc_call(
            client, org_id, "POST", path, json={"action": "SCRAP"}, perms=ALL_PERMS
        )
        assert refused.status_code == 403, refused.text
        allowed = await qc_call(
            client, org_id, "POST", path, json={"action": "SCRAP"}, perms=[*ALL_PERMS, ADJUST]
        )
        assert allowed.status_code == 200, allowed.text
