"""Storage put-away racing itself and its neighbours on real connections (the
`test_tracking_concurrency.py` method): each worker is its own connection and transaction as the
NOBYPASSRLS app role, started together with `asyncio.gather` — no application lock anywhere, only
the kernel's bucket locks and the unique keys.

* the **same key** arriving twice at once is one put-away and N-1 replays — one move, one capture,
  one audit row, one event — never two moves and never a unique-violation 500;
* **different keys** competing for the same stock leave exactly as many winners as the stock allows,
  and every loser leaves nothing behind (no capture, no audit row, no event) because its whole
  transaction rolled back;
* a put-away and a **transfer** (or a **reservation**) of the same stock leave exactly one winner;
* the same **roll** put away to two bins at once moves once;

and the reconciler finds zero drift after every interleaving.
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
from app.inventory.putaway_schemas import PutawayIn
from app.inventory.tracking_schemas import StockTransferIn
from app.models import SCHEMA, AuditLog, OutboxEvent, PutawayCapture, RollEvent
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.inventory_fixtures import q
from tests.putaway_fixtures import PutawayWorld, build_putaway_world
from tests.tracking_fixtures import assert_reconciled, bucket_qty

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


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


async def race(maker, org_id: uuid.UUID, works: list[Work]) -> list[Any]:  # type: ignore[no-untyped-def]
    """Every `work` in its own tenant-bound transaction, all at once. Each outcome is what the work
    returned, or the exception it raised."""

    async def one(work: Work) -> Any:
        async with maker() as s:

            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            try:
                async with s.begin():
                    return await work(s)
            except Exception as exc:
                return exc

    return list(await asyncio.gather(*(one(w) for w in works)))


def putaway(
    pw: PutawayWorld, quantity: str, key: str, *, barcode: str = "RACK-1-B01", **extra: Any
) -> Work:
    async def work(s: AsyncSession) -> Any:
        return await stock_ops.putaway_stock(
            s,
            pw.w.t.ctx,
            PutawayIn(
                inventory_item_id=extra.pop("item", pw.dye),
                from_location_id=pw.receiving,
                to_barcode=barcode,
                quantity=quantity,
                lat_micro=12_971_601,
                lng_micro=77_594_602,
                **extra,
            ),
            idempotency_key=key,
            occurred_on_explicit=False,
        )

    return work


async def tally(session, org_id: uuid.UUID) -> dict[str, int]:
    async with session.begin():
        captures = (
            await session.execute(
                select(func.count())
                .select_from(PutawayCapture)
                .where(PutawayCapture.organization_id == org_id)
            )
        ).scalar_one()
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.organization_id == org_id, AuditLog.action == "putaway.recorded")
            )
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.organization_id == org_id,
                    OutboxEvent.event_type == "operations.inventory.putaway.recorded",
                )
            )
        ).scalar_one()
    return {"captures": captures, "audits": audits, "events": events}


class TestTheSameKeyAtOnce:
    async def test_four_simultaneous_requests_are_one_put_away_and_three_replays(
        self, maker, session, org_id, pw
    ) -> None:
        outcomes = await race(maker, org_id, [putaway(pw, "5", "same-key") for _ in range(4)])
        assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
        assert Counter(o.posted.replayed for o in outcomes) == {False: 1, True: 3}
        assert len({o.posted.move.id for o in outcomes}) == 1
        assert await tally(session, org_id) == {"captures": 1, "audits": 1, "events": 1}
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("15")
        await assert_reconciled(session, org_id)

    async def test_the_same_key_for_different_requests_at_once_is_one_winner_and_conflicts(
        self, maker, session, org_id, pw
    ) -> None:
        outcomes = await race(
            maker, org_id, [putaway(pw, "5", "k"), putaway(pw, "6", "k"), putaway(pw, "7", "k")]
        )
        winners = [o for o in outcomes if not isinstance(o, Exception)]
        losers = [o for o in outcomes if isinstance(o, Exception)]
        assert len(winners) == 1 and len(losers) == 2, outcomes
        assert all(isinstance(e, ConflictProblem) for e in losers), losers
        assert (await tally(session, org_id))["captures"] == 1
        await assert_reconciled(session, org_id)


class TestDifferentKeysForTheSameStock:
    async def test_five_put_aways_of_six_kg_out_of_twenty_leave_exactly_three_winners(
        self, maker, session, org_id, pw
    ) -> None:
        outcomes = await race(maker, org_id, [putaway(pw, "6", f"k-{i}") for i in range(5)])
        winners = [o for o in outcomes if not isinstance(o, Exception)]
        losers = [o for o in outcomes if isinstance(o, Exception)]
        assert len(winners) == 3 and len(losers) == 2, outcomes
        assert all(isinstance(e, ConflictProblem) for e in losers), losers
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("2")
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("18")
        # The losers' transactions rolled back whole: nothing of theirs is anywhere.
        assert await tally(session, org_id) == {"captures": 3, "audits": 3, "events": 3}
        await assert_reconciled(session, org_id)

    async def test_the_balance_can_never_go_negative_however_many_arrive(
        self, maker, session, org_id, pw
    ) -> None:
        outcomes = await race(maker, org_id, [putaway(pw, "4", f"k-{i}") for i in range(12)])
        assert sum(not isinstance(o, Exception) for o in outcomes) == 5  # 5 x 4 kg = 20 kg
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == 0
        await assert_reconciled(session, org_id)


class TestAgainstItsNeighbours:
    async def test_a_put_away_and_a_transfer_of_the_same_stock_leave_one_winner(
        self, maker, session, org_id, pw
    ) -> None:
        async def transfer(s: AsyncSession) -> Any:
            return await stock_ops.transfer_stock(
                s,
                pw.w.t.ctx,
                StockTransferIn(
                    inventory_item_id=pw.dye,
                    from_location_id=pw.receiving,
                    to_location_id=pw.rack_bin2,
                    quantity="15",
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )

        outcomes = await race(maker, org_id, [putaway(pw, "15", "k-1"), transfer])
        assert sum(isinstance(o, Exception) for o in outcomes) == 1, outcomes
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("5")
        await assert_reconciled(session, org_id)
        # Whichever won, exactly the winner's proof exists.
        proofs = await tally(session, org_id)
        assert (
            proofs["captures"]
            == proofs["audits"]
            == proofs["events"]
            == (0 if isinstance(outcomes[0], Exception) else 1)
        )

    async def test_a_put_away_and_a_reservation_of_the_same_stock_leave_one_winner(
        self, maker, session, org_id, pw
    ) -> None:
        async def reserve(s: AsyncSession) -> Any:
            return await ledger.reserve(
                s,
                pw.w.t.ctx,
                item_id=pw.dye,
                location_id=pw.receiving,
                quantity_milli=q("15"),
                reference_type="sales_order",
                reference_id=uuid.uuid4(),
            )

        outcomes = await race(maker, org_id, [putaway(pw, "15", "k-1"), reserve])
        assert sum(isinstance(o, Exception) for o in outcomes) == 1, outcomes
        await assert_reconciled(session, org_id)

    async def test_the_same_roll_put_away_to_two_bins_at_once_moves_once(
        self, maker, session, org_id, pw
    ) -> None:
        w = pw.w
        extra = {"item": pw.fabric, "lot_id": w.lot, "roll_id": w.roll}
        outcomes = await race(
            maker,
            org_id,
            [
                putaway(pw, "82.5", "roll-a", barcode="RACK-1-B01", **dict(extra)),
                putaway(pw, "82.5", "roll-b", barcode="RACK-1-B02", **dict(extra)),
            ],
        )
        assert sum(isinstance(o, Exception) for o in outcomes) == 1, outcomes
        async with session.begin():
            moved = (
                await session.execute(
                    select(func.count())
                    .select_from(RollEvent)
                    .where(RollEvent.roll_id == w.roll, RollEvent.kind == "MOVED")
                )
            ).scalar_one()
        assert moved == 1
        assert (await tally(session, org_id))["captures"] == 1
        await assert_reconciled(session, org_id)
