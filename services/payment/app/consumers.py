"""Event consumers for payment. Runs as its own process.

Two events raise a charge here, and they are the same act asked for by two different services:

* **`commerce.invoice.finalized`** — billing has something to collect. The internal
  `POST /internal/v1/charges` is the imperative path for the same thing; either is enough, and
  having both means billing can integrate whichever way suits it without payment changing.
* **`commerce.subscription.payment_retry_due`** — subscription's dunning sweep has reached the
  next rung of its ladder (ADR-0005). subscription decides *when* to retry, because it owns the
  lifecycle and the grace window; payment decides *how* to debit, because it owns the provider.
  Neither reaches into the other's schema, and the only thing that crosses is the event.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as the charge *claim*. The charge create is *also* idempotent at the database (unique
platform key), so a redelivery is a no-op at two layers — belt and braces, because a duplicate
charge is a duplicate debit. The retry key is per *attempt* rather than per invoice, for the
reason ADR-0004 §6 makes unavoidable: Razorpay has no idempotency key of its own, so ours is the
only one, and reusing the failed charge's key would deduplicate the retry into the charge that
already failed and debit nobody at all.

**The provider call happens with no transaction open.** `mark_processed` rides in the same
transaction as `services.stage_charge`, not the provider call or `finalize_charge` — a queue
consumer holding a connection and a row open for however long a payment processor takes is exactly
the pool-exhaustion risk this shape exists to avoid. A process that dies after the call but before
`finalize_charge` leaves the charge `created` with no `provider_payment_id`; the event itself will
never be redelivered for it (this transaction already marked it processed), so `app/cron.py`'s
stuck-charge sweep — running in this same process, `app/worker.py` — is what resumes it, not a
queue retry. The call itself runs on a worker thread, bounded
(`services.call_provider_charge_off_loop`), so a slow provider never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_provider
from app.models import ProcessedEvent
from app.schemas import ChargeIn
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("payment.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="payment.events")

#: Same shape as `app.cron.WorkScope` — defined again here, not imported, because `app.consumers`
#: and `app.cron` are independent layers (the service anatomy, CLAUDE.md; `.importlinter`'s
#: `service-layers` contract refuses the import).
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it. Module-level and process-wide on
#: purpose, the same seam the sweeps already use (`app.cron.WorkScope`).
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


#: The events that produce a charge. Contract strings, named here rather than imported — one
#: process may only ever hold one service's `app`.
EVT_INVOICE_FINALIZED = "commerce.invoice.finalized"
EVT_PAYMENT_RETRY_DUE = "commerce.subscription.payment_retry_due"


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

    from app import services

    needs_call = False
    charge = None
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("invoice.finalized.duplicate", event_id=str(event_id))
            return

        if int(payload["total_minor"]) <= 0:
            # A zero invoice — a free plan's period, a usage-only plan with no usage — is a real
            # document with nothing to collect. Presenting a zero debit is a provider call and a
            # bank fee that moves no money, so it is recorded as handled and nothing is raised.
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            log.info(
                "invoice.finalized.nothing_to_collect",
                event_id=str(event_id),
                invoice_id=payload.get("invoice_id"),
            )
            return

        charge, needs_call = await services.stage_charge(
            session,
            provider,
            ChargeIn(
                organization_id=organization_id,
                invoice_id=uuid.UUID(payload["invoice_id"]),
                subscription_id=uuid.UUID(payload["subscription_id"])
                if payload.get("subscription_id")
                else None,
                # The billing run names the product, so a failure event can say which one
                # dunning is about. Absent on a hand-finalized invoice, as before.
                product_key=payload.get("product_key"),
                amount_minor=int(payload["total_minor"]),
                currency=payload["currency"],
                # Platform-supplied idempotency key: one charge per finalized invoice.
                idempotency_key=f"invoice:{payload['invoice_id']}",
            ),
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    if needs_call:
        # No transaction open here: this redelivery is already marked processed, so a crash
        # between this line and the finalize below is resumed by app/cron.py's stuck-charge
        # sweep, never by the queue.
        outcome = await services.attempt_provider_charge(
            services.provider_for_charge(provider, charge), charge
        )
        if (
            outcome.needs_record
        ):  # an unknown outcome records nothing: the lease lapses into the sweep
            async with _session_scope(organization_id) as session:
                await services.record_charge_outcome(
                    session, charge.id, outcome, organization_id=organization_id
                )

    log.info(
        "invoice.finalized.charged",
        event_id=str(event_id),
        organization_id=str(organization_id),
        invoice_id=payload.get("invoice_id"),
    )


@consumer.on(EVT_PAYMENT_RETRY_DUE)
async def charge_on_dunning_retry(envelope: dict[str, Any]) -> None:
    """Raise the next dunning retry debit for a past-due subscription (ADR-0005).

    subscription's sweep has decided this attempt is due; everything needed to debit rides on the
    event — the invoice, the amount, the currency, and the platform idempotency key derived from
    the cycle and the attempt number. payment does not read subscription's schema to find out what
    to charge, and subscription does not read payment's to find out whether it worked: the answer
    comes back as `commerce.payment.captured` or `.failed`, which is the same pair the original
    charge produces, so the ladder's rungs and its first step are handled by one code path.

    A retry with no amount is skipped rather than debited for nothing — a zero-value debit is a
    provider call and a bank fee that collects nothing, and the failure that opened the cycle
    always carries the amount it failed to collect.

    **A retry for an invoice already captured is skipped too, before it ever stages a charge.**
    Each rung carries a fresh idempotency key (see above), so the platform-key dedupe that protects
    every other charge-creating path does not protect this one: nothing stops it raising and
    debiting a second charge for an invoice that is already paid. The gap is real — the charge
    whose failure opened this cycle can itself be captured late, by the same payment, after the
    retry is already queued (ADR-0004, PAY-A round 2) — so the invoice is checked here, immediately
    before staging, not left to `_detect_double_collection` to notice after the money has moved a
    second time.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    amount_minor = payload.get("amount_minor")
    provider = get_provider()

    from app import selectors, services

    needs_call = False
    charge = None
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("dunning.retry.duplicate", event_id=str(event_id))
            return

        raw_invoice_id = payload.get("invoice_id")
        invoice_id = uuid.UUID(raw_invoice_id) if raw_invoice_id else None
        if invoice_id is not None and await selectors.captured_charges_for_invoice(
            session, organization_id, invoice_id
        ):
            # Most likely this charge's own late, same-payment capture landed after the retry was
            # scheduled but before it ran. Debiting again would double-collect the invoice; the
            # event is dropped and marked processed like any other handled delivery, so a
            # redelivery of it is still exactly one no-op.
            log.warning(
                "dunning.retry.invoice_already_paid",
                event_id=str(event_id),
                invoice_id=str(invoice_id),
                subscription_id=payload.get("subscription_id"),
            )
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            return

        if amount_minor:
            charge, needs_call = await services.stage_charge(
                session,
                provider,
                ChargeIn(
                    organization_id=organization_id,
                    invoice_id=uuid.UUID(payload["invoice_id"])
                    if payload.get("invoice_id")
                    else None,
                    subscription_id=uuid.UUID(payload["subscription_id"]),
                    product_key=payload.get("product_key"),
                    amount_minor=int(amount_minor),
                    currency=payload["currency"],
                    # One key per attempt, supplied by subscription so the same rung of the ladder
                    # redelivered twice is still exactly one debit.
                    idempotency_key=payload["idempotency_key"],
                ),
            )
        else:
            log.warning(
                "dunning.retry.no_amount",
                event_id=str(event_id),
                subscription_id=payload.get("subscription_id"),
            )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    if needs_call and charge is not None:
        # No transaction open here — see charge_on_invoice_finalized above for why, and why a
        # crash after this point is the stuck-charge sweep's job, not a queue retry.
        outcome = await services.attempt_provider_charge(
            services.provider_for_charge(provider, charge), charge
        )
        if (
            outcome.needs_record
        ):  # an unknown outcome records nothing: the lease lapses into the sweep
            async with _session_scope(organization_id) as session:
                await services.record_charge_outcome(
                    session, charge.id, outcome, organization_id=organization_id
                )

    log.info(
        "dunning.retry.charged",
        event_id=str(event_id),
        organization_id=str(organization_id),
        subscription_id=payload.get("subscription_id"),
        attempt_no=payload.get("attempt_no"),
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
