"""Fixtures for customer-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security, which SQLite
cannot model), the **app** role rather than the superuser (so RLS is visible here instead
of only in production — a suite run as a bypassing role makes every isolation assertion a
false pass), and the tenant bound on every `begin()` because the GUC is transaction-local.
This mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a
tenant needs no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture seeds a small, coherent book through the real service write paths: two
reps, a company each (so OWN scope has something to narrow to), and a contact at each.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so
# only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core import cache
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import FakeRedis, ctx_for

TENANT_TABLES = (
    "contacts",
    "companies",
    "owners",
    "audit_logs",
    "outbox",
    "processed_events",
)

# Two known subjects (rep ids), so OWN scope has an owner to match and an outsider to refuse.
SUBJECT_A = uuid.uuid4()
SUBJECT_B = uuid.uuid4()

DEMO_NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def redis() -> Iterator[FakeRedis]:
    """A stand-in Redis. The reveal route spends a per-user budget from Redis and **fails
    closed** (503) when it is unreachable, so every test that reveals needs one installed —
    `ASGITransport` never runs the lifespan that would open a real client. Sync on purpose: it is
    autouse, and an autouse *async* fixture would demand an event loop the sync tests lack."""
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


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
    """A small book seeded through the real write paths."""
    write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)

    async with session.begin():
        rep_a = await services.create_owner(
            session,
            write,
            full_name="Sofia Marchetti",
            email="sofia.marchetti@opsgrids.example.com",
            role="Regional Sales Head",
            owner_id=SUBJECT_A,
        )
        rep_b = await services.create_owner(
            session,
            write,
            full_name="Kabir Malhotra",
            email="kabir.malhotra@opsgrids.example.com",
            role="Enterprise AE",
            owner_id=SUBJECT_B,
        )

        company_a = await services.create_company(
            session,
            write,
            name="Northwind Logistics",
            domain="northwind.co",
            industry="Logistics",
            size_band="501-1000",
            city="Mumbai",
            country="India",
            status="customer",
            owner_id=SUBJECT_A,
            annual_revenue_minor=8_400_000_000 * 100,
            currency="INR",
            created_at=DEMO_NOW,
        )
        company_b = await services.create_company(
            session,
            write,
            name="Meridian Steelworks",
            domain="meridiansteel.com",
            industry="Manufacturing",
            size_band="1000+",
            city="Jamshedpur",
            country="India",
            status="prospect",
            owner_id=SUBJECT_B,
            annual_revenue_minor=21_000_000_000 * 100,
            currency="INR",
            created_at=DEMO_NOW,
        )

        contact_a = await services.create_contact(
            session,
            write,
            full_name="Rajesh Khanna",
            title="VP Operations",
            email="rajesh.khanna@northwind.co",
            phone="+91 98 xxx210",
            company_id=company_a.id,
            status="customer",
            owner_id=SUBJECT_A,
            last_activity_at=DEMO_NOW,
            created_at=DEMO_NOW,
        )
        contact_b = await services.create_contact(
            session,
            write,
            full_name="Suresh Iyer",
            title="Chief Information Officer",
            email="suresh.iyer@meridiansteel.com",
            phone="+91 98 xxx771",
            company_id=company_b.id,
            status="qualified",
            owner_id=SUBJECT_B,
            last_activity_at=None,
            created_at=DEMO_NOW,
        )

    return {
        "rep_a": rep_a,
        "rep_b": rep_b,
        "company_a": company_a,
        "company_b": company_b,
        "contact_a": contact_a,
        "contact_b": contact_b,
        # Plain-value snapshots for use after a request rolled the session back —
        # a rollback expires ORM instances, and touching one then needs IO.
        "company_a_id": company_a.id,
        "company_b_id": company_b.id,
        "contact_a_id": contact_a.id,
        "contact_b_id": contact_b.id,
        "subject_a": SUBJECT_A,
        "subject_b": SUBJECT_B,
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
