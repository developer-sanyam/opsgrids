"""Fixtures for project-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security and cross-table
foreign keys, which SQLite cannot model the same way), the **app** role rather than the superuser
(so RLS is visible here instead of only in production — a suite run as a bypassing role makes
every isolation assertion a false pass), and the tenant bound on every `begin()` because the GUC
is transaction-local. This mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture seeds a small, coherent portfolio through the real service write paths: two
people, an engagement owned by each (so OWN scope has something to narrow to and an outsider to
refuse), and a board card assigned to each.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
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
    "tasks",
    "projects",
    "people",
    "audit_logs",
    "outbox",
    "processed_events",
)


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
    """A small portfolio seeded through the real write paths."""
    write = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
    today = date.today()

    async with session.begin():
        person_a = await services.create_person(
            session,
            write,
            ref="p01",
            name="Nadia Rahman",
            role="Head of Delivery",
            location="Bengaluru",
            capacity_hours=20,
        )
        person_b = await services.create_person(
            session,
            write,
            ref="p02",
            name="Arman Qureshi",
            role="Engineering Lead",
            location="Bengaluru",
            capacity_hours=30,
        )

        project_a = await services.create_project(
            session,
            write,
            code="PB-01",
            name="Helios Web Platform",
            client="Helios Retail",
            status="on_track",
            progress_pct=62,
            budget_minor=24_000_000,
            spent_minor=15_000_000,
            currency="USD",
            owner_id=person_a.id,
            start_date=date(2026, 5, 4),
            due_date=today + timedelta(days=60),
            billable=True,
            open_tasks=14,
        )
        project_b = await services.create_project(
            session,
            write,
            code="PB-03",
            name="Meridian Data Warehouse",
            client="Meridian Bank",
            status="blocked",
            progress_pct=30,
            budget_minor=32_000_000,
            spent_minor=14_000_000,
            currency="USD",
            owner_id=person_b.id,
            start_date=date(2026, 4, 20),
            due_date=today + timedelta(days=90),
            billable=True,
            open_tasks=9,
        )

        task_a = await services.create_task(
            session,
            write,
            project_id=project_a.id,
            key="PB-210",
            title="Responsive nav for tablet",
            stage="in_progress",
            assignee_id=person_a.id,
            estimate_hours=6,
            priority="medium",
            tags=["frontend"],
            # Due *today*, not tomorrow: `domain.week_dates` is Monday-first, so on a Sunday
            # "tomorrow" is next week and the summary's due-this-week count drops to zero.
            # Today is the one offset that is inside its own week on every weekday.
            due_date=today,
        )
        task_b = await services.create_task(
            session,
            write,
            project_id=project_b.id,
            key="PB-215",
            title="ETL pipeline for ledger sync",
            stage="backlog",
            assignee_id=person_b.id,
            estimate_hours=16,
            priority="high",
            tags=["data"],
            due_date=None,
        )

    return {
        "person_a": person_a,
        "person_b": person_b,
        "project_a": project_a,
        "project_b": project_b,
        "task_a": task_a,
        "task_b": task_b,
        # OWN scope matches the owner's person id against the caller's subject_ref.
        "subject_a": person_a.id,
        "subject_b": person_b.id,
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
