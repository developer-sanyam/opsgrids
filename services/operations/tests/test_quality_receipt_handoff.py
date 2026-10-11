"""The goods-receipt -> quality hand-off (inventory plan Phases 2 and 4 together): a receipt of an
item that tracks quality lands in `QUALITY_HOLD` and emits `operations.stock.received`;
`app.consumers.goods_receipt_qc_requested` opens an incoming inspection over each held bucket
(one per roll for a roll-tracked item), idempotently, and the inspection then releases the stock
through quality.

Driven end to end: the receipt over HTTP, the staged outbox payload handed to the consumer as the
broker would deliver it, the inspection read back from the database and released over HTTP.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import select, update

from app import consumers
from app.config import settings
from app.models import (
    AuditLog,
    Inspection,
    InventoryItem,
    Lot,
    OutboxEvent,
    ProcessedEvent,
)
from app.quality import handoff
from app.receiving import receipts
from platform_core.db import dispose_engine, init_engine
from tests.helpers import as_principal, principal_for
from tests.quality_fixtures import call as quality_call
from tests.receiving_fixtures import API, RECEIVE_PERMS, RWorld, build_rworld, make_po
from tests.test_goods_receipts_api import bucket

pytestmark = pytest.mark.integration


@pytest.fixture
async def w(session, org_id) -> RWorld:
    return await build_rworld(session, org_id)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The consumer opens its own session scope, independent of the fixture `session`."""
    init_engine(settings())
    yield
    await dispose_engine()


def test_the_event_name_is_the_receiving_contract() -> None:
    assert handoff.EVT_STOCK_RECEIVED == receipts.EVT_STOCK_RECEIVED
    assert handoff.EVT_STOCK_RECEIVED in consumers.consumer._handlers


async def receive(client, org_id, po_id, *lines, **extra):  # type: ignore[no-untyped-def]
    payload = {"purchase_order_id": str(po_id), "lines": list(lines), **extra}
    with as_principal(principal_for(org_id, list(RECEIVE_PERMS))):
        response = await client.post(
            API + "/operations/goods-receipts",
            json=payload,
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
    assert response.status_code == 201, response.text
    return response.json()


def line(po_line_id: uuid.UUID, qty: str, **extra):  # type: ignore[no-untyped-def]
    return {"purchase_order_line_id": str(po_line_id), "quantity": qty, **extra}


async def staged_envelopes(session, org_id: uuid.UUID) -> list[dict]:
    """Every `operations.stock.received` outbox row, wrapped the way the broker delivers it."""
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == receipts.EVT_STOCK_RECEIVED,
                        OutboxEvent.organization_id == org_id,
                    )
                    .order_by(OutboxEvent.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "event_id": str(uuid.uuid4()),
            "event_type": row.event_type,
            "organization_id": str(org_id),
            "payload": copy.deepcopy(row.payload),
        }
        for row in rows
    ]


async def inspections(session, org_id: uuid.UUID) -> list[Inspection]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(Inspection)
                    .where(Inspection.organization_id == org_id)
                    .order_by(Inspection.created_at, Inspection.id)
                )
            )
            .scalars()
            .all()
        )


class TestAReceiptIntoQualityHoldOpensAnInspection:
    async def test_a_quality_tracked_receipt_gets_an_incoming_inspection(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        receipt = await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)

        await consumers.goods_receipt_qc_requested(envelope)

        [inspection] = await inspections(session, org_id)
        assert inspection.kind == "incoming"
        assert inspection.status == "PENDING"
        assert inspection.item_id == w.dye
        assert inspection.location_id == w.t.weaving
        assert inspection.quantity_milli == 10_000
        assert inspection.supplier_id == w.supplier_id
        assert inspection.reference_type == "goods_receipt"
        assert str(inspection.reference_id) == receipt["id"]
        # Already on hold: no second hold move, and the stock did not move.
        assert inspection.hold_move_id is None
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="QUALITY_HOLD") == 10_000

    async def test_the_inspection_then_releases_the_received_stock_through_quality(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        await consumers.goods_receipt_qc_requested(envelope)
        [inspection] = await inspections(session, org_id)

        response = await quality_call(
            client, org_id, "POST", f"/inspections/{inspection.id}/release", json={"note": "ok"}
        )
        assert response.status_code == 200, response.text
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="QUALITY_HOLD") == 0
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="AVAILABLE") == 10_000

    async def test_a_roll_tracked_quality_item_gets_one_inspection_per_roll_and_lot(
        self, client, session, org_id, w: RWorld
    ) -> None:
        async with session.begin():
            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.fabric)
                .values(track_quality=True)
            )
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}],
        )
        receipt = await receive(
            client,
            org_id,
            po_id,
            line(
                lines["COT-180-NAVY"],
                "82.5",
                lot={"supplier_lot_no": "MILL-7"},
                rolls=[{"primary_qty": "50"}, {"primary_qty": "32.5"}],
            ),
        )
        [envelope] = await staged_envelopes(session, org_id)
        lot_id = uuid.UUID(receipt["lines"][0]["lot_id"])

        await consumers.goods_receipt_qc_requested(envelope)

        found = await inspections(session, org_id)
        assert sorted(i.quantity_milli for i in found) == [32_500, 50_000]
        assert {i.lot_id for i in found} == {lot_id}
        assert len({i.roll_id for i in found}) == 2 and None not in {i.roll_id for i in found}
        # Each inspection sits over a real, held bucket: releasing one moves only that roll.
        first = found[0]
        response = await quality_call(
            client, org_id, "POST", f"/inspections/{first.id}/release", json={"note": "ok"}
        )
        assert response.status_code == 200, response.text
        assert (
            await bucket(
                session,
                org_id,
                w.fabric,
                w.t.weaving,
                status="AVAILABLE",
                lot=first.lot_id,
                roll=first.roll_id,
            )
            == first.quantity_milli
        )


