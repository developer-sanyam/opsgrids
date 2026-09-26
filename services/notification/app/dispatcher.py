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

**Whose transport.** The `transports` map is the *platform's* — one SMTP relay, one WhatsApp
number, from the environment. It serves only platform-owned messages (login and password mail,
staged under the platform tenant) and the `in_app` feed. A customer's `email` and `whatsapp`
messages go out on that organization's own connection, resolved between the claim and the send by
`app/tenant_transport.py`, and a customer with no usable connection gets a failed message with a
machine-readable reason — never the platform's number. See that module for how the two kinds are
told apart.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import services, tenant_transport
from app.config import Settings, settings
from app.models import ProcessedEvent
from app.transports import Outgoing, Transport, TransportRefused
from app.transports import build as build_transports
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("notification.dispatcher")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="notification.dispatch")

#: Same shape as `app.cron.WorkScope` — defined again here, not imported, because this module
#: and `app.cron` are independent layers (the service anatomy, CLAUDE.md; `.importlinter`'s
#: `service-layers` contract refuses the same import from `app.consumers`, and this module keeps
#: the same discipline even though the contract does not name it explicitly).
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: dispatcher's own connection pool when it runs standalone (`python -m app.dispatcher`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this dispatcher as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded dispatcher's per-message transactions can neither take a
#: connection from request traffic nor be starved by it. Module-level and process-wide on
#: purpose, the same seam the sweeps already use (`app.cron.WorkScope`).
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this dispatcher starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


#: Resolves the transport for one tenant-owned message: `(organization_id, channel)` in, a
#: transport or the reason there is none out. Defaults to `tenant_transport.resolve` bound to this
#: process's session scope; a test passes its own to inject the egress guard's resolver.
TenantRoute = Callable[[uuid.UUID, str], Awaitable[Transport | tenant_transport.ChannelUnavailable]]


async def dispatch(
    envelope: dict[str, Any],
    *,
    transports: dict[str, Transport],
    cfg: Settings,
    tenant_route: TenantRoute | None = None,
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
    async with _session_scope(organization_id) as session:
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

    transport = await _transport_for(
        organization_id,
        message_id,
        channel,
        attempt=attempt,
        platform=transports,
        tenant_route=tenant_route,
        cfg=cfg,
    )
    if transport is None:
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
    async with _session_scope(organization_id) as session:
        await services.record_sent(
            session,
            organization_id=organization_id,
            message_id=message_id,
            provider=receipt.provider,
            provider_message_id=receipt.provider_message_id,
        )
    log.info("dispatch.sent", channel=channel, message_id=str(message_id), attempt=attempt)


async def _transport_for(
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    channel: str,
    *,
    attempt: int,
    platform: dict[str, Transport],
    tenant_route: TenantRoute | None,
    cfg: Settings,
) -> Transport | None:
    """The transport this message goes out on, or None after recording why it cannot.

    A platform-owned message (and the `in_app` feed) takes the platform's transport from
    `platform`. Everything else is a customer's, and takes that customer's own connection — the
    platform's transport is not reachable from that branch, so no failure below can turn into
    a send from the shared number.

    A customer with no usable connection fails the message **permanently and on the spot**: the
    fix is a connection, not time, so a retry would only delay the moment someone looks. The
    reason is a code (`tenant_transport.NOT_CONNECTED` and its siblings) that reaches
    `messages.last_error`, the attempt ledger and the failed event — never a sentence, never a
    credential. Resolution itself failing unexpectedly (the database, say) is transient, like any
    other send that raised something nobody classified.
    """
    if not tenant_transport.needs_own_connection(organization_id, channel):
        platform_transport = platform.get(channel)
        if platform_transport is None:  # pragma: no cover - `build` covers every channel
            log.error("dispatch.no_transport", channel=channel, message_id=str(message_id))
        return platform_transport

    route = tenant_route or _default_tenant_route(cfg)
    try:
        routed = await route(organization_id, channel)
    except Exception as exc:
        log.exception(
            "dispatch.tenant_route_failed",
            organization_id=str(organization_id),
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            error_type=type(exc).__name__,
        )
        await _record_failure(
            organization_id,
            message_id,
            tenant_transport.NO_PROVIDER,
            "channel_resolution_failed",
            permanent=False,
            cfg=cfg,
        )
        return None

    if isinstance(routed, tenant_transport.ChannelUnavailable):
        await _record_failure(
            organization_id,
            message_id,
            tenant_transport.NO_PROVIDER,
            routed.reason,
            permanent=True,
            cfg=cfg,
        )
        log.warning(
            "dispatch.channel_unavailable",
            organization_id=str(organization_id),
            channel=channel,
            message_id=str(message_id),
            attempt=attempt,
            reason=routed.reason,
        )
        return None
    return routed


def _default_tenant_route(cfg: Settings) -> TenantRoute:
    """`tenant_transport.resolve`, reading through whichever scope this process runs on. The
    scope is looked up per call, not captured, so `use_session_scope` still takes effect."""

    async def route(
        organization_id: uuid.UUID, channel: str
    ) -> Transport | tenant_transport.ChannelUnavailable:
        return await tenant_transport.resolve(
            organization_id, channel, scope=_session_scope, cfg=cfg
        )

    return route


async def _record_failure(
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    provider: str,
    error: str,
    *,
    permanent: bool,
    cfg: Settings,
) -> None:
    async with _session_scope(organization_id) as session:
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
