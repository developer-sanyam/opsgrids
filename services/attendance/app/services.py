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
from app.domain import (
    AttendanceStatus,
    CheckOutBeforeCheckIn,
    IllegalTransition,
    assert_can_transition,
    assert_checkout_after_checkin,
)
from app.models import AttendanceRecord, AuditLog, Geofence, OutboxEvent, Shift
from app.schemas import (
    CheckInIn,
    CheckOutIn,
    GeofenceIn,
    GeofencePatch,
    ShiftIn,
    ShiftPatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `hrms.time.*` because these are the product's own, not the platform's.
EVT_CHECKED_IN = "hrms.time.attendance.checked_in"
EVT_CHECKED_OUT = "hrms.time.attendance.checked_out"
EVT_REGULARIZED = "hrms.time.attendance.regularized"
EVT_SHIFT_CREATED = "hrms.time.shift.created"
EVT_SHIFT_UPDATED = "hrms.time.shift.updated"
EVT_GEOFENCE_CREATED = "hrms.time.geofence.created"
EVT_GEOFENCE_UPDATED = "hrms.time.geofence.updated"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


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
    work_date = datetime.now(UTC).date()

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
        actor=_actor(ctx),
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
    work_date = datetime.now(UTC).date()

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
        actor=_actor(ctx),
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
        actor=_actor(ctx),
    )
    return record


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
        actor=_actor(ctx),
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
        actor=_actor(ctx),
    )
    return shift


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
        actor=_actor(ctx),
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
        actor=_actor(ctx),
    )
    return fence
