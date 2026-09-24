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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ProcessedEvent
from app.services import (
    REQUEST_TYPE_LEAVE,
    apply_leave_outcome,
    apply_regularization_outcome,
    open_leave_request,
    open_regularization_request,
    upsert_hierarchy_node,
    withdraw_request,
)
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
    async with _session_scope(organization_id) as session:
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


# --------------------------------------------------------------------- routing (slice 2)

#: Contract strings from attendance-service, named here rather than imported (CLAUDE.md). Both
#: payload shapes are verified against `services/attendance/app/services.py` as this slice was
#: built — `request_regularization` and `_publish_regularization_applied` — with **no mismatch**:
#: attendance already publishes exactly the fields ADR-0006 §12 specifies.
EVT_REGULARIZATION_REQUESTED = "hrms.time.regularization.requested"
EVT_REGULARIZATION_APPLIED = "hrms.time.regularization.applied"

REGULARIZATION_EVENTS = (EVT_REGULARIZATION_REQUESTED, EVT_REGULARIZATION_APPLIED)


@consumer.on(EVT_REGULARIZATION_REQUESTED)
async def on_regularization_requested(envelope: dict[str, Any]) -> None:
    """Opens a `hrms.time.regularization` request (ADR-0006 §4). Payload:
    `organization_id, regularization_request_id (-> source_ref), attendance_id,
    employee_id (-> requester), requested_status, current_status, reason (-> request_note), date`.

    `attendance_id` is not read here: the two rows are joined by `(organization_id, request_type,
    source_ref)` alone (ADR-0006 §3), never by the product's own foreign key, which belongs to
    attendance's schema and stays there.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])

    opened = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("regularization.requested.duplicate", event_id=str(event_id))
            return

        request = await open_regularization_request(
            session,
            organization_id=organization_id,
            source_ref=uuid.UUID(str(payload["regularization_request_id"])),
            requester_employee_id=uuid.UUID(str(payload["employee_id"])),
            summary={
                "date": payload["date"],
                "from": payload["current_status"],
                "to": payload["requested_status"],
            },
            request_note=payload.get("reason"),
        )
        opened = request is not None
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "regularization.requested.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        opened=opened,
    )


@consumer.on(EVT_REGULARIZATION_APPLIED)
async def on_regularization_applied(envelope: dict[str, Any]) -> None:
    """Records the product's return leg (ADR-0006 §12) — published for every decision attendance
    consumes, approvals and rejections alike, so `outcome` here is `applied|failed`, never
    `approved|rejected`. Payload: `organization_id, regularization_request_id (-> source_ref),
    approval_request_id, request_type, outcome, failure_reason?`.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("regularization.applied.duplicate", event_id=str(event_id))
            return

        request = await apply_regularization_outcome(
            session,
            organization_id=organization_id,
            source_ref=uuid.UUID(str(payload["regularization_request_id"])),
            outcome=str(payload["outcome"]),
            failure_reason=payload.get("failure_reason"),
        )
        applied = request is not None
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "regularization.applied.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        applied=applied,
    )


# --------------------------------------------------------------------- routing (HRMS leave slice 1)

#: Contract strings from leave-service (`services/leave/app/services.py`), named here rather than
#: imported (CLAUDE.md). Same shape as regularisation's pair above: `.requested` opens a
#: `hrms.time.leave` request, `.applied` is leave's return leg once a decision lands, `.cancelled`
#: withdraws a still-pending one.
EVT_LEAVE_REQUESTED = "hrms.time.leave.requested"
EVT_LEAVE_APPLIED = "hrms.time.leave.applied"
EVT_LEAVE_CANCELLED = "hrms.time.leave.cancelled"

LEAVE_EVENTS = (EVT_LEAVE_REQUESTED, EVT_LEAVE_APPLIED, EVT_LEAVE_CANCELLED)


