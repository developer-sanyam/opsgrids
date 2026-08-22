"""The relay and the outbox-lag probe see every tenant's rows; the app role sees none.

Row-level security on `outbox` made two things blind at once. The relay's query exists to
discover which organizations have work, so it binds no tenant — and as the app role that now
returns nothing: `attempts` never increments, nothing logs, the outbox grows without bound
(ADR-0002, "Relay stall"). `/health/outbox`, the probe built to make that stall visible, read
through the same engine and reported `pending: 0` for the same reason.

Both now run as `atmora_outbox`: BYPASSRLS, and SELECT and UPDATE on `outbox` only. These
tests pin both halves — that the role can see and mark rows across tenants, and that it can
do nothing else. The app-role case is kept deliberately: it is the defect, recorded so it
stays recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import SCHEMA, Organization, OutboxEvent
from app.schemas import OrganizationIn
from platform_core.app import create_app
from platform_core.events import OutboxRelay, publish_event

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@asynccontextmanager
async def _sessionmaker(dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker on `dsn` with **no tenant binding** — what a relay process holds."""
    engine = create_async_engine(dsn, connect_args=_connect_args())
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class _FakeExchange:
    """Stands in for the aio_pika exchange: records what the relay would have sent."""

    def __init__(self) -> None:
        self.published: list[tuple[Any, str]] = []

    async def publish(self, message: Any, *, routing_key: str) -> None:
        self.published.append((message, routing_key))


class _DownExchange:
    """A broker that refuses everything, so the failure path leaves its evidence."""

    async def publish(self, message: Any, *, routing_key: str) -> None:
        raise ConnectionError("broker unreachable")


def _relay(maker: async_sessionmaker[AsyncSession]) -> OutboxRelay:
    return OutboxRelay(
        amqp_url="amqp://unused",
        outbox_cls=OutboxEvent,
        session_factory=maker,
        producer="account",
    )


@pytest_asyncio.fixture
async def two_tenants_pending(
    org: Organization, admin_session: AsyncSession
) -> dict[str, uuid.UUID]:
    """Exactly one unpublished event per organization, for two organizations.

    Seeded as the admin role, which is the only one that may both create an organization and
    stage an event for it without a tenant binding. The rows that creating the organizations
    staged are cleared first, so the counts below are exact rather than incidental.
    """
    async with admin_session.begin():
        other = await services.create_organization(
            admin_session,
            OrganizationIn(name="Beta Corp", slug="beta"),
            actor_user_id=None,
        )
        # Flush first: a Core DELETE does not autoflush, so without this the event the second
        # organization's creation staged would land after the sweep and make three rows.
        await admin_session.flush()
        await admin_session.execute(text("DELETE FROM account.outbox"))
        for organization in (org, other):
            publish_event(
                admin_session,
                OutboxEvent,
                organization_id=organization.id,
                event_type="test.outbox.seeded",
                aggregate_type="organization",
                aggregate_id=organization.id,
                payload={"slug": organization.slug},
            )
    return {"acme": org.id, "beta": other.id}


async def _all_rows(admin_session: AsyncSession) -> list[OutboxEvent]:
    async with admin_session.begin():
        return list((await admin_session.execute(select(OutboxEvent))).scalars().all())


class TestWhoCanSeeTheOutbox:
    async def test_the_outbox_role_sees_every_tenant_unbound(
        self, two_tenants_pending: dict[str, uuid.UUID]
    ) -> None:
        """The relay's query, as the relay runs it: no tenant, every organization."""
        async with _sessionmaker(settings().outbox_dsn) as maker, maker() as s, s.begin():
            seen = set((await s.execute(select(OutboxEvent.organization_id))).scalars())
        assert seen == set(two_tenants_pending.values())

    async def test_the_app_role_sees_nothing_unbound(
        self, two_tenants_pending: dict[str, uuid.UUID]
    ) -> None:
        """The defect. Not a failure to fix — the property the app role is supposed to have.

        A relay built on this session drains nothing, forever, without an error. That is why
        the relay does not get this role.
        """
        async with _sessionmaker(settings().app_dsn) as maker, maker() as s, s.begin():
            seen = (await s.execute(select(OutboxEvent.organization_id))).scalars().all()
        assert seen == []


