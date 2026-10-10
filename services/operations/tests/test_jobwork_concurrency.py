"""Job work under real concurrency (Inventory plan Phase 6): real second connections, real row
locks — the same harness `test_concurrency.py` uses for the stock kernel, pointed at the job-work
writes. What must hold: one order is issued once, stock is never sent twice, challan numbers are
unique and gapless, two receipts never both take the last of a quantity, a duplicate request is a
replay — and the reconciler finds no drift afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.jobwork import services
from app.jobwork.schemas import JobWorkMaterialIn, JobWorkReceiptLineIn
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for
from tests.jobwork_fixtures import Mill, build_mill, key

pytestmark = pytest.mark.integration

CTX = "operations.jobwork.order.write"


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


@pytest.fixture
async def maker():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=20,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def in_tx(
    maker: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID,
    work: Callable[[AsyncSession], Awaitable[Any]],
) -> Any:
    """One request's worth of work on its own connection, tenant-bound, committed on success."""
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with s.begin():
            return await work(s)


async def approved_order(maker, mill: Mill, quantity: str, *, item=None) -> uuid.UUID:  # type: ignore[no-untyped-def]
    ctx = ctx_for(mill.org_id, CTX, Scope.ORG)

    async def work(s: AsyncSession) -> uuid.UUID:
        order, _ = await services.create_order(
            s,
            ctx,
            supplier_id=mill.supplier_id,
            process="Dyeing",
            due_date=None,
            source_warehouse_id=None,
            receive_location_id=mill.recv,
            qc_required=False,
            notes="",
            materials=[JobWorkMaterialIn(item_id=item or mill.fabric, quantity=quantity)],
            idempotency_key=key(),
        )
        await services.approve_order(s, ctx, order.id)
        return order.id

    return await in_tx(maker, mill.org_id, work)  # type: ignore[no-any-return]


async def try_issue(maker, mill: Mill, order_id: uuid.UUID, *, idem: str | None = None) -> str:  # type: ignore[no-untyped-def]
    ctx = ctx_for(mill.org_id, "operations.jobwork.order.issue", Scope.ORG)

    async def work(s: AsyncSession) -> str:
        _o, challan, replayed = await services.issue_order(
            s,
            ctx,
            order_id,
            vehicle_no=None,
            transporter_id=None,
            distance_km=None,
            note="",
            idempotency_key=idem or key(),
        )
        return f"replay:{challan.challan_number}" if replayed else f"ok:{challan.challan_number}"

    try:
        return await in_tx(maker, mill.org_id, work)  # type: ignore[no-any-return]
    except ConflictProblem as exc:
        return "stock" if "insufficient" in type(exc).__name__.lower() else "conflict"


async def drift(maker, org_id: uuid.UUID) -> list[Any]:  # type: ignore[no-untyped-def]
    async def work(s: AsyncSession) -> list[Any]:
        result = await reconcile_organization(s, org_id, run_id=uuid.uuid4())
        return list(result.findings)

    return await in_tx(maker, org_id, work)  # type: ignore[no-any-return]


async def challan_count(maker, org_id: uuid.UUID, order_id: uuid.UUID | None = None) -> int:  # type: ignore[no-untyped-def]
    async def work(s: AsyncSession) -> int:
        sql = f"SELECT count(*) FROM {SCHEMA}.challans"  # noqa: S608
        if order_id is not None:
            return int(
                (await s.execute(text(sql + " WHERE order_id = :o"), {"o": order_id})).scalar_one()
            )
        return int((await s.execute(text(sql))).scalar_one())

    return await in_tx(maker, org_id, work)  # type: ignore[no-any-return]


