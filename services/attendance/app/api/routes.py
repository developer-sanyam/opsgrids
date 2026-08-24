"""HTTP surface for the attendance service.

Every route declares the permission it needs and, where the action has no meaningful "own"
reading, a **scope floor**. That distinction is the security boundary: every HRMS user holds
`hrms.time.attendance.write` at OWN so they can punch their own attendance, so a route guarded
only by the permission *name* would let anyone regularise anyone's record. Regularisation, and
every shift and geo-fence definition, therefore set `minimum=Scope.ORG` — they are
administrative acts on organization-wide data, not self-service.

Punching (check-in / check-out) is the deliberate exception: it is self-service by construction
— the record's employee is the caller's own `subject_ref`, never a body field — so OWN write is
correct and no floor is set.

Geo-fences are organization location definitions, exactly like shifts, so they are guarded by
`hrms.time.shift.write` too — one consistent "org time-admin" permission rather than two.

Every principal route is also guarded by `require_product("hrms")` — defence in depth. Reads go
through `selectors`, writes through `services`; a router that builds its own query is how an
organization leaks, so none of them do. Every route takes the tenant-bound session and opens no
transaction of its own — the chokepoint owns it.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    AttendanceOut,
    CheckInIn,
    CheckOutIn,
    GeofenceIn,
    GeofenceOut,
    GeofencePatch,
    RegularizeIn,
    ServiceInfo,
    ShiftIn,
    ShiftOut,
    ShiftPatch,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the HRMS scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers HRMS scope questions.
scope.register()

router = APIRouter(tags=["attendance"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one. Routes therefore do
#: not open transactions of their own.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `hrms` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("hrms"))

# --- read / write contexts ----------------------------------------------------------
AttendanceRead = Annotated[ScopeContext, Depends(require("hrms.time.attendance.read"))]
#: Punching is self-service: the record's employee is the caller's own subject reference, so OWN
#: write is legitimate and no scope floor is set.
AttendancePunch = Annotated[ScopeContext, Depends(require("hrms.time.attendance.write"))]
#: Regularising someone's day is an administrative correction, not self-service — so the floor is
#: ORG. Without it, every employee (who holds attendance.write at OWN) could rewrite statuses.
AttendanceRegularize = Annotated[
    ScopeContext, Depends(require("hrms.time.attendance.write", minimum=Scope.ORG))
]
ShiftRead = Annotated[ScopeContext, Depends(require("hrms.time.shift.read"))]
#: Shifts and geo-fences are organization definitions: read by everyone, written only by an
#: administrator. The permission tops out at ORG; the floor makes that explicit at the route.
ShiftWrite = Annotated[ScopeContext, Depends(require("hrms.time.shift.write", minimum=Scope.ORG))]


@router.get("/attendance/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# --------------------------------------------------------------------- attendance


@router.post(
    "/attendance/check-in",
    response_model=AttendanceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Punch in for today",
    dependencies=[Entitled],
)
async def check_in(
    payload: CheckInIn, session: SessionDep, ctx: AttendancePunch, request: Request
) -> AttendanceOut:
    record = await services.check_in(session, ctx, payload, request=request)
    return AttendanceOut.model_validate(record)


@router.post(
    "/attendance/check-out",
    response_model=AttendanceOut,
    summary="Punch out for today",
    dependencies=[Entitled],
)
async def check_out(
    payload: CheckOutIn, session: SessionDep, ctx: AttendancePunch, request: Request
) -> AttendanceOut:
    record = await services.check_out(session, ctx, payload, request=request)
    return AttendanceOut.model_validate(record)


@router.get(
    "/attendance",
    response_model=Page[AttendanceOut],
    summary="Attendance records",
    dependencies=[Entitled],
)
async def list_attendance(
    session: SessionDep,
    ctx: AttendanceRead,
    params: PageQuery,
    employee_id: Annotated[uuid.UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query(description="Inclusive lower bound")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound")] = None,
    record_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[AttendanceOut]:
    stmt = selectors.attendance_stmt(
        ctx,
        employee_id=employee_id,
        date_from=date_from,
        date_to=date_to,
        status=record_status,
    )
    rows, total = await paginate(session, stmt, params)
    items = [AttendanceOut.model_validate(r) for r in rows]
    return Page.build(items, total=total, params=params)


@router.get(
    "/attendance/{record_id}",
    response_model=AttendanceOut,
    summary="One attendance record",
    dependencies=[Entitled],
)
async def read_attendance(
    record_id: uuid.UUID, session: SessionDep, ctx: AttendanceRead
) -> AttendanceOut:
    record = await selectors.get_attendance(session, ctx.organization_id, record_id)
    if record is None:
        raise NotFoundProblem("That attendance record is not part of this organization")
    # Filtering the list and forgetting the detail is the named vulnerability. This closes it:
    # the object-level check re-applies the caller's scope to the single row they named. TEAM is
    # deferred — team_owner_id is None, so a TEAM-scoped caller is refused here (see scope.py).
    ctx.assert_can_touch(
        owner_id=record.employee_id,
        team_owner_id=None,
        location_code=record.location_code,
    )
    return AttendanceOut.model_validate(record)


@router.post(
    "/attendance/{record_id}/regularize",
    response_model=AttendanceOut,
    summary="Correct an attendance record's status",
    dependencies=[Entitled],
)
async def regularize_attendance(
    record_id: uuid.UUID,
    payload: RegularizeIn,
    session: SessionDep,
    ctx: AttendanceRegularize,
    request: Request,
) -> AttendanceOut:
    record = await selectors.get_attendance(session, ctx.organization_id, record_id)
    if record is None:
        raise NotFoundProblem("That attendance record is not part of this organization")
    updated = await services.regularize_attendance(
        session, ctx, record, payload.to, reason=payload.reason, request=request
    )
    return AttendanceOut.model_validate(updated)


# --------------------------------------------------------------------- shifts


@router.get(
    "/shifts", response_model=Page[ShiftOut], summary="Shift definitions", dependencies=[Entitled]
)
async def list_shifts(session: SessionDep, ctx: ShiftRead, params: PageQuery) -> Page[ShiftOut]:
    stmt = selectors.shifts_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    items = [ShiftOut.model_validate(s) for s in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/shifts",
    response_model=ShiftOut,
    status_code=status.HTTP_201_CREATED,
    summary="Define a shift",
    dependencies=[Entitled],
)
async def create_shift(
    payload: ShiftIn, session: SessionDep, ctx: ShiftWrite, request: Request
) -> ShiftOut:
    shift = await services.create_shift(session, ctx, payload, request=request)
    return ShiftOut.model_validate(shift)


@router.patch(
    "/shifts/{shift_id}",
    response_model=ShiftOut,
    summary="Update a shift",
    dependencies=[Entitled],
)
async def update_shift(
    shift_id: uuid.UUID,
    payload: ShiftPatch,
    session: SessionDep,
    ctx: ShiftWrite,
    request: Request,
) -> ShiftOut:
    shift = await selectors.get_shift(session, ctx.organization_id, shift_id)
    if shift is None:
        raise NotFoundProblem("That shift is not part of this organization")
    updated = await services.update_shift(session, ctx, shift, payload, request=request)
    return ShiftOut.model_validate(updated)


# --------------------------------------------------------------------- geofences


@router.get(
    "/geofences",
    response_model=Page[GeofenceOut],
    summary="Field-punch geo-fences",
    dependencies=[Entitled],
)
async def list_geofences(
    session: SessionDep, ctx: ShiftRead, params: PageQuery
) -> Page[GeofenceOut]:
    stmt = selectors.geofences_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    items = [GeofenceOut.model_validate(g) for g in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/geofences",
    response_model=GeofenceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Define a location's geo-fence",
    dependencies=[Entitled],
)
async def create_geofence(
    payload: GeofenceIn, session: SessionDep, ctx: ShiftWrite, request: Request
) -> GeofenceOut:
    fence = await services.create_geofence(session, ctx, payload, request=request)
    return GeofenceOut.model_validate(fence)


@router.patch(
    "/geofences/{geofence_id}",
    response_model=GeofenceOut,
    summary="Update a geo-fence",
    dependencies=[Entitled],
)
async def update_geofence(
    geofence_id: uuid.UUID,
    payload: GeofencePatch,
    session: SessionDep,
    ctx: ShiftWrite,
    request: Request,
) -> GeofenceOut:
    fence = await selectors.get_geofence(session, ctx.organization_id, geofence_id)
    if fence is None:
        raise NotFoundProblem("That geo-fence is not part of this organization")
    updated = await services.update_geofence(session, ctx, fence, payload, request=request)
    return GeofenceOut.model_validate(updated)
