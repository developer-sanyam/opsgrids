"""`app.inventory.ledger.reserve_many` — the multi-line, all-or-nothing reservation an order's
`accept` needs (ADR-0024 §1, §6; docs/fulfilment/plan.md F2). Same kernel, same PostgreSQL row
locks as `test_ledger.py` and `test_concurrency.py`; this file only adds the multi-line entry
point's own guarantees on top of what those already prove for `reserve`.
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
from app.inventory.ledger import InsufficientStock, ReserveLine
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA, AuditLog, InventoryReservation, OutboxEvent
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem, ValidationProblem
from tests.inventory_fixtures import TODAY, Textile, balance, build_textile, q

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def t(session, org_id) -> Textile:
    return await build_textile(session, org_id)


async def post(session, t: Textile, kind: str, *lines: ledger.MoveLine, **kw):  # type: ignore[no-untyped-def]
    """The same one-line helper `test_ledger.py` uses to stock a bucket before reserving it —
    duplicated rather than imported cross-module (`inventory_fixtures.py`'s own reasoning: a
    plain shared module is not fragile to import from, a sibling `test_*.py` file is)."""
    async with session.begin():
        return await ledger.post_move(
            session, t.ctx, kind=kind, lines=list(lines), occurred_on=TODAY, **kw
        )


async def assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


async def reserve_many(session, t: Textile, lines: list[ReserveLine], **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("reference_type", "order")
    kw.setdefault("reference_id", uuid.uuid4())
    kw.setdefault("idempotency_key", f"order:{uuid.uuid4()}:accept")
    async with session.begin():
        return await ledger.reserve_many(session, t.ctx, lines, **kw)


# ------------------------------------------------------------------ sequential


class TestHappyPath:
    async def test_multiple_lines_across_items_and_locations_all_reserve(self, session, t) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))
        await post(session, t, "receipt", ledger.MoveLine(t.buttons, t.finishing, q("1000")))

        order_id = uuid.uuid4()
        result = await reserve_many(
            session,
            t,
            [
                ReserveLine(t.fabric, t.weaving, q("40")),
                ReserveLine(t.dye, t.weaving, q("10")),
                ReserveLine(t.buttons, t.finishing, q("200")),
            ],
            reference_type="order",
            reference_id=order_id,
        )
        assert not result.replayed
        assert len(result.reservations) == 3
        assert all(
            r.reference_type == "order" and r.reference_id == order_id for r in result.reservations
        )

        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("40"))
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("50"), q("10"))
        assert await balance(session, t.org_id, t.buttons, t.finishing) == (q("1000"), q("200"))
        await assert_reconciles_clean(session, t.org_id)

    async def test_writes_exactly_one_audit_row_and_one_event_for_the_whole_call(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))
        order_id = uuid.uuid4()

        await reserve_many(
            session,
            t,
            [
                ReserveLine(t.fabric, t.weaving, q("10")),
                ReserveLine(t.dye, t.weaving, q("5")),
            ],
            reference_type="order",
            reference_id=order_id,
        )

        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "inventory_reservation.created",
                            AuditLog.entity_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == ledger.EVT_STOCK_RESERVED,
                            OutboxEvent.aggregate_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1
        assert len(events) == 1
        assert len(events[0].payload["lines"]) == 2


class TestPartialShortfallReservesNothing:
    async def test_one_short_line_out_of_two_reserves_neither(self, session, t) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("10")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))

        with pytest.raises(InsufficientStock) as refused:
            await reserve_many(
                session,
                t,
                [
                    ReserveLine(t.fabric, t.weaving, q("5")),  # plenty
                    ReserveLine(t.dye, t.weaving, q("100")),  # short: only 50 on hand
                ],
            )
        assert len(refused.value.errors) == 1  # only the dye line was actually short
        assert refused.value.errors[0]["sku"] == "DYE-RED-01"

        # Neither bucket moved — not just "it raised", the reserved figures are untouched.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("10"), 0)
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("50"), 0)
        await assert_reconciles_clean(session, t.org_id)

    async def test_every_short_line_is_named_not_just_the_first(self, session, t) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("1")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("1")))

        with pytest.raises(InsufficientStock) as refused:
            await reserve_many(
                session,
                t,
                [
                    ReserveLine(t.fabric, t.weaving, q("50")),
                    ReserveLine(t.dye, t.weaving, q("50")),
                ],
            )
        skus = {e["sku"] for e in refused.value.errors}
        assert skus == {"COT-180-NAVY", "DYE-RED-01"}
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("1"), 0)
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("1"), 0)

    async def test_a_bucket_with_no_stock_at_all_is_a_shortfall_not_a_crash(
        self, session, t
    ) -> None:
        with pytest.raises(InsufficientStock):
            await reserve_many(session, t, [ReserveLine(t.fabric, t.weaving, q("1"))])


class TestValidation:
    async def test_an_empty_line_list_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem):
            await reserve_many(session, t, [])

    async def test_a_non_positive_quantity_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem):
            await reserve_many(session, t, [ReserveLine(t.fabric, t.weaving, 0)])

    async def test_the_same_bucket_twice_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem):
            await reserve_many(
                session,
                t,
                [
                    ReserveLine(t.fabric, t.weaving, q("1")),
                    ReserveLine(t.fabric, t.weaving, q("2")),
                ],
            )


class TestIdempotency:
    async def test_a_retry_with_the_same_key_replays_without_double_reserving(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))
        order_id = uuid.uuid4()
        lines = [
            ReserveLine(t.fabric, t.weaving, q("10")),
            ReserveLine(t.dye, t.weaving, q("5")),
        ]

        first = await reserve_many(
            session, t, lines, reference_type="order", reference_id=order_id, idempotency_key="k1"
        )
        again = await reserve_many(
            session, t, lines, reference_type="order", reference_id=order_id, idempotency_key="k1"
        )

        assert (first.replayed, again.replayed) == (False, True)
        assert [r.id for r in again.reservations] == [r.id for r in first.reservations]
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("10"))
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("50"), q("5"))

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == t.org_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2  # not four

    async def test_a_retry_with_lines_reordered_still_replays_by_line_id(self, session, t) -> None:
        """Review MINOR G: the derived per-line key follows each line's own `line_id`, not its
        position in `lines` — a retry whose lines happen to come back in a different order (an
        order's own `lines_for` sorts by `created_at`, which is not a guaranteed-stable tiebreaker
        for lines written in the same transaction) still replays the same reservations instead of
        conflicting or double-reserving."""
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))
        order_id = uuid.uuid4()
        fabric_line_id, dye_line_id = uuid.uuid4(), uuid.uuid4()
        first_lines = [
            ReserveLine(t.fabric, t.weaving, q("10"), line_id=fabric_line_id),
            ReserveLine(t.dye, t.weaving, q("5"), line_id=dye_line_id),
        ]
        retried_lines = [  # same two lines, reordered
            ReserveLine(t.dye, t.weaving, q("5"), line_id=dye_line_id),
            ReserveLine(t.fabric, t.weaving, q("10"), line_id=fabric_line_id),
        ]

        first = await reserve_many(
            session,
            t,
            first_lines,
            reference_type="order",
            reference_id=order_id,
            idempotency_key="k9",
        )
        again = await reserve_many(
            session,
            t,
            retried_lines,
            reference_type="order",
            reference_id=order_id,
            idempotency_key="k9",
        )

        assert (first.replayed, again.replayed) == (False, True)
        assert {r.id for r in again.reservations} == {r.id for r in first.reservations}
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("10"))
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("50"), q("5"))

    async def test_the_same_key_for_a_different_set_of_lines_is_a_conflict(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", ledger.MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", ledger.MoveLine(t.dye, t.weaving, q("50")))
        await reserve_many(
            session,
            t,
            [ReserveLine(t.fabric, t.weaving, q("10"))],
            idempotency_key="k2",
        )
        with pytest.raises(ConflictProblem):
            await reserve_many(
                session,
                t,
                [ReserveLine(t.dye, t.weaving, q("5"))],
                idempotency_key="k2",
            )


# ------------------------------------------------------------------ concurrency


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
    `ok`, `refused` (insufficient stock), or the error's class name — the same contract
    `test_concurrency.py`'s own `race` gives."""

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


