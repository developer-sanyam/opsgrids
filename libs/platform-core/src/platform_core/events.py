"""Domain events: transactional outbox, relay, and idempotent consumption.

Publishing is a two-step that cannot come apart. `publish_event` only adds a row to the
caller's transaction, so the event commits with the state change it describes. A separate
relay process then sends it. A crash between the two cannot produce a state change with no
event, or an event with no state change.

Delivery is **at-least-once**, so every consumer is idempotent: it checks
`processed_events` before acting and marks the id inside the same transaction as its side
effects. This is not optional — a redelivered event that increments a counter twice is a
number nobody can explain later.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final

import aio_pika
from sqlalchemy import DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("events")

EXCHANGE: Final[str] = "atmora.events"
DLX: Final[str] = "atmora.events.dlx"
ENVELOPE_VERSION: Final[int] = 1


class OutboxMixin:
    """Mix into a service's Base:  ``class OutboxEvent(Base, OutboxMixin): ...``"""

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=ENVELOPE_VERSION)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedEventMixin:
    """The idempotency ledger. One row per event this service has already handled."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def publish_event(
    session: AsyncSession,
    outbox_cls: type[Any],
    *,
    organization_id: uuid.UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
    causation_id: uuid.UUID | None = None,
    actor: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Stage an event **inside the caller's transaction**. Does not commit."""
    row = outbox_cls(
        organization_id=organization_id,
        event_type=event_type,
        event_version=ENVELOPE_VERSION,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        correlation_id=correlation_id_ctx.get(),
        causation_id=causation_id,
        actor=actor,
    )
    session.add(row)
    return row.id  # type: ignore[no-any-return]


def build_envelope(row: Any, *, producer: str) -> dict[str, Any]:
    """The wire format. Additive-only within a major version."""
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "version": row.event_version,
        "organization_id": str(row.organization_id),
        "aggregate_type": row.aggregate_type,
        "aggregate_id": str(row.aggregate_id),
        "occurred_at": row.created_at.astimezone(UTC).isoformat(),
        "producer": producer,
        "correlation_id": row.correlation_id,
        "causation_id": str(row.causation_id) if row.causation_id else None,
        "actor": row.actor,
        "schema_uri": f"atmora://contracts/{row.event_type}/v{row.event_version}",
        "payload": row.payload,
    }


class OutboxRelay:
    """Drains one service's outbox to the broker.

    Runs as its own process so a slow broker cannot block an API request. Claims rows with
    ``FOR UPDATE SKIP LOCKED``, so several replicas can drain the same table safely.

    **This process must not be subject to row-level security** — it deliberately handles
    every organization's events in one loop. Run it as a role with BYPASSRLS.
    """

    def __init__(
        self,
        *,
        amqp_url: str,
        outbox_cls: type[Any],
        session_factory: async_sessionmaker[AsyncSession],
        producer: str,
        batch_size: int = 100,
        poll_interval: float = 1.0,
        max_attempts: int = 10,
    ) -> None:
        self._amqp_url = amqp_url
        self._outbox_cls = outbox_cls
        self._session_factory = session_factory
        self._producer = producer
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        import asyncio

        connection = await aio_pika.connect_robust(self._amqp_url)
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            exchange = await channel.declare_exchange(
                EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
            )
            log.info("outbox.relay.started", producer=self._producer)
            while not self._stop:
                sent = await self._drain(exchange)
                if sent == 0:
                    await asyncio.sleep(self._poll_interval)

    async def _drain(self, exchange: aio_pika.abc.AbstractExchange) -> int:
        cls: Any = self._outbox_cls
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(cls)
                        .where(cls.published_at.is_(None), cls.attempts < self._max_attempts)
                        .order_by(cls.created_at)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                envelope = build_envelope(row, producer=self._producer)
                try:
                    await exchange.publish(
                        aio_pika.Message(
                            body=json.dumps(envelope, default=str).encode(),
                            content_type="application/json",
                            message_id=str(row.id),
                            correlation_id=row.correlation_id,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=row.event_type,
                    )
                    row.published_at = datetime.now(UTC)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = str(exc)[:500]
                    log.warning(
                        "outbox.publish.failed",
                        event_id=str(row.id),
                        attempts=row.attempts,
                        error=str(exc)[:200],
                    )
            return len(rows)


Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventConsumer:
    """Binds handlers to routing keys and runs them with a dead-letter queue.

    A handler that raises dead-letters the message rather than retrying forever. The DLQ
    is not a graveyard — it needs alerting and a replay path, or it is data loss with
    extra steps.
    """

    def __init__(self, *, amqp_url: str, queue_name: str, prefetch: int = 16) -> None:
        self._amqp_url = amqp_url
        self._queue_name = queue_name
        self._prefetch = prefetch
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(fn)
            return fn

        return decorator

    async def run(self) -> None:
        connection = await aio_pika.connect_robust(self._amqp_url)
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=self._prefetch)
            exchange = await channel.declare_exchange(
                EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
            )
            dlx = await channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
            dlq = await channel.declare_queue(f"{self._queue_name}.dlq", durable=True)
            await dlq.bind(dlx, routing_key="#")

            queue = await channel.declare_queue(
                self._queue_name,
                durable=True,
                arguments={"x-dead-letter-exchange": DLX},
            )
            for routing_key in self._handlers:
                await queue.bind(exchange, routing_key=routing_key)

            log.info(
                "consumer.started",
                queue=self._queue_name,
                bindings=sorted(self._handlers),
            )
            async with queue.iterator() as messages:
                async for message in messages:
                    await self._handle(message)

    async def _handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            envelope = json.loads(message.body)
        except Exception:
            log.error("consumer.undecodable", queue=self._queue_name)
            await message.nack(requeue=False)
            return

        token = correlation_id_ctx.set(envelope.get("correlation_id"))
        try:
            for handler in self._handlers.get(envelope.get("event_type", ""), []):
                await handler(envelope)
            await message.ack()
        except Exception as exc:
            log.error(
                "consumer.handler.failed",
                queue=self._queue_name,
                event_type=envelope.get("event_type"),
                event_id=envelope.get("event_id"),
                error=str(exc)[:300],
            )
            await message.nack(requeue=False)
        finally:
            correlation_id_ctx.reset(token)


async def already_processed(
    session: AsyncSession, processed_cls: type[Any], event_id: uuid.UUID
) -> bool:
    found: Any = await session.get(processed_cls, event_id)
    return found is not None


def mark_processed(
    session: AsyncSession, processed_cls: type[Any], event_id: uuid.UUID, event_type: str
) -> None:
    """Record the id **in the same transaction as the side effects**. Caller commits."""
    session.add(processed_cls(event_id=event_id, event_type=event_type))
