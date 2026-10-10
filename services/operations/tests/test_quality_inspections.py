"""Quality over HTTP (inventory plan Phase 4): templates, opening an inspection, recording readings,
and every decision — each one proven against the **stock** it moves, the append-only ledger, the
audit trail, the outbox and the reconciler, not just the response body.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from app.inventory.reconcile import reconcile_organization
from app.models import AuditLog, InventoryMove, LedgerEntry, OutboxEvent
from tests.inventory_fixtures import balance, ledger_total, q
from tests.quality_fixtures import (
    ALL_PASS,
    QcShop,
    build_qc_shop,
    call,
    make_template,
    open_inspection,
    open_ok,
    record,
    template_body,
)

pytestmark = pytest.mark.integration

EVENTS = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "events"


@pytest.fixture
async def qc(session, org_id) -> QcShop:
    return await build_qc_shop(session, org_id, stock=100)


async def buckets(session, qc: QcShop) -> dict[str, int]:
    """The widget's quantity in every status at the default location, in milli."""
    out = {}
    for status in ("AVAILABLE", "QUALITY_HOLD", "REJECTED", "QUARANTINE"):
        out[status] = (
            await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status=status)
        )[0]
    return out


async def clean(session, qc: QcShop) -> None:
    """The reconciler's verdict: ledger and balances agree everywhere."""
    async with session.begin():
        report = await reconcile_organization(session, qc.org_id)
    assert report.clean, report.findings


async def events(session, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
            .scalars()
            .all()
        )


async def audit_actions(session) -> list[str]:  # type: ignore[no-untyped-def]
    async with session.begin():
        return [r[0] for r in (await session.execute(select(AuditLog.action))).all()]


