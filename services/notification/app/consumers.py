"""Event consumers for notification. Runs as its own process.

This is the service's reason to exist. It binds every routing key in
`domain.SUBSCRIBABLE_EVENTS` — ninety-odd keys across twelve products — and folds each arriving
event through the organization's own rules. The coupling to the producing services is the
routing key and the documented payload, **never an import**: a rule on
`support.desk.ticket.created` works whether or not support is deployed on this branch, and a key
whose producer is not merged yet simply receives nothing until it is.

Every handler is idempotent and tenant-bound. The envelope carries `organization_id`, so each
message is folded inside `session_scope(organization_id=…)`: the NOBYPASSRLS app role with the
tenant GUC set for exactly this message (ADR-0002 §2), so a fold can only ever read that
organization's rules and write that organization's messages. `already_processed` guards the head
and `mark_processed` is staged in the same transaction as the fold, so a redelivered event is a
no-op — redelivery is normal, not exceptional.

One handler here does talk to a provider: `fan_out`, the campaign sender, bound to
`engage.campaign.campaign.send_requested`. A campaign is not a message the dispatcher can take one
at a time — it is an audience expanded, re-checked against suppressions and sent under a daily
cap — so it runs here, in batches, each in its own short transaction.

Apart from that, nothing here talks to a provider. The fold stages message rows and a
`message.queued` event; the dispatcher does the sending. An event transaction that waited on an
SMTP handshake would make a slow provider into a database problem.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import domain, services
from app.config import settings
from app.models import ProcessedEvent
from app.targets import TargetResolver, make_resolver
from app.transports import Transport, TransportUnavailable
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("notification.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="notification.events")


async def fold(envelope: dict[str, Any], resolve_target: TargetResolver | None = None) -> None:
    """Run one event through this organization's rules, idempotently.

    The GUC is set per message (transaction-local), so the app role reads exactly this
    organization's rules and writes exactly its messages. `mark_processed` shares the
    transaction, so a redelivery that gets past the broker is dropped here rather than sending a
    customer the same notification twice.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    event_type = str(envelope["event_type"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))

    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("event.redelivered", event_type=event_type, event_id=str(event_id))
            return
        staged = await services.apply_rules(
            session,
            organization_id=organization_id,
            envelope=envelope,
            resolve_target=resolve_target,
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    if staged:
        log.info("notification.staged", event_type=event_type, count=len(staged))


async def fan_out(
    envelope: dict[str, Any],
    *,
    transport: Transport | None = None,
    batch_size: int | None = None,
) -> str:
    """Send one campaign: expand its audience, then send batch by batch. Returns the last
    batch outcome (`done`, `stopped`, `capped`, `unavailable`, or `redelivered`).

    Each step is its own tenant-bound transaction, so a cancel committed between batches is
    seen by the next one, and a crash loses at most one batch's bookkeeping. `processed_events`
    is written only once the run ends, in its own transaction: a run the provider interrupted
    raises instead, so the broker redelivers it and the redelivery resumes from the recipients
    still `queued` — expansion is idempotent and sent rows are never picked again.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    event_type = str(envelope["event_type"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    campaign_id = uuid.UUID(str((envelope.get("payload") or {})["campaign_id"]))
    size = batch_size or settings().engage_campaign_batch_size

    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("campaign.redelivered", campaign_id=str(campaign_id))
            return "redelivered"
        sending = await services.expand_campaign(
            session, organization_id=organization_id, campaign_id=campaign_id, batch_size=size
        )

    outcome = "stopped"
    while sending:
        async with session_scope(organization_id=organization_id) as session:
            outcome = await services.send_campaign_batch(
                session,
                organization_id=organization_id,
                campaign_id=campaign_id,
                batch_size=size,
                transport=transport,
            )
        if outcome != "more":
            break

    if outcome == "unavailable":
        raise TransportUnavailable(f"campaign {campaign_id}: provider unavailable, will retry")

    async with session_scope(organization_id=organization_id) as session:
        mark_processed(session, ProcessedEvent, event_id, event_type)
    log.info("campaign.fan_out.finished", campaign_id=str(campaign_id), outcome=outcome)
    return outcome


def register(resolve_target: TargetResolver | None = None) -> None:
    """Bind every subscribable routing key to the one fold.

    The target resolver is injected rather than imported, the way console injects its price
    resolver: a rule that targets a contact needs an HTTP call to customer-service, and a test
    should be able to substitute that without a network or a customer-service to talk to.
    """

    async def on_event(envelope: dict[str, Any]) -> None:
        await fold(envelope, resolve_target)

    for event_type in domain.SUBSCRIBABLE_EVENTS:
        consumer.on(event_type)(on_event)

    async def on_campaign_send(envelope: dict[str, Any]) -> None:
        await fan_out(envelope)

    consumer.on(services.EVT_CAMPAIGN_SEND_REQUESTED)(on_campaign_send)


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
