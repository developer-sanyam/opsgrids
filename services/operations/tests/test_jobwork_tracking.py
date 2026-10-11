"""Job work over Phase 1 lots and rolls: a lot or roll a material names must exist in the caller's
organization and belong to its item (404 / 422 at drafting time), and a receipt that touches several
rolls takes their locks before any balance lock.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import ledger
from app.inventory import tracking_services as tracking
from app.inventory.ledger import MoveLine
from app.inventory.tracking_schemas import LotIn, RollIn
from app.jobwork import services as jw_services
from app.jobwork.schemas import JobWorkReceiptLineIn
from app.models import SCHEMA, InventoryItem
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for
from tests.jobwork_fixtures import (
    Mill,
    approve,
    build_mill,
    call,
    create_order,
    issue,
    key,
    order_body,
)
from tests.tracking_fixtures import make_foreign_world

pytestmark = pytest.mark.integration


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


@dataclass
class Tracked:
    lot: uuid.UUID
    roll_a: uuid.UUID  # 40 m
    roll_b: uuid.UUID  # 30 m
    thread_lot: uuid.UUID


async def track(session: AsyncSession, mill: Mill) -> Tracked:
    """Switch the fabric to lot + roll tracking, register one lot and two rolls, and stock the
    rolls at the home bin; give the thread a lot of its own (tracked by lot only)."""
    ctx = ctx_for(mill.org_id, "operations.inventory.stock.adjust", Scope.ORG)
    async with session.begin():
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == mill.org_id, InventoryItem.id == mill.fabric)
            .values(track_lot=True, track_roll=True)
        )
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == mill.org_id, InventoryItem.id == mill.thread)
            .values(track_lot=True)
        )
        lot = await tracking.create_lot(
            session, ctx, LotIn(inventory_item_id=mill.fabric, lot_no="GREIGE-1")
        )
        thread_lot = await tracking.create_lot(
            session, ctx, LotIn(inventory_item_id=mill.thread, lot_no="THREAD-1")
        )
        rolls = []
        for roll_no, qty in (("RA", "40"), ("RB", "30")):
            roll = await tracking.create_roll(
                session,
                ctx,
                RollIn(
                    inventory_item_id=mill.fabric, lot_id=lot.id, roll_no=roll_no, primary_qty=qty
                ),
            )
            await ledger.post_move(
                session,
                ctx,
                kind="receipt",
                lines=[
                    MoveLine(
                        item_id=mill.fabric,
                        location_id=mill.home,
                        quantity_milli=int(qty) * 1000,
                        lot_id=lot.id,
                        roll_id=roll.id,
                    )
                ],
                occurred_on=date.today(),
            )
            rolls.append(roll.id)
    return Tracked(lot=lot.id, roll_a=rolls[0], roll_b=rolls[1], thread_lot=thread_lot.id)


def material(mill: Mill, **over) -> dict:  # type: ignore[no-untyped-def]
    return {"item_id": str(mill.fabric), "quantity": "10", **over}


async def draft(client, mill: Mill, materials: list[dict]):  # type: ignore[no-untyped-def]
    return await call(
        client,
        mill.org_id,
        "POST",
        "/orders",
        json=order_body(mill, materials=materials),
        idem=key(),
    )


class TestNamedLotsAndRollsAreChecked:
    async def test_real_lots_and_rolls_draft_fine(self, client, session, mill: Mill) -> None:
        t = await track(session, mill)
        response = await draft(
            client,
            mill,
            [
                material(mill, lot_id=str(t.lot), roll_id=str(t.roll_a), quantity="40"),
                material(mill, lot_id=str(t.lot), roll_id=str(t.roll_b), quantity="30"),
            ],
        )
        assert response.status_code == 201, response.text

    @pytest.mark.parametrize("what", ["lot_id", "roll_id"])
    async def test_an_unknown_lot_or_roll_is_404(
        self, client, session, mill: Mill, what: str
    ) -> None:
        await track(session, mill)
        response = await draft(client, mill, [material(mill, **{what: str(uuid.uuid4())})])
        assert response.status_code == 404, response.text

    async def test_another_organizations_lot_and_roll_are_404(
        self, client, session, mill: Mill, admin_session
    ) -> None:
        await track(session, mill)
        foreign = await make_foreign_world(admin_session)
        for over in ({"lot_id": str(foreign.lot)}, {"roll_id": str(foreign.roll)}):
            response = await draft(client, mill, [material(mill, **over)])
            assert response.status_code == 404, response.text

    async def test_a_lot_of_another_item_is_422(self, client, session, mill: Mill) -> None:
        t = await track(session, mill)
        response = await draft(client, mill, [material(mill, lot_id=str(t.thread_lot))])
        assert response.status_code == 422, response.text

    async def test_a_roll_under_a_different_lot_is_422(self, client, session, mill: Mill) -> None:
        t = await track(session, mill)
        response = await draft(client, mill, [material(mill, roll_id=str(t.roll_a))])
        # The roll belongs to the lot; naming it without the lot is the same mismatch a move has.
        assert response.status_code == 422, response.text

    async def test_nothing_is_drafted_by_a_refused_request(
        self, client, session, mill: Mill
    ) -> None:
        await track(session, mill)
        await draft(client, mill, [material(mill, lot_id=str(uuid.uuid4()))])
        listing = await call(client, mill.org_id, "GET", "/orders")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 0


class TestRollLocksPrecedeBalanceLocks:
    async def test_a_receipt_waits_on_a_held_roll_before_it_locks_any_balance(
        self, client, session, mill: Mill, org_id
    ) -> None:
        """Receive roll A good and declare roll B short. The return move locks A's balances; the
        shortage move would lock B's roll only afterwards. Hold B as a splitter would, start the
        receipt, and prove it is parked on a roll while A's vendor-bucket balance is still free."""
        t = await track(session, mill)
        order = await create_order(
            client,
            mill,
            materials=[
                material(mill, lot_id=str(t.lot), roll_id=str(t.roll_a), quantity="40"),
                material(mill, lot_id=str(t.lot), roll_id=str(t.roll_b), quantity="30"),
            ],
        )
        await approve(client, mill, order["id"])
        issued = await issue(client, mill, order["id"])
        by_roll = {m["roll_id"]: m["id"] for m in issued["materials"]}
        lines = [
            JobWorkReceiptLineIn(material_id=by_roll[str(t.roll_a)], received="40"),
            JobWorkReceiptLineIn(material_id=by_roll[str(t.roll_b)], shortage="30"),
        ]

        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        def bind(s: AsyncSession) -> None:
            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        ctx = ctx_for(org_id, "operations.jobwork.order.receive", Scope.ORG)
        try:
            async with maker() as holder, maker() as probe, maker() as worker:
                for s in (holder, probe, worker):
                    bind(s)
                await holder.begin()
                await holder.execute(
                    text("SELECT id FROM rolls WHERE id = :r FOR UPDATE"), {"r": t.roll_b}
                )

                async def receive() -> None:
                    async with worker.begin():
                        await jw_services.receive_order(
                            worker,
                            ctx,
                            uuid.UUID(order["id"]),
                            lines=lines,
                            received_on=None,
                            note="",
                            idempotency_key=key(),
                        )

                task = asyncio.create_task(receive())
                await asyncio.sleep(1.0)
                assert not task.done(), "the receipt should be parked on the held roll"

                await probe.begin()
                free = await probe.execute(
                    text(
                        "SELECT id FROM inventory_balances WHERE inventory_item_id = :i "
                        "AND roll_id = :r AND status = 'AT_JOB_WORKER' FOR UPDATE NOWAIT"
                    ),
                    {"i": mill.fabric, "r": t.roll_a},
                )
                assert free.all(), "roll A's vendor-bucket balance must not be locked yet"
                await probe.rollback()

                await holder.rollback()
                await asyncio.wait_for(task, timeout=30)
        finally:
            await engine.dispose()
