"""The product relay's role wiring — the service where all three legs were once missing.

Product shipped with the relay draining through `get_sessionmaker()` (the RLS-bound app
engine), no `db_outbox_role` in config, and no outbox grant in its migrations. Three
independent-looking omissions, one combined effect: the drain matched zero rows, nothing
errored, `/health/outbox` counted zero through the same blind engine, and the outbox filled
silently — ADR-0002's "Relay stall", in production shape. Account's suite pins the platform
mechanics in depth; this pins the same properties on the service that actually regressed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import SCHEMA, OutboxEvent
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


def _relay(maker: async_sessionmaker[AsyncSession]) -> OutboxRelay:
    return OutboxRelay(
        amqp_url="amqp://unused",
        outbox_cls=OutboxEvent,
        session_factory=maker,
        producer="product",
    )


async def _seed_two_tenants(admin_session: AsyncSession) -> set[uuid.UUID]:
    """One unpublished event for each of two organizations, and nothing else."""
    orgs = {uuid.uuid4(), uuid.uuid4()}
    async with admin_session.begin():
        await admin_session.execute(text(f"DELETE FROM {SCHEMA}.outbox"))  # noqa: S608 — SCHEMA is the module constant
        for org in orgs:
            publish_event(
                admin_session,
                OutboxEvent,
                organization_id=org,
                event_type="test.outbox.seeded",
                aggregate_type="activation",
                aggregate_id=org,
                payload={},
            )
    return orgs


class TestTheProductRelayRoleWiring:
    def test_config_names_the_outbox_role(self) -> None:
        """The leg whose absence made `get_outbox_sessionmaker` fall back to the stall."""
        assert settings().db_outbox_role == "opsgrids_outbox"
        assert "opsgrids_outbox" in settings().outbox_dsn

    async def test_the_relay_drains_every_tenant_as_the_outbox_role(
        self, admin_session: AsyncSession
    ) -> None:
        orgs = await _seed_two_tenants(admin_session)
        exchange = _FakeExchange()
        async with _sessionmaker(settings().outbox_dsn) as maker:
            published, failed = await _relay(maker)._drain(exchange)

        assert (published, failed) == (2, 0)

        async with admin_session.begin():
            rows = list((await admin_session.execute(select(OutboxEvent))).scalars())
        assert {row.organization_id for row in rows} == orgs
        assert all(row.published_at is not None for row in rows)

    async def test_as_the_app_role_the_stall_is_still_the_stall(
        self, admin_session: AsyncSession
    ) -> None:
        """The defect, kept demonstrable: this is why the relay must never get the app role."""
        await _seed_two_tenants(admin_session)
        exchange = _FakeExchange()
        async with _sessionmaker(settings().app_dsn) as maker:
            published, failed = await _relay(maker)._drain(exchange)

        assert (published, failed) == (0, 0)
        assert exchange.published == []

        async with admin_session.begin():
            rows = list((await admin_session.execute(select(OutboxEvent))).scalars())
        assert all(row.published_at is None and row.attempts == 0 for row in rows)