class TestConcurrentIssue:
    async def test_one_order_issued_by_eight_requests_is_issued_exactly_once(
        self, session, maker, mill
    ) -> None:
        order_id = await approved_order(maker, mill, "40")
        results = await asyncio.gather(*(try_issue(maker, mill, order_id) for _ in range(8)))
        assert sorted(r.split(":")[0] for r in results) == ["conflict"] * 7 + ["ok"]
        assert await challan_count(maker, mill.org_id, order_id) == 1
        async with session.begin():
            assert (
                await balance_milli_in(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 40_000
            )
            assert await balance_milli_in(session, mill.fabric, mill.home, "AVAILABLE") == 60_000
        assert await drift(maker, mill.org_id) == []

    async def test_the_same_key_eight_times_is_one_issue_and_seven_replays(
        self, maker, mill
    ) -> None:
        order_id = await approved_order(maker, mill, "40")
        idem = key()
        results = await asyncio.gather(
            *(try_issue(maker, mill, order_id, idem=idem) for _ in range(8))
        )
        kinds = sorted(r.split(":")[0] for r in results)
        assert kinds == ["ok"] + ["replay"] * 7
        assert len({r.split(":", 1)[1] for r in results}) == 1  # one challan number
        assert await challan_count(maker, mill.org_id, order_id) == 1
        assert await drift(maker, mill.org_id) == []

    async def test_orders_competing_for_the_same_stock_never_oversend_it(
        self, session, maker, mill
    ) -> None:
        """Five orders of 30 m against 100 m: three win, two are told the numbers, and the vendor
        bucket holds exactly 90 m — never 120."""
        order_ids = [await approved_order(maker, mill, "30") for _ in range(5)]
        results = await asyncio.gather(*(try_issue(maker, mill, o) for o in order_ids))
        wins = [r for r in results if r.startswith("ok:")]
        assert len(wins) == 3, results
        assert sorted(r for r in results if not r.startswith("ok:")) == ["stock", "stock"]
        async with session.begin():
            assert (
                await balance_milli_in(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 90_000
            )
            assert await balance_milli_in(session, mill.fabric, mill.home, "AVAILABLE") == 10_000
        # challan numbers: unique and gapless — the two refusals consumed none
        numbers = sorted(int(r.rsplit("/", 1)[-1]) for r in wins)
        assert numbers == [1, 2, 3]
        assert await drift(maker, mill.org_id) == []

    async def test_many_orders_issued_at_once_get_unique_gapless_challan_numbers(
        self, maker, mill
    ) -> None:
        order_ids = [await approved_order(maker, mill, "5") for _ in range(12)]
        results = await asyncio.gather(*(try_issue(maker, mill, o) for o in order_ids))
        assert all(r.startswith("ok:") for r in results), results
        numbers = sorted(int(r.rsplit("/", 1)[-1]) for r in results)
        assert numbers == list(range(1, 13))
        assert await drift(maker, mill.org_id) == []


async def balance_milli_in(
    session: AsyncSession, item_id: uuid.UUID, location_id: uuid.UUID, status: str
) -> int:
    row = await session.execute(
        text(
            f"""
            SELECT COALESCE(SUM(quantity_milli), 0) FROM {SCHEMA}.inventory_balances
            WHERE inventory_item_id = :i AND location_id = :l AND status = :s
            """  # noqa: S608
        ),
        {"i": item_id, "l": location_id, "s": status},
    )
    return int(row.scalar_one())


async def issued(maker, mill: Mill, quantity: str) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    order_id = await approved_order(maker, mill, quantity)
    assert (await try_issue(maker, mill, order_id)).startswith("ok:")

    async def material(s: AsyncSession) -> uuid.UUID:
        return (
            await s.execute(
                text(f"SELECT id FROM {SCHEMA}.job_work_materials WHERE order_id = :o"),  # noqa: S608
                {"o": order_id},
            )
        ).scalar_one()

    return order_id, await in_tx(maker, mill.org_id, material)


async def try_receive(  # type: ignore[no-untyped-def]
    maker, mill: Mill, order_id: uuid.UUID, material_id: uuid.UUID, qty: str, *, idem=None
) -> str:
    ctx = ctx_for(mill.org_id, "operations.jobwork.order.receive", Scope.ORG)

    async def work(s: AsyncSession) -> str:
        _o, _r, replayed = await services.receive_order(
            s,
            ctx,
            order_id,
            lines=[JobWorkReceiptLineIn(material_id=material_id, received=qty)],
            received_on=None,
            note="",
            idempotency_key=idem or key(),
        )
        return "replay" if replayed else "ok"

    try:
        return await in_tx(maker, mill.org_id, work)  # type: ignore[no-any-return]
    except ConflictProblem:
        return "refused"


class TestConcurrentReceive:
    async def test_receipts_racing_for_the_last_of_a_quantity_cannot_both_take_it(
        self, session, maker, mill
    ) -> None:
        """10 m out; five receipts of 4 m race. Two fit (8 m), three are refused, and exactly 2 m
        stay outstanding — the material row lock is what makes the arithmetic hold."""
        order_id, material_id = await issued(maker, mill, "10")
        results = await asyncio.gather(
            *(try_receive(maker, mill, order_id, material_id, "4") for _ in range(5))
        )
        assert sorted(results) == ["ok", "ok", "refused", "refused", "refused"]
        async with session.begin():
            assert await balance_milli_in(session, mill.fabric, mill.recv, "AVAILABLE") == 8_000
            assert await balance_milli_in(session, mill.fabric, mill.home, "AT_JOB_WORKER") == 2_000
            row = (
                await session.execute(
                    text(
                        f"SELECT received_milli FROM {SCHEMA}.job_work_materials WHERE id = :m"  # noqa: S608
                    ),
                    {"m": material_id},
                )
            ).scalar_one()
        assert row == 8_000
        assert await drift(maker, mill.org_id) == []

    async def test_the_same_receipt_key_sent_six_times_receives_once(
        self, session, maker, mill
    ) -> None:
        order_id, material_id = await issued(maker, mill, "10")
        idem = key()
        results = await asyncio.gather(
            *(try_receive(maker, mill, order_id, material_id, "4", idem=idem) for _ in range(6))
        )
        assert sorted(results) == ["ok"] + ["replay"] * 5
        async with session.begin():
            assert await balance_milli_in(session, mill.fabric, mill.recv, "AVAILABLE") == 4_000
        assert await drift(maker, mill.org_id) == []

    async def test_receiving_and_closing_race_without_closing_an_unbalanced_order(
        self, maker, mill
    ) -> None:
        """A close that lands before the last receipt is refused; one after it succeeds. Either
        way the order is never closed with material still out."""
        order_id, material_id = await issued(maker, mill, "10")
        close_ctx = ctx_for(mill.org_id, "operations.jobwork.order.approve", Scope.ORG)

        async def try_close() -> str:
            async def work(s: AsyncSession) -> str:
                order = await services.close_order(s, close_ctx, order_id)
                return order.status

            try:
                return await in_tx(maker, mill.org_id, work)  # type: ignore[no-any-return]
            except ConflictProblem:
                return "refused"

        results = await asyncio.gather(
            try_receive(maker, mill, order_id, material_id, "10"), try_close(), try_close()
        )
        assert results[0] == "ok"
        assert set(results[1:]) <= {"CLOSED", "refused"}
        assert results[1:].count("CLOSED") <= 1

        async def status(s: AsyncSession) -> tuple[str, int]:
            row = (
                await s.execute(
                    text(
                        f"""
                        SELECT o.status, m.sent_milli - m.received_milli - m.rejected_milli
                               - m.shortage_milli
                        FROM {SCHEMA}.job_work_orders o
                        JOIN {SCHEMA}.job_work_materials m ON m.order_id = o.id WHERE o.id = :o
                        """  # noqa: S608
                    ),
                    {"o": order_id},
                )
            ).one()
            return row[0], int(row[1])

        final, outstanding = await in_tx(maker, mill.org_id, status)
        assert outstanding == 0
        assert final in ("RECEIVED", "CLOSED")
        assert await drift(maker, mill.org_id) == []
