"""Roll and stock operations racing each other on real connections (ADR-0014 §3; inventory
Phase 1 exit criteria): two simultaneous splits of one roll leave exactly one winner, a transfer
and an issue on one bin can never take it below zero, and the reconciler finds zero drift after
every interleaving.

Each worker is its own connection and transaction as the NOBYPASSRLS app role, started together
with `asyncio.gather` — no application lock anywhere, only the roll row lock and the kernel's
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
from app.inventory import ledger, stock_ops
from app.inventory import tracking_services as tracking
from app.inventory.ledger import MoveLine
from app.inventory.tracking_schemas import (
    OpeningLineIn,
    OpeningStockIn,
    RollSplitIn,
    SplitChildIn,
    StockTransferIn,
)
from app.models import SCHEMA, Roll
from platform_core.db import TENANT_GUC
from tests.inventory_fixtures import TODAY, q
from tests.tracking_fixtures import World, assert_reconciled, bucket_qty, build_world

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


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


def split(w: World, *quantities: str, key: str | None = None) -> Work:
    async def work(s: AsyncSession) -> None:
        await tracking.split_roll(
            s,
            w.t.ctx,
            w.roll,
            RollSplitIn(
                location_id=w.t.weaving,
                children=[SplitChildIn(quantity=x) for x in quantities],
            ),
            idempotency_key=key,
            occurred_on_explicit=False,
        )

    return work


async def roll_count(session, org_id: uuid.UUID) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Roll).where(Roll.organization_id == org_id)
                )
            ).scalar_one()
        )


class TestSplitRaces:
    async def test_two_simultaneous_splits_of_one_roll_leave_exactly_one_winner(
        self, maker, session, org_id, world
    ) -> None:
        outcomes = await race(
            maker, org_id, [split(world, "50", "32.5"), split(world, "40", "42.5")]
        )
        assert Counter(outcomes) == {"ok": 1, "ConflictProblem": 1}, outcomes
        assert await roll_count(session, org_id) == 3  # the parent and one set of two children
        assert (await bucket_qty(session, world, lot=world.lot, roll=world.roll))[0] == 0
        await assert_reconciled(session, org_id)

    async def test_eight_simultaneous_splits_still_leave_one(
        self, maker, session, org_id, world
    ) -> None:
        works = [split(world, "10", "72.5"), split(world, "20", "62.5")] * 4
        outcomes = await race(maker, org_id, works)
        assert Counter(outcomes) == {"ok": 1, "ConflictProblem": 7}, outcomes
        assert await roll_count(session, org_id) == 3
        await assert_reconciled(session, org_id)

    async def test_concurrent_retries_of_one_keyed_split_split_once(
        self, maker, session, org_id, world
    ) -> None:
        outcomes = await race(
            maker, org_id, [split(world, "50", "32.5", key="same-key") for _ in range(6)]
        )
        # One does the work; the rest wait for it on the roll lock and replay it.
        assert outcomes == ["ok"] * 6, outcomes
        assert await roll_count(session, org_id) == 3
        await assert_reconciled(session, org_id)

    async def test_a_split_racing_a_transfer_of_the_same_roll_has_one_winner(
        self, maker, session, org_id, world
    ) -> None:
        w = world

        async def transfer(s: AsyncSession) -> None:
            await stock_ops.transfer_stock(
                s,
                w.t.ctx,
                StockTransferIn(
                    inventory_item_id=w.t.fabric,
                    from_location_id=w.t.weaving,
                    to_location_id=w.bin_b,
                    quantity="10",
                    lot_id=w.lot,
                    roll_id=w.roll,
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )

        outcomes = await race(maker, org_id, [split(w, "50", "32.5"), transfer])
        # Whichever commits first starves the other: a split needs the whole roll, and a
        # transfer needs 10 of it.
        assert outcomes.count("ok") == 1, outcomes
        for bucket in (
            await bucket_qty(session, w, lot=w.lot, roll=w.roll),
            await bucket_qty(session, w, location=w.bin_b, lot=w.lot, roll=w.roll),
        ):
            assert bucket[0] >= 0
        await assert_reconciled(session, org_id)


class TestTransferVersusIssue:
    async def _stock_dye(self, session, w: World, kilos: str) -> None:  # type: ignore[no-untyped-def]
        async with session.begin():
            await stock_ops.post_opening_stock(
                session,
                w.t.ctx,
                OpeningStockIn(
                    lines=[
                        OpeningLineIn(
                            inventory_item_id=w.t.dye, location_id=w.t.weaving, quantity=kilos
                        )
                    ]
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )

    async def test_transfers_and_issues_on_one_bin_never_take_it_below_zero(
        self, maker, session, org_id, world
    ) -> None:
        w = world
        await self._stock_dye(session, w, "10")

        def transfer(kilos: str) -> Work:
            async def work(s: AsyncSession) -> None:
                await stock_ops.transfer_stock(
                    s,
                    w.t.ctx,
                    StockTransferIn(
                        inventory_item_id=w.t.dye,
                        from_location_id=w.t.weaving,
                        to_location_id=w.bin_b,
                        quantity=kilos,
                    ),
                    idempotency_key=None,
                    occurred_on_explicit=False,
                )

            return work

        def issue(kilos: str) -> Work:
            async def work(s: AsyncSession) -> None:
                await ledger.post_move(
                    s,
                    w.t.ctx,
                    kind="issue",
                    lines=[MoveLine(w.t.dye, w.t.weaving, -q(kilos))],
                    occurred_on=TODAY,
                )

            return work

        works = [transfer("3"), issue("3")] * 5  # 30 kg asked of a bin holding 10
        outcomes = await race(maker, org_id, works)
        assert outcomes.count("ok") == 3, outcomes  # 10 // 3 whole 3 kg draws
        assert set(outcomes) <= {"ok", "InsufficientStock"}, outcomes

        source = (await bucket_qty(session, w, item=w.t.dye))[0]
        target = (await bucket_qty(session, w, item=w.t.dye, location=w.bin_b))[0]
        assert source >= 0 and target >= 0
        # Every kilo is either still in a bin or was issued (3 per issue): nothing vanished.
        assert (q("10") - (source + target)) % q("3") == 0
        assert source + target <= q("10")
        await assert_reconciled(session, org_id)

    async def test_two_transfers_cannot_both_move_the_same_stock(
        self, maker, session, org_id, world
    ) -> None:
        w = world
        await self._stock_dye(session, w, "10")

        def transfer() -> Work:
            async def work(s: AsyncSession) -> None:
                await stock_ops.transfer_stock(
                    s,
                    w.t.ctx,
                    StockTransferIn(
                        inventory_item_id=w.t.dye,
                        from_location_id=w.t.weaving,
                        to_location_id=w.bin_b,
                        quantity="6",
                    ),
                    idempotency_key=None,
                    occurred_on_explicit=False,
                )

            return work

        outcomes = await race(maker, org_id, [transfer(), transfer()])
        assert Counter(outcomes) == {"ok": 1, "InsufficientStock": 1}, outcomes
        assert (await bucket_qty(session, w, item=w.t.dye))[0] == q("4")
        assert (await bucket_qty(session, w, item=w.t.dye, location=w.bin_b))[0] == q("6")
        await assert_reconciled(session, org_id)
