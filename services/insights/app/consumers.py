"""Event consumers for insights. Runs as its own process (`bin/worker`) or folded into the API
process (`app/main.py`, `RUN_CONSUMERS_IN_API`).

I1 binds the two Core (account) routing keys `docs/insights/events.md` §2 names for this slice:
`core.org.created` and `core.org.updated`, both folding into `org_profile`
(`app/services.upsert_org_profile`). I3 adds the four `crm.pipeline.deal.*` keys (`deal_facts`)
and I4 the three `crm.lead.*` and three `crm.book.company.*` keys (`lead_facts`/`company_facts`).
Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as the fold — data-model.md §1.1's version guard already makes a duplicate apply
harmless, but `processed_events` is still the platform's own ledger every consumer keeps
(ADR-0020 risk 8).

**Binding happens the moment `consumer.run()` (or `ensure_queue_bound`, below) declares the
queue** — before the backfill runner starts discovering organizations (ADR-0020 §3: "the
consumer declares insights.events and binds every routing key before anything else runs"),
which is what closes the gap between an event committed before the backfill's export read and
one committed after it. `ALL_ROUTING_KEYS` is the append-only union every slice's own `*_EVENTS`
tuple joins — a later slice adds one more addend, never edits an earlier one.
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
from app.models import DealFact, LeadFact, ProcessedEvent
from app.services import (
    apply_deal_stage_changed,
    apply_lead_converted,
    mark_company_deleted,
    mark_deal_deleted,
    upsert_company_fact_from_event,
    upsert_deal_fact_from_event,
    upsert_lead_fact_from_event,
    upsert_org_profile,
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

#: Contract strings from deal-service (I3).
EVT_DEAL_CREATED = "crm.pipeline.deal.created"
EVT_DEAL_UPDATED = "crm.pipeline.deal.updated"
EVT_DEAL_STAGE_CHANGED = "crm.pipeline.deal.stage_changed"
EVT_DEAL_DELETED = "crm.pipeline.deal.deleted"

DEAL_EVENTS = (EVT_DEAL_CREATED, EVT_DEAL_UPDATED, EVT_DEAL_STAGE_CHANGED, EVT_DEAL_DELETED)

#: Contract strings from lead-service and customer-service (I4).
EVT_LEAD_CREATED = "crm.lead.created"
EVT_LEAD_UPDATED = "crm.lead.updated"
EVT_LEAD_CONVERTED = "crm.lead.converted"
EVT_COMPANY_CREATED = "crm.book.company.created"
EVT_COMPANY_UPDATED = "crm.book.company.updated"
EVT_COMPANY_DELETED = "crm.book.company.deleted"

LEAD_EVENTS = (EVT_LEAD_CREATED, EVT_LEAD_UPDATED, EVT_LEAD_CONVERTED)
COMPANY_EVENTS = (EVT_COMPANY_CREATED, EVT_COMPANY_UPDATED, EVT_COMPANY_DELETED)

#: The append-only union `ensure_queue_bound`/`main` bind — a later slice (I5+) adds one more
#: addend here, never edits an earlier one (ADR-0020 §3: every routing key is bound by name,
#: never a wildcard, so a new event never lands in this queue by accident).
ALL_ROUTING_KEYS = CORE_EVENTS + DEAL_EVENTS + LEAD_EVENTS + COMPANY_EVENTS


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


# --------------------------------------------------------------------- deal_facts (I3)


async def _fold_deal_full_state(envelope: dict[str, Any]) -> None:
    """Shared body for `crm.pipeline.deal.created`/`.updated` — both carry the deal's full
    state (`docs/insights/events.md` §2)."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.deal_fact.duplicate", event_id=str(event_id), event_type=event_type)
            return

        closed_at_raw = payload.get("closed_at")
        applied = await upsert_deal_fact_from_event(
            session,
            organization_id=organization_id,
            deal_id=uuid.UUID(str(payload["deal_id"])),
            name=str(payload["name"]),
            stage=str(payload["stage"]),
            value_minor=int(payload["value_minor"]),
            currency=str(payload["currency"]),
            owner_ref=uuid.UUID(str(payload["owner_id"])),
            company_ref=uuid.UUID(str(payload["company_id"])),
            close_date=date.fromisoformat(str(payload["close_date"])),
            probability=int(payload["probability"]),
            closed_at=datetime.fromisoformat(closed_at_raw) if closed_at_raw else None,
            source_created_at=(
                datetime.fromisoformat(str(payload["created_at"]))
                if "created_at" in payload
                else _occurred_at(envelope)
            ),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.deal_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_DEAL_CREATED)
async def on_deal_created(envelope: dict[str, Any]) -> None:
    await _fold_deal_full_state(envelope)


@consumer.on(EVT_DEAL_UPDATED)
async def on_deal_updated(envelope: dict[str, Any]) -> None:
    await _fold_deal_full_state(envelope)


