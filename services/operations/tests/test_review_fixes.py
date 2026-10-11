"""Regressions for the inventory-release review findings (one class per finding)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app import selectors, services
from app.models import SCHEMA
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import ConflictProblem
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

WRITE = "operations.procurement.purchase_order.write"
APPROVE = "operations.procurement.purchase_order.approve"


def _ctx(org_id):
    principal = principal_for(org_id, [f"{WRITE}:ORG", f"{APPROVE}:ORG"])
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=WRITE)


class TestPoTransitionsReReadUnderLock:
    """#7: a stale PO row must not be cancelled/closed/approved/edited after a receipt moved it."""

    async def _stale(self, session, admin_session, data, org_id, status, was=None):
        po_id = data["po_a"].id
        if was:
            async with admin_session.begin():
                await admin_session.execute(
                    text(f'UPDATE "{SCHEMA}".purchase_orders SET status=:s WHERE id=:i'),
                    {"s": was, "i": po_id},
                )
        async with session.begin():
            stale = await selectors.get_purchase_order(session, org_id, po_id)
        async with admin_session.begin():
            await admin_session.execute(
                text(f'UPDATE "{SCHEMA}".purchase_orders SET status=:s WHERE id=:i'),
                {"s": status, "i": po_id},
            )
        return stale

    async def test_cancel_refuses_a_po_received_meanwhile(
        self, session, admin_session, data, org_id
    ):
        stale = await self._stale(session, admin_session, data, org_id, "received")
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.cancel_purchase_order(session, _ctx(org_id), stale, reason="late")

    async def test_close_refuses_a_po_cancelled_meanwhile(
        self, session, admin_session, data, org_id
    ):
        stale = await self._stale(session, admin_session, data, org_id, "cancelled", was="received")
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.close_purchase_order(session, _ctx(org_id), stale)

    async def test_approve_refuses_a_po_cancelled_meanwhile(
        self, session, admin_session, data, org_id
    ):
        stale = await self._stale(session, admin_session, data, org_id, "cancelled", was="draft")
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.approve_purchase_order(session, _ctx(org_id), stale)


class TestManufacturingKernelKey:
    """#10: a long client key must not lose the move index to truncation."""

    def test_long_keys_keep_the_move_index_and_fit(self):
        import types
        import uuid

        from app.manufacturing.services import _key

        order = types.SimpleNamespace(id=uuid.uuid4())
        long = "k" * 128
        keys = {_key(order, "output", long, n) for n in (0, 1, 2)}
        assert len(keys) == 3
        assert all(len(k) <= 128 for k in keys)
        assert _key(order, "output", long + "", 0) != _key(order, "output", "k" * 127, 0)
        assert _key(order, "output", None) is None


class TestSyncLotSerialises:
    """#9: two inspections of one lot deciding at once must not both compute from a stale read."""

    async def test_concurrent_sibling_decisions_leave_the_lot_approved(self, session, org_id):
        import asyncio
        import uuid

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models import Inspection, Lot
        from app.quality.services import _sync_lot
        from platform_core.db import TENANT_GUC
        from tests.helpers import ctx_for
        from tests.inventory_fixtures import build_textile, make_lot

        t = await build_textile(session, org_id)
        lot_id = await make_lot(session, org_id, t.fabric)
        ids = [uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            for i in ids:
                session.add(
                    Inspection(
                        id=i,
                        organization_id=org_id,
                        kind="incoming",
                        status="PENDING",
                        item_id=t.fabric,
                        location_id=t.weaving,
                        lot_id=lot_id,
                        quantity_milli=1000,
                    )
                )
        engine = create_async_engine(
            settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        other = async_sessionmaker(engine, expire_on_commit=False)
        ctx = ctx_for(org_id, "operations.quality.inspection.approve", Scope.ORG)

        async def second() -> None:
            async with other() as s2, s2.begin():
                await s2.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)"))
                insp = (await s2.execute(select(Inspection).where(Inspection.id == ids[1]))).scalar_one()
                insp.status = "PASSED"
                await _sync_lot(s2, ctx, insp)

        try:
            async with session.begin():
                first = (
                    await session.execute(select(Inspection).where(Inspection.id == ids[0]))
                ).scalar_one()
                first.status = "PASSED"
                await _sync_lot(session, ctx, first)  # lot -> PENDING; this tx now owns the lot
                task = asyncio.create_task(second())
                await asyncio.sleep(1.0)  # the sibling has read (or is waiting to read) the lot
            await asyncio.wait_for(task, 5)
            async with session.begin():
                status = (
                    await session.execute(select(Lot.quality_status).where(Lot.id == lot_id))
                ).scalar_one()
            assert status == "APPROVED"
        finally:
            await engine.dispose()


class TestBomGraphSerialises:
    """#8: cycle checks across different BOMs of one organization take turns."""

    async def test_assert_no_cycle_waits_for_the_graph_lock(self, session, org_id):
        import asyncio
        import uuid

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.manufacturing.bom_services import assert_no_cycle
        from platform_core.db import TENANT_GUC

        engine = create_async_engine(
            settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        other = async_sessionmaker(engine, expire_on_commit=False)

        async def second() -> None:
            async with other() as s2, s2.begin():
                await s2.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)"))
                await assert_no_cycle(s2, org_id, uuid.uuid4(), {uuid.uuid4()})

        try:
            async with session.begin():
                await assert_no_cycle(session, org_id, uuid.uuid4(), {uuid.uuid4()})
                task = asyncio.create_task(second())
                done, _ = await asyncio.wait({task}, timeout=1.0)
                assert not done, "a second cycle check ran while the first was uncommitted"
            await asyncio.wait_for(task, 5)
        finally:
            await engine.dispose()


class TestIdempotencyKeysBelongToTheCaller:
    """#6: a key someone else used never replays their result for you."""

    async def test_another_user_presenting_the_same_key_and_body_is_refused(self, session, org_id):
        from app.manufacturing import common

        mine = _ctx(org_id)
        theirs = _ctx(org_id)
        async with session.begin():
            first = await common.claim(session, mine, "issue-PO-12", scope="s", payload={"a": 1})
            first.done(uuid.uuid4())
        async with session.begin():
            again = await common.claim(session, mine, "issue-PO-12", scope="s", payload={"a": 1})
            assert again.replay
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await common.claim(session, theirs, "issue-PO-12", scope="s", payload={"a": 1})


class TestPoLineDefaultsSurviveTheMigration:
    """#4/#11: code from before the release can still insert PO lines after `upgrade head`."""

    async def test_the_new_columns_keep_their_server_defaults(self, admin_session):
        async with admin_session.begin():
            rows = (
                await admin_session.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'purchase_order_lines' "
                        "AND column_name IN ('received_milli', 'short_closed')"
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        assert {r[0]: r[1] is not None for r in rows} == {"received_milli": True, "short_closed": True}
