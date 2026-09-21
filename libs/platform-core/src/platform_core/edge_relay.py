"""One process, many schemas' outboxes — the one-box deployment's relay (ADR-0012).

Production runs 23 services on a 2-vCPU/3.8GB VM. Each service's `app/relay.py` is a
correct, self-contained ~35MB process; running all of them at once is what is wrong for
*this* box, not what is wrong with any one of them. That script stays exactly as it is and
stays the default everywhere else — `services/*/bin/relay` still calls `python -m
app.relay`. This module is the edge-only alternative: one process, one shared database
pool, one `OutboxRelay` task per configured schema.

**Isolation is the point.** A service whose migrations never ran, a poison row, or a
broker hiccup must cost that schema's events latency, not take the other 22 schemas down
with it. Each schema's relay runs under its own supervisor loop: a failure is logged and
the relay is rebuilt and restarted with capped exponential backoff; a missing `outbox`
table — the specific, common case of a service that has not been migrated onto this box
yet — is logged once at warning and retried slowly, forever, without ever counting as the
kind of failure that escalates.

**The database pool is the one deliberate difference from a service relay.** Draining 23
schemas from 23 two-connection pools is the memory problem this module exists to fix, so
here there is exactly one `AsyncEngine`, sized for the whole box
(`EDGE_RELAY_POOL_SIZE`/`EDGE_RELAY_MAX_OVERFLOW`, default 5+5), shared by every schema's
relay. That is also why this module does not call `platform_core.db.init_outbox_engine`:
that helper hardcodes a 2+2 pool and binds `search_path` to one schema for the lifetime of
the engine, both wrong for a pool a poller for 23 schemas shares. It still runs as the
exact role a service relay does and fails the exact way a service relay does when that
role is not split — `assert_outbox_role` is reused unchanged, against this module's own
engine, for that parity.

**One AMQP connection per schema, not one shared connection.** `OutboxRelay.run()` owns
its connection for its lifetime (`connect_robust`, then `async with connection:`); sharing
one robust connection with a channel per schema would mean teaching `run()` to not close a
connection it did not open, which is exactly the kind of change to its existing behaviour
this slice avoids. Twenty-three connections from one process to a broker on the same box is
an acceptable cost; `OutboxRelay` itself is untouched.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from pydantic import Field, field_validator
from pydantic_settings import NoDecode
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from platform_core.config import ServiceSettings
from platform_core.db import assert_outbox_role
from platform_core.events import OutboxMixin, OutboxRelay
from platform_core.logging import configure_logging, get_logger
from platform_core.models import NAMING_CONVENTION

log = get_logger("edge_relay")


class _Stoppable(Protocol):
    """What `_graceful_stop` needs from a relay — just `stop()`. `OutboxRelay` satisfies
    it structurally; naming it as a `Protocol` rather than importing `OutboxRelay` into
    every signature that only ever calls `stop()` lets a test's fake relay satisfy it too,
    without pretending to subclass a concrete class it does not otherwise resemble."""

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SchemaSpec:
    """One drained schema and the `producer` its envelopes carry.

    `producer` defaults to `schema` because every service in this platform names its
    schema after its `service_name` today (verified by walking every `services/*/app/
    config.py`). The override exists for the day that stops being true, or for a schema
    that is not a service's primary schema at all.
    """

    schema: str
    producer: str


def parse_edge_relay_schemas(raw: str) -> list[SchemaSpec]:
    """Parse `EDGE_RELAY_SCHEMAS`: comma-separated `schema` or `schema:producer` entries.

    Whitespace around an entry, and around either side of a `:`, is trimmed. Blank entries
    (an empty string, or the gaps a trailing comma leaves) are dropped rather than
    rejected, so `"account,"` and `""` both parse. A schema named more than once is
    rejected outright — a relay silently drains the same table twice under two producer
    names otherwise, which is a worse failure than refusing to start.
    """
    specs: list[SchemaSpec] = []
    seen: set[str] = set()
    for raw_entry in raw.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) > 2:
            raise ValueError(
                f"EDGE_RELAY_SCHEMAS entry {entry!r} has more than one ':' — expected "
                "'schema' or 'schema:producer'"
            )
        schema = parts[0].strip()
        producer = parts[1].strip() if len(parts) == 2 else schema
        if not schema:
            raise ValueError(f"EDGE_RELAY_SCHEMAS entry {entry!r} names no schema")
        if not producer:
            raise ValueError(f"EDGE_RELAY_SCHEMAS entry {entry!r} names an empty producer")
        if schema in seen:
            raise ValueError(
                f"EDGE_RELAY_SCHEMAS names schema {schema!r} more than once — each schema "
                "can only be drained by one relay in this process"
            )
        seen.add(schema)
        specs.append(SchemaSpec(schema=schema, producer=producer))
    return specs


class EdgeRelaySettings(ServiceSettings):
    """Configuration for the edge relay. One instance, one process, many schemas.

    Reuses `DATABASE_URL`, `DB_OUTBOX_ROLE`, `RABBITMQ_URL` and `ENVIRONMENT` from
    `ServiceSettings` unchanged — this is still one relay against one database cluster,
    connecting as the same `opsgrids_outbox` role every service relay does. Everything
    below is what a multi-schema poller needs on top of that.
    """

    service_name: str = "edge-relay"
    #: Owns no tables of its own — every table it touches belongs to another service's
    #: schema, named explicitly per query. `ServiceSettings` requires a value; `public`
    #: is the same placeholder both gateways use for the same reason.
    db_schema: str = "public"

    #: **BYPASSRLS, SELECT and UPDATE on `outbox` only** — identical role, identical
    #: grant, to every service relay (CLAUDE.md). Pinned here the same way each service's
    #: own `config.py` pins it, so the edge relay binds correctly out of the box instead
    #: of needing `DB_OUTBOX_ROLE` set by hand in every environment that runs it.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: `schema` or `schema:producer`, comma-separated. Required: an edge relay configured
    #: to drain nothing is a deployment mistake, not a valid empty state, and `main()`
    #: refuses to start on it.
    edge_relay_schemas: Annotated[list[SchemaSpec], NoDecode] = Field(default_factory=list)

    #: The one pool every schema's relay shares. Small on purpose — see the module
    #: docstring for why this replaces `init_outbox_engine`'s hardcoded 2+2.
    edge_relay_pool_size: int = 5
    edge_relay_max_overflow: int = 5

    #: Forwarded straight through to every schema's `OutboxRelay`; same names, same
    #: defaults as `OutboxRelay.__init__`.
    edge_relay_batch_size: int = 100
    edge_relay_poll_interval_seconds: float = 1.0
    edge_relay_max_attempts: int = 50
    edge_relay_max_backoff_seconds: float = 60.0
    edge_relay_max_idle_interval_seconds: float = 5.0

    #: How a schema's *supervisor* — not `OutboxRelay` itself — paces restarts after an
    #: ordinary failure (a DB hiccup, a broker outage at connect time). Exponential,
    #: capped, independent of `OutboxRelay`'s own idle/failure backoff.
    edge_relay_restart_backoff_initial_seconds: float = 1.0
    edge_relay_restart_backoff_max_seconds: float = 60.0

    #: A missing `outbox` table is not a failure to escalate — it is a service that has
    #: not been migrated onto this box yet. Retried at this fixed, slow interval forever,
    #: logged once rather than on every attempt.
    edge_relay_missing_table_retry_seconds: float = 60.0

    #: How long `stop()` is given to let every relay finish its current pass before its
    #: task is cancelled outright. Bounds shutdown even if a relay is mid-way through a
    #: multi-second `OutboxRelay` backoff sleep when the signal arrives.
    edge_relay_shutdown_grace_seconds: float = 5.0

    @field_validator("edge_relay_schemas", mode="before")
    @classmethod
    def _parse_schemas(cls, v: object) -> object:
        if isinstance(v, str):
            return parse_edge_relay_schemas(v)
        return v


def _make_outbox_model(schema: str) -> type[Any]:
    """An ORM-mapped `OutboxEvent`, bound to `schema`, importing no service's `app`.

    `import app` is ambiguous across services (CLAUDE.md) — this process cannot import
    any service's `app.models` to get at its `OutboxEvent`, and must not import two
    services' `app` packages into one process regardless. Instead it does exactly what
    every service's own `app/models.py` does — mix `OutboxMixin` onto a schema-scoped
    declarative base — except the base is built fresh here, per schema, per call, rather
    than reused from a service module.

    A `Table`, not an ORM class, would be lighter, but `OutboxRelay._drain` calls
    `session.execute(select(cls)...)` and then mutates `row.published_at`/`row.attempts`
    on what it gets back — that mutation is unit-of-work attribute tracking, which only
    ORM-mapped instances have. A bare `Table` gives back immutable `Row` tuples and
    `_drain` would raise on the first successful publish. So this builds a real mapped
    class, one fresh declarative registry per schema, so two schemas' `OutboxEvent`
    classes never collide in the same process.
    """

    class _Base(DeclarativeBase):
        metadata = MetaData(schema=schema, naming_convention=NAMING_CONVENTION)

    class OutboxEvent(_Base, OutboxMixin):
        pass

    return OutboxEvent


def _root_cause(exc: BaseException) -> Iterable[BaseException]:
    """Walk `orig` (SQLAlchemy's wrapped driver error) then `__cause__`, `exc` first."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "orig", None) or current.__cause__


def _is_missing_table_error(exc: BaseException) -> bool:
    """True for "relation \"<schema>.outbox\" does not exist".

    Matched by exception class name — asyncpg raises `UndefinedTableError`, and
    SQLAlchemy wraps it in `orig` — rather than importing asyncpg here, so a lightweight
    fake exception in tests does not need the real driver installed to exercise this
    path.
    """
    return any(type(cause).__name__ == "UndefinedTableError" for cause in _root_cause(exc))


async def _supervise_schema(
    spec: SchemaSpec,
    settings: EdgeRelaySettings,
    session_factory: async_sessionmaker[AsyncSession],
    active: dict[str, _Stoppable | None],
) -> None:
    """Run `spec`'s `OutboxRelay` forever, rebuilding and restarting it on failure.

    Never raises on its own — every failure this function can name is logged and
    retried, so one schema's trouble cannot end the `asyncio.gather` in `run_forever`
    and take the other schemas' tasks down with it. The only way out is task
    cancellation (shutdown) or `relay.run()` returning normally, which only happens
    after `relay.stop()`.
    """
    outbox_cls = _make_outbox_model(spec.schema)
    backoff = settings.edge_relay_restart_backoff_initial_seconds
    warned_missing_table = False

    while True:
        relay = OutboxRelay(
            amqp_url=settings.rabbitmq_url,
            outbox_cls=outbox_cls,
            session_factory=session_factory,
            producer=spec.producer,
            batch_size=settings.edge_relay_batch_size,
            poll_interval=settings.edge_relay_poll_interval_seconds,
            max_attempts=settings.edge_relay_max_attempts,
            max_backoff=settings.edge_relay_max_backoff_seconds,
            max_idle_interval=settings.edge_relay_max_idle_interval_seconds,
        )
        active[spec.schema] = relay
        try:
            await relay.run()
            return  # relay.stop() was called deliberately — shutting down.
        except Exception as exc:
            if _is_missing_table_error(exc):
                if not warned_missing_table:
                    log.warning(
                        "edge_relay.schema.missing_table",
                        schema=spec.schema,
                        producer=spec.producer,
                        error=str(exc)[:200],
                    )
                    warned_missing_table = True
                await asyncio.sleep(settings.edge_relay_missing_table_retry_seconds)
                continue
            log.error(
                "edge_relay.schema.failed",
                schema=spec.schema,
                producer=spec.producer,
                error=str(exc)[:300],
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, settings.edge_relay_restart_backoff_max_seconds)
        finally:
            active[spec.schema] = None


async def _graceful_stop(
    tasks: Mapping[str, asyncio.Task[None]],
    active: Mapping[str, _Stoppable | None],
    grace_seconds: float,
) -> None:
    """Ask every schema's relay to stop, then cancel whichever is still running.

    `relay.stop()` only takes effect the next time that relay's own loop checks it —
    after its current sleep, which can be as long as `max_backoff`/`max_idle_interval` —
    so a relay mid-sleep would otherwise miss the request entirely. The grace window lets
    every relay currently *working* finish cleanly; cancellation is what bounds shutdown
    to a few seconds regardless: `asyncio.sleep` responds to cancellation immediately, no
    matter how much of it is left.
    """
    log.info("edge_relay.stopping")
    for relay in active.values():
        if relay is not None:
            relay.stop()
    _, pending = await asyncio.wait(set(tasks.values()), timeout=grace_seconds)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    log.info("edge_relay.stopped")


async def run_forever(settings: EdgeRelaySettings, *, engine: AsyncEngine | None = None) -> None:
    """Drain every configured schema until `SIGTERM`/`SIGINT`, or forever under a test.

    `engine`, injected, lets tests reuse a single pool across assertions instead of
    opening one per call; production leaves it `None` and this builds the one pool the
    whole process shares.
    """
    specs = settings.edge_relay_schemas
    if not specs:
        raise RuntimeError(
            "EDGE_RELAY_SCHEMAS is empty — the edge relay has nothing to drain. Set it to "
            "a comma-separated list of 'schema' or 'schema:producer' entries."
        )

    owns_engine = engine is None
    if engine is None:
        if not settings.db_outbox_role:
            log.warning(
                "outbox.role.not_split",
                service=settings.service_name,
                hint="db_outbox_role is unset; the edge relay runs as the app role and sees "
                "no rows under row-level security",
            )
        engine = create_async_engine(
            settings.outbox_dsn,
            pool_size=settings.edge_relay_pool_size,
            max_overflow=settings.edge_relay_max_overflow,
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    # Deliberately no `search_path`: one pool is shared by every schema, and
                    # each schema's `OutboxEvent` table is built with its own `schema=` above,
                    # so every statement is fully qualified and search_path plays no part.
                    "application_name": f"{settings.service_name}-outbox",
                }
            },
        )
    await assert_outbox_role(engine, environment=settings.environment)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )

    active: dict[str, _Stoppable | None] = {spec.schema: None for spec in specs}
    tasks = {
        spec.schema: asyncio.create_task(
            _supervise_schema(spec, settings, session_factory, active),
            name=f"edge-relay-{spec.schema}",
        )
        for spec in specs
    }

    log.info(
        "edge_relay.started",
        schemas=[spec.schema for spec in specs],
        producers={spec.schema: spec.producer for spec in specs},
    )

    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    handlers_installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            # NotImplementedError on platforms (e.g. some Windows event loops) without
            # signal-handler support — not a target for this VM-only module, but cheap to
            # tolerate rather than crash on.
            loop.add_signal_handler(sig, stop_requested.set)
            handlers_installed.append(sig)

    try:
        try:
            # The ordinary path: SIGTERM/SIGINT set the event and this returns normally.
            await stop_requested.wait()
        except asyncio.CancelledError:
            # `run_forever` itself was cancelled directly (a test driving shutdown without
            # signals, or an embedding process managing this as a task). Same graceful
            # stop either way, then this task keeps its own cancellation — swallowing it
            # here would make a cancelled task look like it exited on its own.
            await _graceful_stop(tasks, active, settings.edge_relay_shutdown_grace_seconds)
            raise
        await _graceful_stop(tasks, active, settings.edge_relay_shutdown_grace_seconds)
    finally:
        for sig in handlers_installed:
            loop.remove_signal_handler(sig)
        if owns_engine:
            await engine.dispose()


def main() -> None:
    settings = EdgeRelaySettings()
    configure_logging(
        settings.service_name, level=settings.log_level, json_output=settings.log_json
    )
    asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
