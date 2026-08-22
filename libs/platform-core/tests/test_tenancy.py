"""The tenant chokepoint's building blocks, against the real database.

`assert_tenant` is the ADR-0002 §4 probe — the one round trip that turns "RLS silently
returned nothing" into an immediate error — and it is only worth anything if it reads the
GUC back the way Postgres actually reports it, which is NULL before the first set and ''
after a transaction ends. `assert_app_role` is the startup guard that refuses to serve as
a role row-level security cannot bind; it is tested against the admin role, because that
is the mistake a developer actually makes.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from platform_core.config import ServiceSettings
from platform_core.db import assert_app_role, assert_tenant, set_tenant

pytestmark = pytest.mark.integration


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """The migration role. BYPASSRLS — exactly what the guard must refuse."""
    engine = create_async_engine(_Settings(db_admin_role="atmora_admin").admin_dsn)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(admin_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(admin_engine, expire_on_commit=False)() as s:
        yield s


class TestAssertTenant:
    async def test_an_unset_guc_raises(self, session: AsyncSession) -> None:
        async with session.begin():
            with pytest.raises(RuntimeError, match="not bound"):
                await assert_tenant(session, uuid.uuid4())

    async def test_another_organizations_guc_raises(self, session: AsyncSession) -> None:
        async with session.begin():
            await set_tenant(session, uuid.uuid4())
            with pytest.raises(RuntimeError, match="not bound"):
                await assert_tenant(session, uuid.uuid4())

    async def test_the_matching_guc_passes(self, session: AsyncSession) -> None:
        org = uuid.uuid4()
        async with session.begin():
            await set_tenant(session, org)
            await assert_tenant(session, org)

    async def test_the_guc_does_not_survive_into_the_next_transaction(
        self, session: AsyncSession
    ) -> None:
        """The fact the chokepoint's placement rests on (ADR-0002 §4).

        Once the transaction that set it ends, the same session reads the GUC back as ''
        — so a tenant set once per request would leave every later transaction unbound.
        """
        org = uuid.uuid4()
        async with session.begin():
            await set_tenant(session, org)
        async with session.begin():
            with pytest.raises(RuntimeError, match="not bound"):
                await assert_tenant(session, org)


class TestAssertAppRole:
    async def test_a_bypassrls_role_is_refused_outside_local(
        self, admin_engine: AsyncEngine
    ) -> None:
        with pytest.raises(RuntimeError, match="rolbypassrls=True"):
            await assert_app_role(admin_engine, environment="ci")

    async def test_a_bypassrls_role_is_only_warned_about_in_local(
        self, admin_engine: AsyncEngine
    ) -> None:
        """A developer on the wrong DSN should find out without being locked out."""
        with structlog.testing.capture_logs() as logs:
            await assert_app_role(admin_engine, environment="local")
        assert any(entry["event"] == "db.role.bypasses_rls" for entry in logs)
