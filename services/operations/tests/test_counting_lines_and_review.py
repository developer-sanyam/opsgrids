"""Count lines and discrepancy review over HTTP (ADR-0026, plan F8): counting a line, the
unlisted-line path, and review — approving posts through `app.inventory.ledger.post_move`
(ADR-0026 §3) computing its delta from the *live* balance, never the line's own snapshot; rejecting
never touches the ledger.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.ledger import MoveLine
from platform_core.auth import Scope
from tests.counting_fixtures import (
    COUNT_MANAGE_PERM,
    COUNT_READ_PERM,
    CountingShop,
    add_line,
    build_counting_shop,
    close_session,
    list_lines,
    open_session_ok,
    review_line,
    start_review,
    submit_count,
)
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def cshop(session, org_id) -> CountingShop:
    return await build_counting_shop(session, org_id)


async def _widget_line(client, org_id: uuid.UUID, session_id: str, widget_id: uuid.UUID) -> dict:
    resp = await list_lines(client, org_id, session_id)
    return next(
        line for line in resp.json()["items"] if line["inventory_item_id"] == str(widget_id)
    )


class TestSubmitCount:
    async def test_a_matching_count_needs_no_review(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        response = await submit_count(client, org_id, row["id"], line["id"], counted="100")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counted"] == "100"
        assert body["review_status"] == "NONE"

    async def test_a_mismatched_count_is_pending_review(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        response = await submit_count(client, org_id, row["id"], line["id"], counted="97")
        assert response.status_code == 200, response.text
        assert response.json()["review_status"] == "PENDING"

    async def test_a_line_cannot_be_counted_twice(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        first = await submit_count(client, org_id, row["id"], line["id"], counted="100")
        assert first.status_code == 200, first.text
        second = await submit_count(client, org_id, row["id"], line["id"], counted="90")
        assert second.status_code == 409

    async def test_counting_is_refused_once_the_session_left_active(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await start_review(client, org_id, row["id"])
        response = await submit_count(client, org_id, row["id"], line["id"], counted="100")
        assert response.status_code == 409

    async def test_a_caller_without_execute_cannot_submit(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        response = await submit_count(
            client, org_id, row["id"], line["id"], counted="100", perms=[COUNT_READ_PERM]
        )
        assert response.status_code == 403

    async def test_cross_tenant_cannot_submit(self, client, org_id, cshop: CountingShop) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        response = await submit_count(client, uuid.uuid4(), row["id"], line["id"], counted="100")
        assert response.status_code == 404


class TestAddUnlistedLine:
    async def test_stock_found_where_none_was_expected_creates_a_pending_line(
        self, client, session, org_id, cshop: CountingShop
    ) -> None:
        loc_ctx = ctx_for(org_id, "operations.inventory.location.write", Scope.ORG)
        async with session.begin():
            warehouse = await inv_selectors.get_warehouse(session, org_id, cshop.shop.warehouse_id)
            assert warehouse is not None
            spare = await inv_services.create_location(
                session, loc_ctx, warehouse, kind="bin", code="SPARE-1", name="Spare bin"
            )
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        response = await add_line(
            client,
            org_id,
            row["id"],
            inventory_item_id=cshop.shop.widget,
            location_id=spare.id,
            counted="4",
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["is_unlisted"] is True
        assert body["expected"] == "0"
        assert body["counted"] == "4"
        assert body["review_status"] == "PENDING"

    async def test_a_bucket_already_tracked_cannot_be_added_again(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        response = await add_line(
            client,
            org_id,
            row["id"],
            inventory_item_id=cshop.shop.widget,
            location_id=uuid.UUID(line["location_id"]),
            counted="4",
        )
        assert response.status_code == 409, response.text


class TestReview:
    async def test_approving_posts_the_counted_figure_even_after_the_balance_drifted(
        self, client, session, org_id, cshop: CountingShop
    ) -> None:
        """The kernel's own guarantee (ADR-0026 §3/§6): the delta is computed from the *live*
        balance under lock at approval time, not from the line's snapshot — proven by moving
        stock in between."""
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        submit = await submit_count(client, org_id, row["id"], line["id"], counted="90")
        assert submit.status_code == 200, submit.text
        assert submit.json()["review_status"] == "PENDING"

        # Drift: 10 more units arrive at the same home bucket after the count was taken.
        adjust_ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            home = await inv_selectors.get_location(session, org_id, uuid.UUID(line["location_id"]))
            assert home is not None
            await ledger.post_move(
                session,
                adjust_ctx,
                kind="receipt",
                lines=[
                    MoveLine(item_id=cshop.shop.widget, location_id=home.id, quantity_milli=10_000)
                ],
                occurred_on=date.today(),
                reference_type="test_drift",
                reference_id=uuid.uuid4(),
            )
        # Balance is now 110 (100 opening + 10 received); the count session still says 90.

        await start_review(client, org_id, row["id"])
        approved = await review_line(client, org_id, row["id"], line["id"], decision="approve")
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["review_status"] == "APPROVED"
        assert body["posted_move_id"]

        balances = await inv_selectors.balances_for_item(session, org_id, cshop.shop.widget)
        assert sum(b[0].quantity_milli for b in balances) == 90_000  # the counted figure, exactly

    async def test_rejecting_needs_a_note_and_never_posts(
        self, client, session, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="80")
        await start_review(client, org_id, row["id"])

        blank = await review_line(client, org_id, row["id"], line["id"], decision="reject", note="")
        assert blank.status_code == 422, blank.text

        rejected = await review_line(
            client,
            org_id,
            row["id"],
            line["id"],
            decision="reject",
            note="recount confirmed original",
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["review_status"] == "REJECTED"
        assert rejected.json()["posted_move_id"] is None

        balances = await inv_selectors.balances_for_item(session, org_id, cshop.shop.widget)
        assert sum(b[0].quantity_milli for b in balances) == 100_000  # untouched

    async def test_reviewing_before_review_status_is_refused(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="90")
        # Still ACTIVE — review requires REVIEW.
        response = await review_line(client, org_id, row["id"], line["id"], decision="approve")
        assert response.status_code == 409

    async def test_reviewing_a_matching_line_is_refused_not_pending(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="100")
        await start_review(client, org_id, row["id"])
        response = await review_line(client, org_id, row["id"], line["id"], decision="approve")
        assert response.status_code == 409

    async def test_close_is_refused_while_a_line_is_still_pending(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="90")
        await start_review(client, org_id, row["id"])
        response = await close_session(client, org_id, row["id"])
        assert response.status_code == 409

    async def test_close_succeeds_once_every_line_is_resolved(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        lines_resp = await list_lines(client, org_id, row["id"])
        for line in lines_resp.json()["items"]:
            await submit_count(client, org_id, row["id"], line["id"], counted=line["expected"])
        await start_review(client, org_id, row["id"])
        response = await close_session(client, org_id, row["id"])
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CLOSED"

    async def test_a_caller_without_review_cannot_approve(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="90")
        await start_review(client, org_id, row["id"])
        response = await review_line(
            client, org_id, row["id"], line["id"], decision="approve", perms=[COUNT_MANAGE_PERM]
        )
        assert response.status_code == 403

    async def test_cross_tenant_cannot_review(self, client, org_id, cshop: CountingShop) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        line = await _widget_line(client, org_id, row["id"], cshop.shop.widget)
        await submit_count(client, org_id, row["id"], line["id"], counted="90")
        await start_review(client, org_id, row["id"])
        response = await review_line(
            client, uuid.uuid4(), row["id"], line["id"], decision="approve"
        )
        assert response.status_code == 404
