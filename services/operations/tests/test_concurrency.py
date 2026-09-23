"""Concurrent stock writes against real PostgreSQL row locks (ADR-0014 §3).

Every worker is its own connection and its own transaction, as the NOBYPASSRLS app role, racing
the others through `asyncio.gather` — no application-level lock anywhere, only what the database
does with `SELECT … FOR UPDATE` and the idempotency unique index. Each scenario ends by
reconciling the organization: whatever the interleaving, the balances must equal the ledger.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import ledger
from app.inventory.ledger import InsufficientStock, MoveLine
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA, InventoryReservation
from platform_core.db import TENANT_GUC
from tests.inventory_fixtures import TODAY, Textile, balance, build_textile, ledger_total, q

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def t(session, org_id) -> Textile:
    return await build_textile(session, org_id)


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
    """Run every `work` in its own tenant-bound transaction, all at once. Returns, per work,
    `ok`, `refused` (insufficient stock), or the error's class name."""

    async def one(work: Work) -> str:
        async with maker() as s:

            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            try:
                async with s.begin():
                    await work(s)
            except InsufficientStock:
                return "refused"
            except Exception as exc:
                return type(exc).__name__
            return "ok"

    return list(await asyncio.gather(*(one(w) for w in works)))


def issue(t: Textile, milli: int, **kw: Any) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.post_move(
            s,
            t.ctx,
            kind="issue",
            lines=[MoveLine(t.fabric, t.weaving, -milli, **kw)],
            occurred_on=TODAY,
        )

    return work


def receive(t: Textile, milli: int, *, key: str | None = None) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.post_move(
            s,
            t.ctx,
            kind="receipt",
            lines=[MoveLine(t.fabric, t.weaving, milli)],
            occurred_on=TODAY,
            idempotency_key=key,
        )

    return work


def reserve(t: Textile, milli: int) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.reserve(
            s, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=milli
        )

    return work


def release(t: Textile, reservation_id: uuid.UUID, milli: int | None = None) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.release(s, t.ctx, reservation_id, quantity_milli=milli)

    return work


def count_to(t: Textile, milli: int) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.post_move(
            s,
            t.ctx,
            kind="count",
            lines=[MoveLine(t.fabric, t.weaving, counted_milli=milli)],
            occurred_on=TODAY,
        )

    return work


async def assert_reconciled(session, t: Textile) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, t.org_id)
    assert report.clean, report.findings


async def stock(session, t: Textile, milli: int) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        await ledger.post_move(
            session,
            t.ctx,
            kind="receipt",
            lines=[MoveLine(t.fabric, t.weaving, milli)],
            occurred_on=TODAY,
        )


class TestScenarioA_ConsumeTheSameStock:
    async def test_two_workers_each_consuming_all_100_units(self, session, maker, t) -> None:
        await stock(session, t, q("100"))
        results = await race(maker, t.org_id, [issue(t, q("100")), issue(t, q("100"))])
        assert sorted(results) == ["ok", "refused"]
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (0, 0)
        assert await ledger_total(session, t.org_id, t.fabric) == 0
        await assert_reconciled(session, t)

    async def test_fifty_workers_against_ten_units(self, session, maker, t) -> None:
        await stock(session, t, q("10"))
        results = await race(maker, t.org_id, [issue(t, q("1")) for _ in range(50)])
        assert results.count("ok") == 10
        assert results.count("refused") == 40
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (0, 0)
        await assert_reconciled(session, t)

    async def test_decimal_consumption_never_overdraws(self, session, maker, t) -> None:
        # 82.5 m of fabric, 40 cutters each taking 2.5 m: exactly 33 fit.
        await stock(session, t, q("82.5"))
        results = await race(maker, t.org_id, [issue(t, q("2.5")) for _ in range(40)])
        assert results.count("ok") == 33
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (0, 0)
        await assert_reconciled(session, t)


