"""Event consumers for product. Runs as its own process.

This is where commerce meets entitlement (ADR-0004 §5). The subscription service owns the
lifecycle and emits a `commerce.subscription.*` event on every transition; here those events are
projected onto the `Activation` row the gateway's 402 gate reads. There is **no nightly job**:
`Activation.entitles` already evaluates trial expiry at read time, so keeping the row's status in
step with the lifecycle is all that entitlement needs.

Three properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `atmora_product` (NOBYPASSRLS), and
  `activations` is under a forced tenant policy. `session_scope(organization_id=...)` binds the
  tenant for the transaction, so the upsert passes the policy's `WITH CHECK`. Without it the write
  is refused — the RLS net, proven by a test that omits the binding and asserts the failure.
* **Every handler is idempotent.** Delivery is at-least-once; `already_processed` before acting,
  `mark_processed` in the same transaction as the upsert. A redelivered `suspended` must not
  resurrect a row or double anything.
* **De-entitlement latency is EVENTUAL, by design.** A suspension flips the activation off, but a
  customer already holding an access token keeps it until the token refreshes (≤15 min TTL). The
  projection deliberately does **not** bump the organization's permission-version, which would
  force an immediate global re-check: that is the immediate-cutover option (ADR-0004 Consequences),
  deferred. The manual `activate()` endpoint (services.py) is retained for the non-breaking
  cutover — commerce drives the same row a human can still set by hand.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from app import services
from app.config import settings
from app.models import ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("product.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="product.events")

#: The lifecycle events this service projects. Contract strings from the subscription service —
#: named here, not imported, because one process may only ever hold one service's `app`.
SUBSCRIPTION_EVENTS = (
    "commerce.subscription.trial_started",
    "commerce.subscription.activated",
    "commerce.subscription.renewed",
    "commerce.subscription.upgraded",
    "commerce.subscription.downgraded",
    "commerce.subscription.past_due",
    "commerce.subscription.suspended",
    "commerce.subscription.cancelled",
)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def project_subscription_event(envelope: dict[str, Any]) -> None:
    """Project one `commerce.subscription.*` event onto the activation.

    Bound to every lifecycle routing key; the subscription's `state` in the payload — not the
    event name — decides whether the activation entitles, so renew/upgrade/downgrade all land as
    `active` with no special-casing.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])

    # The GUC is set here, per message, for the whole transaction: the upsert below is a
    # NOBYPASSRLS write against a forced tenant policy and only passes because of this binding.
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("subscription.event.duplicate", event_id=str(event_id))
            return
        await services.project_subscription(
            session,
            organization_id=organization_id,
            product_key=payload["product_key"],
            state=payload["state"],
            trial_ends_at=_parse_dt(payload.get("trial_ends_at")),
            # The envelope's occurred_at, not now(): the out-of-order guard compares event
            # time to event time, so a stale transition folded late is dropped.
            occurred_at=_parse_dt(envelope.get("occurred_at")),
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "subscription.projected",
        event_id=str(event_id),
        organization_id=str(organization_id),
        product_key=payload.get("product_key"),
        state=payload.get("state"),
    )


for _event_type in SUBSCRIPTION_EVENTS:
    consumer.on(_event_type)(project_subscription_event)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
