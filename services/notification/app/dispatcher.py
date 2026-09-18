"""The dispatcher. Runs as its own process, and is the only thing that talks to a provider.

Driven by `notification.delivery.message.queued` rather than by polling a table. That choice
buys three things the polling design would have had to build: tenancy comes from the envelope's
`organization_id` so the app role can stay NOBYPASSRLS with the GUC bound per message; the relay
that delivers the event already exists and is already the privileged component; and redelivery —
the retry — is the broker's job rather than a second claim loop with its own backoff.

**Three transactions, and the provider call happens between the first and the third with nothing
open.** That is the whole design:

1. Claim: `pending` → `sending`, attempt counted, suppression re-checked. Commit.
2. Send. No transaction. A provider that hangs costs a connection, not a lock.
3. Record: the outcome and an append-only `Delivery` row. Commit.

The cost is a crash window — a process that dies between 2 and 3 leaves a `sending` row. That is
deliberately left visible rather than swept back into `pending`, because "did the customer
actually receive this?" has no safe default answer, and inventing one either double-sends an
invoice or silently drops it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import services
from app.config import Settings, settings
from app.models import ProcessedEvent
from app.transports import Outgoing, Transport, TransportRefused
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
            session, organization_id=organization_id, message_id=message_id
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
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error=str(exc)[:200],
        )
        return
    except Exception as exc:
        # `TransportUnavailable` and anything unexpected are treated alike: transient. An
        # unclassified exception from a provider client is exactly the case where retrying is
        # cheap and being wrong about permanence is not — a bug that reads as permanent silently
        # drops messages, while one that reads as transient shows up as a retry count.
        await _record_failure(
            organization_id, message_id, transport.name, str(exc), permanent=False, cfg=cfg
        )
        log.warning(
            "dispatch.unavailable",
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error=str(exc)[:200],
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
