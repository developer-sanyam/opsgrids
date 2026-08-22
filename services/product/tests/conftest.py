"""Fixtures for product-service tests.

Same shape as account-service, for the same reasons: real PostgreSQL, the app role rather than
the superuser, and the tenant bound on every transaction so row-level security is visible here
instead of only in production.

One difference worth stating. `products` is global and seeded by a migration, so it is **not**
truncated between tests. Truncating it would empty the catalogue and every activation test
would fail with "no such product" — a failure that reads as a bug in the code under test rather
than in the fixture.
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

# The service directory is the import root: every service exposes a top-level `app`,
# so only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.db import TENANT_GUC, get_session

#: Tenant-scoped tables only — the catalogue survives.
TENANT_TABLES = ("activations", "audit_logs", "outbox", "processed_events")


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    """A tenant.

    No account-service row is needed: `organization_id` is a plain indexed UUID here, never a
    foreign key across a schema boundary — which is what keeps a later database-per-service
    split a configuration change.
    """
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
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
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
