"""Event consumers for finance. Runs as its own process.

This is where e-invoicing stops being a button and becomes autopilot. Finalizing a reportable
document stages `finance.compliance.invoice.filing_requested` on the outbox — in the same
transaction as the invoice, so it is sent only once the document is durable — and the handler
below is what picks it up and files it. There is no path where a filing attempt happens inside the
request that raised the invoice: the reporting window is measured in days, and tying a customer's
ability to bill to a government portal's uptime would be trading a real dependency for a
compliance deadline that was never at risk.

Four properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `opsgrids_finance` (NOBYPASSRLS) and every
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
* **A retry waits for its own `not_before` (ADR-0015).** `services.request_filing` hands a future
  `not_before` to the scheduler instead of publishing this event directly, so in normal operation
  this handler never sees one still in the future. It checks anyway, as defence in depth against a
  hand-redelivered message or a DLQ replay: acting early would jump a backoff that exists to stop
  hammering a struggling portal. See `services.rearm_early_filing_request`. The check forgives
  `domain.FILING_EARLY_ARRIVAL_TOLERANCE` of clock skew between the scheduler's own clock and
  this pod's, so ordinary scheduling latency is never mistaken for an early arrival.
* **The tenant is bound from the envelope, never from the payload (ADR-0015 review).** A
  `filing_requested` message can arrive two ways: published directly by this service (envelope
  and payload always agree, since both come from the same `ctx.organization_id`), or relayed
  through the scheduler, whose own envelope names the *scheduler's* row's organization —
  trustworthy, because it is a column the scheduler's own row-level security enforced — while
  the payload inside it is JSON `schedule_event` was handed, and this consumer has no way to
  prove nothing between here and there altered it. If they ever disagree, this refuses rather
  than trusting whichever one happens to look more convenient.

The handler never raises for a business situation. A missing invoice, a document that needs no
IRN, one already filed — all are outcomes, logged. Raising would nack the message, and the second
nack dead-letters it; a dead-lettered event in a compliance path is an incident, so it is reserved
for the things that genuinely are one (the database is gone, the process is misconfigured).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app import domain, services
from app.config import settings
from app.deps import get_irp_client
from app.models import ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.errors import BusinessRuleProblem
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
    envelope_organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload_organization_id = uuid.UUID(payload["organization_id"])
    invoice_id = uuid.UUID(payload["invoice_id"])
    attempt = int(payload.get("attempt", 1))
    not_before_raw = payload.get("not_before")
    not_before = datetime.fromisoformat(not_before_raw) if not_before_raw else None
    client = get_irp_client()

    if payload_organization_id != envelope_organization_id:
        # The envelope's own organization is a column its producer's row-level security
        # enforced; the payload is JSON carried inside it, and this consumer cannot prove
        # nothing altered it in between. Refuse rather than bind a session to whichever one
        # happens to look right — the GUC below is what makes every write in this handler
        # tenant-scoped, and it must never be handed an organization this message cannot back.
        async with session_scope(organization_id=envelope_organization_id) as session:
            if await already_processed(session, ProcessedEvent, event_id):
                log.info("finance.filing.duplicate", event_id=str(event_id))
                return
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
        log.error(
            "finance.filing.organization_mismatch",
            event_id=str(event_id),
            envelope_organization_id=str(envelope_organization_id),
            payload_organization_id=str(payload_organization_id),
        )
        return

    organization_id = envelope_organization_id

    # The GUC is set here, per message, for the *claim* transaction: the filing row is a
    # NOBYPASSRLS write against a forced tenant policy and only passes because of this binding.
    # `mark_processed` runs here too, not after the call below — a redelivery of this same event
    # must not re-register a document the first delivery may already have registered, and it is
    # the *claim* that decides that (via `live_registration`), not the call. A process that dies
    # after this commits but before the call returns or `record_autofile_result` runs leaves no
    # filing row behind at all (see `stage_registration_attempt`); `app/cron.py`'s filing-reclaim
    # sweep is what resumes such an invoice, not a queue retry.
    #
    # Note this `datetime.now(UTC)` is a *different* clock from the one `claim_autofile_attempt`
    # resolves internally (`app.services`'s own, unpatched in tests that freeze this module's):
    # this one only ever decides whether a message arrived early, never what gets written.
    claim: services.AutofileClaim | None = None
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("finance.filing.duplicate", event_id=str(event_id))
            return

        if not_before is not None and (
            (not_before - datetime.now(UTC)) > domain.FILING_EARLY_ARRIVAL_TOLERANCE
        ):
            # Defence in depth (ADR-0015 §5): the scheduler is supposed to hold this until
            # `not_before`, so arriving early is not the ordinary case. Re-arm rather than act.
            ctx = services.service_ctx(organization_id)
            services.rearm_early_filing_request(
                session,
                ctx,
                invoice_id=invoice_id,
                event_id=event_id,
                attempt=attempt,
                not_before=not_before,
                payload=payload,
            )
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            log.warning(
                "finance.filing.early_arrival",
                event_id=str(event_id),
                organization_id=str(organization_id),
                invoice_id=str(invoice_id),
                attempt=attempt,
                not_before=not_before.isoformat(),
            )
            return

        claim = await services.claim_autofile_attempt(
            session, services.service_ctx(organization_id), invoice_id, attempt=attempt
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    if claim.outcome is not None:
        result = claim.outcome
    else:
        if claim.invoice is None or claim.irp_payload is None:  # pragma: no cover
            raise BusinessRuleProblem("a claim that did not resolve to an outcome named no invoice")

        # No transaction open here at all (D3): the claim above already committed, and the
        # record transaction below opens only after the portal has answered.
        call_outcome = services.call_irp_register(client, claim.irp_payload)

        async with session_scope(organization_id=organization_id) as session:
            result = await services.record_autofile_result(
                session,
                services.service_ctx(organization_id),
                claim.invoice.id,
                call_outcome,
                attempt=attempt,
                retryable=claim.retryable,
                at=claim.at,
                client_name=client.name,
            )

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
