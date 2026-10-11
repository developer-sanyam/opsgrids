"""Manufacturing hardening: kernel idempotency keys, replayed posts, duplicate BOM lines and the
per-organization BOM-graph lock under two real concurrent transactions."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.manufacturing import bom_services, services
from app.manufacturing.models import ManufacturingRequest
from app.manufacturing.schemas import IssueIn, IssueLineIn
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for
from tests.inventory_fixtures import balance, q
from tests.manufacturing_fixtures import (
    Mfg,
    build_mfg,
    call,
    make_bom,
    make_order,
    material,
    ok,
    stock,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def m(session, org_id) -> Mfg:
    return await build_mfg(session, org_id)


class _Order:
    id = uuid.UUID(int=7)


class TestKernelKey:
    def test_long_client_keys_keep_their_operation_and_sequence(self) -> None:
        long_a, long_b = "k" * 100 + "-a", "k" * 100 + "-b"
        keys = {
            services._key(_Order(), op, k, n)  # type: ignore[arg-type]
            for op in ("issue", "output", "wastage")
            for k in (long_a, long_b)
            for n in (0, 1, 2)
        }
        assert len(keys) == 18  # nothing collapsed
        assert all(k is not None and len(k) <= 128 for k in keys)

    def test_the_same_inputs_give_the_same_key(self) -> None:
        a = services._key(_Order(), "issue", "x" * 128, 1)  # type: ignore[arg-type]
        assert a == services._key(_Order(), "issue", "x" * 128, 1)  # type: ignore[arg-type]
        assert services._key(_Order(), "issue", None) is None  # type: ignore[arg-type]


class TestReplayedPostsDoNotCountTwice:
    async def test_an_issue_the_kernel_replays_leaves_the_counters_alone(
        self, client, session, admin_session, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "100")
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")])
        order = await make_order(client, m, m.shirt, "20")
        oid = uuid.UUID(order["id"])
        ctx = ctx_for(
            m.org_id,
            "operations.manufacturing.production_order.issue",
            Scope.ORG,
            sub=uuid.uuid4(),
        )
        payload = IssueIn(
            over_issue=True,
            lines=[
                IssueLineIn(
                    material_id=uuid.UUID(material(order, "COT-180-NAVY")["id"]),
                    location_id=m.t.weaving,
                    quantity="5",
                )
            ],
        )
        key = "r" * 128
        for _ in range(2):
            async with session.begin():
                await services.issue_materials(session, ctx, oid, payload, key=key)
            # forget the HTTP-level claim so only the stock kernel's own key can stop a re-post
            async with admin_session.begin():
                await admin_session.execute(delete(ManufacturingRequest))
        detail = await ok(client, m.org_id, "GET", f"/orders/{oid}")
        assert material(detail, "COT-180-NAVY")["issued"] == "5"
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == (
            q("5"),
            0,
        )


class TestDuplicateComponents:
    async def test_a_part_cannot_be_added_twice_to_one_version(
        self, client, session, m: Mfg
    ) -> None:
        made = await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")], publish=False)
        path = f"/boms/{made['bom_id']}/versions/{made['id']}/components"
        resp = await call(
            client,
            m.org_id,
            "POST",
            path,
            json={"component_item_id": str(m.t.fabric), "quantity": "2"},
        )
        assert resp.status_code == 409
        # another part is still fine, and the same part in a different BOM is unaffected
        await ok(
            client,
            m.org_id,
            "POST",
            path,
            expect=201,
            json={"component_item_id": str(m.t.dye), "quantity": "2"},
        )
        await make_bom(client, m, m.panel, "BOM-P", [(m.t.fabric, "1", "0")])

    async def test_the_database_refuses_the_same_part_twice_in_a_version(
        self, client, session, admin_session, m: Mfg
    ) -> None:
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        made = await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "1", "0")], publish=False)
        with pytest.raises(IntegrityError, match="uq_bom_components_item"):
            async with admin_session.begin():
                await admin_session.execute(
                    text(
                        "INSERT INTO bom_components (id, organization_id, bom_version_id,"
                        " line_no, component_item_id, quantity_milli, scrap_bps, notes)"
                        " SELECT gen_random_uuid(), organization_id, bom_version_id, 99,"
                        " component_item_id, 1000, 0, '' FROM bom_components"
                        " WHERE bom_version_id = :v"
                    ),
                    {"v": uuid.UUID(made["id"])},
                )


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=4,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class TestConcurrentPublish:
    async def test_two_boms_that_close_a_cycle_cannot_both_publish(
        self, client, session, maker, m: Mfg
    ) -> None:
        # shirt <- panel and panel <- shirt: either alone is fine, both together are a cycle
        a = await make_bom(client, m, m.shirt, "BOM-S", [(m.panel, "1", "0")], publish=False)
        b = await make_bom(client, m, m.panel, "BOM-P", [(m.shirt, "1", "0")], publish=False)
        ctx = ctx_for(m.org_id, "operations.manufacturing.bom.publish", Scope.ORG, sub=uuid.uuid4())
        barrier = asyncio.Barrier(2)

        async def publish(made: dict) -> str:
            async with maker() as s:

                @event.listens_for(s.sync_session, "after_begin")
                def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                    connection.exec_driver_sql(
                        f"SELECT set_config('{TENANT_GUC}', '{m.org_id}', true)"
                    )

                try:
                    async with s.begin():
                        await s.connection()  # a real transaction is open on both sides
                        await barrier.wait()
                        await bom_services.publish_version(
                            s, ctx, uuid.UUID(made["bom_id"]), uuid.UUID(made["id"])
                        )
                        # hold the transaction open so the other side's check runs while this
                        # one is uncommitted: only the lock can order them
                        await asyncio.sleep(0.5)
                except Exception as exc:
                    return type(exc).__name__
                return "ok"

        outcome = sorted(await asyncio.gather(publish(a), publish(b)))
        assert outcome == ["ConflictProblem", "ok"]