class TestTheOutboxRoleCanDoNothingElse:
    """Least privilege inside the bypass — the same argument as the directory reader."""

    async def test_it_bypasses_rls_and_the_app_role_does_not(self) -> None:
        probe = text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        async with _sessionmaker(settings().outbox_dsn) as maker, maker() as s:
            assert (await s.execute(probe)).scalar_one() is True
        async with _sessionmaker(settings().app_dsn) as maker, maker() as s:
            assert (await s.execute(probe)).scalar_one() is False

    async def test_it_cannot_insert(self, org: Organization) -> None:
        """Only a request transaction stages an event, alongside the change it describes."""
        async with _sessionmaker(settings().outbox_dsn) as maker, maker() as s:
            with pytest.raises(Exception, match="permission denied"):
                async with s.begin():
                    publish_event(
                        s,
                        OutboxEvent,
                        organization_id=org.id,
                        event_type="test.outbox.forged",
                        aggregate_type="organization",
                        aggregate_id=org.id,
                        payload={},
                    )
                    await s.flush()

    async def test_it_cannot_delete(self, two_tenants_pending: dict[str, uuid.UUID]) -> None:
        """The outbox is the record of what was sent: the relay marks rows, never removes them."""
        async with _sessionmaker(settings().outbox_dsn) as maker, maker() as s:
            with pytest.raises(Exception, match="permission denied"):
                await s.execute(text("DELETE FROM account.outbox"))

    @pytest.mark.parametrize(
        "query", ["SELECT 1 FROM account.users", "SELECT 1 FROM account.memberships"]
    )
    async def test_it_cannot_read_the_directory(self, query: str) -> None:
        """A BYPASSRLS role that could read identity tables would be a second directory."""
        async with _sessionmaker(settings().outbox_dsn) as maker, maker() as s:
            with pytest.raises(Exception, match="permission denied"):
                await s.execute(text(query))


class TestTheRelayDrains:
    async def test_as_the_outbox_role_every_tenant_is_published_and_marked(
        self, two_tenants_pending: dict[str, uuid.UUID], admin_session: AsyncSession
    ) -> None:
        """One drain, two organizations, both rows sent and stamped."""
        exchange = _FakeExchange()
        async with _sessionmaker(settings().outbox_dsn) as maker:
            drained = await _relay(maker)._drain(exchange)

        assert drained == 2
        assert [key for _, key in exchange.published] == ["test.outbox.seeded"] * 2

        rows = await _all_rows(admin_session)
        assert {row.organization_id for row in rows} == set(two_tenants_pending.values())
        assert all(row.published_at is not None for row in rows)

    async def test_as_the_outbox_role_a_broker_failure_is_counted_on_every_tenant_row(
        self, two_tenants_pending: dict[str, uuid.UUID], admin_session: AsyncSession
    ) -> None:
        """`attempts` is the only trace a live relay leaves; it has to move for every tenant."""
        async with _sessionmaker(settings().outbox_dsn) as maker:
            await _relay(maker)._drain(_DownExchange())

        rows = await _all_rows(admin_session)
        assert len(rows) == 2
        assert all(row.attempts == 1 and row.published_at is None for row in rows)
        assert all(row.last_error == "broker unreachable" for row in rows)

    async def test_as_the_app_role_nothing_is_published_and_nothing_is_marked(
        self, two_tenants_pending: dict[str, uuid.UUID], admin_session: AsyncSession
    ) -> None:
        """The stall, demonstrated: no error, no publish, no mark, no evidence."""
        exchange = _FakeExchange()
        async with _sessionmaker(settings().app_dsn) as maker:
            drained = await _relay(maker)._drain(exchange)

        assert drained == 0
        assert exchange.published == []

        rows = await _all_rows(admin_session)
        assert len(rows) == 2
        assert all(row.published_at is None and row.attempts == 0 for row in rows)


class TestTheProbeCounts:
    async def test_health_outbox_reports_every_tenants_backlog(
        self, two_tenants_pending: dict[str, uuid.UUID]
    ) -> None:
        """The probe exists to expose the stall, so it cannot share the stall's blind spot.

        Built through `create_app` and run through its real lifespan, because that is where
        the outbox engine is opened: a test that called the route with hand-initialised
        engines would pass whatever the lifespan forgot. `ASGITransport` does not run
        lifespan events on its own.
        """
        app = create_app(settings(), title="probe", outbox_model=OutboxEvent)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await client.get("/health/outbox")

        assert response.status_code == 200
        body = response.json()
        assert body["pending"] == 2
        assert body["oldest_seconds"] >= 0
