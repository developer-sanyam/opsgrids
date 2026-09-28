"""Count sessions over HTTP (ADR-0026, plan F8): opening (and its scope resolution), the
warehouse-level exclusivity lock (ADR-0026 §5), the lifecycle transitions, negative-authz and
cross-tenant 404 on every endpoint.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.counting import services as counting_services
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.ledger import MoveLine
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.counting_fixtures import (
    COUNT_READ_PERM,
    CountingShop,
    build_counting_shop,
    cancel_session,
    close_session,
    list_lines,
    open_session,
    open_session_ok,
    start_review,
)
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def cshop(session, org_id) -> CountingShop:
    return await build_counting_shop(session, org_id)


class TestOpenSession:
    async def test_warehouse_scope_snapshots_every_available_bucket(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        assert row["status"] == "ACTIVE"
        assert row["scope_kind"] == "WAREHOUSE"
        # Three items seeded by build_shop (widget, gizmo, unpriced), each at its home bin.
        assert row["line_count"] == 3

        lines_resp = await list_lines(client, org_id, row["id"])
        assert lines_resp.status_code == 200, lines_resp.text
        expected = {str(cshop.shop.widget): "100", str(cshop.shop.gizmo): "100"}
        by_item = {
            line["inventory_item_id"]: line["expected"] for line in lines_resp.json()["items"]
        }
        for item_id, qty in expected.items():
            assert by_item[item_id] == qty

    async def test_zone_scope_only_snapshots_locations_under_the_zone(
        self, client, session, org_id, cshop: CountingShop
    ) -> None:
        loc_ctx = ctx_for(org_id, "operations.inventory.location.write", Scope.ORG)
        adjust_ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            warehouse = await inv_selectors.get_warehouse(session, org_id, cshop.shop.warehouse_id)
            assert warehouse is not None
            zone = await inv_services.create_location(
                session, loc_ctx, warehouse, kind="zone", code="Z1", name="Zone 1"
            )
            bin_in_zone = await inv_services.create_location(
                session,
                loc_ctx,
                warehouse,
                kind="bin",
                code="Z1-B1",
                name="Zone 1 Bin 1",
                parent_id=str(zone.id),
            )
        async with session.begin():
            await ledger.post_move(
                session,
                adjust_ctx,
                kind="receipt",
                lines=[
                    MoveLine(
                        item_id=cshop.shop.widget, location_id=bin_in_zone.id, quantity_milli=5_000
                    )
                ],
                occurred_on=date.today(),
                reference_type="test_seed",
                reference_id=uuid.uuid4(),
            )

        row = await open_session_ok(
            client,
            org_id,
            warehouse_id=cshop.shop.warehouse_id,
            scope_kind="ZONE",
            zone_location_id=zone.id,
        )
        assert row["line_count"] == 1
        lines_resp = await list_lines(client, org_id, row["id"])
        only_line = lines_resp.json()["items"][0]
        assert only_line["location_id"] == str(bin_in_zone.id)
        assert only_line["expected"] == "5"

    async def test_locations_scope_needs_locations_inside_the_warehouse(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        outside = uuid.uuid4()
        response = await open_session(
            client,
            org_id,
            warehouse_id=cshop.shop.warehouse_id,
            scope_kind="LOCATIONS",
            location_ids=[outside],
        )
        assert response.status_code == 404, response.text

    async def test_a_second_session_for_the_same_warehouse_is_refused(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        first = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        assert first["status"] == "ACTIVE"
        second = await open_session(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        assert second.status_code == 409, second.text

    async def test_a_caller_without_manage_cannot_open_a_session(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        response = await open_session(
            client, org_id, warehouse_id=cshop.shop.warehouse_id, perms=[COUNT_READ_PERM]
        )
        assert response.status_code == 403

    async def test_cross_tenant_open_does_not_see_the_other_orgs_warehouse(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        other_org = uuid.uuid4()
        response = await open_session(client, other_org, warehouse_id=cshop.shop.warehouse_id)
        assert response.status_code == 404


class TestConcurrentOpen:
    async def test_exactly_one_of_two_simultaneous_opens_succeeds(
        self, org_id, cshop: CountingShop
    ) -> None:
        """Real second connections, real row locks (ADR-0026 §5) — the same `maker`/race harness
        `test_concurrency.py` uses for the stock kernel, applied to `open_session`."""
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
            pool_size=10,
            max_overflow=0,
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def attempt() -> str:
            async with maker() as s:

                @event.listens_for(s.sync_session, "after_begin")
                def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                    connection.exec_driver_sql(
                        f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)"
                    )

                ctx = ctx_for(org_id, "operations.inventory.count.manage", Scope.ORG)
                try:
                    async with s.begin():
                        await counting_services.open_session(
                            s,
                            ctx,
                            warehouse_id=cshop.shop.warehouse_id,
                            scope_kind="WAREHOUSE",
                            zone_location_id=None,
                            location_ids=None,
                            label="race",
                        )
                except ConflictProblem:
                    return "refused"
                return "ok"

        try:
            results = await asyncio.gather(attempt(), attempt())
        finally:
            await engine.dispose()
        assert sorted(results) == ["ok", "refused"]


class TestSessionLifecycle:
    async def test_active_to_review_to_closed(self, client, org_id, cshop: CountingShop) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        review = await start_review(client, org_id, row["id"])
        assert review.status_code == 200, review.text
        assert review.json()["status"] == "REVIEW"

        closed = await close_session(client, org_id, row["id"])
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"

    async def test_cannot_close_straight_from_active(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        response = await close_session(client, org_id, row["id"])
        assert response.status_code == 409

    async def test_cancel_from_active(self, client, org_id, cshop: CountingShop) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        response = await cancel_session(client, org_id, row["id"], reason="wrong warehouse")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCELLED"

    async def test_a_cancelled_warehouse_can_be_recounted(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        await cancel_session(client, org_id, row["id"])
        second = await open_session(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        assert second.status_code == 201, second.text

    async def test_a_caller_without_manage_cannot_transition(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        response = await start_review(client, org_id, row["id"], perms=[COUNT_READ_PERM])
        assert response.status_code == 403

    async def test_cross_tenant_cannot_read_or_transition(
        self, client, org_id, cshop: CountingShop
    ) -> None:
        row = await open_session_ok(client, org_id, warehouse_id=cshop.shop.warehouse_id)
        other = uuid.uuid4()
        read = await client_get_session(client, other, row["id"])
        assert read.status_code == 404
        transition = await start_review(client, other, row["id"])
        assert transition.status_code == 404


async def client_get_session(client, org_id: uuid.UUID, session_id: str):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [COUNT_READ_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        return await client.get(f"/api/v1/operations/counting/sessions/{session_id}")
