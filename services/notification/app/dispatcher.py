"""The dispatcher. Runs as its own process, and is the only thing that talks to a provider.

Driven by `notification.delivery.message.queued` rather than by polling a table. That choice
buys two things the polling design would have had to build: tenancy comes from the envelope's
`organization_id` so the app role can stay NOBYPASSRLS with the GUC bound per message, and the
relay that delivers the event already exists and is already the privileged component.

**Three transactions, and the provider call happens between the first and the third with nothing
open.** That is the whole design:

1. Claim: `pending` → `sending`, attempt counted, suppression re-checked, a lease taken
   (`Message.retry_at`). The event is marked processed in the same transaction. Commit.
2. Send. No transaction. A provider that hangs costs a connection, not a lock.
3. Record: the outcome and an append-only `Delivery` row. Commit.

**A failure is not the broker's to retry, because the event is spent.** It was marked processed
at 1, and the consumer acks it once this function returns — so a redelivery would stop at
`already_processed`. What a transient failure does instead is written in step 3, in the same
transaction as the outcome: the message goes back to `pending` with `retry_at` set to the end of
an exponential backoff (`services.record_failure`). The recovery sweep in `app/cron.py` queues it
again when that arrives, through the outbox, as a fresh event. Attempts are bounded
(`dispatch_max_attempts`); the last failure marks the message `failed`, publishes
`message.failed` and logs an alert. A permanent refusal (`TransportRefused`) skips all of that and
fails at once.

**The crash window, and what it costs.** A process that dies between 1 and 3 leaves a `sending`
row whose lease (`retry_at`) lapses; the sweep then settles the claim as a failed attempt
(`services.redrive_message`) and the message is retried or given up on like any other failure.
That is **at-least-once**: if the process died after the provider accepted the message and
before the outcome was recorded, the customer gets it twice. Earlier versions left such a row
`sending` for a person to look at, on the grounds that "did the customer receive this?" has no
safe default. It has no safe default, but a row that stays `sending` for ever is a password reset
that never arrives and nothing that says so — and a duplicate is a nuisance the customer can
see, where a silent drop is neither visible nor recoverable. Attempts stay bounded, so a message
that crashes the process every time cannot loop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import services
from app.config import Settings, settings
from app.models import ProcessedEvent
from app.transports import Outgoing, Transport, TransportError, TransportRefused
from app.transports import build as build_transports
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("notification.dispatcher")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="notification.dispatch")


async def dispatch(
    envelope: dict[str, Any],
    *,
    transports: dict[str, Transport],
    cfg: Settings,
) -> None:
    """Send one queued message, or record why it did not go.

    Idempotency is layered rather than doubled up by accident. `processed_events` stops a
    redelivered *event* from starting a second send; the `pending` state check inside the claim
    stops a message that another dispatcher already took. Either alone would leave a gap; both
    together mean a customer gets one copy.
    """
    event_id = uuid.UUID(str(envelope["event_id"]))
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope.get("payload") or {}
    message_id = uuid.UUID(str(payload["message_id"]))

    # 1. Claim. Short, and committed before anything touches a network.
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("dispatch.redelivered", message_id=str(message_id))
            return
        message = await services.claim_for_send(
            session,
            organization_id=organization_id,
            message_id=message_id,
            lease_seconds=cfg.dispatch_claim_lease_seconds,
        )
        mark_processed(session, ProcessedEvent, event_id, str(envelope["event_type"]))
        if message is None:
            # Already sent, suppressed since, or claimed elsewhere. All benign.
            return
        outgoing = Outgoing(
            to_address=message.to_address,
            subject=message.subject,
            body=message.body,
            # Built and validated when the message was staged, not now: a template that cannot
            # be built is an authoring fault and belongs to the rule that named it, where the
            # log line can say which variable was missing. By here it is settled.
            template=message.provider_template_payload,
        )
        channel = message.channel
        attempt = message.attempts

    transport = transports.get(channel)
    if transport is None:  # pragma: no cover - `build` covers every channel in CHANNELS
        log.error("dispatch.no_transport", channel=channel, message_id=str(message_id))
        return

    # 2. Send. **No transaction is open here.**
    try:
        receipt = await transport.send(outgoing)
    except TransportRefused as exc:
        await _record_failure(
            organization_id, message_id, transport.name, str(exc), permanent=True, cfg=cfg
        )
        log.warning(
            "dispatch.refused",
            organization_id=str(organization_id),
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error=str(exc)[:200],
        )
        return
    except TransportError as exc:
        # `TransportUnavailable` (and its TLS kind). A transport writes its own message and
        # keeps the provider's words out of it, so this is safe to store and to log.
        await _record_failure(
            organization_id, message_id, transport.name, str(exc), permanent=False, cfg=cfg
        )
        log.warning(
            "dispatch.unavailable",
            organization_id=str(organization_id),
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error=str(exc)[:200],
        )
        return
    except Exception as exc:
        # Anything unexpected is treated like an outage: transient. An unclassified exception
        # from a provider client is exactly the case where retrying is cheap and being wrong
        # about permanence is not — a bug that reads as permanent silently drops messages, while
        # one that reads as transient shows up as a retry count. Its type is recorded, never its
        # text: nothing that is not a `TransportError` has been written with this row's storage
        # and logs in mind, and a client library's message can carry the request it was making.
        description = f"unclassified {type(exc).__name__}"
        await _record_failure(
            organization_id, message_id, transport.name, description, permanent=False, cfg=cfg
        )
        log.warning(
            "dispatch.unavailable",
            organization_id=str(organization_id),
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error=description,
        )
        return

    # 3. Record the win.
    async with session_scope(organization_id=organization_id) as session:
        await services.record_sent(
            session,
            organization_id=organization_id,
            message_id=message_id,
            provider=receipt.provider,
            provider_message_id=receipt.provider_message_id,
        )
    log.info("dispatch.sent", channel=channel, message_id=str(message_id), attempt=attempt)


async def _record_failure(
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    provider: str,
    error: str,
    *,
    permanent: bool,
    cfg: Settings,
) -> None:
    async with session_scope(organization_id=organization_id) as session:
        await services.record_failure(
            session,
            organization_id=organization_id,
            message_id=message_id,
            provider=provider,
            error=error,
            permanent=permanent,
            max_attempts=cfg.dispatch_max_attempts,
            retry_base_seconds=cfg.dispatch_retry_base_seconds,
            retry_max_seconds=cfg.dispatch_retry_max_seconds,
        )


def register(transports: dict[str, Transport], cfg: Settings) -> None:
    """Bind the queued-message key. Split out so tests register a recording transport."""

    async def on_queued(envelope: dict[str, Any]) -> None:
        await dispatch(envelope, transports=transports, cfg=cfg)

    consumer.on(services.EVT_MESSAGE_QUEUED)(on_queued)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    # Raises rather than downgrading to `log` when a real transport is named without
    # credentials: a service that quietly stops sending looks exactly like one with nothing
    # to send, and only a customer finds the difference.
    register(build_transports(cfg), cfg)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