def assert_valid_event(event: OutboxEvent) -> None:
    schema = json.loads((EVENTS / event.event_type / "v1.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(event.payload))
    assert not errors, [e.message for e in errors]


# ------------------------------------------------------------------ templates


class TestTemplates:
    async def test_create_and_read_back_with_exact_decimal_strings(
        self, client, qc: QcShop
    ) -> None:
        made = await make_template(client, qc.org_id)
        assert made["code"] == "INC-FAB"
        gsm, shrink, colour = made["params"]
        assert (gsm["min"], gsm["max"]) == ("170", "190")
        assert (shrink["min"], shrink["max"], shrink["is_critical"]) == (None, "3.5", True)
        assert (colour["kind"], colour["min"], colour["max"]) == ("pass_fail", None, None)
        assert [p["position"] for p in made["params"]] == [0, 1, 2]
        read = await call(client, qc.org_id, "GET", f"/templates/{made['id']}")
        assert read.json() == made

    async def test_duplicate_code_is_409_and_a_deleted_code_can_be_reused(
        self, client, qc: QcShop
    ) -> None:
        first = await make_template(client, qc.org_id)
        dup = await call(client, qc.org_id, "POST", "/templates", json=template_body())
        assert dup.status_code == 409
        gone = await call(client, qc.org_id, "DELETE", f"/templates/{first['id']}")
        assert gone.status_code == 204
        assert (
            await call(client, qc.org_id, "GET", f"/templates/{first['id']}")
        ).status_code == 404
        again = await call(client, qc.org_id, "POST", "/templates", json=template_body())
        assert again.status_code == 201

    @pytest.mark.parametrize(
        "params",
        [
            [{"name": "A", "kind": "numeric", "min": "5", "max": "1"}],
            [{"name": "A", "kind": "pass_fail", "min": "1"}],
            [{"name": "A"}, {"name": "a"}],
            [{"name": "A", "min": "1.2345"}],
            [{"name": "A", "min": 1.5}],
        ],
    )
    async def test_bad_parameters_are_422(self, client, qc: QcShop, params: list[dict]) -> None:
        response = await call(
            client, qc.org_id, "POST", "/templates", json=template_body(params=params)
        )
        assert response.status_code == 422, response.text

    async def test_patch_replaces_the_parameter_list_only_when_given(
        self, client, qc: QcShop
    ) -> None:
        made = await make_template(client, qc.org_id)
        renamed = await call(
            client, qc.org_id, "PATCH", f"/templates/{made['id']}", json={"name": "Fabric v2"}
        )
        assert renamed.json()["name"] == "Fabric v2"
        assert len(renamed.json()["params"]) == 3
        swapped = await call(
            client,
            qc.org_id,
            "PATCH",
            f"/templates/{made['id']}",
            json={"params": [{"name": "Width", "min": "145", "max": "150", "unit": "cm"}]},
        )
        assert [p["name"] for p in swapped.json()["params"]] == ["Width"]
        null_name = await call(
            client, qc.org_id, "PATCH", f"/templates/{made['id']}", json={"name": None}
        )
        assert null_name.status_code == 422

    async def test_filters_and_pagination(self, client, qc: QcShop) -> None:
        await make_template(client, qc.org_id, code="A")
        await make_template(client, qc.org_id, code="B", kind="finished")
        await make_template(client, qc.org_id, code="C", is_active=False)
        only_finished = await call(
            client, qc.org_id, "GET", "/templates", params={"kind": "finished"}
        )
        assert [t["code"] for t in only_finished.json()["items"]] == ["B"]
        inactive = await call(client, qc.org_id, "GET", "/templates", params={"active": "false"})
        assert [t["code"] for t in inactive.json()["items"]] == ["C"]
        allt = await call(client, qc.org_id, "GET", "/templates")
        assert allt.json()["total"] == 3

    async def test_a_retried_create_with_the_same_key_makes_one_template(
        self, client, qc: QcShop
    ) -> None:
        body = template_body()
        one = await call(client, qc.org_id, "POST", "/templates", json=body, key="tpl-1")
        two = await call(client, qc.org_id, "POST", "/templates", json=body, key="tpl-1")
        assert one.status_code == two.status_code == 201
        assert one.json()["id"] == two.json()["id"]
        other = await call(
            client, qc.org_id, "POST", "/templates", json=template_body(code="OTHER"), key="tpl-1"
        )
        assert other.status_code == 409
        total = await call(client, qc.org_id, "GET", "/templates")
        assert total.json()["total"] == 1

    async def test_template_changes_are_audited(self, client, session, qc: QcShop) -> None:
        made = await make_template(client, qc.org_id)
        await call(client, qc.org_id, "PATCH", f"/templates/{made['id']}", json={"name": "n"})
        await call(client, qc.org_id, "DELETE", f"/templates/{made['id']}")
        actions = await audit_actions(session)
        assert {"qc_template.created", "qc_template.updated", "qc_template.deleted"} <= set(actions)


# ------------------------------------------------------------------ opening an inspection


class TestOpen:
    async def test_opening_moves_stock_available_to_quality_hold(
        self, client, session, qc: QcShop
    ) -> None:
        body = await open_ok(client, qc, quantity="40")
        assert body["status"] == "PENDING"
        assert body["quantity"] == "40"
        assert body["hold_move_id"] is not None
        assert body["item_sku"] == "WGT-001"
        assert body["location_code"].startswith("WH-SHOP/")
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"]) == (q("60"), q("40"))
        # Moved, not created or destroyed.
        assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("100")
        await clean(session, qc)

    async def test_the_hold_is_audited_evented_and_a_valid_contract(
        self, client, session, qc: QcShop
    ) -> None:
        body = await open_ok(client, qc)
        assert "inspection.created" in await audit_actions(session)
        (event,) = await events(session, "operations.quality.hold")
        assert event.payload["inspection_id"] == body["id"]
        assert event.payload["reason"] == "inspection_opened"
        assert event.payload["move_id"] == body["hold_move_id"]
        assert_valid_event(event)

    async def test_more_than_is_available_is_409_and_changes_nothing(
        self, client, session, qc: QcShop
    ) -> None:
        response = await open_inspection(client, qc, quantity="101")
        assert response.status_code == 409, response.text
        assert response.json()["type"].endswith("insufficient-stock")
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"]) == (q("100"), 0)
        assert (await call(client, qc.org_id, "GET", "/inspections")).json()["total"] == 0
        assert await events(session, "operations.quality.hold") == []

    async def test_reserved_stock_cannot_be_held(self, client, session, qc: QcShop) -> None:
        from app.inventory import ledger
        from platform_core.auth import Scope
        from tests.helpers import ctx_for

        ctx = ctx_for(qc.org_id, "operations.inventory.stock.reserve", Scope.ORG)
        async with session.begin():
            await ledger.reserve(
                session,
                ctx,
                item_id=qc.shop.widget,
                location_id=qc.location_id,
                quantity_milli=q("80"),
            )
        response = await open_inspection(client, qc, quantity="30")
        assert response.status_code == 409, response.text
        ok = await open_inspection(client, qc, quantity="20")
        assert ok.status_code == 201, ok.text

    @pytest.mark.parametrize("quantity", ["0", "-1", "1.5", "abc", "1.2345"])
    async def test_bad_quantities_are_422(self, client, qc: QcShop, quantity: str) -> None:
        response = await open_inspection(client, qc, quantity=quantity)
        assert response.status_code == 422, response.text

    async def test_a_json_number_quantity_is_refused(self, client, qc: QcShop) -> None:
        response = await open_inspection(client, qc, quantity=40)
        assert response.status_code == 422

    async def test_unknown_item_or_location_is_404(self, client, qc: QcShop) -> None:
        bad_item = await open_inspection(client, qc, item=uuid.uuid4())
        assert bad_item.status_code == 404
        bad_loc = await open_inspection(client, qc, location_id=str(uuid.uuid4()))
        assert bad_loc.status_code == 404

    async def test_template_must_match_kind_item_and_be_active(self, client, qc: QcShop) -> None:
        template = await make_template(client, qc.org_id)
        wrong_kind = await open_inspection(client, qc, kind="finished", template_id=template["id"])
        assert wrong_kind.status_code == 422
        scoped = await make_template(client, qc.org_id, code="GIZ", item_id=str(qc.shop.gizmo))
        wrong_item = await open_inspection(client, qc, template_id=scoped["id"])
        assert wrong_item.status_code == 422
        await call(
            client, qc.org_id, "PATCH", f"/templates/{template['id']}", json={"is_active": False}
        )
        inactive = await open_inspection(client, qc, template_id=template["id"])
        assert inactive.status_code == 409
        missing = await open_inspection(client, qc, template_id=str(uuid.uuid4()))
        assert missing.status_code == 404

    async def test_a_retried_open_with_the_same_key_holds_stock_once(
        self, client, session, qc: QcShop
    ) -> None:
        first = await open_inspection(client, qc, quantity="40")
        assert first.status_code == 201
        # Same request, same key, twice more.
        body = {
            "kind": "incoming",
            "item_id": str(qc.shop.widget),
            "location_id": str(qc.location_id),
            "quantity": "25",
        }
        one = await call(client, qc.org_id, "POST", "/inspections", json=body, key="open-1")
        two = await call(client, qc.org_id, "POST", "/inspections", json=body, key="open-1")
        assert one.status_code == two.status_code == 201
        assert one.json()["id"] == two.json()["id"]
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"]) == (q("35"), q("65"))
        clash = await call(
            client, qc.org_id, "POST", "/inspections", json={**body, "quantity": "26"}, key="open-1"
        )
        assert clash.status_code == 409
        assert (await buckets(session, qc))["QUALITY_HOLD"] == q("65")

    async def test_hold_stock_false_needs_the_stock_to_already_be_on_hold(
        self, client, session, qc: QcShop
    ) -> None:
        refused = await open_inspection(client, qc, quantity="10", hold_stock=False)
        assert refused.status_code == 409, refused.text
        await open_ok(client, qc, quantity="30")  # puts 30 on hold, claimed by that inspection
        # Nothing unclaimed is on hold, so a second inspection of held stock is refused too.
        still = await open_inspection(client, qc, quantity="5", hold_stock=False)
        assert still.status_code == 409
        # Stock that arrived on hold by another route (a receipt into QUALITY_HOLD) is inspectable.
        from app.inventory import ledger
        from app.inventory.ledger import MoveLine
        from platform_core.auth import Scope
        from tests.helpers import ctx_for
        from tests.inventory_fixtures import TODAY

        ctx = ctx_for(qc.org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            await ledger.post_move(
                session,
                ctx,
                kind="receipt",
                lines=[MoveLine(qc.shop.widget, qc.location_id, q("12"), status="QUALITY_HOLD")],
                occurred_on=TODAY,
            )
        ok = await open_inspection(client, qc, quantity="12", hold_stock=False)
        assert ok.status_code == 201, ok.text
        assert ok.json()["hold_move_id"] is None
        assert await events(session, "operations.quality.hold") != []
        over = await open_inspection(client, qc, quantity="1", hold_stock=False)
        assert over.status_code == 409
        await clean(session, qc)

    async def test_list_filters_and_detail(self, client, qc: QcShop) -> None:
        a = await open_ok(client, qc, quantity="5")
        await open_ok(client, qc, quantity="6", kind="finished")
        by_kind = await call(client, qc.org_id, "GET", "/inspections", params={"kind": "finished"})
        assert by_kind.json()["total"] == 1
        by_status = await call(
            client, qc.org_id, "GET", "/inspections", params={"status": "PENDING"}
        )
        assert by_status.json()["total"] == 2
        none = await call(client, qc.org_id, "GET", "/inspections", params={"status": "PASSED"})
        assert none.json()["total"] == 0
        bad = await call(client, qc.org_id, "GET", "/inspections", params={"status": "WRONG"})
        assert bad.status_code == 422
        by_item = await call(
            client, qc.org_id, "GET", "/inspections", params={"item_id": str(qc.shop.gizmo)}
        )
        assert by_item.json()["total"] == 0
        one = await call(client, qc.org_id, "GET", f"/inspections/{a['id']}")
        assert one.json()["id"] == a["id"]
        assert one.json()["can_release"] is True
        assert one.json()["suggested_outcome"] == "PENDING"


# ------------------------------------------------------------------ results


class TestResults:
    async def test_the_server_judges_each_reading_and_snapshots_the_bounds(
        self, client, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        response = await record(
            client,
            qc.org_id,
            insp["id"],
            [
                {"param_name": "GSM", "value": "169.999"},
                {"param_name": "Shrinkage", "value": "3.5"},
                {"param_name": "Colour match", "passed": False, "note": "streaks"},
            ],
        )
        assert response.status_code == 200, response.text
        by_name = {r["param_name"]: r for r in response.json()["results"]}
        assert by_name["GSM"]["passed"] is False
        assert by_name["GSM"]["value"] == "169.999"
        assert (by_name["GSM"]["min"], by_name["GSM"]["max"]) == ("170", "190")
        assert by_name["Shrinkage"]["passed"] is True  # the bound is inclusive
        assert by_name["Colour match"]["passed"] is False
        assert response.json()["suggested_outcome"] == "HOLD"
        assert response.json()["can_release"] is True
        # Editing the template later does not change what this inspection was judged against.
        await call(
            client,
            qc.org_id,
            "PATCH",
            f"/templates/{template['id']}",
            json={"params": [{"name": "GSM", "min": "1", "max": "1000"}]},
        )
        again = await call(client, qc.org_id, "GET", f"/inspections/{insp['id']}")
        gsm = next(r for r in again.json()["results"] if r["param_name"] == "GSM")
        assert (gsm["min"], gsm["max"], gsm["passed"]) == ("170", "190", False)

    async def test_a_client_verdict_on_a_numeric_parameter_is_ignored(
        self, client, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        response = await record(
            client,
            qc.org_id,
            insp["id"],
            [{"param_name": "GSM", "value": "300", "passed": True}],
        )
        assert response.json()["results"][0]["passed"] is False

    async def test_a_failed_critical_reading_suggests_failed_and_blocks_release(
        self, client, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        response = await record(
            client,
            qc.org_id,
            insp["id"],
            [{"param_name": "Shrinkage", "value": "9"}],
        )
        assert response.json()["suggested_outcome"] == "FAILED"
        assert response.json()["can_release"] is False

    async def test_re_recording_replaces_by_name(self, client, qc: QcShop) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        await record(client, qc.org_id, insp["id"], [{"param_name": "GSM", "value": "100"}])
        second = await record(
            client, qc.org_id, insp["id"], [{"param_name": "GSM", "value": "180"}]
        )
        results = second.json()["results"]
        assert len(results) == 1
        assert (results[0]["value"], results[0]["passed"]) == ("180", True)

    @pytest.mark.parametrize(
        "results",
        [
            [{"param_name": "Nope", "value": "1"}],
            [{"param_name": "GSM"}],
            [{"param_name": "Colour match"}],
            [{"param_name": "GSM", "value": "1"}, {"param_name": "gsm", "value": "2"}],
            [{"param_name": "GSM", "value": "1.0001"}],
            [{"param_name": "GSM", "value": 180}],
        ],
    )
    async def test_bad_readings_are_422(self, client, qc: QcShop, results: list[dict]) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        response = await record(client, qc.org_id, insp["id"], results)
        assert response.status_code == 422, response.text

    async def test_without_a_template_only_pass_fail_observations_are_allowed(
        self, client, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc)
        numeric = await record(
            client, qc.org_id, insp["id"], [{"param_name": "Smell", "value": "1"}]
        )
        assert numeric.status_code == 422
        ok = await record(client, qc.org_id, insp["id"], [{"param_name": "Smell", "passed": True}])
        assert ok.status_code == 200
        assert ok.json()["results"][0]["param_kind"] == "pass_fail"

    async def test_results_are_refused_once_decided(self, client, qc: QcShop) -> None:
        insp = await open_ok(client, qc)
        await call(client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={})
        response = await record(
            client, qc.org_id, insp["id"], [{"param_name": "x", "passed": True}]
        )
        assert response.status_code == 409


# ------------------------------------------------------------------ release


class TestRelease:
    async def test_release_all_returns_the_stock_to_available(
        self, client, session, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, quantity="40", template_id=template["id"])
        await record(client, qc.org_id, insp["id"], ALL_PASS)
        response = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={"note": "good"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["accepted"], body["rejected"]) == ("PASSED", "40", "0")
        assert body["decision_move_id"] is not None
        assert body["decided_by"] is not None
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"], b["REJECTED"]) == (q("100"), 0, 0)
        await clean(session, qc)
        (event,) = await events(session, "operations.quality.released")
        assert event.payload["accepted_milli"] == q("40")
        assert_valid_event(event)
        assert "inspection.released" in await audit_actions(session)
        assert await events(session, "operations.quality.rejected") == []

    async def test_a_partial_pass_splits_available_and_rejected(
        self, client, session, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"accepted": "25", "rejected_bucket": "QUARANTINE", "note": "some faded"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["accepted"], body["rejected"]) == ("PARTIAL_PASS", "25", "15")
        assert body["rejected_bucket"] == "QUARANTINE"
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"], b["QUARANTINE"]) == (q("85"), 0, q("15"))
        assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("100")
        await clean(session, qc)
        (released,) = await events(session, "operations.quality.released")
        (rejected,) = await events(session, "operations.quality.rejected")
        assert released.payload["accepted_milli"] == q("25")
        assert (rejected.payload["rejected_milli"], rejected.payload["bucket"]) == (
            q("15"),
            "QUARANTINE",
        )
        assert_valid_event(released)
        assert_valid_event(rejected)

    async def test_fractional_quantities_stay_exact_for_a_measured_item(
        self, client, session, qc: QcShop
    ) -> None:
        from app.inventory import ledger
        from app.inventory import services as inv_services
        from app.inventory.ledger import MoveLine
        from platform_core.auth import Scope
        from tests.helpers import ctx_for
        from tests.inventory_fixtures import TODAY

        ctx = ctx_for(qc.org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            cloth = await inv_services.create_inventory_item(
                session,
                ctx,
                sku="CLOTH-M",
                name="Cloth",
                category="raw_materials",
                warehouse="Shop Store",
                uom="m",
                on_hand=0,
            )
            await ledger.post_move(
                session,
                ctx,
                kind="receipt",
                lines=[MoveLine(cloth.id, qc.location_id, q("100.125"))],
                occurred_on=TODAY,
            )
        insp = await open_ok(client, qc, quantity="40.125", item=cloth.id)
        assert insp["quantity"] == "40.125"
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"accepted": "25.5", "note": "n"},
        )
        assert response.status_code == 200, response.text
        assert (response.json()["accepted"], response.json()["rejected"]) == ("25.5", "14.625")
        assert (await balance(session, qc.org_id, cloth.id, qc.location_id))[0] == q("85.5")
        assert (await balance(session, qc.org_id, cloth.id, qc.location_id, status="REJECTED"))[
            0
        ] == q("14.625")
        await clean(session, qc)

    async def test_a_fraction_of_a_discrete_item_is_422(self, client, qc: QcShop) -> None:
        insp = await open_ok(client, qc, quantity="40")
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"accepted": "25.5"},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("accepted", ["41", "0", "-5", "x"])
    async def test_impossible_accepted_quantities_are_422(
        self, client, session, qc: QcShop, accepted: str
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"accepted": accepted},
        )
        assert response.status_code == 422, response.text
        assert (await buckets(session, qc))["QUALITY_HOLD"] == q("40")

    async def test_a_failed_critical_parameter_blocks_release(
        self, client, session, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, quantity="40", template_id=template["id"])
        await record(
            client,
            qc.org_id,
            insp["id"],
            [
                {"param_name": "GSM", "value": "180"},
                {"param_name": "Shrinkage", "value": "9"},
                {"param_name": "Colour match", "passed": True},
            ],
        )
        response = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={}
        )
        assert response.status_code == 409, response.text
        assert (await buckets(session, qc))["QUALITY_HOLD"] == q("40")
        # Rejecting is always open.
        rejected = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/reject",
            json={"reason": "shrinks"},
        )
        assert rejected.status_code == 200

    async def test_a_noncritical_failure_needs_a_note_to_release_anyway(
        self, client, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, quantity="40", template_id=template["id"])
        await record(
            client,
            qc.org_id,
            insp["id"],
            [
                {"param_name": "GSM", "value": "150"},
                {"param_name": "Shrinkage", "value": "1"},
                {"param_name": "Colour match", "passed": True},
            ],
        )
        no_note = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={}
        )
        assert no_note.status_code == 422
        ok = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"note": "customer accepts lighter fabric"},
        )
        assert ok.status_code == 200, ok.text

    async def test_every_template_parameter_needs_a_reading_before_release(
        self, client, qc: QcShop
    ) -> None:
        template = await make_template(client, qc.org_id)
        insp = await open_ok(client, qc, template_id=template["id"])
        await record(client, qc.org_id, insp["id"], [{"param_name": "GSM", "value": "180"}])
        response = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={}
        )
        assert response.status_code == 409
        assert "no recorded result" in response.json()["detail"]

    async def test_a_retried_release_replays_and_posts_once(
        self, client, session, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        path = f"/inspections/{insp['id']}/release"
        one = await call(client, qc.org_id, "POST", path, json={"note": "ok"}, key="rel-1")
        moves_after_first = await _move_count(session)
        two = await call(client, qc.org_id, "POST", path, json={"note": "ok"}, key="rel-1")
        assert one.status_code == two.status_code == 200
        assert one.json() == two.json()
        assert await _move_count(session) == moves_after_first
        assert len(await events(session, "operations.quality.released")) == 1
        different = await call(client, qc.org_id, "POST", path, json={"note": "other"}, key="rel-1")
        assert different.status_code == 409
        no_key = await call(client, qc.org_id, "POST", path, json={"note": "ok"})
        assert no_key.status_code == 409  # already PASSED; only the keyed retry replays
        await clean(session, qc)


async def _move_count(session) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (await session.execute(select(func.count()).select_from(InventoryMove))).scalar_one()
        )


