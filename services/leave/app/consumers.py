"""Event consumers for leave. Runs as its own process.

Two independent groups of handlers, sharing nothing but the idempotency ledger:

1. **The employee directory** (`on_employee_*`) — the same three lifecycle events approval's
   `hierarchy_nodes` consumes (ADR-0006 §2), kept here as a much thinner copy: just the
   reporting manager and the employment status, versioned the same way (apply iff
   `hierarchy_version` is strictly greater than what is stored). See `app/models.py`'s
   `EmployeeDirectory` for what it is for and why it is narrower than approval's own copy.

2. **The approval round trip** (`on_approval_request_*`) — both of approval's request events,
   the same shape `attendance/app/consumers.py` uses for regularisation: `.opened` and `.decided`
   both name `request_type` and this service ignores anything that is not
   `hrms.time.leave` (ADR-0006 §12), each loads the leave request by `(organization_id, id =
   source_ref)` and acts only while it is still `pending`.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the same
transaction as its side effects. Redelivery is normal, not exceptional.
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
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("leave.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="leave.events")

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own, so
#: it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


# --------------------------------------------------------------------- the employee directory

#: Contract strings from employee-service, named here rather than imported (CLAUDE.md — one
#: process may only ever hold one service's top-level `app`).
EVT_EMPLOYEE_ONBOARDED = "hrms.people.employee.onboarded"
EVT_EMPLOYEE_UPDATED = "hrms.people.employee.updated"
EVT_EMPLOYEE_TRANSITIONED = "hrms.people.employee.transitioned"

HIERARCHY_EVENTS = (EVT_EMPLOYEE_ONBOARDED, EVT_EMPLOYEE_UPDATED, EVT_EMPLOYEE_TRANSITIONED)

DIRECTORY_STATUSES = ("probation", "active", "on_notice", "exited")


@dataclass(frozen=True, slots=True)
class DirectoryState:
    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    status: str
    hierarchy_version: int


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _tenant_from_envelope(envelope: dict[str, Any]) -> uuid.UUID | None:
    """The organization this event is scoped to — refusing a payload whose own
    `organization_id` disagrees with the envelope's own. `session_scope` binds the tenant GUC
    from whichever value a handler passes it; without this check a producer bug (or a forged
    event) that disagreed with itself would process one tenant's row under another tenant's
    isolation, which row-level security cannot catch from inside — it trusts the GUC it is given.
    Logged and dropped, never raised, never marked processed: there is no organization this
    handler trusts enough to bind `session_scope` to (and `processed_events` is itself
    un-policied, so writing to it needs an org id too). Redelivery is harmless — the same
    mismatch refuses again, every time.
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


