"""Race-safe numbering: incident and service-request numbers.

Written against real PostgreSQL, like the rest of the suite — the advisory lock these guard is
database behaviour, and proving it needs two independent connections genuinely racing each
other, which only a real server can arbitrate. Before the fix, both allocators read "max existing
suffix, plus one" with no lock: two concurrent callers could read the same max, compute the same
next value, and collide on the table's unique constraint as an unhandled 500 instead of landing
as two consecutive, distinct values. Mirrors `services/support/tests/test_numbering.py`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import services
from app.config import settings
from app.models import SCHEMA
from app.schemas import IncidentIn, ServiceRequestIn
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — the concurrency tests need two real
    sessions racing each other rather than the one shared fixture session."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


class TestConcurrentIncidentNumbers:
    async def test_two_concurrent_raises_never_collide(self, org_id: uuid.UUID, data) -> None:
        """The seeded data already holds INC-4812 and INC-4821; two more incidents raised at
        the same instant must land as two distinct, consecutive numbers."""

        async def _raise(session: AsyncSession, summary: str) -> str:
            write = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
            async with session.begin():
                incident = await services.raise_incident(
                    session,
                    write,
                    IncidentIn(summary=summary, requester="Race Tester"),
                )
                return incident.number

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            n1, n2 = await asyncio.gather(_raise(s1, "First"), _raise(s2, "Second"))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert n1 != n2
        assert {n1, n2} == {"INC-4822", "INC-4823"}


class TestConcurrentRequestNumbers:
    async def test_two_concurrent_raises_never_collide(self, org_id: uuid.UUID, data) -> None:
        """The seeded data already holds REQ-2048 and REQ-2051; two more requests raised at
        the same instant must land as two distinct, consecutive numbers."""

        async def _raise(session: AsyncSession, subject: str) -> str:
            write = ctx_for(org_id, "itsm.service.request.write", Scope.OWN)
            async with session.begin():
                req = await services.raise_request(
                    session,
                    write,
                    ServiceRequestIn(
                        catalog_item_id="CAT-01", subject=subject, requester="Race Tester"
                    ),
                )
                return req.number

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            n1, n2 = await asyncio.gather(_raise(s1, "First"), _raise(s2, "Second"))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert n1 != n2
        assert {n1, n2} == {"REQ-2052", "REQ-2053"}
