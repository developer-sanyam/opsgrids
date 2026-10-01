"""Operational alerts and insight dashboards (fulfilment plan §4; F12).

Low stock and a delayed pick task, detected once, refreshed while the condition holds, and closed
automatically once it clears — the same "tamper to manufacture the condition, then read what the
sweep found" shape `test_reconcile.py` uses, since neither condition is something this build's own
write paths can cause on demand (an item already below a configured reorder point; a task that has
simply been sitting for a while). HTTP tests cover the read/resolve endpoints' authz.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts.services import (
    EVT_LOW_STOCK,
    EVT_TASK_DELAYED,
    resolve_alert,
    sweep_organization,
)
from app.config import settings
from app.cron import AlertSweep
from app.models import (
    SCHEMA,
    FulfilmentSettings,
    InventoryItem,
    OperationalAlert,
    OutboxEvent,
    PickTask,
)
from platform_core.db import TENANT_GUC
from tests.fulfilment_fixtures import Floor, accept_order, build_floor, create_tasks
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

ALERT_READ = "operations.automation.alert.read:ORG"
ALERT_MANAGE = "operations.automation.alert.manage:ORG"
INSIGHT_READ = "operations.automation.insight.read:ORG"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id)


async def _events(session, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
            .scalars()
            .all()
        )


async def _alerts(session, org_id: uuid.UUID, kind: str) -> list[OperationalAlert]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(OperationalAlert).where(
                        OperationalAlert.organization_id == org_id,
                        OperationalAlert.kind == kind,
                    )
                )
            )
            .scalars()
            .all()
        )


class TestLowStock:
    async def test_opens_once_refreshes_then_resolves(
        self, session, admin_session, org_id, floor: Floor
    ) -> None:
        widget = floor.shop.widget
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == widget)
                .values(reorder_point=1000)  # far above the fixture's own stock
            )

        async with session.begin():
            report = await sweep_organization(session, org_id)
        assert report.opened == 1
        alerts = await _alerts(session, org_id, "low_stock")
        assert len(alerts) == 1 and alerts[0].resolved_at is None
        assert len(await _events(session, EVT_LOW_STOCK)) == 1

        # A second pass over the same, still-breaching item refreshes, never duplicates or
        # re-alerts.
        async with session.begin():
            report = await sweep_organization(session, org_id)
        assert report.opened == 0 and report.refreshed == 1
        assert len(await _alerts(session, org_id, "low_stock")) == 1
        assert len(await _events(session, EVT_LOW_STOCK)) == 1

        # Replenished above the reorder point: the open alert closes on its own, no action here.
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem).where(InventoryItem.id == widget).values(reorder_point=0)
            )
        async with session.begin():
            report = await sweep_organization(session, org_id)
        assert report.resolved == 1
        (alert,) = await _alerts(session, org_id, "low_stock")
        assert alert.resolved_at is not None and alert.resolved_by == "system:alert_sweep"

    async def test_alert_contact_travels_on_the_event(
        self, session, admin_session, org_id, floor: Floor
    ) -> None:
        async with session.begin():
            session.add(
                FulfilmentSettings(
                    organization_id=org_id,
                    warehouse_id=None,
                    alert_email="ops@example.com",
                    alert_phone="+15551234567",
                )
            )
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == floor.shop.widget)
                .values(reorder_point=1000)
            )
        async with session.begin():
            await sweep_organization(session, org_id)
        (event,) = await _events(session, EVT_LOW_STOCK)
        assert event.payload["alert_contact"] == {
            "email": "ops@example.com",
            "phone": "+15551234567",
        }


class TestDelayedTask:
    async def test_opens_once_and_resolves_when_the_task_closes(
        self, client, session, admin_session, org_id, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "2"}],
        )
        tasks = await create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])

        # Backdate `created_at` well past the default 60-minute SLA — nothing in this build's own
        # write paths can otherwise make a task old.
        async with admin_session.begin():
            await admin_session.execute(
                update(PickTask)
                .where(PickTask.id == task_id)
                .values(created_at=datetime.now(UTC) - timedelta(hours=2))
            )

        async with session.begin():
            report = await sweep_organization(session, org_id)
        assert report.opened == 1
        (alert,) = await _alerts(session, org_id, "task_delayed")
        assert alert.subject_id == task_id and alert.resolved_at is None
        assert len(await _events(session, EVT_TASK_DELAYED)) == 1

        # Cancel the task (a real terminal transition) — the next sweep finds it no longer open
        # and resolves the alert on its own.
        async with admin_session.begin():
            await admin_session.execute(
                update(PickTask).where(PickTask.id == task_id).values(status="CANCELLED")
            )
        async with session.begin():
            report = await sweep_organization(session, org_id)
        assert report.resolved == 1
        (alert,) = await _alerts(session, org_id, "task_delayed")
        assert alert.resolved_at is not None


class TestResolveAlertByHand:
    async def test_a_manager_can_dismiss_an_open_alert(
        self, session, admin_session, org_id, floor: Floor
    ) -> None:
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == floor.shop.widget)
                .values(reorder_point=1000)
            )
        async with session.begin():
            await sweep_organization(session, org_id)
        (alert,) = await _alerts(session, org_id, "low_stock")

        async with session.begin():
            resolved = await resolve_alert(session, org_id, alert.id, resolved_by="user:asha")
        assert resolved is not None and resolved.resolved_by == "user:asha"

        # Idempotent: resolving an already-resolved alert is a no-op, not a conflict.
        async with session.begin():
            again = await resolve_alert(session, org_id, alert.id, resolved_by="user:someone-else")
        assert again is not None and again.resolved_by == "user:asha"


class TestAlertsHttp:
    async def test_list_alerts_over_http(
        self, client, session, admin_session, org_id, floor: Floor
    ) -> None:
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == floor.shop.widget)
                .values(reorder_point=1000)
            )
        async with session.begin():
            await sweep_organization(session, org_id)

        principal = principal_for(org_id, [ALERT_READ], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/alerts")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1 and body["items"][0]["kind"] == "low_stock"

    async def test_a_reader_cannot_resolve_an_alert(
        self, client, session, admin_session, org_id, floor: Floor
    ) -> None:
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == floor.shop.widget)
                .values(reorder_point=1000)
            )
        async with session.begin():
            await sweep_organization(session, org_id)
        (alert,) = await _alerts(session, org_id, "low_stock")

        principal = principal_for(org_id, [ALERT_READ], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(f"/api/v1/operations/alerts/{alert.id}/resolve")
        assert response.status_code == 403

    async def test_cross_tenant_alert_is_not_found(
        self, client, session, admin_session, org_id, floor: Floor
    ) -> None:
        async with admin_session.begin():
            await admin_session.execute(
                update(InventoryItem)
                .where(InventoryItem.id == floor.shop.widget)
                .values(reorder_point=1000)
            )
        async with session.begin():
            await sweep_organization(session, org_id)
        (alert,) = await _alerts(session, org_id, "low_stock")

        other = principal_for(uuid.uuid4(), [ALERT_MANAGE], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(f"/api/v1/operations/alerts/{alert.id}/resolve")
        assert response.status_code == 404

    async def test_insight_backlog_needs_its_own_permission(self, client, org_id) -> None:
        principal = principal_for(org_id, [ALERT_READ], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/insights/backlog")
        assert response.status_code == 403

        allowed = principal_for(org_id, [INSIGHT_READ], subject_ref=uuid.uuid4())
        with as_principal(allowed):
            response = await client.get("/api/v1/operations/insights/backlog")
        assert response.status_code == 200


class TestNightlySweep:
    """`AlertSweep.tick()` against the real outbox role — not `sweep_organization` called
    directly, the way the rest of this file tests it. The two are not equivalent: discovery
    (`_ALERT_ORGANIZATIONS_SQL`, run as `opsgrids_outbox`) is a different code path from the
    per-organization work above it, and a column the outbox role was never granted fails there,
    not here. That gap went undetected from this phase's own deploy until a real incident
    (`operations.alerts.tick_failed`, `InsufficientPrivilegeError` on `inventory_items`, hundreds
    of consecutive failures with no migration ever reporting an error) — the same shape
    `test_reconcile.py::TestNightlySweep` already guards `ReconcileSweep` against, extended here
    so the next sweep added to this file does not reopen it."""

    async def test_the_sweep_discovers_every_organization_as_the_real_outbox_role(
        self, client, session, admin_session, org_id, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "2"}],
        )
        tasks = await create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])
        async with admin_session.begin():
            await admin_session.execute(
                update(PickTask)
                .where(PickTask.id == task_id)
                .values(created_at=datetime.now(UTC) - timedelta(hours=2))
            )

        connect = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        # Discovery as the real outbox role: exactly `_ALERT_ORGANIZATIONS_SQL`'s own columns,
        # the same proof `test_reconcile.py` holds `ReconcileSweep` to.
        discovery = create_async_engine(settings().outbox_dsn, connect_args=connect)
        work = create_async_engine(settings().app_dsn, connect_args=connect)
        work_maker = async_sessionmaker(work, expire_on_commit=False)

        @asynccontextmanager
        async def scope(organization_id):  # type: ignore[no-untyped-def]
            async with work_maker() as s, s.begin():
                await s.execute(
                    text("SELECT set_config(:g, :o, true)"),
                    {"g": TENANT_GUC, "o": str(organization_id)},
                )
                yield s

        try:
            sweep = AlertSweep(discovery_factory=async_sessionmaker(discovery), work_scope=scope)
            report = await sweep.tick()
        finally:
            await discovery.dispose()
            await work.dispose()

        assert report.failed == 0
        assert report.opened == 1
