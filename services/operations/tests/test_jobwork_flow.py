# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""The job-work flow over HTTP (Inventory plan Phase 6): draft → approve → issue → receive (in
parts) → reconcile → close, against the real ledger. Exact quantity maths, the challan, the e-way
hook, the QC hand-off, idempotency, the reconciler's verdict, and what close refuses."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA, AuditLog, OutboxEvent
from tests.jobwork_fixtures import (
    Mill,
    approve,
    balance_milli,
    build_mill,
    call,
    create_order,
    issue,
    issued_order,
    key,
    order_body,
    receipt_line,
    receive,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


async def events(session, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
            .scalars()
            .all()
        )


async def assert_reconciler_clean(session, org_id) -> None:
    async with session.begin():
        result = await reconcile_organization(session, org_id, run_id=uuid.uuid4())
    assert result.findings == [], result.findings


class TestCreateAndApprove:
    async def test_a_draft_moves_no_stock_and_gets_a_number(self, client, session, mill) -> None:
        order = await create_order(client, mill)
        assert order["status"] == "DRAFT"
        assert order["number"].startswith(f"JW-{date.today().year}-")
        assert order["materials"][0]["quantity"] == "82.5"
        assert order["materials"][0]["sent"] == "0"
        assert order["materials"][0]["location_id"] == str(mill.home)  # the item's home bin
        assert await balance_milli(session, mill.fabric, mill.home, "AVAILABLE") == 100_000
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 0

    async def test_numbers_are_sequential_per_organization(self, client, mill) -> None:
        first = await create_order(client, mill)
        second = await create_order(client, mill)
        assert (
            int(second["number"].rsplit("-", 1)[-1]) == int(first["number"].rsplit("-", 1)[-1]) + 1
        )

    async def test_create_is_idempotent_and_refuses_a_reused_key_with_a_different_body(
        self, client, mill
    ) -> None:
        idem = key()
        body = order_body(mill)
        first = await call(client, mill.org_id, "POST", "/orders", json=body, idem=idem)
        replay = await call(client, mill.org_id, "POST", "/orders", json=body, idem=idem)
        assert first.status_code == 201
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        listed = await call(client, mill.org_id, "GET", "/orders")
        assert listed.json()["total"] == 1
        other = order_body(mill, process="Printing")
        clash = await call(client, mill.org_id, "POST", "/orders", json=other, idem=idem)
        assert clash.status_code == 409

    async def test_idempotency_key_is_required_on_create(self, client, mill) -> None:
        response = await call(client, mill.org_id, "POST", "/orders", json=order_body(mill))
        assert response.status_code == 422

    @pytest.mark.parametrize("quantity", ["0", "-1", "1.0005", "abc", "1e3"])
    async def test_bad_quantities_are_refused(self, client, mill, quantity) -> None:
        body = order_body(mill, materials=[{"item_id": str(mill.fabric), "quantity": quantity}])
        response = await call(client, mill.org_id, "POST", "/orders", json=body, idem=key())
        assert response.status_code == 422, response.text

    async def test_a_discrete_unit_refuses_a_fraction(self, client, mill) -> None:
        body = order_body(mill, materials=[{"item_id": str(mill.thread), "quantity": "2.5"}])
        response = await call(client, mill.org_id, "POST", "/orders", json=body, idem=key())
        assert response.status_code == 422

    async def test_a_float_quantity_is_refused(self, client, mill) -> None:
        body = order_body(mill, materials=[{"item_id": str(mill.fabric), "quantity": 82.5}])
        response = await call(client, mill.org_id, "POST", "/orders", json=body, idem=key())
        assert response.status_code == 422

    async def test_the_same_bucket_twice_is_refused(self, client, mill) -> None:
        line = {"item_id": str(mill.fabric), "quantity": "1"}
        body = order_body(mill, materials=[line, line])
        response = await call(client, mill.org_id, "POST", "/orders", json=body, idem=key())
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "field", ["supplier_id", "item_id", "location_id", "receive_location_id", "warehouse"]
    )
    async def test_references_outside_the_organization_are_404(self, client, mill, field) -> None:
        ghost = str(uuid.uuid4())
        body = order_body(mill)
        if field == "supplier_id":
            body["supplier_id"] = ghost
        elif field == "item_id":
            body["materials"][0]["item_id"] = ghost
        elif field == "location_id":
            body["materials"][0]["location_id"] = ghost
        elif field == "receive_location_id":
            body["receive_location_id"] = ghost
        else:
            body["source_warehouse_id"] = ghost
        response = await call(client, mill.org_id, "POST", "/orders", json=body, idem=key())
        assert response.status_code == 404, response.text

    async def test_draft_header_edit_checks_the_version(self, client, mill) -> None:
        order = await create_order(client, mill)
        ok = await call(
            client,
            mill.org_id,
            "PATCH",
            f"/orders/{order['id']}",
            json={"expected_version": 1, "process": "Bleaching", "qc_required": True},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["process"] == "Bleaching"
        assert ok.json()["qc_required"] is True
        assert ok.json()["version"] == 2
        stale = await call(
            client,
            mill.org_id,
            "PATCH",
            f"/orders/{order['id']}",
            json={"expected_version": 1, "process": "x"},
        )
        assert stale.status_code == 409

    async def test_an_approved_order_can_no_longer_be_edited(self, client, mill) -> None:
        order = await create_order(client, mill)
        approved = await approve(client, mill, order["id"])
        response = await call(
            client,
            mill.org_id,
            "PATCH",
            f"/orders/{order['id']}",
            json={"expected_version": approved["version"], "process": "x"},
        )
        assert response.status_code == 409

    async def test_approve_is_idempotent_by_key(self, client, mill) -> None:
        order = await create_order(client, mill)
        idem = key()
        one = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:approve", idem=idem)
        two = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:approve", idem=idem)
        assert one.status_code == two.status_code == 200
        assert one.json()["version"] == two.json()["version"]
        # without the key, the second approve is a plain state conflict
        three = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:approve")
        assert three.status_code == 409
        # the key is bound to the action it produced
        wrong = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:cancel", idem=idem)
        assert wrong.status_code == 409

    async def test_cancel_a_draft_and_nothing_after(self, client, mill) -> None:
        order = await create_order(client, mill)
        cancelled = await call(
            client,
            mill.org_id,
            "POST",
            f"/orders/{order['id']}:cancel",
            json={"reason": "wrong vendor"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        again = await approve_expect(client, mill, order["id"])
        assert again == 409

    async def test_an_issued_order_cannot_be_cancelled(self, client, mill) -> None:
        order = await issued_order(client, mill)
        response = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:cancel")
        assert response.status_code == 409


async def approve_expect(client, mill: Mill, order_id: str) -> int:  # type: ignore[no-untyped-def]
    return (await call(client, mill.org_id, "POST", f"/orders/{order_id}:approve")).status_code


class TestIssue:
    async def test_issue_moves_stock_to_the_vendor_bucket_and_cuts_a_challan(
        self, client, session, mill
    ) -> None:
        order = await issued_order(
            client,
            mill,
            materials=[
                {"item_id": str(mill.fabric), "quantity": "82.5"},
                {"item_id": str(mill.thread), "quantity": "20"},
            ],
        )
        assert order["status"] == "ISSUED"
        assert order["issued_at"] is not None
        assert [m["sent"] for m in order["materials"]] == ["82.5", "20"]
        assert await balance_milli(session, mill.fabric, mill.home, "AVAILABLE") == 17_500
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 82_500
        assert await balance_milli(session, mill.thread, mill.home, "AVAILABLE") == 180_000
        assert await balance_milli(session, mill.thread, mill.home, "AT_JOB_WORKER") == 20_000

        [challan] = order["challans"]
        assert challan["challan_number"].startswith("DC/")
        assert len(challan["challan_number"]) <= 16
        assert challan["supplier_name"] == "Rainbow Dyers"
        assert challan["total_value_minor"] == 82_500 * 400_00 // 1000 + 20 * 50_00
        assert challan["currency"] == "INR"
        assert challan["lines"][0]["quantity"] == "82.5"
        assert challan["lines"][0]["value_minor"] == 33_000_00
        await assert_reconciler_clean(session, mill.org_id)

    async def test_the_ledger_rows_name_the_order(self, client, session, mill) -> None:
        order = await issued_order(client, mill)
        async with session.begin():
            rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT m.kind, m.reference_type, l.status, l.quantity_milli
                        FROM {SCHEMA}.inventory_ledger l
                        JOIN {SCHEMA}.inventory_moves m ON m.id = l.move_id
                        WHERE m.reference_id = :o ORDER BY l.quantity_milli
                        """
                    ),
                    {"o": uuid.UUID(order["id"])},
                )
            ).all()
        assert [(r.kind, r.reference_type, r.status, r.quantity_milli) for r in rows] == [
            ("relocation", "job_work_order", "AVAILABLE", -82_500),
            ("relocation", "job_work_order", "AT_JOB_WORKER", 82_500),
        ]

    async def test_only_an_approved_order_is_issued(self, client, mill) -> None:
        draft = await create_order(client, mill)
        response = await call(
            client, mill.org_id, "POST", f"/orders/{draft['id']}:issue", json={}, idem=key()
        )
        assert response.status_code == 409

    async def test_issue_refuses_more_than_is_available_with_the_numbers(
        self, client, session, mill
    ) -> None:
        order = await create_order(
            client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "100.001"}]
        )
        await approve(client, mill, order["id"])
        response = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:issue", json={}, idem=key()
        )
        assert response.status_code == 409
        assert "insufficient-stock" in response.json()["type"]
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 0

    async def test_a_failed_issue_leaves_the_order_approved_and_numbers_unconsumed(
        self, client, session, mill
    ) -> None:
        bad = await create_order(
            client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "500"}]
        )
        await approve(client, mill, bad["id"])
        failed = await call(
            client, mill.org_id, "POST", f"/orders/{bad['id']}:issue", json={}, idem=key()
        )
        assert failed.status_code == 409
        after = await call(client, mill.org_id, "GET", f"/orders/{bad['id']}")
        assert after.json()["status"] == "APPROVED"
        assert after.json()["challans"] == []

    async def test_stock_reserved_for_something_else_cannot_be_sent(
        self, client, session, mill
    ) -> None:
        from app.inventory import ledger
        from platform_core.auth import Scope
        from tests.helpers import ctx_for

        ctx = ctx_for(mill.org_id, "operations.inventory.stock.reserve", Scope.ORG)
        async with session.begin():
            await ledger.reserve(
                session,
                ctx,
                item_id=mill.fabric,
                location_id=mill.home,
                quantity_milli=90_000,
                reference_type="order",
                reference_id=uuid.uuid4(),
            )
        order = await create_order(client, mill)  # 82.5 of the 10 that are unreserved
        await approve(client, mill, order["id"])
        response = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:issue", json={}, idem=key()
        )
        assert response.status_code == 409
        assert "insufficient-stock" in response.json()["type"]

    async def test_issue_replays_by_key_without_a_second_move_or_challan(
        self, client, session, mill
    ) -> None:
        order = await create_order(client, mill)
        await approve(client, mill, order["id"])
        idem = key()
        body = {"vehicle_no": "MH12AB1234", "distance_km": 120}
        one = await issue(client, mill, order["id"], body=body, idem=idem)
        two = await issue(client, mill, order["id"], body=body, idem=idem)
        assert one["challans"][0]["id"] == two["challans"][0]["id"]
        assert len(two["challans"]) == 1
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 82_500
        changed = await call(
            client,
            mill.org_id,
            "POST",
            f"/orders/{order['id']}:issue",
            json={"vehicle_no": "OTHER"},
            idem=idem,
        )
        assert changed.status_code == 409
        # a fresh key on an already-issued order is a plain state conflict
        again = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:issue", json={}, idem=key()
        )
        assert again.status_code == 409

    async def test_issue_needs_a_key(self, client, mill) -> None:
        order = await create_order(client, mill)
        await approve(client, mill, order["id"])
        response = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:issue", json={})
        assert response.status_code == 422

    async def test_a_vendor_on_hold_cannot_be_sent_material(self, client, session, org_id) -> None:
        held = await build_mill(session, org_id, on_hold=True)
        order = await create_order(client, held)
        await approve(client, held, order["id"])
        response = await call(
            client, org_id, "POST", f"/orders/{order['id']}:issue", json={}, idem=key()
        )
        assert response.status_code == 409
        assert "on hold" in response.json()["detail"]

    async def test_challan_numbers_are_gapless_and_in_order(self, client, mill) -> None:
        numbers = []
        for _ in range(3):
            order = await issued_order(
                client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "1"}]
            )
            numbers.append(order["challans"][0]["challan_number"])
        sequence = [int(n.rsplit("/", 1)[-1]) for n in numbers]
        assert sequence == [1, 2, 3]
        assert len({n.rsplit("/", 1)[0] for n in numbers}) == 1  # one financial-year series

    async def test_issue_audits_and_emits_after_commit_in_the_same_transaction(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill, due_date=str(date.today() + timedelta(days=7)))
        [issued] = await events(session, "operations.jobwork.issued")
        payload = issued.payload
        assert payload["order_number"] == order["number"]
        assert payload["challan_number"] == order["challans"][0]["challan_number"]
        assert payload["lines"][0]["quantity"] == "82.5"
        async with session.begin():
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.entity_id == uuid.UUID(order["id"]))
                    )
                )
                .scalars()
                .all()
            )
        assert {"jobwork.order.created", "jobwork.order.approved", "jobwork.order.issued"} <= set(
            actions
        )


class TestChallanAndEway:
    async def test_below_the_threshold_no_eway_bill_is_requested(
        self, client, session, mill
    ) -> None:
        order = await issued_order(
            client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "10"}]
        )  # Rs 4,000
        assert order["challans"][0]["eway_status"] == "NOT_REQUIRED"
        assert await events(session, "operations.jobwork.challan.eway_requested") == []

    async def test_exactly_at_the_threshold_is_not_above_it(self, client, mill) -> None:
        order = await create_order(
            client,
            mill,
            materials=[
                {"item_id": str(mill.fabric), "quantity": "100"},  # Rs 40,000
                {"item_id": str(mill.thread), "quantity": "200"},  # Rs 10,000
            ],
        )
        await approve(client, mill, order["id"])
        issued = await issue(
            client, mill, order["id"], body={"vehicle_no": "MH12AB1234", "distance_km": 80}
        )
        assert issued["challans"][0]["total_value_minor"] == 50_000_00
        assert issued["challans"][0]["eway_status"] == "NOT_REQUIRED"

    async def test_one_paisa_over_the_threshold_requests_an_eway_bill(
        self, client, session, org_id
    ) -> None:
        from app.inventory import ledger
        from app.inventory import services as inv_services
        from app.inventory.ledger import MoveLine
        from platform_core.auth import Scope
        from tests.helpers import ctx_for

        mill = await build_mill(session, org_id)
        ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            pricey = await inv_services.create_inventory_item(
                session,
                ctx,
                sku="SILK-1",
                name="Silk",
                category="raw_materials",
                warehouse="Shop Store",
                uom="m",
                unit_value_minor=50_000_01,
            )
            await ledger.post_move(
                session,
                ctx,
                kind="opening",
                lines=[
                    MoveLine(
                        item_id=pricey.id, location_id=pricey.home_location_id, quantity_milli=1_000
                    )
                ],
                occurred_on=date.today(),
                reference_type="test_seed",
                reference_id=uuid.uuid4(),
            )
        order = await create_order(
            client, mill, materials=[{"item_id": str(pricey.id), "quantity": "1"}]
        )
        await approve(client, mill, order["id"])
        issued = await issue(
            client,
            mill,
            order["id"],
            body={
                "vehicle_no": "MH12AB1234",
                "transporter_id": "27AAAAA0000A1Z5",
                "distance_km": 90,
            },
        )
        challan = issued["challans"][0]
        assert challan["total_value_minor"] == 50_000_01
        assert challan["eway_status"] == "REQUESTED"
        [event] = await events(session, "operations.jobwork.challan.eway_requested")
        assert event.payload["challan_number"] == challan["challan_number"]
        assert event.payload["consignment_value_minor"] == 50_000_01
        assert event.payload["distance_km"] == 90
        assert event.payload["supply_reason"] == "job_work"

    async def test_transport_facts_can_be_added_and_an_eway_bill_requested_later(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        challan_id = order["challans"][0]["id"]
        response = await call(
            client,
            mill.org_id,
            "POST",
            f"/challans/{challan_id}:request-eway",
            json={"distance_km": 250, "vehicle_no": "KA01XY9999"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["eway_status"] == "REQUESTED"
        assert body["distance_km"] == 250
        assert body["challan_number"] == order["challans"][0]["challan_number"]
        assert len(await events(session, "operations.jobwork.challan.eway_requested")) == 1

    async def test_the_challan_read_returns_the_frozen_document(self, client, mill) -> None:
        order = await issued_order(client, mill)
        challan_id = order["challans"][0]["id"]
        response = await call(client, mill.org_id, "GET", f"/challans/{challan_id}")
        assert response.status_code == 200
        assert response.json()["order_number"] == order["number"]
        assert response.json()["lines"] == order["challans"][0]["lines"]

    async def test_the_database_refuses_to_change_or_delete_an_issued_challan(
        self, client, session, admin_session, mill
    ) -> None:
        """Immutable below the application: even a BYPASSRLS admin cannot rewrite a challan's
        number, goods or value — only the e-way facts move."""
        order = await issued_order(client, mill)
        challan_id = uuid.UUID(order["challans"][0]["id"])
        for assignment in (
            "challan_number = 'DC/99-00/0001'",
            "total_value_minor = 1",
            "supplier_name = 'Somebody Else'",
            "lines = '[]'::jsonb",
        ):
            with pytest.raises(Exception, match="immutable"):
                await admin_session.execute(
                    text(f"UPDATE {SCHEMA}.challans SET {assignment} WHERE id = :i"),
                    {"i": challan_id},
                )
            await admin_session.rollback()
        with pytest.raises(Exception, match="cannot be deleted"):
            await admin_session.execute(
                text(f"DELETE FROM {SCHEMA}.challans WHERE id = :i"),
                {"i": challan_id},
            )
        await admin_session.rollback()
        await admin_session.execute(
            text(
                f"UPDATE {SCHEMA}.challans SET eway_status = 'GENERATED', "
                "eway_bill_no = '1' WHERE id = :i"
            ),
            {"i": challan_id},
        )
        await admin_session.commit()
        again = await call(
            client,
            mill.org_id,
            "POST",
            f"/challans/{challan_id}:request-eway",
            json={"distance_km": 5},
        )
        assert again.status_code == 409  # a generated bill is not re-requested


class TestReceive:
    async def test_partial_receipts_do_exact_arithmetic_and_balance_the_ledger(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)  # 82.5 m out
        material = order["materials"][0]["id"]

        first = await receive(
            client,
            mill,
            order["id"],
            [receipt_line(material, received="30.125", rejected="2.375")],
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "PARTIALLY_RECEIVED"
        m = body["materials"][0]
        assert (m["received"], m["rejected"], m["shortage"], m["outstanding"]) == (
            "30.125",
            "2.375",
            "0",
            "50",
        )
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 30_125
        assert await balance_milli(session, mill.fabric, mill.recv, "REJECTED") == 2_375
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 50_000
        assert body["reconciliation"]["documents_settled"] is False
        assert body["reconciliation"]["ledger_consistent"] is True

        second = await receive(
            client,
            mill,
            order["id"],
            [receipt_line(material, received="45.5", shortage="4.5")],
        )
        assert second.status_code == 200, second.text
        done = second.json()
        assert done["status"] == "RECEIVED"
        rec = done["reconciliation"]
        assert (
            rec["total_sent"],
            rec["total_received"],
            rec["total_rejected"],
            rec["total_shortage"],
            rec["total_outstanding"],
        ) == ("82.5", "75.625", "2.375", "4.5", "0")
        assert rec["balanced"] is True
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 75_625
        assert await balance_milli(session, mill.fabric, mill.recv, "REJECTED") == 2_375
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 0
        assert await balance_milli(session, mill.fabric, mill.home, "AVAILABLE") == 17_500
        assert len(done["receipts"]) == 2
        assert done["receipts"][0]["receipt_no"] != done["receipts"][1]["receipt_no"]
        await assert_reconciler_clean(session, mill.org_id)

    async def test_the_last_milli_unit_is_accounted_for_exactly(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        one = await receive(client, mill, order["id"], [receipt_line(material, received="82.499")])
        assert one.json()["materials"][0]["outstanding"] == "0.001"
        assert one.json()["status"] == "PARTIALLY_RECEIVED"
        over = await receive(client, mill, order["id"], [receipt_line(material, received="0.002")])
        assert over.status_code == 409
        last = await receive(client, mill, order["id"], [receipt_line(material, rejected="0.001")])
        assert last.json()["status"] == "RECEIVED"
        assert await balance_milli(session, mill.fabric, mill.recv, "REJECTED") == 1

    async def test_receiving_more_than_is_outstanding_is_refused_with_the_numbers(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        response = await receive(
            client, mill, order["id"], [receipt_line(material, received="50", rejected="32.501")]
        )
        assert response.status_code == 409
        error = response.json()["errors"][0]
        assert error["requested"] == "82.501"
        assert error["outstanding"] == "82.5"
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 0
        assert await balance_milli(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 82_500
        after = await call(client, mill.org_id, "GET", f"/orders/{order['id']}")
        assert after.json()["receipts"] == []
        assert after.json()["status"] == "ISSUED"

    async def test_an_all_zero_line_is_refused(self, client, mill) -> None:
        order = await issued_order(client, mill)
        response = await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"])]
        )
        assert response.status_code == 422

    async def test_the_same_material_twice_in_one_receipt_is_refused(self, client, mill) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        response = await receive(
            client,
            mill,
            order["id"],
            [receipt_line(material, received="1"), receipt_line(material, received="1")],
        )
        assert response.status_code == 422

    async def test_a_material_from_another_order_is_404(self, client, mill) -> None:
        one = await issued_order(
            client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "5"}]
        )
        two = await issued_order(
            client, mill, materials=[{"item_id": str(mill.fabric), "quantity": "5"}]
        )
        response = await receive(
            client,
            mill,
            one["id"],
            [receipt_line(two["materials"][0]["id"], received="1")],
        )
        assert response.status_code == 404

    async def test_only_an_order_at_the_vendor_can_receive(self, client, mill) -> None:
        draft = await create_order(client, mill)
        response = await receive(
            client, mill, draft["id"], [receipt_line(draft["materials"][0]["id"], received="1")]
        )
        assert response.status_code == 409

    async def test_returned_goods_can_go_to_a_line_specific_location(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        response = await receive(
            client,
            mill,
            order["id"],
            [receipt_line(order["materials"][0]["id"], received="10", location_id=str(mill.home))],
        )
        assert response.status_code == 200, response.text
        assert await balance_milli(session, mill.fabric, mill.home, "AVAILABLE") == 27_500
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 0

    async def test_receive_replays_by_key_without_receiving_twice(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        line = receipt_line(order["materials"][0]["id"], received="10", rejected="1")
        idem = key()
        one = await receive(client, mill, order["id"], [line], idem=idem)
        two = await receive(client, mill, order["id"], [line], idem=idem)
        assert one.status_code == two.status_code == 200
        assert len(two.json()["receipts"]) == 1
        assert two.json()["materials"][0]["received"] == "10"
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 10_000
        different = receipt_line(order["materials"][0]["id"], received="11")
        clash = await receive(client, mill, order["id"], [different], idem=idem)
        assert clash.status_code == 409

    async def test_receive_needs_a_key(self, client, mill) -> None:
        order = await issued_order(client, mill)
        response = await call(
            client,
            mill.org_id,
            "POST",
            f"/orders/{order['id']}:receive",
            json={"lines": [receipt_line(order["materials"][0]["id"], received="1")]},
        )
        assert response.status_code == 422

    async def test_two_materials_landing_in_one_bucket_are_merged_into_one_kernel_line(
        self, client, session, mill
    ) -> None:
        """Two roll-less lines of one item from two bins both returning to one bin: the kernel
        touches a bucket once per move, so the receipt has to add them up first."""
        order = await create_order(
            client,
            mill,
            materials=[
                {"item_id": str(mill.fabric), "quantity": "10", "location_id": str(mill.home)},
                {"item_id": str(mill.fabric), "quantity": "5", "location_id": str(mill.recv)},
            ],
        )
        # the second line needs stock in the receive bin to be issued from
        from app.inventory import ledger
        from app.inventory.ledger import MoveLine
        from platform_core.auth import Scope
        from tests.helpers import ctx_for

        ctx = ctx_for(mill.org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            await ledger.post_move(
                session,
                ctx,
                kind="opening",
                lines=[MoveLine(item_id=mill.fabric, location_id=mill.recv, quantity_milli=5_000)],
                occurred_on=date.today(),
                reference_type="test_seed",
                reference_id=uuid.uuid4(),
            )
        await approve(client, mill, order["id"])
        issued = await issue(client, mill, order["id"])
        lines = [
            receipt_line(m["id"], received=m["quantity"], location_id=str(mill.home))
            for m in issued["materials"]
        ]
        response = await receive(client, mill, order["id"], lines)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "RECEIVED"
        assert await balance_milli(session, mill.fabric, mill.home, "AVAILABLE") == 90_000 + 15_000
        await assert_reconciler_clean(session, mill.org_id)

    async def test_receive_audits_and_emits_received(self, client, session, mill) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        response = await receive(
            client,
            mill,
            order["id"],
            [receipt_line(material, received="80", shortage="2.5")],
            note="two bales short",
        )
        assert response.status_code == 200
        [event] = await events(session, "operations.jobwork.received")
        assert event.payload["fully_received"] is True
        assert event.payload["outstanding"] == "0"
        assert event.payload["lines"][0]["shortage"] == "2.5"
        assert event.payload["receipt_no"] == response.json()["receipts"][0]["receipt_no"]
        async with session.begin():
            audit = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "jobwork.order.received")
                    )
                )
                .scalars()
                .all()
            )
        assert len(audit) == 1
        assert audit[0].reason == "two bales short"

    async def test_a_shortage_is_written_off_the_vendor_bucket_as_a_lost_adjustment(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        await receive(
            client,
            mill,
            order["id"],
            [receipt_line(order["materials"][0]["id"], received="80", shortage="2.5")],
        )
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        f"""
                        SELECT m.kind, m.reason, l.quantity_milli FROM {SCHEMA}.inventory_ledger l
                        JOIN {SCHEMA}.inventory_moves m ON m.id = l.move_id
                        WHERE m.kind = 'adjustment' AND m.reference_id = :o
                        """
                    ),
                    {"o": uuid.UUID(order["id"])},
                )
            ).one()
        assert (row.kind, row.reason, row.quantity_milli) == ("adjustment", "lost", -2_500)


class TestQcHandoff:
    async def test_returned_goods_go_to_quality_hold_and_an_event_is_emitted(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill, qc_required=True)
        material = order["materials"][0]["id"]
        response = await receive(
            client, mill, order["id"], [receipt_line(material, received="70", rejected="2.5")]
        )
        assert response.status_code == 200, response.text
        assert await balance_milli(session, mill.fabric, mill.recv, "QUALITY_HOLD") == 70_000
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 0
        assert await balance_milli(session, mill.fabric, mill.recv, "REJECTED") == 2_500
        receipt = response.json()["receipts"][0]
        assert receipt["good_status"] == "QUALITY_HOLD"
        assert receipt["qc_handoff"] is True
        [event] = await events(session, "operations.jobwork.receipt.qc_requested")
        assert event.payload["source"] == "job_work"
        assert event.payload["lines"][0]["received"] == "70"
        assert event.payload["lines"][0]["status"] == "QUALITY_HOLD"
        await assert_reconciler_clean(session, mill.org_id)

    async def test_without_qc_the_goods_are_available_and_no_handoff_is_emitted(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], received="70")]
        )
        assert await events(session, "operations.jobwork.receipt.qc_requested") == []
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 70_000

    async def test_a_pure_shortage_has_nothing_to_inspect(self, client, session, mill) -> None:
        order = await issued_order(client, mill, qc_required=True)
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], shortage="1")]
        )
        assert await events(session, "operations.jobwork.receipt.qc_requested") == []


class TestReconcileAndClose:
    async def test_close_is_refused_while_material_is_outstanding(self, client, mill) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        for received in (None, "30"):
            if received:
                await receive(
                    client, mill, order["id"], [receipt_line(material, received=received)]
                )
            response = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close")
            assert response.status_code == 409, response.text
            assert "reconciliation" in response.json()["type"]
            error = response.json()["errors"][0]
            assert error["documents_settled"] is False
            assert error["total_sent"] == "82.5"
            assert error["total_outstanding"] == ("82.5" if received is None else "52.5")
        after = await call(client, mill.org_id, "GET", f"/orders/{order['id']}")
        assert after.json()["status"] == "PARTIALLY_RECEIVED"
        assert after.json()["closed_at"] is None

    async def test_close_is_refused_when_the_ledger_disagrees_with_settled_documents(
        self, client, session, admin_session, mill
    ) -> None:
        """Documents say settled; the ledger says 1 milli is still at the vendor. The status is
        only a summary — close recomputes from the ledger and refuses."""
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        await receive(client, mill, order["id"], [receipt_line(material, received="82.5")])
        # tamper: an extra AT_JOB_WORKER ledger row under this order, bypassing every API
        move_id = uuid.uuid4()
        await admin_session.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_moves
                    (id, organization_id, kind, reference_type, reference_id, note, occurred_on)
                VALUES (:m, :o, 'adjustment', 'job_work_order', :r, 'tamper', current_date)
                """
            ),
            {"m": move_id, "o": mill.org_id, "r": uuid.UUID(order["id"])},
        )
        await admin_session.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_ledger
                    (id, organization_id, move_id, inventory_item_id, location_id, status,
                     quantity_milli, balance_after_milli)
                VALUES (gen_random_uuid(), :o, :m, :i, :l, 'AT_JOB_WORKER', 1, 1)
                """
            ),
            {"m": move_id, "o": mill.org_id, "i": mill.fabric, "l": mill.home},
        )
        await admin_session.commit()

        response = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close")
        assert response.status_code == 409, response.text
        error = response.json()["errors"][0]
        assert error["documents_settled"] is True
        assert error["ledger_consistent"] is False
        assert error["drift"] == [
            {"item_id": str(mill.fabric), "documents": "0", "ledger": "0.001"}
        ]
        view = await call(client, mill.org_id, "GET", f"/orders/{order['id']}/reconciliation")
        assert view.json()["balanced"] is False
        assert view.json()["drift"][0]["sku"] == "FAB-GREIGE"

    async def test_close_succeeds_once_everything_is_accounted_for(
        self, client, session, mill
    ) -> None:
        order = await issued_order(client, mill)
        material = order["materials"][0]["id"]
        await receive(
            client,
            mill,
            order["id"],
            [receipt_line(material, received="70", rejected="10", shortage="2.5")],
        )
        view = await call(client, mill.org_id, "GET", f"/orders/{order['id']}/reconciliation")
        assert view.json()["balanced"] is True
        response = await call(
            client, mill.org_id, "POST", f"/orders/{order['id']}:close", json={"reason": "settled"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CLOSED"
        assert response.json()["closed_at"] is not None
        [event] = await events(session, "operations.jobwork.closed")
        assert event.payload["total_shortage"] == "2.5"
        # closed is final: no more receipts, no second close without the key
        again = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close")
        assert again.status_code == 409
        late = await receive(client, mill, order["id"], [receipt_line(material, received="1")])
        assert late.status_code == 409
        await assert_reconciler_clean(session, mill.org_id)

    async def test_close_replays_by_key(self, client, mill) -> None:
        order = await issued_order(client, mill)
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], received="82.5")]
        )
        idem = key()
        one = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close", idem=idem)
        two = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close", idem=idem)
        assert one.status_code == two.status_code == 200
        assert one.json()["version"] == two.json()["version"]

    async def test_an_unissued_order_cannot_be_closed_and_does_not_read_as_balanced(
        self, client, mill
    ) -> None:
        order = await create_order(client, mill)
        response = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close")
        assert response.status_code == 409
        view = await call(client, mill.org_id, "GET", f"/orders/{order['id']}/reconciliation")
        assert view.json()["issued"] is False
        assert view.json()["balanced"] is False

    async def test_the_reconciliation_view_lists_every_figure(self, client, mill) -> None:
        order = await issued_order(client, mill)
        await receive(
            client,
            mill,
            order["id"],
            [receipt_line(order["materials"][0]["id"], received="20", rejected="5", shortage="1")],
        )
        view = (
            await call(client, mill.org_id, "GET", f"/orders/{order['id']}/reconciliation")
        ).json()
        assert [view["total_sent"], view["total_received"], view["total_rejected"]] == [
            "82.5",
            "20",
            "5",
        ]
        assert view["total_shortage"] == "1"
        assert view["total_outstanding"] == "56.5"
        assert view["lines"][0]["outstanding"] == "56.5"


class TestListAndOverdue:
    async def test_filters_and_the_overdue_flag(self, client, mill) -> None:
        late = await issued_order(client, mill, due_date=str(date.today() - timedelta(days=3)))
        await issued_order(
            client,
            mill,
            due_date=str(date.today() + timedelta(days=3)),
            materials=[{"item_id": str(mill.fabric), "quantity": "1"}],
        )
        await create_order(client, mill)
        overdue = await call(client, mill.org_id, "GET", "/orders?overdue=true")
        assert [o["id"] for o in overdue.json()["items"]] == [late["id"]]
        assert overdue.json()["items"][0]["is_overdue"] is True
        assert overdue.json()["items"][0]["days_overdue"] == 3
        issued_only = await call(client, mill.org_id, "GET", "/orders?status=ISSUED")
        assert issued_only.json()["total"] == 2
        by_vendor = await call(
            client, mill.org_id, "GET", f"/orders?supplier_id={mill.supplier_id}"
        )
        assert by_vendor.json()["total"] == 3
        by_vendor_other = await call(
            client, mill.org_id, "GET", f"/orders?supplier_id={uuid.uuid4()}"
        )
        assert by_vendor_other.json()["total"] == 0
        searched = await call(client, mill.org_id, "GET", f"/orders?search={late['number']}")
        assert searched.json()["total"] == 1

    async def test_a_received_order_is_never_overdue(self, client, mill) -> None:
        order = await issued_order(client, mill, due_date=str(date.today() - timedelta(days=3)))
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], received="82.5")]
        )
        detail = (await call(client, mill.org_id, "GET", f"/orders/{order['id']}")).json()
        assert detail["is_overdue"] is False
        listed = await call(client, mill.org_id, "GET", "/orders?overdue=true")
        assert listed.json()["total"] == 0

    async def test_list_totals_are_exact_strings(self, client, mill) -> None:
        order = await issued_order(client, mill)
        await receive(
            client,
            mill,
            order["id"],
            [receipt_line(order["materials"][0]["id"], received="30.125")],
        )
        row = (await call(client, mill.org_id, "GET", "/orders")).json()["items"][0]
        assert (row["total_sent"], row["total_outstanding"], row["material_count"]) == (
            "82.5",
            "52.375",
            1,
        )
        assert row["supplier_name"] == "Rainbow Dyers"


class TestPermissionsAreDeclared:
    def test_the_catalogue_carries_the_five_jobwork_permissions(self) -> None:
        from app.catalogue import OPERATIONS, OPERATIONS_ROLE_TEMPLATES

        codes = set(OPERATIONS.codes())
        wanted = {
            f"operations.jobwork.order.{a}"
            for a in ("read", "write", "issue", "receive", "approve")
        }
        assert wanted <= codes
        by_code = {t.code: {g.code for g in t.grants} for t in OPERATIONS_ROLE_TEMPLATES}
        assert wanted <= by_code["OPERATIONS_MANAGER"]
        assert wanted <= by_code["OPERATIONS_ADMIN"]
        assert not wanted & by_code["OPERATIONS_USER"]  # ordinary seats do not run job work
        assert not wanted & by_code["OPERATIONS_WORKER"]
        for spec in OPERATIONS.specs:
            if spec.module == "jobwork":
                assert (spec.default_scope, spec.max_scope) == ("ORG", "ORG")


class TestNumbering:
    async def test_the_challan_series_restarts_each_financial_year_and_the_order_series_each_year(
        self, client, session, mill
    ) -> None:
        from app.jobwork import numbering

        await issued_order(client, mill)  # DC/<this fy>/0001 and JW-<this year>-0001
        async with session.begin():
            same_fy = await numbering.next_challan_number(session, mill.org_id)
            next_fy = await numbering.next_challan_number(
                session, mill.org_id, today=date(2099, 4, 1)
            )
            last_fy = await numbering.next_challan_number(
                session, mill.org_id, today=date(2099, 3, 31)
            )
            next_year = await numbering.next_order_number(
                session, mill.org_id, today=date(2099, 1, 1)
            )
        assert same_fy.endswith("/0002")
        assert next_fy == "DC/99-00/0001"
        assert last_fy == "DC/98-99/0001"
        assert next_year == "JW-2099-0001"
        assert len(next_fy) <= 16
