"""Event consumers for insights. Runs as its own process (`bin/worker`) or folded into the API
process (`app/main.py`, `RUN_CONSUMERS_IN_API`).

I1 binds the two Core (account) routing keys `docs/insights/events.md` §2 names for this slice:
`core.org.created` and `core.org.updated`, both folding into `org_profile`
(`app/services.upsert_org_profile`). Every handler is idempotent: `already_processed` before
acting, `mark_processed` in the same transaction as the fold — data-model.md §1.1's version
guard already makes a duplicate apply harmless, but `processed_events` is still the platform's
own ledger every consumer keeps (ADR-0020 risk 8).

I2 adds finance's four (`docs/insights/events.md` §2): `finance.billing.invoice.issued` and
`.payment.received`/`.purchase.recorded` fold full state; `finance.billing.invoice.paid` is a
**partial** transition, applied as an `UPDATE ... WHERE`, never an `INSERT`
(`app/services.apply_invoice_paid`) — a missing target row logs
`insights.fold.orphan_transition` and acks, exactly as data-model.md §1.1 describes for a partial
event whose creating row has not landed yet.

**Binding happens the moment `consumer.run()` (or `ensure_queue_bound`, below) declares the
queue** — before the backfill runner starts discovering organizations (ADR-0020 §3: "the
consumer declares insights.events and binds every routing key before anything else runs"),
which is what closes the gap between an event committed before the backfill's export read and
one committed after it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import Any

import aio_pika
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ProcessedEvent
from app.services import (
    apply_invoice_paid,
    invoice_fact_exists,
    upsert_invoice_fact_from_event,
    upsert_org_profile,
    upsert_payment_fact_from_event,
    upsert_purchase_fact_from_event,
)
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import DLX, EXCHANGE, EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("insights.consumers")

#: Named once so `app/backfill.py`'s `ensure_queue_bound` (below) — which must declare and bind
#: the identical queue before the backfill runner's discovery starts — imports rather than
#: repeats the string literal.
QUEUE_NAME = "insights.events"

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name=QUEUE_NAME)

#: Same shape as every other service's `app.cron`/`app.backfill` `WorkScope` — defined again
#: here, not imported, because `app.consumers` and `app.backfill` are independent layers.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine —
#: standalone (`python -m app.consumers`/`app.worker`, `bin/worker`). `app/main.py` swaps it to
#: a bulkhead scope before starting this consumer as a supervised background task inside the API
#: process, on `RUN_CONSUMERS_IN_API`.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


#: Contract strings from account-service, named here rather than imported (CLAUDE.md: one
#: process may hold only one service's top-level `app`).
EVT_ORG_CREATED = "core.org.created"
EVT_ORG_UPDATED = "core.org.updated"

CORE_EVENTS = (EVT_ORG_CREATED, EVT_ORG_UPDATED)

#: Contract strings from finance-service (ADR-0020 slice I2), named here for the same reason.
EVT_INVOICE_ISSUED = "finance.billing.invoice.issued"
EVT_INVOICE_PAID = "finance.billing.invoice.paid"
EVT_PAYMENT_RECEIVED = "finance.billing.payment.received"
EVT_PURCHASE_RECORDED = "finance.billing.purchase.recorded"

FINANCE_EVENTS = (EVT_INVOICE_ISSUED, EVT_INVOICE_PAID, EVT_PAYMENT_RECEIVED, EVT_PURCHASE_RECORDED)

#: Every routing key this queue binds — appended to as later slices land theirs (ADR-0020 §6);
#: `ensure_queue_bound` below and `consumer.run()` both bind exactly this list.
BOUND_EVENTS = CORE_EVENTS + FINANCE_EVENTS


def _tenant_from_envelope(envelope: dict[str, Any]) -> uuid.UUID | None:
    """Refuses a payload whose own `organization_id` disagrees with the envelope's — the same
    guard `services/approval/app/consumers.py` uses, for the same reason: `session_scope` binds
    whatever this returns, and row-level security trusts the GUC it is given. Logged and
    dropped, never raised: a producer bug here will not be fixed by redelivering the same
    message."""
    payload = envelope.get("payload") or {}
    envelope_org = envelope.get("organization_id")
    payload_org = payload.get("organization_id")
    if envelope_org is None or payload_org is None or str(envelope_org) != str(payload_org):
        log.error(
            "insights.event.tenant_mismatch",
            event_id=envelope.get("event_id"),
            event_type=envelope.get("event_type"),
        )
        return None
    return uuid.UUID(str(payload_org))


def _occurred_at(envelope: dict[str, Any]) -> datetime:
    """The event version (data-model.md §1.1): the envelope's own `occurred_at`, which is the
    outbox row's `created_at` — the source transaction's `now()`."""
    raw = envelope.get("occurred_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            log.warning("insights.event.malformed_occurred_at", raw=str(raw)[:100])
    return datetime.now(UTC)


async def _fold_org_profile(envelope: dict[str, Any]) -> None:
    """Shared body for `core.org.created`/`core.org.updated` — both carry the organization's
    full settings state after the change (`docs/insights/events.md` §2). A field missing from an
    un-upgraded producer **raises**, which dead-letters the message rather than silently writing
    a wrong default (`docs/insights/events.md` §1) — the next `core.org.updated` or the account
    export corrects it, per the platform's own self-healing rule.
    """
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info(
                "insights.org_profile.duplicate", event_id=str(event_id), event_type=event_type
            )
            return

        applied = await upsert_org_profile(
            session,
            organization_id=organization_id,
            timezone=str(payload["timezone"]),
            currency=str(payload["currency"]),
            fiscal_year_start_month=int(payload["fiscal_year_start_month"]),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.org_profile.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_ORG_CREATED)
async def on_org_created(envelope: dict[str, Any]) -> None:
    await _fold_org_profile(envelope)


@consumer.on(EVT_ORG_UPDATED)
async def on_org_updated(envelope: dict[str, Any]) -> None:
    await _fold_org_profile(envelope)


def _date(payload: dict[str, Any], key: str) -> date:
    """Business dates travel on the wire as ISO strings (never a timezone) — `docs/insights/adr
    §8`: "business dates are used as they are". A missing or malformed one is exactly the
    "field insights needs" case events.md §1 describes: it raises, and the message dead-letters
    rather than storing a guessed date."""
    return date.fromisoformat(str(payload[key]))


async def _fold_invoice_issued(envelope: dict[str, Any]) -> None:
    """`finance.billing.invoice.issued` — full state (`docs/insights/events.md` §2:
    `invoice_facts`, i.e. `revenue`, `invoices_issued`, feed)."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.invoice_fact.duplicate", event_id=str(event_id))
            return
        applied = await upsert_invoice_fact_from_event(
            session,
            organization_id=organization_id,
            id=uuid.UUID(str(payload["invoice_id"])),
            number=str(payload["number"]),
            document_type=str(payload["document_type"]),
            status=str(payload["status"]),
            issued_date=_date(payload, "issued_date"),
            due_date=_date(payload, "due_date"),
            currency=str(payload["currency"]),
            subtotal_minor=int(payload["subtotal_minor"]),
            tax_minor=int(payload["tax_minor"]),
            total_minor=int(payload["total_minor"]),
            customer_ref=uuid.UUID(str(payload["customer_id"])),
            source_created_at=_occurred_at(envelope),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.invoice_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


async def _fold_invoice_paid(envelope: dict[str, Any]) -> None:
    """`finance.billing.invoice.paid` — a **partial** transition (data-model.md §1.1): applied
    only if `invoice_facts` already has the row. A missing row logs
    `insights.fold.orphan_transition` and acks — the creating `invoice.issued` fold is either
    still in flight or was lost, and the weekly re-export repairs it either way."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.invoice_fact.duplicate", event_id=str(event_id))
            return
        invoice_id = uuid.UUID(str(payload["invoice_id"]))
        # `paid_at` is the envelope's own `occurred_at` (data-model.md §3: "the settling
        # payment's own occurred_at"), never `payload["paid_date"]` — that field is the business
        # date the schema commits (events.md §2), a `date` with no time, and is not what this
        # column stores. Both are the same instant `record_payment` staged the event under.
        applied = await apply_invoice_paid(
            session,
            organization_id=organization_id,
            id=invoice_id,
            status="paid",
            paid_at=_occurred_at(envelope),
            source_updated_at=_occurred_at(envelope),
        )
        if not applied and not await invoice_fact_exists(
            session, organization_id=organization_id, id=invoice_id
        ):
            log.warning(
                "insights.fold.orphan_transition",
                event_id=str(event_id),
                event_type=event_type,
                organization_id=str(organization_id),
                entity="finance.invoices",
            )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.invoice_fact.paid_processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


async def _fold_payment_received(envelope: dict[str, Any]) -> None:
    """`finance.billing.payment.received` — full state (`docs/insights/events.md` §2:
    `payment_facts`, i.e. `payments_received`, feed)."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.payment_fact.duplicate", event_id=str(event_id))
            return
        applied = await upsert_payment_fact_from_event(
            session,
            organization_id=organization_id,
            id=uuid.UUID(str(payload["payment_id"])),
            reference=str(payload["reference"]),
            received_date=_date(payload, "received_date"),
            method=str(payload["method"]),
            currency=str(payload["currency"]),
            amount_minor=int(payload["amount_minor"]),
            source_created_at=_occurred_at(envelope),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.payment_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


async def _fold_purchase_recorded(envelope: dict[str, Any]) -> None:
    """`finance.billing.purchase.recorded` — full state (`docs/insights/events.md` §2:
    `purchase_facts`, i.e. `purchases_recorded`)."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.purchase_fact.duplicate", event_id=str(event_id))
            return
        applied = await upsert_purchase_fact_from_event(
            session,
            organization_id=organization_id,
            id=uuid.UUID(str(payload["purchase_document_id"])),
            number=str(payload["number"]),
            document_type=str(payload["document_type"]),
            document_date=_date(payload, "document_date"),
            currency=str(payload["currency"]),
            taxable_minor=int(payload["taxable_minor"]),
            tax_minor=int(payload["tax_minor"]),
            total_minor=int(payload["total_minor"]),
            source_created_at=_occurred_at(envelope),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.purchase_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_INVOICE_ISSUED)
async def on_invoice_issued(envelope: dict[str, Any]) -> None:
    await _fold_invoice_issued(envelope)


@consumer.on(EVT_INVOICE_PAID)
async def on_invoice_paid(envelope: dict[str, Any]) -> None:
    await _fold_invoice_paid(envelope)


@consumer.on(EVT_PAYMENT_RECEIVED)
async def on_payment_received(envelope: dict[str, Any]) -> None:
    await _fold_payment_received(envelope)


@consumer.on(EVT_PURCHASE_RECORDED)
async def on_purchase_recorded(envelope: dict[str, Any]) -> None:
    await _fold_purchase_recorded(envelope)


async def ensure_queue_bound(amqp_url: str) -> None:
    """Declare and bind `insights.events` before the backfill runner's discovery makes its first
    export call (ADR-0020 §3). Declared **identically** to `EventConsumer.run()`
    (`platform_core/events.py`) — the same queue name, `durable=True`, dead-letter exchange and
    DLQ binding — so a live worker's own declare on the same queue never collides with this one
    (a mismatched declare is a 406 PRECONDITION_FAILED, on whichever side declares second). This
    does not start consuming; it only ensures the queue and its bindings exist, exactly what
    `services/approval/app/backfill.py:ensure_queue_bound` already does for the same reason.
    """
    connection = await aio_pika.connect_robust(amqp_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlx = await channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
        dlq = await channel.declare_queue(f"{QUEUE_NAME}.dlq", durable=True)
        await dlq.bind(dlx, routing_key="#")

        queue = await channel.declare_queue(
            QUEUE_NAME, durable=True, arguments={"x-dead-letter-exchange": DLX}
        )
        for routing_key in BOUND_EVENTS:
            await queue.bind(exchange, routing_key=routing_key)
        log.info("insights.backfill.queue_bound", routing_keys=list(BOUND_EVENTS))
    finally:
        await connection.close()


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    log.info("insights.consumer.bindings", routing_keys=list(BOUND_EVENTS))
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