def _directory_state(payload: dict[str, Any]) -> DirectoryState | None:
    """Parse the full-state hierarchy fields out of an employee event, or `None` to skip it —
    the same "absent version means an un-upgraded pod, ignore rather than treat as version 0"
    rule ADR-0006 §2.1 states for approval's identical parse, and the same "logged and dropped,
    never raised" discipline for a malformed payload (a redelivery would see the same shape)."""
    raw_version = payload.get("hierarchy_version")
    if raw_version is None:
        return None
    try:
        status = str(payload["status"])
        if status not in DIRECTORY_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        return DirectoryState(
            employee_id=uuid.UUID(str(payload["employee_id"])),
            reporting_manager_id=_uuid_or_none(payload.get("reporting_manager_id")),
            status=status,
            hierarchy_version=int(raw_version),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.error("directory.event.malformed", error=str(exc)[:200], fields=sorted(payload.keys()))
        return None


async def _apply_directory_event(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    event_type = envelope["event_type"]
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("directory.event.duplicate", event_id=str(event_id), event_type=event_type)
            return

        state = _directory_state(payload)
        if state is not None:
            from app.services import upsert_directory_entry

            applied = await upsert_directory_entry(
                session,
                organization_id=organization_id,
                employee_id=state.employee_id,
                reporting_manager_id=state.reporting_manager_id,
                status=state.status,
                source_version=state.hierarchy_version,
                source_occurred_at=_occurred_at(envelope),
            )
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "directory.event.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        carried_state=state is not None,
        applied=applied,
    )


def _occurred_at(envelope: dict[str, Any]) -> datetime:
    raw = envelope.get("occurred_at")
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        log.warning("directory.event.malformed_occurred_at", raw=str(raw)[:100])
        return datetime.now(UTC)


@consumer.on(EVT_EMPLOYEE_ONBOARDED)
async def on_employee_onboarded(envelope: dict[str, Any]) -> None:
    await _apply_directory_event(envelope)


@consumer.on(EVT_EMPLOYEE_UPDATED)
async def on_employee_updated(envelope: dict[str, Any]) -> None:
    await _apply_directory_event(envelope)


@consumer.on(EVT_EMPLOYEE_TRANSITIONED)
async def on_employee_transitioned(envelope: dict[str, Any]) -> None:
    await _apply_directory_event(envelope)


# --------------------------------------------------------------------- the approval round trip

EVT_APPROVAL_REQUEST_OPENED = "core.approval.request.opened"
EVT_APPROVAL_REQUEST_DECIDED = "core.approval.request.decided"
#: The request type this product owns on both shared events (ADR-0006 §12).
REQUEST_TYPE_LEAVE = "hrms.time.leave"


def _decided_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("decided_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            log.warning("leave.decided.malformed_decided_at", raw=str(raw)[:100])
    return datetime.now(UTC)


def _display_name(payload: dict[str, Any], key: str) -> str | None:
    raw = payload.get(key)
    if not raw:
        return None
    return str(raw)[:200]


@consumer.on(EVT_APPROVAL_REQUEST_OPENED)
async def on_approval_request_opened(envelope: dict[str, Any]) -> None:
    payload = envelope["payload"]
    if payload.get("request_type") != REQUEST_TYPE_LEAVE:
        # Not ours. Every request type approval routes shares this one event type, so the
        # binding cannot be narrower than the routing key — filtering happens here instead.
        return

    event_id = uuid.UUID(envelope["event_id"])
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return
    source_ref = uuid.UUID(payload["source_ref"])
    approval_request_id = uuid.UUID(payload["request_id"])
    raw_approver = payload.get("approver_employee_id")
    approver_employee_id = uuid.UUID(raw_approver) if raw_approver else None
    approver_name = _display_name(payload, "approver_name")
    opened_at = _occurred_at(envelope)

    applied = False
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("leave.opened.duplicate", event_id=str(event_id))
            return

        from app import selectors, services

        req = await selectors.get_leave_request_by_source(session, organization_id, source_ref)
        if req is None:
            log.warning(
                "leave.opened.unknown_request",
                event_id=str(event_id),
                organization_id=str(organization_id),
                source_ref=str(source_ref),
            )
        elif req.status != "pending":
            log.info(
                "leave.opened.already_settled",
                event_id=str(event_id),
                request_id=str(req.id),
                status=req.status,
            )
        else:
            await services.apply_leave_opened(
                session,
                req,
                approval_request_id=approval_request_id,
                approver_employee_id=approver_employee_id,
                approver_name=approver_name,
                opened_at=opened_at,
            )
            applied = True
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "leave.opened.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        source_ref=str(source_ref),
        applied=applied,
    )


@consumer.on(EVT_APPROVAL_REQUEST_DECIDED)
async def on_approval_request_decided(envelope: dict[str, Any]) -> None:
    payload = envelope["payload"]
    if payload.get("request_type") != REQUEST_TYPE_LEAVE:
        return

    event_id = uuid.UUID(envelope["event_id"])
    organization_id = _tenant_from_envelope(envelope)
    if organization_id is None:
        return
    source_ref = uuid.UUID(payload["source_ref"])
    approval_request_id = uuid.UUID(payload["request_id"])
    outcome = payload["outcome"]
    decided_at = _decided_at(payload)
    comment = payload.get("comment")
    decided_by_name = _display_name(payload, "decided_by_name")

    applied_status: str | None = None
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("leave.decided.duplicate", event_id=str(event_id))
            return

        from app import selectors, services

        # An explicit, exhaustive check — never "anything that is not 'rejected' is an
        # approval" — the same discipline `approval.services.decide_request`'s own
        # `_DECISION_OUTCOMES` mapping states. An outcome outside this pair cannot come from
        # approval's real `decide_request` (its own `DecisionIn.outcome` is
        # `Literal["approve", "reject"]`, published as `"approved"`/`"rejected"`), so seeing one
        # here means a malformed or forged event — logged and dropped, never applied as either.
        if outcome not in ("approved", "rejected"):
            log.error(
                "leave.decided.unknown_outcome",
                event_id=str(event_id),
                organization_id=str(organization_id),
                source_ref=str(source_ref),
                outcome=str(outcome)[:50],
            )
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            return

        req = await selectors.get_leave_request_by_source(session, organization_id, source_ref)
        if req is None:
            log.warning(
                "leave.decided.unknown_request",
                event_id=str(event_id),
                organization_id=str(organization_id),
                source_ref=str(source_ref),
            )
        elif req.status != "pending":
            log.info(
                "leave.decided.already_settled",
                event_id=str(event_id),
                request_id=str(req.id),
                status=req.status,
            )
        else:
            req = await services.apply_leave_decision(
                session,
                req,
                outcome=outcome,
                approval_request_id=approval_request_id,
                decided_at=decided_at,
                comment=comment,
                decided_by_name=decided_by_name,
            )
            applied_status = req.status
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "leave.decided.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        source_ref=str(source_ref),
        outcome=outcome,
        applied_status=applied_status,
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
