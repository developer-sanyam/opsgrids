"""MRP follow-ups: open PO supply after receiving, the requisition consumer, returned material,
the sync size cap, and not re-requesting a shortage that is already on its way."""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app import consumers
from app.config import settings
from app.inventory import selectors as inv_selectors
from app.models import AuditLog, OutboxEvent, PurchaseRequisition, PurchaseRequisitionLine
from app.mrp.models import MrpShortage
from app.receiving import conversions
from app.receiving.schemas import UomConversionIn
from platform_core.db import dispose_engine, init_engine
from tests.manufacturing_fixtures import make_order
from tests.mrp_fixtures import (
    Plant,
    add_purchase_order,
    add_sales_order,
    build_plant,
    mrp,
    shortages,
    start_run,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def p(client, session, org_id) -> Plant:
    return await build_plant(client, session, org_id)


@pytest.fixture
async def _engine():  # type: ignore[no-untyped-def]
    init_engine(settings())
    yield
    await dispose_engine()


async def _open_po(client, session, p: Plant, lines, **kw) -> str:  # type: ignore[no-untyped-def]
    await add_purchase_order(session, p.org_id, "PO-X", lines, **kw)
    await add_sales_order(session, p.org_id, "SO-X", [(p.m.shirt, "10")])  # 50 buttons
    run = await start_run(client, p.org_id, sources=["SALES_ORDER"])
    return (await shortages(client, p.org_id, run["id"]))["BTN-14MM"]["open_po"]


class TestOpenPoSupply:
    async def test_a_partially_received_po_still_counts_its_remainder(
        self, client, session, p: Plant
    ) -> None:
        got = await _open_po(client, session, p, [("BTN-14MM", 20, 5)], status="partially_received")
        assert got == "15"

    async def test_received_milli_not_the_whole_unit_view_decides_what_is_left(
        self, client, session, p: Plant
    ) -> None:
        # 20 ordered, 5.5 received: received_qty (whole units) says 5, the exact figure says 5.5
        line = ("BTN-14MM", 20, 5, {"received_milli": 5_500})
        assert await _open_po(client, session, p, [line], status="partially_received") == "14.5"

    async def test_a_box_priced_line_counts_in_stock_units(self, client, session, p: Plant) -> None:
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, p.org_id, p.t.buttons)
            assert item is not None
            await conversions.create_conversion(
                session, p.t.ctx, item, UomConversionIn(uom="box", numerator=12, denominator=1)
            )
        # 5 boxes of 12 = 60 pcs ordered; 12 pcs (one box) received
        line = ("BTN-14MM", 5, 1, {"uom": "box", "received_milli": 12_000})
        assert await _open_po(client, session, p, [line], status="partially_received") == "48"

    async def test_a_short_closed_line_expects_nothing_more(
        self, client, session, p: Plant
    ) -> None:
        line = ("BTN-14MM", 20, 5, {"short_closed": True})
        got = await _open_po(client, session, p, [line], status="partially_received")
        assert got == "0"


class TestReturnedMaterial:
    async def test_material_returned_to_stock_is_demand_again(
        self, client, session, admin_session, p: Plant
    ) -> None:
        org = p.org_id
        order = await make_order(client, p.m, p.m.panel, "4", output_location=p.t.finishing)
        before = await start_run(client, org, sources=["PRODUCTION_ORDER"])
        base = (await shortages(client, org, before["id"]))["COT-180-NAVY"]["gross"]
        # 2 m of fabric issued to the floor and all of it handed back
        await admin_session.execute(
            text(
                "UPDATE operations.production_materials SET issued_milli = 2000, "
                "returned_milli = 2000 WHERE production_order_id = :o AND item_id = :i"
            ),
            {"o": uuid.UUID(order["id"]), "i": p.t.fabric},
        )
        await admin_session.commit()
        after = await start_run(client, org, sources=["PRODUCTION_ORDER"])
        assert (await shortages(client, org, after["id"]))["COT-180-NAVY"]["gross"] == base


class TestSyncSizeCap:
    async def test_sync_mode_cannot_bypass_the_cap(
        self, client, session, p: Plant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings(), "mrp_sync_max_demands", 1)
        await add_sales_order(session, p.org_id, "SO-1", [(p.m.shirt, "1"), (p.m.panel, "1")])
        r = await mrp(
            client, p.org_id, "POST", "/runs", json={"sources": ["SALES_ORDER"], "mode": "sync"}
        )
        assert r.status_code == 422, r.text
        ok = await mrp(
            client, p.org_id, "POST", "/runs", json={"sources": ["SALES_ORDER"], "mode": "auto"}
        )
        assert ok.status_code == 202 and ok.json()["execution"] == "WORKER"


async def _request(client, org, run_id, **body):  # type: ignore[no-untyped-def]
    return await mrp(client, org, "POST", f"/runs/{run_id}/requisitions", json=body)


async def _requested_event(session, org, request_id: str) -> dict:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org,
                        OutboxEvent.event_type == "operations.mrp.requisition_requested",
                    )
                )
            )
            .scalars()
            .all()
        )
    (row,) = [r for r in rows if r.payload["request_id"] == request_id]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": row.event_type,
        "organization_id": str(org),
        "payload": row.payload,
    }


