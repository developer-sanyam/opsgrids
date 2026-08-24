"""Fixtures for console-service tests.

Same shape as product- and metering-service, for the same reasons: real PostgreSQL, the service's
**app** role rather than the superuser (so an RLS policy or an append-only revoke that is inert
shows as a failure here rather than a false pass — the outcome ADR-0002 calls worse than no test), a
BYPASSRLS **directory** session for the operator's cross-tenant reads, and a bare-UUID tenant that
needs no cross-schema row.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a tenant
needs no account-service row — a bare uuid is a whole tenant.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`,
# so only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.db import (
    dispose_engine,
    get_directory_session,
    get_session,
    init_engine,
)

#: Cleared between tests. `processed_events` too, so an idempotency test starts from a clean ledger.
TENANT_TABLES = (
    "org_commercial_state",
    "revenue_events",
    "usage_summary",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_c() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    """Truncate the projection and platform tables before each test, on the admin role."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in TENANT_TABLES)
    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()
    yield


@pytest_asyncio.fixture
async def engine_ready() -> AsyncIterator[None]:
    """Initialise the process-wide app engine so `session_scope` works in fold tests.

    The consumer folds through `session_scope(organization_id=…)`, which reaches the app engine as
    the NOBYPASSRLS `atmora_console` role — so the writes are RLS-real here, exactly as in
    production. Disposed after each test so state never leaks between them.
    """
    init_engine(settings())
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for seeding projection rows a cross-tenant read has to find."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def directory_session() -> AsyncIterator[AsyncSession]:
    """The real operator reader: BYPASSRLS, SELECT-only, no tenant binding.

    The read API must be exercised through what serves it in production — a session that genuinely
    resolves every organization's projections with no GUC set. Handing it the tenant-bound app
    session would make it pass for the wrong reason (ADR-0002, "the cross-tenant reader").
    """
    engine = create_async_engine(settings().directory_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def unbound_app_session() -> AsyncIterator[AsyncSession]:
    """The application role with **no** tenant GUC — the projections must read as empty here.

    Under the forced policy, `current_org()` is NULL on this session, so a cross-tenant read returns
    nothing. Kept as a fixture so the companion test proves the projections are RLS-real and the
    operator view genuinely depends on the bypass.
    """
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def app_plain_session() -> AsyncIterator[AsyncSession]:
    """An app-role session with no active transaction — what an override's audit write runs on.

    `services.record_override` opens its own `session.begin()` and binds the target org, so this
    must be handed over un-bound and outside a transaction (the "then narrow" write of ADR-0002's
    bypass-then-narrow).
    """
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    directory_session: AsyncSession, app_plain_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """HTTP client with the read and write sessions overridden to the test's own.

    The read API uses `get_directory_session`; the override endpoint uses `get_session`. Overriding
    both routes every endpoint through a session the test can also read, while leaving each path's
    own transaction and tenant binding intact. `get_delegation_client` is left for the override test
    to override with a mock transport.
    """

    async def _directory() -> AsyncIterator[AsyncSession]:
        yield directory_session

    async def _app() -> AsyncIterator[AsyncSession]:
        yield app_plain_session

    app.dependency_overrides[get_directory_session] = _directory
    app.dependency_overrides[get_session] = _app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
