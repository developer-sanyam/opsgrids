"""Fixtures for standup-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security, which SQLite cannot
model the same way), the **app** role rather than the superuser (so RLS is visible here instead
of only in production — a suite run as a bypassing role makes every isolation assertion a false
pass), and the tenant bound on every `begin()` because the GUC is transaction-local. This
mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture posts two people's check-ins for today through the real service write path
(so OWN scope has something to narrow to and an outsider to refuse), one of them blocked.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import ctx_for

TENANT_TABLES = (
    "standup_entries",
    "audit_logs",
    "outbox",
    "processed_events",
)

#: The day the fixtures post for — "today" for every test.
TODAY = date.today()


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`."""
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
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def data(session: AsyncSession, org_id: uuid.UUID) -> dict:
    """Two people's check-ins for today, seeded through the real write path."""
    write = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()

    async with session.begin():
        entry_a = await services.post_entry(
            session,
            write,
            person_id=person_a,
            person_ref="p02",
            entry_date=TODAY,
            yesterday="Unblocked the Stellar CRM data mapping.",
            today="Break down the CRM migration epic.",
            blockers="Waiting on Stellar's sandbox credentials.",
        )
        entry_b = await services.post_entry(
            session,
            write,
            person_id=person_b,
            person_ref="p03",
            entry_date=TODAY,
            yesterday="Shipped dark-theme design tokens for review.",
            today="Empty-state designs for the Vertex portal.",
            blockers=None,
        )

    return {
        "entry_a": entry_a,
        "entry_b": entry_b,
        # OWN scope matches the entry's person id against the caller's subject_ref.
        "subject_a": person_a,
        "subject_b": person_b,
    }


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
