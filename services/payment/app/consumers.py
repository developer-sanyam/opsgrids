"""Event consumers for payment. Runs as its own process.

payment raises a charge when billing finalizes an invoice. The internal `POST /internal/v1/charges`
is the imperative path (billing calls it directly); this consumer is the event-driven one —
binding `commerce.invoice.finalized` and raising the charge from the event. Either is enough, and
having both means billing can integrate whichever way suits it without payment changing.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as its side effects. The charge create is *also* idempotent at the database (unique
platform key), so a redelivery is a no-op at two layers — belt and braces, because a duplicate
charge is a duplicate debit.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.config import settings
from app.deps import get_provider
from app.models import ProcessedEvent
from app.schemas import ChargeIn
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("payment.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="payment.events")

#: The billing event that produces a charge. A contract string, named here rather than imported —
#: one process may only ever hold one service's `app`.
EVT_INVOICE_FINALIZED = "commerce.invoice.finalized"


@consumer.on(EVT_INVOICE_FINALIZED)
async def charge_on_invoice_finalized(envelope: dict[str, Any]) -> None:
    """Raise a charge for a finalized invoice.

    The charge's `idempotency_key` is derived from the invoice id, so a redelivered
    `commerce.invoice.finalized` — or a finalize that billing retried — never raises a second
    charge. The GUC is set per message via `session_scope(organization_id=...)`, so this
    NOBYPASSRLS write passes the tenant policy.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    provider = get_provider()

    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("invoice.finalized.duplicate", event_id=str(event_id))
            return
        from app import services

        await services.create_charge(
            session,
            provider,
            ChargeIn(
                organization_id=organization_id,
                invoice_id=uuid.UUID(payload["invoice_id"]),
                subscription_id=uuid.UUID(payload["subscription_id"])
                if payload.get("subscription_id")
                else None,
                amount_minor=int(payload["total_minor"]),
                currency=payload["currency"],
                # Platform-supplied idempotency key: one charge per finalized invoice.
                idempotency_key=f"invoice:{payload['invoice_id']}",
            ),
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "invoice.finalized.charged",
        event_id=str(event_id),
        organization_id=str(organization_id),
        invoice_id=payload.get("invoice_id"),
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
