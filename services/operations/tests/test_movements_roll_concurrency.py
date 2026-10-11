"""A transfer dispatch of a roll racing a split of that roll (ADR-0014 §12: lock order is item ->
roll -> balance everywhere; a roll is split only when it lives in one bucket).

Dispatch posts its lines through `ledger.post_move`, so it takes the roll `FOR SHARE` before any
balance lock and the split's `FOR UPDATE` on the roll queues it — the loser is refused cleanly
(`ConflictProblem` / `InsufficientStock`), never a `DeadlockDetectedError`. The in-transit bucket
is part of "lives in one bucket": a roll with a portion on its way is refused a split with
`roll_in_several_buckets`, so no child roll can be made while part of the parent is in transit.

Each worker is its own connection and transaction as the NOBYPASSRLS app role, started together
with `asyncio.gather`. Repeated, because a lock-order bug only shows on some interleavings.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import tracking_services as tracking
from app.inventory.tracking_schemas import RollSplitIn, SplitChildIn
from app.models import SCHEMA, Roll
from app.movements import transfers
from app.movements.schemas import (
    TransferDecisionIn,
    TransferDispatchIn,
    TransferIn,
    TransferLineIn,
)
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for
from tests.inventory_fixtures import q
from tests.movements_fixtures import in_transit_total
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
        pool_size=10,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def race(maker, org_id: uuid.UUID, works: list[Work]) -> list[str]:  # type: ignore[no-untyped-def]
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


async def approved_roll_transfer(session, w: World, quantity: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
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
                        inventory_item_id=w.t.fabric,
                        from_location_id=w.t.weaving,
                        quantity=quantity,
                        lot_id=w.lot,
                        roll_id=w.roll,
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


def dispatch(org_id: uuid.UUID, transfer_id: uuid.UUID) -> Work:
    async def work(s: AsyncSession) -> None:
        await transfers.dispatch_transfer(
            s,
            ctx(org_id, "operations.inventory.transfer.dispatch"),
            transfer_id,
            TransferDispatchIn(),
            idempotency_key=None,
        )

    return work


def split(w: World) -> Work:
    async def work(s: AsyncSession) -> None:
        await tracking.split_roll(
            s,
            w.t.ctx,
            w.roll,
            RollSplitIn(
                location_id=w.t.weaving,
                children=[SplitChildIn(quantity="50"), SplitChildIn(quantity="32.5")],
            ),
            idempotency_key=None,
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


@pytest.mark.parametrize("attempt", range(4))
@pytest.mark.parametrize("quantity", ["82.5", "30"], ids=["whole-roll", "part-of-roll"])
async def test_a_dispatch_racing_a_split_of_the_same_roll_has_one_winner(
    session, maker, org_id, world, quantity, attempt
) -> None:
    w = world
    transfer_id = await approved_roll_transfer(session, w, quantity)
    rolls_before = await roll_count(session, org_id)

    outcomes = await race(maker, org_id, [dispatch(org_id, transfer_id), split(w)])

    assert outcomes.count("ok") == 1, outcomes
    loser = next(o for o in outcomes if o != "ok")
    # A dispatch that loses finds the roll consumed; a split that loses finds the roll in two
    # buckets (part in transit) or empty. Never a raw database error such as a deadlock.
    assert loser in {"ConflictProblem", "InsufficientStock"}, outcomes
    on_site = (await bucket_qty(session, w, lot=w.lot, roll=w.roll))[0]
    in_transit = await in_transit_total(session, org_id, w.t.fabric)
    assert on_site >= 0 and in_transit >= 0
    if outcomes[0] == "ok":  # the dispatch won: nothing was split, the roll is intact
        assert await roll_count(session, org_id) == rolls_before
        assert in_transit == q(quantity)
        assert on_site == q("82.5") - q(quantity)
    else:  # the split won: the parent is consumed and nothing went in transit
        assert await roll_count(session, org_id) == rolls_before + 2
        assert in_transit == 0
        assert on_site == 0
    await assert_reconciled(session, org_id)
