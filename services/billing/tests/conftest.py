"""Fixtures for billing-service tests.

Same shape as employee- and product-service, for the same reasons: real PostgreSQL (the schema
leans on a trigger, row-level security and privilege revokes SQLite cannot model), the service's
**app** role rather than the superuser (so a policy or a revoke that is inert reads as a failure
here rather than a false pass), and the tenant bound on every transaction because the GUC is
transaction-local.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant
needs no row in any other service — a bare UUID is a whole tenant.
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
from platform_core.db import TENANT_GUC, get_directory_session, get_session

#: Everything billing owns. Tenant-scoped or global-and-test-empty, so all are safe to truncate.
#: `invoice_lines` before `invoices` for the foreign key; TRUNCATE CASCADE would handle it too.
BILLING_TABLES = (
    "invoice_lines",
    "invoices",
    "credit_ledger",
    "tax_rates",
    "tax_invoice_series",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    """The primary tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    """A second tenant, for isolation tests."""
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """The **app** role, every transaction bound to `org_id` (reapplied on each `begin()`
    because the GUC is transaction-local). This mirrors what the request chokepoints do.

    Pointing this at the admin or superuser DSN would make row-level security and the append-only
    revokes invisible here and every isolation assertion a false pass — the failure ADR-0002
    calls worse than no test."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in BILLING_TABLES)

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
    """A BYPASSRLS session, for seeding another tenant's rows in an isolation test and for
    reading the policied `outbox`/`audit_logs` across a binding when proving an event was staged.

    Reaching for it to make a tenant-scoped test pass would hide exactly what the policies exist
    to catch — so it is used only where a second organization's data must exist, or where a
    cross-binding read is the point."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def directory_session() -> AsyncIterator[AsyncSession]:
    """The real staff credit-ledger reader: BYPASSRLS, SELECT-only, no tenant binding.

    The admin route must be exercised through what serves it in production — a session that
    genuinely reads across every organization with no GUC set — rather than the tenant-bound
    `session` fixture, which would make the read pass for the wrong reason (ADR-0002, "the
    cross-tenant reader").
    """
    engine = create_async_engine(settings().directory_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    session: AsyncSession, directory_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts.

    Both the customer chokepoint (`get_tenant_session`) and the internal routes wrap
    `get_session`, so overriding that one dependency routes every route through the shared
    session while leaving each route's own tenant binding intact. The staff credit-ledger
    route uses `get_directory_session` instead, overridden to the real cross-tenant reader."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    async def _directory_override() -> AsyncIterator[AsyncSession]:
        yield directory_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_directory_session] = _directory_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
