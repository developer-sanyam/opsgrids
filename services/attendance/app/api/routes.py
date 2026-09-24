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

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services, signals
from app.config import settings
from app.domain import ALLOWED_TRANSITIONS, AttendanceStatus
from app.models import SCHEMA, AttendanceRecord, PunchSignal, RegularizationRequest
from app.schemas import (
    AttendanceOut,
    CheckInIn,
    CheckOutIn,
    FlagOut,
    FlagReasonName,
    GeofenceIn,
    GeofenceOut,
    GeofencePatch,
    RegularizationRequestIn,
    RegularizationRequestOut,
    RegularizeIn,
    ServiceInfo,
    ShiftIn,
    ShiftOut,
    ShiftPatch,
    SignalSettingsIn,
    SignalSettingsOut,
    SignalSummaryOut,
    SignalUnmasked,
    SignalUnmaskIn,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import ForbiddenProblem, NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the HRMS scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers HRMS scope questions.
scope.register()

router = APIRouter(tags=["attendance"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one. Routes therefore do
#: not open transactions of their own.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

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
#: The self-service ask for a correction (ADR-0006 §11). `max_scope` is OWN in the catalogue, so
#: there is no floor to set here — the ceiling already makes "request a correction for someone
#: else" ungrantable through the normal install path. The object-level check still runs in the
#: route (`_assert_requesting_own_record`), comparing identities directly rather than going
#: through `assert_can_touch`/`Scope.ORG`, because a permission's ceiling is not itself an
#: object-level check and this one has no scope at which "for someone else" is legitimate.
AttendanceRequest = Annotated[ScopeContext, Depends(require("hrms.time.attendance.request"))]
#: R22. Reading anti-spoofing flags has no self-service reading — an employee is not shown the rules
#: applied to them — so the floor is TEAM: an OWN grant is refused, not quietly answered with the
#: caller's own flags. The permission's ceiling is ORG (HR).
AttendanceFlagRead = Annotated[
    ScopeContext, Depends(require("hrms.time.attendance_flag.read", minimum=Scope.TEAM))
]
#: R22. Unmasking a punch's IP address or device id: its own dangerous permission **and** a recent
#: second factor, exactly as unmasking an employee's PII is (`employee/app/api/routes.py`). No
#: floor: the object-level check re-scopes to the one punch named, and the catalogue grants it to
#: HR at ORG only.
AttendanceUnmask = Annotated[
    ScopeContext,
    Depends(require("hrms.time.attendance.read_sensitive", amr={"otp"})),
]
ShiftRead = Annotated[ScopeContext, Depends(require("hrms.time.shift.read"))]
#: Shifts and geo-fences are organization definitions: read by everyone, written only by an
#: administrator. The permission tops out at ORG; the floor makes that explicit at the route.
ShiftWrite = Annotated[ScopeContext, Depends(require("hrms.time.shift.write", minimum=Scope.ORG))]


@router.get("/attendance/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


def _regularization_out(
    reg: RegularizationRequest, record: AttendanceRecord
) -> RegularizationRequestOut:
    """`location_code` is composed here, not stored on the row — see the field's docstring in
    `schemas.py` for the open contract question this settles. `approver_unassigned` is likewise
    computed rather than stored, so `approval_opened_at`/`approver_employee_id` stay the one
    source of truth: null (routing not yet known) while `.opened` has not landed, else whether
    that step opened with no approver at all (ADR-0006 §4)."""
    approver_unassigned = (
        None if reg.approval_opened_at is None else reg.approver_employee_id is None
    )
    return RegularizationRequestOut.model_validate(reg).model_copy(
        update={
            "location_code": record.location_code,
            "approver_unassigned": approver_unassigned,
        }
    )


def _assert_requesting_own_record(ctx: ScopeContext, record: AttendanceRecord) -> None:
    """`hrms.time.attendance.request` is self-service by construction: its catalogue ceiling is
    OWN, so no legitimately-issued grant reaches another employee's record. But
    `ScopeContext.assert_can_touch` returns unconditionally at `Scope.ORG` — it exists for
    permissions that genuinely have an organization-wide reading — so a single over-scoped
    grant string (a bad install, a forged `X-Perms` header) would otherwise let a caller raise a
    correction against *any* employee's record, and approval would then route it to that
    victim's manager as if the victim had asked. There is no scope at which "for someone else"
    is ever legitimate here, so this compares identities directly rather than going through
    `assert_can_touch` at all — the same shape `services._subject_employee` uses for
    check-in/check-out.
    """
    subject = ctx.subject_ref
    if subject is None or record.employee_id != subject:
        raise ForbiddenProblem("You may only request a correction to your own attendance record")


def _attendance_out(
    record: AttendanceRecord, regularization: RegularizationRequest | None
) -> AttendanceOut:
    """Compose the read model the screens doc asks for: the record plus its correction state
    and the transitions the server's own state machine allows from here (ADR-0006 §11, **C3**).
    `regularization` is a second table's row, fetched separately — `AttendanceOut.model_validate`
    never sees it on the ORM object, so it is attached here rather than left to `from_attributes`.
    """
    out = AttendanceOut.model_validate(record)
    return out.model_copy(
        update={
            "regularization": (
                _regularization_out(regularization, record) if regularization is not None else None
            ),
            "allowed_transitions": sorted(
                t.value for t in ALLOWED_TRANSITIONS[AttendanceStatus(record.status)]
            ),
        }
    )


# --------------------------------------------------------------------- anti-spoofing (R22)
#
# Declared **before** `/attendance/{record_id}` on purpose: FastAPI matches in registration order,
# and `flags` or `signal-settings` would otherwise be captured as a record id and answered 422.


def _signal_out(signal: PunchSignal) -> SignalSummaryOut:
    return SignalSummaryOut.model_validate(signal)


@router.get(
    "/attendance/flags",
    response_model=Page[FlagOut],
    summary="Attendance anti-spoofing flags",
    dependencies=[Entitled],
)
async def list_flags(
    session: SessionDep,
    ctx: AttendanceFlagRead,
    params: PageQuery,
    employee_id: Annotated[uuid.UUID | None, Query()] = None,
    reason: Annotated[FlagReasonName | None, Query()] = None,
    date_from: Annotated[
        date | None, Query(description="Inclusive lower bound on the work day")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Inclusive upper bound on the work day")
    ] = None,
) -> Page[FlagOut]:
    """The flags raised on punches, narrowed to the caller's scope exactly as the attendance
    records they describe are — a TEAM manager sees flags on their reports and nobody else's. The
    signal beside each flag is **masked**: the address and device id come back only from the unmask
    route. Observe-only: these are prompts to look, never a verdict on the employee."""
    stmt = selectors.flags_stmt(
        ctx, employee_id=employee_id, reason=reason, date_from=date_from, date_to=date_to
    )
    rows, total = await paginate(session, stmt, params)
    # One query for the page's signals and one for its days, not one per row.
    signals_by_id = await selectors.get_signals_by_ids(
        session, ctx.organization_id, [f.signal_id for f in rows]
    )
    work_dates = await selectors.get_work_dates(
        session, ctx.organization_id, [f.attendance_id for f in rows]
    )
    items = [
        FlagOut.model_validate(f).model_copy(
            update={
                "work_date": work_dates.get(f.attendance_id),
                "signal": (
                    _signal_out(signals_by_id[f.signal_id])
                    if f.signal_id in signals_by_id
                    else None
                ),
            }
        )
        for f in rows
    ]
    return Page.build(items, total=total, params=params)


@router.post(
    "/attendance/signals/{signal_id}/unmask",
    response_model=SignalUnmasked,
    summary="Reveal the IP address and device id recorded with a punch",
    dependencies=[Entitled],
)
async def unmask_signal(
    signal_id: uuid.UUID,
    payload: SignalUnmaskIn,
    session: SessionDep,
    ctx: AttendanceUnmask,
    request: Request,
    response: Response,
) -> SignalUnmasked:
    """The single decrypting endpoint for punch signals. Guarded by the dangerous
    `read_sensitive` permission and a second-factor step-up, then re-scoped to this one punch: a
    TEAM grant reaches only the caller's reports', an ORG grant anyone's in the organization. The
    service opens the requested fields and writes the `entity.sensitive_read` audit row in the same
    transaction, so a revealed value never exists without a record that it was revealed."""
    signal = await selectors.get_signal(session, ctx.organization_id, signal_id)
    if signal is None:
        raise NotFoundProblem("That punch signal is not part of this organization")
    record = await selectors.get_attendance(session, ctx.organization_id, signal.attendance_id)
    if record is None:
        raise NotFoundProblem("That punch signal is not part of this organization")
    # The named vulnerability: a scoped list beside an unguarded detail. Re-apply the caller's scope
    # to the single punch they named, before anything is opened.
    team_owner_id = (
        await selectors.get_team_manager(session, ctx.organization_id, signal.employee_id)
        if ctx.scope is Scope.TEAM
        else None
    )
    ctx.assert_can_touch(
        owner_id=signal.employee_id,
        team_owner_id=team_owner_id,
        location_code=record.location_code,
    )
    revealed = await signals.unmask_signal(session, ctx, signal, payload.fields, request=request)
    # A revealed address or device id must not be kept by a browser cache, a proxy or the
    # back/forward store: the audit row says it was read once, and a stored copy would be read
    # again unrecorded.
    response.headers["Cache-Control"] = "no-store"
    return revealed


@router.get(
    "/attendance/signal-settings",
    response_model=SignalSettingsOut,
    summary="The organization's anti-spoofing rules",
    dependencies=[Entitled],
)
async def read_signal_settings(session: SessionDep, ctx: ShiftWrite) -> SignalSettingsOut:
    """Administrators only (`shift.write:ORG`, the org time-admin permission): the thresholds and
    the allowed ranges tell a person exactly how to stay under them. An organization that has never
    saved a setting reads back every rule off.

    Limit of the address rule, stated plainly: the office-network rule applies only to punches
    declared as office. The work mode is the client's own claim, so declaring `remote` sidesteps
    it; the rule catches an office punch from the wrong network, not a client that misstates its
    work mode."""
    row = await selectors.get_signal_settings(session, ctx.organization_id)
    if row is None:
        return SignalSettingsOut()
    return SignalSettingsOut.model_validate(row)


@router.put(
    "/attendance/signal-settings",
    response_model=SignalSettingsOut,
    summary="Set the organization's anti-spoofing rules",
    dependencies=[Entitled],
)
async def put_signal_settings(
    payload: SignalSettingsIn, session: SessionDep, ctx: ShiftWrite, request: Request
) -> SignalSettingsOut:
    """A full replacement — the same body always yields the same state — audited with the rules
    as they were and as they are. Every rule is observe-only: it can raise a flag and can never
    refuse or alter a punch.

    Limit of the address rule, stated plainly: the office-network rule applies only to punches
    declared as office. The work mode is the client's own claim, so declaring `remote` sidesteps
    it; the rule catches an office punch from the wrong network, not a client that misstates its
    work mode."""
    row = await signals.put_settings(session, ctx, payload, request=request)
    return SignalSettingsOut.model_validate(row)


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
    return _attendance_out(record, None)


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
    regularization = await selectors.get_latest_regularization(
        session, ctx.organization_id, record.id
    )
    return _attendance_out(record, regularization)


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
    # Batched, not one query per row (CLAUDE.md: a loop with a query in it is a bug).
    regularizations = await selectors.get_latest_regularizations(
        session, ctx.organization_id, [r.id for r in rows]
    )
    items = [_attendance_out(r, regularizations.get(r.id)) for r in rows]
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
    # the object-level check re-applies the caller's scope to the single row they named. For TEAM
    # the row's owner is looked up on the same projection the list joins, so the two cannot
    # disagree; every other rung answers from the record alone and pays no extra query.
    team_owner_id = (
        await selectors.get_team_manager(session, ctx.organization_id, record.employee_id)
        if ctx.scope is Scope.TEAM
        else None
    )
    ctx.assert_can_touch(
        owner_id=record.employee_id,
        team_owner_id=team_owner_id,
        location_code=record.location_code,
    )
    regularization = await selectors.get_latest_regularization(
        session, ctx.organization_id, record.id
    )
    return _attendance_out(record, regularization)


@router.post(
    "/attendance/{record_id}/regularize",
    response_model=AttendanceOut,
    summary="Correct an attendance record's status directly (HR override)",
    dependencies=[Entitled],
)
async def regularize_attendance(
    record_id: uuid.UUID,
    payload: RegularizeIn,
    session: SessionDep,
    ctx: AttendanceRegularize,
    request: Request,
) -> AttendanceOut:
    """The documented escape hatch for a stuck or urgent correction (ADR-0006 §6, §11) — an
    administrator changes the status directly, audited in its own domain, unaffected by whether a
    self-service request happens to be pending. If one is, its own decision lands later through
    the approval consumer and finds the transition it was raised for no longer legal, and reports
    `failed` rather than silently overwriting what HR just did."""
    record = await selectors.get_attendance(session, ctx.organization_id, record_id)
    if record is None:
        raise NotFoundProblem("That attendance record is not part of this organization")
    updated = await services.regularize_attendance(
        session, ctx, record, payload.to, reason=payload.reason, request=request
    )
    regularization = await selectors.get_latest_regularization(
        session, ctx.organization_id, updated.id
    )
    return _attendance_out(updated, regularization)


@router.post(
    "/attendance/{record_id}/regularization-requests",
    response_model=RegularizationRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask for a correction to your own attendance record",
    dependencies=[Entitled],
)
async def request_regularization(
    record_id: uuid.UUID,
    payload: RegularizationRequestIn,
    session: SessionDep,
    ctx: AttendanceRequest,
    request: Request,
) -> RegularizationRequestOut:
    """Self-service (ADR-0006 §11). Opens a `pending` request and publishes
    `hrms.time.regularization.requested`; approval routes it and reports the decision back on
    `core.approval.request.decided`, applied by `app.consumers`."""
    record = await selectors.get_attendance(session, ctx.organization_id, record_id)
    if record is None:
        raise NotFoundProblem("That attendance record is not part of this organization")
    # The named vulnerability again: the permission's OWN ceiling makes "request for someone
    # else" ungrantable, but a caller who could still name another person's record by id must be
    # refused *here*, not merely left unable to list it. `assert_can_touch` is not the right tool
    # for this one check — it returns unconditionally at ORG, which is correct for permissions
    # that have a real organization-wide reading, but this one never does. See
    # `_assert_requesting_own_record`.
    _assert_requesting_own_record(ctx, record)
    reg = await services.request_regularization(session, ctx, record, payload, request=request)
    return _regularization_out(reg, record)


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


@router.delete(
    "/shifts/{shift_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a shift",
    dependencies=[Entitled],
)
async def delete_shift(
    shift_id: uuid.UUID, session: SessionDep, ctx: ShiftWrite, request: Request
) -> Response:
    shift = await selectors.get_shift(session, ctx.organization_id, shift_id)
    if shift is None:
        raise NotFoundProblem("That shift is not part of this organization")
    await services.delete_shift(session, ctx, shift, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.delete(
    "/geofences/{geofence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a geo-fence",
    dependencies=[Entitled],
)
async def delete_geofence(
    geofence_id: uuid.UUID, session: SessionDep, ctx: ShiftWrite, request: Request
) -> Response:
    fence = await selectors.get_geofence(session, ctx.organization_id, geofence_id)
    if fence is None:
        raise NotFoundProblem("That geo-fence is not part of this organization")
    await services.delete_geofence(session, ctx, fence, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