def _tenant_from_envelope(envelope: dict[str, Any]) -> uuid.UUID | None:
    """The organization this event is scoped to — refusing a payload whose own
    `organization_id` disagrees with the envelope's own. `session_scope` binds the tenant GUC
    from whichever value a handler passes it; without this check a producer bug (or a forged
    event) that disagreed with itself would process one tenant's row under another tenant's
    isolation, which row-level security cannot catch from inside — it trusts the GUC it is given.
    Logged and dropped, never raised, the same "malformed input is not retried into a dead
    letter" discipline this module already uses for a malformed `occurred_at`.
    """
    payload = envelope.get("payload") or {}
    envelope_org = envelope.get("organization_id")
    payload_org = payload.get("organization_id")
    if envelope_org is None or payload_org is None or str(envelope_org) != str(payload_org):
        log.error(
            "event.tenant_mismatch",
            event_id=envelope.get("event_id"),
            event_type=envelope.get("event_type"),
            envelope_organization_id=envelope_org,
            payload_organization_id=payload_org,
        )
        return None
    return uuid.UUID(str(payload_org))


@consumer.on(EVT_LEAVE_REQUESTED)
async def on_leave_requested(envelope: dict[str, Any]) -> None:
    """Opens a `hrms.time.leave` request (ADR-0006 §4). Payload: `organization_id,
    leave_request_id (-> source_ref), employee_id (-> requester), leave_type_id, start_date,
    end_date, half_day_start, half_day_end, total_half_days, reason (-> request_note)`.

    `summary` carries only structured, enumerable facts — dates, ids and half-day counts — never
    `reason`, the same "no free text in the JSONB" rule ADR-0006 §3 states for regularisation's
    identical split.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    # A tenant mismatch is refused before `processed_events` is touched: there is no organization
    # this handler trusts enough to bind `session_scope` to, and `processed_events` is itself
    # un-policied, so writing to it here would need an org id anyway. Redelivery is harmless —
    # the same mismatch refuses again, every time — which is the safe direction to be wrong in.
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    opened = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("leave.requested.duplicate", event_id=str(event_id))
            return

        request = await open_leave_request(
            session,
            organization_id=organization_id,
            source_ref=uuid.UUID(str(payload["leave_request_id"])),
            requester_employee_id=uuid.UUID(str(payload["employee_id"])),
            summary={
                "leave_type_id": payload["leave_type_id"],
                "start_date": payload["start_date"],
                "end_date": payload["end_date"],
                "half_day_start": payload["half_day_start"],
                "half_day_end": payload["half_day_end"],
                "total_half_days": payload["total_half_days"],
            },
            request_note=payload.get("reason"),
        )
        opened = request is not None
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "leave.requested.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        opened=opened,
    )


@consumer.on(EVT_LEAVE_APPLIED)
async def on_leave_applied(envelope: dict[str, Any]) -> None:
    """Records leave's return leg (ADR-0006 §12). Payload: `organization_id, leave_request_id
    (-> source_ref), approval_request_id, request_type, outcome, failure_reason?`."""
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("leave.applied.duplicate", event_id=str(event_id))
            return

        request = await apply_leave_outcome(
            session,
            organization_id=organization_id,
            source_ref=uuid.UUID(str(payload["leave_request_id"])),
            outcome=str(payload["outcome"]),
            failure_reason=payload.get("failure_reason"),
        )
        applied = request is not None
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "leave.applied.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        applied=applied,
    )


@consumer.on(EVT_LEAVE_CANCELLED)
async def on_leave_cancelled(envelope: dict[str, Any]) -> None:
    """Withdraws a still-`pending` `hrms.time.leave` request (`withdraw_request`) — leave's own
    cancellation of a request nobody has decided yet. Payload: `organization_id,
    leave_request_id (-> source_ref), employee_id, was_status`.

    A cancellation of an *already-decided* request (leave's `was_status == "approved"`) needs no
    action here: approval already recorded that decision, and there is nothing left to withdraw
    — `withdraw_request` itself is a no-op once the request has no open step, so this handler
    does not need to branch on `was_status` to get that right.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    withdrawn = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("leave.cancelled.duplicate", event_id=str(event_id))
            return

        request = await withdraw_request(
            session,
            organization_id=organization_id,
            request_type=REQUEST_TYPE_LEAVE,
            source_ref=uuid.UUID(str(payload["leave_request_id"])),
        )
        withdrawn = request is not None
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "leave.cancelled.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        withdrawn=withdrawn,
    )


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    log.info(
        "approval.consumer.bindings",
        routing_keys=list(HIERARCHY_EVENTS) + list(REGULARIZATION_EVENTS) + list(LEAVE_EVENTS),
    )
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
