"""Purchase requisitions over HTTP (inventory Phase 2): the request -> approve -> convert lifecycle,
every refused transition, OWN-scope visibility, conversion into a draft purchase order, and the
audit rows and events each step leaves.

Authorization matrix and cross-tenant 404s for every route: `test_receiving_authz.py`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, OutboxEvent, PurchaseOrder, PurchaseOrderLine, Supplier
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.receiving_fixtures import (
    API,
    PO_READ,
    PO_WRITE,
    REQ_APPROVE,
    REQ_READ,
    REQ_READ_OWN,
    REQ_WRITE,
    REQ_WRITE_OWN,
    RWorld,
    build_rworld,
    future,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
PR = "/operations/purchase-requisitions"


@pytest.fixture
async def w(session, org_id) -> RWorld:
    return await build_rworld(session, org_id)


async def call(client, org_id, perms, method, path, body=None, *, subject=None, params=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, list(perms), subject_ref=subject)):
        return await client.request(method, API + path, json=body, params=params)


def req_body(w: RWorld, **extra):  # type: ignore[no-untyped-def]
    return {
        "requested_by": "Asha",
        "lines": [
            {"inventory_item_id": str(w.buttons), "quantity": "240", "est_unit_price_minor": 200},
            {"inventory_item_id": str(w.dye), "quantity": "12", "note": "red, lab dip approved"},
        ],
        **extra,
    }


async def create(client, org_id, w, **extra):  # type: ignore[no-untyped-def]
    response = await call(client, org_id, [REQ_WRITE], "POST", PR, req_body(w, **extra))
    assert response.status_code == 201, response.text
    return response.json()


async def move(client, org_id, rid, verb, perms, body=None):  # type: ignore[no-untyped-def]
    return await call(
        client, org_id, perms, "POST", f"{PR}/{rid}/{verb}", body if body is not None else {}
    )


def convert_body(w: RWorld, **extra):  # type: ignore[no-untyped-def]
    return {
        "supplier_id": str(w.supplier_id),
        "expected_date": future().isoformat(),
        "warehouse": "Weaving Store",
        **extra,
    }


async def events(session, kind: str) -> list[OutboxEvent]:  # type: ignore[no-untyped-def]
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type
                        == f"operations.procurement.purchase_requisition.{kind}"
                    )
                )
            )
            .scalars()
            .all()
        )


class TestCreateAndEdit:
    async def test_a_requisition_starts_as_a_numbered_draft_with_its_lines(
        self, client, session, org_id, w
    ) -> None:
        subject = uuid.uuid4()
        response = await call(
            client, org_id, [REQ_WRITE_OWN], "POST", PR, req_body(w), subject=subject
        )
        assert response.status_code == 201, response.text
        req = response.json()
        assert req["status"] == "DRAFT" and req["number"].endswith("-0001")
        assert req["requested_by"] == "Asha" and req["requested_by_id"] == str(subject)
        assert [(ln["sku"], ln["quantity"], ln["uom"]) for ln in req["lines"]] == [
            ("BTN-14MM", "240", "pcs"),
            ("DYE-RED-01", "12", "kg"),
        ]
        assert req["lines"][0]["est_unit_price_minor"] == 200
        second = await call(client, org_id, [REQ_WRITE], "POST", PR, req_body(w))
        assert second.json()["number"].endswith("-0002")

    async def test_the_created_event_matches_its_contract(self, client, session, org_id, w) -> None:
        req = await create(client, org_id, w)
        (event,) = await events(session, "created")
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert event.payload["requisition_id"] == req["id"] and event.payload["line_count"] == 2

    @pytest.mark.parametrize(
        ("line", "field"),
        [
            ({"quantity": "0"}, "quantity"),
            ({"quantity": "-3"}, "quantity"),
            ({"quantity": "1.5"}, "quantity"),  # buttons are counted in whole pieces
        ],
    )
    async def test_a_bad_quantity_is_a_422_naming_the_line(
        self, client, org_id, w, line, field
    ) -> None:
        body = {"lines": [{"inventory_item_id": str(w.buttons), **line}]}
        response = await call(client, org_id, [REQ_WRITE], "POST", PR, body)
        assert response.status_code == 422, response.text

    async def test_an_unknown_item_supplier_or_warehouse_is_a_404(self, client, org_id, w) -> None:
        for extra in (
            {"lines": [{"inventory_item_id": str(uuid.uuid4()), "quantity": "1"}]},
            {
                "lines": [
                    {
                        "inventory_item_id": str(w.buttons),
                        "quantity": "1",
                        "suggested_supplier_id": str(uuid.uuid4()),
                    }
                ]
            },
            {
                "warehouse_id": str(uuid.uuid4()),
                "lines": [{"inventory_item_id": str(w.buttons), "quantity": "1"}],
            },
        ):
            response = await call(client, org_id, [REQ_WRITE], "POST", PR, extra)
            assert response.status_code == 404, response.text

    async def test_a_draft_can_be_edited_and_its_lines_replaced(
        self, client, session, org_id, w
    ) -> None:
        req = await create(client, org_id, w)
        response = await call(
            client,
            org_id,
            [REQ_WRITE],
            "PATCH",
            f"{PR}/{req['id']}",
            {
                "reason": "Q4 restock",
                "needed_by": future(30).isoformat(),
                "lines": [{"inventory_item_id": str(w.fabric), "quantity": "250.5"}],
            },
        )
        assert response.status_code == 200, response.text
        out = response.json()
        assert out["reason"] == "Q4 restock" and out["needed_by"] == future(30).isoformat()
        assert [(ln["sku"], ln["quantity"], ln["line_no"]) for ln in out["lines"]] == [
            ("COT-180-NAVY", "250.5", 1)
        ]

    async def test_only_a_draft_can_be_edited(self, client, org_id, w) -> None:
        req = await create(client, org_id, w)
        await move(client, org_id, req["id"], "submit", [REQ_WRITE])
        response = await call(
            client, org_id, [REQ_WRITE], "PATCH", f"{PR}/{req['id']}", {"reason": "late change"}
        )
        assert response.status_code == 409


class TestLifecycle:
    async def test_submit_approve_convert_walks_to_a_draft_purchase_order(
        self, client, session, org_id, w
    ) -> None:
        req = await create(client, org_id, w)
        rid = req["id"]
        submitted = await move(client, org_id, rid, "submit", [REQ_WRITE])
        assert submitted.status_code == 200 and submitted.json()["status"] == "SUBMITTED"
        assert submitted.json()["submitted_at"]
        approved = await move(client, org_id, rid, "approve", [REQ_APPROVE], {"note": "go ahead"})
        assert approved.json()["status"] == "APPROVED"
        assert (approved.json()["decision_note"], bool(approved.json()["decided_at"])) == (
            "go ahead",
            True,
        )
        converted = await call(
            client,
            org_id,
            [REQ_WRITE, PO_WRITE],
            "POST",
            f"{PR}/{rid}/convert",
            convert_body(w, prices=[{"line_id": req["lines"][1]["id"], "unit_price_minor": 90}]),
        )
        assert converted.status_code == 200, converted.text
        po = converted.json()
        assert po["status"] == "draft" and po["supplier_id"] == str(w.supplier_id)
        assert po["notes"].startswith(f"From requisition {req['number']}")
        by_sku = {ln["sku"]: ln for ln in po["line_items"]}
        # estimate on line 1, the override on line 2
        assert (by_sku["BTN-14MM"]["quantity"], by_sku["BTN-14MM"]["unit_price_minor"]) == (
            240,
            200,
        )
        assert (by_sku["DYE-RED-01"]["quantity"], by_sku["DYE-RED-01"]["unit_price_minor"]) == (
            12,
            90,
        )
        assert by_sku["BTN-14MM"]["uom"] == "pcs"

        done = await call(client, org_id, [REQ_READ], "GET", f"{PR}/{rid}")
        assert done.json()["status"] == "CONVERTED"
        assert done.json()["converted_po_id"] == po["id"]
        assert done.json()["converted_po_number"] == po["po_number"]
        async with session.begin():
            open_pos = (
                await session.execute(select(Supplier.open_pos).where(Supplier.id == w.supplier_id))
            ).scalar_one()
        assert open_pos == 1  # the PO went through the PO module's own writer

    async def test_each_step_leaves_an_audit_row_and_a_contract_valid_event(
        self, client, session, org_id, w
    ) -> None:
        req = await create(client, org_id, w)
        rid = req["id"]
        await move(client, org_id, rid, "submit", [REQ_WRITE])
        await move(client, org_id, rid, "approve", [REQ_APPROVE], {"note": "ok"})
        await call(
            client, org_id, [REQ_WRITE, PO_WRITE], "POST", f"{PR}/{rid}/convert", convert_body(w)
        )
        for kind in ("submitted", "approved", "converted"):
            (event,) = await events(session, kind)
            validate_event_schema(
                event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
            )
            assert event.payload["requisition_id"] == rid
        (converted,) = await events(session, "converted")
        assert converted.payload["po_number"] and converted.payload["status"] == "CONVERTED"
        async with session.begin():
            actions = {
                a
                for (a,) in (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.entity == "purchase_requisition")
                    )
                ).all()
            }
        assert actions == {
            "purchase_requisition.created",
            "purchase_requisition.submitted",
            "purchase_requisition.approved",
            "purchase_requisition.converted",
        }

    async def test_reject_needs_a_reason_and_is_terminal(self, client, session, org_id, w) -> None:
        req = await create(client, org_id, w)
        rid = req["id"]
        await move(client, org_id, rid, "submit", [REQ_WRITE])
        blank = await move(client, org_id, rid, "reject", [REQ_APPROVE], {"reason": ""})
        assert blank.status_code == 422
        rejected = await move(
            client, org_id, rid, "reject", [REQ_APPROVE], {"reason": "over budget"}
        )
        assert rejected.json()["status"] == "REJECTED"
        assert rejected.json()["decision_note"] == "over budget"
        (event,) = await events(session, "rejected")
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert (await move(client, org_id, rid, "approve", [REQ_APPROVE])).status_code == 409
        assert (await move(client, org_id, rid, "submit", [REQ_WRITE])).status_code == 409

    @pytest.mark.parametrize(
        ("setup", "verb", "perms"),
        [
            ([], "approve", [REQ_APPROVE]),  # still a draft
            ([], "reject", [REQ_APPROVE]),
            ([], "convert", [REQ_WRITE, PO_WRITE]),
            (["submit"], "submit", [REQ_WRITE]),
            (["submit"], "convert", [REQ_WRITE, PO_WRITE]),  # decided? not yet
            (["submit", "approve"], "reject", [REQ_APPROVE]),
            (["submit", "approve"], "submit", [REQ_WRITE]),
            (["cancel"], "submit", [REQ_WRITE]),
            (["cancel"], "cancel", [REQ_WRITE]),
        ],
    )
    async def test_a_transition_the_machine_does_not_allow_is_a_409(
        self, client, org_id, w, setup, verb, perms
    ) -> None:
        req = await create(client, org_id, w)
        rid = req["id"]
        for step in setup:
            step_perms = [REQ_APPROVE] if step == "approve" else [REQ_WRITE]
            assert (await move(client, org_id, rid, step, step_perms)).status_code == 200
        body = (
            convert_body(w) if verb == "convert" else ({"reason": "r"} if verb == "reject" else {})
        )
        response = await call(client, org_id, perms, "POST", f"{PR}/{rid}/{verb}", body)
        assert response.status_code == 409, (setup, verb, response.text)

    async def test_a_converted_requisition_cannot_be_cancelled_or_converted_twice(
        self, client, org_id, w
    ) -> None:
        req = await create(client, org_id, w)
        rid = req["id"]
        await move(client, org_id, rid, "submit", [REQ_WRITE])
        await move(client, org_id, rid, "approve", [REQ_APPROVE])
        first = await call(
            client, org_id, [REQ_WRITE, PO_WRITE], "POST", f"{PR}/{rid}/convert", convert_body(w)
        )
        assert first.status_code == 200
        assert (await move(client, org_id, rid, "cancel", [REQ_WRITE])).status_code == 409
        again = await call(
            client, org_id, [REQ_WRITE, PO_WRITE], "POST", f"{PR}/{rid}/convert", convert_body(w)
        )
        assert again.status_code == 409

    async def test_cancel_works_from_draft_submitted_and_approved(
        self, client, session, org_id, w
    ) -> None:
        for steps in ([], ["submit"], ["submit", "approve"]):
            req = await create(client, org_id, w)
            for step in steps:
                await move(
                    client,
                    org_id,
                    req["id"],
                    step,
                    [REQ_APPROVE if step == "approve" else REQ_WRITE],
                )
            cancelled = await move(
                client, org_id, req["id"], "cancel", [REQ_WRITE], {"note": "no longer needed"}
            )
            assert cancelled.json()["status"] == "CANCELLED"
        assert len(await events(session, "cancelled")) == 3

    async def test_two_approvals_race_and_exactly_one_wins(self, client, org_id, w) -> None:
        req = await create(client, org_id, w)
        await move(client, org_id, req["id"], "submit", [REQ_WRITE])
        first = await move(client, org_id, req["id"], "approve", [REQ_APPROVE])
        second = await move(client, org_id, req["id"], "approve", [REQ_APPROVE])
        assert (first.status_code, second.status_code) == (200, 409)


class TestConvert:
    async def approved(self, client, org_id, w, **extra):  # type: ignore[no-untyped-def]
        req = await create(client, org_id, w, **extra)
        await move(client, org_id, req["id"], "submit", [REQ_WRITE])
        await move(client, org_id, req["id"], "approve", [REQ_APPROVE])
        return req

    async def test_the_requisitions_own_warehouse_is_the_default(
        self, client, session, org_id, w
    ) -> None:
        from app.inventory import selectors as inv_selectors

        async with session.begin():
            warehouse = await inv_selectors.get_warehouse_by_name(session, org_id, "Weaving Store")
            warehouse_id = str(warehouse.id)
        req = await self.approved(client, org_id, w, warehouse_id=warehouse_id)
        body = convert_body(w)
        del body["warehouse"]
        response = await call(
            client, org_id, [REQ_WRITE, PO_WRITE], "POST", f"{PR}/{req['id']}/convert", body
        )
        assert response.status_code == 200, response.text
        assert response.json()["warehouse"] == "Weaving Store"

    async def test_with_no_warehouse_anywhere_it_is_a_422(self, client, org_id, w) -> None:
        req = await self.approved(client, org_id, w)
        body = convert_body(w)
        del body["warehouse"]
        response = await call(
            client, org_id, [REQ_WRITE, PO_WRITE], "POST", f"{PR}/{req['id']}/convert", body
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "warehouse"

    async def test_a_fractional_quantity_cannot_become_a_whole_unit_po_line(
        self, client, session, org_id, w
    ) -> None:
        req = await create(
            client,
            org_id,
            w,
            lines=[{"inventory_item_id": str(w.fabric), "quantity": "82.5"}],
        )
        await move(client, org_id, req["id"], "submit", [REQ_WRITE])
        await move(client, org_id, req["id"], "approve", [REQ_APPROVE])
        response = await call(
            client,
            org_id,
            [REQ_WRITE, PO_WRITE],
            "POST",
            f"{PR}/{req['id']}/convert",
            convert_body(w),
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "not_whole"
        # nothing converted, nothing raised
        async with session.begin():
            pos = (
                await session.execute(
                    select(func.count())
                    .select_from(PurchaseOrder)
                    .where(PurchaseOrder.organization_id == org_id)
                )
            ).scalar_one()
        assert pos == 0
        still = await call(client, org_id, [REQ_READ], "GET", f"{PR}/{req['id']}")
        assert still.json()["status"] == "APPROVED"

    async def test_a_price_for_a_line_that_is_not_there_is_a_422(self, client, org_id, w) -> None:
        req = await self.approved(client, org_id, w)
        response = await call(
            client,
            org_id,
            [REQ_WRITE, PO_WRITE],
            "POST",
            f"{PR}/{req['id']}/convert",
            convert_body(w, prices=[{"line_id": str(uuid.uuid4()), "unit_price_minor": 1}]),
        )
        assert response.status_code == 422

    async def test_an_inactive_supplier_cannot_be_ordered_from(
        self, client, session, org_id, w
    ) -> None:
        from sqlalchemy import update

        async with session.begin():
            await session.execute(
                update(Supplier).where(Supplier.id == w.supplier_id).values(status="on_hold")
            )
        req = await self.approved(client, org_id, w)
        response = await call(
            client,
            org_id,
            [REQ_WRITE, PO_WRITE],
            "POST",
            f"{PR}/{req['id']}/convert",
            convert_body(w),
        )
        assert response.status_code == 409

    async def test_the_converted_po_lines_can_then_be_received(
        self, client, session, org_id, w
    ) -> None:
        req = await self.approved(client, org_id, w)
        po = (
            await call(
                client,
                org_id,
                [REQ_WRITE, PO_WRITE],
                "POST",
                f"{PR}/{req['id']}/convert",
                convert_body(w),
            )
        ).json()
        async with session.begin():
            lines = (
                (
                    await session.execute(
                        select(PurchaseOrderLine).where(
                            PurchaseOrderLine.purchase_order_id == uuid.UUID(po["id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert {ln.sku for ln in lines} == {"BTN-14MM", "DYE-RED-01"}
        assert all(ln.received_milli == 0 and not ln.short_closed for ln in lines)


class TestScopeAndLists:
    async def test_an_own_scope_reader_sees_only_their_own_requisitions(
        self, client, org_id, w
    ) -> None:
        asha, ben = uuid.uuid4(), uuid.uuid4()
        mine = await call(client, org_id, [REQ_WRITE_OWN], "POST", PR, req_body(w), subject=asha)
        theirs = await call(client, org_id, [REQ_WRITE_OWN], "POST", PR, req_body(w), subject=ben)
        listing = await call(client, org_id, [REQ_READ_OWN], "GET", PR, subject=asha)
        assert [r["id"] for r in listing.json()["items"]] == [mine.json()["id"]]
        peek = await call(
            client, org_id, [REQ_READ_OWN], "GET", f"{PR}/{theirs.json()['id']}", subject=asha
        )
        assert peek.status_code == 403
        own = await call(
            client, org_id, [REQ_READ_OWN], "GET", f"{PR}/{mine.json()['id']}", subject=asha
        )
        assert own.status_code == 200
        everyone = await call(client, org_id, [REQ_READ], "GET", PR, subject=asha)
        assert len(everyone.json()["items"]) == 2

    async def test_an_own_scope_writer_cannot_touch_someone_elses(self, client, org_id, w) -> None:
        asha, ben = uuid.uuid4(), uuid.uuid4()
        theirs = (
            await call(client, org_id, [REQ_WRITE_OWN], "POST", PR, req_body(w), subject=ben)
        ).json()
        for verb, body in (("submit", {}), ("cancel", {})):
            response = await call(
                client,
                org_id,
                [REQ_WRITE_OWN],
                "POST",
                f"{PR}/{theirs['id']}/{verb}",
                body,
                subject=asha,
            )
            assert response.status_code == 403, verb
        edit = await call(
            client,
            org_id,
            [REQ_WRITE_OWN],
            "PATCH",
            f"{PR}/{theirs['id']}",
            {"reason": "x"},
            subject=asha,
        )
        assert edit.status_code == 403

    async def test_the_list_filters_by_status_and_search(self, client, org_id, w) -> None:
        a = await create(client, org_id, w)
        b = await create(client, org_id, w, requested_by="Zed")
        await move(client, org_id, b["id"], "submit", [REQ_WRITE])
        submitted = await call(
            client, org_id, [REQ_READ], "GET", PR, params={"status": "SUBMITTED"}
        )
        assert [r["id"] for r in submitted.json()["items"]] == [b["id"]]
        found = await call(client, org_id, [REQ_READ], "GET", PR, params={"q": "zed"})
        assert [r["id"] for r in found.json()["items"]] == [b["id"]]
        by_number = await call(client, org_id, [REQ_READ], "GET", PR, params={"q": a["number"]})
        assert [r["id"] for r in by_number.json()["items"]] == [a["id"]]
        row = submitted.json()["items"][0]
        assert (row["line_count"], row["status"]) == (2, "SUBMITTED")
        assert submitted.json()["total"] == 1

    async def test_the_convert_route_needs_the_po_write_permission_too(
        self, client, org_id, w
    ) -> None:
        req = await create(client, org_id, w)
        await move(client, org_id, req["id"], "submit", [REQ_WRITE])
        await move(client, org_id, req["id"], "approve", [REQ_APPROVE])
        only_req = await call(
            client,
            org_id,
            [REQ_WRITE, PO_READ],
            "POST",
            f"{PR}/{req['id']}/convert",
            convert_body(w),
        )
        assert only_req.status_code == 403
        still = await call(client, org_id, [REQ_READ], "GET", f"{PR}/{req['id']}")
        assert still.json()["status"] == "APPROVED"
