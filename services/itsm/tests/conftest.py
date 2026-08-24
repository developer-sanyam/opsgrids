"""Fixtures for itsm-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security and a trigger, which
SQLite cannot model), the **app** role rather than the superuser (so RLS is visible here instead
of only in production — a suite run as a bypassing role makes every isolation assertion a false
pass), and the tenant bound on every `begin()` because the GUC is transaction-local. This mirrors
exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture seeds a small, coherent ITSM dataset through the real service write paths:
assets, a catalog, incidents with worklogs (two owned by known subjects so OWN scope has something
to narrow to), and requests.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import ctx_for

TENANT_TABLES = (
    "incident_worklogs",
    "incidents",
    "requests",
    "catalog_items",
    "assets",
    "audit_logs",
    "outbox",
    "processed_events",
)

# Two known subjects, so OWN scope has an owner to match and an outsider to refuse.
SUBJECT_A = uuid.uuid4()
SUBJECT_B = uuid.uuid4()

DEMO_NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in TENANT_TABLES)

    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()

    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def data(session: AsyncSession, org_id: uuid.UUID) -> dict:
    """A small ITSM dataset seeded through the real write paths."""
    write = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)

    async with session.begin():
        laptop = await services.create_asset(
            session,
            write,
            tag="AST-1001",
            name='MacBook Pro 14" M3',
            asset_type="laptop",
            status="in_use",
            owner="Rohan Mehta",
            location="Pune",
            serial="C02XR3K9MD6T",
            cost_minor=21_900_000,
            cost_currency="INR",
        )
        switch = await services.create_asset(
            session,
            write,
            tag="AST-1006",
            name="Cisco Catalyst 9300",
            asset_type="network",
            status="in_repair",
            owner="Network team",
            location="Bengaluru",
            serial="FCW2340L0GH",
            cost_minor=62_500_000,
            cost_currency="INR",
        )

        await services.create_catalog_item(
            session,
            write,
            code="CAT-01",
            name="New joiner onboarding",
            category="onboarding",
            description="Provision a laptop and accounts.",
            sla_days=3,
            requests_30d=12,
        )
        await services.create_catalog_item(
            session,
            write,
            code="CAT-04",
            name="VPN access",
            category="access",
            description="Grant remote-access VPN.",
            sla_days=1,
            requests_30d=18,
        )

        # An incident owned by SUBJECT_A, open, breaching, against the switch.
        inc_a = await services.create_incident(
            session,
            write,
            number="INC-4821",
            summary="Bengaluru office internet completely down",
            description="Core switch stopped forwarding.",
            priority="P1",
            status="in_progress",
            category="network",
            requester="Priya Nair",
            requester_id=SUBJECT_A,
            assignee="Aravind Pillai",
            affected_asset_id=switch.id,
            sla_state="breached",
            sla_breached=True,
            sla_minutes_remaining=-38,
            created_at=DEMO_NOW,
            updated_at=DEMO_NOW,
            worklog=[
                {"author": "Priya Nair", "body": "Raised as P1.", "kind": "note", "at": DEMO_NOW},
                {
                    "author": "Service desk",
                    "body": "Auto-assigned.",
                    "kind": "assignment",
                    "at": DEMO_NOW,
                },
            ],
        )
        # An incident owned by SUBJECT_B, resolved.
        inc_b = await services.create_incident(
            session,
            write,
            number="INC-4812",
            summary="DC-1 switch failover verification",
            description="Scheduled failover test.",
            priority="P1",
            status="resolved",
            category="network",
            requester="Meera Raghavan",
            requester_id=SUBJECT_B,
            assignee="Aravind Pillai",
            affected_asset_id=switch.id,
            sla_state="met",
            sla_minutes_remaining=None,
            created_at=DEMO_NOW,
            updated_at=DEMO_NOW,
            worklog=[
                {
                    "author": "Meera Raghavan",
                    "body": "Run the test.",
                    "kind": "note",
                    "at": DEMO_NOW,
                }
            ],
        )

        await services.create_request(
            session,
            write,
            number="REQ-2051",
            catalog_item_code="CAT-01",
            requester="Sneha Kulkarni",
            requester_id=SUBJECT_A,
            subject="Vaidehi Kulkarni (joins 01 Sep)",
            status="approval",
            assignee="Meera Raghavan",
            created_at=DEMO_NOW,
            updated_at=DEMO_NOW,
        )
        await services.create_request(
            session,
            write,
            number="REQ-2048",
            catalog_item_code="CAT-04",
            requester="Aditya Ghosh",
            requester_id=SUBJECT_B,
            subject="finance reporting VPN",
            status="fulfilled",
            created_at=DEMO_NOW,
            updated_at=DEMO_NOW,
        )

    return {
        "laptop": laptop,
        "switch": switch,
        "inc_a": inc_a,
        "inc_b": inc_b,
        "subject_a": SUBJECT_A,
        "subject_b": SUBJECT_B,
    }


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
