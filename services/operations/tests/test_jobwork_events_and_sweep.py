"""Job-work events and the overdue sweep (Inventory plan Phase 6).

Every event the module stages is validated, as the code actually built it, against the JSON Schema
committed for it in `libs/contracts/events/` — and the sweep runs exactly as production runs it:
discovery as the real `opsgrids_outbox` role (which may read the columns `env.py` granted and
nothing else), work as the app role under a tenant binding.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.jobwork.sweep import JobWorkOverdueSweep
from app.models import SCHEMA, AuditLog, OutboxEvent
from platform_core.db import TENANT_GUC
from tests.jobwork_fixtures import (
    Mill,
    approve,
    build_mill,
    call,
    create_order,
    issue,
    issued_order,
    receipt_line,
    receive,
)

pytestmark = pytest.mark.integration

EVENTS = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "events"
JOBWORK_EVENTS = (
    "operations.jobwork.issued",
    "operations.jobwork.received",
    "operations.jobwork.overdue",
    "operations.jobwork.closed",
    "operations.jobwork.challan.eway_requested",
    "operations.jobwork.receipt.qc_requested",
)


def schema_for(event_type: str) -> Draft202012Validator:
    path = EVENTS / event_type / "v1.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def test_every_jobwork_event_has_a_committed_schema() -> None:
    for event_type in JOBWORK_EVENTS:
        document = json.loads((EVENTS / event_type / "v1.schema.json").read_text("utf-8"))
        Draft202012Validator.check_schema(document)
        assert document["$id"] == f"opsgrids://contracts/{event_type}/v1"


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


@pytest.fixture
async def stack():  # type: ignore[no-untyped-def]
    """The sweep's two connections: the outbox role for discovery, the app role for work."""
    connect = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    discovery = create_async_engine(settings().outbox_dsn, connect_args=connect)
    work = create_async_engine(settings().app_dsn, connect_args=connect)
    work_maker = async_sessionmaker(work, expire_on_commit=False)

    @asynccontextmanager
    async def scope(org_id):  # type: ignore[no-untyped-def]
        async with work_maker() as s, s.begin():
            await s.execute(
                text("SELECT set_config(:g, :o, true)"), {"g": TENANT_GUC, "o": str(org_id)}
            )
            yield s

    yield async_sessionmaker(discovery), scope
    await discovery.dispose()
    await work.dispose()


async def staged(session, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.event_type == event_type)
                    .order_by(OutboxEvent.created_at)
                )
            )
            .scalars()
            .all()
        )


class TestEventContracts:
    async def test_every_event_the_flow_stages_matches_its_schema(
        self, client, session, mill
    ) -> None:
        # qc_required + a high-value fabric would be ideal; the thread crosses the e-way threshold
        order = await create_order(
            client,
            mill,
            qc_required=True,
            due_date=str(date.today() - timedelta(days=2)),
            materials=[
                {"item_id": str(mill.fabric), "quantity": "100", "location_id": str(mill.home)},
                {"item_id": str(mill.thread), "quantity": "200"},
            ],
        )
        await approve(client, mill, order["id"])
        issued = await issue(
            client,
            mill,
            order["id"],
            body={
                "vehicle_no": "MH12AB1234",
                "transporter_id": "27AAAAA0000A1Z5",
                "distance_km": 90,
            },
        )
        # Rs 40,000 + Rs 10,000 = exactly the threshold: request the bill by hand as well
        await call(
            client,
            mill.org_id,
            "POST",
            f"/challans/{issued['challans'][0]['id']}:request-eway",
            json={"distance_km": 90},
        )
        lines = [
            receipt_line(
                m["id"], received=m["quantity"] if i else "60", rejected="0" if i else "10"
            )
            for i, m in enumerate(issued["materials"])
        ]
        await receive(client, mill, order["id"], lines)
        await receive(
            client,
            mill,
            order["id"],
            [receipt_line(issued["materials"][0]["id"], shortage="30")],
        )
        closed = await call(client, mill.org_id, "POST", f"/orders/{order['id']}:close")
        assert closed.status_code == 200, closed.text

        seen: set[str] = set()
        for event_type in JOBWORK_EVENTS:
            if event_type == "operations.jobwork.overdue":
                continue  # raised by the sweep; covered below
            rows = await staged(session, event_type)
            assert rows, f"{event_type} was never staged"
            seen.add(event_type)
            validator = schema_for(event_type)
            for row in rows:
                errors = [e.message for e in validator.iter_errors(row.payload)]
                assert not errors, (event_type, errors, row.payload)
        assert len(seen) == 5

    async def test_the_overdue_event_matches_its_schema(self, client, session, stack, mill) -> None:
        await issued_order(client, mill, due_date=str(date.today() - timedelta(days=4)))
        discovery, scope = stack
        await JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope).tick()
        [row] = await staged(session, "operations.jobwork.overdue")
        errors = [
            e.message for e in schema_for("operations.jobwork.overdue").iter_errors(row.payload)
        ]
        assert not errors, errors
        assert row.payload["days_overdue"] == 4
        assert row.payload["supplier_name"] == "Rainbow Dyers"
        assert row.payload["outstanding"] == "82.5"


