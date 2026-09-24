"""`platform.schedule.requested` must never be silently dropped when nobody is bound to its
routing key yet (ADR-0015 §6, review finding).

A topic exchange publish with no queue bound to the routing key is, by default, still
confirmed by the broker — the message is gone, and `OutboxRelay._drain` would mark it
`published_at` regardless. That is an accepted, longstanding trade for ordinary events (a
consumer that has not deployed yet loses events it was never going to see, until it deploys
and the queue exists). It is not acceptable for a schedule request: the whole point of asking
the scheduler to hold something is that it must actually arrive there.

This is the one place in the suite that needs a **real** broker rather than a fake exchange:
the guarantee is specifically about a real `basic.return` frame, which nothing else here
models. Needs `make infra`'s Postgres (`opsgrids_admin`/`opsgrids_outbox` roles, `scheduler`
schema migrated) and RabbitMQ reachable at `DATABASE_URL`/`RABBITMQ_URL`.

`scheduler.outbox` is a real, shared table — other services' own test suites use it too, and
this file does not truncate it (truncation is each service's own fixture, and platform-core
owns no schema to truncate). Every assertion below is therefore scoped to the one row this
test itself inserted and reads back by id, never to `_drain`'s own aggregate
`(published, failed)` counts, which reflect whatever else happens to be claimable in the same
pass. Each test deletes its own row in a `finally`, so a re-run starts clean regardless of
whether the previous run passed.
"""

from __future__ import annotations

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
from platform_core.edge_relay import _make_outbox_model
from platform_core.events import EXCHANGE, PLATFORM_SCHEDULE_REQUESTED, OutboxRelay

pytestmark = pytest.mark.integration

SCHEMA = "scheduler"


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


def _admin_dsn() -> str:
    return _Settings(db_admin_role="opsgrids_admin").admin_dsn


def _outbox_dsn() -> str:
    return _Settings(db_outbox_role="opsgrids_outbox").outbox_dsn


def _rabbitmq_url() -> str:
    return _Settings().rabbitmq_url


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_admin_dsn())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def outbox_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_outbox_dsn())
    yield engine
    await engine.dispose()


async def _insert_row(engine: AsyncEngine, *, event_type: str) -> uuid.UUID:
    cls = _make_outbox_model(SCHEMA)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session, session.begin():
        row = cls(
            organization_id=uuid.uuid4(),
            event_type=event_type,
            aggregate_type="probe",
            aggregate_id=uuid.uuid4(),
            payload={"probe": "guaranteed-routing"},
        )
        session.add(row)
        await session.flush()
        return row.id  # type: ignore[no-any-return]


async def _fetch_row(engine: AsyncEngine, row_id: uuid.UUID) -> Any:
    cls = _make_outbox_model(SCHEMA)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session, session.begin():
        return await session.get(cls, row_id)


async def _delete_row(engine: AsyncEngine, row_id: uuid.UUID) -> None:
    statement = f'DELETE FROM "{SCHEMA}"."outbox" WHERE id = :id'  # noqa: S608
    async with engine.begin() as conn:
        await conn.execute(text(statement), {"id": str(row_id)})


def _relay(outbox_engine: AsyncEngine) -> OutboxRelay:
    return OutboxRelay(
        amqp_url=_rabbitmq_url(),
        outbox_cls=_make_outbox_model(SCHEMA),
        session_factory=async_sessionmaker(
            outbox_engine, expire_on_commit=False, class_=AsyncSession
        ),
        producer="scheduler",
    )


class TestGuaranteedRoutingForScheduleRequests:
    async def test_an_unrouted_schedule_request_is_retried_not_silently_dropped(
        self, admin_engine: AsyncEngine, outbox_engine: AsyncEngine
    ) -> None:
        row_id = await _insert_row(admin_engine, event_type=PLATFORM_SCHEDULE_REQUESTED)
        try:
            connection = await aio_pika.connect_robust(_rabbitmq_url())
            try:
                channel = await connection.channel(publisher_confirms=True)
                exchange = await channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
                guaranteed_channel = await connection.channel(
                    publisher_confirms=True, on_return_raises=True
                )
                guaranteed_exchange = await guaranteed_channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )

                # Nothing is bound to `platform.schedule.requested` on this exchange right
                # now — the ordinary shape of "the scheduler has not been deployed yet".
                # Without the guaranteed-routing channel this row would come back published.
                await _relay(outbox_engine)._drain(exchange, guaranteed_exchange)
            finally:
                await connection.close()

            row = await _fetch_row(admin_engine, row_id)
            assert row is not None
            assert row.published_at is None
            assert row.attempts == 1
            assert row.last_error
        finally:
            await _delete_row(admin_engine, row_id)

    async def test_once_a_queue_is_bound_the_same_row_is_delivered_on_retry(
        self, admin_engine: AsyncEngine, outbox_engine: AsyncEngine
    ) -> None:
        row_id = await _insert_row(admin_engine, event_type=PLATFORM_SCHEDULE_REQUESTED)
        try:
            connection = await aio_pika.connect_robust(_rabbitmq_url())
            try:
                channel = await connection.channel(publisher_confirms=True)
                exchange = await channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
                # `exclusive=True`: RabbitMQ drops this queue the moment `connection.close()`
                # runs below, unbound messages and all — no separate `queue.delete()` needed,
                # and nothing this test leaves behind for the next run to trip over.
                queue = await channel.declare_queue(exclusive=True)
                await queue.bind(exchange, routing_key=PLATFORM_SCHEDULE_REQUESTED)

                guaranteed_channel = await connection.channel(
                    publisher_confirms=True, on_return_raises=True
                )
                guaranteed_exchange = await guaranteed_channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )

                await _relay(outbox_engine)._drain(exchange, guaranteed_exchange)
            finally:
                await connection.close()

            row = await _fetch_row(admin_engine, row_id)
            assert row is not None
            assert row.published_at is not None
            assert row.attempts == 0
        finally:
            await _delete_row(admin_engine, row_id)