@consumer.on(EVT_DEAL_STAGE_CHANGED)
async def on_deal_stage_changed(envelope: dict[str, Any]) -> None:
    """A **partial** event (data-model.md §1.1): `UPDATE`-only, never creates a fact. An
    orphan — the row is not there because the `created` fold was lost or is still in flight —
    logs `insights.fold.orphan_transition` and acks; the weekly re-export repairs it."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.deal_fact.duplicate", event_id=str(event_id), event_type=event_type)
            return

        deal_id = uuid.UUID(str(payload["deal_id"]))
        closed_at_raw = payload.get("closed_at")
        source_updated_at = _occurred_at(envelope)
        applied = await apply_deal_stage_changed(
            session,
            organization_id=organization_id,
            deal_id=deal_id,
            stage=str(payload["to"]),
            name=str(payload["name"]),
            value_minor=int(payload["value_minor"]),
            currency=str(payload["currency"]),
            owner_ref=uuid.UUID(str(payload["owner_id"])),
            probability=int(payload["probability"]),
            closed_at=datetime.fromisoformat(closed_at_raw) if closed_at_raw else None,
            source_updated_at=source_updated_at,
        )
        if not applied:
            exists = await session.get(
                DealFact, {"organization_id": organization_id, "id": deal_id}
            )
            if exists is None:
                log.warning(
                    "insights.fold.orphan_transition",
                    event_id=str(event_id),
                    event_type=event_type,
                    organization_id=str(organization_id),
                    entity_id=str(deal_id),
                )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.deal_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_DEAL_DELETED)
async def on_deal_deleted(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.deal_fact.duplicate", event_id=str(event_id), event_type=event_type)
            return

        applied = await mark_deal_deleted(
            session,
            organization_id=organization_id,
            deal_id=uuid.UUID(str(payload["deal_id"])),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.deal_fact.deleted",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


# --------------------------------------------------------------------- lead_facts (I4)


async def _fold_lead_full_state(envelope: dict[str, Any]) -> None:
    """Shared body for `crm.lead.created`/`.updated` — both carry the lead's full state."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.lead_fact.duplicate", event_id=str(event_id), event_type=event_type)
            return

        owner_raw = payload.get("owner_id")
        converted_raw = payload.get("converted_at")
        occurred_at = _occurred_at(envelope)
        applied = await upsert_lead_fact_from_event(
            session,
            organization_id=organization_id,
            lead_id=uuid.UUID(str(payload["lead_id"])),
            status=str(payload["status"]),
            source=str(payload["source"]),
            owner_ref=uuid.UUID(str(owner_raw)) if owner_raw else None,
            converted_at=datetime.fromisoformat(converted_raw) if converted_raw else None,
            source_created_at=occurred_at,
            source_updated_at=occurred_at,
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.lead_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_LEAD_CREATED)
async def on_lead_created(envelope: dict[str, Any]) -> None:
    await _fold_lead_full_state(envelope)


@consumer.on(EVT_LEAD_UPDATED)
async def on_lead_updated(envelope: dict[str, Any]) -> None:
    await _fold_lead_full_state(envelope)


@consumer.on(EVT_LEAD_CONVERTED)
async def on_lead_converted(envelope: dict[str, Any]) -> None:
    """A **partial** event — no `source` (`docs/insights/events.md` §2) — so this is
    `UPDATE`-only, exactly like `on_deal_stage_changed`."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("insights.lead_fact.duplicate", event_id=str(event_id), event_type=event_type)
            return

        lead_id = uuid.UUID(str(payload["lead_id"]))
        owner_raw = payload.get("owner_id")
        applied = await apply_lead_converted(
            session,
            organization_id=organization_id,
            lead_id=lead_id,
            owner_ref=uuid.UUID(str(owner_raw)) if owner_raw else None,
            converted_at=datetime.fromisoformat(str(payload["converted_at"])),
            source_updated_at=_occurred_at(envelope),
        )
        if not applied:
            exists = await session.get(
                LeadFact, {"organization_id": organization_id, "id": lead_id}
            )
            if exists is None:
                log.warning(
                    "insights.fold.orphan_transition",
                    event_id=str(event_id),
                    event_type=event_type,
                    organization_id=str(organization_id),
                    entity_id=str(lead_id),
                )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.lead_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


# --------------------------------------------------------------------- company_facts (I4)


async def _fold_company_full_state(envelope: dict[str, Any]) -> None:
    """Shared body for `crm.book.company.created`/`.updated` — both carry the company's full
    state."""
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info(
                "insights.company_fact.duplicate", event_id=str(event_id), event_type=event_type
            )
            return

        occurred_at = _occurred_at(envelope)
        created_raw = payload.get("created_at")
        applied = await upsert_company_fact_from_event(
            session,
            organization_id=organization_id,
            company_id=uuid.UUID(str(payload["company_id"])),
            name=str(payload["name"]),
            status=str(payload["status"]),
            owner_ref=uuid.UUID(str(payload["owner_id"])),
            source_created_at=datetime.fromisoformat(created_raw) if created_raw else occurred_at,
            source_updated_at=occurred_at,
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.company_fact.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_COMPANY_CREATED)
async def on_company_created(envelope: dict[str, Any]) -> None:
    await _fold_company_full_state(envelope)


@consumer.on(EVT_COMPANY_UPDATED)
async def on_company_updated(envelope: dict[str, Any]) -> None:
    await _fold_company_full_state(envelope)


@consumer.on(EVT_COMPANY_DELETED)
async def on_company_deleted(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info(
                "insights.company_fact.duplicate", event_id=str(event_id), event_type=event_type
            )
            return

        applied = await mark_company_deleted(
            session,
            organization_id=organization_id,
            company_id=uuid.UUID(str(payload["company_id"])),
            source_updated_at=_occurred_at(envelope),
        )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "insights.company_fact.deleted",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        applied=applied,
    )


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
        for routing_key in ALL_ROUTING_KEYS:
            await queue.bind(exchange, routing_key=routing_key)
        log.info("insights.backfill.queue_bound", routing_keys=list(ALL_ROUTING_KEYS))
    finally:
        await connection.close()


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    log.info("insights.consumer.bindings", routing_keys=list(ALL_ROUTING_KEYS))
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
