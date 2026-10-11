"""The adjustment-approval threshold also binds the other writers of stock: opening stock (single
and bulk) and quality scrap. Below the threshold, or with none set, nothing changes."""

from __future__ import annotations

import pytest

from tests import test_bulk_import_api as bulk
from tests import test_stock_ops_api as ops
from tests.movements_fixtures import CONTROL_CONFIGURE, call, mover
from tests.quality_fixtures import QcShop, build_qc_shop, open_ok
from tests.quality_fixtures import call as qc_call
from tests.tracking_fixtures import World, build_world

pytestmark = pytest.mark.integration

ADJUST = "operations.inventory.stock.adjust:ORG"


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
