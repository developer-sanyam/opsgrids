"""Manufacturing against real PostgreSQL row locks.

Every worker is its own connection and transaction as the NOBYPASSRLS app role, all released at
once with `asyncio.gather` — no application-level lock, only the order-row lock manufacturing
takes first and the balance/reservation locks the stock kernel takes underneath it. Whatever the
interleaving, stock never goes negative, nothing is double-issued, and the reconciler finds zero
drift afterwards.
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
from app.inventory.reconcile import reconcile_organization
from app.manufacturing import services
from app.manufacturing.schemas import IssueIn, IssueLineIn, OutputIn, ReserveIn
from app.models import SCHEMA, InventoryBalance
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for
from tests.inventory_fixtures import balance, q
from tests.manufacturing_fixtures import Mfg, build_mfg, make_bom, make_order, material, ok, stock

pytestmark = pytest.mark.integration

Work = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture
async def m(session, org_id) -> Mfg:
    return await build_mfg(session, org_id)


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=40,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def race(maker, org_id: uuid.UUID, works: list[Work]) -> list[str]:  # type: ignore[no-untyped-def]
    """Each work in its own tenant-bound transaction, all at once. Per work: `ok`, or the class
    name of what it raised (`InsufficientStock`, `InsufficientWip`, `ConflictProblem`, ...)."""

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


async def drift(session, org_id: uuid.UUID) -> int:
    async with session.begin():
        return len((await reconcile_organization(session, org_id)).findings)


async def never_negative(session, org_id: uuid.UUID) -> None:
    async with session.begin():
        rows = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id
                )
            )
        ).all()
    assert all(qty >= 0 and 0 <= res <= qty for qty, res in rows), rows


def ctx(m: Mfg, perm: str = "operations.manufacturing.production_order.issue"):  # type: ignore[no-untyped-def]
    return ctx_for(m.org_id, perm, Scope.ORG)


async def orders_for(client, m: Mfg, count: int, quantity: str) -> list[dict]:  # type: ignore[no-untyped-def]
    return [await make_order(client, m, m.shirt, quantity) for _ in range(count)]


def adhoc_issue(m: Mfg, order: dict, quantity: str) -> Work:
    line = IssueLineIn(
        material_id=uuid.UUID(material(order, "COT-180-NAVY")["id"]),
        location_id=m.t.weaving,
        quantity=quantity,
    )

    async def work(s: AsyncSession) -> None:
        await services.issue_materials(
            s, ctx(m), uuid.UUID(order["id"]), IssueIn(lines=[line], over_issue=True)
        )

    return work


class TestCompetingOrders:
    async def test_two_issues_for_the_same_stock_never_go_negative(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        first, second = await orders_for(client, m, 2, "60")
        outcome = await race(
            maker, m.org_id, [adhoc_issue(m, first, "60"), adhoc_issue(m, second, "60")]
        )
        assert sorted(outcome) == ["InsufficientStock", "ok"]
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving) == (q("40"), 0)
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == (
            q("60"),
            0,
        )
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0

    async def test_ten_orders_race_for_one_hundred_and_exactly_six_get_fifteen(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        orders = await orders_for(client, m, 10, "15")
        outcome = await race(maker, m.org_id, [adhoc_issue(m, o, "15") for o in orders])
        assert outcome.count("ok") == 6 and outcome.count("InsufficientStock") == 4
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving) == (q("10"), 0)
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == (
            q("90"),
            0,
        )
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0

    async def test_competing_reservations_never_promise_more_than_exists(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        orders = await orders_for(client, m, 4, "40")  # 160 wanted, 100 exist

        def reserve(order: dict) -> Work:
            async def work(s: AsyncSession) -> None:
                await services.reserve_materials(
                    s, ctx(m), uuid.UUID(order["id"]), ReserveIn(), key=None
                )

            return work

        outcome = await race(maker, m.org_id, [reserve(o) for o in orders])
        assert outcome == ["ok"] * 4
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving) == (q("100"), q("100"))
        covered = []
        for o in orders:
            detail = await ok(client, m.org_id, "GET", f"/orders/{o['id']}")
            covered.append(q(material(detail, "COT-180-NAVY")["reserved"]))
        assert sum(covered) == q("100") and max(covered) <= q("40")
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0

    async def test_a_reservation_protects_an_order_from_a_later_ad_hoc_issue(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        holder, taker = await orders_for(client, m, 2, "70")
        await ok(client, m.org_id, "POST", f"/orders/{holder['id']}/reserve", json={})
        # 70 reserved for `holder`; `taker` wants 70 ad hoc but only 30 are free
        outcome = await race(maker, m.org_id, [adhoc_issue(m, taker, "70")])
        assert outcome == ["InsufficientStock"]
        issued = await ok(client, m.org_id, "POST", f"/orders/{holder['id']}/issue", json={})
        assert material(issued, "COT-180-NAVY")["issued"] == "70"
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0


class TestOneOrderUnderContention:
    async def test_two_outputs_cannot_both_consume_the_same_wip(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "2", "0")])
        (order,) = await orders_for(client, m, 1, "7")  # 14 m issued
        oid = uuid.UUID(order["id"])
        await ok(client, m.org_id, "POST", f"/orders/{oid}/reserve", json={})
        await ok(client, m.org_id, "POST", f"/orders/{oid}/issue", json={})

        def output(quantity: str) -> Work:
            async def work(s: AsyncSession) -> None:
                await services.record_output(
                    s,
                    ctx(m, "operations.manufacturing.production_order.complete"),
                    oid,
                    OutputIn(quantity=quantity),
                )

            return work

        # 4 + 4 shirts need 16 m but only 14 m are in WIP: exactly one gets through
        outcome = await race(maker, m.org_id, [output("4"), output("4")])
        assert sorted(outcome) == ["InsufficientWip", "ok"]
        detail = await ok(client, m.org_id, "GET", f"/orders/{oid}")
        assert detail["produced_quantity"] == "4"
        assert material(detail, "COT-180-NAVY")["wip"] == "6"
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0

    async def test_a_retried_issue_with_one_key_issues_once(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        (order,) = await orders_for(client, m, 1, "20")
        oid = uuid.UUID(order["id"])
        await ok(client, m.org_id, "POST", f"/orders/{oid}/reserve", json={})

        def issue() -> Work:
            async def work(s: AsyncSession) -> None:
                await services.issue_materials(s, ctx(m), oid, IssueIn(), key="same-key")

            return work

        outcome = await race(maker, m.org_id, [issue() for _ in range(8)])
        assert outcome == ["ok"] * 8
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == (
            q("20"),
            0,
        )
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving) == (q("80"), 0)
        detail = await ok(client, m.org_id, "GET", f"/orders/{oid}")
        assert material(detail, "COT-180-NAVY")["issued"] == "20"
        assert await drift(session, m.org_id) == 0

    async def test_issue_racing_cancel_leaves_a_consistent_order(
        self, client, session, maker, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        (order,) = await orders_for(client, m, 1, "20")
        oid = uuid.UUID(order["id"])
        await ok(client, m.org_id, "POST", f"/orders/{oid}/reserve", json={})

        async def issue(s: AsyncSession) -> None:
            await services.issue_materials(s, ctx(m), oid, IssueIn())

        async def cancel(s: AsyncSession) -> None:
            await services.cancel_order(s, ctx(m), oid, reason="race")

        outcome = await race(maker, m.org_id, [issue, cancel])
        detail = await ok(client, m.org_id, "GET", f"/orders/{oid}")
        if detail["status"] == "CANCELLED":
            # cancel won: nothing was issued and the reservation is gone
            assert await balance(session, m.org_id, m.t.fabric, m.t.weaving) == (q("100"), 0)
            assert "ok" in outcome
        else:
            # issue won: material is in WIP, so cancel was refused
            assert detail["status"] == "IN_PROGRESS" and "ConflictProblem" in outcome
            assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == (
                q("20"),
                0,
            )
        await never_negative(session, m.org_id)
        assert await drift(session, m.org_id) == 0
