"""Event consumers for subscription. Runs as its own process.

This is where the entitlement loop turns (ADR-0004 §5). The subscription service owns the
lifecycle; one of the things that moves that lifecycle is a **payment failure** in another
service. payment-service emits `commerce.payment.failed`; the handler here consumes it and
suspends the affected subscription — which emits `commerce.subscription.suspended`, which
product-service projects to a de-entitled `Activation`, which the gateway turns into a 402. Each
step happens in the service that owns it, connected only by events over the outbox.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as its side effects. Redelivery is normal, not exceptional — and a suspension is
additionally idempotent at the domain level (a subscription already suspended is left as is).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config import settings
from app.models import ProcessedEvent
from platform_core.db import init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("subscription.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="subscription.events")

#: The payment event that closes the loop. A contract string from payment-service, named here
#: rather than imported — one process may only ever hold one service's `app` (ADR-0002).
EVT_PAYMENT_FAILED = "commerce.payment.failed"


@consumer.on(EVT_PAYMENT_FAILED)
async def suspend_on_payment_failed(envelope: dict[str, Any]) -> None:
    """Suspend the subscription a failed charge was collecting for.

    The event carries `organization_id` (which tenant to bind) and `subscription_id` (which
    subscription to suspend) — stamped by payment when the charge was raised and echoed back
    through the whole chain. The GUC is set per message via `session_scope(organization_id=...)`,
    so this NOBYPASSRLS suspension and the `commerce.subscription.suspended` it emits pass the
    tenant policy. A charge with no subscription (`subscription_id` absent) is nothing to suspend.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    raw_subscription_id = payload.get("subscription_id")

    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("payment.failed.duplicate", event_id=str(event_id))
            return

        # Import inside the handler to keep the module import-light and avoid a cycle with the
        # services layer, mirroring how services.require_subscription imports selectors lazily.
        from app import selectors, services

        if raw_subscription_id:
            subscription = await selectors.get_subscription(session, uuid.UUID(raw_subscription_id))
            # Invisible under RLS unless it belongs to this tenant, so another organization's id is
            # simply None here — never a cross-tenant suspension.
            if subscription is not None:
                await services.suspend_on_payment_failure(session, subscription)
        # Marked processed regardless: an event for a subscription that no longer exists, or a
        # charge with no subscription, is handled — recording the id stops it being retried forever.
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "payment.failed.suspended",
        event_id=str(event_id),
        organization_id=str(organization_id),
        subscription_id=raw_subscription_id,
    )


async def main() -> None:
    init_engine(settings())
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