# ------------------------------------------------------------------ reject and disposition


class TestRejectAndDispose:
    @pytest.mark.parametrize("bucket", ["REJECTED", "QUARANTINE"])
    async def test_reject_moves_all_held_stock_to_the_bucket(
        self, client, session, qc: QcShop, bucket: str
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/reject",
            json={"reason": "torn", "bucket": bucket},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["status"], body["accepted"], body["rejected"]) == ("FAILED", "0", "40")
        assert body["rejected_bucket"] == bucket
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"], b[bucket]) == (q("60"), 0, q("40"))
        await clean(session, qc)
        (event,) = await events(session, "operations.quality.rejected")
        assert event.payload["reason"] == "torn"
        assert_valid_event(event)
        assert "inspection.rejected" in await audit_actions(session)

    async def test_a_reason_is_required(self, client, qc: QcShop) -> None:
        insp = await open_ok(client, qc)
        for body in ({}, {"reason": ""}, {"reason": "   "}):
            response = await call(
                client, qc.org_id, "POST", f"/inspections/{insp['id']}/reject", json=body
            )
            assert response.status_code == 422

    async def test_return_to_vendor_removes_the_rejected_stock(
        self, client, session, qc: QcShop
    ) -> None:
        supplier = str(uuid.uuid4())
        insp = await open_ok(client, qc, quantity="40", supplier_id=supplier)
        await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/reject", json={"reason": "bad"}
        )
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/disposition",
            json={"action": "RETURN_TO_VENDOR", "reference": "DN-1042", "note": "collected"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["disposition"], body["disposition_reference"]) == (
            "RETURN_TO_VENDOR",
            "DN-1042",
        )
        assert body["disposition_move_id"] is not None
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["QUALITY_HOLD"], b["REJECTED"]) == (q("60"), 0, 0)
        assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("60")
        await clean(session, qc)
        (event,) = await events(session, "operations.quality.disposed")
        assert event.payload["supplier_id"] == supplier
        assert (event.payload["action"], event.payload["disposed_milli"]) == (
            "RETURN_TO_VENDOR",
            q("40"),
        )
        assert_valid_event(event)
        assert "inspection.disposed" in await audit_actions(session)

    async def test_scrap_after_a_partial_pass_only_scraps_the_rejected_part(
        self, client, session, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/release",
            json={"accepted": "30", "note": "n"},
        )
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/disposition",
            json={"action": "SCRAP"},
        )
        assert response.status_code == 200, response.text
        b = await buckets(session, qc)
        assert (b["AVAILABLE"], b["REJECTED"]) == (q("90"), 0)
        assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("90")
        await clean(session, qc)

    async def test_disposition_is_once_only_and_only_for_rejected_stock(
        self, client, session, qc: QcShop
    ) -> None:
        passed = await open_ok(client, qc, quantity="10")
        await call(client, qc.org_id, "POST", f"/inspections/{passed['id']}/release", json={})
        nothing = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{passed['id']}/disposition",
            json={"action": "SCRAP"},
        )
        assert nothing.status_code == 409
        pending = await open_ok(client, qc, quantity="10")
        early = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{pending['id']}/disposition",
            json={"action": "SCRAP"},
        )
        assert early.status_code == 409
        failed = await open_ok(client, qc, quantity="10")
        await call(
            client, qc.org_id, "POST", f"/inspections/{failed['id']}/reject", json={"reason": "r"}
        )
        path = f"/inspections/{failed['id']}/disposition"
        first = await call(client, qc.org_id, "POST", path, json={"action": "SCRAP"}, key="d-1")
        replay = await call(client, qc.org_id, "POST", path, json={"action": "SCRAP"}, key="d-1")
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json()
        second = await call(client, qc.org_id, "POST", path, json={"action": "RETURN_TO_VENDOR"})
        assert second.status_code == 409
        clash = await call(
            client, qc.org_id, "POST", path, json={"action": "RETURN_TO_VENDOR"}, key="d-1"
        )
        assert clash.status_code == 409
        assert len(await events(session, "operations.quality.disposed")) == 1
        await clean(session, qc)

    async def test_bad_actions_are_422(self, client, qc: QcShop) -> None:
        insp = await open_ok(client, qc)
        response = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{insp['id']}/disposition",
            json={"action": "BURN"},
        )
        assert response.status_code == 422


