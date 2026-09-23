"""Fixtures for lead-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security, which SQLite cannot
model the same way), the **app** role rather than the superuser (so RLS is visible here instead
of only in production — a suite run as a bypassing role makes every isolation assertion a false
pass), and the tenant bound on every `begin()` because the GUC is transaction-local. This
mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant
needs no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture creates two leads through the real service write path (so OWN scope has
something to narrow to and an outsider to refuse), one of them qualified and email-bearing.
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

from app import clients, services
from app.config import settings
from app.main import app
from app.models import SCHEMA
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import ctx_for

TENANT_TABLES = (
    "leads",
    "assignment_rules",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def _stub_owners_active(monkeypatch: pytest.MonkeyPatch):
    """Every owner this suite's fixtures name is presumed an active rep in customer's
    directory — a real `owners:validate` call would need customer-service actually running.
    `tests/test_assignment.py` and `tests/test_authz.py` override this per-test with their own
    `monkeypatch.setattr` where the inactive/unknown case is the thing under test."""

    async def _none_invalid(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(clients, "invalid_owner_ids", _none_invalid)


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
    """Two leads, seeded through the real write path."""
    write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()

    async with session.begin():
        lead_a = await services.create_lead(
            session,
            write,
            full_name="Priya Sharma",
            company="Stellar Robotics",
            email="priya@stellar.example",
            phone="+919876543210",
            source="form",
            owner_id=owner_a,
            notes="Inbound from the pricing page.",
        )
        lead_b = await services.create_lead(
            session,
            write,
            full_name="Arjun Verma",
            company="Vertex Freight",
            email=None,
            phone=None,
            source="manual",
            owner_id=owner_b,
            notes="",
        )
        # Qualify lead_a so conversion tests have a lead that is legally convertible.
        lead_a = await services.update_lead(session, write, lead_a, LeadPatch(status="working"))
        lead_a = await services.update_lead(session, write, lead_a, LeadPatch(status="qualified"))

    return {
        "lead_a": lead_a,
        "lead_b": lead_b,
        # Plain-value snapshots for use after a request rolled the session back — a
        # rollback expires ORM instances, and touching one then needs IO.
        "lead_a_id": lead_a.id,
        "lead_b_id": lead_b.id,
        "owner_a": owner_a,
        "owner_b": owner_b,
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
