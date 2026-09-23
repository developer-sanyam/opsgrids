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
from pathlib import Path
from typing import Any, Final

import aio_pika
from sqlalchemy import DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("events")

EXCHANGE: Final[str] = "opsgrids.events"
DLX: Final[str] = "opsgrids.events.dlx"
ENVELOPE_VERSION: Final[int] = 1

#: The delayed-delivery primitive (ADR-0015). A producer stages this on its **own** outbox
#: instead of the event it actually wants delivered; `scheduler-service` consumes it, holds it in
#: `scheduled_jobs`, and republishes the *target* event — unchanged, on its own outbox, with the
#: target's own `event_type` as the routing key — the moment `due_at` arrives. The producer's own
#: queue binding (already bound to that routing key, because the event is its own) receives it
#: exactly as if the producer had published it directly. See `schedule_event` below.
PLATFORM_SCHEDULE_REQUESTED: Final[str] = "platform.schedule.requested"


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
    """Stage an event **inside the caller's transaction**. Does not commit.

    `id` is generated here rather than left to `OutboxMixin`'s own column default: that default
    is a flush-time default, so the id it would eventually get is not yet on the Python object
    when this function returns it to the caller — every existing caller has discarded the return
    value, which is exactly how that went unnoticed, but `scheduler`'s `fire_job` (ADR-0015)
    needs a real id immediately, to carry as the fired event's own `causation_id`.
    """
    row = outbox_cls(
        id=uuid.uuid4(),
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


def schedule_event(
    session: AsyncSession,
    outbox_cls: type[Any],
    *,
    organization_id: uuid.UUID,
    due_at: datetime,
    idempotency_key: str,
    target_event_type: str,
    target_aggregate_type: str,
    target_aggregate_id: uuid.UUID,
    target_payload: dict[str, Any],
    target_actor: dict[str, Any] | None = None,
    causation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Ask the scheduler to deliver an event later instead of publishing it now (ADR-0015).

    Stages **`PLATFORM_SCHEDULE_REQUESTED`** on the caller's own outbox — never a call to the
    scheduler service, which has no HTTP surface for this at all. Whether "later" is warranted at
    all is the caller's own policy, not this function's: it always schedules, on whatever `due_at`
    it is given. Finance's `request_filing` is the first caller, and it only reaches here once it
    has already decided `not_before` is in the future; a `due_at` in the past is still honoured
    correctly (the scheduler fires anything already due on its very next sweep), just pointlessly
    slower than calling `publish_event` directly would have been.

    `idempotency_key` is the domain-level guard, checked again by the scheduler's own consumer
    against a unique `(organization_id, idempotency_key)` constraint — event-id idempotency alone
    is not enough once two distinct events can reasonably ask for the same delayed delivery (a
    redelivered request, or a caller that computes the same key twice by design, as the early-
    arrival defence in `finance/app/consumers.py` deliberately does not).
    """
    return publish_event(
        session,
        outbox_cls,
        organization_id=organization_id,
        event_type=PLATFORM_SCHEDULE_REQUESTED,
        aggregate_type=target_aggregate_type,
        aggregate_id=target_aggregate_id,
        payload={
            "idempotency_key": idempotency_key,
            "due_at": due_at.astimezone(UTC).isoformat(),
            "target": {
                "event_type": target_event_type,
                "aggregate_type": target_aggregate_type,
                "aggregate_id": str(target_aggregate_id),
                "payload": target_payload,
                "actor": target_actor,
            },
        },
        causation_id=causation_id,
        actor=target_actor,
    )


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
        "schema_uri": f"opsgrids://contracts/{row.event_type}/v{row.event_version}",
        "payload": row.payload,
    }


def event_schema_path(event_type: str, version: int, *, contracts_root: Path) -> Path:
    """Where `build_envelope`'s `schema_uri` for `(event_type, version)` resolves on disk.

    `opsgrids://contracts/{event_type}/v{version}` names a schema, not a file, so this is the
    one place that URI is turned into a path — every publisher's schema lives at
    `libs/contracts/events/<event_type>/v<version>.schema.json`, and this function is what a
    test asks instead of building that string itself and drifting from `build_envelope`.
    """
    return contracts_root / "events" / event_type / f"v{version}.schema.json"


def validate_event_schema(
    event_type: str, version: int, payload: dict[str, Any], *, contracts_root: Path
) -> None:
    """Raise if `payload` does not match the committed JSON Schema for this event.

    Dev/test-time only: nothing on the publish path (`publish_event`, the relay) calls this —
    `jsonschema` is a dev dependency, not a runtime one, imported here rather than at module
    level so importing `platform_core.events` in a service that never validates a contract never
    requires it to be installed. The intended caller is a service's own test suite, building a
    real event through the real function that publishes it and checking the payload that
    produced against the schema committed for it (`libs/contracts/events/...`), the same way
    `tools/export_openapi.py --check` catches an OpenAPI document that fell behind its code.

    Raises `FileNotFoundError` for an event type or version with no committed schema — a
    publisher that shipped ahead of its contract — and `jsonschema.ValidationError` for a
    payload that does not match one that exists.
    """
    from jsonschema import Draft202012Validator, FormatChecker

    path = event_schema_path(event_type, version, contracts_root=contracts_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"No committed schema for {event_type} v{version} at {path} — add one under "
            "libs/contracts/events/."
        )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


class OutboxRelay:
    """Drains one service's outbox to the broker.

    Runs as its own process so a slow broker cannot block an API request. Claims rows with
    ``FOR UPDATE SKIP LOCKED``, so several replicas can drain the same table safely.

    **This process must not be subject to row-level security** — it deliberately handles
    every organization's events in one loop. Run it as `opsgrids_outbox` (BYPASSRLS, SELECT and
    UPDATE on `outbox` only), through `get_outbox_sessionmaker`; as the app role it selects
    nothing, logs nothing and loops while the outbox fills.

    An idle relay backs off: production runs many of these processes on a small box, and a
    quiet platform must not turn into a constant stream of empty `SELECT`s. Each empty pass
    doubles the sleep, capped at ``max_idle_interval``, and any pass that publishes at least
    one row resets it to ``poll_interval`` so a backlog still drains at full speed. The price
    is latency: the first event after a quiet period can wait up to ``max_idle_interval``
    before the next poll picks it up. That is the trade for not issuing dozens of idle
    queries a second.
    """

    #: Event types that must never be silently dropped for want of a bound queue (ADR-0015
    #: §6). A topic exchange publish with nobody bound to the routing key is, by default, a
    #: message the broker still confirms and the relay still marks `published_at` — it is
    #: gone, and nothing anywhere noticed. That is an acceptable, long-standing trade for
    #: ordinary events (a consumer that has not deployed yet loses events it was never going
    #: to see anyway, until it does deploy and the queue exists). It is not acceptable for
    #: `platform.schedule.requested`: the whole point of asking the scheduler to hold
    #: something is that it must actually arrive there.
    _GUARANTEED_ROUTING_EVENT_TYPES: Final[frozenset[str]] = frozenset(
        {PLATFORM_SCHEDULE_REQUESTED}
    )

    def __init__(
        self,
        *,
        amqp_url: str,
        outbox_cls: type[Any],
        session_factory: async_sessionmaker[AsyncSession],
        producer: str,
        batch_size: int = 100,
        poll_interval: float = 1.0,
        max_attempts: int = 50,
        max_backoff: float = 60.0,
        max_idle_interval: float = 5.0,
        guaranteed_routing_event_types: frozenset[str] | None = None,
    ) -> None:
        self._amqp_url = amqp_url
        self._outbox_cls = outbox_cls
        self._session_factory = session_factory
        self._producer = producer
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._max_backoff = max_backoff
        self._max_idle_interval = max_idle_interval
        self._guaranteed_routing_event_types = (
            guaranteed_routing_event_types
            if guaranteed_routing_event_types is not None
            else self._GUARANTEED_ROUTING_EVENT_TYPES
        )
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
            # A second channel, used only for `_guaranteed_routing_event_types`.
            # `on_return_raises=True` turns an unroutable publish on *this* channel into an
            # exception `_drain` already treats as a failed publish (attempts bumped,
            # retried next pass) instead of the ordinary channel's silent confirm-and-drop.
            # Scoped to its own channel and a small, named set of event types on purpose:
            # every other event keeps today's behaviour, completely unexamined here — this
            # is not a platform-wide policy change, just a guarantee for the one event type
            # that needs one.
            guaranteed_channel = await connection.channel(
                publisher_confirms=True, on_return_raises=True
            )
            guaranteed_exchange = await guaranteed_channel.declare_exchange(
                EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
            )
            log.info("outbox.relay.started", producer=self._producer)
            backoff = self._poll_interval
            # Below poll_interval, max() clamps this to poll_interval every pass — i.e. no
            # idle backoff at all, which is how a caller opts back into the old behaviour.
            idle_cap = max(self._max_idle_interval, self._poll_interval)
            idle = self._poll_interval
            while not self._stop:
                published, failed = await self._drain(exchange, guaranteed_exchange)
                if failed:
                    # A struggling broker must not be retried in a tight loop. Each pass
                    # burns one of every failing row's attempts, so without spacing a
                    # seconds-long outage used to exhaust all of them and abandon the rows
                    # for good. Exponential, capped, and reset by the first clean pass.
                    # Independent of the idle backoff below: a failing pass is not an empty
                    # one, so it neither grows nor resets `idle`.
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._max_backoff)
                    continue
                backoff = self._poll_interval
                if published == 0:
                    # Exponential idle backoff: doubles each consecutive empty pass, capped
                    # at idle_cap. A row published resets it below without touching this
                    # branch, so the next quiet spell starts cold at poll_interval again.
                    await asyncio.sleep(idle)
                    idle = min(idle * 2, idle_cap)
                else:
                    idle = self._poll_interval

    async def _drain(
        self,
        exchange: aio_pika.abc.AbstractExchange,
        guaranteed_exchange: aio_pika.abc.AbstractExchange | None = None,
    ) -> tuple[int, int]:
        """One pass over the claimable backlog. Returns ``(published, failed)``.

        Failures are counted rather than folded into the batch size, because the run loop
        must know the difference: a full batch of failed publishes used to come back as
        "progress" and drive a sleepless retry loop.

        ``guaranteed_exchange`` defaults to ``exchange`` — every existing single-exchange
        caller (a service's own `bin/relay`, and every test that calls this directly) keeps
        publishing everything through the one exchange it passed, unchanged. `run()` is the
        only caller that ever passes two: a row whose `event_type` is one of
        `_guaranteed_routing_event_types` goes out on the strict one instead.
        """
        target_exchange = guaranteed_exchange if guaranteed_exchange is not None else exchange
        cls: Any = self._outbox_cls
        published = failed = 0
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
                publish_via = (
                    target_exchange
                    if row.event_type in self._guaranteed_routing_event_types
                    else exchange
                )
                try:
                    await publish_via.publish(
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
                    published += 1
                except Exception as exc:
                    failed += 1
                    row.attempts += 1
                    row.last_error = str(exc)[:500]
                    if row.attempts >= self._max_attempts:
                        # From here the drain query skips the row: it will never be sent
                        # without an operator resetting `attempts` (tools/outbox.py). That
                        # is data standing still, so it is an error, not a warning.
                        log.error(
                            "outbox.abandoned",
                            event_id=str(row.id),
                            event_type=row.event_type,
                            attempts=row.attempts,
                            error=str(exc)[:200],
                        )
                    else:
                        log.warning(
                            "outbox.publish.failed",
                            event_id=str(row.id),
                            attempts=row.attempts,
                            error=str(exc)[:200],
                        )
            return published, failed


Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventConsumer:
    """Binds handlers to routing keys and runs them with a dead-letter queue.

    A handler that raises gets the message redelivered **once** — a transient failure (a
    database restart, a deadlock, a connection blip) must not cost the event — and a second
    failure dead-letters it rather than retrying forever. The DLQ is not a graveyard: depth
    is visible on it as a queue metric, and `tools/dlq.py` inspects and replays it. A
    dead-lettered event in a money path is an incident, not a log line.
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
            if not message.redelivered:
                # First failure: put it back. Handlers are idempotent by contract, so a
                # retry is free — and most of what raises here (a DB restart, a deadlock,
                # a pool blip) is gone by the time the message comes around again.
                log.warning(
                    "consumer.handler.retrying",
                    queue=self._queue_name,
                    event_type=envelope.get("event_type"),
                    event_id=envelope.get("event_id"),
                    error=str(exc)[:300],
                )
                await message.nack(requeue=True)
            else:
                log.error(
                    "consumer.handler.dead_lettered",
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