# ------------------------------------------------------------------ state machine over HTTP


class TestStateMachine:
    async def test_hold_keeps_stock_held_and_can_still_be_decided(
        self, client, session, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        held = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/hold", json={"note": "recheck"}
        )
        assert held.status_code == 200
        assert held.json()["status"] == "HOLD"
        assert (await buckets(session, qc))["QUALITY_HOLD"] == q("40")
        reasons = {e.payload["reason"] for e in await events(session, "operations.quality.hold")}
        assert reasons == {"inspection_opened", "decision_hold"}
        again = await call(client, qc.org_id, "POST", f"/inspections/{insp['id']}/hold", json={})
        assert again.status_code == 409  # HOLD -> HOLD is not a transition
        # Readings can still be taken on HOLD, and it can be released afterwards.
        ok = await record(client, qc.org_id, insp["id"], [{"param_name": "Looks", "passed": True}])
        assert ok.status_code == 200
        released = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={}
        )
        assert released.json()["status"] == "PASSED"

    async def test_rework_then_reinspect_clears_readings_and_returns_to_pending(
        self, client, session, qc: QcShop
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        await record(client, qc.org_id, insp["id"], [{"param_name": "Looks", "passed": False}])
        reworked = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/rework", json={"note": "re-dye"}
        )
        assert reworked.json()["status"] == "REWORK"
        assert (await buckets(session, qc))["QUALITY_HOLD"] == q("40")
        # A reworked inspection cannot be released or have readings taken until re-inspected.
        assert (
            await call(client, qc.org_id, "POST", f"/inspections/{insp['id']}/release", json={})
        ).status_code == 409
        assert (
            await record(client, qc.org_id, insp["id"], [{"param_name": "Looks", "passed": True}])
        ).status_code == 409
        back = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/reinspect", json={}
        )
        assert back.json()["status"] == "PENDING"
        assert back.json()["results"] == []
        # ...and a REWORK can also be abandoned as a failure.
        other = await open_ok(client, qc, quantity="10")
        await call(client, qc.org_id, "POST", f"/inspections/{other['id']}/rework", json={})
        failed = await call(
            client,
            qc.org_id,
            "POST",
            f"/inspections/{other['id']}/reject",
            json={"reason": "unfixable"},
        )
        assert failed.status_code == 200, failed.text  # REWORK -> FAILED is an allowed exit
        assert failed.json()["status"] == "FAILED"
        await clean(session, qc)

    async def test_pending_cannot_be_reinspected(self, client, qc: QcShop) -> None:
        insp = await open_ok(client, qc)
        response = await call(
            client, qc.org_id, "POST", f"/inspections/{insp['id']}/reinspect", json={}
        )
        assert response.status_code == 409

    @pytest.mark.parametrize("final", ["release", "reject"])
    async def test_a_decided_inspection_accepts_no_further_decision(
        self, client, session, qc: QcShop, final: str
    ) -> None:
        insp = await open_ok(client, qc, quantity="40")
        first_body = {} if final == "release" else {"reason": "r"}
        assert (
            await call(
                client, qc.org_id, "POST", f"/inspections/{insp['id']}/{final}", json=first_body
            )
        ).status_code == 200
        before = await buckets(session, qc)
        attempts = [
            ("release", {}),
            ("reject", {"reason": "again"}),
            ("hold", {}),
            ("rework", {}),
            ("reinspect", {}),
        ]
        for action, body in attempts:
            response = await call(
                client, qc.org_id, "POST", f"/inspections/{insp['id']}/{action}", json=body
            )
            assert response.status_code == 409, (action, response.text)
        assert await buckets(session, qc) == before
        await clean(session, qc)

    async def test_an_unknown_inspection_is_404(self, client, qc: QcShop) -> None:
        response = await call(
            client, qc.org_id, "POST", f"/inspections/{uuid.uuid4()}/release", json={}
        )
        assert response.status_code == 404


