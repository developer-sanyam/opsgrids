"""Race-safe numbering: ticket numbers, agent refs and message refs.

Written against real PostgreSQL, like the rest of the suite — the advisory lock these guard is
database behaviour, and proving it needs two independent connections genuinely racing each
other, which only a real server can arbitrate. Before the fix, all three allocators read
"max existing suffix, plus one" with no lock: two concurrent callers could read the same max,
compute the same next value, and collide on the table's unique constraint as an unhandled 500
instead of landing as two consecutive, distinct values.
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

from app import selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import AgentIn, RequesterIn, TicketCreateIn
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


class TestConcurrentTicketNumbers:
    async def test_two_concurrent_opens_never_collide(self, org_id: uuid.UUID, queue) -> None:
        """The seeded queue already holds TK-0001..TK-0003 (`conftest.seed_queue`); two more
        tickets opened at the same instant must land as two distinct, consecutive numbers."""

        async def _open(session: AsyncSession, subject: str) -> str:
            write = ctx_for(org_id, "support.desk.ticket.write", Scope.ORG)
            async with session.begin():
                ticket = await services.create_ticket_from(
                    session,
                    write,
                    TicketCreateIn(
                        subject=subject,
                        requester=RequesterIn(
                            name="Race Tester", email="race@example.com", company="Acme"
                        ),
                        body="Racing the allocator.",
                    ),
                )
                return ticket.number

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            n1, n2 = await asyncio.gather(_open(s1, "First"), _open(s2, "Second"))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert n1 != n2
        assert {n1, n2} == {"TK-0004", "TK-0005"}


class TestConcurrentAgentRefs:
    async def test_two_concurrent_adds_never_collide(self, org_id: uuid.UUID, queue) -> None:
        """The seeded queue already holds ag-01 and ag-02; two more agents added at the same
        instant must land as two distinct, consecutive refs."""

        async def _add(session: AsyncSession, name: str) -> str:
            write = ctx_for(org_id, "support.desk.agent.write", Scope.ORG)
            async with session.begin():
                agent = await services.create_agent(
                    session,
                    write,
                    AgentIn(name=name, email=f"{name.lower()}@example.com"),
                )
                return agent.ref

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            r1, r2 = await asyncio.gather(_add(s1, "Third"), _add(s2, "Fourth"))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert r1 != r2
        assert {r1, r2} == {"ag-03", "ag-04"}


class TestConcurrentMessageRefs:
    async def test_two_concurrent_replies_never_collide(
        self, session: AsyncSession, org_id: uuid.UUID, queue
    ) -> None:
        """Two replies landing on the same ticket at the same instant must land as two
        distinct, consecutive message refs, never both `m3`."""
        async with session.begin():
            ticket = await selectors.get_ticket(session, org_id, "TK-0001")
            assert ticket is not None
            ticket_id = ticket.id

        async def _reply(worker_session: AsyncSession, body: str) -> str:
            write = ctx_for(org_id, "support.desk.ticket.write", Scope.ORG)
            async with worker_session.begin():
                t = await selectors.get_ticket(worker_session, org_id, "TK-0001")
                assert t is not None
                message = await services.add_message(
                    worker_session,
                    write,
                    t,
                    body=body,
                    author_type="agent",
                    author_name="Daniel Okafor",
                )
                return message.ref

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            ref1, ref2 = await asyncio.gather(_reply(s1, "First reply"), _reply(s2, "Second reply"))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert ref1 != ref2
        assert {ref1, ref2} == {"m3", "m4"}

        async with session.begin():
            after = await selectors.get_messages(session, org_id, ticket_id)
        assert len(after) == 4
