"""Fixtures for notification-service tests.

Same shape as account-, product-, employee- and support-service, for the same reasons: real
PostgreSQL rather than SQLite (the schema leans on row-level security, which SQLite cannot
model), the **app** role rather than the superuser (so RLS is visible here instead of only in
production — a suite run as a bypassing role makes every isolation assertion a false pass), and
the tenant bound on every `begin()` because the GUC is transaction-local.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a tenant
needs no account-service row to exist — a bare uuid is a whole tenant.

`tests/test_domain.py` deliberately touches none of this: the rendering and suppression rules are
pure, and the two rules that protect a customer from receiving something wrong should not need
Postgres to be verified.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

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

#: Every table here is tenant-scoped or global-and-empty, so all are safe to truncate.
TENANT_TABLES = (
    "deliveries",
    "messages",
    "rules",
    "templates",
    "preferences",
    "audit_logs",
    "outbox",
    "processed_events",
)


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest_asyncio.fixture
def org_id() -> uuid.UUID:
    """A tenant. A bare UUID is the whole of it — no cross-schema row is needed."""
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`.

    The binding is reapplied on each `begin()` rather than once, because the GUC is
    transaction-local. This mirrors exactly what `get_tenant_session` does per request, and what
    the consumer's `session_scope` does per message. Pointing this at the admin DSN would make
    row-level security invisible here and every isolation assertion a false pass — the failure
    ADR-0002 calls worse than no test.
    """
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
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant.

    Use this only where a second organization's data has to exist for an isolation test. Reaching
    for it to make a tenant-scoped test pass would hide exactly what the policies exist to catch.
    """
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def seed_automation(session: AsyncSession, org_id: uuid.UUID) -> None:
    """One email template and one rule on it, written through the real operator path.

    Deliberately the *support* ticket rule: it is the shape the outbound email promised — an
    acknowledgement that goes out because a ticket was opened, with nobody pressing anything.
    """
    from app import services
    from platform_core.auth import Scope
    from tests.helpers import ctx_for

    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code="ticket_acknowledged",
            channel="email",
            subject="We have your ticket {{number}}",
            body="Hello {{requester_name}}, we opened {{number}} and are on it.",
        )
        await services.upsert_rule(
            session,
            write,
            code="ack_new_tickets",
            event_type="support.desk.ticket.created",
            template_code="ticket_acknowledged",
            channel="email",
            category="operational",
            recipient_path="requester_email",
        )
