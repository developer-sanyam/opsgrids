"""MRP end to end through the real routes: netting against fixtures (available, reserved, open PO,
open production), multi-level explosion with scrap, pegging, determinism, idempotency,
requisition requests, events against their contracts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy import select, text

from app.models import AuditLog, OutboxEvent
from app.mrp.models import MrpDemand, MrpRun, MrpShortage
from tests.manufacturing_fixtures import make_order, ok
from tests.mrp_fixtures import (
    P_READ,
    P_RUN,
    Plant,
    add_purchase_order,
    add_sales_order,
    build_plant,
    mrp,
    mrp_ok,
    need,
    reserve_for,
    shortages,
    start_run,
    stock_in,
)

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "events"


def contract(event_type: str, payload: dict) -> None:
    schema = json.loads((SCHEMAS / event_type / "v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)


async def events(session, org_id, event_type: str) -> list[dict]:
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


@pytest_asyncio.fixture
async def p(client, session, org_id) -> Plant:
    return await build_plant(client, session, org_id)


def q_(milli: int) -> str:
    from app.inventory.quantity import format_qty

    return format_qty(milli)


class TestNettingAgainstFixtures:
    async def test_two_level_explosion_netted_against_stock_po_and_open_production(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        # supply: panel 2 m, fabric 5 m, dye 0.5 kg on hand; buttons 20 on an approved PO of which
        # 5 are in; a draft PO and a received PO of buttons must not count.
        await stock_in(session, p, panel="2", fabric="5", dye="0.5")
        await add_purchase_order(session, org, "PO-1", [("BTN-14MM", 20, 5)])
        await add_purchase_order(session, org, "PO-DRAFT", [("BTN-14MM", 100, 0)], status="draft")
        await add_purchase_order(
            session, org, "PO-DONE", [("BTN-14MM", 100, 100)], status="received"
        )
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])

        run = await start_run(client, org, sources=["SALES_ORDER"])
        assert run["status"] == "COMPLETED" and run["execution"] == "SYNC"
        assert run["demand_count"] == 1
        s = await shortages(client, org, run["id"])

        # shirts: nothing in stock -> make 10
        assert (s["SHIRT-01"]["action"], s["SHIRT-01"]["shortage"], s["SHIRT-01"]["level"]) == (
            "MAKE",
            "10",
            0,
        )
        # panel: gross 12 (1.2 per shirt), 2 on hand -> make 10
        assert (s["PANEL-NAVY"]["gross"], s["PANEL-NAVY"]["supply"]) == ("12", "2")
        assert (s["PANEL-NAVY"]["action"], s["PANEL-NAVY"]["shortage"]) == ("MAKE", "10")
        # buttons: 50 gross - 15 open PO (draft and received ignored) -> buy 35
        assert (s["BTN-14MM"]["gross"], s["BTN-14MM"]["open_po"]) == ("50", "15")
        assert (s["BTN-14MM"]["action"], s["BTN-14MM"]["shortage"]) == ("BUY", "35")
        # only the NET panel (10 m) explodes: fabric ceil(1.234 * 10 * 1.025)
        fabric_gross = need(1234, 1000, 10_000, 250)
        assert fabric_gross == 12_649
        assert s["COT-180-NAVY"]["gross"] == q_(fabric_gross)
        assert s["COT-180-NAVY"]["shortage"] == q_(fabric_gross - 5_000) == "7.649"
        dye_gross = need(50, 1000, 10_000, 1000)
        assert s["DYE-RED-01"]["gross"] == q_(dye_gross) == "0.55"
        assert s["DYE-RED-01"]["shortage"] == "0.05"
        assert run["shortage_count"] == 5 and run["items_planned"] == 5

    async def test_reserved_stock_is_not_available(self, client, session, p: Plant) -> None:
        org = p.org_id
        await stock_in(session, p, panel="4", fabric="30", dye="5", buttons="100")
        await reserve_for(session, p, p.m.panel, "3")  # held for something outside this run
        await add_sales_order(session, org, "SO-1", [(p.m.panel, "4")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        s = await shortages(client, org, run["id"])
        panel = s["PANEL-NAVY"]
        assert (panel["on_hand"], panel["reserved"], panel["available"]) == ("4", "3", "1")
        assert (panel["gross"], panel["shortage"]) == ("4", "3")

    async def test_stock_reserved_for_the_order_itself_is_not_counted_twice(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await stock_in(session, p, fabric="5")
        order_id = await add_sales_order(
            session, org, "SO-1", [(p.t.fabric, "8")], status="ACCEPTED"
        )
        # the order already holds 5 m of the 8 it needs
        await reserve_for(
            session, p, p.t.fabric, "5", reference_type="order", reference_id=order_id
        )
        run = await start_run(client, org, sources=["SALES_ORDER"])
        demands = await mrp_ok(client, org, "GET", f"/runs/{run['id']}/demands")
        (d,) = demands["items"]
        assert (d["requested"], d["reserved"], d["quantity"]) == ("8", "5", "3")
        s = await shortages(client, org, run["id"])
        # 3 still to cover, 0 free (all 5 are reserved) -> short 3, not 8 and not 0
        fabric = s["COT-180-NAVY"]
        assert (fabric["gross"], fabric["available"], fabric["shortage"]) == ("3", "0", "3")

    async def test_open_production_orders_are_demand_for_material_and_supply_for_output(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await stock_in(session, p, fabric="5")
        # an approved order for 4 m of panel: needs fabric/dye, and will add 4 m of panel
        order = await make_order(client, p.m, p.m.panel, "4", output_location=p.t.finishing)
        fabric_need = need(1234, 1000, 4_000, 250)
        await add_sales_order(session, org, "SO-1", [(p.m.panel, "10")])

        only_orders = await start_run(client, org, sources=["PRODUCTION_ORDER"])
        s = await shortages(client, org, only_orders["id"])
        assert set(s) == {"COT-180-NAVY", "DYE-RED-01"}
        assert s["COT-180-NAVY"]["gross"] == q_(fabric_need)
        assert s["COT-180-NAVY"]["shortage"] == q_(fabric_need - 5_000)
        demands = await mrp_ok(client, org, "GET", f"/runs/{only_orders['id']}/demands")
        assert {d["source_ref"] for d in demands["items"]} == {order["number"]}

        both = await start_run(client, org, sources=["SALES_ORDER", "PRODUCTION_ORDER"])
        s = await shortages(client, org, both["id"])
        # panel: 10 wanted, the open order brings 4 -> make 6 (not 10)
        assert (s["PANEL-NAVY"]["open_production"], s["PANEL-NAVY"]["shortage"]) == ("4", "6")
        # fabric: the order's own need + the net 6 m of panel
        fabric_total = fabric_need + need(1234, 1000, 6_000, 250)
        assert s["COT-180-NAVY"]["gross"] == q_(fabric_total)

    async def test_a_materials_reservation_is_not_demand_again(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await stock_in(session, p, fabric="20", dye="5")
        order = await make_order(client, p.m, p.m.panel, "4", output_location=p.t.finishing)
        await ok(client, org, "POST", f"/orders/{order['id']}/reserve", json={})
        run = await start_run(client, org, sources=["PRODUCTION_ORDER"])
        # everything the order needs is already reserved: no open demand, no shortage
        assert run["demand_count"] == 0 and run["shortage_count"] == 0

    async def test_manual_demand_and_a_covered_run_has_no_shortage(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await stock_in(session, p, panel="9", fabric="1")
        run = await start_run(
            client,
            org,
            sources=["MANUAL"],
            manual_demands=[
                {"item_id": str(p.m.panel), "quantity": "9", "due_date": "2026-11-15"},
            ],
        )
        assert run["status"] == "COMPLETED" and run["shortage_count"] == 0
        run2 = await start_run(
            client,
            org,
            sources=["MANUAL"],
            manual_demands=[
                {"item_id": str(p.m.panel), "quantity": "10", "due_date": "2026-11-15"}
            ],
        )
        s = await shortages(client, org, run2["id"])
        assert s["PANEL-NAVY"]["shortage"] == "1" and s["PANEL-NAVY"]["need_by"] == "2026-11-15"
        # the fabric need is the net 1 m of panel, against 1 m on hand
        assert s["COT-180-NAVY"]["gross"] == q_(need(1234, 1000, 1_000, 250))

    async def test_pegging_traces_components_to_the_sales_order(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-A", [(p.m.shirt, "4")])
        await add_sales_order(session, org, "SO-B", [(p.m.shirt, "6")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        s = await shortages(client, org, run["id"])
        detail = await mrp_ok(
            client, org, "GET", f"/runs/{run['id']}/shortages/{s['BTN-14MM']['id']}"
        )
        lines = {(ln["source_ref"], ln["parent_sku"]): ln["quantity"] for ln in detail["lines"]}
        assert lines == {("SO-A", "SHIRT-01"): "20", ("SO-B", "SHIRT-01"): "30"}
        fabric = await mrp_ok(
            client, org, "GET", f"/runs/{run['id']}/shortages/{s['COT-180-NAVY']['id']}"
        )
        assert sum(float(ln["quantity"]) for ln in fabric["lines"]) == pytest.approx(
            float(s["COT-180-NAVY"]["gross"])
        )
        assert {ln["parent_sku"] for ln in fabric["lines"]} == {"PANEL-NAVY"}
        assert {ln["source_ref"] for ln in fabric["lines"]} == {"SO-A", "SO-B"}

    async def test_only_open_orders_are_demand(self, client, session, p: Plant) -> None:
        org = p.org_id
        for number, status in [
            ("SO-DRAFT", "DRAFT"),
            ("SO-READY", "READY"),
            ("SO-DONE", "COMPLETED"),
            ("SO-CANCEL", "CANCELLED"),
            ("SO-OPEN", "ACCEPTED"),
        ]:
            await add_sales_order(session, org, number, [(p.t.buttons, "1")], status=status)
        run = await start_run(client, org, sources=["SALES_ORDER"])
        demands = await mrp_ok(client, org, "GET", f"/runs/{run['id']}/demands")
        assert [d["source_ref"] for d in demands["items"]] == ["SO-OPEN"]

    async def test_effective_dates_choose_the_bom_in_force_on_the_plan_date(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        # a second published panel version from 2027: 2 m of fabric per metre, no dye
        draft = await ok(
            client,
            org,
            "POST",
            f"/boms/{p.panel_bom}/versions",
            expect=201,
            json={"effective_from": "2027-01-01"},
        )
        vid = draft["id"]
        await ok(
            client,
            org,
            "POST",
            f"/boms/{p.panel_bom}/versions/{vid}/components",
            expect=201,
            json={"component_item_id": str(p.t.fabric), "quantity": "2", "scrap_percent": "0"},
        )
        await ok(client, org, "POST", f"/boms/{p.panel_bom}/versions/{vid}/publish")
        manual = [{"item_id": str(p.m.panel), "quantity": "10"}]
        now = await start_run(client, org, sources=["MANUAL"], manual_demands=manual)
        future = await start_run(
            client, org, sources=["MANUAL"], manual_demands=manual, plan_date="2027-02-01"
        )
        a = await shortages(client, org, now["id"])
        b = await shortages(client, org, future["id"])
        assert a["COT-180-NAVY"]["gross"] == q_(need(1234, 1000, 10_000, 250)) and "DYE-RED-01" in a
        assert b["COT-180-NAVY"]["gross"] == "20" and "DYE-RED-01" not in b


class TestSubstitutes:
    async def test_substitutes_are_ignored_unless_chosen(self, client, session, p: Plant) -> None:
        org = p.org_id
        from app.inventory import services as inv_services

        async with session.begin():
            alt = await inv_services.create_inventory_item(
                session,
                p.t.ctx,
                sku="COT-200-NAVY",
                name="Cotton 200 GSM, navy",
                category="raw_materials",
                warehouse="Weaving Store",
                uom="m",
            )
            lining_item = await inv_services.create_inventory_item(
                session,
                p.t.ctx,
                sku="LINING",
                name="Lining",
                category="components",
                warehouse="Weaving Store",
                uom="m",
            )
        # lining = 1 m of the navy cotton per metre, with a declared substitute (1.25 m of the
        # 200 GSM per metre of the 180)
        created = await ok(
            client,
            org,
            "POST",
            "/boms",
            expect=201,
            json={"code": "BOM-LINING", "name": "lining recipe", "item_id": str(lining_item.id)},
        )
        version = created["versions"][0]
        comp = await ok(
            client,
            org,
            "POST",
            f"/boms/{created['id']}/versions/{version['id']}/components",
            expect=201,
            json={"component_item_id": str(p.t.fabric), "quantity": "1", "scrap_percent": "0"},
        )
        component = comp["components"][0]["id"]
        await ok(
            client,
            org,
            "POST",
            f"/boms/{created['id']}/versions/{version['id']}/components/{component}/substitutes",
            expect=201,
            json={"substitute_item_id": str(alt.id), "ratio": "1.25"},
        )
        await ok(client, org, "POST", f"/boms/{created['id']}/versions/{version['id']}/publish")

        manual = [{"item_id": str(lining_item.id), "quantity": "8"}]
        plain = await start_run(client, org, sources=["MANUAL"], manual_demands=manual)
        chosen = await start_run(
            client,
            org,
            sources=["MANUAL"],
            manual_demands=manual,
            substitutes=[{"component_item_id": str(p.t.fabric), "substitute_item_id": str(alt.id)}],
        )
        a = await shortages(client, org, plain["id"])
        b = await shortages(client, org, chosen["id"])
        assert a["COT-180-NAVY"]["gross"] == "8" and "COT-200-NAVY" not in a
        assert b["COT-200-NAVY"]["gross"] == "10" and "COT-180-NAVY" not in b  # 8 * 1.25

    async def test_choosing_an_undeclared_substitute_is_refused(
        self, client, session, p: Plant
    ) -> None:
        response = await mrp(
            client,
            p.org_id,
            "POST",
            "/runs",
            json={
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(p.m.panel), "quantity": "1"}],
                "substitutes": [
                    {
                        "component_item_id": str(p.t.fabric),
                        "substitute_item_id": str(p.t.dye),
                    }
                ],
            },
        )
        assert response.status_code == 422, response.text


class TestRunLifecycle:
    async def test_a_run_stores_its_demand_and_result_and_audits_and_emits(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "3")])
        run = await start_run(client, org, sources=["SALES_ORDER"], notes="week 42")
        assert run["number"] == "MRP-00001" and run["notes"] == "week 42"
        assert run["sources"] == ["SALES_ORDER"] and run["result_hash"]
        again = await mrp_ok(client, org, "GET", f"/runs/{run['id']}")
        assert again["result_hash"] == run["result_hash"]
        listing = await mrp_ok(client, org, "GET", "/runs")
        assert [r["number"] for r in listing["items"]] == ["MRP-00001"]
        second = await start_run(client, org, sources=["SALES_ORDER"])
        assert second["number"] == "MRP-00002"
        listing = await mrp_ok(client, org, "GET", "/runs")
        assert [r["number"] for r in listing["items"]] == ["MRP-00002", "MRP-00001"]
        queued = await mrp_ok(client, org, "GET", "/runs", params={"status": "QUEUED"})
        assert queued["total"] == 0

        (event, _) = await events(session, org, "operations.mrp.run_completed")
        contract("operations.mrp.run_completed", event)
        assert event["shortage_count"] == run["shortage_count"]
        async with session.begin():
            audit = (
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.organization_id == org)
                    )
                )
                .scalars()
                .all()
            )
        assert "mrp_run.created" in audit and "mrp_run.completed" in audit

    async def test_results_are_deterministic_across_runs(self, client, session, p: Plant) -> None:
        org = p.org_id
        await stock_in(session, p, panel="2", fabric="5", dye="0.5")
        await add_purchase_order(session, org, "PO-1", [("BTN-14MM", 20, 5)])
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        await add_sales_order(session, org, "SO-2", [(p.m.shirt, "3"), (p.m.panel, "2")])
        hashes = {
            (await start_run(client, org, sources=["SALES_ORDER"]))["result_hash"] for _ in range(4)
        }
        assert len(hashes) == 1

    async def test_a_cyclic_bom_is_a_409_naming_the_loop_and_stores_nothing(
        self, client, session, admin_session, p: Plant
    ) -> None:
        org = p.org_id
        # the publish guard refuses cycles, so build one below it: point the panel's fabric line at
        # the shirt (shirt -> panel -> shirt)
        await admin_session.execute(
            text("ALTER TABLE operations.bom_components DISABLE TRIGGER USER")
        )
        try:
            await admin_session.execute(
                text(
                    "UPDATE operations.bom_components SET component_item_id = :shirt "
                    "WHERE organization_id = :o AND component_item_id = :fabric"
                ),
                {"shirt": p.m.shirt, "fabric": p.t.fabric, "o": org},
            )
        finally:
            await admin_session.execute(
                text("ALTER TABLE operations.bom_components ENABLE TRIGGER USER")
            )
            await admin_session.commit()
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "1")])
        response = await mrp(client, org, "POST", "/runs", json={"sources": ["SALES_ORDER"]})
        assert response.status_code == 409, response.text
        assert "SHIRT-01" in response.text and "PANEL-NAVY" in response.text
        listing = await mrp_ok(client, org, "GET", "/runs")
        assert listing["total"] == 0

    async def test_validation(self, client, session, p: Plant) -> None:
        org = p.org_id
        for body in (
            {"sources": []},
            {"sources": ["MANUAL"]},  # nothing to plan from
            {
                "sources": ["SALES_ORDER"],
                "manual_demands": [{"item_id": str(p.m.panel), "quantity": "1"}],
            },
            {"sources": ["BOGUS"]},
            {"sources": ["SALES_ORDER", "SALES_ORDER"]},
            {"unknown": 1},
            {
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(p.m.panel), "quantity": "1.2345"}],
            },
            {
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(p.m.panel), "quantity": "0"}],
            },
            {
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(p.m.panel), "quantity": 1.5}],
            },
            {
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(p.m.shirt), "quantity": "1.5"}],
            },  # pcs
        ):
            response = await mrp(client, org, "POST", "/runs", json=body)
            assert response.status_code == 422, (body, response.text)
        unknown_item = await mrp(
            client,
            org,
            "POST",
            "/runs",
            json={
                "sources": ["MANUAL"],
                "manual_demands": [{"item_id": str(uuid.uuid4()), "quantity": "1"}],
            },
        )
        assert unknown_item.status_code == 404
        bad_status = await mrp(client, org, "GET", "/runs", params={"status": "NOPE"})
        assert bad_status.status_code == 422

    async def test_demand_sources_preview(self, client, session, p: Plant) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "1"), (p.t.buttons, "2")])
        body = await mrp_ok(client, org, "GET", "/demand-sources")
        assert body["sales_order_lines"] == 2 and body["production_order_materials"] == 0
        assert body["mode_if_auto"] == "sync" and body["sync_limit"] >= 1


class TestIdempotency:
    async def test_a_replayed_run_is_the_same_run(self, client, session, p: Plant) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "3")])
        body = {"sources": ["SALES_ORDER"]}
        first = await mrp(client, org, "POST", "/runs", json=body, key="k-1")
        again = await mrp(client, org, "POST", "/runs", json=body, key="k-1")
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
        listing = await mrp_ok(client, org, "GET", "/runs")
        assert listing["total"] == 1
        different = await mrp(
            client, org, "POST", "/runs", json={"sources": ["SALES_ORDER"], "notes": "x"}, key="k-1"
        )
        assert different.status_code == 409

    async def test_a_replayed_requisition_request_does_not_emit_twice(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "3")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        first = await mrp(
            client, org, "POST", f"/runs/{run['id']}/requisitions", json={}, key="r-1"
        )
        again = await mrp(
            client, org, "POST", f"/runs/{run['id']}/requisitions", json={}, key="r-1"
        )
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["request_id"] == first.json()["request_id"]
        assert again.json()["replayed"] is True
        assert len(await events(session, org, "operations.mrp.requisition_requested")) == 1


class TestRequisitionRequest:
    async def test_it_requests_only_purchased_shortages_and_links_them(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await stock_in(session, p, fabric="5")
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        s = await shortages(client, org, run["id"])
        assert {k for k, v in s.items() if v["action"] == "MAKE"} == {"SHIRT-01", "PANEL-NAVY"}

        out = await mrp_ok(
            client,
            org,
            "POST",
            f"/runs/{run['id']}/requisitions",
            expect=201,
            json={"note": "urgent"},
        )
        lines = {ln["sku"]: ln for ln in out["lines"]}
        assert set(lines) == {"BTN-14MM", "COT-180-NAVY", "DYE-RED-01"}
        assert lines["BTN-14MM"]["quantity"] == "50" and lines["BTN-14MM"]["uom"] == "pcs"
        assert lines["COT-180-NAVY"]["quantity"] == s["COT-180-NAVY"]["shortage"]

        # linked back on every requested shortage, none on the MAKE rows
        s = await shortages(client, org, run["id"])
        assert {s[k]["requisition_request_id"] for k in lines} == {out["request_id"]}
        assert s["SHIRT-01"]["requisition_request_id"] is None
        pending = await shortages(client, org, run["id"], requested="false")
        assert set(pending) == {"SHIRT-01", "PANEL-NAVY"}

        (event,) = await events(session, org, "operations.mrp.requisition_requested")
        contract("operations.mrp.requisition_requested", event)
        assert event["request_id"] == out["request_id"] and event["note"] == "urgent"
        assert {ln["sku"] for ln in event["lines"]} == set(lines)

        # nothing left to request, and an explicit request for a requested row is a conflict
        again = await mrp(client, org, "POST", f"/runs/{run['id']}/requisitions", json={})
        assert again.status_code == 409
        explicit = await mrp(
            client,
            org,
            "POST",
            f"/runs/{run['id']}/requisitions",
            json={"shortage_ids": [s["BTN-14MM"]["id"]]},
        )
        assert explicit.status_code == 409

    async def test_a_subset_and_the_refusals(self, client, session, p: Plant) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "2")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        s = await shortages(client, org, run["id"])
        make = await mrp(
            client,
            org,
            "POST",
            f"/runs/{run['id']}/requisitions",
            json={"shortage_ids": [s["SHIRT-01"]["id"]]},
        )
        assert make.status_code == 422  # a produced item is made, not bought
        stranger = await mrp(
            client,
            org,
            "POST",
            f"/runs/{run['id']}/requisitions",
            json={"shortage_ids": [str(uuid.uuid4())]},
        )
        assert stranger.status_code == 404
        one = await mrp_ok(
            client,
            org,
            "POST",
            f"/runs/{run['id']}/requisitions",
            expect=201,
            json={"shortage_ids": [s["BTN-14MM"]["id"]]},
        )
        assert [ln["sku"] for ln in one["lines"]] == ["BTN-14MM"]
        rest = await mrp_ok(
            client, org, "POST", f"/runs/{run['id']}/requisitions", expect=201, json={}
        )
        assert {ln["sku"] for ln in rest["lines"]} == {"COT-180-NAVY", "DYE-RED-01"}

    async def test_a_queued_run_cannot_be_requisitioned(self, client, session, p: Plant) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "2")])
        run = await start_run(client, org, expect=202, sources=["SALES_ORDER"], mode="worker")
        assert run["status"] == "QUEUED"
        response = await mrp(client, org, "POST", f"/runs/{run['id']}/requisitions", json={})
        assert response.status_code == 409


class TestRowsAreImmutableAndScoped:
    async def test_shortage_rows_hold_the_arithmetic(self, client, session, p: Plant) -> None:
        org = p.org_id
        await stock_in(session, p, fabric="5")
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(MrpShortage).where(MrpShortage.run_id == uuid.UUID(run["id"]))
                    )
                )
                .scalars()
                .all()
            )
            demands = (
                (
                    await session.execute(
                        select(MrpDemand).where(MrpDemand.run_id == uuid.UUID(run["id"]))
                    )
                )
                .scalars()
                .all()
            )
            stored = (
                await session.execute(select(MrpRun).where(MrpRun.id == uuid.UUID(run["id"])))
            ).scalar_one()
        for r in rows:
            free = max(0, r.on_hand_milli - r.reserved_milli)
            assert r.shortage_milli == r.gross_milli - (
                free + r.open_po_milli + r.open_production_milli
            )
        assert [d.quantity_milli for d in demands] == [10_000]
        assert stored.demand_count == 1 and stored.shortage_count == len(rows)

    async def test_a_reader_cannot_run(self, client, session, p: Plant) -> None:
        response = await mrp(
            client,
            p.org_id,
            "POST",
            "/runs",
            perms=[P_READ],
            json={"sources": ["SALES_ORDER"]},
        )
        assert response.status_code == 403
        ran = await mrp(
            client,
            p.org_id,
            "POST",
            "/runs",
            perms=[P_RUN],
            json={"sources": ["SALES_ORDER"]},
        )
        assert ran.status_code == 201
