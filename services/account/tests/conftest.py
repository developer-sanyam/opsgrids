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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so
# only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Base, Membership, Organization
from app.schemas import OrganizationIn
from platform_core import cache
from platform_core.db import (
    TENANT_GUC,
    dispose_engine,
    get_directory_session,
    get_session,
    init_engine,
)
from tests.helpers import FakeRedis


@pytest_asyncio.fixture(autouse=True)
async def _engine() -> AsyncIterator[None]:
    """The process-wide engine the app lifespan would normally open.

    Needed because routes that cannot bind a tenant up front — accepting an invitation is the
    one — open their own tenant-scoped transaction from the shared sessionmaker after reading
    which organization they belong to. Overriding `get_session` is not enough for those: they
    ask the sessionmaker directly, exactly as they do in production.

    Per test rather than per session: an asyncpg pool belongs to the event loop that created
    it, and pytest-asyncio gives each test its own. A session-scoped engine works until a test
    actually uses it from a different loop, and then fails as a pending-task error that says
    nothing about the cause.
    """
    init_engine(settings())
    yield
    await dispose_engine()


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest_asyncio.fixture
async def org() -> AsyncIterator[Organization]:
    """The organization every other fixture hangs off, created on the **admin** engine.

    Two things force this shape, and the fixture graph had to invert for them.

    Truncating needs the admin role: TRUNCATE is withheld from the app role because it is not
    filtered by row-level security, so a role holding it could empty a table across every
    tenant at once.

    Creating an organization needs it too. The policy on `organizations` compares `id` against
    the tenant binding, so a tenant literally cannot insert one — there is no id to name before
    the row exists. That is correct rather than awkward: bringing an organization into being is
    a platform operation, not something a customer does to itself.

    So this runs first, and the tenant session below binds to what it created.
    """
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables))

    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))

    maker = async_sessionmaker(admin, expire_on_commit=False)
    async with maker() as s, s.begin():
        organization = await services.create_organization(
            s,
            OrganizationIn(name="Acme Technologies", slug="acme"),
            actor_user_id=None,
        )
    yield organization
    await admin.dispose()


@pytest_asyncio.fixture
async def session(org: Organization) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org`.

    The binding is reapplied on each `begin()` rather than once, because the GUC is
    transaction-local: it does not survive its transaction, so a single call would leave the
    second and later transactions in a test unbound and reading nothing. This mirrors exactly
    what `get_tenant_session` does per request in production.

    Pointing this at the admin or superuser DSN would make row-level security invisible here
    and every isolation assertion a false pass — the failure ADR-0002 calls out as worse than
    having no test at all.
    """
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind_tenant(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org.id}', true)")

        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_session(org: Organization) -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for the operations a tenant genuinely cannot perform.

    Creating an organization is the main one: the policy compares `id` to the tenant binding,
    so there is no value a tenant could set to insert one. Use this only where the operation is
    legitimately a platform action — reaching for it to make a tenant-scoped test pass would
    hide exactly what these policies exist to catch.
    """
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


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


@pytest_asyncio.fixture(autouse=True)
async def redis() -> AsyncIterator[FakeRedis]:
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest_asyncio.fixture
async def directory_session() -> AsyncIterator[AsyncSession]:
    """A real directory session: BYPASSRLS, SELECT-only, no tenant binding.

    The internal routes must be exercised through something that genuinely reads across
    tenants, because that is what they do in production. Handing them the tenant-bound
    session instead would make them pass for the wrong reason — and then row-level security
    landing would break sign-in with the tests still green, which is the exact failure mode
    ADR-0002 spends its length warning about.
    """
    engine = create_async_engine(settings().directory_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    session: AsyncSession, directory_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's session, so writes are visible to assertions."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    async def _directory_override() -> AsyncIterator[AsyncSession]:
        yield directory_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_directory_session] = _directory_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
