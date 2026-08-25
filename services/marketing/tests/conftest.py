"""Fixtures for marketing-service tests.

Same shape as the employee service, for the same reasons: real PostgreSQL rather than SQLite (the
schema leans on row-level security, which SQLite cannot model), the **app** role rather than the
superuser (so RLS is visible here instead of only in production — a suite run as a bypassing role
makes every isolation assertion a false pass), and the tenant bound on every `begin()` because the
GUC is transaction-local.

The seeded data is a small, realistic slice of the SPA's fixtures: two audiences, four campaigns
across three channels in a mix of statuses, and a two-point revenue series. It is enough for the
rollups to have something to fold, and for a cross-tenant test to have a victim to try to reach.

`organization_id` is a plain UUID here, never a foreign key across a schema boundary, so a tenant
needs no account-service row to exist — a bare uuid is a whole tenant.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator, Iterator
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
from app.models import SCHEMA, Campaign
from app.schemas import AudienceIn, CampaignIn
from platform_core import cache
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import FakeRedis, ctx_for

#: Every table here is tenant-scoped or global-and-empty, so all are safe to truncate.
TENANT_TABLES = (
    "campaigns",
    "audiences",
    "revenue_spend_points",
    "audit_logs",
    "outbox",
    "processed_events",
)

WRITE = "marketing.analytics.campaign.write"
READ = "marketing.analytics.campaign.read"


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture(autouse=True)
def redis() -> Iterator[FakeRedis]:
    """A stand-in Redis so the app factory's `redis=True` never reaches a real server here."""
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
    transaction-local. This mirrors exactly what `get_tenant_session` does per request. Pointing
    this at the admin or superuser DSN would make row-level security invisible here and every
    isolation assertion a false pass — the failure ADR-0002 calls worse than no test.
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


async def seed_org(session: AsyncSession, org_id: uuid.UUID) -> dict[str, object]:
    """Create a compact, realistic marketing slice for one org through the real write paths.

    Two audiences; four campaigns across search / social / email in live, paused, draft and ended
    states; and a two-point revenue series. Returned ids let a test reach specific rows.
    """
    write_ctx = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        ent = await services.create_audience(
            session,
            write_ctx,
            AudienceIn(
                name="Enterprise India",
                size=4210,
                criteria="1000+ employees · India HQ",
                created_at=date(2025, 11, 2),
            ),
        )
        saas = await services.create_audience(
            session,
            write_ctx,
            AudienceIn(
                name="Growth-stage SaaS",
                size=9840,
                criteria="SaaS · Series A-C",
                created_at=date(2025, 9, 18),
            ),
        )
    lakh = 10_000_000
    async with session.begin():
        # (name, channel, status, budget, spent, revenue, pipeline, leads, conversions, aud)
        specs = [
            ("Diwali Enterprise Push", "search", "live", 32, 28, 142, 320, 640, 34, ent.id),
            ("Retargeting Trials", "social", "live", 9, 8, 47, 66, 520, 41, saas.id),
            ("Founder Newsletter", "email", "paused", 4, 3, 12, 54, 300, 0, ent.id),
            ("Reels Test", "social", "draft", 6, 0, 0, 0, 0, 0, saas.id),
        ]
        campaigns: list[Campaign] = []
        for name, channel, status_, budget, spent, revenue, pipeline, leads, conv, aud in specs:
            c = await services.create_campaign(
                session,
                write_ctx,
                CampaignIn(
                    name=name,
                    channel=channel,
                    status=status_,
                    budget_minor=budget * lakh,
                    spent_minor=spent * lakh,
                    revenue_minor=revenue * lakh,
                    pipeline_minor=pipeline * lakh,
                    leads=leads,
                    conversions=conv,
                    goal="Pipeline",
                    audience_id=aud,
                    start_date=date(2026, 6, 1),
                    end_date=None,
                    owner="Meghna Rao",
                ),
            )
            campaigns.append(c)
        await services.upsert_revenue_point(
            session,
            write_ctx,
            position=0,
            label="Jan",
            revenue_minor=172 * lakh,
            spend_minor=58 * lakh,
        )
        await services.upsert_revenue_point(
            session,
            write_ctx,
            position=1,
            label="Feb",
            revenue_minor=190 * lakh,
            spend_minor=62 * lakh,
        )
    return {
        "audiences": [ent, saas],
        "campaigns": campaigns,
    }


@pytest_asyncio.fixture
async def seeded(session: AsyncSession, org_id: uuid.UUID) -> dict[str, object]:
    """The seeded org — two audiences, four campaigns, a two-point revenue series."""
    return await seed_org(session, org_id)


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    from app.main import app

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
