"""`app.inventory.ledger.post_move`'s reservation transfer (ADR-0024 §6, amending ADR-0014 §3;
`docs/fulfilment/plan.md` F3's kernel prerequisite) — a pick or pack relocation that carries its
reservation to the destination bucket in the same call, rather than freeing it at the source and
re-reserving it at the destination across two calls and two commits.

Same kernel, same PostgreSQL row locks `test_ledger.py` already proves for `post_move` and
`reserve`; this file only adds the transfer parameter's own guarantees on top of those.
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
from platform_core.errors import ConflictProblem, ValidationProblem
from tests.inventory_fixtures import TODAY, Textile, balance, build_textile, q

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def t(session, org_id) -> Textile:
    return await build_textile(session, org_id)


async def post(session, t: Textile, kind: str, *lines: MoveLine, **kw):  # type: ignore[no-untyped-def]
    async with session.begin():
        return await ledger.post_move(
            session, t.ctx, kind=kind, lines=list(lines), occurred_on=TODAY, **kw
        )


async def reserve(session, t: Textile, item_id, location_id, quantity_milli, **kw):  # type: ignore[no-untyped-def]
    async with session.begin():
        return await ledger.reserve(
            session,
            t.ctx,
            item_id=item_id,
            location_id=location_id,
            quantity_milli=quantity_milli,
            **kw,
        )


async def get_reservation(session, reservation_id):  # type: ignore[no-untyped-def]
    async with session.begin():
        return await session.get(InventoryReservation, reservation_id)


async def assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


def pick(fabric, source, destination, quantity_milli, reservation_id):  # type: ignore[no-untyped-def]
    """The pair of lines a pick confirmation posts: the whole line comes off the named
    reservation (module docstring — a transfer never dips into free stock the way a draw's
    excess does)."""
    return (
        MoveLine(fabric, source, -quantity_milli, transfer_reservation_id=reservation_id),
        MoveLine(fabric, destination, quantity_milli),
    )


class TestHappyPath:
    async def test_a_pick_transfers_the_reservation_not_just_the_stock(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        order_id = uuid.uuid4()
        made = await reserve(
            session, t, t.fabric, t.weaving, q("60"), reference_type="order", reference_id=order_id
        )
        rid = made.reservation.id

        await post(session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("60"), rid))

        # The destination staging bucket's reserved_milli is up by the picked quantity and the
        # source bin's is down by the same amount (F3's exit criterion, verbatim).
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("40"), 0)
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("60"), q("60"))

        source = await get_reservation(session, rid)
        assert (
            source.status,
            source.consumed_milli,
            source.released_milli,
            source.transferred_milli,
        ) == ("transferred", 0, 0, q("60"))  # moved, not consumed and not freed

        async with session.begin():
            destination = (
                await session.execute(
                    select(InventoryReservation).where(
                        InventoryReservation.organization_id == t.org_id,
                        InventoryReservation.transferred_from_reservation_id == rid,
                    )
                )
            ).scalar_one()
        assert destination.status == "active"
        assert destination.quantity_milli == q("60")
        assert (destination.reference_type, destination.reference_id) == ("order", order_id)
        await assert_reconciles_clean(session, t.org_id)

    async def test_a_second_pick_on_the_same_order_tops_up_the_destination_reservation(
        self, session, t
    ) -> None:
        """A split pick — part of one order's reservation moves now, the rest later — lands on
        the *same* destination reservation rather than opening a second one (ADR-0024 §6: "opens
        (or tops up)")."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("60"))
        rid = made.reservation.id

        await post(session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("25"), rid))
        await post(session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("35"), rid))

        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("40"), 0)
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("60"), q("60"))
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == t.org_id,
                            InventoryReservation.transferred_from_reservation_id == rid,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1  # topped up, not a second reservation
        assert rows[0].quantity_milli == q("60")
        source = await get_reservation(session, rid)
        assert (source.status, source.transferred_milli) == ("transferred", q("60"))
        await assert_reconciles_clean(session, t.org_id)


