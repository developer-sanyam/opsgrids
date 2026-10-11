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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import a1_services, domain, selectors, services, w1_services, w1_setup
from app.config import settings
from app.contacts import ContactResolver, make_contact_resolver
from app.models import ProcessedEvent
from app.storefront_optin import StorefrontOptInFetcher, make_storefront_opt_in_fetcher
from app.targets import TargetResolver, make_resolver
from platform_core import pii
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("notification.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="notification.events")

#: Same shape as `app.cron.WorkScope` — defined again here, not imported, because `app.consumers`
#: and `app.cron` are independent layers (the service anatomy, CLAUDE.md; `.importlinter`'s
#: `service-layers` contract refuses the import).
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it. Module-level and process-wide on
#: purpose, the same seam the sweeps already use (`app.cron.WorkScope`).
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


class ContactResolutionUnavailable(RuntimeError):
    """The CRM could not be asked at all — raised so `EventConsumer`'s own redeliver-once-then-
    dead-letter handling (`platform_core.events.EventConsumer._handle`) gives this a genuine
    second attempt, rather than `fold_inbound_contact` silently acknowledging a message nothing
    was actually resolved for. Never raised for "the CRM answered and it was nobody" — that is a
    definite answer (`app.contacts.ContactResolution.answered=True, contact_id=None`) and is
    written and marked processed like any other, not retried."""


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

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("event.redelivered", event_type=event_type, event_id=str(event_id))
            return
        staged = await services.apply_rules(
            session,
            organization_id=organization_id,
            envelope=envelope,
            resolve_target=resolve_target,
        )
        # Automations (slice A1) start on the same event, in this same transaction: one run per
        # (automation, event), so the ledger row below and the run rows commit together.
        started = await a1_services.start_runs(
            session,
            organization_id=organization_id,
            envelope=envelope,
            resolve_target=resolve_target,
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    if started:
        log.info("automation.runs.started", event_type=event_type, count=len(started))
    if staged:
        log.info("notification.staged", event_type=event_type, count=len(staged))


async def expand_campaign_send(
    envelope: dict[str, Any],
    *,
    batch_size: int | None = None,
    now: datetime | None = None,
) -> str:
    """Expand a campaign's audience into `queued` recipient rows. Returns `expanded`,
    `redelivered`, or `not_sending` (the campaign was cancelled before this ran, this firing was a
    scheduled send superseded by a reschedule, arrived too early, or failed the send gate at fire
    time — see `services.activate_scheduled_campaign`).

    One transaction, acking fast: this is the whole of what runs on the shared consumer for a
    campaign send. `app/cron.py`'s driver does the sending, on its own poll loop, so a campaign
    of any size never risks `notification.events`'s `consumer_timeout` and never delays another
    product's event behind it.

    A payload carrying `scheduled_for` (ADR-0015: this firing came from the scheduler, off a
    `schedule_send` request rather than an immediate `request_send`) is activated first, in the
    same transaction as the expansion that follows it — `request_send` already left the campaign
    `sending` by the time its own event reaches here, so activation only ever has something to do
    for a scheduled one. Activation re-runs the send gate against the campaign's state right now,
    not the state it had when it was scheduled, and pauses (never sends) a campaign that no longer
    passes it — see `services.activate_scheduled_campaign`'s own docstring.

    `now` is threaded through to that same early-arrival check purely for tests — the real
    consumer, bound as an event handler, is always called with the default (the wall clock).
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    event_type = str(envelope["event_type"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope.get("payload") or {}
    campaign_id = uuid.UUID(str(payload["campaign_id"]))
    scheduled_for_raw = payload.get("scheduled_for")
    scheduled_for = datetime.fromisoformat(scheduled_for_raw) if scheduled_for_raw else None
    size = batch_size or settings().engage_campaign_batch_size

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("campaign.redelivered", campaign_id=str(campaign_id))
            return "redelivered"
        if scheduled_for is not None:
            await services.activate_scheduled_campaign(
                session,
                organization_id=organization_id,
                campaign_id=campaign_id,
                scheduled_for=scheduled_for,
                now=now,
            )
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

    async with _session_scope(organization_id) as session:
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
            # id an earlier, successful run already wrote) and mark nothing processed. Raising
            # rather than returning is what actually gets this a real second attempt: a bare
            # `return` acked the message and the CRM being briefly unreachable turned into a
            # contact this conversation would never resolve unless someone noticed and replayed
            # it by hand. `EventConsumer` redelivers once and only dead-letters on a *second*
            # failure, so this cannot hot-loop — the same mechanism a transient database error
            # already relies on elsewhere in this service.
            log.warning("inbound.contact.unanswered", conversation_id=str(conversation_id))
            raise ContactResolutionUnavailable(
                f"could not resolve a contact for conversation {conversation_id}"
            )

        await services.resolve_conversation_contact(
            session,
            organization_id=organization_id,
            conversation_id=conversation_id,
            contact_id=resolution.contact_id,
        )
        mark_processed(session, ProcessedEvent, fold_id, "notification.whatsapp.contact_resolved")

    log.info("inbound.contact.resolved", matched=resolution.contact_id is not None)


#: Storefront's event for a ticked WhatsApp opt-in on its enquiry form (ids only).
EVT_STOREFRONT_WHATSAPP_OPTED_IN = "storefront.enquiry.whatsapp_opted_in"


async def record_storefront_opt_in(
    envelope: dict[str, Any], fetch: StorefrontOptInFetcher | None
) -> None:
    """Record a storefront buyer's WhatsApp marketing opt-in on the consent ledger, once.

    The number is fetched from storefront (the event never carries it). Storefront not answering
    raises `StorefrontUnavailable`, so the event is redelivered rather than acknowledged with
    nothing written; a 404 is a definite answer and is marked processed.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    enquiry_id = uuid.UUID(str((envelope.get("payload") or {})["enquiry_id"]))
    if fetch is None:
        log.error("storefront_opt_in.no_fetcher_configured")
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            return
        details = await fetch(str(organization_id), str(enquiry_id))
        if details is not None:
            await services.record_storefront_opt_in(
                session,
                organization_id=organization_id,
                enquiry_id=enquiry_id,
                name=details.name,
                email=details.email,
                phone=details.phone,
                occurred_at=details.opted_in_at,
            )
        mark_processed(session, ProcessedEvent, event_id, EVT_STOREFRONT_WHATSAPP_OPTED_IN)
    log.info("storefront_opt_in.recorded", found=details is not None)


async def wake_automation_run(
    envelope: dict[str, Any], resolve_target: TargetResolver | None = None
) -> str:
    """Resume one automation run that was waiting, exactly once (slice A1).

    The scheduler republishes `notification.automation.run_wake` at the wake time; the run's
    rotating token, compared and cleared under a row lock, is what makes a second delivery (or
    the overdue-wait sweep racing the scheduler) a no-op. The event id also goes through the
    ledger, like every other handler here.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope.get("payload") or {}
    if str(payload.get("organization_id", organization_id)) != str(organization_id):
        log.error("automation.wake.envelope_payload_org_mismatch")
        return "mismatch"
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            return "redelivered"
        outcome = await a1_services.wake_run(
            session,
            organization_id=organization_id,
            run_id=uuid.UUID(str(payload["run_id"])),
            wake_token=str(payload.get("wake_token") or ""),
            resolve_target=resolve_target,
        )
        mark_processed(session, ProcessedEvent, event_id, str(envelope["event_type"]))
    log.info("automation.wake", outcome=outcome)
    return outcome


async def run_whatsapp_setup(envelope: dict[str, Any]) -> str:
    """Run one WhatsApp Embedded Signup (slice W1). The steps, their idempotency and their
    failure handling live in `app/w1_setup.py`; this binds them to this process's tenant scope."""
    return await w1_setup.run_setup(envelope, scope=_session_scope)


def register(
    resolve_target: TargetResolver | None = None,
    resolve_contact: ContactResolver | None = None,
    fetch_storefront_opt_in: StorefrontOptInFetcher | None = None,
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

    async def on_automation_wake(envelope: dict[str, Any]) -> None:
        await wake_automation_run(envelope, resolve_target)

    consumer.on(a1_services.EVT_RUN_WAKE)(on_automation_wake)

    async def on_whatsapp_setup(envelope: dict[str, Any]) -> None:
        await run_whatsapp_setup(envelope)

    consumer.on(w1_services.EVT_SETUP_REQUESTED)(on_whatsapp_setup)

    # Defaulted here rather than at each entrypoint: every process that consumes needs it.
    fetch_opt_in = fetch_storefront_opt_in or make_storefront_opt_in_fetcher(settings())

    async def on_storefront_opt_in(envelope: dict[str, Any]) -> None:
        await record_storefront_opt_in(envelope, fetch_opt_in)

    consumer.on(EVT_STOREFRONT_WHATSAPP_OPTED_IN)(on_storefront_opt_in)


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
