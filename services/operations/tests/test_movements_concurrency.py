"""Phase 3 movements racing each other on real connections (ADR-0014 §3; plan Phase 3 exit
criteria): 50 parallel reservations against 10 units reserve exactly 10, two concurrent dispatches
of one transfer have one winner, a dispatch and an issue on one bin can never take it below zero,
a double receipt cannot receive twice, a redelivered approval posts once — and the reconciler finds
zero drift after every interleaving.

Each worker is its own connection and transaction as the NOBYPASSRLS app role, started together
with `asyncio.gather` — no application lock anywhere, only the transfer row lock and the kernel's
bucket locks.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.models import (
    SCHEMA,
    AdjustmentRequest,
    InventoryMove,
    InventoryReservation,
    StockTransfer,
)
from app.movements import adjustments, reservations, transfers
from app.movements.schemas import (
    AdjustmentIn,
    ReservationIn,
    TransferDecisionIn,
    TransferDispatchIn,
    TransferIn,
    TransferLineIn,
    TransferReceiveIn,
    TransferReceiveLineIn,
)
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for
from tests.inventory_fixtures import TODAY, q
from tests.movements_fixtures import World, build_world, in_transit_total, receive_stock
from tests.tracking_fixtures import assert_reconciled, bucket_qty

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def world(session, org_id) -> World:
    w = await build_world(session, org_id)
    await receive_stock(session, w, w.t.dye, w.t.weaving, "100")
    return w


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=60,
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


def ctx(org_id: uuid.UUID, permission: str):  # type: ignore[no-untyped-def]
    return ctx_for(org_id, permission, Scope.ORG, subject_ref=uuid.uuid4())


async def approved_transfer(session, w: World, quantity: str = "40") -> uuid.UUID:  # type: ignore[no-untyped-def]
    """A transfer taken to APPROVED, each step as a different person, in committed transactions."""
    org = w.org_id
    async with session.begin():
        transfer, _ = await transfers.create_transfer(
            session,
            ctx(org, "operations.inventory.transfer.write"),
            TransferIn(
                from_warehouse_id=w.warehouse_weave,
                to_warehouse_id=w.warehouse_fin,
                lines=[
                    TransferLineIn(
                        inventory_item_id=w.t.dye, from_location_id=w.t.weaving, quantity=quantity
                    )
                ],
            ),
            idempotency_key=None,
        )
        transfer_id = transfer.id
    async with session.begin():
        await transfers.request_transfer(
            session, ctx(org, "operations.inventory.transfer.write"), transfer_id
        )
    async with session.begin():
        await transfers.approve_transfer(
            session,
            ctx(org, "operations.inventory.transfer.approve"),
            transfer_id,
            TransferDecisionIn(),
        )
    return transfer_id


class TestReservationsNeverOverCommit:
    async def test_fifty_parallel_reservations_against_ten_units_reserve_exactly_ten(
        self, session, maker, org_id, world
    ) -> None:
        # Clear the fixture's 100 down to exactly 10 free units.
        async with session.begin():
            await ledger.post_move(
                session,
                world.t.ctx,
                kind="issue",
                lines=[MoveLine(world.t.dye, world.t.weaving, -q("90"))],
                occurred_on=TODAY,
            )

        def reserve(_n: int) -> Work:
            async def work(s: AsyncSession) -> None:
                await reservations.create_reservation(
                    s,
                    ctx(org_id, "operations.inventory.stock.reserve"),
                    ReservationIn(
                        inventory_item_id=world.t.dye, location_id=world.t.weaving, quantity="1"
                    ),
                    idempotency_key=None,
                )

            return work

        results = await race(maker, org_id, [reserve(n) for n in range(50)])
        assert Counter(results) == {"ok": 10, "InsufficientStock": 40}
        assert await bucket_qty(session, world, item=world.t.dye) == (q("10"), q("10"))
        async with session.begin():
            held = (
                await session.execute(
                    select(func.count())
                    .select_from(InventoryReservation)
                    .where(
                        InventoryReservation.organization_id == org_id,
                        InventoryReservation.status == "active",
                    )
                )
            ).scalar_one()
        assert held == 10
        await assert_reconciled(session, org_id)

    async def test_mixed_sizes_never_exceed_what_is_there(
        self, session, maker, org_id, world
    ) -> None:
        sizes = ["30", "25", "25", "20", "15", "10", "10", "5", "5", "5", "40", "60"]

        def reserve(size: str) -> Work:
            async def work(s: AsyncSession) -> None:
                await reservations.create_reservation(
                    s,
                    ctx(org_id, "operations.inventory.stock.reserve"),
                    ReservationIn(
                        inventory_item_id=world.t.dye, location_id=world.t.weaving, quantity=size
                    ),
                    idempotency_key=None,
                )

            return work

        results = await race(maker, org_id, [reserve(s) for s in sizes])
        assert set(results) <= {"ok", "InsufficientStock"}
        on_hand, reserved = await bucket_qty(session, world, item=world.t.dye)
        assert on_hand == q("100") and reserved <= on_hand
        await assert_reconciled(session, org_id)

    async def test_ten_retries_of_one_keyed_reservation_reserve_once(
        self, session, maker, org_id, world
    ) -> None:
        def reserve() -> Work:
            async def work(s: AsyncSession) -> None:
                await reservations.create_reservation(
                    s,
                    ctx(org_id, "operations.inventory.stock.reserve"),
                    ReservationIn(
                        inventory_item_id=world.t.dye, location_id=world.t.weaving, quantity="30"
                    ),
                    idempotency_key="one-request",
                )

            return work

        assert set(await race(maker, org_id, [reserve() for _ in range(10)])) == {"ok"}
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("30"))
        await assert_reconciled(session, org_id)

    async def test_expiry_racing_a_consumption_never_double_counts(
        self, session, maker, org_id, world
    ) -> None:
        from datetime import UTC, datetime, timedelta

        async with session.begin():
            made = await ledger.reserve(
                session,
                world.t.ctx,
                item_id=world.t.dye,
                location_id=world.t.weaving,
                quantity_milli=q("30"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            rid = made.reservation.id
            from sqlalchemy import update

            await session.execute(
                update(InventoryReservation)
                .where(InventoryReservation.id == rid)
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )

        async def expire(s: AsyncSession) -> None:
            await reservations.expire_due(s, org_id)

        async def consume(s: AsyncSession) -> None:
            await ledger.post_move(
                s,
                world.t.ctx,
                kind="issue",
                lines=[MoveLine(world.t.dye, world.t.weaving, -q("30"), reservation_id=rid)],
                occurred_on=TODAY,
            )

        results = await race(maker, org_id, [expire, consume, expire])
        assert set(results) <= {"ok", "ConflictProblem", "InsufficientStock"}
        on_hand, reserved = await bucket_qty(session, world, item=world.t.dye)
        assert reserved == 0
        assert on_hand in (q("100"), q("70"))
        await assert_reconciled(session, org_id)


class TestDispatchRaces:
    async def test_two_simultaneous_dispatches_of_one_transfer_have_one_winner(
        self, session, maker, org_id, world
    ) -> None:
        transfer_id = await approved_transfer(session, world, "40")

        def dispatch() -> Work:
            async def work(s: AsyncSession) -> None:
                await transfers.dispatch_transfer(
                    s,
                    ctx(org_id, "operations.inventory.transfer.dispatch"),
                    transfer_id,
                    TransferDispatchIn(),
                    idempotency_key=None,
                )

            return work

        results = await race(maker, org_id, [dispatch(), dispatch()])
        assert Counter(results) == {"ok": 1, "ConflictProblem": 1}
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("60")
        assert await in_transit_total(session, org_id, world.t.dye) == q("40")
        async with session.begin():
            moves = (
                await session.execute(
                    select(func.count())
                    .select_from(InventoryMove)
                    .where(
                        InventoryMove.organization_id == org_id,
                        InventoryMove.reference_type == "stock_transfer",
                    )
                )
            ).scalar_one()
        assert moves == 1
        await assert_reconciled(session, org_id)

    async def test_ten_simultaneous_dispatches_still_move_the_stock_once(
        self, session, maker, org_id, world
    ) -> None:
        transfer_id = await approved_transfer(session, world, "40")

        def dispatch() -> Work:
            async def work(s: AsyncSession) -> None:
                await transfers.dispatch_transfer(
                    s,
                    ctx(org_id, "operations.inventory.transfer.dispatch"),
                    transfer_id,
                    TransferDispatchIn(),
                    idempotency_key=None,
                )

            return work

        results = await race(maker, org_id, [dispatch() for _ in range(10)])
        assert Counter(results) == {"ok": 1, "ConflictProblem": 9}
        assert await in_transit_total(session, org_id, world.t.dye) == q("40")
        await assert_reconciled(session, org_id)

    async def test_a_dispatch_and_an_issue_on_one_bin_never_go_negative(
        self, session, maker, org_id, world
    ) -> None:
        transfer_id = await approved_transfer(session, world, "80")

        async def dispatch(s: AsyncSession) -> None:
            await transfers.dispatch_transfer(
                s,
                ctx(org_id, "operations.inventory.transfer.dispatch"),
                transfer_id,
                TransferDispatchIn(),
                idempotency_key=None,
            )

        async def issue(s: AsyncSession) -> None:
            await ledger.post_move(
                s,
                world.t.ctx,
                kind="issue",
                lines=[MoveLine(world.t.dye, world.t.weaving, -q("80"))],
                occurred_on=TODAY,
            )

        results = await race(maker, org_id, [dispatch, issue])
        assert Counter(results) == {"ok": 1, "InsufficientStock": 1}
        on_hand = (await bucket_qty(session, world, item=world.t.dye))[0]
        assert on_hand == q("20")
        await assert_reconciled(session, org_id)


class TestReceiptRaces:
    async def test_two_receivers_cannot_both_receive_the_same_stock(
        self, session, maker, org_id, world
    ) -> None:
        transfer_id = await approved_transfer(session, world, "40")
        async with session.begin():
            dispatched, _ = await transfers.dispatch_transfer(
                session,
                ctx(org_id, "operations.inventory.transfer.dispatch"),
                transfer_id,
                TransferDispatchIn(),
                idempotency_key=None,
            )
            lines = await transfers.sel.transfer_lines(session, org_id, dispatched.id)
            line_id = lines[0].id

        def receive() -> Work:
            async def work(s: AsyncSession) -> None:
                await transfers.receive_transfer(
                    s,
                    ctx(org_id, "operations.inventory.transfer.receive"),
                    transfer_id,
                    TransferReceiveIn(
                        lines=[TransferReceiveLineIn(line_id=line_id, received_quantity="40")]
                    ),
                    idempotency_key=None,
                )

            return work

        results = await race(maker, org_id, [receive(), receive(), receive()])
        assert Counter(results) == {"ok": 1, "ConflictProblem": 2}
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == q("40")
        async with session.begin():
            status = (
                await session.execute(
                    select(StockTransfer.status).where(StockTransfer.id == transfer_id)
                )
            ).scalar_one()
        assert status == "RECEIVED"
        await assert_reconciled(session, org_id)


class TestNumbersAndDecisions:
    async def test_twenty_concurrent_drafts_get_twenty_distinct_numbers(
        self, session, maker, org_id, world
    ) -> None:
        def draft() -> Work:
            async def work(s: AsyncSession) -> None:
                await transfers.create_transfer(
                    s,
                    ctx(org_id, "operations.inventory.transfer.write"),
                    TransferIn(
                        from_warehouse_id=world.warehouse_weave,
                        to_warehouse_id=world.warehouse_fin,
                        lines=[
                            TransferLineIn(
                                inventory_item_id=world.t.dye,
                                from_location_id=world.t.weaving,
                                quantity="1",
                            )
                        ],
                    ),
                    idempotency_key=None,
                )

            return work

        assert set(await race(maker, org_id, [draft() for _ in range(20)])) == {"ok"}
        async with session.begin():
            numbers = (
                (
                    await session.execute(
                        select(StockTransfer.number).where(StockTransfer.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(numbers) == len(set(numbers)) == 20

    async def test_one_idempotency_key_raced_creates_one_transfer(
        self, session, maker, org_id, world
    ) -> None:
        author = ctx(org_id, "operations.inventory.transfer.write")  # one person, retrying

        def draft() -> Work:
            async def work(s: AsyncSession) -> None:
                await transfers.create_transfer(
                    s,
                    author,
                    TransferIn(
                        from_warehouse_id=world.warehouse_weave,
                        to_warehouse_id=world.warehouse_fin,
                        lines=[
                            TransferLineIn(
                                inventory_item_id=world.t.dye,
                                from_location_id=world.t.weaving,
                                quantity="1",
                            )
                        ],
                    ),
                    idempotency_key="same-draft",
                )

            return work

        assert set(await race(maker, org_id, [draft() for _ in range(8)])) == {"ok"}
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(StockTransfer)
                    .where(StockTransfer.organization_id == org_id)
                )
            ).scalar_one()
        assert count == 1

    async def test_a_decision_delivered_three_times_at_once_posts_the_adjustment_once(
        self, session, maker, org_id, world
    ) -> None:
        threshold_ctx = ctx(org_id, "operations.inventory.stock.adjust")
        async with session.begin():
            from app.movements import common

            await common.set_thresholds(
                session, threshold_ctx, transfer_milli=None, adjustment_milli=q("20")
            )
        async with session.begin():
            row, _ = await adjustments.create_adjustment(
                session,
                threshold_ctx,
                AdjustmentIn(
                    inventory_item_id=world.t.dye,
                    location_id=world.t.weaving,
                    quantity_delta="-25",
                    reason="damaged",
                    evidence_document_id=uuid.uuid4(),
                ),
                idempotency_key=None,
            )
            adjustment_id = row.id
            assert row.status == "PENDING_APPROVAL"

        def decide() -> Work:
            async def work(s: AsyncSession) -> None:
                await adjustments.apply_approval_decision(
                    s,
                    org_id,
                    adjustment_id,
                    outcome="approved",
                    approval_request_id=uuid.uuid4(),
                    decided_by_name="Meera Shah",
                    comment=None,
                )

            return work

        assert set(await race(maker, org_id, [decide(), decide(), decide()])) == {"ok"}
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("75")
        async with session.begin():
            applied = (
                await session.execute(
                    select(func.count())
                    .select_from(AdjustmentRequest)
                    .where(
                        AdjustmentRequest.organization_id == org_id,
                        AdjustmentRequest.status == "APPLIED",
                    )
                )
            ).scalar_one()
            moves = (
                await session.execute(
                    select(func.count())
                    .select_from(InventoryMove)
                    .where(InventoryMove.reference_type == "adjustment_request")
                )
            ).scalar_one()
        assert (applied, moves) == (1, 1)
        await assert_reconciled(session, org_id)
