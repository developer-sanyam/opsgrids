"""Fixtures for insights-service tests.

Same shape as approval-, subscription- and attendance-service, for the same reasons: real
PostgreSQL rather than SQLite (row-level security cannot be modelled by SQLite at all), the
service's own **app** role rather than the superuser (so a policy that is inert reads as a
failure here rather than a false pass), and the tenant bound on every transaction because the
GUC is transaction-local.

Run against an isolated database, never the shared `opsgrids` one several worktrees' dev work
also lands in — `make insights-db` creates `opsgrids_insights`; point `DATABASE_URL` at it:

    DATABASE_URL=postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_insights \\
      uv run pytest
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

TENANT_TABLES = (
    "org_profile",
    "deal_facts",
    "lead_facts",
    "company_facts",
    "daily_snapshots",
    "metric_coverage",
    "backfill_runs",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_org() -> uuid.UUID:
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
        def _bind(_session: Any, _transaction: Any, connection: Any) -> None:
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


@pytest_asyncio.fixture
async def discovery_engines(session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
    """The two engines the sweeps run on, wired exactly as `app/worker.py`'s `main()` wires them.
    Depends on `session` only for its truncation, so a sweep test starts from a clean schema.

    * **discovery** connects on the real **outbox DSN** — the BYPASSRLS role the sweeps discover
      due work with. Using it here rather than the admin role proves the column grants this
      slice's own migration applies are actually in effect: without them the sweep would raise
      `42501`; without BYPASSRLS it would quietly find nothing.
    * **work** connects on the app role, with the tenant bound per transaction, matching
      `session_scope(organization_id=...)` in the real process.
    """
    cfg = settings()
    discovery = create_async_engine(cfg.outbox_dsn, connect_args=_connect_args())
    work = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    yield discovery, work
    await discovery.dispose()
    await work.dispose()


@pytest_asyncio.fixture
async def work_scope(discovery_engines: tuple[Any, Any]) -> Any:
    """A `WorkScope` over real PostgreSQL — one tenant-bound transaction per call, on the app
    role, exactly what `session_scope(organization_id=...)` gives the real process."""
    _discovery_engine, work_engine = discovery_engines
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    return scope


@pytest_asyncio.fixture
async def discovery_sessionmaker(
    discovery_engines: tuple[Any, Any],
) -> async_sessionmaker[AsyncSession]:
    discovery_engine, _work_engine = discovery_engines
    return async_sessionmaker(discovery_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's org-bound session, so writes are visible to asserts.

    `get_tenant_session` wraps `get_session`, so overriding that one dependency routes every
    route through the shared session while leaving the chokepoint's own binding intact.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
