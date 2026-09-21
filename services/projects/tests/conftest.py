"""Fixtures for projects-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security and cross-table
foreign keys, which SQLite cannot model the same way), the **app** role rather than the superuser
(so RLS is visible here instead of only in production — a suite run as a bypassing role makes
every isolation assertion a false pass), and the tenant bound on every `begin()` because the GUC
is transaction-local. This mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture seeds a small, coherent portfolio through the real service write paths: two
members, a project owned by each (so OWN scope has something to narrow to and an outsider to
refuse), a milestone and a board task on each.
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
    "tasks",
    "milestones",
    "projects",
    "members",
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
    write = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)

    # Each member's `directory_id` stands in for the account-directory id a real login would
    # carry as the JWT `sref` claim — a different id space from `member.id` (`services.py`'s
    # `_owner_directory_id`). Generated here rather than left `None` so a task-write OWN test
    # has a caller whose `subject_ref` genuinely resolves through the link.
    directory_a = uuid.uuid4()
    directory_b = uuid.uuid4()

    async with session.begin():
        member_a = await services.create_member(
            session,
            write,
            ref="p-01",
            name="Aarav Sharma",
            role="Delivery Lead",
            directory_id=directory_a,
        )
        member_b = await services.create_member(
            session,
            write,
            ref="p-02",
            name="Lena Nowak",
            role="Product Manager",
            directory_id=directory_b,
        )

        project_a = await services.create_project(
            session,
            write,
            code="PRJ-104",
            name="Northwind Trade Portal",
            client="Northwind Trading",
            owner_id=member_a.id,
            phase="build",
            health="on_track",
            progress_pct=62,
            start_date=date(2026, 4, 1),
            target_date=date(2026, 10, 15),
            budget_minor=1_240_000_000,
            spent_minor=732_000_000,
            currency="INR",
            team_size=9,
            open_tasks=34,
        )
        project_b = await services.create_project(
            session,
            write,
            code="PRJ-108",
            name="Helios Grid Analytics",
            client="Helios Energy",
            owner_id=member_b.id,
            phase="design",
            health="at_risk",
            progress_pct=34,
            start_date=date(2026, 6, 10),
            target_date=date(2026, 12, 20),
            budget_minor=1_860_000_000,
            spent_minor=510_000_000,
            currency="INR",
            team_size=7,
            open_tasks=41,
        )

        milestone_a = await services.create_milestone(
            session,
            write,
            project_id=project_a.id,
            title="Payment gateway integration",
            owner_id=member_a.id,
            due_date=date(2026, 8, 15),
            status="on_track",
            progress_pct=48,
        )
        task_a = await services.create_task(
            session,
            write,
            project_id=project_a.id,
            title="Bulk-order CSV import",
            assignee_id=member_a.id,
            stage="backlog",
            priority="low",
            points=5,
        )
        task_b = await services.create_task(
            session,
            write,
            project_id=project_b.id,
            title="Substation ingestion spike",
            assignee_id=member_b.id,
            stage="backlog",
            priority="medium",
            points=3,
        )

    return {
        "member_a": member_a,
        "member_b": member_b,
        "project_a": project_a,
        "project_b": project_b,
        "milestone_a": milestone_a,
        "task_a": task_a,
        "task_b": task_b,
        # Plain strings for building request URLs and JSON bodies: a request that gets refused
        # (403/404) rolls back the shared test session, which expires every ORM instance —
        # re-touching `.id` on one after that outside the SQLAlchemy greenlet context raises
        # `MissingGreenlet`. These are captured once, before any HTTP round trip can expire them.
        "milestone_a_id": str(milestone_a.id),
        "task_a_id": str(task_a.id),
        "task_b_id": str(task_b.id),
        # `Project.owner_id` / `Milestone.owner_id` OWN checks compare directly against
        # `members.id` (matching the pre-existing detail route, `routes.py`'s `read_project`).
        "subject_a": member_a.id,
        "subject_b": member_b.id,
        # `Task.assignee_id` OWN checks resolve through the assignee's `directory_id` instead
        # (`services.py`'s `_owner_directory_id`) — these are what a task-write OWN caller's
        # `subject_ref` must equal.
        "directory_a": directory_a,
        "directory_b": directory_b,
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
