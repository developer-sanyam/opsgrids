"""Fixtures for employee-service tests.

Same shape as account- and product-service, for the same reasons: real PostgreSQL rather than
SQLite (the schema leans on partial unique indexes and row-level security, which SQLite cannot
model), the **app** role rather than the superuser (so RLS is visible here instead of only in
production — a suite run as a bypassing role makes every isolation assertion a false pass), and
the tenant bound on every `begin()` because the GUC is transaction-local.

The people fixture is deliberately four employees in two reporting lines: a manager with a
report, and another manager with an outsider. Almost every scope question here is "can the
manager reach the outsider", and it needs all four — with the outsider present, a scope test
fails at the *authorization* check rather than at "no such row", which would pass for the wrong
reason.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a
tenant needs no account-service row to exist — a bare uuid is a whole tenant.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only
# one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Employee
from app.schemas import EmployeeIn
from platform_core import cache
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from platform_core.testing import cross_service_schema_lock
from tests.helpers import FakeRedis, ctx_for

#: Every table here is tenant-scoped or global-and-empty, so all are safe to truncate.
TENANT_TABLES = ("employees", "audit_logs", "outbox", "processed_events")

#: This suite is not parallel-safe against `services/approval/tests/test_cross_service_contract.py`,
#: which drives employee's real write path and its real `/internal/v1/hierarchy` route through
#: `tests/emit_events.py`, `tests/bump_employee.py` and `tests/serve_hierarchy.py` — so it holds
#: live rows in *this* schema while it runs. The `TRUNCATE` below would delete them mid-test.
#: `make test-services` runs the two services concurrently, so the two suites take this advisory
#: lock against each other; see `platform_core.testing`. Every other service is untouched and
#: stays fully parallel.
CROSS_SERVICE_LOCK_HOLDER = "services/employee/tests"


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture(autouse=True)
def redis() -> Iterator[FakeRedis]:
    """A stand-in Redis so the app factory's `redis=True` never reaches a real server here.

    Sync rather than async on purpose: it is autouse and so runs for the sync `test_service`
    boot tests too, and an autouse *async* fixture would demand an event loop those do not have.
    """
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def org_id() -> uuid.UUID:
    """A tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`.

    The binding is reapplied on each `begin()` rather than once, because the GUC is
    transaction-local — a single call would leave later transactions in a test unbound and
    reading nothing. This mirrors exactly what `get_tenant_session` does per request.

    Pointing this at the admin or superuser DSN would make row-level security invisible here and
    every isolation assertion a false pass — the failure ADR-0002 calls worse than no test.

    The whole fixture — the truncation *and* the test that runs under it — is wrapped in the
    cross-service advisory lock (see `CROSS_SERVICE_LOCK_HOLDER` above). Locking only the
    `TRUNCATE` would not be enough: the rows this test then writes are in the same schema the
    sibling suite is reading, so the exclusion has to last as long as the test does. It is held
    per test rather than for the whole session so the two suites interleave at test granularity
    instead of one waiting out the other end to end. Every database-touching test in this
    service reaches the database through this fixture, so this is the one place it belongs.
    """
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in TENANT_TABLES)

    async with cross_service_schema_lock(cfg.app_dsn, SCHEMA, holder=CROSS_SERVICE_LOCK_HOLDER):
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
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant.

    Use this only where a second organization's data has to exist for an isolation test.
    Reaching for it to make a tenant-scoped test pass would hide exactly what the policies
    exist to catch.
    """
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _hire(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    employee_no: str,
    full_name: str,
    reporting_manager_id: uuid.UUID | None = None,
    location_code: str | None = None,
) -> Employee:
    """Onboard one employee through the real write path, as an ORG-scoped HR admin would."""
    ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
    return await services.create_employee(
        session,
        ctx,
        EmployeeIn(
            employee_no=employee_no,
            full_name=full_name,
            reporting_manager_id=reporting_manager_id,
            location_code=location_code,
        ),
    )


@pytest_asyncio.fixture
async def people(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Employee]:
    async with session.begin():
        manager = await _hire(session, org_id, employee_no="EMP0001", full_name="Maya Manager")
        other_manager = await _hire(session, org_id, employee_no="EMP0002", full_name="Omar Other")
    async with session.begin():
        report = await _hire(
            session,
            org_id,
            employee_no="EMP0003",
            full_name="Rhea Report",
            reporting_manager_id=manager.id,
            location_code="MUM",
        )
        outsider = await _hire(
            session,
            org_id,
            employee_no="EMP0004",
            full_name="Otto Outsider",
            reporting_manager_id=other_manager.id,
            location_code="DEL",
        )
    return {
        "manager": manager,
        "other_manager": other_manager,
        "report": report,
        "outsider": outsider,
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
