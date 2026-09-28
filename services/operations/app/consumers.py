"""Event consumers for operations. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.

Two fulfilment handlers (ADR-0024 §1, ADR-0025 §3; `docs/fulfilment/plan.md` F3):

* **`expire_offer_requested`** — bound to `operations.fulfilment.offer.expire_requested`, the
  target event `app.fulfilment.services.offer_task` asks `services/scheduler` to relay back once
  an offer's timeout is up (`platform_core.events.schedule_event`, ADR-0015). Fires the exact
  conditional update ADR-0025 §3 specifies; racing an accept that already landed is a silent
  no-op, never an error.
* **`order_cancelled`** — bound to `operations.orders.order.cancelled`, `app/orders/`'s own
  event. This is how a cancelled order's fan-out reaches its open pick tasks: `app/orders/`
  never imports `app/fulfilment/` (ADR-0024 §1, enforced by `.importlinter`), so the only path
  from "order cancelled" to "its tasks are cancelled too" is this round trip through the outbox.

One more, F4's own (ADR-0025 §1's billing gate; Fulfilment plan F4):

* **`finance_invoice_issued`** — bound to `finance.orders.invoice.issued`, a **different
  service's** own event (`services/finance/app/services.py::EVT_ORDER_INVOICE_ISSUED`, already
  deployed). There is no cross-service import to bind against — the routing key is copied by hand
  onto `app.orders.services.EVT_FINANCE_INVOICE_ISSUED`, the same contract-not-code discipline
  `EVT_OFFER_EXPIRE_REQUESTED` already documents for `services/scheduler`. Writes the order's own
  `invoice_id` fact, which `app.orders.services.check_billing_gate` reads.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.fulfilment import services as fulfilment_services
from app.models import ProcessedEvent
from app.orders import services as order_services
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("operations.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="operations.events")

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own to
#: import it from, so it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope both handlers below open. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `Settings.run_consumers_in_api` (MAJOR 2, security review) — so a folded consumer's per-message
#: transactions can neither take a connection from request traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point both handlers above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


async def expire_offer_requested(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    offer_id = uuid.UUID(str(payload["offer_id"]))

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("fulfilment.offer_expiry.duplicate", event_id=str(event_id))
            return
        ctx = fulfilment_services.service_ctx(organization_id)
        fired = await fulfilment_services.expire_offer(session, ctx, offer_id=offer_id)
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "fulfilment.offer_expiry.handled",
        event_id=str(event_id),
        offer_id=str(offer_id),
        fired=fired,
    )


consumer.on(fulfilment_services.EVT_OFFER_EXPIRE_REQUESTED)(expire_offer_requested)


async def order_cancelled(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    order_id = uuid.UUID(str(payload["order_id"]))

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("fulfilment.order_cancelled.duplicate", event_id=str(event_id))
            return
        ctx = fulfilment_services.service_ctx(organization_id)
        cancelled = await fulfilment_services.cancel_tasks_for_order(
            session, ctx, order_id=order_id
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "fulfilment.order_cancelled.handled",
        event_id=str(event_id),
        order_id=str(order_id),
        cancelled_tasks=len(cancelled),
    )


consumer.on("operations.orders.order.cancelled")(order_cancelled)


async def finance_invoice_issued(envelope: dict[str, Any]) -> None:
    """Bound to `finance.orders.invoice.issued` — see this module's own docstring for why the
    routing key is a copied string, not an import. **The GUC is set from the envelope's own
    `organization_id`, never the payload's** — the payload is JSON this consumer cannot prove
    nothing altered in transit, while the envelope's column is one finance's own row-level
    security enforced; a mismatch is marked processed (so it is never retried into a loop) and
    logged as an error rather than trusted, the identical guard
    `services/finance/app/consumers.py::invoice_requested_from_order` already documents for the
    same cross-service shape.
    """
    event_id = uuid.UUID(envelope["event_id"])
    envelope_organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    payload_organization_id = uuid.UUID(str(payload["organization_id"]))

    if payload_organization_id != envelope_organization_id:
        async with _session_scope(envelope_organization_id) as session:
            if await already_processed(session, ProcessedEvent, event_id):
                log.info("orders.invoice_issued.duplicate", event_id=str(event_id))
                return
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
        log.error(
            "orders.invoice_issued.organization_mismatch",
            event_id=str(event_id),
            envelope_organization_id=str(envelope_organization_id),
            payload_organization_id=str(payload_organization_id),
        )
        return

    order_id = uuid.UUID(str(payload["order_id"]))
    invoice_id = uuid.UUID(str(payload["invoice_id"]))
    invoice_number = str(payload["invoice_number"])

    async with _session_scope(envelope_organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("orders.invoice_issued.duplicate", event_id=str(event_id))
            return
        ctx = order_services.service_ctx(envelope_organization_id)
        recorded = await order_services.record_invoice_issued(
            session,
            ctx,
            order_id=order_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "orders.invoice_issued.handled",
        event_id=str(event_id),
        order_id=str(order_id),
        invoice_id=str(invoice_id),
        recorded=recorded,
    )


consumer.on(order_services.EVT_FINANCE_INVOICE_ISSUED)(finance_invoice_issued)


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