class TestOverTransferAndNonRelocation:
    async def test_transferring_more_than_the_open_remainder_is_a_conflict(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("40"))
        rid = made.reservation.id

        with pytest.raises(ConflictProblem, match="Only 40 is still open"):
            await post(
                session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("41"), rid)
            )

        # Nothing moved: the whole transaction rolled back.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("40"))
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (0, 0)
        source = await get_reservation(session, rid)
        assert (source.status, source.transferred_milli) == ("active", 0)

    async def test_a_transfer_needs_a_matching_relocation_line_different_item(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        await post(session, t, "receipt", MoveLine(t.dye, t.finishing, q("5")))
        made = await reserve(session, t, t.fabric, t.weaving, q("40"))
        rid = made.reservation.id

        with pytest.raises(ValidationProblem, match="exactly one matching relocation line"):
            await post(
                session,
                t,
                "relocation",
                MoveLine(t.fabric, t.weaving, -q("40"), transfer_reservation_id=rid),
                MoveLine(t.dye, t.finishing, q("40")),  # wrong item: no destination candidate
            )
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("40"))

    async def test_a_transfer_needs_a_different_location_not_just_a_status_change(
        self, session, t
    ) -> None:
        """A quality-hold move is `kind == "relocation"` too, but it never leaves the location —
        pairing on item/lot/roll/status alone would wrongly match it; a transfer needs the
        *location* to differ (ADR-0024 §6: "same item, lot, roll and status, different
        location")."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("40"))
        rid = made.reservation.id

        with pytest.raises(ValidationProblem, match="exactly one matching relocation line"):
            await post(
                session,
                t,
                "relocation",
                MoveLine(t.fabric, t.weaving, -q("40"), transfer_reservation_id=rid),
                MoveLine(t.fabric, t.weaving, q("40"), status="QUALITY_HOLD"),
            )
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("40"))

    async def test_a_transfer_is_only_valid_on_a_relocation_kind(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("40"))
        rid = made.reservation.id

        with pytest.raises(ValidationProblem, match="only valid on a relocation"):
            await post(
                session,
                t,
                "issue",
                MoveLine(t.fabric, t.weaving, -q("40"), transfer_reservation_id=rid),
            )

    async def test_a_line_cannot_both_draw_and_transfer(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("40"))
        rid = made.reservation.id

        with pytest.raises(ValidationProblem, match="not both"):
            await post(
                session,
                t,
                "relocation",
                MoveLine(
                    t.fabric, t.weaving, -q("40"), reservation_id=rid, transfer_reservation_id=rid
                ),
                MoveLine(t.fabric, t.finishing, q("40")),
            )


class TestIdempotency:
    async def test_a_retried_pick_replays_without_doubling(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(
            session,
            t,
            t.fabric,
            t.weaving,
            q("60"),
            reference_type="order",
            reference_id=uuid.uuid4(),
        )
        rid = made.reservation.id
        lines = pick(t.fabric, t.weaving, t.finishing, q("60"), rid)

        first = await post(session, t, "relocation", *lines, idempotency_key="pick-1")
        again = await post(session, t, "relocation", *lines, idempotency_key="pick-1")

        assert (first.replayed, again.replayed) == (False, True)
        assert again.move.id == first.move.id
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("40"), 0)
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("60"), q("60"))
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
        assert len(rows) == 2  # source + destination, not doubled by the replay


class TestReleaseAfterTransfer:
    async def test_a_partially_transferred_reservation_is_still_releasable(
        self, session, t
    ) -> None:
        """Cancellation after a partial pick releases what is still open — the transferred
        portion, already carried by the destination reservation, is untouched (ADR-0024 §6:
        "cancellation releases it")."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("60"))
        rid = made.reservation.id

        await post(session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("25"), rid))
        source = await get_reservation(session, rid)
        assert (source.status, source.transferred_milli) == ("active", q("25"))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("75"), q("35"))

        async with session.begin():
            released = await ledger.release(session, t.ctx, rid)
        # Fully spoken for now (25 transferred + 35 released = 60), and — same rule a
        # consume/release mix already follows (MEDIUM #2) — the more consequential fact wins:
        # some of it genuinely moved with stock, so the reservation closes `transferred`, not
        # `released`, even though the released portion is the larger one.
        assert released.status == "transferred"
        assert (released.released_milli, released.transferred_milli) == (q("35"), q("25"))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("75"), 0)
        # The destination reservation from the partial pick is unaffected by releasing the rest.
        async with session.begin():
            destination = (
                await session.execute(
                    select(InventoryReservation).where(
                        InventoryReservation.organization_id == t.org_id,
                        InventoryReservation.transferred_from_reservation_id == rid,
                    )
                )
            ).scalar_one()
        assert (destination.status, destination.quantity_milli) == ("active", q("25"))
        await assert_reconciles_clean(session, t.org_id)

    async def test_releasing_a_fully_transferred_reservation_is_refused(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made = await reserve(session, t, t.fabric, t.weaving, q("30"))
        rid = made.reservation.id

        await post(session, t, "relocation", *pick(t.fabric, t.weaving, t.finishing, q("30"), rid))
        source = await get_reservation(session, rid)
        assert source.status == "transferred"

        with pytest.raises(ConflictProblem, match="fully transferred"):
            async with session.begin():
                await ledger.release(session, t.ctx, rid)


# ------------------------------------------------------------------ concurrency


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=10,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def race(maker, org_id: uuid.UUID, works: list[Work]) -> list[str]:  # type: ignore[no-untyped-def]
    """Run every `work` in its own tenant-bound transaction, all at once — the same harness
    `test_ledger_reserve_many.py` uses, duplicated rather than imported (sibling `test_*.py`
    files are fragile to import from; the module-level `tests/inventory_fixtures.py` is not)."""

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


def transfer_work(t: Textile, lines: tuple[MoveLine, MoveLine]) -> Work:
    async def work(s: AsyncSession) -> None:
        await ledger.post_move(s, t.ctx, kind="relocation", lines=list(lines), occurred_on=TODAY)

    return work


def reserve_work(t: Textile, item_id, location_id, quantity_milli) -> Work:  # type: ignore[no-untyped-def]
    async def work(s: AsyncSession) -> None:
        await ledger.reserve(
            s, t.ctx, item_id=item_id, location_id=location_id, quantity_milli=quantity_milli
        )

    return work


class TestConcurrencyProof:
    async def test_a_second_order_cannot_reserve_the_source_buckets_freed_looking_stock(
        self, session, maker, t
    ) -> None:
        """F3's own stated exit criterion: after a pick transfers order A's reservation away, the
        source bin still holds order C's separate, untouched reservation. A second order (B)
        racing the pick tries to reserve the bin's now-smaller-looking stock — for a quantity
        that only fits if the pick had, even for a moment, made *all* of the departed quantity
        free rather than moving exactly order A's promise with it. It is refused every time, no
        matter which side the scheduler runs first: the balance's row lock means B either sees
        the bin before the pick (order C's 20 already blocks it) or after (order C's 20 still
        blocks it, unchanged) — never a state in between where it looks free."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        made_a = await reserve(
            session,
            t,
            t.fabric,
            t.weaving,
            q("60"),
            reference_type="order",
            reference_id=uuid.uuid4(),
        )
        await reserve(
            session,
            t,
            t.fabric,
            t.weaving,
            q("20"),
            reference_type="order",
            reference_id=uuid.uuid4(),
        )
        rid_a = made_a.reservation.id
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("80"))

        results = await race(
            maker,
            t.org_id,
            [
                transfer_work(t, pick(t.fabric, t.weaving, t.finishing, q("60"), rid_a)),
                reserve_work(t, t.fabric, t.weaving, q("21")),  # 1 more than the true 20 available
            ],
        )
        assert results == ["ok", "refused"]

        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("40"), q("20"))
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("60"), q("60"))
        await assert_reconciles_clean(session, t.org_id)

        # And the exact, actionable numbers — not just "refused" — hold once things settle:
        # order C's 20 is still the only thing standing in the way.
        with pytest.raises(InsufficientStock) as refused:
            await reserve(session, t, t.fabric, t.weaving, q("21"))
        assert refused.value.errors[0]["available"] == "20"
