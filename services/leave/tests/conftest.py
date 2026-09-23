"""Fixtures for leave-service tests.

Same shape as attendance- and employee-service, for the same reasons: real PostgreSQL rather
than SQLite (row-level security, a partial unique index and a GiST exclusion constraint, none of
which SQLite can model), the **app** role rather than the superuser (so RLS is visible here
instead of only in production), and the tenant bound on every `begin()` because the GUC is
transaction-local.

`organization_id` and every employee id are plain UUIDs here, never a foreign key across a
schema boundary — a bare uuid is a whole tenant, and so is a bare employee id.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator, Iterator
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
from platform_core import cache
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import FakeRedis

#: Every table here is tenant-scoped or global-and-empty, so all are safe to truncate. CASCADE
#: because `leave_ledger_entries` carries foreign keys into `leave_types` and `leave_requests`.
TENANT_TABLES = (
    "leave_ledger_entries",
    "leave_requests",
    "leave_types",
    "leave_settings",
    "employee_directory",
    "audit_logs",
    "outbox",
    "processed_events",
)

SCHEMA = "leave"


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture(autouse=True)
def redis() -> Iterator[FakeRedis]:
    """A stand-in Redis so the app factory's `redis=True` readiness check never reaches a real
    server here. Sync rather than async — it is autouse and so runs for the sync boot tests too."""
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def org_id() -> uuid.UUID:
    """A tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`.

    The binding is reapplied on each `begin()` rather than once, because the GUC is
    transaction-local — mirrors exactly what `get_tenant_session` does per request.
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
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant, or for driving the
    consumer's own `session_scope` in a test that does not go through the HTTP layer."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
