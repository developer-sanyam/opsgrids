"""Production orders end to end: reservation -> issue -> WIP -> output -> finished goods, exactly.

Every figure below is worked by hand in the comments, because the point of these tests is that the
arithmetic is integer, rounded up once, and never drifts: 7 shirts at 1.234 m with a 2.5 % scrap
allowance need 8.854 m (1.234 x 7 = 8.638; x 1.025 = 8.85395, rounded up to the milli), and
splitting the output 3 + 4 consumes exactly what 7 in one go would.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select

from app.inventory.reconcile import reconcile_organization
from app.manufacturing.models import ProductionGenealogy, ProductionWastage
from app.models import AuditLog, OutboxEvent
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import balance, q
from tests.manufacturing_fixtures import (
    P_BOM_READ,
    P_ORD_APPROVE,
    P_ORD_ISSUE,
    P_ORD_READ,
    P_ORD_WRITE,
    Mfg,
    build_mfg,
    call,
    make_bom,
    make_lot,
    make_order,
    material,
    ok,
    stock,
)

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "events"


@pytest.fixture
async def m(session, org_id) -> Mfg:
    return await build_mfg(session, org_id)


@pytest.fixture
async def shirt_bom(client, session, m: Mfg) -> dict:
    """1 shirt = 1.234 m fabric (2.5 % scrap) + 6 buttons (no scrap); stock to cover 100 shirts."""
    await stock(session, m, m.t.fabric, "200")
    await stock(session, m, m.t.buttons, "1000")
    return await make_bom(
        client,
        m,
        m.shirt,
        "BOM-SHIRT",
        [(m.t.fabric, "1.234", "2.5"), (m.t.buttons, "6", "0")],
    )


async def events(session, org_id: uuid.UUID, event_type: str) -> list[dict]:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id, OutboxEvent.event_type == event_type
                    )
                )
            )
            .scalars()
            .all()
        )
    return [r.payload for r in rows]


def assert_matches_contract(event_type: str, payload: dict) -> None:
    schema = json.loads((SCHEMAS / event_type / "v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)


async def drift(session, org_id: uuid.UUID) -> int:
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    return len(report.findings)


class TestPlanning:
    async def test_materials_are_the_bom_scaled_with_scrap_rounded_up_once(
        self, client, m, shirt_bom
    ) -> None:
        order = await make_order(client, m, m.shirt, "7", approve=False)
        fabric = material(order, "COT-180-NAVY")
        # 1234 * 7000 * (10000 + 250) / (1000 * 10000) = 8853.95 -> 8854 (milli)
        assert fabric["required"] == "8.854"
        assert fabric["scrap_percent"] == "2.5"
        # Discrete unit: 6 * 7 = 42 exactly.
        assert material(order, "BTN-14MM")["required"] == "42"
        assert order["number"] == "MO-00001"
        assert order["status"] == "DRAFT"
        assert order["bom_version_no"] == 1

    async def test_the_order_pins_the_version_it_was_planned_from(
        self, client, m, shirt_bom
    ) -> None:
        order = await make_order(client, m, m.shirt, "10", approve=False)
        assert order["bom_version_id"] == shirt_bom["id"]
        # A newer version does not touch the pinned one.
        v2 = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{shirt_bom['bom_id']}/versions",
            expect=201,
            json={"copy_from_version_id": shirt_bom["id"]},
        )
        await ok(
            client,
            m.org_id,
            "PATCH",
            f"/boms/{shirt_bom['bom_id']}/versions/{v2['id']}/components/{v2['components'][0]['id']}",
            json={"quantity": "2"},
        )
        await ok(
            client, m.org_id, "POST", f"/boms/{shirt_bom['bom_id']}/versions/{v2['id']}/publish"
        )
        again = await ok(client, m.org_id, "GET", f"/orders/{order['id']}")
        assert again["bom_version_id"] == shirt_bom["id"]
        # 1.234 * 10 * 1.025 = 12.6485 -> 12.649, from v1's 1.234 m, not v2's 2 m
        assert material(again, "COT-180-NAVY")["required"] == "12.649"

    async def test_editing_a_draft_recomputes_requirements(self, client, m, shirt_bom) -> None:
        order = await make_order(client, m, m.shirt, "7", approve=False)
        edited = await ok(
            client, m.org_id, "PATCH", f"/orders/{order['id']}", json={"quantity": "100"}
        )
        # 1234 * 100000 * 10250 / 10^7 = 126485 milli
        assert material(edited, "COT-180-NAVY")["required"] == "126.485"
        assert material(edited, "BTN-14MM")["required"] == "600"

    async def test_a_started_order_cannot_be_edited(self, client, m, shirt_bom) -> None:
        order = await make_order(client, m, m.shirt, "7")
        response = await call(
            client, m.org_id, "PATCH", f"/orders/{order['id']}", json={"quantity": "8"}
        )
        assert response.status_code == 409

    async def test_state_machine_refuses_skipping_approval(self, client, m, shirt_bom) -> None:
        order = await make_order(client, m, m.shirt, "7", approve=False)
        for action in ("start", "reserve", "issue", "outputs", "complete"):
            body = {"quantity": "1"} if action == "outputs" else {}
            response = await call(
                client, m.org_id, "POST", f"/orders/{order['id']}/{action}", json=body
            )
            assert response.status_code == 409, (action, response.text)


class TestFlow:
    async def test_reserve_issue_output_complete_is_exact(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        oid = order["id"]

        # --- reserve: nothing moves, the promise narrows what is available
        reserved = await ok(client, org, "POST", f"/orders/{oid}/reserve", json={})
        assert reserved["shortages"] == []
        assert material(reserved, "COT-180-NAVY")["reserved"] == "8.854"
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("200"), q("8.854"))

        # --- issue (BOM-based: everything reserved) -> WIP, order starts
        issued = await ok(client, org, "POST", f"/orders/{oid}/issue", json={})
        assert issued["status"] == "IN_PROGRESS"
        fabric = material(issued, "COT-180-NAVY")
        assert (fabric["reserved"], fabric["issued"], fabric["wip"]) == ("0", "8.854", "8.854")
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("191.146"), 0)
        assert await balance(session, org, m.t.fabric, m.t.weaving, status="WIP") == (
            q("8.854"),
            0,
        )

        # --- output 3: standard consumption is the no-scrap need of the running total
        # fabric: ceil(1.234 * 3) = 3.702 ; buttons: 6 * 3 = 18
        first = await ok(client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "3"})
        assert first["produced_quantity"] == "3"
        assert material(first, "COT-180-NAVY")["consumed"] == "3.702"
        assert material(first, "COT-180-NAVY")["wip"] == "5.152"
        assert material(first, "BTN-14MM")["consumed"] == "18"
        assert await balance(session, org, m.shirt, m.t.finishing) == (q("3"), 0)

        # --- output 4 more: cumulative ceil(1.234 * 7) = 8.638, so this draws 4.936 — no drift
        second = await ok(client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "4"})
        assert second["produced_quantity"] == "7"
        assert material(second, "COT-180-NAVY")["consumed"] == "8.638"
        assert material(second, "COT-180-NAVY")["wip"] == "0.216"
        assert material(second, "BTN-14MM")["wip"] == "0"

        # --- variance: standard (with scrap) 8.854, actual 8.638 -> 0.216 under, -2.44 %
        line = next(v for v in second["variance"]["lines"] if v["sku"] == "COT-180-NAVY")
        assert (line["standard"], line["theoretical"], line["actual"]) == (
            "8.854",
            "8.638",
            "8.638",
        )
        assert (line["variance"], line["variance_percent"]) == ("-0.216", "-2.44")

        # --- completing refuses to lose the 0.216 m in WIP silently
        refused = await call(client, org, "POST", f"/orders/{oid}/complete", json={})
        assert refused.status_code == 409 and "WIP" in refused.json()["detail"]

        done = await ok(client, org, "POST", f"/orders/{oid}/complete", json={"leftover": "return"})
        assert done["status"] == "COMPLETED"
        # 200 - 8.638 consumed; the 0.216 went back to AVAILABLE; nothing left in WIP
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("191.362"), 0)
        assert await balance(session, org, m.t.fabric, m.t.weaving, status="WIP") == (0, 0)
        assert await balance(session, org, m.shirt, m.t.finishing) == (q("7"), 0)
        assert await drift(session, org) == 0

    async def test_the_events_are_staged_and_match_their_contracts(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "2")
        oid = order["id"]
        await ok(client, org, "POST", f"/orders/{oid}/reserve", json={})
        await ok(client, org, "POST", f"/orders/{oid}/issue", json={})
        await ok(client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "2"})
        await ok(client, org, "POST", f"/orders/{oid}/complete", json={"leftover": "return"})

        started = await events(session, org, "operations.production.started")
        completed = await events(session, org, "operations.production.completed")
        assert len(started) == 1 and len(completed) == 1
        assert_matches_contract("operations.production.started", started[0])
        assert_matches_contract("operations.production.completed", completed[0])
        assert completed[0]["produced_quantity"] == "2"
        assert {v["item_id"] for v in completed[0]["variance"]} == {
            str(m.t.fabric),
            str(m.t.buttons),
        }
        async with session.begin():
            actions = {
                a
                for (a,) in (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.organization_id == org)
                    )
                ).all()
            }
        assert {
            "production_order.created",
            "production_order.approved",
            "production_order.reserved",
            "production_order.materials_issued",
            "production_order.output_recorded",
            "production_order.completed",
            "inventory_move.posted",
            "bom_version.published",
        } <= actions

    async def test_output_without_enough_wip_is_refused_with_numbers(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        oid = order["id"]
        await ok(client, org, "POST", f"/orders/{oid}/reserve", json={})
        await ok(client, org, "POST", f"/orders/{oid}/issue", json={})
        # 7 shirts of standard need is 8.638 of the 8.854 issued, but 8 shirts need 9.872
        response = await call(client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "8"})
        assert response.status_code == 409
        body = response.json()
        assert body["type"].endswith("insufficient-wip")
        assert any(e["sku"] == "COT-180-NAVY" and e["short"] == "1.018" for e in body["errors"])
        # Nothing partially posted: WIP and FG are untouched.
        assert await balance(session, org, m.t.fabric, m.t.weaving, status="WIP") == (
            q("8.854"),
            0,
        )
        assert await balance(session, org, m.shirt, m.t.finishing) == (0, 0)

    async def test_genealogy_records_parent_lots_for_each_output(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        lot_a = await make_lot(session, m, m.t.fabric, "LOT-A")
        lot_b = await make_lot(session, m, m.t.fabric, "LOT-B")
        out_lot = await make_lot(session, m, m.shirt, "SHIRT-LOT")
        await stock(session, m, m.t.fabric, "5", lot=lot_a)
        await stock(session, m, m.t.fabric, "20", lot=lot_b)
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "2", "0")])
        order = await make_order(client, m, m.shirt, "4")  # needs 8 m
        oid = order["id"]
        # Pick lots explicitly: all 5 of lot A, then 3 of lot B.
        loc = str(m.t.weaving)
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/reserve",
            json={
                "lines": [
                    {
                        "material_id": order["materials"][0]["id"],
                        "location_id": loc,
                        "lot_id": str(lot_a),
                        "quantity": "5",
                    }
                ]
            },
        )
        issued = await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/issue",
            json={
                "lines": [
                    {
                        "material_id": order["materials"][0]["id"],
                        "location_id": loc,
                        "lot_id": str(lot_a),
                    },
                    {
                        "material_id": order["materials"][0]["id"],
                        "location_id": loc,
                        "lot_id": str(lot_b),
                        "quantity": "3",
                    },
                ]
            },
        )
        assert {p["lot_id"]: p["wip"] for p in issued["picks"]} == {
            str(lot_a): "5",
            str(lot_b): "3",
        }
        # 3 shirts = 6 m: FIFO over the picks -> 5 from A then 1 from B; output carries a lot.
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/outputs",
            json={"quantity": "3", "lot_id": str(out_lot)},
        )
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ProductionGenealogy).where(
                            ProductionGenealogy.organization_id == org
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert {(r.parent_lot_id, r.quantity_milli) for r in rows} == {
            (lot_a, q("5")),
            (lot_b, q("1")),
        }
        assert {r.child_lot_id for r in rows} == {out_lot}
        trace = await ok(client, org, "GET", f"/orders/{oid}/genealogy")
        assert sum(q(g["quantity"]) for g in trace) == q("6")
        # The per-lot stock tells the same story.
        assert await balance_lot(session, org, m.t.fabric, m.t.weaving, "WIP", lot_a) == 0
        assert await balance_lot(session, org, m.t.fabric, m.t.weaving, "WIP", lot_b) == q("2")
        assert await drift(session, org) == 0


async def balance_lot(session, org_id, item, location, status, lot) -> int:  # type: ignore[no-untyped-def]
    from app.models import InventoryBalance

    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item,
                    InventoryBalance.location_id == location,
                    InventoryBalance.status == status,
                    InventoryBalance.lot_id == lot,
                )
            )
        ).scalar_one_or_none()
    return row or 0


class TestShortageAndReservations:
    async def test_a_short_reservation_reports_and_raises_the_alert_event(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        await stock(session, m, m.t.fabric, "5")  # the order needs 8.854
        await stock(session, m, m.t.buttons, "100")
        await make_bom(
            client, m, m.shirt, "BOM-SHIRT", [(m.t.fabric, "1.234", "2.5"), (m.t.buttons, "6", "0")]
        )
        order = await make_order(client, m, m.shirt, "7")
        result = await ok(client, org, "POST", f"/orders/{order['id']}/reserve", json={})
        (short,) = result["shortages"]
        assert (short["sku"], short["required"], short["covered"], short["short"]) == (
            "COT-180-NAVY",
            "8.854",
            "5",
            "3.854",
        )
        assert material(result, "COT-180-NAVY")["reserved"] == "5"
        assert material(result, "BTN-14MM")["reserved"] == "42"
        (event,) = await events(session, org, "operations.production.material_shortage")
        assert_matches_contract("operations.production.material_shortage", event)
        assert event["shortages"][0]["short"] == "3.854"
        # Stock arrives; a second reserve tops the same pick up to what is needed.
        await stock(session, m, m.t.fabric, "10")
        result = await ok(client, org, "POST", f"/orders/{order['id']}/reserve", json={})
        assert result["shortages"] == []
        assert material(result, "COT-180-NAVY")["reserved"] == "8.854"
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("15"), q("8.854"))
        assert await drift(session, org) == 0

    async def test_cancel_releases_reservations(self, client, session, m: Mfg, shirt_bom) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        await ok(client, org, "POST", f"/orders/{order['id']}/reserve", json={})
        assert (await balance(session, org, m.t.fabric, m.t.weaving))[1] == q("8.854")
        cancelled = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/cancel",
            json={"reason": "customer pulled out"},
        )
        assert cancelled["status"] == "CANCELLED"
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("200"), 0)
        assert await drift(session, org) == 0

    async def test_cancel_is_refused_while_material_is_in_wip(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        await ok(client, org, "POST", f"/orders/{order['id']}/reserve", json={})
        await ok(client, org, "POST", f"/orders/{order['id']}/issue", json={})
        response = await call(
            client, org, "POST", f"/orders/{order['id']}/cancel", json={"reason": "x"}
        )
        assert response.status_code == 409 and "WIP" in response.json()["detail"]

    async def test_issue_without_a_reservation_names_the_stock(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        nothing = await call(client, org, "POST", f"/orders/{order['id']}/issue", json={})
        assert nothing.status_code == 409
        fabric = material(order, "COT-180-NAVY")
        adhoc = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/issue",
            json={
                "lines": [
                    {
                        "material_id": fabric["id"],
                        "location_id": str(m.t.weaving),
                        "quantity": "4",
                    }
                ]
            },
        )
        assert material(adhoc, "COT-180-NAVY")["issued"] == "4"
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("196"), 0)

    async def test_over_issue_needs_an_explicit_flag(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        fabric = material(order, "COT-180-NAVY")
        line = {"material_id": fabric["id"], "location_id": str(m.t.weaving), "quantity": "9"}
        refused = await call(
            client, org, "POST", f"/orders/{order['id']}/issue", json={"lines": [line]}
        )
        assert refused.status_code == 409 and "over_issue" in refused.json()["detail"]
        allowed = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/issue",
            json={"lines": [line], "over_issue": True},
        )
        assert material(allowed, "COT-180-NAVY")["issued"] == "9"

    async def test_issue_never_dips_into_stock_reserved_for_someone_else(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        await stock(session, m, m.t.fabric, "10")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        first = await make_order(client, m, m.shirt, "8")
        second = await make_order(client, m, m.shirt, "8")
        await ok(client, org, "POST", f"/orders/{first['id']}/reserve", json={})
        # The second order reserves what is left (2) and is told it is short by 6.
        result = await ok(client, org, "POST", f"/orders/{second['id']}/reserve", json={})
        assert result["shortages"][0]["short"] == "6"
        # It cannot issue 8 either: 8 are reserved for the first order.
        fabric = material(second, "COT-180-NAVY")
        blocked = await call(
            client,
            org,
            "POST",
            f"/orders/{second['id']}/issue",
            json={
                "lines": [
                    {"material_id": fabric["id"], "location_id": str(m.t.weaving), "quantity": "8"}
                ],
                "over_issue": True,
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["type"].endswith("insufficient-stock")
        assert await drift(session, org) == 0


class TestWastageAndReturns:
    async def _running(self, client, session, m: Mfg) -> dict:
        await stock(session, m, m.t.fabric, "100")
        await stock(session, m, m.t.buttons, "500")
        await make_bom(
            client, m, m.shirt, "BOM-SHIRT", [(m.t.fabric, "2", "5"), (m.t.buttons, "6", "0")]
        )
        order = await make_order(client, m, m.shirt, "10")  # fabric 21 (20 + 5 %), buttons 60
        await ok(client, m.org_id, "POST", f"/orders/{order['id']}/reserve", json={})
        return await ok(client, m.org_id, "POST", f"/orders/{order['id']}/issue", json={})

    async def test_wastage_draws_wip_by_type_and_alerts_once_over_the_allowance(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        order = await self._running(client, session, m)
        oid, fabric = order["id"], material(order, "COT-180-NAVY")
        assert fabric["required"] == "21"
        # Allowance = 5 % of the 20 m standard need = 1 m. 0.6 m of cutting loss is inside it.
        step = await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "CUTTING", "quantity": "0.6"},
        )
        assert material(step, "COT-180-NAVY")["wasted"] == "0.6"
        assert material(step, "COT-180-NAVY")["wip"] == "20.4"
        assert await events(session, org, "operations.production.wastage_threshold_exceeded") == []
        # 0.5 m of defects takes the total to 1.1 m: over the allowance -> one alert.
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "DEFECT", "quantity": "0.5"},
        )
        # ... and a third write does not re-alert.
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "DEFECT", "quantity": "0.1"},
        )
        alerts = await events(session, org, "operations.production.wastage_threshold_exceeded")
        assert len(alerts) == 1
        assert_matches_contract("operations.production.wastage_threshold_exceeded", alerts[0])
        assert (alerts[0]["wasted"], alerts[0]["allowed"], alerts[0]["threshold_percent"]) == (
            "1.1",
            "1",
            "5",
        )
        rows = await ok(client, org, "GET", f"/orders/{oid}/wastage")
        assert sorted((r["wastage_type"], r["quantity"]) for r in rows) == [
            ("CUTTING", "0.6"),
            ("DEFECT", "0.1"),
            ("DEFECT", "0.5"),
        ]
        assert await drift(session, org) == 0

    async def test_a_configured_threshold_overrides_the_bom_allowance(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        order = await self._running(client, session, m)
        oid, fabric = order["id"], material(order, "COT-180-NAVY")
        # DEFECT may be at most 1 % of 20 m = 0.2 m.
        threshold = await ok(
            client,
            org,
            "POST",
            "/wastage-thresholds",
            json={"item_id": str(m.shirt), "wastage_type": "DEFECT", "max_percent": "1"},
        )
        assert (threshold["sku"], threshold["max_percent"]) == ("SHIRT-01", "1")
        # 0.5 m of CUTTING is fine (the BOM's 5 % applies to it) ...
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "CUTTING", "quantity": "0.5"},
        )
        assert await events(session, org, "operations.production.wastage_threshold_exceeded") == []
        # ... 0.3 m of DEFECT is not.
        await ok(
            client,
            org,
            "POST",
            f"/orders/{oid}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "DEFECT", "quantity": "0.3"},
        )
        (alert,) = await events(session, org, "operations.production.wastage_threshold_exceeded")
        assert (alert["wastage_type"], alert["wasted"], alert["allowed"]) == (
            "DEFECT",
            "0.3",
            "0.2",
        )
        # Setting the same scope again updates rather than duplicates.
        await ok(
            client,
            org,
            "POST",
            "/wastage-thresholds",
            json={"item_id": str(m.shirt), "wastage_type": "DEFECT", "max_percent": "2.5"},
        )
        listed = await ok(client, org, "GET", "/wastage-thresholds")
        assert [(t["wastage_type"], t["max_percent"]) for t in listed] == [("DEFECT", "2.5")]
        await ok(client, org, "DELETE", f"/wastage-thresholds/{listed[0]['id']}", expect=204)
        assert await ok(client, org, "GET", "/wastage-thresholds") == []

    async def test_wastage_beyond_wip_is_refused(self, client, session, m: Mfg) -> None:
        order = await self._running(client, session, m)
        fabric = material(order, "COT-180-NAVY")
        response = await call(
            client,
            m.org_id,
            "POST",
            f"/orders/{order['id']}/wastage",
            json={"material_id": fabric["id"], "wastage_type": "SPILLAGE", "quantity": "21.001"},
        )
        assert response.status_code == 409
        assert response.json()["type"].endswith("insufficient-wip")

    async def test_return_puts_wip_back_to_a_chosen_location(self, client, session, m: Mfg) -> None:
        org = m.org_id
        order = await self._running(client, session, m)
        pick = next(p for p in order["picks"] if p["item_id"] == str(m.t.fabric))
        returned = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/returns",
            json={
                "lines": [
                    {"pick_id": pick["id"], "quantity": "6", "location_id": str(m.t.finishing)}
                ]
            },
        )
        fabric = material(returned, "COT-180-NAVY")
        assert (fabric["returned"], fabric["wip"], fabric["issued"]) == ("6", "15", "21")
        assert await balance(session, org, m.t.fabric, m.t.finishing) == (q("6"), 0)
        assert await balance(session, org, m.t.fabric, m.t.weaving, status="WIP") == (q("15"), 0)
        # Returning more than is in WIP is refused with the number.
        again = await call(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/returns",
            json={"lines": [{"pick_id": pick["id"], "quantity": "15.001"}]},
        )
        assert again.status_code == 409 and "15" in again.json()["detail"]
        assert await drift(session, org) == 0

    async def test_complete_can_scrap_the_leftover_as_wastage(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        order = await self._running(client, session, m)
        oid = order["id"]
        await ok(client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "10"})
        # consumed 20 m + 60 buttons; 1 m of fabric is left (the scrap allowance, unused)
        done = await ok(client, org, "POST", f"/orders/{oid}/complete", json={"leftover": "scrap"})
        assert material(done, "COT-180-NAVY")["wasted"] == "1"
        assert material(done, "COT-180-NAVY")["wip"] == "0"
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ProductionWastage).where(ProductionWastage.organization_id == org)
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.wastage_type, r.quantity_milli) for r in rows] == [("LEFTOVER", q("1"))]
        # standard 21, actual 20 consumed + 1 wasted = 21 -> zero variance
        line = next(v for v in done["variance"]["lines"] if v["sku"] == "COT-180-NAVY")
        assert (line["variance"], line["variance_percent"]) == ("0", "0.00")
        assert await balance(session, org, m.t.fabric, m.t.weaving) == (q("79"), 0)
        assert await drift(session, org) == 0

    async def test_completing_without_output_is_refused(self, client, session, m: Mfg) -> None:
        order = await self._running(client, session, m)
        response = await call(
            client, m.org_id, "POST", f"/orders/{order['id']}/complete", json={"leftover": "return"}
        )
        assert response.status_code == 409 and "output" in response.json()["detail"]


class TestSubstitutes:
    async def test_a_substitute_changes_the_item_and_scales_the_requirement(
        self, client, session, m: Mfg
    ) -> None:
        org = m.org_id
        # Dye is declared as a (1.25x) substitute for the fabric line, purely to exercise the
        # ratio arithmetic on a different unit.
        bom = await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "2", "0")], publish=False)
        await ok(
            client,
            org,
            "POST",
            f"/boms/{bom['bom_id']}/versions/{bom['id']}/components/{bom['components'][0]['id']}/substitutes",
            expect=201,
            json={"substitute_item_id": str(m.t.dye), "ratio": "1.25"},
        )
        await ok(client, org, "POST", f"/boms/{bom['bom_id']}/versions/{bom['id']}/publish")
        order = await make_order(client, m, m.shirt, "3", approve=False)
        line = material(order, "COT-180-NAVY")
        assert line["required"] == "6"
        switched = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/materials/{line['id']}/substitute",
            json={"substitute_item_id": str(m.t.dye)},
        )
        dye = material(switched, "DYE-RED-01")
        # 2 * 3 * 1.25 = 7.5
        assert (dye["required"], dye["is_substitute"], dye["ratio"]) == ("7.5", True, "1.25")
        back = await ok(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/materials/{dye['id']}/substitute",
            json={},
        )
        assert material(back, "COT-180-NAVY")["required"] == "6"
        # An item that was never declared is refused.
        bad = await call(
            client,
            org,
            "POST",
            f"/orders/{order['id']}/materials/{dye['id']}/substitute",
            json={"substitute_item_id": str(m.t.buttons)},
        )
        assert bad.status_code == 404


class TestIdempotency:
    async def test_replaying_a_key_does_not_move_stock_twice(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        oid = order["id"]
        await ok(client, org, "POST", f"/orders/{oid}/reserve", json={}, key="res-1")
        first = await ok(client, org, "POST", f"/orders/{oid}/issue", json={}, key="iss-1")
        again = await ok(client, org, "POST", f"/orders/{oid}/issue", json={}, key="iss-1")
        assert first["replayed"] is False and again["replayed"] is True
        assert material(again, "COT-180-NAVY")["issued"] == "8.854"
        out1 = await ok(
            client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "3"}, key="out-1"
        )
        out2 = await ok(
            client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "3"}, key="out-1"
        )
        assert out1["produced_quantity"] == out2["produced_quantity"] == "3"
        assert await balance(session, org, m.shirt, m.t.finishing) == (q("3"), 0)
        # The same key for a different request is a conflict, never a second execution.
        clash = await call(
            client, org, "POST", f"/orders/{oid}/outputs", json={"quantity": "1"}, key="out-1"
        )
        assert clash.status_code == 409
        assert await drift(session, org) == 0

    async def test_creating_twice_with_one_key_makes_one_order(
        self, client, m: Mfg, shirt_bom
    ) -> None:
        body = {
            "item_id": str(m.shirt),
            "quantity": "3",
            "output_location_id": str(m.t.finishing),
        }
        first = await ok(client, m.org_id, "POST", "/orders", expect=201, json=body, key="mk-1")
        again = await ok(client, m.org_id, "POST", "/orders", expect=200, json=body, key="mk-1")
        assert first["id"] == again["id"]
        listing = await ok(client, m.org_id, "GET", "/orders")
        assert listing["total"] == 1


class TestProductionPlannerRole:
    async def test_a_planner_runs_orders_but_cannot_approve_or_publish(
        self, client, m: Mfg, shirt_bom
    ) -> None:
        planner = [P_BOM_READ, P_ORD_READ, P_ORD_WRITE, P_ORD_ISSUE]
        order = await ok(
            client,
            m.org_id,
            "POST",
            "/orders",
            expect=201,
            perms=planner,
            json={
                "item_id": str(m.shirt),
                "quantity": "1",
                "output_location_id": str(m.t.finishing),
            },
        )
        denied = await call(
            client, m.org_id, "POST", f"/orders/{order['id']}/approve", perms=planner
        )
        assert denied.status_code == 403
        allowed = await call(
            client, m.org_id, "POST", f"/orders/{order['id']}/approve", perms=[P_ORD_APPROVE]
        )
        assert allowed.status_code == 200


class TestMultiLevel:
    async def test_a_produced_component_is_made_by_its_own_order_and_the_lots_chain(
        self, client, session, m: Mfg
    ) -> None:
        """Shirt <- panel <- fabric. The panel is a produced item with its own BOM; its order's
        output becomes stock, the shirt order consumes that stock, and the genealogy rows chain
        fabric lot -> panel lot -> shirt lot."""
        org = m.org_id
        fabric_lot = await make_lot(session, m, m.t.fabric, "FAB-LOT")
        panel_lot = await make_lot(session, m, m.panel, "PANEL-LOT")
        shirt_lot = await make_lot(session, m, m.shirt, "SHIRT-LOT")
        await stock(session, m, m.t.fabric, "50", lot=fabric_lot)
        await make_bom(client, m, m.panel, "BOM-PANEL", [(m.t.fabric, "1.2", "0")])
        await make_bom(client, m, m.shirt, "BOM-SHIRT", [(m.panel, "2", "0")])

        panel_order = await make_order(client, m, m.panel, "10", output_location=m.t.weaving)
        pid = panel_order["id"]
        await ok(client, org, "POST", f"/orders/{pid}/reserve", json={})
        await ok(client, org, "POST", f"/orders/{pid}/issue", json={})
        await ok(
            client,
            org,
            "POST",
            f"/orders/{pid}/outputs",
            json={"quantity": "10", "lot_id": str(panel_lot)},
        )
        await ok(client, org, "POST", f"/orders/{pid}/complete", json={})
        made = await balance_lot(session, org, m.panel, m.t.weaving, "AVAILABLE", panel_lot)
        assert made == q("10")

        shirt_order = await make_order(client, m, m.shirt, "3")  # 6 m of panel
        sid = shirt_order["id"]
        reserved = await ok(client, org, "POST", f"/orders/{sid}/reserve", json={})
        assert reserved["shortages"] == []
        assert reserved["picks"][0]["lot_id"] == str(panel_lot)
        await ok(client, org, "POST", f"/orders/{sid}/issue", json={})
        await ok(
            client,
            org,
            "POST",
            f"/orders/{sid}/outputs",
            json={"quantity": "3", "lot_id": str(shirt_lot)},
        )
        await ok(client, org, "POST", f"/orders/{sid}/complete", json={"leftover": "return"})

        first = await ok(client, org, "GET", f"/orders/{pid}/genealogy")
        second = await ok(client, org, "GET", f"/orders/{sid}/genealogy")
        assert [(g["parent_lot_id"], g["child_lot_id"], g["quantity"]) for g in first] == [
            (str(fabric_lot), str(panel_lot), "12")
        ]
        assert [(g["parent_lot_id"], g["child_lot_id"], g["quantity"]) for g in second] == [
            (str(panel_lot), str(shirt_lot), "6")
        ]
        # 10 m of panel made, 6 consumed by shirts, 4 left; 50 - 12 of fabric left
        left_panel = await balance_lot(session, org, m.panel, m.t.weaving, "AVAILABLE", panel_lot)
        left_fabric = await balance_lot(
            session, org, m.t.fabric, m.t.weaving, "AVAILABLE", fabric_lot
        )
        assert (left_panel, left_fabric) == (q("4"), q("38"))
        assert await drift(session, org) == 0

        # The BOM explosion sees the same two levels.
        listed = await ok(client, org, "GET", "/boms", params={"item_id": str(m.shirt)})
        shirt_bom = listed["items"][0]
        tree = await ok(
            client,
            org,
            "GET",
            f"/boms/{shirt_bom['id']}/versions/{shirt_bom['current_version_id']}/explosion",
            params={"quantity": "3"},
        )
        assert [(t["sku"], t["quantity"]) for t in tree["leaf_totals"]] == [("COT-180-NAVY", "7.2")]


class TestExternalRelease:
    async def test_a_reservation_released_through_the_reservations_api_is_noticed(
        self, client, session, m: Mfg, shirt_bom
    ) -> None:
        org = m.org_id
        order = await make_order(client, m, m.shirt, "7")
        oid = order["id"]
        reserved = await ok(client, org, "POST", f"/orders/{oid}/reserve", json={})
        fabric_pick = next(p for p in reserved["picks"] if p["item_id"] == str(m.t.fabric))
        with as_principal(principal_for(org, ["operations.inventory.stock.reserve:ORG"])):
            released = await client.post(
                f"/api/v1/operations/inventory/reservations/{fabric_pick['reservation_id']}:release"
            )
        assert released.status_code == 200
        # The order's next action re-reads the kernel's truth: the fabric is no longer reserved,
        # so a BOM-based issue moves only the buttons.
        issued = await ok(client, org, "POST", f"/orders/{oid}/issue", json={})
        assert material(issued, "COT-180-NAVY")["issued"] == "0"
        assert material(issued, "COT-180-NAVY")["reserved"] == "0"
        assert material(issued, "BTN-14MM")["issued"] == "42"
        assert await drift(session, org) == 0