class TestOverdueSweep:
    async def test_announces_each_overdue_order_exactly_once(
        self, client, session, stack, mill
    ) -> None:
        late = await issued_order(client, mill, due_date=str(date.today() - timedelta(days=3)))
        await issued_order(
            client,
            mill,
            due_date=str(date.today() + timedelta(days=3)),
            materials=[{"item_id": str(mill.fabric), "quantity": "1"}],
        )
        no_due_date = await create_order(client, mill)  # a draft, never overdue
        discovery, scope = stack
        sweep = JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope)

        first = await sweep.tick()
        assert (first.organizations, first.announced, first.failed) == (1, 1, 0)
        second = await sweep.tick()
        assert (second.organizations, second.announced, second.failed) == (0, 0, 0)

        events = await staged(session, "operations.jobwork.overdue")
        assert [e.payload["order_id"] for e in events] == [late["id"]]
        assert events[0].payload["order_id"] != no_due_date["id"]
        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "jobwork.order.overdue")
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1
        assert audits[0].actor_id is None  # a system act, not a person's

    async def test_a_partially_received_order_is_still_announced_with_what_is_left(
        self, client, session, stack, mill
    ) -> None:
        order = await issued_order(client, mill, due_date=str(date.today() - timedelta(days=1)))
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], received="30")]
        )
        discovery, scope = stack
        await JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope).tick()
        [event] = await staged(session, "operations.jobwork.overdue")
        assert event.payload["status"] == "PARTIALLY_RECEIVED"
        assert event.payload["outstanding"] == "52.5"

    async def test_a_received_or_closed_order_is_never_announced(
        self, client, session, stack, mill
    ) -> None:
        order = await issued_order(client, mill, due_date=str(date.today() - timedelta(days=5)))
        await receive(
            client, mill, order["id"], [receipt_line(order["materials"][0]["id"], received="82.5")]
        )
        discovery, scope = stack
        report = await JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope).tick()
        assert report.announced == 0
        assert await staged(session, "operations.jobwork.overdue") == []

    async def test_the_clock_decides_what_is_overdue(self, client, session, stack, mill) -> None:
        await issued_order(client, mill, due_date=str(date.today() + timedelta(days=2)))
        discovery, scope = stack
        today = JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope)
        assert (await today.tick()).announced == 0
        later = JobWorkOverdueSweep(
            discovery_factory=discovery,
            work_scope=scope,
            today=lambda: date.today() + timedelta(days=3),
        )
        assert (await later.tick()).announced == 1
        events = await staged(session, "operations.jobwork.overdue")
        assert events[0].payload["days_overdue"] == 1

    async def test_discovery_as_the_outbox_role_reads_only_what_env_py_granted(self, stack) -> None:
        """The F11/F12 failure: a sweep reads a column the BYPASSRLS discovery role was never
        granted, and fails every tick forever. Prove the grant covers the query, and covers
        nothing wider."""
        discovery, _scope = stack
        async with discovery() as s:
            await s.execute(
                text(
                    f"""
                    SELECT DISTINCT organization_id FROM {SCHEMA}.job_work_orders
                    WHERE status IN ('ISSUED', 'PARTIALLY_RECEIVED') AND due_date < current_date
                      AND overdue_notified_at IS NULL AND deleted_at IS NULL
                    """  # noqa: S608
                )
            )
            await s.rollback()
        async with discovery() as s:
            with pytest.raises(Exception, match="permission denied"):
                await s.execute(text(f"SELECT process, notes FROM {SCHEMA}.job_work_orders"))  # noqa: S608
        async with discovery() as s:
            with pytest.raises(Exception, match="permission denied"):
                await s.execute(text(f"SELECT lines FROM {SCHEMA}.challans"))  # noqa: S608

    async def test_one_organizations_failure_does_not_stop_the_next(
        self, client, session, stack, mill, monkeypatch
    ) -> None:
        await issued_order(client, mill, due_date=str(date.today() - timedelta(days=2)))
        discovery, scope = stack
        calls: list[uuid.UUID] = []

        @asynccontextmanager
        async def exploding(org_id):  # type: ignore[no-untyped-def]
            calls.append(org_id)
            raise RuntimeError("tenant down")
            yield  # pragma: no cover

        report = await JobWorkOverdueSweep(discovery_factory=discovery, work_scope=exploding).tick()
        assert (report.organizations, report.announced, report.failed) == (1, 0, 1)
        assert calls == [mill.org_id]
        # and the next pass, healthy again, still finds it: nothing was marked announced
        again = await JobWorkOverdueSweep(discovery_factory=discovery, work_scope=scope).tick()
        assert again.announced == 1
