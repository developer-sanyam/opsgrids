"""Fixtures for scheduler-service tests.

Same shape as approval- and subscription-service's own conftests, for the same reasons: real
PostgreSQL (row-level security and `FOR UPDATE SKIP LOCKED` are not things SQLite can model), the
service's **app** role rather than the superuser (so a policy that is inert reads as a failure
here rather than a false pass), and the tenant bound on every transaction because the GUC is
transaction-local.

Everything scheduler owns is tenant-scoped, so every table is truncated between tests. The
`session` fixture binds `org_id`; `admin_session` is the BYPASSRLS reader/seeder used to prove a
cross-tenant read is refused and to seed a second organization's rows directly; `sweep`/
`sweep_engines` wire the due-job sweep exactly as `app/cron.py`'s `main()` does.
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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models import SCHEMA
from platform_core.db import TENANT_GUC

TENANT_TABLES = (
    "scheduled_jobs",
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
    """The app role, every transaction bound to `org_id` — what a real request or a real
    consumer message runs on."""
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
async def bind_session(session: AsyncSession) -> AsyncIterator[Any]:
    """A factory for an app-role session bound to an arbitrary organization — what proving row-
    level security actually refuses a *different* tenant needs, since the plain `session` fixture
    is fixed to `org_id` for the whole test. Depends on `session` only for its truncation."""
    cfg = settings()
    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())

    @asynccontextmanager
    async def _bind(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    yield _bind
    await engine.dispose()


@pytest_asyncio.fixture
async def sweep_engines(session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
    """The two engines the due-job sweep runs on, wired exactly as `app/cron.py`'s `main()`.
    Depends on `session` only for its truncation, so a sweep test starts from a clean schema.

    * **discovery** connects on the real **outbox DSN** — the BYPASSRLS role the sweep discovers
      due work with. Using it here rather than the admin role proves the column grant
      `migrations/env.py:grant_sweep_reader` is actually in effect.
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
    """A `Sweep` over real PostgreSQL, drivable one `tick(now)` at a time — real rows, real
    locks, real policies, with no clock and no engine patched."""
    from app.cron import Sweep

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

    yield Sweep(discovery_factory=discovery, work_scope=work_scope)