async def test_the_whole_journey_leaves_ledger_and_balances_in_agreement(
    client, session, qc: QcShop
) -> None:
    """Hold, partial release, reject, dispose, across two items — then the reconciler."""
    template = await make_template(client, qc.org_id)
    a = await open_ok(client, qc, quantity="40", template_id=template["id"])
    await record(client, qc.org_id, a["id"], ALL_PASS)
    await call(
        client,
        qc.org_id,
        "POST",
        f"/inspections/{a['id']}/release",
        json={"accepted": "30", "note": "n"},
    )
    await call(
        client,
        qc.org_id,
        "POST",
        f"/inspections/{a['id']}/disposition",
        json={"action": "RETURN_TO_VENDOR"},
    )
    b = await open_ok(client, qc, quantity="20", item=qc.shop.gizmo)
    await call(client, qc.org_id, "POST", f"/inspections/{b['id']}/reject", json={"reason": "r"})
    await call(
        client, qc.org_id, "POST", f"/inspections/{b['id']}/disposition", json={"action": "SCRAP"}
    )
    assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("90")
    assert await ledger_total(session, qc.org_id, qc.shop.gizmo) == q("80")
    async with session.begin():
        entries = (
            await session.execute(select(func.count()).select_from(LedgerEntry))
        ).scalar_one()
    assert entries > 0
    await clean(session, qc)
