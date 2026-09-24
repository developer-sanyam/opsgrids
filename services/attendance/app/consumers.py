"""Event consumers for attendance. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.

**Both of approval's request events, not just the decision.** Approval publishes two events for
every request it routes: `core.approval.request.opened`, when the step is first routed, and
`core.approval.request.decided`, when someone acts on it. Both are approval's one event of their
kind for *every* request type it routes — not just this product's — so the routing key alone
cannot bind narrowly; each handler filters on `payload["request_type"]` itself and ignores
anything that is not `hrms.time.regularization` (ADR-0006 §12: "Attendance's consumer ignores any
`request_type` it does not own"). Each then loads `regularization_requests` by
`(organization_id, id = source_ref)` and acts only if that row is still `pending` — a replay, or
either event arriving after `.decided` has already settled the row, is a no-op rather than an
error, which is what makes redelivery safe on top of the ordinary `processed_events` ledger, and
is also what stops a late `.opened` from ever overwriting a terminal decision.

**Approval's payloads now carry a display name alongside each id.** `.opened` carries
`approver_name: str | None` beside `approver_employee_id`; `.decided` carries
`decided_by_name: str | None` beside `decided_by_employee_id` — the fix for the contract gap this
docstring used to describe (approval used to publish bare ids only). Both name fields are stored
verbatim onto `approver_name`/`decided_by_name` (`app/models.py`), so the "Waiting on {Name}"
screen and a decided row's "{who} rejected, with comment" no longer need a separate resolve
against employee-service for these two cases.

Both fields are treated as **optional on the wire**, exactly like every other field this module
reads with `.get(...)`: a name key absent from the payload (an event published before approval's
change lands, or replayed from the outbox) stores `null` and is logged, never raised on and never
dead-lettered. A payload that carries the key with an explicit `null` value is a different, and
legitimate, fact — an unassigned step has no approver to name; a decider outside approval's
hierarchy copy (a route-(c) `decide:ORG` decision) has no denormalized name to publish — and also
stores `null`, indistinguishably from the key being absent, since neither case is an error this
table needs to tell apart from the other.

`decided_by_employee_id` itself is still not stored as its own column — only its name is asked
for here — see `apply_regularization_decision`'s docstring.

**The reporting line, from employee's three lifecycle events (R8).** `hrms.people.employee.
onboarded`, `.updated` and `.transitioned` each carry the employee's *complete* hierarchy state —
manager, status and `hierarchy_version` — not a diff, so one handler serves all three and a
consumer that missed or reordered an event converges on the next one it sees (ADR-0006 §2). The
handler upserts `team_membership`, the read model behind TEAM scope, gated on `hierarchy_version`
being strictly greater than the stored one: redelivery, a stale pre-move event arriving late, and
a backfill racing the live stream are all no-ops, whatever `processed_events` says. An event with
no `hierarchy_version` (a pod that predates it) is dropped, never read as version 0; one whose
`organization_id` disagrees with its envelope's is dropped too — the transaction is bound to the
envelope's tenant, and a payload naming another is not something to apply under either.

`processed_events` here is the platform mixin, keyed on the event id alone. Its re-keying to
`(organization_id, event_id)` is ADR-0006's open question 2 and changes every service's ledger at
once; this consumer neither depends on the current shape nor pre-empts the change, because the
version gate above is what actually makes the projection idempotent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.domain import TEAM_STATUSES, normalise_manager
from app.models import ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

if TYPE_CHECKING:
    from app.services import TeamMembershipWrite

log = get_logger("attendance.consumers")

#: Named once so `app/backfill.py`'s `ensure_queue_bound` — which must declare and bind the
#: identical queue before any backfill HTTP call (a topic exchange drops what nothing is bound
#: to) — imports it rather than repeating the string literal.
QUEUE_NAME = "attendance.events"

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name=QUEUE_NAME)

#: Contract strings from approval-service, named here rather than imported — one process may only
#: ever hold one service's top-level `app` (CLAUDE.md).
EVT_APPROVAL_REQUEST_OPENED = "core.approval.request.opened"
EVT_APPROVAL_REQUEST_DECIDED = "core.approval.request.decided"
#: The request type this product owns on both shared events (ADR-0006 §12).
REQUEST_TYPE_REGULARIZATION = "hrms.time.regularization"

#: From employee-service. Each carries the employee's full hierarchy state (ADR-0006 §2).
EVT_EMPLOYEE_ONBOARDED = "hrms.people.employee.onboarded"
EVT_EMPLOYEE_UPDATED = "hrms.people.employee.updated"
EVT_EMPLOYEE_TRANSITIONED = "hrms.people.employee.transitioned"
TEAM_EVENTS = (EVT_EMPLOYEE_ONBOARDED, EVT_EMPLOYEE_UPDATED, EVT_EMPLOYEE_TRANSITIONED)

#: Every routing key this process binds, for the backfill to bind ahead of the worker.
BOUND_EVENTS = (EVT_APPROVAL_REQUEST_OPENED, EVT_APPROVAL_REQUEST_DECIDED, *TEAM_EVENTS)


def _decided_at(payload: dict[str, Any]) -> datetime:
    """`decided_at` is load-bearing (`ck_regularization_requests_decided_iff_not_pending`), but a
    malformed value must not dead-letter an otherwise-good decision — the same "logged and
    dropped rather than raised" discipline approval's own hierarchy consumer uses for
    `occurred_at`, which is display-only there but cannot be here, so this falls back to now."""
    raw = payload.get("decided_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            log.warning("regularization.decided.malformed_decided_at", raw=str(raw)[:100])
    return datetime.now(UTC)


def _opened_at(envelope: dict[str, Any]) -> datetime:
    """`.opened`'s own payload carries no timestamp of when the step opened — unlike `.decided`'s
    `decided_at`, there is nothing named `opened_at` on `payload` (verified against
    `services/approval/app/services.py`'s `publish_event(...)` call). The envelope's own
    `occurred_at` — the outbox row's `created_at`, stamped when approval published the event
    (`platform_core.events.build_envelope`) — is the nearest fact available, and is display-only
    here (nothing CHECKs it), so a malformed value is logged and dropped rather than raised, the
    same discipline `_decided_at` uses for its own field."""
    raw = envelope.get("occurred_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            log.warning("regularization.opened.malformed_occurred_at", raw=str(raw)[:100])
    return datetime.now(UTC)


def _display_name(payload: dict[str, Any], key: str) -> str | None:
    """Pull an optional display name off a payload — `approver_name` on `.opened`, or
    `decided_by_name` on `.decided`. Both are treated exactly like a field this module already
    reads with `.get(...)`: an absent key (an event published before approval's change lands, or
    a replay from the outbox) and an explicit `null` (unassigned step; decider outside approval's
    hierarchy copy) both fall through to `None` here, never raised on. Truncated to the column's
    own `String(200)` width, the same defensive-against-a-too-long-value shape
    `payment.services._reconcile_charge` uses for `charge.failure_reason` — approval's own source
    (`employees.full_name` / `hierarchy_nodes.display_name`) is never wider than that, but a
    processor is never trusted to guarantee it."""
    raw = payload.get(key)
    if not raw:
        return None
    return str(raw)[:200]


@consumer.on(EVT_APPROVAL_REQUEST_OPENED)
async def on_approval_request_opened(envelope: dict[str, Any]) -> None:
    """The consumer ADR-0006's data model always intended (`RegularizationRequest`'s own
    docstring used to say `approval_request_id` was "populated late from the .opened/.decided
    events" before the `.opened` half was stripped out as unbuilt) — built here at last, so the
    approver a step routed to is known before a decision lands, not only after.
    """
    payload = envelope["payload"]
    if payload.get("request_type") != REQUEST_TYPE_REGULARIZATION:
        # Not ours. Every request type approval routes shares this one event type, so the
        # binding cannot be narrower than the routing key — filtering happens here instead.
        return

    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(payload["organization_id"])
    source_ref = uuid.UUID(payload["source_ref"])
    approval_request_id = uuid.UUID(payload["request_id"])
    raw_approver = payload.get("approver_employee_id")
    approver_employee_id = uuid.UUID(raw_approver) if raw_approver else None
    approver_name = _display_name(payload, "approver_name")
    opened_at = _opened_at(envelope)

    applied: bool = False
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("regularization.opened.duplicate", event_id=str(event_id))
            return

        from app import selectors, services

        reg = await selectors.get_regularization(session, organization_id, source_ref)
        if reg is None:
            log.warning(
                "regularization.opened.unknown_request",
                event_id=str(event_id),
                organization_id=str(organization_id),
                source_ref=str(source_ref),
            )
        elif reg.status != "pending":
            # A late `.opened` arriving after `.decided` already settled the row (or a replay
            # under a fresh event id) — a no-op, never an overwrite of a terminal decision.
            log.info(
                "regularization.opened.already_settled",
                event_id=str(event_id),
                request_id=str(reg.id),
                status=reg.status,
            )
        else:
            await services.apply_regularization_opened(
                session,
                reg,
                approval_request_id=approval_request_id,
                approver_employee_id=approver_employee_id,
                approver_name=approver_name,
                opened_at=opened_at,
            )
            applied = True
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "regularization.opened.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        source_ref=str(source_ref),
        approver_employee_id=str(approver_employee_id) if approver_employee_id else None,
        approver_name_present=approver_name is not None,
        applied=applied,
    )


@consumer.on(EVT_APPROVAL_REQUEST_DECIDED)
async def on_approval_request_decided(envelope: dict[str, Any]) -> None:
    payload = envelope["payload"]
    if payload.get("request_type") != REQUEST_TYPE_REGULARIZATION:
        # Not ours. Every request type approval routes shares this one event type, so the
        # binding cannot be narrower than the routing key — filtering happens here instead.
        return

    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(payload["organization_id"])
    source_ref = uuid.UUID(payload["source_ref"])
    approval_request_id = uuid.UUID(payload["request_id"])
    outcome = payload["outcome"]
    decided_at = _decided_at(payload)
    comment = payload.get("comment")
    decided_by_name = _display_name(payload, "decided_by_name")

    applied_status: str | None = None
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("regularization.decided.duplicate", event_id=str(event_id))
            return

        # Imported inside the handler to keep the module import-light and avoid a cycle with the
        # services layer, mirroring `subscription.consumers`.
        from app import selectors, services

        reg = await selectors.get_regularization(session, organization_id, source_ref)
        if reg is None:
            log.warning(
                "regularization.decided.unknown_request",
                event_id=str(event_id),
                organization_id=str(organization_id),
                source_ref=str(source_ref),
            )
        elif reg.status != "pending":
            log.info(
                "regularization.decided.already_settled",
                event_id=str(event_id),
                request_id=str(reg.id),
                status=reg.status,
            )
        else:
            record = await selectors.get_attendance(session, organization_id, reg.attendance_id)
            if record is None:
                log.warning(
                    "regularization.decided.attendance_missing",
                    event_id=str(event_id),
                    request_id=str(reg.id),
                    attendance_id=str(reg.attendance_id),
                )
            else:
                reg = await services.apply_regularization_decision(
                    session,
                    reg,
                    record,
                    outcome=outcome,
                    approval_request_id=approval_request_id,
                    decided_at=decided_at,
                    comment=comment,
                    decided_by_name=decided_by_name,
                )
                applied_status = reg.status
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "regularization.decided.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        source_ref=str(source_ref),
        outcome=outcome,
        applied_status=applied_status,
        decided_by_name_present=decided_by_name is not None,
    )


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _team_state(payload: dict[str, Any], occurred_at: datetime) -> TeamMembershipWrite | None:
    """Parse an employee event's full hierarchy state into a projection write, or `None` to skip.

    `None` covers two cases treated identically: the state is genuinely absent (`hierarchy_version`
    missing — a pod that predates it, which ADR-0006 §2.1 says is *ignored, never read as version
    0*) and the payload is malformed in a way that makes it unusable. Both are logged and dropped
    rather than raised: `EventConsumer` redelivers once and then dead-letters, and a payload's shape
    does not change between the two attempts, so raising would only move the message to the DLQ.
    """
    from app.services import TeamMembershipWrite

    raw_version = payload.get("hierarchy_version")
    if raw_version is None:
        return None
    try:
        employee_id = uuid.UUID(str(payload["employee_id"]))
        status = str(payload["status"])
        version = int(raw_version)
        manager = _uuid_or_none(payload.get("reporting_manager_id"))
    except (KeyError, TypeError, ValueError) as exc:
        log.error("team.event.malformed", error=str(exc)[:200], fields=sorted(payload.keys()))
        return None
    if status not in TEAM_STATUSES or version < 0:
        log.error("team.event.malformed", status=status[:40], version=version)
        return None
    normalised = normalise_manager(employee_id, manager)
    if normalised != manager:
        log.warning("team.event.self_manager", employee_id=str(employee_id))
    return TeamMembershipWrite(
        employee_id=employee_id,
        manager_employee_id=normalised,
        status=status,
        source_version=version,
        source_occurred_at=occurred_at,
    )


def _envelope_occurred_at(envelope: dict[str, Any]) -> datetime:
    """`source_occurred_at` is display and debugging only and **never compared** (`models.
    TeamMembership`), so a malformed value must not dead-letter an otherwise-good state: it falls
    back to now and is logged, the same discipline `_decided_at` uses."""
    raw = envelope.get("occurred_at")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            log.warning("team.event.malformed_occurred_at", raw=str(raw)[:100])
    return datetime.now(UTC)


async def _apply_team_event(envelope: dict[str, Any]) -> None:
    """Shared body for the three employee lifecycle events (see the module docstring)."""
    from app import services

    payload = envelope["payload"]
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    try:
        organization_id = uuid.UUID(str(envelope["organization_id"]))
        payload_org = uuid.UUID(str(payload["organization_id"]))
    except (KeyError, TypeError, ValueError):
        log.error("team.event.no_tenant", event_id=str(event_id), event_type=event_type)
        return
    if payload_org != organization_id:
        # The transaction below is bound to the envelope's tenant. A payload naming another is
        # corrupt or hostile, and either way is not applied under any tenant.
        log.error(
            "team.event.tenant_mismatch",
            event_id=str(event_id),
            event_type=event_type,
            organization_id=str(organization_id),
        )
        return

    applied = False
    carried = False
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("team.event.duplicate", event_id=str(event_id), event_type=event_type)
            return
        state = _team_state(payload, _envelope_occurred_at(envelope))
        if state is not None:
            carried = True
            applied = (
                await services.upsert_team_membership_bulk(
                    session, organization_id=organization_id, rows=[state]
                )
                > 0
            )
        # Marked processed whether or not a state was found or applied: a version-less payload and
        # a stale or equal version are handled outcomes, not errors, and recording the id is what
        # stops either being reprocessed forever.
        mark_processed(session, ProcessedEvent, event_id, event_type)

    log.info(
        "team.event.processed",
        event_id=str(event_id),
        event_type=event_type,
        organization_id=str(organization_id),
        carried_hierarchy_state=carried,
        applied=applied,
    )


@consumer.on(EVT_EMPLOYEE_ONBOARDED)
async def on_employee_onboarded(envelope: dict[str, Any]) -> None:
    await _apply_team_event(envelope)


@consumer.on(EVT_EMPLOYEE_UPDATED)
async def on_employee_updated(envelope: dict[str, Any]) -> None:
    await _apply_team_event(envelope)


@consumer.on(EVT_EMPLOYEE_TRANSITIONED)
async def on_employee_transitioned(envelope: dict[str, Any]) -> None:
    await _apply_team_event(envelope)


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