def accept(t: Textile, lines: list[ReserveLine], order_id: uuid.UUID) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.reserve_many(
            s,
            t.ctx,
            lines,
            reference_type="order",
            reference_id=order_id,
            idempotency_key=f"order:{order_id}:accept",
        )

    return work


async def stock(session, t: Textile, item: uuid.UUID, location: uuid.UUID, milli: int) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        await ledger.post_move(
            session,
            t.ctx,
            kind="receipt",
            lines=[ledger.MoveLine(item, location, milli)],
            occurred_on=TODAY,
        )


class TestConcurrentAccept:
    async def test_fifty_orders_one_orders_worth_of_stock_exactly_one_accepts(
        self, session, maker, t
    ) -> None:
        """F2's own stated exit criterion, proved at the kernel level: 50 concurrent multi-line
        `reserve_many` calls against a single SKU where only one call's total quantity is
        available → exactly one succeeds, the other 49 see `InsufficientStock`."""
        await stock(session, t, t.fabric, t.weaving, q("40"))

        orders = [uuid.uuid4() for _ in range(50)]
        works = [
            accept(t, [ReserveLine(t.fabric, t.weaving, q("40"))], order_id) for order_id in orders
        ]
        results = await race(maker, t.org_id, works)
        assert results.count("ok") == 1
        assert results.count("refused") == 49
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("40"), q("40"))
        await assert_reconciles_clean(session, t.org_id)

    async def test_two_line_accept_where_one_line_is_short_leaves_both_buckets_untouched(
        self, session, maker, t
    ) -> None:
        await stock(session, t, t.fabric, t.weaving, q("100"))
        await stock(session, t, t.dye, t.weaving, q("10"))

        order_id = uuid.uuid4()
        results = await race(
            maker,
            t.org_id,
            [
                accept(
                    t,
                    [
                        ReserveLine(t.fabric, t.weaving, q("50")),  # available
                        ReserveLine(t.dye, t.weaving, q("20")),  # short: only 10 on hand
                    ],
                    order_id,
                )
            ],
        )
        assert results == ["refused"]
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), 0)
        assert await balance(session, t.org_id, t.dye, t.weaving) == (q("10"), 0)
        await assert_reconciles_clean(session, t.org_id)

    async def test_a_retried_accept_under_concurrency_replays_once(self, session, maker, t) -> None:
        await stock(session, t, t.fabric, t.weaving, q("100"))
        order_id = uuid.uuid4()
        lines = [ReserveLine(t.fabric, t.weaving, q("10"))]

        results = await race(maker, t.org_id, [accept(t, lines, order_id) for _ in range(10)])
        assert results == ["ok"] * 10
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("10"))
        await assert_reconciles_clean(session, t.org_id)

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == t.org_id,
                            InventoryReservation.reference_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
