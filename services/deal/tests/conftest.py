"""Fixtures for deal-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security and a trigger,
which SQLite cannot model), the **app** role rather than the superuser (so RLS is visible
here instead of only in production — a suite run as a bypassing role makes every isolation
assertion a false pass), and the tenant bound on every `begin()` because the GUC is
transaction-local. This mirrors exactly what `get_tenant_session` does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a
tenant needs no account-service row to exist — a bare uuid is a whole tenant. The
company / contact ids deals point at are likewise bare UUIDs: the account book lives in
another schema and no constraint reaches across.

The `data` fixture seeds a small, coherent pipeline through the real service write paths:
two reps with a deal each (so OWN scope has something to narrow to — one open, one
closed-won) and a short timeline.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
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
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import ctx_for

TENANT_TABLES = (
    "activities",
    "deals",
    "audit_logs",
    "outbox",
    "processed_events",
)

# Two known subjects (rep ids), so OWN scope has an owner to match and an outsider to refuse.
SUBJECT_A = uuid.uuid4()
SUBJECT_B = uuid.uuid4()

COMPANY_A = uuid.uuid4()
COMPANY_B = uuid.uuid4()
CONTACT_A = uuid.uuid4()

DEMO_NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


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
        # The append-only trigger refuses even a cascaded DELETE, so truncate directly —
        # TRUNCATE does not fire row-level triggers.
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
    """A small pipeline seeded through the real write paths."""
    write = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)

    async with session.begin():
        # An open deal run by SUBJECT_A, with a timeline.
        deal_a = await services.create_deal(
            session,
            write,
            name="Fleet telematics rollout",
            company_id=COMPANY_A,
            primary_contact_id=CONTACT_A,
            value_minor=32_000_000 * 100,
            currency="INR",
            stage="negotiation",
            owner_id=SUBJECT_A,
            probability=70,
            close_date=date(2026, 8, 29),
            source="Referral",
            created_at=DEMO_NOW,
        )
        # A decided deal run by SUBJECT_B.
        deal_b = await services.create_deal(
            session,
            write,
            name="Omnichannel CRM",
            company_id=COMPANY_B,
            value_minor=21_000_000 * 100,
            currency="INR",
            stage="closed_won",
            owner_id=SUBJECT_B,
            probability=100,
            close_date=date(2026, 8, 12),
            source="Referral",
            created_at=DEMO_NOW,
        )

        act_a = await services.log_activity(
            session,
            write,
            activity_type="call",
            subject="Discovery call on telematics scope",
            body="Rajesh wants a 30-vehicle pilot first.",
            deal_id=deal_a.id,
            company_id=COMPANY_A,
            contact_id=CONTACT_A,
            owner_id=SUBJECT_A,
            occurred_at=DEMO_NOW,
            direction="outbound",
        )
        act_b = await services.log_activity(
            session,
            write,
            activity_type="meeting",
            subject="Kickoff - Harbor & Co go-live",
            deal_id=deal_b.id,
            company_id=COMPANY_B,
            owner_id=SUBJECT_B,
            occurred_at=DEMO_NOW,
        )

    return {
        "deal_a": deal_a,
        "deal_b": deal_b,
        "act_a": act_a,
        "act_b": act_b,
        # Plain-value snapshots for use after a request rolled the session back —
        # a rollback expires ORM instances, and touching one then needs IO.
        "deal_a_id": deal_a.id,
        "deal_b_id": deal_b.id,
        "act_a_id": act_a.id,
        "act_b_id": act_b.id,
        "company_a": COMPANY_A,
        "contact_a": CONTACT_A,
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
