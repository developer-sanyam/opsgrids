"""Event consumers for console. Runs as its own process.

This is the whole point of the service (ADR-0004 §4): it binds the commerce transition-event
routing keys and folds each into the projections. The coupling to the producing services is the
routing key and the documented payload — never an import — so console consumes
`commerce.subscription.*`, `commerce.invoice.*`, `commerce.credit.*` and `commerce.usage.recorded`
whether or not those services' code is on this branch. A routing key whose producer is not merged
yet simply receives nothing until it is; the binding is harmless and waiting.

It also binds account-service's `core.org.created`/`core.org.updated` (`domain.ORG_EVENTS`) —
identity, not commerce, folded into the same per-org row so the operator list can show a name
rather than a bare id. Console went live after every existing organization and subscription, so
this binding alone never backfills what predates it; `app/backfill.py` is the one-off that does,
through the same fold functions this module calls.

Every handler is idempotent and tenant-bound. The envelope carries `organization_id`, so each
message is folded inside `session_scope(organization_id=…)`: the NOBYPASSRLS app role with the
tenant GUC set for exactly this message (ADR-0002 §2), so a fold can only write the event's own
organization's projection row. `already_processed` guards the head and `mark_processed` is staged
in the same transaction as the fold, so a redelivered event is a no-op — redelivery is normal, not
exceptional.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.config import settings
from app.models import ProcessedEvent
from app.price import PriceResolver, make_resolver
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("console.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="console.events")

Fold = Callable[[Any, dict[str, Any]], Awaitable[None]]

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own, so
#: it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope `consume` opens below. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point `consume` at `scope` instead of the default engine. Called once, before this
    consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


async def consume(envelope: dict[str, Any], fold: Fold) -> None:
    """Run one fold inside a tenant-bound, idempotent transaction.

    The GUC is set per message (transaction-local), so the app role writes exactly this
    organization's projection row and no other. `mark_processed` shares the transaction, so a
    redelivery that gets past the broker is dropped here rather than double-counted.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    event_type = str(envelope["event_type"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("event.redelivered", event_type=event_type, event_id=str(event_id))
            return
        await fold(session, envelope)
        mark_processed(session, ProcessedEvent, event_id, event_type)


def register(resolve_price: PriceResolver) -> None:
    """Bind every routing key to its fold. Split out so tests register with a fake resolver."""

    async def fold_subscription(session: Any, envelope: dict[str, Any]) -> None:
        await services.fold_subscription_event(session, envelope, resolve_price=resolve_price)

    async def on_subscription(env: dict[str, Any]) -> None:
        await consume(env, fold_subscription)

    async def on_invoice(env: dict[str, Any]) -> None:
        await consume(env, services.fold_invoice_event)

    async def on_credit(env: dict[str, Any]) -> None:
        await consume(env, services.fold_credit_event)

    async def on_usage(env: dict[str, Any]) -> None:
        await consume(env, services.fold_usage_event)

    async def on_org(env: dict[str, Any]) -> None:
        await consume(env, services.fold_org_event)

    for key in domain.SUBSCRIPTION_EVENTS:
        consumer.on(key)(on_subscription)
    for key in domain.INVOICE_EVENTS:
        consumer.on(key)(on_invoice)
    for key in domain.CREDIT_EVENTS:
        consumer.on(key)(on_credit)
    consumer.on(domain.USAGE_EVENT)(on_usage)
    for key in domain.ORG_EVENTS:
        consumer.on(key)(on_org)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    register(make_resolver(cfg))
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