class TestNothingToInspect:
    async def test_a_receipt_of_an_untracked_item_opens_nothing(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 2}],
        )
        await receive(client, org_id, po_id, line(lines["BTN-14MM"], "100"))
        [envelope] = await staged_envelopes(session, org_id)
        await consumers.goods_receipt_qc_requested(envelope)
        assert await inspections(session, org_id) == []

    async def test_a_hold_asked_for_on_an_item_that_does_not_track_quality_opens_nothing(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 2}],
        )
        await receive(client, org_id, po_id, line(lines["BTN-14MM"], "100", hold=True))
        [envelope] = await staged_envelopes(session, org_id)
        assert envelope["payload"]["lines"][0]["status"] == "QUALITY_HOLD"
        await consumers.goods_receipt_qc_requested(envelope)
        assert await inspections(session, org_id) == []


class TestIdempotency:
    async def test_a_redelivered_event_does_not_open_a_second_inspection(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        await consumers.goods_receipt_qc_requested(envelope)
        await consumers.goods_receipt_qc_requested(envelope)
        assert len(await inspections(session, org_id)) == 1
        async with session.begin():
            processed = (
                await session.execute(
                    select(ProcessedEvent).where(
                        ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                    )
                )
            ).all()
        assert len(processed) == 1

    async def test_the_same_receipt_under_a_new_event_id_replays_the_inspection(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        await consumers.goods_receipt_qc_requested(envelope)
        await consumers.goods_receipt_qc_requested({**envelope, "event_id": str(uuid.uuid4())})
        assert len(await inspections(session, org_id)) == 1


class TestRefusalsDoNotPoisonTheQueue:
    async def test_stock_that_already_left_quality_hold_is_skipped_not_raised(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        # Someone inspects and releases it by hand before the consumer runs.
        manual = await quality_call(
            client,
            org_id,
            "POST",
            "/inspections",
            json={
                "kind": "incoming",
                "item_id": str(w.dye),
                "location_id": str(w.t.weaving),
                "quantity": "10",
                "hold_stock": False,
            },
        )
        assert manual.status_code == 201, manual.text
        await consumers.goods_receipt_qc_requested(envelope)  # must not raise
        # The bucket was fully claimed by the manual inspection: no second one.
        assert len(await inspections(session, org_id)) == 1

    async def test_a_payload_naming_another_organization_is_ignored(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        envelope["payload"]["organization_id"] = str(uuid.uuid4())
        await consumers.goods_receipt_qc_requested(envelope)
        assert await inspections(session, org_id) == []

    async def test_a_move_that_is_not_in_the_callers_organization_opens_nothing(
        self, client, session, org_id, w: RWorld
    ) -> None:
        """The ledger read is org-filtered: an envelope for another tenant naming this tenant's
        move id (a forged or crossed message) finds no held bucket and opens no inspection."""
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        other = uuid.uuid4()
        envelope["organization_id"] = str(other)
        envelope["payload"]["organization_id"] = str(other)
        await consumers.goods_receipt_qc_requested(envelope)
        assert await inspections(session, org_id) == []
        assert await inspections(session, other) == []


async def lot_status(session, org_id: uuid.UUID, lot_id: uuid.UUID) -> str:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(Lot.quality_status).where(Lot.organization_id == org_id, Lot.id == lot_id)
            )
        ).scalar_one()


async def two_roll_lot(client, session, org_id, w: RWorld):  # type: ignore[no-untyped-def]
    """A quality-tracked, roll-tracked receipt handed off: one lot, two rolls, two inspections."""
    async with session.begin():
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.fabric)
            .values(track_quality=True)
        )
    po_id, lines = await make_po(
        session, w, [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}]
    )
    receipt = await receive(
        client,
        org_id,
        po_id,
        line(
            lines["COT-180-NAVY"],
            "80",
            lot={"supplier_lot_no": "MILL-9"},
            rolls=[{"primary_qty": "50"}, {"primary_qty": "30"}],
        ),
    )
    [envelope] = await staged_envelopes(session, org_id)
    await consumers.goods_receipt_qc_requested(envelope)
    first, second = await inspections(session, org_id)
    return uuid.UUID(receipt["lines"][0]["lot_id"]), first, second