async def _requisitions(session, org) -> list[PurchaseRequisition]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(PurchaseRequisition).where(PurchaseRequisition.organization_id == org)
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.usefixtures("_engine")
class TestRequisitionConsumer:
    async def test_the_event_becomes_one_draft_requisition_even_when_delivered_twice(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        out = (await _request(client, org, run["id"], note="urgent")).json()
        envelope = await _requested_event(session, org, out["request_id"])

        await consumers.mrp_requisition_requested(envelope)
        await consumers.mrp_requisition_requested(envelope)
        # a redelivery under a fresh event id is the same request, still one requisition
        await consumers.mrp_requisition_requested({**envelope, "event_id": str(uuid.uuid4())})

        (req,) = await _requisitions(session, org)
        assert req.status == "DRAFT"
        assert out["request_id"] in (req.reason or "") and run["number"] in (req.reason or "")
        assert req.created_by == envelope["payload"]["requested_by"]
        assert req.requested_by_id == uuid.UUID(envelope["payload"]["requested_by_ref"])
        async with session.begin():
            lines = (
                (
                    await session.execute(
                        select(PurchaseRequisitionLine).where(
                            PurchaseRequisitionLine.requisition_id == req.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            audited = (
                await session.execute(
                    select(func.count()).where(
                        AuditLog.organization_id == org,
                        AuditLog.action == "purchase_requisition.created",
                    )
                )
            ).scalar_one()
        assert {ln.inventory_item_id for ln in lines} == {
            uuid.UUID(ln["item_id"]) for ln in envelope["payload"]["lines"]
        }
        assert audited == 1
        async with session.begin():
            linked = (
                (
                    await session.execute(
                        select(MrpShortage.requisition_id).where(
                            MrpShortage.requisition_request_id == uuid.UUID(out["request_id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert linked and set(linked) == {req.id}

    async def test_a_payload_naming_another_organization_is_not_acted_on(
        self, client, session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        out = (await _request(client, org, run["id"])).json()
        envelope = await _requested_event(session, org, out["request_id"])
        envelope["payload"] = {**envelope["payload"], "organization_id": str(uuid.uuid4())}
        await consumers.mrp_requisition_requested(envelope)
        assert await _requisitions(session, org) == []

    async def test_a_user_requisition_quoting_the_request_id_does_not_swallow_the_request(
        self, client, session, p: Plant
    ) -> None:
        from app.receiving import requisitions as requisitions_service
        from app.receiving.schemas import RequisitionIn, RequisitionLineIn
        from platform_core.auth import Scope
        from tests.helpers import ctx_for

        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        run = await start_run(client, org, sources=["SALES_ORDER"])
        out = (await _request(client, org, run["id"])).json()
        envelope = await _requested_event(session, org, out["request_id"])
        user = ctx_for(org, "operations.procurement.requisition.write", Scope.ORG)
        async with session.begin():
            spoof = await requisitions_service.create_requisition(
                session,
                user,
                RequisitionIn(
                    reason=f"MRP request {out['request_id']} (typed by hand)",
                    lines=[RequisitionLineIn(inventory_item_id=p.t.fabric, quantity="1")],
                ),
            )
        await consumers.mrp_requisition_requested(envelope)
        reqs = await _requisitions(session, org)
        assert len(reqs) == 2
        async with session.begin():
            linked = (
                (
                    await session.execute(
                        select(MrpShortage.requisition_id).where(
                            MrpShortage.requisition_request_id == uuid.UUID(out["request_id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert linked and spoof.id not in linked


@pytest.mark.usefixtures("_engine")
class TestNoDoubleRequest:
    async def test_a_shortage_already_requested_in_an_earlier_run_is_not_requested_again(
        self, client, session, admin_session, p: Plant
    ) -> None:
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        first = await start_run(client, org, sources=["SALES_ORDER"])
        out = (await _request(client, org, first["id"])).json()
        assert out["lines"]

        second = await start_run(client, org, sources=["SALES_ORDER"])
        # nothing left to ask for: the same items are still on their way
        assert (await _request(client, org, second["id"])).status_code == 409
        buys = [
            s["id"]
            for s in (await shortages(client, org, second["id"])).values()
            if s["action"] == "BUY"
        ]
        assert (await _request(client, org, second["id"], shortage_ids=buys)).status_code == 409

        # once the requisition made from it is cancelled the need can be raised again
        await consumers.mrp_requisition_requested(
            await _requested_event(session, org, out["request_id"])
        )
        await admin_session.execute(
            text("UPDATE operations.purchase_requisitions SET status = 'CANCELLED'")
        )
        await admin_session.commit()
        again = await _request(client, org, second["id"])
        assert again.status_code == 201, again.text


@pytest.mark.usefixtures("_engine")
class TestCrossRunRequestsAreSerialised:
    async def test_a_request_waits_for_the_organizations_requisition_lock(
        self, client, session, admin_session, p: Plant
    ) -> None:
        """Two planners requesting the same item from two runs would each miss the other's
        uncommitted request; the check-then-mark is taken under one per-organization lock."""
        org = p.org_id
        await add_sales_order(session, org, "SO-1", [(p.m.shirt, "10")])
        first = await start_run(client, org, sources=["SALES_ORDER"])
        async with admin_session.begin():
            await admin_session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"mrp-req:{org}"},
            )
            task = asyncio.create_task(_request(client, org, first["id"]))
            await asyncio.sleep(1)
            assert not task.done(), "the request did not wait for the lock"
        assert (await task).status_code == 201
