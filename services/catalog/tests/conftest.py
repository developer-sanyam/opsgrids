"""Fixtures for catalog-service tests.

Same shape as product- and employee-service, and for the same reasons: real PostgreSQL (the
schema leans on a trigger and row-level security SQLite cannot model), the service's **app**
role rather than the superuser (so a policy that is inert reads as a failure here rather than a
false pass), and the tenant bound on every transaction because the GUC is transaction-local.

Two differences from a tenant service:

* The definitional tables (`plans`, `plan_versions`, `prices`, `features`) are global, so they
  are truncated between tests like everything else — this service *creates* plans in its tests,
  unlike product-service which only reads a seeded catalogue — and the seed migration's rows go
  with them, which is fine: these tests build exactly the plans they assert on.
* The app session binds the **platform sentinel** organization, not a customer's. Authoring a
  plan is a platform act; that sentinel is what catalog's policied `audit_logs`/`outbox` rows
  belong to, and binding it is what lets those writes satisfy their `WITH CHECK`.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only
# one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import PLATFORM_ORG, SCHEMA
from platform_core.db import TENANT_GUC, get_session

#: Everything catalog owns. The definitional tables are global but test-created, so they are
#: cleared too — with FK cascade, truncating `plans` takes its versions, prices and features.
CATALOG_TABLES = (
    "plans",
    "plan_versions",
    "prices",
    "features",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_a() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def platform_session() -> AsyncIterator[AsyncSession]:
    """The app role, every transaction bound to the platform sentinel — what an operator write
    runs on. Reapplied on each `begin()` because the GUC is transaction-local."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in CATALOG_TABLES)

    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()

    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{PLATFORM_ORG}', true)")

        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for reading catalog's policied `outbox`/`audit_logs` across the
    sentinel binding when a test needs to prove an event was staged."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(platform_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's session, so writes made over HTTP are visible to asserts.

    Both the customer chokepoint (`get_tenant_session`) and the operator one
    (`get_operator_session`) wrap `get_session`, so overriding that one dependency routes every
    route through the shared session while leaving each chokepoint's own binding intact.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield platform_session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
