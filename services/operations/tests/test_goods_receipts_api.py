"""Goods receipts over HTTP (inventory Phase 2): the tolerance arithmetic on real purchase orders,
partial receipts adding up across documents, lot and roll creation, quality-hold routing, exact
box-to-piece conversion, idempotent replay, and the purchase-order state it leaves behind.

Authorization and cross-tenant behaviour of the routes are `test_receiving_authz.py`'s; two
receipts racing each other are `test_receiving_concurrency.py`'s; this file is what a
receipt *does*.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.inventory.quantity import format_qty
from app.inventory.reconcile import reconcile_organization
from app.models import (
    AuditLog,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    InventoryMove,
    LedgerEntry,
    Lot,
    OutboxEvent,
    PurchaseOrder,
    Roll,
    Supplier,
)
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.receiving_fixtures import (
    API,
    ITEM_READ,
    PO_READ,
    RECEIPT_READ,
    RECEIVE_PERMS,
    RWorld,
    build_rworld,
    make_po,
    po_line_state,
)

pytestmark = pytest.mark.integration

#: What the legacy single-shot receive route needs: the PO write plus the two warehouse keys.
LEGACY_RECEIVE = [
    "operations.procurement.purchase_order.write:ORG",
    "operations.procurement.receipt.write:ORG",
    "operations.inventory.stock.receive:ORG",
]

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
GRN = "/operations/goods-receipts"


@pytest.fixture
async def w(session, org_id) -> RWorld:
    return await build_rworld(session, org_id)


def line(po_line_id: uuid.UUID, qty: str, **extra):  # type: ignore[no-untyped-def]
    return {"purchase_order_line_id": str(po_line_id), "quantity": qty, **extra}


def body(po_id: uuid.UUID, *lines, **extra):  # type: ignore[no-untyped-def]
    return {"purchase_order_id": str(po_id), "lines": list(lines), **extra}


async def post(client, org_id, payload, *, key: str | None = None, perms=RECEIVE_PERMS):  # type: ignore[no-untyped-def]
    headers = {"Idempotency-Key": key or uuid.uuid4().hex}
    with as_principal(principal_for(org_id, list(perms))):
        return await client.post(API + GRN, json=payload, headers=headers)


async def get(client, org_id, path, perm=RECEIPT_READ):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, [perm])):
        return await client.get(API + path)


async def bucket(  # type: ignore[no-untyped-def]
    session, org_id, item, location, *, status="AVAILABLE", lot=None, roll=None
) -> int:
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item,
                    InventoryBalance.location_id == location,
                    InventoryBalance.status == status,
                    InventoryBalance.lot_id.is_not_distinct_from(lot),
                    InventoryBalance.roll_id.is_not_distinct_from(roll),
                )
            )
        ).scalar_one_or_none()
    return row or 0


async def po_status(session, org_id, po_id) -> str:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(PurchaseOrder.status).where(
                    PurchaseOrder.organization_id == org_id, PurchaseOrder.id == po_id
                )
            )
        ).scalar_one()


async def assert_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.clean, report.findings


async def count(session, model, org_id) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(func.count()).select_from(model).where(model.organization_id == org_id)
            )
        ).scalar_one()


def buttons_line(qty: int = 100, uom: str = "pcs", price: int = 245_00) -> dict:
    return {"sku": "BTN-14MM", "quantity": qty, "uom": uom, "unit_price_minor": price}


class TestExactPartialAndShort:
    async def test_receipt_numbering_stays_numeric_past_9999(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        first = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))
        year = first.json()["number"].split("-")[1]
        # Receipts are append-only for the app role, so move the counter as the schema owner.
        admin = create_async_engine(settings().admin_dsn)
        async with admin.begin() as conn:
            await conn.execute(
                update(GoodsReceipt)
                .where(GoodsReceipt.id == uuid.UUID(first.json()["id"]))
                .values(number=f"GRN-{year}-9999")
            )
        await admin.dispose()
        second = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))
        assert second.status_code == 201, second.text
        third = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))
        assert third.status_code == 201, third.text
        assert (second.json()["number"], third.json()["number"]) == (
            f"GRN-{year}-10000",
            f"GRN-{year}-10001",
        )

    async def test_a_receipt_without_an_idempotency_key_is_refused(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        with as_principal(principal_for(org_id, list(RECEIVE_PERMS))):
            response = await client.post(
                API + GRN, json=body(po_id, line(lines["BTN-14MM"], "100"))
            )
        assert response.status_code == 422, response.text
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 0

    async def test_an_exact_receipt_books_the_stock_and_completes_the_po(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "100")))
        assert response.status_code == 201, response.text
        grn = response.json()
        assert grn["number"].startswith("GRN-") and grn["status"] == "POSTED"
        assert grn["replayed"] is False and grn["po_status"] == "received"
        (row,) = grn["lines"]
        assert (row["received_qty"], row["ordered_qty"], row["prior_received_qty"]) == (
            "100",
            "100",
            "0",
        )
        assert (row["cumulative_qty"], row["outcome"], row["stock_status"]) == (
            "100",
            "EXACT",
            "AVAILABLE",
        )
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 100_000
        assert await po_status(session, org_id, po_id) == "received"
        assert await po_line_state(session, org_id, lines["BTN-14MM"]) == (100_000, 100, False)
        await assert_clean(session, org_id)

    async def test_the_ledger_move_is_a_receipt_referencing_the_goods_receipt(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)])
        grn = (await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))).json()
        async with session.begin():
            move = (
                await session.execute(
                    select(InventoryMove).where(
                        InventoryMove.organization_id == org_id, InventoryMove.kind == "receipt"
                    )
                )
            ).scalar_one()
            entry = (
                await session.execute(select(LedgerEntry).where(LedgerEntry.move_id == move.id))
            ).scalar_one()
        assert (move.reference_type, str(move.reference_id)) == ("goods_receipt", grn["id"])
        # cost is the PO's, in the PO's currency (ADR-0014 §8)
        assert (entry.unit_cost_minor, entry.unit_cost_currency) == (245_00, "INR")

    async def test_two_partial_receipts_add_up_and_the_second_completes_the_po(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        first = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "40")))
        assert first.status_code == 201
        assert first.json()["po_status"] == "partially_received"
        assert first.json()["lines"][0]["outcome"] == "PARTIAL"
        assert await po_status(session, org_id, po_id) == "partially_received"

        second = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "60")))
        assert second.status_code == 201, second.text
        row = second.json()["lines"][0]
        assert (row["prior_received_qty"], row["received_qty"], row["cumulative_qty"]) == (
            "40",
            "60",
            "100",
        )
        assert (row["outcome"], second.json()["po_status"]) == ("EXACT", "received")
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 100_000
        assert await po_line_state(session, org_id, lines["BTN-14MM"]) == (100_000, 100, False)
        assert await count(session, GoodsReceipt, org_id) == 2
        await assert_clean(session, org_id)

    async def test_a_decimal_partial_keeps_the_exact_figure_beside_the_whole_unit_one(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        grn = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "2.625")))
        assert grn.status_code == 201, grn.text
        assert await po_line_state(session, org_id, lines["DYE-RED-01"]) == (2_625, 2, False)
        ctx = await get(client, org_id, f"/operations/purchase-orders/{po_id}/receiving")
        row = ctx.json()["lines"][0]
        assert (row["received_qty"], row["remaining_qty"]) == ("2.625", "7.375")
        with as_principal(principal_for(org_id, [PO_READ])):
            po = await client.get(f"{API}/operations/purchase-orders/{po_id}")
        assert po.json()["line_items"][0]["received_exact"] == "2.625"
        assert po.json()["line_items"][0]["received_qty"] == 2

    async def test_closing_a_line_short_completes_it_and_the_order(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "60", close_short=True))
        )
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert (row["outcome"], row["close_short"], row["cumulative_qty"]) == ("SHORT", True, "60")
        assert response.json()["po_status"] == "received"
        assert await po_line_state(session, org_id, lines["BTN-14MM"]) == (60_000, 60, True)
        again = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "1")))
        assert again.status_code == 409  # the order is closed out

    async def test_a_line_closed_short_while_another_is_open_leaves_the_order_partial(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                buttons_line(100),
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90},
            ],
        )
        first = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "0", close_short=True))
        )
        assert first.status_code == 201, first.text
        assert first.json()["po_status"] == "partially_received"
        assert first.json()["lines"][0]["received_qty"] == "0"
        # no stock was booked for a zero line
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 0
        # the closed line takes nothing further
        blocked = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "5")))
        assert blocked.status_code == 409
        assert "closed short" in blocked.json()["detail"]
        done = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "10")))
        assert done.status_code == 201 and done.json()["po_status"] == "received"

    async def test_closing_short_a_line_that_is_already_complete_is_refused(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)])
        response = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "10", close_short=True))
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "lines[0].close_short"

    async def test_one_receipt_may_carry_several_lines_of_one_po_line(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        pl = lines["BTN-14MM"]
        response = await post(
            client,
            org_id,
            body(
                po_id,
                line(pl, "30"),
                line(pl, "20", location_id=str(w.bin_b)),
            ),
        )
        assert response.status_code == 201, response.text
        first, second = response.json()["lines"]
        assert (first["prior_received_qty"], first["cumulative_qty"]) == ("0", "30")
        assert (second["prior_received_qty"], second["cumulative_qty"]) == ("30", "50")
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 30_000
        assert await bucket(session, org_id, w.buttons, w.bin_b) == 20_000

    async def test_two_lines_landing_in_one_bucket_post_as_one_summed_line(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        pl = lines["BTN-14MM"]
        response = await post(client, org_id, body(po_id, line(pl, "30"), line(pl, "20")))
        assert response.status_code == 201, response.text
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 50_000
        async with session.begin():
            entries = (
                await session.execute(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.organization_id == org_id)
                )
            ).scalar_one()
        assert entries == 1
        await assert_clean(session, org_id)


class TestTolerance:
    async def test_over_receipt_within_tolerance_is_accepted_and_marked_over(
        self, client, session, org_id, w
    ) -> None:
        # buttons carry a 5 % tolerance: 100 ordered -> up to 105
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "105")))
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert (row["outcome"], row["cumulative_qty"], row["tolerance_pct"]) == ("OVER", "105", "5")
        assert response.json()["po_status"] == "received"
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 105_000

    async def test_over_receipt_beyond_tolerance_is_refused_and_books_nothing(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "106")))
        assert response.status_code == 422
        error = response.json()["errors"][0]
        assert error["field"] == "lines[0].quantity" and error["code"] == "over_receipt"
        assert "105" in error["message"]
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 0
        assert await count(session, GoodsReceipt, org_id) == 0
        assert await po_status(session, org_id, po_id) == "approved"

    async def test_zero_tolerance_means_exactly_the_ordered_quantity(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        over = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "10.001")))
        assert over.status_code == 422
        exact = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "10")))
        assert exact.status_code == 201

    async def test_the_limit_counts_what_earlier_receipts_already_took(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        pl = lines["BTN-14MM"]
        assert (await post(client, org_id, body(po_id, line(pl, "60")))).status_code == 201
        # 60 + 46 = 106 > 105
        assert (await post(client, org_id, body(po_id, line(pl, "46")))).status_code == 422
        ok = await post(client, org_id, body(po_id, line(pl, "45")))
        assert ok.status_code == 201
        assert ok.json()["lines"][0]["outcome"] == "OVER"
        assert ok.json()["lines"][0]["cumulative_qty"] == "105"

    async def test_the_limit_is_floored_to_a_whole_milli_unit(
        self, client, session, org_id, w
    ) -> None:
        # 0.5 % of 3 kg is 0.015 kg exactly; of 333 pcs it is 1.665 pcs -> a discrete unit gets
        # whole pieces only, so the *quantity* check (not the floor) refuses 335 and allows 334
        async with session.begin():
            from app.models import InventoryItem

            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.buttons)
                .values(receipt_tolerance_bp=50)
            )
        po_id, lines = await make_po(session, w, [buttons_line(333)])
        pl = lines["BTN-14MM"]
        assert (await post(client, org_id, body(po_id, line(pl, "335")))).status_code == 422
        assert (await post(client, org_id, body(po_id, line(pl, "334")))).status_code == 201

    async def test_receiving_context_reports_the_remaining_and_the_tolerance_headroom(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "70")))
        response = await get(client, org_id, f"/operations/purchase-orders/{po_id}/receiving")
        assert response.status_code == 200
        ctx = response.json()
        assert ctx["can_receive"] is True and ctx["po_status"] == "partially_received"
        row = ctx["lines"][0]
        assert (row["ordered_qty"], row["received_qty"], row["remaining_qty"]) == (
            "100",
            "70",
            "30",
        )
        assert (row["max_receivable_qty"], row["tolerance_pct"]) == ("35", "5")
        assert row["conversions"] == [{"uom": "box", "numerator": 12, "denominator": 1}]
        assert row["default_location_id"] == str(w.t.weaving)


class TestUnitConversion:
    async def test_three_boxes_of_twelve_book_thirty_six_pieces(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(120)])
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "3", uom="box")))
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert (row["entered_qty"], row["entered_uom"]) == ("3", "box")
        assert (row["received_qty"], row["stock_uom"], row["cumulative_qty"]) == ("36", "pcs", "36")
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 36_000
        assert response.json()["po_status"] == "partially_received"

    async def test_a_po_ordered_in_boxes_is_compared_in_pieces(
        self, client, session, org_id, w
    ) -> None:
        # 10 boxes at 24.00 a box: 120 pieces at 2.00 a piece
        po_id, lines = await make_po(session, w, [buttons_line(10, "box", 24_00)])
        context = await get(client, org_id, f"/operations/purchase-orders/{po_id}/receiving")
        assert context.json()["lines"][0]["ordered_qty"] == "120"
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10", uom="box")))
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert (row["ordered_qty"], row["received_qty"], row["outcome"]) == ("120", "120", "EXACT")
        assert (row["unit_cost_minor"], row["unit_cost_currency"]) == (2_00, "INR")
        assert response.json()["po_status"] == "received"

    async def test_a_fraction_of_a_box_that_is_not_whole_pieces_is_refused(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(120)])
        response = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "0.5", uom="box"))
        )
        # a box is a discrete unit: half a box is not a thing the books can hold
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "lines[0].quantity"

    async def test_a_unit_with_no_recorded_ratio_is_refused_not_guessed(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(120)])
        response = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "1", uom="carton"))
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "invalid_quantity"
        assert await count(session, GoodsReceipt, org_id) == 0

    async def test_a_fixed_unit_of_the_same_dimension_converts_exactly(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        response = await post(
            client, org_id, body(po_id, line(lines["DYE-RED-01"], "2500", uom="g"))
        )
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert (row["entered_qty"], row["entered_uom"], row["received_qty"]) == ("2500", "g", "2.5")


class TestTracking:
    async def test_a_roll_delivery_creates_a_lot_and_one_roll_per_roll_with_both_quantities(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 123_45}],
        )
        response = await post(
            client,
            org_id,
            body(
                po_id,
                line(
                    lines["COT-180-NAVY"],
                    "82.5",
                    lot={"supplier_lot_no": "MILL-77", "lot_no": "LOT-IN-1"},
                    rolls=[
                        {
                            "roll_no": "R-IN-1",
                            "primary_qty": "50",
                            "secondary_qty": "9",
                            "secondary_uom": "kg",
                            "width_mm": 1500,
                            "gsm": 180,
                            "shade": "NAVY",
                            "grade": "A",
                        },
                        {
                            "roll_no": "R-IN-2",
                            "primary_qty": "32.5",
                            "secondary_qty": "5.8",
                            "secondary_uom": "kg",
                        },
                    ],
                ),
            ),
        )
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert row["lot_no"] == "LOT-IN-1" and row["supplier_lot_no"] == "MILL-77"
        rolls = {r["roll_no"]: r for r in row["rolls"]}
        assert (rolls["R-IN-1"]["primary_qty"], rolls["R-IN-1"]["secondary_qty"]) == ("50", "9")
        assert (rolls["R-IN-2"]["primary_qty"], rolls["R-IN-2"]["secondary_uom"]) == ("32.5", "kg")
        async with session.begin():
            lot = (
                await session.execute(select(Lot).where(Lot.organization_id == org_id))
            ).scalar_one()
            db_rolls = (
                (await session.execute(select(Roll).where(Roll.organization_id == org_id)))
                .scalars()
                .all()
            )
        assert (lot.source_type, str(lot.source_id), lot.quality_status) == (
            "goods_receipt",
            response.json()["id"],
            "APPROVED",
        )
        assert lot.received_on is not None and lot.supplier_lot_no == "MILL-77"
        assert {r.roll_no: (r.primary_milli, r.secondary_milli) for r in db_rolls} == {
            "R-IN-1": (50_000, 9_000),
            "R-IN-2": (32_500, 5_800),
        }
        # each roll is its own bucket, and they add up to the delivery
        total = 0
        for r in db_rolls:
            qty = await bucket(session, org_id, w.fabric, w.t.weaving, lot=lot.id, roll=r.id)
            assert qty == r.primary_milli
            total += qty
        assert total == 82_500
        await assert_clean(session, org_id)

    async def test_rolls_that_do_not_add_up_are_refused_and_create_nothing(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}],
        )
        response = await post(
            client,
            org_id,
            body(
                po_id,
                line(
                    lines["COT-180-NAVY"],
                    "82.5",
                    rolls=[{"primary_qty": "50"}, {"primary_qty": "32.4"}],
                ),
            ),
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "rolls_do_not_add_up"
        assert await count(session, Roll, org_id) == 0
        assert await count(session, Lot, org_id) == 0
        assert await count(session, LedgerEntry, org_id) == 0

    async def test_a_roll_tracked_item_needs_its_rolls_described(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}],
        )
        response = await post(client, org_id, body(po_id, line(lines["COT-180-NAVY"], "50")))
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "required"
        assert response.json()["errors"][0]["field"] == "lines[0].rolls"

    async def test_a_lot_tracked_item_with_no_lot_given_gets_a_generated_one(
        self, client, session, org_id, w
    ) -> None:
        async with session.begin():
            from app.models import InventoryItem

            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.dye)
                .values(track_lot=True, track_quality=False)
            )
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        response = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "4")))
        assert response.status_code == 201, response.text
        row = response.json()["lines"][0]
        assert row["lot_no"].startswith("LOT-") and row["lot_id"]

    async def test_a_second_delivery_can_go_into_the_same_lot(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}],
        )
        first = await post(
            client,
            org_id,
            body(
                po_id,
                line(
                    lines["COT-180-NAVY"],
                    "40",
                    lot={"supplier_lot_no": "MILL-9"},
                    rolls=[{"primary_qty": "40"}],
                ),
            ),
        )
        lot_id = first.json()["lines"][0]["lot_id"]
        second = await post(
            client,
            org_id,
            body(
                po_id,
                line(
                    lines["COT-180-NAVY"],
                    "20",
                    lot={"lot_id": lot_id},
                    rolls=[{"primary_qty": "20"}],
                ),
            ),
        )
        assert second.status_code == 201, second.text
        assert second.json()["lines"][0]["lot_id"] == lot_id
        assert await count(session, Lot, org_id) == 1
        assert await count(session, Roll, org_id) == 2

    async def test_a_lot_of_another_item_is_refused(self, client, session, org_id, w) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                {"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1},
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 1},
            ],
        )
        first = await post(
            client,
            org_id,
            body(po_id, line(lines["COT-180-NAVY"], "10", rolls=[{"primary_qty": "10"}])),
        )
        fabric_lot = first.json()["lines"][0]["lot_id"]
        async with session.begin():
            from app.models import InventoryItem

            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.dye)
                .values(track_lot=True)
            )
        response = await post(
            client,
            org_id,
            body(po_id, line(lines["DYE-RED-01"], "1", lot={"lot_id": fabric_lot})),
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "wrong_item"

    @pytest.mark.parametrize(
        ("extra", "field"),
        [
            ({"rolls": [{"primary_qty": "100"}]}, "lines[0].rolls"),
            ({"lot": {"supplier_lot_no": "X"}}, "lines[0].lot"),
        ],
    )
    async def test_tracking_an_item_does_not_track_is_refused(
        self, client, session, org_id, w, extra, field
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "100", **extra)))
        assert response.status_code == 422
        assert response.json()["errors"][0] == {
            "field": field,
            "code": "not_tracked",
            "message": response.json()["errors"][0]["message"],
        }

    async def test_an_expiry_tracked_item_needs_an_expiry_date_on_a_new_lot(
        self, client, session, org_id, w
    ) -> None:
        async with session.begin():
            from app.models import InventoryItem

            await session.execute(
                update(InventoryItem)
                .where(InventoryItem.organization_id == org_id, InventoryItem.id == w.dye)
                .values(track_lot=True, track_expiry=True, track_quality=False)
            )
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 1}]
        )
        missing = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "5")))
        assert missing.status_code == 422
        assert missing.json()["errors"][0]["field"] == "lines[0].lot.expires_on"
        given = await post(
            client,
            org_id,
            body(
                po_id,
                line(lines["DYE-RED-01"], "5", lot={"expires_on": "2030-01-01"}),
            ),
        )
        assert given.status_code == 201, given.text


class TestQualityHoldAndLocation:
    async def test_a_quality_tracked_item_is_received_into_quality_hold(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90}]
        )
        response = await post(client, org_id, body(po_id, line(lines["DYE-RED-01"], "10")))
        assert response.status_code == 201, response.text
        assert response.json()["lines"][0]["stock_status"] == "QUALITY_HOLD"
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="QUALITY_HOLD") == 10_000
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="AVAILABLE") == 0
        await assert_clean(session, org_id)

    async def test_a_line_can_ask_for_hold_on_an_item_that_does_not_track_quality(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "100", hold=True))
        )
        assert response.json()["lines"][0]["stock_status"] == "QUALITY_HOLD"
        assert await bucket(session, org_id, w.buttons, w.t.weaving, status="QUALITY_HOLD") == (
            100_000
        )

    async def test_a_held_lot_is_pending_inspection_not_approved(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1}],
        )
        await post(
            client,
            org_id,
            body(
                po_id,
                line(lines["COT-180-NAVY"], "10", hold=True, rolls=[{"primary_qty": "10"}]),
            ),
        )
        async with session.begin():
            lot = (
                await session.execute(select(Lot).where(Lot.organization_id == org_id))
            ).scalar_one()
        assert lot.quality_status == "PENDING"

    async def test_each_line_names_its_own_receipt_location(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                buttons_line(100),
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90},
            ],
        )
        response = await post(
            client,
            org_id,
            body(
                po_id,
                line(lines["BTN-14MM"], "100", location_id=str(w.bin_b)),
                line(lines["DYE-RED-01"], "10"),
            ),
        )
        assert response.status_code == 201, response.text
        by_sku = {ln["sku"]: ln for ln in response.json()["lines"]}
        assert by_sku["BTN-14MM"]["location_id"] == str(w.bin_b)
        assert by_sku["BTN-14MM"]["location_label"].endswith("/BIN-B")
        assert by_sku["DYE-RED-01"]["location_id"] == str(w.t.weaving)
        assert await bucket(session, org_id, w.buttons, w.bin_b) == 100_000
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 0

    async def test_a_location_of_another_tenant_is_a_404(
        self, client, session, org_id, w, admin_session
    ) -> None:
        other = await build_rworld(admin_session, uuid.uuid4())
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        response = await post(
            client,
            org_id,
            body(po_id, line(lines["BTN-14MM"], "100", location_id=str(other.bin_b))),
        )
        assert response.status_code == 404
        assert await count(session, GoodsReceipt, org_id) == 0


class TestServiceLinesAndPoState:
    async def test_a_line_with_no_stocked_item_is_accounted_but_posts_no_stock(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                buttons_line(10),
                {"sku": "SVC-FREIGHT", "quantity": 1, "uom": "", "unit_price_minor": 5_000_00},
            ],
        )
        response = await post(
            client,
            org_id,
            body(po_id, line(lines["BTN-14MM"], "10"), line(lines["SVC-FREIGHT"], "1")),
        )
        assert response.status_code == 201, response.text
        by_sku = {ln["sku"]: ln for ln in response.json()["lines"]}
        assert by_sku["SVC-FREIGHT"]["inventory_item_id"] is None
        assert by_sku["SVC-FREIGHT"]["location_id"] is None
        assert by_sku["SVC-FREIGHT"]["outcome"] == "EXACT"
        assert response.json()["po_status"] == "received"
        assert await count(session, LedgerEntry, org_id) == 1

    async def test_a_service_line_takes_no_location_lot_or_rolls(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "SVC-FREIGHT", "quantity": 1, "uom": "", "unit_price_minor": 1}]
        )
        response = await post(
            client,
            org_id,
            body(po_id, line(lines["SVC-FREIGHT"], "1", location_id=str(w.bin_b))),
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "not_stocked"

    @pytest.mark.parametrize("status", ["draft", "received", "closed", "cancelled"])
    async def test_a_po_that_is_not_open_for_receiving_is_a_409(
        self, client, session, org_id, w, status
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)], status=status)
        response = await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))
        assert response.status_code == 409
        if status == "draft":
            assert "approved" in response.json()["detail"]
        assert await count(session, GoodsReceipt, org_id) == 0

    async def test_a_po_line_of_another_po_is_a_404(self, client, session, org_id, w) -> None:
        po_a, _ = await make_po(session, w, [buttons_line(10)])
        _, lines_b = await make_po(session, w, [buttons_line(10)])
        response = await post(client, org_id, body(po_a, line(lines_b["BTN-14MM"], "10")))
        assert response.status_code == 404

    async def test_receiving_closes_out_the_suppliers_open_po_count_exactly_once(
        self, client, session, org_id, w
    ) -> None:
        async with session.begin():
            await session.execute(
                update(Supplier)
                .where(Supplier.organization_id == org_id, Supplier.id == w.supplier_id)
                .values(open_pos=3)
            )
        po_id, lines = await make_po(session, w, [buttons_line(100)])

        async def open_pos() -> int:
            async with session.begin():
                return (
                    await session.execute(
                        select(Supplier.open_pos).where(Supplier.id == w.supplier_id)
                    )
                ).scalar_one()

        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "50")))
        assert await open_pos() == 3  # still open
        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "50")))
        assert await open_pos() == 2

    async def test_the_receipt_is_immutable(self, client, session, org_id, w) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)])
        grn = (await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))).json()
        from sqlalchemy.exc import DBAPIError

        for model in (GoodsReceipt, GoodsReceiptLine):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin():
                    await session.execute(
                        update(model)
                        .where(model.organization_id == org_id)
                        .values(created_at=func.now())
                    )
        assert grn["id"]


class TestIdempotency:
    async def test_a_retry_with_the_same_key_replays_and_books_nothing_more(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        payload = body(po_id, line(lines["BTN-14MM"], "40"))
        first = await post(client, org_id, payload, key="grn-key-1")
        second = await post(client, org_id, payload, key="grn-key-1")
        assert (first.status_code, second.status_code) == (201, 200)
        assert second.json()["replayed"] is True and first.json()["replayed"] is False
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["number"] == first.json()["number"]
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 40_000
        assert await count(session, GoodsReceipt, org_id) == 1
        assert await po_line_state(session, org_id, lines["BTN-14MM"]) == (40_000, 40, False)
        async with session.begin():
            events = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "operations.stock.received")
                )
            ).scalar_one()
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "goods_receipt.posted")
                )
            ).scalar_one()
        assert (events, audits) == (1, 1)

    async def test_the_same_key_for_a_different_request_is_a_409(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "40")), key="grn-key-2")
        clash = await post(
            client, org_id, body(po_id, line(lines["BTN-14MM"], "41")), key="grn-key-2"
        )
        assert clash.status_code == 409
        assert clash.json()["errors"][0]["code"] == "idempotency_key_reused"
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 40_000

    async def test_a_replay_still_answers_after_the_po_has_been_completed(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)])
        payload = body(po_id, line(lines["BTN-14MM"], "10"))
        first = await post(client, org_id, payload, key="grn-key-3")
        assert first.json()["po_status"] == "received"
        again = await post(client, org_id, payload, key="grn-key-3")
        assert again.status_code == 200 and again.json()["id"] == first.json()["id"]

    async def test_without_a_key_two_posts_are_two_receipts(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        payload = body(po_id, line(lines["BTN-14MM"], "10"))
        assert (await post(client, org_id, payload)).status_code == 201
        assert (await post(client, org_id, payload)).status_code == 201
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 20_000


class TestEventsAndReads:
    async def test_the_received_event_matches_its_contract(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                buttons_line(100),
                {"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1},
                {"sku": "SVC-FREIGHT", "quantity": 1, "uom": "", "unit_price_minor": 1},
            ],
        )
        grn = await post(
            client,
            org_id,
            body(
                po_id,
                line(lines["BTN-14MM"], "30"),
                line(lines["COT-180-NAVY"], "10", rolls=[{"primary_qty": "10"}]),
                line(lines["SVC-FREIGHT"], "1"),
            ),
        )
        assert grn.status_code == 201, grn.text
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "operations.stock.received")
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        payload = event.payload
        assert payload["goods_receipt_id"] == grn.json()["id"]
        assert payload["po_status"] == "partially_received"
        by_sku = {ln["sku"]: ln for ln in payload["lines"]}
        assert by_sku["BTN-14MM"]["outcome"] == "PARTIAL"
        assert len(by_sku["COT-180-NAVY"]["roll_ids"]) == 1
        assert by_sku["SVC-FREIGHT"]["inventory_item_id"] is None

    async def test_completing_the_po_also_emits_the_legacy_received_event(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(10)])
        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "10")))
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.procurement.purchase_order.received"
                    )
                )
            ).scalar_one()
        assert event.payload["matched_lines"][0]["sku"] == "BTN-14MM"
        assert event.payload["matched_lines"][0]["quantity"] == 10
        assert event.payload["matched_lines"][0]["quantity_exact"] == "10"

    async def test_list_filters_and_detail(self, client, session, org_id, w) -> None:
        po_a, lines_a = await make_po(session, w, [buttons_line(100)])
        po_b, lines_b = await make_po(session, w, [buttons_line(100)])
        a = (
            await post(
                client,
                org_id,
                body(
                    po_a,
                    line(lines_a["BTN-14MM"], "10"),
                ),
            )
        ).json()
        await post(client, org_id, body(po_b, line(lines_b["BTN-14MM"], "10")))
        listing = await get(client, org_id, GRN)
        assert listing.status_code == 200 and len(listing.json()["items"]) == 2
        only_a = await get(client, org_id, f"{GRN}?purchase_order_id={po_a}")
        assert [r["id"] for r in only_a.json()["items"]] == [a["id"]]
        assert only_a.json()["items"][0]["line_count"] == 1
        by_number = await get(client, org_id, f"{GRN}?q={a['number'].lower()}")
        assert [r["id"] for r in by_number.json()["items"]] == [a["id"]]
        detail = await get(client, org_id, f"{GRN}/{a['id']}")
        assert detail.status_code == 200 and detail.json()["po_number"]
        assert detail.json()["supplier_name"] == "Textile Mills Ltd"
        assert detail.json()["lines"][0]["sku"] == "BTN-14MM"

    async def test_the_list_pages_by_cursor(self, client, session, org_id, w) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(1000)])
        for _ in range(3):
            await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "1")))
        first = await get(client, org_id, f"{GRN}?limit=2")
        assert len(first.json()["items"]) == 2 and first.json()["next_cursor"]
        rest = await get(client, org_id, f"{GRN}?limit=2&cursor={first.json()['next_cursor']}")
        assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None


class TestReconcilerStaysClean:
    async def test_a_mixed_series_of_receipts_leaves_zero_drift(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                buttons_line(120),
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 90},
                {"sku": "COT-180-NAVY", "quantity": 100, "uom": "m", "unit_price_minor": 1},
            ],
        )
        steps = [
            line(lines["BTN-14MM"], "3", uom="box"),
            line(lines["DYE-RED-01"], "4.25"),
            line(
                lines["COT-180-NAVY"],
                "50.5",
                lot={"supplier_lot_no": "M-1"},
                rolls=[{"primary_qty": "30.25"}, {"primary_qty": "20.25"}],
            ),
            line(lines["BTN-14MM"], "84"),
            line(lines["BTN-14MM"], "0", close_short=True),
        ]
        for step in steps[:-1]:
            response = await post(client, org_id, body(po_id, step))
            assert response.status_code == 201, response.text
        await assert_clean(session, org_id)
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 120_000
        assert format_qty(
            await bucket(session, org_id, w.dye, w.t.weaving, status="QUALITY_HOLD")
        ) == ("4.25")


class TestLegacyEndpointIsAWrapper:
    async def test_the_single_shot_receive_after_a_partial_receives_only_the_rest(
        self, client, session, org_id, w
    ) -> None:
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        await post(client, org_id, body(po_id, line(lines["BTN-14MM"], "40")))
        with as_principal(principal_for(org_id, LEGACY_RECEIVE)):
            received = await client.post(f"{API}/operations/purchase-orders/{po_id}/receive")
        assert received.status_code == 200, received.text
        assert received.json()["status"] == "received"
        assert await bucket(session, org_id, w.buttons, w.t.weaving) == 100_000
        assert await po_line_state(session, org_id, lines["BTN-14MM"]) == (100_000, 100, False)
        # one receipt per post: the partial one, and the legacy one for the remaining 60
        assert await count(session, GoodsReceipt, org_id) == 2
        await assert_clean(session, org_id)

    async def test_the_single_shot_receive_gives_tracked_items_a_lot_and_a_roll(
        self, client, session, org_id, w
    ) -> None:
        po_id, _ = await make_po(
            session, w, [{"sku": "COT-180-NAVY", "quantity": 40, "uom": "m", "unit_price_minor": 1}]
        )
        with as_principal(principal_for(org_id, LEGACY_RECEIVE)):
            received = await client.post(f"{API}/operations/purchase-orders/{po_id}/receive")
        assert received.status_code == 200, received.text
        assert await count(session, Lot, org_id) == 1
        assert await count(session, Roll, org_id) == 1
        await assert_clean(session, org_id)

    async def test_the_single_shot_receive_routes_a_quality_tracked_item_to_hold(
        self, client, session, org_id, w
    ) -> None:
        po_id, _ = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 5, "uom": "kg", "unit_price_minor": 1}]
        )
        with as_principal(principal_for(org_id, LEGACY_RECEIVE)):
            received = await client.post(f"{API}/operations/purchase-orders/{po_id}/receive")
        assert received.status_code == 200
        assert await bucket(session, org_id, w.dye, w.t.weaving, status="QUALITY_HOLD") == 5_000


async def test_an_item_read_permission_alone_cannot_see_receipts(
    client, session, org_id, w
) -> None:
    with as_principal(principal_for(org_id, [ITEM_READ])):
        response = await client.get(API + GRN)
    assert response.status_code == 403


class TestReceivedOnClock:
    """The receipt date is an Indian date: one day ahead is tolerated, the distant past is not."""

    async def test_a_date_one_day_ahead_of_the_ist_clock_is_accepted_two_is_not(
        self, client, session, org_id, w, monkeypatch
    ) -> None:
        from datetime import date, timedelta

        from app.jobwork import domain

        ist = date.today() + timedelta(days=10)
        monkeypatch.setattr(domain, "ist_today", lambda now=None: ist)
        po_id, lines = await make_po(session, w, [buttons_line(100)])
        too_far = await post(
            client,
            org_id,
            body(po_id, line(lines["BTN-14MM"], "10"), received_on=str(ist + timedelta(days=2))),
        )
        assert too_far.status_code == 422, too_far.text
        # Not before the order existed: the PO was raised today, so a receipt in 2001 is refused.
        ancient = await post(
            client,
            org_id,
            body(po_id, line(lines["BTN-14MM"], "10"), received_on="2001-01-01"),
        )
        assert ancient.status_code == 422, ancient.text
        ok = await post(
            client,
            org_id,
            body(po_id, line(lines["BTN-14MM"], "10"), received_on=str(ist + timedelta(days=1))),
        )
        assert ok.status_code == 201, ok.text
