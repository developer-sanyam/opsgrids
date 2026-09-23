"""Fixtures for storefront-service tests.

Same shape as document-, approval- and attendance-service, for the same reasons: real
PostgreSQL rather than SQLite (the schema leans on row-level security, a partial-free unique
index and `ON CONFLICT`, none of which SQLite models), the service's **app** role rather than the
superuser (so a policy that is inert reads as a failure here rather than a false pass), and the
tenant bound on every transaction because the GUC is transaction-local.

Two things here have no counterpart elsewhere, and both exist because of the anonymous route.

**`engines`** initialises the real process-wide engines — including the **directory** engine,
which connects as `opsgrids_storefront_directory`. Every other service's suite overrides
`get_session` and never opens one. Here, overriding the bypass reader would test a fake: the
thing worth proving is that the real BYPASSRLS role can read `storefront.storefronts`, that it
was actually granted that by `migrations/env.py`, and that everything after it is still policied.
So the public tests drive the real two-engine path and commit for real.

**`_clear_lookup_cache`** empties the Redis entries the slug and host lookups cache. They are
keyed by slug and by hostname and live for five minutes, so without this a test that publishes a
store or claims a domain would be answered from the entry a previous test's 404 wrote — which is
the same staleness ADR-0010 §13 and ADR-0012 §2 accept in production and would be pure noise
here.
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
from platform_core.auth import Principal, get_principal
from platform_core.cache import close_redis, get_redis, init_redis
from platform_core.db import (
    TENANT_GUC,
    dispose_directory_engine,
    dispose_engine,
    get_session,
    init_directory_engine,
    init_engine,
)

TENANT_TABLES = (
    "zone_postal_codes",
    "delivery_zones",
    "serviceability_demand",
    "product_variants",
    "products",
    "categories",
    "enquiries",
    "theme_documents",
    "custom_domain_claims",
    "storefronts",
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


@pytest.fixture
def slug() -> str:
    """A slug unique to this test. Slugs are globally unique and cached for five minutes, so
    sharing one between tests would make them depend on each other through Redis."""
    return f"t{uuid.uuid4().hex[:16]}"


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
    and reads across the tenant binding to prove what a policied query actually wrote.

    Roll it back before reading something a *request* committed on another connection — it holds
    an open transaction from its last statement, and READ COMMITTED means a new one is what sees
    the new row.
    """
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def engines() -> AsyncIterator[None]:
    """The real application and **directory** engines, for the anonymous path.

    Per test rather than per session: an engine binds itself to the event loop it was created on,
    and pytest-asyncio gives each test a fresh one.
    """
    cfg = settings()
    init_engine(cfg)
    init_directory_engine(cfg)
    yield
    await dispose_directory_engine()
    await dispose_engine()


async def _clear_lookup_cache() -> None:
    """Drop this service's cached slug **and host** entries. See the module docstring — the
    five-minute TTL is correct in production and is pure cross-talk between tests.

    Both key shapes, because both are negative-cached: a test that seeds a domain would
    otherwise be answered from the `_MISS` a previous test's 404 wrote under the same host.
    Hosts are not per-test unique the way slugs are — `merchant.com` reads better in a test than
    a random string, and the alternative is every assertion carrying a UUID.

    Folded into the `client` fixture rather than standing on its own, so it cannot be ordered
    before `init_redis` or after `close_redis` by the order a test happens to name its fixtures.
    """
    redis = get_redis()
    keys: list[str] = []
    for pattern in ("storefront:slug:*", "storefront:host:*"):
        keys += [key async for key in redis.scan_iter(match=pattern)]
    if keys:
        await redis.delete(*keys)


@pytest_asyncio.fixture
async def client(session: AsyncSession, engines: None) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's org-bound session, so merchant writes are visible to
    asserts.

    `get_tenant_session` wraps `get_session`, so overriding that one dependency routes every
    authenticated route through the shared session while leaving the chokepoint's own binding
    intact. **The anonymous route is deliberately not overridden**: it opens its own transaction
    through `session_scope` on the real engine and commits, exactly as it does in production.

    Redis is initialised for real, and opened and closed per test: the client binds itself to the
    event loop it was created on, so a cached one leaks into the next test as
    `Event loop is closed`.
    """
    init_redis(settings().redis_url)
    await _clear_lookup_cache()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await _clear_lookup_cache()
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
