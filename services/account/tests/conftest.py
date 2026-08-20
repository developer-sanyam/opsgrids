"""Fixtures for account-service tests.

Tests run against **real PostgreSQL**, never SQLite. The schema leans on `citext`, partial
unique indexes and JSONB; a test that passes on SQLite proves nothing about the database
this ships on.

The fixture set is deliberately four people in two reporting lines: a manager with a
report, and another manager with an outsider. Almost every authorization question here is
"can the manager reach the outsider", and it needs all four to be asked at all — with the
outsider present, a scope test fails at the *authorization* check rather than at "no such
row", which would pass for the wrong reason.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so
# only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Base, Membership, Organization
from app.schemas import OrganizationIn
from platform_core.db import get_session


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A function-scoped session against a freshly truncated schema.

    Truncating rather than wrapping everything in a rolled-back outer transaction, so the
    code under test performs its own real commits — which is the behaviour that matters
    for anything involving locking or constraints.
    """
    engine = create_async_engine(
        settings().database_url,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        async with s.begin():
            tables = ", ".join(
                f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables)
            )
            await s.execute(text(f"TRUNCATE {tables} CASCADE"))
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def org(session: AsyncSession) -> Organization:
    async with session.begin():
        return await services.create_organization(
            session,
            OrganizationIn(name="Acme Technologies", slug="acme"),
            actor_user_id=None,
        )


@pytest_asyncio.fixture
async def people(session: AsyncSession, org: Organization) -> dict[str, Membership]:
    async with session.begin():
        manager = await services.add_member(
            session,
            org_id=org.id,
            email="manager@example.com",
            full_name="Maya Manager",
            actor_user_id=None,
            role_codes=["ORG_ADMIN"],
        )
        other_manager = await services.add_member(
            session,
            org_id=org.id,
            email="other@example.com",
            full_name="Omar Other",
            actor_user_id=None,
            role_codes=["ORG_ADMIN"],
        )
    async with session.begin():
        report = await services.add_member(
            session,
            org_id=org.id,
            email="report@example.com",
            full_name="Rhea Report",
            actor_user_id=None,
            role_codes=["MEMBER"],
        )
        outsider = await services.add_member(
            session,
            org_id=org.id,
            email="outsider@example.com",
            full_name="Otto Outsider",
            actor_user_id=None,
            role_codes=["MEMBER"],
        )
        report.manager_membership_id = manager.id
        report.location_codes = "MUM"
        outsider.manager_membership_id = other_manager.id
        outsider.location_codes = "DEL"

    return {
        "manager": manager,
        "other_manager": other_manager,
        "report": report,
        "outsider": outsider,
    }


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's session, so writes are visible to assertions."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
