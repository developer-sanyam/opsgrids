"""Goods receipts racing each other on real connections (ADR-0014 §3; inventory Phase 2): two
deliveries against one PO line can never both squeeze under the tolerance and together exceed it,
a retried `Idempotency-Key` posts once however many copies arrive at the same moment, the
single-shot receive and a goods receipt cannot over-receive between them, and the reconciler finds
zero drift after every interleaving.

Each worker is its own connection and transaction as the NOBYPASSRLS app role, started together
with `asyncio.gather` — no application lock anywhere, only the PO row lock and the kernel's bucket
locks.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA, GoodsReceipt, InventoryBalance, PurchaseOrder, PurchaseOrderLine
from app.receiving import receipts
from app.receiving.schemas import GoodsReceiptIn, ReceiptLineIn
from platform_core.db import TENANT_GUC
from tests.receiving_fixtures import RWorld, build_rworld, make_po, po_line_state

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def w(session, org_id) -> RWorld:
    return await build_rworld(session, org_id)


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=30,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def race(maker, org_id: uuid.UUID, works: list[Work]) -> list[str]:  # type: ignore[no-untyped-def]
    """Every `work` in its own tenant-bound transaction, all at once: `ok`, or the class name of
    what it raised."""

    async def one(work: Work) -> str:
        async with maker() as s:

            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            try:
                async with s.begin():
                    await work(s)
            except Exception as exc:
                return type(exc).__name__
            return "ok"

    return list(await asyncio.gather(*(one(w) for w in works)))


def receive(
    w: RWorld, po_id: uuid.UUID, line_id: uuid.UUID, qty: str, key: str | None = None
) -> Work:
    async def work(s: AsyncSession) -> None:
        await receipts.post_goods_receipt(
            s,
            w.t.ctx,
            GoodsReceiptIn(
                purchase_order_id=po_id,
                received_on=date.today(),
                lines=[ReceiptLineIn(purchase_order_line_id=line_id, quantity=qty)],
            ),
            idempotency_key=key,
        )

    return work


async def assert_reconciled(session, org_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.clean, report.findings


async def stock(session, org_id: uuid.UUID, item: uuid.UUID, status: str = "AVAILABLE") -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0)).where(
                        InventoryBalance.organization_id == org_id,
                        InventoryBalance.inventory_item_id == item,
                        InventoryBalance.status == status,
                    )
                )
            ).scalar_one()
        )


class TestToleranceRaces:
    async def test_two_receipts_that_together_exceed_the_order_cannot_both_post(
        self, session, maker, org_id, w
    ) -> None:
        # dye: zero tolerance, 100 kg ordered; two simultaneous deliveries of 60
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 100, "uom": "kg", "unit_price_minor": 1}]
        )
        line_id = lines["DYE-RED-01"]
        results = await race(
            maker, org_id, [receive(w, po_id, line_id, "60"), receive(w, po_id, line_id, "60")]
        )
        assert sorted(results) == ["ValidationProblem", "ok"], results
        assert await po_line_state(session, org_id, line_id) == (60_000, 60, False)
        assert await stock(session, org_id, w.dye, "QUALITY_HOLD") == 60_000
        await assert_reconciled(session, org_id)

    async def test_many_simultaneous_receipts_never_pass_the_ordered_quantity(
        self, session, maker, org_id, w
    ) -> None:
        # ten deliveries of 15 against 100 ordered, zero tolerance: six fit (90), four do not
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 100, "uom": "kg", "unit_price_minor": 1}]
        )
        line_id = lines["DYE-RED-01"]
        results = Counter(
            await race(maker, org_id, [receive(w, po_id, line_id, "15") for _ in range(10)])
        )
        assert results == Counter({"ok": 6, "ValidationProblem": 4}), results
        assert await po_line_state(session, org_id, line_id) == (90_000, 90, False)
        assert await stock(session, org_id, w.dye, "QUALITY_HOLD") == 90_000
        async with session.begin():
            posted = (
                await session.execute(
                    select(func.count())
                    .select_from(GoodsReceipt)
                    .where(GoodsReceipt.organization_id == org_id)
                )
            ).scalar_one()
        assert posted == 6
        await assert_reconciled(session, org_id)

    async def test_the_tolerance_limit_holds_across_racing_receipts(
        self, session, maker, org_id, w
    ) -> None:
        # buttons: 5 % tolerance, 100 ordered -> at most 105; four deliveries of 30 -> three fit
        po_id, lines = await make_po(
            session,
            w,
            [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 1}],
        )
        line_id = lines["BTN-14MM"]
        results = Counter(
            await race(maker, org_id, [receive(w, po_id, line_id, "30") for _ in range(4)])
        )
        assert results == Counter({"ok": 3, "ValidationProblem": 1}), results
        received, _, _ = await po_line_state(session, org_id, line_id)
        assert received == 90_000 <= 105_000
        await assert_reconciled(session, org_id)

    async def test_receipts_against_different_lines_of_one_po_all_post(
        self, session, maker, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session,
            w,
            [
                {"sku": "DYE-RED-01", "quantity": 10, "uom": "kg", "unit_price_minor": 1},
                {"sku": "BTN-14MM", "quantity": 10, "uom": "pcs", "unit_price_minor": 1},
            ],
        )
        results = await race(
            maker,
            org_id,
            [
                receive(w, po_id, lines["DYE-RED-01"], "10"),
                receive(w, po_id, lines["BTN-14MM"], "10"),
            ],
        )
        assert results == ["ok", "ok"]
        async with session.begin():
            status = (
                await session.execute(select(PurchaseOrder.status).where(PurchaseOrder.id == po_id))
            ).scalar_one()
        assert status == "received"
        await assert_reconciled(session, org_id)


class TestIdempotencyRaces:
    async def test_the_same_key_arriving_five_times_at_once_posts_once(
        self, session, maker, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 1}]
        )
        line_id = lines["BTN-14MM"]
        results = await race(
            maker, org_id, [receive(w, po_id, line_id, "20", key="same-key") for _ in range(5)]
        )
        assert results == ["ok"] * 5, results  # four replays, one post — none an error
        async with session.begin():
            posted = (
                await session.execute(
                    select(func.count())
                    .select_from(GoodsReceipt)
                    .where(GoodsReceipt.organization_id == org_id)
                )
            ).scalar_one()
        assert posted == 1
        assert await po_line_state(session, org_id, line_id) == (20_000, 20, False)
        assert await stock(session, org_id, w.buttons) == 20_000
        await assert_reconciled(session, org_id)

    async def test_a_replay_of_a_receipt_that_completed_the_po_is_still_a_replay(
        self, session, maker, org_id, w
    ) -> None:
        """The winner makes the PO `received`; the copies that waited for its lock must replay it,
        not answer 409 "already received"."""
        po_id, lines = await make_po(
            session, w, [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 1}]
        )
        line_id = lines["BTN-14MM"]
        results = await race(
            maker, org_id, [receive(w, po_id, line_id, "100", key="done-key") for _ in range(5)]
        )
        assert results == ["ok"] * 5, results
        assert await po_line_state(session, org_id, line_id) == (100_000, 100, False)
        assert await stock(session, org_id, w.buttons) == 100_000
        async with session.begin():
            status = (
                await session.execute(select(PurchaseOrder.status).where(PurchaseOrder.id == po_id))
            ).scalar_one()
        assert status == "received"
        await assert_reconciled(session, org_id)

    async def test_different_keys_are_different_receipts(self, session, maker, org_id, w) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "BTN-14MM", "quantity": 100, "uom": "pcs", "unit_price_minor": 1}]
        )
        line_id = lines["BTN-14MM"]
        results = await race(
            maker, org_id, [receive(w, po_id, line_id, "10", key=f"k-{i}") for i in range(4)]
        )
        assert results == ["ok"] * 4
        assert await stock(session, org_id, w.buttons) == 40_000
        await assert_reconciled(session, org_id)


class TestLegacyReceiveRace:
    async def test_the_single_shot_receive_and_a_receipt_cannot_over_receive_together(
        self, session, maker, org_id, w
    ) -> None:
        po_id, lines = await make_po(
            session, w, [{"sku": "DYE-RED-01", "quantity": 100, "uom": "kg", "unit_price_minor": 1}]
        )
        line_id = lines["DYE-RED-01"]

        async def legacy(s: AsyncSession) -> None:
            po = (
                await s.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
            ).scalar_one()
            await receipts.receive_whole_purchase_order(
                s, w.t.ctx, po, received_date=None, request=None, unmatched_out=None
            )

        results = await race(maker, org_id, [legacy, receive(w, po_id, line_id, "60")])
        received, _, _ = await po_line_state(session, org_id, line_id)
        # whichever won, the order never holds more than was ordered, and stock equals the books
        assert received <= 100_000, results
        assert await stock(session, org_id, w.dye, "QUALITY_HOLD") == received
        assert results.count("ok") >= 1
        async with session.begin():
            line = (
                await session.execute(
                    select(PurchaseOrderLine).where(PurchaseOrderLine.id == line_id)
                )
            ).scalar_one()
        assert line.received_milli == received
        await assert_reconciled(session, org_id)
