"""Fixtures for finance-service tests.

Same shape as account-, product- and employee-service, for the same reasons: real PostgreSQL
rather than SQLite (the schema leans on row-level security, which SQLite cannot model), the
**app** role rather than the superuser (so RLS is visible here instead of only in production — a
suite run as a bypassing role makes every isolation assertion a false pass), and the tenant bound
on every `begin()` because the GUC is transaction-local.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a tenant
needs no account-service row to exist — a bare uuid is a whole tenant.
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

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The demo seed lives at repo-root tools/demo; add it so tests can drive the real seeder.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))

from app.config import settings
from app.models import SCHEMA
from platform_core import cache
from platform_core.auth import ScopeContext, get_principal
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import FINANCE_PERMISSIONS, FakeRedis, admin_ctx, principal_for

#: Every table here is tenant-scoped; all are safe to truncate before a test.
#:
#: `einvoice_filings`, `ims_decisions`, `eway_bills`, `gst_return_transitions` and
#: `invoice_withholdings` are append-only *for the application* — a trigger refuses UPDATE and
#: DELETE — and `gst_returns` freezes its snapshot the same way. TRUNCATE is neither an UPDATE nor
#: a DELETE, and this runs as the admin role that owns the tables, so the fixture can still reset
#: them between tests without the triggers being weakened for anybody else.
#:
#: `withholding_rates` and `tax_rates` are deliberately absent: they are global vocabulary seeded
#: by a migration, not tenant data, and truncating them would leave the register with no schedule
#: to resolve against — a fixture quietly deleting the law is not a clean slate.
TENANT_TABLES = (
    "customers",
    "invoices",
    "invoice_lines",
    "invoice_number_series",
    "einvoice_filings",
    "eway_bills",
    "gst_registrations",
    "gst_returns",
    "gst_return_transitions",
    "invoice_withholdings",
    "itc_ledger_entries",
    "purchase_documents",
    "ims_documents",
    "ims_reconciliations",
    "ims_decision_pushes",
    "ims_decisions",
    "payments",
    "payment_allocations",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture(autouse=True)
def redis() -> Iterator[FakeRedis]:
    """A stand-in Redis so the app factory's `redis=True` never reaches a real server here."""
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
    transaction-local. Pointing this at the admin or superuser DSN would make row-level security
    invisible here and every isolation assertion a false pass.
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
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant.

    Use this only where a second organization's data has to exist for an isolation test."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def unbound_app_session() -> AsyncIterator[AsyncSession]:
    """The application role with **no** tenant GUC bound.

    What a consumer would run on if somebody dropped `session_scope(organization_id=...)`. Under
    the forced policies on the compliance tables `current_org()` is NULL here, so a write is
    refused outright — which is the RLS net, and the only way to prove the per-message binding is
    load-bearing rather than decorative is to omit it and watch the insert fail.
    """
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def ctx(org_id: uuid.UUID) -> ScopeContext:
    """An ORG-scoped finance context holding every read grant."""
    return admin_ctx(org_id)


@pytest_asyncio.fixture
async def book(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Seed the demo finance book into this test's org through the real seeder."""
    from seed_finance import seed_finance

    async with session.begin():
        return await seed_finance(session, ctx)


@pytest_asyncio.fixture
async def client(session: AsyncSession, org_id: uuid.UUID) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts.

    A fully-granted, entitled finance principal for `org_id` is injected by default, so the
    happy-path read tests need no auth plumbing; the authz tests override `get_principal` with
    `as_principal` to narrow or drop grants.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    from app.main import app

    default_principal = principal_for(org_id, [f"{p}:ORG" for p in FINANCE_PERMISSIONS])
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_principal] = lambda: default_principal
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
