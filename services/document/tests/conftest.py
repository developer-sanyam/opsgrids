"""Fixtures for document-service tests.

Same shape as approval-, subscription- and attendance-service, for the same reasons: real
PostgreSQL rather than SQLite (the schema leans on row-level security and a partial index,
neither of which SQLite can model), the service's **app** role rather than the superuser (so a
policy that is inert reads as a failure here rather than a false pass), and the tenant bound on
every transaction because the GUC is transaction-local.

The one fixture with no counterpart elsewhere is `store`: a fake object store that records what
was written instead of writing it. It exists so the unit suite can assert the *ordering* of the
two writes an upload performs — which is the whole of ADR-0009 §9 and is invisible to a test
that only checks the happy path. The real MinIO round trip is `test_storage_minio.py`, which is
where "this actually works" is established; neither test substitutes for the other.
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

# The service directory is the import root: every service exposes a top-level `app`,
# so only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.main import app
from app.models import SCHEMA
from app.storage import get_object_store
from platform_core.auth import Principal, get_principal
from platform_core.cache import close_redis, init_redis
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import FakeObjectStore

TENANT_TABLES = (
    "assets",
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
def other_org() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def actor_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """The app role, every transaction bound to `org_id` — what a customer request runs on."""
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
    """A BYPASSRLS session: seeds another organization's rows a cross-tenant read must not see,
    and reads across the tenant binding to prove what a policied query actually wrote."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def store() -> FakeObjectStore:
    """Records puts and deletes rather than performing them. See the module docstring."""
    return FakeObjectStore(settings())


@pytest_asyncio.fixture
async def client(session: AsyncSession, store: FakeObjectStore) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's org-bound session, so writes are visible to asserts.

    `get_tenant_session` wraps `get_session`, so overriding that one dependency routes every
    route through the shared session while leaving the chokepoint's own binding intact.

    Redis is initialised for real, because the upload route's rate limiter fails **closed** —
    a test running without it would see 503 on every upload and prove nothing about the route.
    Opened and closed per test rather than once for the session: the client binds itself to the
    event loop it was created on, and pytest-asyncio gives each test a fresh one, so a cached
    client leaks into the next test as `Event loop is closed` — which the limiter then reports
    as 503 and every upload assertion fails for a reason that has nothing to do with the route.
    """
    init_redis(settings().redis_url)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_object_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await close_redis()


@pytest.fixture
def as_principal() -> Iterator[object]:
    """Install a forwarded principal for the duration of a `with` block."""
    from contextlib import contextmanager

    @contextmanager
    def _install(principal: Principal) -> Iterator[None]:
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_principal, None)

    yield _install