class TestScenarioB_ConcurrentReservations:
    async def test_two_reservations_of_60_against_100(self, session, maker, t) -> None:
        await stock(session, t, q("100"))
        results = await race(maker, t.org_id, [reserve(t, q("60")), reserve(t, q("60"))])
        assert sorted(results) == ["ok", "refused"]
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("60"))
        await assert_reconciled(session, t)

    async def test_twenty_reservations_never_over_reserve(self, session, maker, t) -> None:
        await stock(session, t, q("100"))
        results = await race(maker, t.org_id, [reserve(t, q("10")) for _ in range(20)])
        assert results.count("ok") == 10
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("100"))
        await assert_reconciled(session, t)

    async def test_a_reservation_racing_an_issue_for_the_same_units(
        self, session, maker, t
    ) -> None:
        await stock(session, t, q("100"))
        results = await race(maker, t.org_id, [reserve(t, q("60")), issue(t, q("60"))])
        assert sorted(results) == ["ok", "refused"]
        on_hand, reserved = await balance(session, t.org_id, t.fabric, t.weaving)
        assert on_hand - reserved == q("40")
        await assert_reconciled(session, t)


class TestScenarioC_ReceiptAndConsumptionTogether:
    async def test_no_lost_updates(self, session, maker, t) -> None:
        await stock(session, t, q("100"))
        works = [receive(t, q("2")) for _ in range(25)] + [issue(t, q("3")) for _ in range(25)]
        results = await race(maker, t.org_id, works)
        assert results == ["ok"] * 50
        expected = q("100") + 25 * q("2") - 25 * q("3")
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (expected, 0)
        assert await ledger_total(session, t.org_id, t.fabric) == expected
        await assert_reconciled(session, t)


class TestScenarioD_DuplicateRequests:
    async def test_ten_retries_of_one_request_post_exactly_once(self, session, maker, t) -> None:
        results = await race(maker, t.org_id, [receive(t, q("5"), key="grn-9") for _ in range(10)])
        assert results == ["ok"] * 10  # every retry answers; only one of them wrote
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("5"), 0)
        assert await ledger_total(session, t.org_id, t.fabric) == q("5")
        await assert_reconciled(session, t)


class TestNoDeadlocks:
    async def test_opposite_multi_line_moves_queue_instead_of_deadlocking(
        self, session, maker, t
    ) -> None:
        async with session.begin():
            await ledger.post_move(
                session,
                t.ctx,
                kind="receipt",
                lines=[
                    MoveLine(t.fabric, t.weaving, q("500")),
                    MoveLine(t.fabric, t.finishing, q("500")),
                ],
                occurred_on=TODAY,
            )

        def transfer(src: uuid.UUID, dst: uuid.UUID) -> Work:
            async def work(s: AsyncSession) -> None:
                # Lines deliberately given in opposite orders by the two directions.
                await ledger.post_move(
                    s,
                    t.ctx,
                    kind="relocation",
                    lines=[MoveLine(t.fabric, src, -q("1")), MoveLine(t.fabric, dst, q("1"))],
                    occurred_on=TODAY,
                )

            return work

        works = [transfer(t.weaving, t.finishing) for _ in range(20)] + [
            transfer(t.finishing, t.weaving) for _ in range(20)
        ]
        results = await race(maker, t.org_id, works)
        assert results == ["ok"] * 40  # a DeadlockDetectedError would show up here by name
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("500"), 0)
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("500"), 0)
        await assert_reconciled(session, t)


