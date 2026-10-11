"""A declared job-work shortage is a write-off of the vendor's stock, so it honours the adjustment
approval threshold the same way quality scrap does: over it, the receiver also needs the stock
adjust permission at ORG scope."""

from __future__ import annotations

import pytest

from app.movements import common
from platform_core.auth import Scope
from tests.helpers import ctx_for
from tests.inventory_fixtures import q
from tests.jobwork_fixtures import (
    ALL_JW_ORG,
    Mill,
    build_mill,
    call,
    issued_order,
    key,
    receipt_line,
    receive,
)

pytestmark = pytest.mark.integration

ADJUST = "operations.inventory.stock.adjust"


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


async def _threshold(session, org_id, threshold: str) -> None:  # type: ignore[no-untyped-def]
    ctx = ctx_for(org_id, "operations.inventory.control.configure", Scope.ORG)
    async with session.begin():
        await common.set_thresholds(
            session, ctx, transfer_milli=None, adjustment_milli=q(threshold)
        )


async def test_a_shortage_over_the_threshold_needs_the_adjust_permission(
    client, session, mill: Mill
) -> None:
    order = await issued_order(client, mill)
    material = order["materials"][0]["id"]
    await _threshold(session, mill.org_id, "2")

    refused = await receive(client, mill, order["id"], [receipt_line(material, shortage="2.5")])
    assert refused.status_code == 403, refused.text

    under = await receive(client, mill, order["id"], [receipt_line(material, shortage="1")])
    assert under.status_code == 200, under.text

    allowed = await call(
        client,
        mill.org_id,
        "POST",
        f"/orders/{order['id']}:receive",
        perms=[*ALL_JW_ORG, f"{ADJUST}:ORG"],
        json={"lines": [receipt_line(material, shortage="2.5")]},
        idem=key(),
    )
    assert allowed.status_code == 200, allowed.text
