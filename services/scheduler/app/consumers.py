"""Event consumers for scheduler. Runs as its own process.

One binding: `platform.schedule.requested` (ADR-0015). Every handler is idempotent:
`already_processed` before acting, `mark_processed` in the same transaction as the side effect —
redelivery is normal, not exceptional, and so is the *domain*: `services.ingest_schedule_request`
also guards on `(organization_id, idempotency_key)` at the database, because two distinct events
can legitimately ask for the same delayed delivery (finance's own early-arrival defence does this
on purpose, and a plain redelivery of this very event is the ordinary case the ledger already
covers).

A payload that fails to parse is logged and dropped, never raised: `EventConsumer` redelivers
once and then dead-letters, and a malformed payload does not become processable by retrying it
(the same discipline `approval/app/consumers.py`'s `_hierarchy_state` documents).
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
from platform_core.events import (
    PLATFORM_SCHEDULE_REQUESTED,
    EventConsumer,
    already_processed,
    mark_processed,
)
from platform_core.logging import get_logger

log = get_logger("scheduler.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="scheduler.events")


def _parse(envelope: dict[str, Any]) -> services.ScheduleRequest | None:
    """Extract a `ScheduleRequest` out of one envelope, or `None` to drop it.

    `None` covers a payload this service does not recognise: missing fields, a `due_at` that does
    not parse, a target whose own shape is wrong. All are logged and dropped rather than raised —
    see the module docstring for why.
    """
    payload = envelope.get("payload") or {}
    target = payload.get("target") or {}
    try:
        return services.ScheduleRequest(
            idempotency_key=str(payload["idempotency_key"]),
            due_at=datetime.fromisoformat(str(payload["due_at"])),
            requested_event_id=uuid.UUID(str(envelope["event_id"])),
            correlation_id=envelope.get("correlation_id"),
            target_event_type=str(target["event_type"]),
            target_aggregate_type=str(target["aggregate_type"]),
            target_aggregate_id=uuid.UUID(str(target["aggregate_id"])),
            target_payload=dict(target["payload"]),
            target_actor=(dict(target["actor"]) if target.get("actor") else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.error(
            "scheduler.schedule_requested.malformed",
            event_id=envelope.get("event_id"),
            error=str(exc)[:300],
        )
        return None


async def handle_schedule_requested(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(str(envelope["event_id"]))
    organization_id = uuid.UUID(str(envelope["organization_id"]))

    request = _parse(envelope)

    # The GUC is set here, per message: `scheduled_jobs` is a NOBYPASSRLS write against a forced
    # tenant policy and only passes because of this binding (ADR-0002 §2).
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("scheduler.schedule_requested.duplicate", event_id=str(event_id))
            return
        if request is not None:
            await services.ingest_schedule_request(session, organization_id, request)
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    if request is not None:
        log.info(
            "scheduler.schedule_requested.ingested",
            event_id=str(event_id),
            organization_id=str(organization_id),
            target_event_type=request.target_event_type,
            due_at=request.due_at.isoformat(),
        )


consumer.on(PLATFORM_SCHEDULE_REQUESTED)(handle_schedule_requested)


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