async def decide(client, org_id, insp, action: str, body: dict) -> None:  # type: ignore[no-untyped-def]
    response = await quality_call(
        client, org_id, "POST", f"/inspections/{insp.id}/{action}", json=body
    )
    assert response.status_code == 200, response.text


class TestLotQualityStatusFollowsTheInspections:
    async def test_the_lot_is_approved_only_once_every_roll_is_decided(
        self, client, session, org_id, w: RWorld
    ) -> None:
        lot_id, first, second = await two_roll_lot(client, session, org_id, w)
        assert await lot_status(session, org_id, lot_id) == "PENDING"
        await decide(client, org_id, first, "release", {"note": "ok"})
        assert await lot_status(session, org_id, lot_id) == "PENDING"
        await decide(client, org_id, second, "release", {"note": "ok"})
        assert await lot_status(session, org_id, lot_id) == "APPROVED"

    async def test_a_held_inspection_puts_the_lot_on_hold(
        self, client, session, org_id, w: RWorld
    ) -> None:
        lot_id, first, _ = await two_roll_lot(client, session, org_id, w)
        await decide(client, org_id, first, "hold", {"note": "look again"})
        assert await lot_status(session, org_id, lot_id) == "ON_HOLD"

    async def test_rejecting_every_roll_rejects_the_lot_and_disposal_keeps_it_so(
        self, client, session, org_id, w: RWorld
    ) -> None:
        lot_id, first, second = await two_roll_lot(client, session, org_id, w)
        await decide(client, org_id, first, "reject", {"reason": "torn"})
        assert await lot_status(session, org_id, lot_id) == "PENDING"
        await decide(client, org_id, second, "reject", {"reason": "torn"})
        assert await lot_status(session, org_id, lot_id) == "REJECTED"
        await decide(client, org_id, first, "disposition", {"action": "SCRAP"})
        assert await lot_status(session, org_id, lot_id) == "REJECTED"

    async def test_opening_another_inspection_sends_an_approved_lot_back_to_pending(
        self, client, session, org_id, w: RWorld
    ) -> None:
        lot_id, first, second = await two_roll_lot(client, session, org_id, w)
        await decide(client, org_id, first, "release", {"note": "ok"})
        await decide(client, org_id, second, "release", {"note": "ok"})
        assert await lot_status(session, org_id, lot_id) == "APPROVED"
        opened = await quality_call(
            client,
            org_id,
            "POST",
            "/inspections",
            json={
                "kind": "incoming",
                "item_id": str(first.item_id),
                "location_id": str(first.location_id),
                "quantity": "10",
                "lot_id": str(lot_id),
                "roll_id": str(first.roll_id),
            },
        )
        assert opened.status_code == 201, opened.text
        assert await lot_status(session, org_id, lot_id) == "PENDING"

    async def test_a_lot_with_some_good_stock_is_approved(
        self, client, session, org_id, w: RWorld
    ) -> None:
        lot_id, first, second = await two_roll_lot(client, session, org_id, w)
        await decide(client, org_id, first, "release", {"note": "ok"})
        await decide(client, org_id, second, "reject", {"reason": "torn"})
        assert await lot_status(session, org_id, lot_id) == "APPROVED"


class TestTrackQualityIsCapturedAtReceipt:
    async def test_the_event_carries_the_flag_each_line_was_received_under(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90},
                {"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 2},
            ],
        )
        await receive(
            client, org_id, po_id, line(lines["DYE-RED-01"], "10"), line(lines["BTN-14MM"], "100")
        )
        [envelope] = await staged_envelopes(session, org_id)
        flags = {ln["sku"]: ln["track_quality"] for ln in envelope["payload"]["lines"]}
        assert flags == {"DYE-RED-01": True, "BTN-14MM": False}

    async def test_switching_the_flag_off_before_consumption_still_opens_the_inspection(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        async with session.begin():
            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.dye)
                .values(track_quality=False)
            )
        await consumers.goods_receipt_qc_requested(envelope)
        assert len(await inspections(session, org_id)) == 1

    async def test_a_skipped_bucket_leaves_an_audit_row(
        self, client, session, org_id, w: RWorld
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        receipt = await receive(client, org_id, po_id, line(lines["DYE-RED-01"], "10"))
        [envelope] = await staged_envelopes(session, org_id)
        manual = await quality_call(
            client,
            org_id,
            "POST",
            "/inspections",
            json={
                "kind": "incoming",
                "item_id": str(w.dye),
                "location_id": str(w.t.weaving),
                "quantity": "10",
                "hold_stock": False,
            },
        )
        assert manual.status_code == 201, manual.text
        await consumers.goods_receipt_qc_requested(envelope)
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "goods_receipt.qc_handoff_skipped",
                        )
                    )
                )
                .scalars()
                .all()
            )
        [row] = rows
        assert str(row.entity_id) == receipt["id"]
        assert str(w.dye) in row.after["skipped"][0]["bucket"] and row.after["skipped"][0]["reason"]
