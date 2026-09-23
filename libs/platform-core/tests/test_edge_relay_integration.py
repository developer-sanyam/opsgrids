"""The edge relay against a real Postgres: two schemas, one shared pool, one process.

Everything else in `test_edge_relay.py` fakes `OutboxRelay` itself; this file is the one
place that proves the real thing — `_make_outbox_model`'s generated class, `run_forever`'s
one shared engine, `assert_outbox_role`'s startup guard, `OutboxRelay._drain`'s
`FOR UPDATE SKIP LOCKED` query — actually reads two different services' schemas correctly.
Only the broker is faked, the same way `test_outbox_relay_idle_backoff.py` fakes it: no
`make infra` RabbitMQ container is required to prove the database half of this, and a
missing broker would otherwise make this test about the broker rather than the relay.

Needs `make infra`'s Postgres (`opsgrids_admin` and `opsgrids_outbox` roles, `account` and
`lead` schemas, both migrated) reachable at `DATABASE_URL`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import aio_pika
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from platform_core.config import ServiceSettings
from platform_core.edge_relay import EdgeRelaySettings, _make_outbox_model, run_forever

pytestmark = pytest.mark.integration

SCHEMAS = ("account", "lead")


class _AdminSettings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


def _admin_dsn() -> str:
    return _AdminSettings(db_admin_role="opsgrids_admin").admin_dsn


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """Inserts the rows the relay is meant to find — the migration role, BYPASSRLS, owns
    every schema, exactly like the fixture `test_tenancy.py` uses for the same reason."""
    engine = create_async_engine(_admin_dsn())
    yield engine
    await engine.dispose()


class _CapturingExchange:
    """Stands in for the broker: every schema's `OutboxRelay` publishes into this one
    shared list, so the test can tell which schema's row produced which envelope."""

    def __init__(self, published: list[dict[str, Any]]) -> None:
        self._published = published

    async def publish(self, message: aio_pika.Message, *, routing_key: str) -> None:
        envelope = json.loads(message.body)
        self._published.append(envelope)


class _FakeChannel:
    def __init__(self, published: list[dict[str, Any]]) -> None:
        self._published = published

    async def declare_exchange(self, *args: Any, **kwargs: Any) -> _CapturingExchange:
        return _CapturingExchange(self._published)


class _FakeConnection:
    def __init__(self, published: list[dict[str, Any]]) -> None:
        self._published = published

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def channel(
        self, *, publisher_confirms: bool = True, on_return_raises: bool = False
    ) -> _FakeChannel:
        return _FakeChannel(self._published)


async def _insert_outbox_row(
    admin_engine: AsyncEngine, schema: str, *, organization_id: uuid.UUID, aggregate_id: uuid.UUID
) -> uuid.UUID:
    cls = _make_outbox_model(schema)
    sessionmaker = async_sessionmaker(admin_engine, expire_on_commit=False, class_=AsyncSession)
    async with sessionmaker() as session, session.begin():
        row = cls(
            organization_id=organization_id,
            event_type="edge_relay.integration_test",
            aggregate_type="probe",
            aggregate_id=aggregate_id,
            payload={"schema": schema},
        )
        session.add(row)
        await session.flush()
        return row.id  # type: ignore[no-any-return]


def _ours(published: list[dict[str, Any]], organization_id: uuid.UUID) -> list[dict[str, Any]]:
    """The envelopes this test's own rows produced.

    These are real, shared outbox tables: another service's test run may have left unpublished
    rows in them, and the relay — correctly — drains those too. Judging every envelope it
    published made this test fail on a row it never wrote.
    """
    return [entry for entry in published if entry.get("organization_id") == str(organization_id)]


async def _delete_outbox_row(admin_engine: AsyncEngine, schema: str, event_id: uuid.UUID) -> None:
    # Table identifiers cannot be bound parameters; `schema` is always one of the
    # hardcoded `SCHEMAS` above, never external input.
    statement = f'DELETE FROM "{schema}"."outbox" WHERE id = :id'  # noqa: S608
    async with admin_engine.begin() as conn:
        await conn.execute(text(statement), {"id": str(event_id)})


class TestEdgeRelayAgainstRealSchemas:
    async def test_two_schemas_each_publish_through_their_own_producer(
        self, admin_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        org_id = uuid.uuid4()
        row_ids: dict[str, uuid.UUID] = {}
        for schema in SCHEMAS:
            row_ids[schema] = await _insert_outbox_row(
                admin_engine, schema, organization_id=org_id, aggregate_id=uuid.uuid4()
            )

        published: list[dict[str, Any]] = []

        async def _fake_connect_robust(url: str) -> _FakeConnection:
            return _FakeConnection(published)

        monkeypatch.setattr("platform_core.events.aio_pika.connect_robust", _fake_connect_robust)

        settings = EdgeRelaySettings(
            edge_relay_schemas="account,lead:Lead-Service",
            edge_relay_poll_interval_seconds=0.05,
            edge_relay_max_idle_interval_seconds=0.1,
        )
        engine = create_async_engine(settings.outbox_dsn, pool_size=5, max_overflow=5)

        task = asyncio.create_task(run_forever(settings, engine=engine))
        try:
            for _ in range(250):  # ~5s ceiling
                if len(_ours(published, org_id)) >= 2:
                    break
                await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await engine.dispose()
            for schema, row_id in row_ids.items():
                await _delete_outbox_row(admin_engine, schema, row_id)

        by_schema = {entry["payload"]["schema"]: entry for entry in _ours(published, org_id)}
        assert set(by_schema) == set(SCHEMAS)
        assert by_schema["account"]["producer"] == "account"
        assert by_schema["lead"]["producer"] == "Lead-Service"
        assert by_schema["account"]["organization_id"] == str(org_id)
        assert by_schema["lead"]["organization_id"] == str(org_id)

    async def test_a_schema_with_no_migrated_outbox_table_does_not_block_the_others(
        self, admin_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`billing_probe_missing` names no real schema — the relay for it must log the
        slow missing-table path and keep polling, while `account`'s relay still drains."""
        org_id = uuid.uuid4()
        row_id = await _insert_outbox_row(
            admin_engine, "account", organization_id=org_id, aggregate_id=uuid.uuid4()
        )

        published: list[dict[str, Any]] = []

        async def _fake_connect_robust(url: str) -> _FakeConnection:
            return _FakeConnection(published)

        monkeypatch.setattr("platform_core.events.aio_pika.connect_robust", _fake_connect_robust)

        settings = EdgeRelaySettings(
            edge_relay_schemas="account,does_not_exist_schema",
            edge_relay_poll_interval_seconds=0.05,
            edge_relay_max_idle_interval_seconds=0.1,
            edge_relay_missing_table_retry_seconds=60.0,
        )
        engine = create_async_engine(settings.outbox_dsn, pool_size=5, max_overflow=5)

        task = asyncio.create_task(run_forever(settings, engine=engine))
        try:
            for _ in range(100):
                if published:
                    break
                await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await engine.dispose()
            await _delete_outbox_row(admin_engine, "account", row_id)

        assert len(published) == 1
        assert published[0]["payload"]["schema"] == "account"
