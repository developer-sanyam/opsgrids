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

**An event invites one attempt.** Every queued event carries the message's attempt count when it
was made, and the claim refuses one whose count is no longer the message's: the sweep can
publish a second event for a message whose first is still in the queue, and without the count
each extra event would claim the message at once and burn an attempt, ignoring the backoff.

**Recording is fenced by the attempt this dispatcher claimed.** A send can outlive its lease, and
by the time it answers the sweep may have settled the claim and queued another attempt. What the
provider did is the truth, so a *success* is always recorded (`services.record_sent`): the
message becomes `sent` whatever state it was in, and the queued retry then finds it sent. A
*failure* is recorded only while it is still the message's current claim
(`services.record_failure`); a stale one must not flip a message another attempt has in flight
back to `pending`. The row is locked, waiting its turn, for both.

**The crash window, and what it costs.** A process that dies between 1 and 3 leaves a `sending`
row whose lease (`retry_at`) lapses; the sweep then settles the claim as a failed attempt
(`services.redrive_message`) and the message is retried or given up on like any other failure.
For email that is **at-least-once**: if the process died after the provider accepted the message
and before the outcome was recorded, the customer gets it twice — under the same `Message-ID`
(`<message id>@<sender domain>`, stable across attempts), which most mailboxes fold into one.
Earlier versions left such a row `sending` for a person to look at, on the grounds that "did the
customer receive this?" has no safe default. It has no safe default, but a row that stays
`sending` for ever is a password reset that never arrives and nothing that says so — and a
duplicate is a nuisance the customer can see, where a silent drop is neither visible nor
recoverable. Attempts stay bounded, so a message that crashes the process every time cannot loop.

**WhatsApp is not at-least-once.** Meta has no idempotency key and a template is billed, so a
second send is a second message the customer gets and the organization pays for. Only a failure
that provably never reached Meta is retried (`TransportUnavailable`: a connection that could not
be made, a full pool, a 429). A request that may have been received — a read timeout, a reset, a
5xx — is `TransportOutcomeUnknown`, and so is a WhatsApp claim whose lease lapsed: the attempt
fails as `outcome_unknown`, `message.failed` is published, an alert is logged, and a person checks
with Meta and resends by hand.
"""

from __future__ import annotations

import asyncio
import uuid
from email.utils import parseaddr
from typing import Any

from app import services
from app.config import Settings, settings
from app.models import ProcessedEvent
from app.transports import (
    Outgoing,
    Transport,
    TransportError,
    TransportOutcomeUnknown,
    TransportRefused,
)
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
            expected_attempts=_expected_attempts(payload),
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
            # Stable across attempts: the same message keeps the same `Message-ID`, so the
            # duplicate a crash-after-accept can produce (at-least-once, below) carries the
            # id most mailboxes already fold on. Email only — other transports ignore it.
            headers=(
                {"Message-ID": f"<{message_id}@{_message_id_domain(cfg)}>"}
                if message.channel == "email"
                else None
            ),
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
            organization_id,
            message_id,
            attempt,
            transport.name,
            str(exc),
            permanent=True,
            reason="refused",
            cfg=cfg,
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
    except TransportOutcomeUnknown as exc:
        # The provider may have taken it and the answer was lost. Not retried — a second send is
        # a second message — so it fails here, `message.failed` goes out, and a person decides.
        await _record_failure(
            organization_id,
            message_id,
            attempt,
            transport.name,
            str(exc),
            permanent=True,
            reason="outcome_unknown",
            cfg=cfg,
        )
        log.warning(
            "dispatch.outcome_unknown",
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
            organization_id,
            message_id,
            attempt,
            transport.name,
            str(exc),
            permanent=False,
            cfg=cfg,
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
            organization_id,
            message_id,
            attempt,
            transport.name,
            description,
            permanent=False,
            cfg=cfg,
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
            attempt=attempt,
            provider=receipt.provider,
            provider_message_id=receipt.provider_message_id,
        )
    log.info("dispatch.sent", channel=channel, message_id=str(message_id), attempt=attempt)


def _expected_attempts(payload: dict[str, Any]) -> int | None:
    """The attempt count the queued event was made at, or `None` for an event that carries none
    (one already in flight from before the field existed), which is honoured as it always was."""
    raw = payload.get("attempts")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _message_id_domain(cfg: Settings) -> str:
    """The domain a stable `Message-ID` is minted under: the sender's, so it is a name we are
    entitled to, and never empty."""
    return parseaddr(cfg.email_from)[1].rpartition("@")[2] or "notification.invalid"


async def _record_failure(
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    attempt: int,
    provider: str,
    error: str,
    *,
    permanent: bool,
    cfg: Settings,
    reason: str | None = None,
) -> None:
    async with session_scope(organization_id=organization_id) as session:
        await services.record_failure(
            session,
            organization_id=organization_id,
            message_id=message_id,
            attempt=attempt,
            provider=provider,
            error=error,
            permanent=permanent,
            reason=reason,
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
