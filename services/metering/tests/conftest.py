"""Fixtures for metering-service tests.

Same shape as employee- and catalog-service, and for the same reasons: real PostgreSQL rather
than SQLite (the schema leans on partial-free unique indexes, row-level security and a
privilege-level append-only revoke that SQLite cannot model), the service's **app** role rather
than the superuser (so a policy or a revoke that is inert reads as a failure here instead of a
false pass — the outcome ADR-0002 calls worse than no test), and the tenant bound on every
`begin()` because the GUC is transaction-local.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a tenant
needs no account-service row to exist — a bare uuid is a whole tenant.
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

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.db import TENANT_GUC, get_session

#: Tenant-scoped tables cleared between tests. `meters` is global reference data seeded by a
#: migration and is deliberately left intact — the tests read against it, never rebuild it.
TENANT_TABLES = ("usage_events", "usage_rollups", "audit_logs", "outbox", "processed_events")


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    """A tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    """A second tenant, for the cross-tenant isolation tests."""
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`.

    The binding is reapplied on each `begin()` rather than once, because the GUC is
    transaction-local — a single call would leave later transactions unbound and reading nothing.
    This mirrors exactly what `get_tenant_session` does per request. The ingest service layer sets
    its own per-organization GUC inside its transactions, which runs *after* this `after_begin`
    hook and so overrides it — exactly as it would in production.

    Pointing this at the admin or superuser DSN would make row-level security and the append-only
    revoke invisible here and every isolation assertion a false pass.
    """
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
    """A BYPASSRLS session, for seeding another tenant's rows and reading across the binding.

    Use this only where a second organization's data has to exist for an isolation test, or to
    read the policied `outbox` to prove an event was staged. Reaching for it to make a
    tenant-scoped test pass would hide exactly what the policies exist to catch.
    """
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's session, so writes made over HTTP are visible to asserts.

    Both the customer chokepoint (`get_tenant_session`) and the internal ingest wrap `get_session`,
    so overriding that one dependency routes every route through the shared session while leaving
    each path's own transaction and tenant binding intact.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
