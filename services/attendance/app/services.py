"""Writes for the attendance service.

Every function here follows employee-service's shape: change the row, record the audit entry,
stage the event — **all inside one transaction**. That is why `get_tenant_session` opens the
transaction and does not commit for the route: a crash between the state change and the event
cannot leave one without the other, and a rolled-back write never leaves an audit row claiming
it happened.

Events are staged into the outbox, never published inline. Publishing before the commit is
durable is the bug the outbox exists to prevent (ADR-0000).

The geo-fence check lives here and nowhere else. It is a **PostGIS query, not app-side maths**
(CLAUDE.md, plan A.4): `ST_DWithin` over the `geography` type answers "is this punch within the
allowed radius of the location's centre" in the database, so there is no haversine in Python and
no new dependency. This slice does the distance check only — the device, IP and mock-location
anti-spoofing stack is deliberately out of scope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import settings
from app.domain import (
    AttendanceStatus,
    CheckOutBeforeCheckIn,
    IllegalTransition,
    assert_can_transition,
    assert_checkout_after_checkin,
    work_date_in,
)
from app.models import (
    AttendanceRecord,
    AuditLog,
    Geofence,
    OutboxEvent,
    RegularizationRequest,
    Shift,
)
from app.schemas import (
    CheckInIn,
    CheckOutIn,
    GeofenceIn,
    GeofencePatch,
    RegularizationRequestIn,
    ShiftIn,
    ShiftPatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import actor_for, publish_event

# Event names. `hrms.time.*` because these are the product's own, not the platform's.
EVT_CHECKED_IN = "hrms.time.attendance.checked_in"
EVT_CHECKED_OUT = "hrms.time.attendance.checked_out"
EVT_REGULARIZED = "hrms.time.attendance.regularized"
#: ADR-0006 — the approval engine's request/decide pair. `.requested` is what approval opens a
#: request from; `.applied` is attendance's return leg, closing the loop so "approved in approval"
#: never sits uncontradicted against an attendance row that never actually moved (ADR-0006 §12).
EVT_REGULARIZATION_REQUESTED = "hrms.time.regularization.requested"
EVT_REGULARIZATION_APPLIED = "hrms.time.regularization.applied"
#: The request type approval routes this product's regularisation requests under (ADR-0006 §12).
REQUEST_TYPE_REGULARIZATION = "hrms.time.regularization"
EVT_SHIFT_CREATED = "hrms.time.shift.created"
EVT_SHIFT_UPDATED = "hrms.time.shift.updated"
EVT_SHIFT_DELETED = "hrms.time.shift.deleted"
EVT_GEOFENCE_CREATED = "hrms.time.geofence.created"
EVT_GEOFENCE_UPDATED = "hrms.time.geofence.updated"
EVT_GEOFENCE_DELETED = "hrms.time.geofence.deleted"


def _subject_employee(ctx: ScopeContext) -> uuid.UUID:
    """The employee a self-service punch belongs to — the caller's own subject reference.

    A caller whose token carries no `subject_ref` is authenticated but is not an employee of
    this organization, so they have no attendance to punch. That is a business rule, not a
    permission failure: they hold `attendance.write`, there is simply no "own" record for it to
    act on.
    """
    subject = ctx.principal.subject_ref
    if subject is None:
        raise BusinessRuleProblem("Only an employee can punch their own attendance")
    return subject


async def _assert_within_fence(
    session: AsyncSession,
    fence: Geofence,
    *,
    lat: Decimal | None,
    lng: Decimal | None,
    location_code: str,
) -> None:
    """Enforce a field punch against its location's geo-fence — a PostGIS distance query.

    `ST_DWithin` over `geography` returns whether the punch is within `radius_m` metres of the
    fence centre, computed on the spheroid by the database rather than by app-side maths. A punch
    with no coordinate cannot be placed, so it is refused before the query runs.
    """
    if lat is None or lng is None:
        raise BusinessRuleProblem(f"A field punch at {location_code!r} requires a location fix")
    inside = (
        await session.execute(
            text(
                "SELECT ST_DWithin("
                "ST_MakePoint(:lng, :lat)::geography, "
                "ST_MakePoint(:clng, :clat)::geography, :radius_m)"
            ),
            {
                "lng": float(lng),
                "lat": float(lat),
                "clng": float(fence.center_lng),
                "clat": float(fence.center_lat),
                "radius_m": fence.radius_m,
            },
        )
    ).scalar_one()
    if not inside:
        raise BusinessRuleProblem(f"Punch is outside the allowed area for {location_code}")


async def check_in(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CheckInIn,
    *,
    request: Request | None = None,
) -> AttendanceRecord:
    """Open today's attendance for the calling employee.

    One record per employee per day: a second check-in on the same day is a conflict, not a
    silent re-open, so a double punch is visible rather than swallowed. A field punch at a
    location that has a geo-fence is checked against it before the row is written.
    """
    employee_id = _subject_employee(ctx)
    work_date = work_date_in(datetime.now(UTC), settings().org_timezone)

    if await selectors.get_day_record(session, ctx.organization_id, employee_id, work_date):
        raise ConflictProblem(f"Attendance for {work_date.isoformat()} is already open")

    if payload.shift_id is not None and not await selectors.get_shift(
        session, ctx.organization_id, payload.shift_id
    ):
        raise NotFoundProblem("That shift is not defined for this organization")

    if payload.work_mode == "field" and payload.location_code is not None:
        fence = await selectors.get_geofence_by_location(
            session, ctx.organization_id, payload.location_code
        )
        if fence is not None:
            await _assert_within_fence(
                session,
                fence,
                lat=payload.lat,
                lng=payload.lng,
                location_code=payload.location_code,
            )

    record = AttendanceRecord(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        employee_id=employee_id,
        work_date=work_date,
        check_in_at=datetime.now(UTC),
        source=payload.source,
        work_mode=payload.work_mode,
        location_code=payload.location_code,
        shift_id=payload.shift_id,
        check_in_lat=payload.lat,
        check_in_lng=payload.lng,
        status=AttendanceStatus.PRESENT.value,
        notes=payload.notes,
    )
    session.add(record)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="attendance.checked_in",
        entity="attendance",
        entity_id=record.id,
        after={
            "employee_id": record.employee_id,
            "work_date": record.work_date,
            "work_mode": record.work_mode,
            "location_code": record.location_code,
            "source": record.source,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CHECKED_IN,
        aggregate_type="attendance",
        aggregate_id=record.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "attendance_id": str(record.id),
            "employee_id": str(record.employee_id),
            "work_date": record.work_date.isoformat(),
            "work_mode": record.work_mode,
            "location_code": record.location_code,
        },
        actor=actor_for(ctx),
    )
    return record


async def check_out(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CheckOutIn,
    *,
    request: Request | None = None,
) -> AttendanceRecord:
    """Close today's open attendance for the calling employee.

    There must be an open record for today, and it must not already be closed. The check-out
    instant is stamped server-side and must strictly follow the check-in — the ordering rule the
    pure `domain` function owns, applied here so a clock skew cannot record a day that ended
    before it began.
    """
    employee_id = _subject_employee(ctx)
    work_date = work_date_in(datetime.now(UTC), settings().org_timezone)

    record = await selectors.get_day_record(session, ctx.organization_id, employee_id, work_date)
    if record is None:
        raise NotFoundProblem(f"No open attendance for {work_date.isoformat()} to close")
    if record.check_out_at is not None:
        raise BusinessRuleProblem(f"Attendance for {work_date.isoformat()} is already closed")

    now = datetime.now(UTC)
    try:
        assert_checkout_after_checkin(record.check_in_at, now)
    except CheckOutBeforeCheckIn as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    record.check_out_at = now
    record.check_out_lat = payload.lat
    record.check_out_lng = payload.lng
    record.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="attendance.checked_out",
        entity="attendance",
        entity_id=record.id,
        after={"employee_id": record.employee_id, "check_out_at": record.check_out_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CHECKED_OUT,
        aggregate_type="attendance",
        aggregate_id=record.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "attendance_id": str(record.id),
            "employee_id": str(record.employee_id),
            "work_date": record.work_date.isoformat(),
            "check_out_at": record.check_out_at.isoformat(),
        },
        actor=actor_for(ctx),
    )
    return record


async def regularize_attendance(
    session: AsyncSession,
    ctx: ScopeContext,
    record: AttendanceRecord,
    target: str,
    *,
    reason: str,
    request: Request | None = None,
) -> AttendanceRecord:
    """Correct a day's status directly, with a reason, audited.

    There is **no approval workflow engine in this slice** (that is a later one). Regularisation
    here is exactly this: an authorised caller changes the status, the pure state machine in
    `domain.py` refuses an illegal step, and the change carries its reason into the audit row.
    """
    current = AttendanceStatus(record.status)
    to = AttendanceStatus(target)
    try:
        assert_can_transition(current, to)
    except IllegalTransition as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    record.status = to.value
    record.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="attendance.regularized",
        entity="attendance",
        entity_id=record.id,
        before={"status": current.value},
        after={"status": to.value},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REGULARIZED,
        aggregate_type="attendance",
        aggregate_id=record.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "attendance_id": str(record.id),
            "employee_id": str(record.employee_id),
            "from": current.value,
            "to": to.value,
        },
        actor=actor_for(ctx),
    )
    return record


def _consumer_actor() -> dict[str, Any]:
    """The provenance stamped on a write the approval-decision consumer makes — not a person and
    not a caller-supplied service, mirroring `payment.services._provider_actor`'s shape for the
    same reason: the outbox `actor` column always names *something*, even when nobody is at the
    keyboard."""
    return {"type": "consumer", "id": "attendance.consumer"}


async def request_regularization(
    session: AsyncSession,
    ctx: ScopeContext,
    record: AttendanceRecord,
    payload: RegularizationRequestIn,
    *,
    request: Request | None = None,
) -> RegularizationRequest:
    """Ask for a correction to your own attendance (ADR-0006 §11) — self-service, routed.

    Two checks the ADR asks for **at request time, not only when the decision lands**: the
    requested transition must be legal from the record's *current* status (the same state
    machine `regularize_attendance` enforces directly), and the record must not already carry a
    pending request — the partial unique index is the backstop for a race, this is the readable
    error for the ordinary case. The object-level "is this the caller's own record" check runs in
    the route, before this is ever called, the same shape `read_attendance` uses.

    Publishes `hrms.time.regularization.requested` — the event approval's consumer opens a
    request from — in the same transaction as the row, never inline (ADR-0000).
    """
    current = AttendanceStatus(record.status)
    target = AttendanceStatus(payload.requested_status)
    try:
        assert_can_transition(current, target)
    except IllegalTransition as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    if await selectors.get_pending_regularization(session, ctx.organization_id, record.id):
        raise ConflictProblem("A regularization request for this record is already pending")

    reg = RegularizationRequest(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        attendance_id=record.id,
        employee_id=record.employee_id,
        requested_status=target.value,
        reason=payload.reason,
        status="pending",
    )
    session.add(reg)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="attendance.regularization_requested",
        entity="regularization_request",
        entity_id=reg.id,
        after={
            "attendance_id": reg.attendance_id,
            "current_status": current.value,
            "requested_status": reg.requested_status,
        },
        reason=reg.reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REGULARIZATION_REQUESTED,
        aggregate_type="regularization_request",
        aggregate_id=reg.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "regularization_request_id": str(reg.id),
            "attendance_id": str(record.id),
            "employee_id": str(record.employee_id),
            "requested_status": reg.requested_status,
            "current_status": current.value,
            "reason": reg.reason,
            "date": record.work_date.isoformat(),
        },
        actor=actor_for(ctx),
    )
    return reg


def _publish_regularization_applied(
    session: AsyncSession,
    reg: RegularizationRequest,
    *,
    outcome: str,
    failure_reason: str | None = None,
) -> None:
    """Attendance's return leg (ADR-0006 §12). Published for **every** decision it consumes: a
    landed approval and a rejection are both `applied` — the event means "I applied your
    decision", not "I corrected the day" — and an approval whose transition has since become
    illegal is `failed`. Never raises: approval's own alarm on `application_state` is what finds
    the population where this leg never runs, not a redelivery loop here."""
    payload: dict[str, Any] = {
        "organization_id": str(reg.organization_id),
        "regularization_request_id": str(reg.id),
        "approval_request_id": (str(reg.approval_request_id) if reg.approval_request_id else None),
        "request_type": REQUEST_TYPE_REGULARIZATION,
        "outcome": outcome,
    }
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    publish_event(
        session,
        OutboxEvent,
        organization_id=reg.organization_id,
        event_type=EVT_REGULARIZATION_APPLIED,
        aggregate_type="regularization_request",
        aggregate_id=reg.id,
        payload=payload,
        actor=_consumer_actor(),
    )


async def apply_regularization_opened(
    session: AsyncSession,
    reg: RegularizationRequest,
    *,
    approval_request_id: uuid.UUID,
    approver_employee_id: uuid.UUID | None,
    approver_name: str | None,
    opened_at: datetime,
) -> RegularizationRequest:
    """Apply `core.approval.request.opened` to a still-`pending` regularization request
    (ADR-0006 §12, the half of the loop that was specified and never built). Called only from
    `consumers.py`, which has already checked `reg.status == "pending"` — a replay, or `.opened`
    arriving after `.decided` for the same request, never reaches here, so this can never
    overwrite a terminal decision.

    Sets `approval_request_id` only if it is not already set (harmless either way — `.opened` and
    `.decided` both carry the same id, but `.decided` may have already written it if it somehow
    landed first). Sets `approval_opened_at`, `approver_employee_id` and `approver_name`
    unconditionally; a null `approver_employee_id` here means the step opened unassigned, not
    "unknown" — that is what `approval_opened_at` being set at all is for (see the model's
    docstring). `approver_name` is `consumers._display_name`'s own already-optional extraction
    (`None` for an absent key, an unassigned step, or a name the payload sets to `null`) — this
    function never distinguishes those cases either, it only stores what it is given.

    No outbox event: nothing downstream needs to know approval merely opened a step — attendance's
    own return leg is `.applied`, published only once a decision lands.
    """
    if reg.approval_request_id is None:
        reg.approval_request_id = approval_request_id
    reg.approval_opened_at = opened_at
    reg.approver_employee_id = approver_employee_id
    reg.approver_name = approver_name

    record_audit(
        session,
        AuditLog,
        organization_id=reg.organization_id,
        actor_id=None,
        acting_as="attendance.consumer",
        action="attendance.regularization_opened",
        entity="regularization_request",
        entity_id=reg.id,
        after={
            "approval_request_id": reg.approval_request_id,
            "approver_employee_id": approver_employee_id,
            "approver_name": approver_name,
            "unassigned": approver_employee_id is None,
        },
    )
    return reg


async def apply_regularization_decision(
    session: AsyncSession,
    reg: RegularizationRequest,
    record: AttendanceRecord,
    *,
    outcome: str,
    approval_request_id: uuid.UUID,
    decided_at: datetime,
    comment: str | None = None,
    decided_by_name: str | None = None,
    decided_by_user_id: uuid.UUID | None = None,
    self_approved: bool = False,
) -> RegularizationRequest:
    """Apply `core.approval.request.decided` to a still-`pending` regularization request
    (ADR-0006 §12). Called only from `consumers.py`, which has already checked `reg.status ==
    "pending"` — a replay or a stale `.opened` arriving after `.decided` never reaches here.

    There is no `ScopeContext`: the caller is an event, not a person, so the audit rows below
    carry no `actor_id` and are attributed to the consumer, the same "consumer-driven" shape
    ADR-0006 §13 specifies for approval's own opening and escalation rows.

    * **`rejected`** moves the request to `rejected` and stops — the attendance record itself is
      untouched, because rejecting a request is a decision about the request, not a correction.
    * **`approved`** re-validates the transition against the record's *current* status before
      applying it. HR may have corrected the day directly through the override endpoint while
      the request was in flight; if the step that was legal when the request was raised has since
      become illegal, this does **not** raise — raising would redeliver once and then dead-letter
      a fact that is not a processing error — it records `failed` with a reason instead.

    `comment` is the decider's own free text from `.decided`'s `comment` field, stored verbatim
    on `reg.decision_comment` (Text, plaintext — the same rule `reg.reason` already follows, data
    model §5) and threaded onto every audit row this function writes through `record_audit`'s
    `reason=` parameter, exactly how `request_regularization` carries the requester's own `reason`
    — never folded into a `before`/`after` blob.

    `decided_by_name` is `consumers._display_name`'s own already-optional extraction — `None` for
    an absent key or a decider outside approval's hierarchy copy (a legitimate org-level decision,
    not an error) — stored verbatim on `reg.decided_by_name`, the same plaintext, unmasked
    treatment `decision_comment` gets (data model §5: approval's own source is not sealed either).
    Unlike `comment`, it is a system-resolved fact rather than the decider's own authored text, so
    it travels in each audit row's `after` snapshot beside the status it accompanies, the same
    place `approver_employee_id` already travels in `apply_regularization_opened` — `reason=`
    stays reserved for `comment` alone, never overwritten.

    Either branch publishes `hrms.time.regularization.applied` from this same transaction.
    """
    # The decider is the audit actor (a user id); `acting_as` keeps naming the consumer.
    self_flag = {"self_approved": True} if self_approved else {}
    reg.approval_request_id = approval_request_id
    reg.decided_at = decided_at
    reg.decision_comment = comment
    reg.decided_by_name = decided_by_name

    if outcome == "rejected":
        reg.status = "rejected"
        record_audit(
            session,
            AuditLog,
            organization_id=reg.organization_id,
            actor_id=decided_by_user_id,
            acting_as="attendance.consumer",
            action="attendance.regularization_rejected",
            entity="regularization_request",
            entity_id=reg.id,
            before={"status": "pending"},
            after={"status": "rejected", "decided_by_name": decided_by_name, **self_flag},
            reason=comment,
        )
        _publish_regularization_applied(session, reg, outcome="applied")
        return reg

    current = AttendanceStatus(record.status)
    target = AttendanceStatus(reg.requested_status)
    try:
        assert_can_transition(current, target)
    except IllegalTransition:
        reg.status = "failed"
        reg.failure_reason = (
            f"The record has since moved to {current.value!r}; a correction to "
            f"{target.value!r} is no longer legal."
        )[:500]
        record_audit(
            session,
            AuditLog,
            organization_id=reg.organization_id,
            actor_id=decided_by_user_id,
            acting_as="attendance.consumer",
            action="attendance.regularization_failed",
            entity="regularization_request",
            entity_id=reg.id,
            before={"status": "pending"},
            after={
                "status": "failed",
                "failure_reason": reg.failure_reason,
                "decided_by_name": decided_by_name,
            },
            reason=comment,
        )
        _publish_regularization_applied(
            session, reg, outcome="failed", failure_reason=reg.failure_reason
        )
        return reg

    record.status = target.value
    record.updated_by = "attendance.consumer"
    reg.status = "approved"

    record_audit(
        session,
        AuditLog,
        organization_id=reg.organization_id,
        actor_id=decided_by_user_id,
        acting_as="attendance.consumer",
        action="attendance.regularization_approved",
        entity="regularization_request",
        entity_id=reg.id,
        before={"status": "pending"},
        after={"status": "approved", "decided_by_name": decided_by_name, **self_flag},
        reason=comment,
    )
    # `reason` here is the requester's own stated reason for the correction (`reg.reason`), not
    # the decider's comment — this row documents why the *attendance record* changed, which is
    # what the requester asked for; the row above documents the decision itself.
    record_audit(
        session,
        AuditLog,
        organization_id=reg.organization_id,
        actor_id=decided_by_user_id,
        acting_as="attendance.consumer",
        action="attendance.regularized",
        entity="attendance",
        entity_id=record.id,
        before={"status": current.value},
        after={"status": target.value},
        reason=reg.reason,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=reg.organization_id,
        event_type=EVT_REGULARIZED,
        aggregate_type="attendance",
        aggregate_id=record.id,
        payload={
            "organization_id": str(reg.organization_id),
            "attendance_id": str(record.id),
            "employee_id": str(record.employee_id),
            "from": current.value,
            "to": target.value,
        },
        actor=_consumer_actor(),
    )
    _publish_regularization_applied(session, reg, outcome="applied")
    return reg


# --------------------------------------------------------------------- shifts


async def create_shift(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ShiftIn,
    *,
    request: Request | None = None,
) -> Shift:
    """Define an organization shift. `code` is unique within the organization."""
    if await selectors.get_shift_by_code(session, ctx.organization_id, payload.code):
        raise ConflictProblem(f"A shift coded {payload.code!r} already exists")

    shift = Shift(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        **payload.model_dump(),
    )
    session.add(shift)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="shift.created",
        entity="shift",
        entity_id=shift.id,
        after={"code": shift.code, "name": shift.name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SHIFT_CREATED,
        aggregate_type="shift",
        aggregate_id=shift.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "shift_id": str(shift.id),
            "code": shift.code,
        },
        actor=actor_for(ctx),
    )
    return shift


_SHIFT_TRACKED = ("name", "start_time", "end_time", "grace_minutes", "is_night")


async def update_shift(
    session: AsyncSession,
    ctx: ScopeContext,
    shift: Shift,
    payload: ShiftPatch,
    *,
    request: Request | None = None,
) -> Shift:
    """Patch a shift's window or grace. `code` is not editable — see the schema."""
    changes = payload.model_dump(exclude_unset=True)
    before = {f: shift.as_dict().get(f) for f in _SHIFT_TRACKED}
    for field, value in changes.items():
        setattr(shift, field, value)
    shift.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="shift.updated",
        entity="shift",
        entity_id=shift.id,
        before=before,
        after={f: shift.as_dict().get(f) for f in _SHIFT_TRACKED},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SHIFT_UPDATED,
        aggregate_type="shift",
        aggregate_id=shift.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "shift_id": str(shift.id),
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return shift


async def delete_shift(
    session: AsyncSession,
    ctx: ScopeContext,
    shift: Shift,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a shift. `shifts.id` is the one foreign key `attendance_records.shift_id`
    holds inside this schema, and it carries no `ON DELETE` action — a soft delete never touches
    that row, so past punches keep their shift reference exactly as they always have. There is no
    "assignment" or "roster" table anywhere in this service (or in employee-service, which holds
    no shift/geofence reference at all) that names a shift as a standing, ongoing obligation, so
    there is nothing "live" to refuse this against: the only guard a retired shift needs is that
    `selectors.get_shift` already stops offering it to a *new* check-in (404, `services.check_in`)
    once `deleted_at` is set. `uq_shifts_org_code` is a partial index on `deleted_at is null`
    (migration `f4c1a9b3d2e7`), so the code frees up for reuse the moment this commits."""
    before_deleted_at = shift.deleted_at
    shift.deleted_at = datetime.now(UTC)
    shift.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="shift.deleted",
        entity="shift",
        entity_id=shift.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": shift.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SHIFT_DELETED,
        aggregate_type="shift",
        aggregate_id=shift.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "shift_id": str(shift.id),
            "code": shift.code,
        },
        actor=actor_for(ctx),
    )


# --------------------------------------------------------------------- geofences


async def create_geofence(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: GeofenceIn,
    *,
    request: Request | None = None,
) -> Geofence:
    """Define the allowed field-punch area for a location. `location_code` is unique per org."""
    if await selectors.get_geofence_by_location(
        session, ctx.organization_id, payload.location_code
    ):
        raise ConflictProblem(f"A geo-fence for {payload.location_code!r} already exists")

    fence = Geofence(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        **payload.model_dump(),
    )
    session.add(fence)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="geofence.created",
        entity="geofence",
        entity_id=fence.id,
        after={"location_code": fence.location_code, "radius_m": fence.radius_m},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GEOFENCE_CREATED,
        aggregate_type="geofence",
        aggregate_id=fence.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "geofence_id": str(fence.id),
            "location_code": fence.location_code,
        },
        actor=actor_for(ctx),
    )
    return fence


_GEOFENCE_TRACKED = ("name", "center_lat", "center_lng", "radius_m")


async def update_geofence(
    session: AsyncSession,
    ctx: ScopeContext,
    fence: Geofence,
    payload: GeofencePatch,
    *,
    request: Request | None = None,
) -> Geofence:
    """Patch a geo-fence's centre, radius or name. `location_code` is not editable."""
    changes = payload.model_dump(exclude_unset=True)
    before = {f: fence.as_dict().get(f) for f in _GEOFENCE_TRACKED}
    for field, value in changes.items():
        setattr(fence, field, value)
    fence.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="geofence.updated",
        entity="geofence",
        entity_id=fence.id,
        before=before,
        after={f: fence.as_dict().get(f) for f in _GEOFENCE_TRACKED},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GEOFENCE_UPDATED,
        aggregate_type="geofence",
        aggregate_id=fence.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "geofence_id": str(fence.id),
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return fence


async def delete_geofence(
    session: AsyncSession,
    ctx: ScopeContext,
    fence: Geofence,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a geo-fence. `AttendanceRecord` carries no foreign key to `geofences` — a
    fence is matched against a punch's `location_code` at check-in time
    (`selectors.get_geofence_by_location`), never stored as a reference on the record — so there
    is no "live assignment" a retired fence could orphan. A field punch against a location whose
    fence was just deleted simply stops being fenced (falls back to whatever `check_in` does for
    an unfenced `location_code`); it does not fail. `uq_geofences_org_location` is a partial
    index on `deleted_at is null` (migration `f4c1a9b3d2e7`), so the location code frees up for a
    new fence the moment this commits."""
    before_deleted_at = fence.deleted_at
    fence.deleted_at = datetime.now(UTC)
    fence.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="geofence.deleted",
        entity="geofence",
        entity_id=fence.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": fence.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_GEOFENCE_DELETED,
        aggregate_type="geofence",
        aggregate_id=fence.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "geofence_id": str(fence.id),
            "location_code": fence.location_code,
        },
        actor=actor_for(ctx),
    )
