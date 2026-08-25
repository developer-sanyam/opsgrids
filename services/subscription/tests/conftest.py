"""Fixtures for subscription-service tests.

Same shape as product- and catalog-service, for the same reasons: real PostgreSQL (the schema
leans on a trigger and row-level security SQLite cannot model), the service's **app** role rather
than the superuser (so a policy that is inert reads as a failure here rather than a false pass),
and the tenant bound on every transaction because the GUC is transaction-local.

Everything subscription owns is tenant-scoped, so every table is truncated between tests. The
`session` fixture binds `org_id`; `admin_session` is the BYPASSRLS reader/seeder used to prove a
cross-tenant read is refused and to read the policied `outbox` a test staged.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
from tests.helpers import FakeCatalog

TENANT_TABLES = (
    "subscriptions",
    "subscription_items",
    "subscription_transitions",
    "dunning_cycles",
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
def catalog() -> FakeCatalog:
    """A stand-in for the catalog pin lookup, so no test makes a real HTTP call."""
    return FakeCatalog()


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
    and reads the policied `outbox` across the tenant binding."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def sweep_engines(session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
    """The two engines the dunning sweep runs on, wired exactly as the process wires them.

    Depends on `session` only for its truncation, so a sweep test starts from a clean schema.

    * **discovery** connects on the real **outbox DSN** — the BYPASSRLS role the sweep discovers
      due work with. Using it here rather than the admin role is deliberate: it proves the `GRANT
      SELECT ON dunning_cycles` in `migrations/env.py` is actually applied. Without that grant the
      sweep would raise; without BYPASSRLS it would quietly find nothing, which is the failure this
      whole seam exists to prevent.
    * **work** connects on the app role, with the tenant bound per transaction, which is what
      `session_scope(organization_id=...)` does in the process.
    """
    cfg = settings()
    discovery = create_async_engine(cfg.outbox_dsn, connect_args=_connect_args())
    work = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    yield discovery, work
    await discovery.dispose()
    await work.dispose()


@pytest_asyncio.fixture
async def sweep(sweep_engines: tuple[Any, Any]) -> AsyncIterator[Any]:
    """A `DunningSweep` over real PostgreSQL, drivable one `tick(now)` at a time.

    `tick` takes the clock as an argument, so the whole fourteen-day ladder is exercised in
    milliseconds against real rows, real locks and real policies — without sleeping, patching a
    clock, or compressing the policy into something the production code does not run.
    """
    from app.cron import DunningSweep

    discovery_engine, work_engine = sweep_engines
    discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    yield DunningSweep(discovery_factory=discovery, work_scope=work_scope)


@pytest_asyncio.fixture
async def client(session: AsyncSession, catalog: FakeCatalog) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's org-bound session and a fake catalog.

    `get_tenant_session` wraps `get_session`, so overriding that one dependency routes every route
    through the shared session while leaving the chokepoint's own binding intact.
    """
    from app.api.routes import get_catalog

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_catalog] = lambda: catalog
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
