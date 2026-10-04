"""The large-organization path: a run over more demand than `mrp_sync_max_demands` (or one asked
for with `mode: worker`) is stored `QUEUED`, executed by `MrpRunSweep` as the real outbox
(discovery) and app (work) roles, and ends up byte-for-byte the run a small organization would
have got synchronously."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import SCHEMA, AuditLog, OutboxEvent
from app.mrp.models import MrpRun
from app.mrp.sweep import MrpRunSweep
from platform_core.db import TENANT_GUC
from tests.mrp_fixtures import (
    Plant,
    add_purchase_order,
    add_sales_order,
    build_plant,
    mrp,
    mrp_ok,
    shortages,
    start_run,
    stock_in,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def sweep_factory():  # type: ignore[no-untyped-def]
    """A `MrpRunSweep` wired like production: discovery as `opsgrids_outbox`, work as the app
    role with the tenant bound transaction-locally."""
    connect = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    discovery = create_async_engine(settings().outbox_dsn, connect_args=connect)
    work = create_async_engine(settings().app_dsn, connect_args=connect)
    maker = async_sessionmaker(work, expire_on_commit=False)

    @asynccontextmanager
    async def scope(org_id):  # type: ignore[no-untyped-def]
        async with maker() as s, s.begin():
            await s.execute(
                text("SELECT set_config(:g, :o, true)"), {"g": TENANT_GUC, "o": str(org_id)}
            )
            yield s

    def make() -> MrpRunSweep:
        return MrpRunSweep(discovery_factory=async_sessionmaker(discovery), work_scope=scope)

    try:
        yield make
    finally:
        await discovery.dispose()
        await work.dispose()


@pytest_asyncio.fixture
async def p(client, session, org_id) -> Plant:
    plant = await build_plant(client, session, org_id)
    await stock_in(session, plant, panel="2", fabric="5", dye="0.5")
    await add_purchase_order(session, org_id, "PO-1", [("BTN-14MM", 20, 5)])
    await add_sales_order(session, org_id, "SO-1", [(plant.m.shirt, "10")])
    return plant


class TestQueueing:
    async def test_a_forced_worker_run_is_queued_with_nothing_computed(
        self, client, session, p: Plant
    ) -> None:
        run = await start_run(client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker")
        assert (run["status"], run["execution"]) == ("QUEUED", "WORKER")
        assert (
            run["demand_count"] == 0 and run["shortage_count"] == 0 and run["result_hash"] is None
        )
        assert (await shortages(client, p.org_id, run["id"])) == {}

    async def test_auto_queues_when_open_demand_exceeds_the_limit(
        self, client, session, p: Plant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings(), "mrp_sync_max_demands", 2)
        await add_sales_order(
            session, p.org_id, "SO-2", [(p.m.shirt, "1"), (p.m.panel, "1"), (p.t.buttons, "1")]
        )
        preview = await mrp_ok(client, p.org_id, "GET", "/demand-sources")
        assert preview["sales_order_lines"] == 4 and preview["mode_if_auto"] == "worker"
        queued = await start_run(client, p.org_id, expect=202, sources=["SALES_ORDER"])
        assert queued["execution"] == "WORKER" and queued["status"] == "QUEUED"
        # a run that does not read orders is under the limit, so it is synchronous
        small = await start_run(
            client,
            p.org_id,
            sources=["MANUAL"],
            manual_demands=[{"item_id": str(p.m.panel), "quantity": "1"}],
        )
        assert small["execution"] == "SYNC" and small["status"] == "COMPLETED"

    async def test_under_the_limit_stays_synchronous(self, client, session, p: Plant) -> None:
        run = await start_run(client, p.org_id, sources=["SALES_ORDER"])
        assert run["execution"] == "SYNC"

    async def test_a_replayed_queued_request_is_not_queued_twice(
        self, client, session, p: Plant
    ) -> None:
        body = {"sources": ["SALES_ORDER"], "mode": "worker"}
        a = await mrp(client, p.org_id, "POST", "/runs", json=body, key="w-1")
        b = await mrp(client, p.org_id, "POST", "/runs", json=body, key="w-1")
        assert (a.status_code, b.status_code) == (202, 200)
        assert a.json()["id"] == b.json()["id"]


class TestTheSweep:
    async def test_the_sweep_completes_a_queued_run_identically_to_a_synchronous_one(
        self, client, session, p: Plant, sweep_factory
    ) -> None:
        sync = await start_run(client, p.org_id, sources=["SALES_ORDER"])
        queued = await start_run(
            client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        report = await sweep_factory().tick()
        assert (report.organizations, report.completed, report.failed, report.failed_runs) == (
            1,
            1,
            0,
            0,
        )
        done = await mrp_ok(client, p.org_id, "GET", f"/runs/{queued['id']}")
        assert done["status"] == "COMPLETED" and done["execution"] == "WORKER"
        assert done["completed_at"] is not None
        assert done["result_hash"] == sync["result_hash"]
        a = await shortages(client, p.org_id, sync["id"])
        b = await shortages(client, p.org_id, queued["id"])
        strip = lambda rows: {  # noqa: E731
            k: {f: v for f, v in row.items() if f not in ("id", "run_id")}
            for k, row in rows.items()
        }
        assert strip(a) == strip(b) and a
        # the requisition path works on a worker-made run
        out = await mrp_ok(
            client, p.org_id, "POST", f"/runs/{queued['id']}/requisitions", expect=201, json={}
        )
        assert {ln["sku"] for ln in out["lines"]} == {"BTN-14MM", "COT-180-NAVY", "DYE-RED-01"}

    async def test_the_sweep_audits_and_emits_for_a_worker_run(
        self, client, session, p: Plant, sweep_factory
    ) -> None:
        queued = await start_run(
            client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        await sweep_factory().tick()
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent.payload).where(
                            OutboxEvent.organization_id == p.org_id,
                            OutboxEvent.event_type == "operations.mrp.run_completed",
                        )
                    )
                )
                .scalars()
                .all()
            )
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.organization_id == p.org_id)
                    )
                )
                .scalars()
                .all()
            )
        assert [e["run_id"] for e in events] == [queued["id"]]
        assert events[0]["execution"] == "WORKER"
        assert "mrp_run.completed" in actions

    async def test_one_failing_run_fails_alone_and_the_next_still_runs(
        self, client, session, admin_session, p: Plant, sweep_factory
    ) -> None:
        good_first = await start_run(
            client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        # a run whose BOM loops: tamper below the publish guard, queue, tamper back for the next
        await admin_session.execute(
            text("ALTER TABLE operations.bom_components DISABLE TRIGGER USER")
        )
        await admin_session.execute(
            text(
                "UPDATE operations.bom_components SET component_item_id = :shirt "
                "WHERE organization_id = :o AND component_item_id = :fabric"
            ),
            {"shirt": p.m.shirt, "fabric": p.t.fabric, "o": p.org_id},
        )
        await admin_session.execute(
            text("ALTER TABLE operations.bom_components ENABLE TRIGGER USER")
        )
        await admin_session.commit()
        bad = await start_run(client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker")
        report = await sweep_factory().tick()
        assert report.failed == 0  # the organization's pass itself did not fail
        assert (report.completed, report.failed_runs) == (0, 2)  # both see the loop now
        row = await mrp_ok(client, p.org_id, "GET", f"/runs/{bad['id']}")
        assert row["status"] == "FAILED"
        assert "SHIRT-01" in row["error"] and "PANEL-NAVY" in row["error"]
        first = await mrp_ok(client, p.org_id, "GET", f"/runs/{good_first['id']}")
        assert first["status"] == "FAILED"  # same data, same loop: failures are not flukes
        # a failed run has nothing stored and cannot be requisitioned
        assert (await shortages(client, p.org_id, bad["id"])) == {}
        refused = await mrp(client, p.org_id, "POST", f"/runs/{bad['id']}/requisitions", json={})
        assert refused.status_code == 409
        # and the next tick finds nothing left to do
        again = await sweep_factory().tick()
        assert (again.organizations, again.completed) == (0, 0)

    async def test_every_organization_with_a_queued_run_is_served_and_each_sees_only_its_own(
        self, client, session, p: Plant, sweep_factory
    ) -> None:
        stranger = uuid.uuid4()
        mine = await start_run(client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker")
        theirs = await start_run(
            client, stranger, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        report = await sweep_factory().tick()
        assert (report.organizations, report.completed, report.failed) == (2, 2, 0)
        assert (await mrp_ok(client, p.org_id, "GET", f"/runs/{mine['id']}"))["demand_count"] == 1
        theirs_done = await mrp_ok(client, stranger, "GET", f"/runs/{theirs['id']}")
        assert theirs_done["status"] == "COMPLETED" and theirs_done["demand_count"] == 0
        # neither sees the other's run
        assert (await mrp(client, stranger, "GET", f"/runs/{mine['id']}")).status_code == 404
        assert (await mrp(client, p.org_id, "GET", f"/runs/{theirs['id']}")).status_code == 404

    async def test_two_sweeps_at_once_run_a_queued_run_exactly_once(
        self, client, session, p: Plant, sweep_factory
    ) -> None:
        queued = await start_run(
            client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        a, b = sweep_factory(), sweep_factory()
        ra, rb = await asyncio.gather(a.tick(), b.tick())
        assert ra.completed + rb.completed == 1
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == p.org_id,
                            OutboxEvent.event_type == "operations.mrp.run_completed",
                        )
                    )
                )
                .scalars()
                .all()
            )
            run = (
                await session.execute(select(MrpRun).where(MrpRun.id == uuid.UUID(queued["id"])))
            ).scalar_one()
        assert len(events) == 1 and run.status == "COMPLETED"

    async def test_the_discovery_role_cannot_read_a_runs_payload(self, p: Plant) -> None:
        engine = create_async_engine(
            settings().outbox_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with engine.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text("SELECT params FROM mrp_runs"))
        finally:
            await engine.dispose()

    async def test_a_large_run_completes_in_one_pass(
        self, client, session, p: Plant, sweep_factory
    ) -> None:
        """A few hundred demand lines through the worker, results stored per run."""
        for i in range(60):
            await add_sales_order(
                session,
                p.org_id,
                f"SO-L{i:03d}",
                [(p.m.shirt, "1"), (p.m.panel, "1"), (p.t.buttons, "3"), (p.t.fabric, "1")],
            )
        queued = await start_run(
            client, p.org_id, expect=202, sources=["SALES_ORDER"], mode="worker"
        )
        report = await sweep_factory().tick()
        assert report.completed == 1
        row = await mrp_ok(client, p.org_id, "GET", f"/runs/{queued['id']}")
        assert row["status"] == "COMPLETED" and row["demand_count"] == 1 + 60 * 4
        s = await shortages(client, p.org_id, queued["id"])
        # 70 shirts, 70 + 60 panels wanted, 2 on hand -> make 128 panels
        assert s["SHIRT-01"]["shortage"] == "70"
        assert s["PANEL-NAVY"]["gross"] == "144"  # 84 from shirts (1.2 m each) + 60 direct
        assert s["PANEL-NAVY"]["shortage"] == "142"
