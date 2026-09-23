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

`expand_campaign_send`, bound to `engage.campaign.campaign.send_requested`, is the one handler
that is not a fold of an arbitrary product's event: it is Engage's own send gate committing, and
it does exactly one thing — expand the audience into `queued` recipient rows — before returning.
It used to also *send* those rows, batch after batch, inside this same handler; that ran on
`notification.events`, the queue every other product's events (password resets included) also
ride, and a large campaign could run past RabbitMQ's `consumer_timeout` and get redelivered
mid-send, or simply hold up somebody else's ticket notification behind it. Sending is now
`app/cron.py`'s job — a separate, resumable poll loop, not the shared consumer — which claims
batches of `queued` recipients for campaigns in `sending` (or `paused`, on its own backoff) and
sends them outside this queue entirely. See its module docstring for the rest of that design.

Nothing here talks to a provider. The fold stages message rows and a `message.queued` event; the
dispatcher does the sending. Expansion writes rows and nothing else. An event transaction that
waited on an SMTP handshake would make a slow provider into a database problem.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import domain, selectors, services
from app.config import settings
from app.contacts import ContactResolver, make_contact_resolver
from app.models import ProcessedEvent
from app.targets import TargetResolver, make_resolver
from platform_core import pii
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


async def expand_campaign_send(
    envelope: dict[str, Any],
    *,
    batch_size: int | None = None,
) -> str:
    """Expand a campaign's audience into `queued` recipient rows. Returns `expanded`,
    `redelivered`, or `not_sending` (the campaign was cancelled before this ran).

    One transaction, acking fast: this is the whole of what runs on the shared consumer for a
    campaign send. `app/cron.py`'s driver does the sending, on its own poll loop, so a campaign
    of any size never risks `notification.events`'s `consumer_timeout` and never delays another
    product's event behind it.
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
        mark_processed(session, ProcessedEvent, event_id, event_type)

    outcome = "expanded" if sending else "not_sending"
    log.info("campaign.expanded", campaign_id=str(campaign_id), outcome=outcome)
    return outcome


async def fold_inbound_contact(
    envelope: dict[str, Any], resolve_contact: ContactResolver | None = None
) -> None:
    """Resolve the contact for one inbound WhatsApp conversation, idempotently.

    A separate handler from `fold`, on the same event (`services.EVT_INBOUND_RECEIVED`) but
    **not** the same idempotency row: that event's own fold (`fold_inbound_message`, run inside
    the webhook request, not here) already durably wrote the conversation and message before this
    ever runs, so this step's identity is the *contact resolution*, not the arrival of the
    message. A derived id namespaced against the raw event id keeps the two folds from treating
    each other's `processed_events` row as their own.

    **Only a definite answer is marked processed.** `app/contacts.ContactResolution.answered`
    tells "the CRM said nobody" (write `None`, mark processed — asking again would waste a call on
    a question already settled) apart from "the CRM could not be asked" (write nothing, mark
    nothing — a transient outage must not permanently erase a match a *later* run could still
    make, and must not be confused with the ledger's own redelivery guard, which exists for a
    message Meta sends twice, not for a lookup this service itself declined to finish).
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope.get("payload") or {}
    conversation_id = uuid.UUID(str(payload["conversation_id"]))

    # The envelope is the trusted tenant binding (it is what `session_scope` below binds the GUC
    # to); the payload's own copy is a second statement of the same fact written by
    # `fold_inbound_message`. The two must never disagree — if they do, something upstream is
    # broken in a way worth refusing loudly rather than silently trusting whichever value is
    # convenient.
    payload_org = payload.get("organization_id")
    if payload_org is not None and str(payload_org) != str(organization_id):
        log.error(
            "inbound.contact.envelope_payload_org_mismatch",
            envelope_org=str(organization_id),
        )
        return

    fold_id = uuid.uuid5(event_id, "contact_resolve")

    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, fold_id):
            log.info("inbound.contact.redelivered", conversation_id=str(conversation_id))
            return

        if resolve_contact is None:
            log.error("inbound.contact.no_resolver_configured")
            return

        conversation = await selectors.get_conversation(session, organization_id, conversation_id)
        if conversation is None:
            # Nothing to resolve a phone number for, and nothing to retry — the conversation this
            # event named is gone (deleted, or a stale redelivery of an org that never had it).
            return

        phone = pii.open_sealed(conversation.wa_address_enc, settings=settings())
        resolution = await resolve_contact(str(organization_id), phone)

        if not resolution.answered:
            # No definite answer — leave the conversation exactly as it is (including a contact
            # id an earlier, successful run already wrote) and mark nothing processed, so a later
            # redelivery or a manual replay gets a real second attempt rather than a permanent
            # "already handled" that was never actually answered.
            log.warning("inbound.contact.unanswered", conversation_id=str(conversation_id))
            return

        await services.resolve_conversation_contact(
            session,
            organization_id=organization_id,
            conversation_id=conversation_id,
            contact_id=resolution.contact_id,
        )
        mark_processed(session, ProcessedEvent, fold_id, "notification.whatsapp.contact_resolved")

    log.info("inbound.contact.resolved", matched=resolution.contact_id is not None)


def register(
    resolve_target: TargetResolver | None = None,
    resolve_contact: ContactResolver | None = None,
) -> None:
    """Bind every subscribable routing key to the one fold.

    The target resolver is injected rather than imported, the way console injects its price
    resolver: a rule that targets a contact needs an HTTP call to customer-service, and a test
    should be able to substitute that without a network or a customer-service to talk to. The
    contact resolver is the same shape, in the other direction.
    """

    async def on_event(envelope: dict[str, Any]) -> None:
        await fold(envelope, resolve_target)

    for event_type in domain.SUBSCRIBABLE_EVENTS:
        consumer.on(event_type)(on_event)

    async def on_campaign_send(envelope: dict[str, Any]) -> None:
        await expand_campaign_send(envelope)

    consumer.on(services.EVT_CAMPAIGN_SEND_REQUESTED)(on_campaign_send)

    async def on_inbound_received(envelope: dict[str, Any]) -> None:
        await fold_inbound_contact(envelope, resolve_contact)

    consumer.on(services.EVT_INBOUND_RECEIVED)(on_inbound_received)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    register(make_resolver(cfg), make_contact_resolver(cfg))
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