class TestScenarioE_ReleaseRacingConsumptionOfTheSameReservation:
    """B1: `drawn` must subtract `released_milli`, not just `consumed_milli` — otherwise a
    release and an issue against the same reservation, run concurrently, can together draw more
    than the reservation ever promised and trip the `consumed + released <= quantity` CHECK as a
    raw `IntegrityError` instead of either succeeding cleanly or refusing with numbers. The two
    lock the same balance and the same reservation row in the same order every other path in this
    module uses, so they serialise rather than deadlock — this proves the serialised order always
    lands on a consistent, reconciled state, not just the sequential case `test_ledger.py` covers.
    """

    async def test_release_and_a_reservation_backed_issue_together_never_overdraw_it(
        self, session, maker, t
    ) -> None:
        await stock(session, t, q("100"))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id

        results = await race(
            maker,
            t.org_id,
            [release(t, rid, q("60")), issue(t, q("40"), reservation_id=rid)],
        )
        # Never an IntegrityError, whichever order the lock queue picked.
        assert results == ["ok", "ok"]
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("60"), 0)
        async with session.begin():
            # `made.reservation` is already in this session's identity map from the reserve()
            # call above, at its pre-race values — the race ran on separate connections, so a
            # plain `get()` would return that stale copy rather than what they committed.
            res = (
                await session.execute(
                    select(InventoryReservation)
                    .where(InventoryReservation.id == rid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            assert (res.status, res.consumed_milli, res.released_milli) == (
                "consumed",
                q("40"),
                q("60"),
            )
        await assert_reconciled(session, t)

    async def test_release_racing_a_count_settles_on_one_consistent_outcome(
        self, session, maker, t
    ) -> None:
        """The two valid interleavings give different, but each internally consistent, answers:
        release-then-count sees the freed capacity and succeeds; count-then-release sees the full
        reservation still standing and is refused. Either is correct; an `IntegrityError` or a
        silently over-committed balance is not."""
        await stock(session, t, q("100"))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id

        results = await race(maker, t.org_id, [release(t, rid, q("60")), count_to(t, q("45"))])
        assert results[0] == "ok"  # the release itself always succeeds
        assert results[1] in ("ok", "refused")
        on_hand, reserved = await balance(session, t.org_id, t.fabric, t.weaving)
        if results[1] == "ok":
            assert (on_hand, reserved) == (q("45"), q("40"))
        else:
            assert (on_hand, reserved) == (q("100"), q("40"))
        await assert_reconciled(session, t)

    async def _run_to_completion(self, maker, org_id: uuid.UUID, work: Work) -> None:
        """One work item on its own connection, awaited to commit before returning — a
        deterministic order, not `race`'s `asyncio.gather` (MEDIUM #2: the tests below need to
        pin down *which* of two operations closes the reservation, not merely that neither one
        corrupts state under a race whichever way the lock queue happens to fall)."""
        async with maker() as s:

            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            async with s.begin():
                await work(s)

    async def test_a_release_then_the_remaining_issue_both_close_it_as_consumed(
        self, session, maker, t
    ) -> None:
        """Release first (leaves 40 open), then an issue draws exactly what is left —
        `post_move`'s closing branch is the one that fires."""
        await stock(session, t, q("100"))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id

        await self._run_to_completion(maker, t.org_id, release(t, rid, q("60")))
        await self._run_to_completion(maker, t.org_id, issue(t, q("40"), reservation_id=rid))

        async with session.begin():
            res = (
                await session.execute(
                    select(InventoryReservation)
                    .where(InventoryReservation.id == rid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        assert (res.status, res.consumed_milli, res.released_milli) == (
            "consumed",
            q("40"),
            q("60"),
        )
        await assert_reconciled(session, t)

    async def test_an_issue_then_the_remaining_release_also_closes_it_as_consumed(
        self, session, maker, t
    ) -> None:
        """Reversed order from the test above — an issue first (leaves 60 open), then a release
        gives back exactly what is left, so `release`'s own closing branch is the one that fires
        this time. Before the fix this branch always set `released` regardless of what had
        already been consumed; the terminal status must not depend on which path closed it
        (MEDIUM #2) — this is the one order that fails without the fix."""
        await stock(session, t, q("100"))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id

        await self._run_to_completion(maker, t.org_id, issue(t, q("40"), reservation_id=rid))
        await self._run_to_completion(maker, t.org_id, release(t, rid, q("60")))

        async with session.begin():
            res = (
                await session.execute(
                    select(InventoryReservation)
                    .where(InventoryReservation.id == rid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        assert (res.status, res.consumed_milli, res.released_milli) == (
            "consumed",
            q("40"),
            q("60"),
        )
        await assert_reconciled(session, t)
