"""Event consumers for product. Runs as its own process.

This is where commerce meets entitlement (ADR-0004 §5). The subscription service owns the
lifecycle and emits a `commerce.subscription.*` event on every transition; here those events are
projected onto the `Activation` row the gateway's 402 gate reads — one source per product the
event's `entitled_products` names (a bundle's members, or just the product), with each
activation recomputed as the OR over its sources (`services.project_subscription`). There is
**no nightly job**: `Activation.entitles` already evaluates trial expiry at read time, so keeping
the row's status in step with the lifecycle is all that entitlement needs.

Three properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `opsgrids_product` (NOBYPASSRLS), and
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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("product.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="product.events")

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own, so
#: it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


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
    # Not a transition: the one-off backfill re-announces a bundle subscription that predates
    # `entitled_products`, with the same payload shape, so its members are finally projected.
    "commerce.subscription.entitlements_resynced",
)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _entitled_products(payload: dict[str, Any]) -> list[str] | None:
    """The members this event speaks for, or None for an event from before the field existed
    (which the projection reads as `[product_key]`). A malformed value is an error, not a
    default: guessing at which products a customer paid for is how a bundle entitled nothing."""
    value = payload.get("entitled_products")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"entitled_products must be a list of product keys, got {value!r}")
    return list(value)


def _subscription_id(envelope: dict[str, Any]) -> uuid.UUID | None:
    """The providing subscription: the payload names it; older events only as the aggregate."""
    raw = envelope["payload"].get("subscription_id")
    if raw is None and envelope.get("aggregate_type") == "subscription":
        raw = envelope.get("aggregate_id")
    return uuid.UUID(str(raw)) if raw else None


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
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("subscription.event.duplicate", event_id=str(event_id))
            return
        members = _entitled_products(payload)
        if members is None and await _is_bundle(session, payload["product_key"]):
            # A bundle event from a subscription-service that predates `entitled_products`. The
            # old fold would project an activation for the bundle's own key, which no route
            # checks — entitling nothing while looking like it did. Record it and wait for the
            # event that names the members (the next transition, or the backfill's resync).
            log.warning(
                "subscription.projection.bundle_without_members",
                event_id=str(event_id),
                organization_id=str(organization_id),
                product_key=payload["product_key"],
                hint="run `python -m app.backfill` in subscription-service",
            )
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
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
            entitled_products=members,
            subscription_id=_subscription_id(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "subscription.projected",
        event_id=str(event_id),
        organization_id=str(organization_id),
        product_key=payload.get("product_key"),
        entitled_products=members,
        state=payload.get("state"),
    )


async def _is_bundle(session: Any, product_key: str) -> bool:
    product = await selectors.get_product(session, product_key)
    return bool(product is not None and product.is_bundle)


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
