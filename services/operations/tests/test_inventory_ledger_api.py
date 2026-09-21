"""The item-level HTTP surface on top of the ledger (ADR-0014 §10): the compatibility fields keep
their shape, the exact fields appear beside them, retries with an `Idempotency-Key` post once,
and the per-bucket balances are readable only inside the caller's organization."""

from __future__ import annotations

import uuid

import pytest

from app.inventory import ledger
from app.inventory.ledger import MoveLine
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import TODAY, build_textile, ledger_rows, q

pytestmark = pytest.mark.integration

READ = "operations.inventory.item.read:ORG"
ADJUST = "operations.inventory.stock.adjust:ORG"


class TestCompatibleShape:
    async def test_an_item_carries_both_the_old_integer_and_the_exact_quantities(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [READ])):
            body = (
                await client.get(f"/api/v1/operations/inventory/{data['low_stock_item'].id}")
            ).json()
        assert body["on_hand"] == 6
        assert (body["on_hand_qty"], body["reserved_qty"], body["available_qty"]) == (
            "6",
            "0",
            "6",
        )

    async def test_list_filters_and_sorts_still_work_on_the_derived_stock(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [READ])):
            low = (await client.get("/api/v1/operations/inventory?low_stock=true")).json()
            by_on_hand = (await client.get("/api/v1/operations/inventory?sort=on_hand")).json()
        assert [i["sku"] for i in low["items"]] == ["RM-ALU-7075"]
        assert [i["sku"] for i in by_on_hand["items"]] == ["RM-ALU-7075", "CMP-REL-014"]

    async def test_movement_history_carries_exact_quantity_and_bucket(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [READ])):
            body = (
                await client.get(
                    f"/api/v1/operations/inventory/{data['low_stock_item'].id}/movements"
                )
            ).json()
        [opening] = body["items"]
        assert (opening["kind"], opening["quantity_delta"], opening["on_hand_after"]) == (
            "opening",
            6,
            6,
        )
        assert (opening["quantity"], opening["balance_after"], opening["status"]) == (
            "6",
            "6",
            "AVAILABLE",
        )


class TestIdempotentAdjustments:
    async def test_a_retried_adjustment_posts_once(self, client, session, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        url = f"/api/v1/operations/inventory/{item_id}/adjustments"
        payload = {"quantity_delta": 4, "reason": "found"}
        headers = {"Idempotency-Key": "adj-2026-09-22-001"}
        with as_principal(principal_for(org_id, [READ, ADJUST])):
            first = await client.post(url, json=payload, headers=headers)
            retry = await client.post(url, json=payload, headers=headers)
        assert (first.status_code, retry.status_code) == (201, 201)
        assert first.json()["on_hand"] == retry.json()["on_hand"] == 10
        assert await ledger_rows(session, org_id, item_id) == 2  # opening + one adjustment

    async def test_the_same_key_with_a_different_body_is_a_conflict(
        self, client, org_id, data
    ) -> None:
        url = f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments"
        headers = {"Idempotency-Key": "adj-dup"}
        with as_principal(principal_for(org_id, [READ, ADJUST])):
            await client.post(url, json={"quantity_delta": 1, "reason": "found"}, headers=headers)
            clash = await client.post(
                url, json={"quantity_delta": 2, "reason": "found"}, headers=headers
            )
        assert clash.status_code == 409

    async def test_a_refused_adjustment_explains_itself(self, client, org_id, data) -> None:
        url = f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments"
        with as_principal(principal_for(org_id, [READ, ADJUST])):
            refused = await client.post(url, json={"quantity_delta": -20, "reason": "damaged"})
        assert refused.status_code == 409
        problem = refused.json()
        assert problem["type"].endswith("/insufficient-stock")
        assert problem["errors"][0]["available"] == "6"


class TestBalances:
    async def test_balances_break_stock_down_by_bucket(self, client, session, org_id) -> None:
        t = await build_textile(session, org_id)
        async with session.begin():
            await ledger.post_move(
                session,
                t.ctx,
                kind="receipt",
                lines=[
                    MoveLine(t.fabric, t.weaving, q("82.5")),
                    MoveLine(t.fabric, t.finishing, q("10"), status="QUALITY_HOLD"),
                ],
                occurred_on=TODAY,
            )
            await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("12.5")
            )
        with as_principal(principal_for(org_id, [READ])):
            rows = (await client.get(f"/api/v1/operations/inventory/{t.fabric}/balances")).json()
            item = (await client.get(f"/api/v1/operations/inventory/{t.fabric}")).json()
        assert [
            (
                r["warehouse_code"],
                r["status"],
                r["on_hand_qty"],
                r["reserved_qty"],
                r["available_qty"],
            )
            for r in rows
        ] == [
            ("WH-FIN", "QUALITY_HOLD", "10", "0", "10"),
            ("WH-WEAVE", "AVAILABLE", "82.5", "12.5", "70"),
        ]
        assert (item["on_hand_qty"], item["reserved_qty"], item["available_qty"]) == (
            "92.5",
            "12.5",
            "70",
        )

    async def test_another_organizations_item_balances_are_not_found(
        self, client, admin_session, org_id
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        with as_principal(principal_for(org_id, [READ])):
            probe = await client.get(f"/api/v1/operations/inventory/{other.fabric}/balances")
        assert probe.status_code == 404

    async def test_balances_need_item_read(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, ["operations.inventory.warehouse.read:ORG"])):
            denied = await client.get(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/balances"
            )
        assert denied.status_code == 403
