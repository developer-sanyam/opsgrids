"""Event consumers for approval. Runs as its own process.

This slice's only consumer maintains `hierarchy_nodes` — the read-only copy of the reporting
line (ADR-0006 §2). It binds to all three employee lifecycle events, because every one of them
is specified to carry the **complete** hierarchy state of the employee after the change, not a
diff (ADR-0006 §2: "Full state, not a diff: a consumer that has missed or reordered an event
converges on the next one it sees, and never needs to replay history"). The three handlers are
therefore identical except for which routing key woke them up, and share one implementation.

**Contract note — what this consumer needs and what employee-service ships today.** ADR-0006 §2
and §12 specify that `onboarded`, `updated` and `transitioned` gain `reporting_manager_id`,
`dotted_line_manager_id`, `status`, `display_name`, `employee_no`, `location_code` and
`hierarchy_version` on top of their existing fields. Verified against
`services/employee/app/services.py` as this slice was built: **none of those fields exist on the
payload yet** — that work is `employee-service`'s, landing in parallel with this slice (the
`docs/adr/0006-approval-engine.md` context table lists the exact three `publish_event` calls with
their current, hierarchy-free shape). This consumer is written **against the ADR's contract, not
against today's payload**, and is safe either way: `_hierarchy_state` returns `None` whenever
`hierarchy_version` is absent, and the handler below drops such an event exactly the way ADR-0006
§2.1 requires ("If employee returns a node whose `hierarchy_version` is absent — an un-upgraded
pod — the row is **ignored**, never treated as version 0"). Until employee's change ships and
deploys ahead of this consumer (the ADR's deploy order, data model §6), every one of these events
is a no-op; once it ships, no restart or backfill of *this* service is needed for the stream to
start carrying real hierarchy state.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as the side effect. Redelivery is normal, not exceptional — and the upsert itself is
idempotent at the domain level too (`app/services.upsert_hierarchy_node`'s version gate), so two
independent sources of repetition (at-least-once delivery, and a backfill racing the live stream)
both converge safely.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.models import ProcessedEvent
from app.services import upsert_hierarchy_node
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("approval.consumers")

#: Named once so `app/backfill.py`'s `ensure_queue_bound` — which must declare and bind the
#: identical queue before the worker ever runs (ADR-0006 §2) — can import it rather than repeat
#: the string literal, which is exactly the kind of drift that produced a 406 PRECONDITION_FAILED
#: between the two declarations.
QUEUE_NAME = "approval.events"

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name=QUEUE_NAME)

#: Contract strings from employee-service, named here rather than imported — one process may
#: only ever hold one service's top-level `app` (CLAUDE.md).
EVT_EMPLOYEE_ONBOARDED = "hrms.people.employee.onboarded"
EVT_EMPLOYEE_UPDATED = "hrms.people.employee.updated"
EVT_EMPLOYEE_TRANSITIONED = "hrms.people.employee.transitioned"

HIERARCHY_EVENTS = (EVT_EMPLOYEE_ONBOARDED, EVT_EMPLOYEE_UPDATED, EVT_EMPLOYEE_TRANSITIONED)


@dataclass(frozen=True, slots=True)
class HierarchyState:
    """The full-state hierarchy fields a payload must carry together (ADR-0006 §2, §12)."""

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    hierarchy_version: int


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _hierarchy_state(payload: dict[str, Any]) -> HierarchyState | None:
    """Parse the full-state hierarchy fields out of an employee event, or `None` to skip it.

    `None` covers two cases the ADR treats identically: the field is genuinely absent (an
    un-upgraded employee pod, per §2.1) or the payload is malformed in a way that makes the state
    unusable. Both are logged and dropped rather than raised — raising would redeliver once and
    then dead-letter a message that is not going to become processable by retrying it
    (`EventConsumer` redelivers once; a payload shape does not change between the two attempts).
    """
    raw_version = payload.get("hierarchy_version")
    if raw_version is None:
        return None
    try:
        return HierarchyState(
            employee_id=uuid.UUID(str(payload["employee_id"])),
            reporting_manager_id=_uuid_or_none(payload.get("reporting_manager_id")),
            dotted_line_manager_id=_uuid_or_none(payload.get("dotted_line_manager_id")),
            status=str(payload["status"]),
            display_name=str(payload["display_name"]),
            employee_no=str(payload["employee_no"]),
            location_code=(str(payload["location_code"]) if payload.get("location_code") else None),
            hierarchy_version=int(raw_version),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.error(
            "hierarchy.event.malformed",
            error=str(exc)[:200],
            fields=sorted(payload.keys()),
        )
        return None


async def _apply_hierarchy_event(envelope: dict[str, Any]) -> None:
    """Shared body for all three employee lifecycle events (see the module docstring)."""
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    event_type = envelope["event_type"]

    applied = False
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("hierarchy.event.duplicate", event_id=str(event_id), event_type=event_type)
            return

        state = _hierarchy_state(payload)
        if state is not None:
            occurred_at = _occurred_at(envelope)
            applied = await upsert_hierarchy_node(
                session,
                organization_id=organization_id,
                employee_id=state.employee_id,
                reporting_manager_id=state.reporting_manager_id,
                dotted_line_manager_id=state.dotted_line_manager_id,
                status=state.status,
                display_name=state.display_name,
                employee_no=state.employee_no,
                location_code=state.location_code,
                source_version=state.hierarchy_version,
                source_occurred_at=occurred_at,
            )
        # Marked processed regardless of whether a state was found or applied: a pre-hierarchy
        # payload, and a stale or equal version, are both handled outcomes, not errors — and
        # recording the id is what stops either from being reprocessed forever.
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "hierarchy.event.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        carried_hierarchy_state=state is not None,
        applied=applied,
    )


def _occurred_at(envelope: dict[str, Any]) -> datetime:
    """`source_occurred_at` is display/debugging only and **never compared**
    (`HierarchyNode`'s docstring, ADR-0006 §2's "a version, not a timestamp") — so a malformed
    value must not dead-letter an otherwise-good event. This falls back to "now" and logs a
    warning, the same "logged and dropped rather than raised" discipline `_hierarchy_state` uses
    for the fields that *are* load-bearing.
    """
    raw = envelope.get("occurred_at")
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        log.warning("hierarchy.event.malformed_occurred_at", raw=str(raw)[:100])
        return datetime.now(UTC)


@consumer.on(EVT_EMPLOYEE_ONBOARDED)
async def on_employee_onboarded(envelope: dict[str, Any]) -> None:
    await _apply_hierarchy_event(envelope)


@consumer.on(EVT_EMPLOYEE_UPDATED)
async def on_employee_updated(envelope: dict[str, Any]) -> None:
    await _apply_hierarchy_event(envelope)


@consumer.on(EVT_EMPLOYEE_TRANSITIONED)
async def on_employee_transitioned(envelope: dict[str, Any]) -> None:
    await _apply_hierarchy_event(envelope)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    log.info("approval.consumer.bindings", routing_keys=list(HIERARCHY_EVENTS))
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
