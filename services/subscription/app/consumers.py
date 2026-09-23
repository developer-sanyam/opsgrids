"""Event consumers for subscription. Runs as its own process.

This is where the entitlement loop turns (ADR-0004 §5), under the dunning policy ADR-0005 fixes.
The subscription service owns the lifecycle; what moves that lifecycle is what payment-service
observes about money:

* **`commerce.payment.failed`** opens a grace window — `active -> past_due` — and schedules the
  retry ladder. It no longer suspends. Suspension is a *scheduled* act, taken by `app.cron` when
  the window closes, because a failed debit on Indian rails is far more often a mandate that has
  not activated yet or a debit that needs the customer to authenticate by hand than a customer
  who will not pay.
* **`commerce.payment.captured`** closes the window and returns the subscription to `active` —
  from `past_due` during grace, or from `suspended` after it. Recovery at any rung is the whole
  reason `suspended` is not `cancelled`.

Both handlers are idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as the side effects. Redelivery is normal, not exceptional — and both are idempotent
at the domain level too, because at-least-once delivery is not the only source of repetition: two
genuinely distinct failed charges for the same subscription must also not open two grace windows.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config import settings
from app.models import ProcessedEvent
from app.schemas import PaymentOutcome
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("subscription.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="subscription.events")

#: The payment events that close the loop. Contract strings from payment-service, named here
#: rather than imported — one process may only ever hold one service's `app` (ADR-0002).
EVT_PAYMENT_FAILED = "commerce.payment.failed"
EVT_PAYMENT_CAPTURED = "commerce.payment.captured"


def _outcome(payload: dict[str, Any]) -> PaymentOutcome:
    """Parse the collection facts out of a payment event, at the edge and once.

    `amount_minor` and `currency` are copied onto the dunning cycle so a retry can be raised
    without this service reading billing's or payment's schema (ADR-0000 §10). Missing fields are
    tolerated: a charge need not name an invoice, and a cycle with no amount still runs its ladder
    — payment can resolve the amount from the invoice when it raises the retry.
    """
    return PaymentOutcome(
        invoice_id=uuid.UUID(payload["invoice_id"]) if payload.get("invoice_id") else None,
        amount_minor=payload.get("amount_minor"),
        currency=payload.get("currency"),
        failure_reason=(payload.get("failure_reason") or None),
    )


@consumer.on(EVT_PAYMENT_FAILED)
async def open_dunning_on_payment_failed(envelope: dict[str, Any]) -> None:
    """Open — or count against — the grace window for the subscription a failed charge was for.

    The event carries `organization_id` (which tenant to bind) and `subscription_id` (whose window
    this is), stamped by payment when the charge was raised. The GUC is set per message via
    `session_scope(organization_id=...)`, so this NOBYPASSRLS write and the
    `commerce.subscription.past_due` it emits pass the tenant policy. A charge with no subscription
    (`subscription_id` absent) has no lifecycle to move.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    raw_subscription_id = payload.get("subscription_id")

    state = None
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("payment.failed.duplicate", event_id=str(event_id))
            return

        # Imported inside the handler to keep the module import-light and avoid a cycle with the
        # services layer, mirroring how services.require_subscription imports selectors lazily.
        from app import selectors, services

        if raw_subscription_id:
            subscription = await selectors.get_subscription(session, uuid.UUID(raw_subscription_id))
            # Invisible under RLS unless it belongs to this tenant, so another organization's id is
            # simply None here — never a cross-tenant state change.
            if subscription is not None:
                subscription = await services.record_payment_failure(
                    session, subscription, _outcome(payload)
                )
                state = subscription.state
        # Marked processed regardless: an event for a subscription that no longer exists, or a
        # charge with no subscription, is handled — recording the id stops it being retried forever.
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "payment.failed.dunning",
        event_id=str(event_id),
        organization_id=str(organization_id),
        subscription_id=raw_subscription_id,
        state=state,
    )


@consumer.on(EVT_PAYMENT_CAPTURED)
async def recover_on_payment_captured(envelope: dict[str, Any]) -> None:
    """Money arrived: close any open window and return the subscription to `active`.

    Bound to *every* capture, not only the ones dunning asked for, because the customer may well
    pay through a link, a re-run of the original invoice, or the console rather than the retry we
    scheduled — and the platform must not keep a paid-up customer suspended because the money came
    in by a route it did not initiate. A capture for a subscription that is already `active` is
    the ordinary renewal case and moves nothing.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    raw_subscription_id = payload.get("subscription_id")

    state = None
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("payment.captured.duplicate", event_id=str(event_id))
            return

        from app import selectors, services

        if raw_subscription_id:
            subscription = await selectors.get_subscription(session, uuid.UUID(raw_subscription_id))
            if subscription is not None:
                subscription = await services.record_payment_captured(
                    session, subscription, _outcome(payload)
                )
                state = subscription.state
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "payment.captured.recovered",
        event_id=str(event_id),
        organization_id=str(organization_id),
        subscription_id=raw_subscription_id,
        state=state,
    )


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
