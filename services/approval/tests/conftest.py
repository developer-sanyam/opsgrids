"""Fixtures for approval-service tests.

Same shape as subscription- and attendance-service, for the same reasons: real PostgreSQL rather
than SQLite (the schema leans on row-level security and a recursive CTE with the SQL-standard
`CYCLE` clause, neither of which SQLite can model), the service's **app** role rather than the
superuser (so a policy that is inert reads as a failure here rather than a false pass), and the
tenant bound on every transaction because the GUC is transaction-local.

Everything approval owns in this slice is tenant-scoped, so every table is truncated between
tests. The `session` fixture binds `org_id`; `admin_session` is the BYPASSRLS reader/seeder used
to prove a cross-tenant write is invisible and to seed a second organization's rows directly.
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
    "hierarchy_nodes",
    "approval_policies",
    "approval_requests",
    "approval_steps",
    "delegations",
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


@pytest_asyncio.fixture
async def sweep_engines(session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
    """The two engines the SLA sweep runs on, wired exactly as `app/cron.py`'s `main()` wires
    them. Depends on `session` only for its truncation, so a sweep test starts from a clean
    schema. Same shape as `services/subscription/tests/conftest.py`'s own `sweep_engines`.

    * **discovery** connects on the real **outbox DSN** — the BYPASSRLS role the sweep discovers
      due work with. Using it here rather than the admin role proves the column grant
      `migrations/env.py`'s `grant_sla_sweep_reader` applies is actually in effect: without it the
      sweep would raise `42501`; without BYPASSRLS it would quietly find nothing.
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
async def sweep(sweep_engines: tuple[Any, Any]) -> AsyncIterator[Any]:
    """An `EscalationSweep` over real PostgreSQL, drivable one `tick(now)` at a time — real rows,
    real locks, real policies, with no clock and no engine patched."""
    from app.cron import EscalationSweep

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

    yield EscalationSweep(discovery_factory=discovery, work_scope=work_scope)


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
