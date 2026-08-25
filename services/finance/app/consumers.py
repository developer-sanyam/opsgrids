"""Event consumers for finance. Runs as its own process.

This is where e-invoicing stops being a button and becomes autopilot. Finalizing a reportable
document stages `finance.compliance.invoice.filing_requested` on the outbox — in the same
transaction as the invoice, so it is sent only once the document is durable — and the handler
below is what picks it up and files it. There is no path where a filing attempt happens inside the
request that raised the invoice: the reporting window is measured in days, and tying a customer's
ability to bill to a government portal's uptime would be trading a real dependency for a
compliance deadline that was never at risk.

Four properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `atmora_finance` (NOBYPASSRLS) and every
  table it writes is under a forced tenant policy. `session_scope(organization_id=...)` binds the
  tenant for the transaction, so the filing row passes the policy's `WITH CHECK`. Without it the
  write is refused — the RLS net, and the reason a consumer must never be started on the admin or
  outbox DSN, where row security would be silently inert.
* **Every handler is idempotent.** Delivery is at-least-once: `already_processed` before acting,
  `mark_processed` in the same transaction as the side effects. A redelivered request must not
  file the same document twice.
* **And so is the *domain*.** Idempotency at the event id is not enough, because two distinct
  events can name the same document — a retry that crossed with a manual filing, a second
  finalize. `services.autofile_invoice` therefore asks `live_registration` **before** it touches
  the portal, and a document already carrying an IRN is never sent again. The portal answers a
  duplicate registration with an error; discovering that by asking is how an integration files
  every invoice twice under load and finds out from its customers.
* **Failure is bounded and it surfaces.** A portal rejection is terminal at the first attempt — the
  same bytes will be refused again. An outage is retried on an exponential backoff up to
  `domain.MAX_FILING_ATTEMPTS`, and when that budget is spent it becomes a terminal `failed` row
  carrying `retry_exhausted`. Nothing loops forever, and nothing fails silently.

The handler never raises for a business situation. A missing invoice, a document that needs no
IRN, one already filed — all are outcomes, logged. Raising would nack the message, and the second
nack dead-letters it; a dead-lettered event in a compliance path is an incident, so it is reserved
for the things that genuinely are one (the database is gone, the process is misconfigured).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import services
from app.config import settings
from app.deps import get_irp_client
from app.models import ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("finance.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="finance.events")


async def file_requested_invoice(envelope: dict[str, Any]) -> None:
    """File one document because the queue asked for it, and record what happened.

    Bound to `finance.compliance.invoice.filing_requested`, which this same service publishes:
    once when a reportable document is finalized, and again to schedule each bounded retry. Going
    out through the broker rather than looping in-process is deliberate — the attempt survives a
    restart, several replicas share the work, and the retry is visible as a queued message rather
    than as a sleeping coroutine nobody can inspect.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    invoice_id = uuid.UUID(payload["invoice_id"])
    attempt = int(payload.get("attempt", 1))
    client = get_irp_client()

    # The GUC is set here, per message, for the whole transaction: the filing row is a
    # NOBYPASSRLS write against a forced tenant policy and only passes because of this binding.
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("finance.filing.duplicate", event_id=str(event_id))
            return
        result = await services.autofile_invoice(
            session,
            services.service_ctx(organization_id),
            client,
            invoice_id=invoice_id,
            attempt=attempt,
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "finance.filing.attempted",
        event_id=str(event_id),
        organization_id=str(organization_id),
        invoice_id=str(invoice_id),
        number=payload.get("number"),
        attempt=attempt,
        outcome=result.outcome,
        next_attempt=result.next_attempt,
        portal=client.name,
    )


consumer.on(services.EVT_INVOICE_FILING_REQUESTED)(file_requested_invoice)


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
