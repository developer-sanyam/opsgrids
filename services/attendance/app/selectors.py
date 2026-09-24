"""Reads for the attendance service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.

The scope columns for an attendance record are:

    OWN      -> AttendanceRecord.employee_id             (the caller's own records)
    TEAM     -> TeamMembership.manager_employee_id       (the caller's current direct reports)
    LOCATION -> AttendanceRecord.location_code           (a single code column)

TEAM is a **join to a projection**, not a column on the record: the reporting line is
employee-service's fact, and attendance keeps its own copy of it (`models.TeamMembership`), fed by
events. The join is added only for a TEAM-scoped caller — every other rung answers from the
record alone — and it is an inner join, so an employee the projection has never heard of matches
no manager. An unpopulated projection therefore shows a manager nobody: the safe way to be wrong.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.domain import STATUS_EXITED
from app.models import (
    AttendanceFlag,
    AttendanceRecord,
    Geofence,
    PunchSignal,
    RegularizationRequest,
    Shift,
    SignalSettings,
    TeamMembership,
)
from platform_core.auth import Scope, ScopeContext


def _apply_scope(
    ctx: ScopeContext,
    stmt: Select[Any],
    *,
    org_col: InstrumentedAttribute[Any],
    employee_col: InstrumentedAttribute[Any],
    location_col: InstrumentedAttribute[Any],
) -> Select[Any]:
    """Inject the caller's scope into a statement over rows that belong to an employee.

    The one place the TEAM join is built, so every list that narrows by "whose row is this" —
    attendance records, and the flags on them — answers TEAM the same way and cannot drift. For a
    TEAM-scoped caller the row is inner-joined to the reporting-line projection and `ctx.apply` is
    handed the manager column; every other rung answers from the row alone and pays no join.
    `exited` employees are excluded (`models.TeamMembership`), and both join conditions and the
    filter name the tenant beside RLS rather than instead of it.
    """
    team_col = None
    if ctx.scope is Scope.TEAM:
        stmt = stmt.join(
            TeamMembership,
            and_(
                TeamMembership.organization_id == org_col,
                TeamMembership.employee_id == employee_col,
            ),
        ).where(
            TeamMembership.organization_id == ctx.organization_id,
            TeamMembership.status != STATUS_EXITED,
        )
        team_col = TeamMembership.manager_employee_id
    return ctx.apply(stmt, owner_col=employee_col, team_col=team_col, location_col=location_col)


def attendance_stmt(
    ctx: ScopeContext,
    *,
    employee_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered attendance list. `ctx.apply` is the enforcement point.

    For a TEAM-scoped caller the record is joined to the reporting-line projection and
    `ctx.apply` compares the projection's manager column to the caller's own employee id — so the
    scope rule itself (a caller with no employee id matches nothing, never everything) is the
    platform's, not re-implemented here. `exited` employees are excluded: a manager's team is
    their *current* reports (see `models.TeamMembership`). Both join conditions name the tenant,
    beside RLS rather than instead of it.
    """
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.organization_id == ctx.organization_id,
        AttendanceRecord.deleted_at.is_(None),
    )
    stmt = _apply_scope(
        ctx,
        stmt,
        org_col=AttendanceRecord.organization_id,
        employee_col=AttendanceRecord.employee_id,
        location_col=AttendanceRecord.location_code,
    )

    if employee_id:
        stmt = stmt.where(AttendanceRecord.employee_id == employee_id)
    if date_from:
        stmt = stmt.where(AttendanceRecord.work_date >= date_from)
    if date_to:
        stmt = stmt.where(AttendanceRecord.work_date <= date_to)
    if status:
        stmt = stmt.where(AttendanceRecord.status == status)

    return stmt.order_by(AttendanceRecord.work_date.desc(), AttendanceRecord.employee_id)


async def get_attendance(
    session: AsyncSession, org_id: uuid.UUID, record_id: uuid.UUID
) -> AttendanceRecord | None:
    """Org-scoped fetch by id. Returns None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.id == record_id,
        AttendanceRecord.organization_id == org_id,
        AttendanceRecord.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_team_manager(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> uuid.UUID | None:
    """The manager `employee_id` currently reports to, or None.

    The object-level half of TEAM scope: `ctx.assert_can_touch(team_owner_id=...)` compares this to
    the caller's own employee id, so a record a manager could name by id but whose owner is not
    their report is refused exactly as the list refuses it. None covers every "not in anyone's
    team" case — no projection row yet, no manager, or an `exited` employee — and `assert_can_touch`
    refuses all of them.
    """
    stmt = select(TeamMembership.manager_employee_id).where(
        TeamMembership.organization_id == org_id,
        TeamMembership.employee_id == employee_id,
        TeamMembership.status != STATUS_EXITED,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_day_record(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, work_date: date
) -> AttendanceRecord | None:
    """The one record for an employee on a date, or None. Backs the check-in/out uniqueness."""
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.organization_id == org_id,
        AttendanceRecord.employee_id == employee_id,
        AttendanceRecord.work_date == work_date,
        AttendanceRecord.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- regularization requests


async def get_pending_regularization(
    session: AsyncSession, org_id: uuid.UUID, attendance_id: uuid.UUID
) -> RegularizationRequest | None:
    """The one `pending` request against a record, or None — backs the partial unique index's
    friendly error and the "may I raise a second one" check at request time."""
    stmt = select(RegularizationRequest).where(
        RegularizationRequest.organization_id == org_id,
        RegularizationRequest.attendance_id == attendance_id,
        RegularizationRequest.status == "pending",
        RegularizationRequest.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_regularization(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> RegularizationRequest | None:
    """Org-scoped fetch by id — the consumer's lookup by `source_ref` (ADR-0006 §12)."""
    stmt = select(RegularizationRequest).where(
        RegularizationRequest.id == request_id,
        RegularizationRequest.organization_id == org_id,
        RegularizationRequest.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_latest_regularization(
    session: AsyncSession, org_id: uuid.UUID, attendance_id: uuid.UUID
) -> RegularizationRequest | None:
    """The most recent regularization request against one record, or None — what a single-record
    screen shows as the correction state."""
    stmt = (
        select(RegularizationRequest)
        .where(
            RegularizationRequest.organization_id == org_id,
            RegularizationRequest.attendance_id == attendance_id,
            RegularizationRequest.deleted_at.is_(None),
        )
        .order_by(RegularizationRequest.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_latest_regularizations(
    session: AsyncSession, org_id: uuid.UUID, attendance_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, RegularizationRequest]:
    """The most recent regularization request per record, batched into one query for a whole
    page rather than one per row — a loop with a query in it is a bug (CLAUDE.md)."""
    if not attendance_ids:
        return {}
    stmt = (
        select(RegularizationRequest)
        .where(
            RegularizationRequest.organization_id == org_id,
            RegularizationRequest.attendance_id.in_(attendance_ids),
            RegularizationRequest.deleted_at.is_(None),
        )
        # `DISTINCT ON` under the postgresql dialect this service always runs against — verified
        # against SQLAlchemy 2.0.43 rather than assumed (`select(...).distinct(col)` compiles to
        # a plain, unhelpful `DISTINCT` on every other dialect).
        .distinct(RegularizationRequest.attendance_id)
        .order_by(RegularizationRequest.attendance_id, RegularizationRequest.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {r.attendance_id: r for r in rows}


# --------------------------------------------------------------------- shifts


def shifts_stmt(ctx: ScopeContext) -> Select[Any]:
    """Org-scoped shift list. Shifts are org definitions, so there is no per-row narrowing
    beyond the tenant — but the explicit `organization_id` filter still sits beside RLS."""
    return (
        select(Shift)
        .where(Shift.organization_id == ctx.organization_id, Shift.deleted_at.is_(None))
        .order_by(Shift.code)
    )


async def get_shift(session: AsyncSession, org_id: uuid.UUID, shift_id: uuid.UUID) -> Shift | None:
    stmt = select(Shift).where(
        Shift.id == shift_id,
        Shift.organization_id == org_id,
        Shift.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_shift_by_code(session: AsyncSession, org_id: uuid.UUID, code: str) -> Shift | None:
    """Looked up within one organization — `code` is unique per org, not globally."""
    stmt = select(Shift).where(
        Shift.organization_id == org_id,
        Shift.code == code,
        Shift.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- geofences


def geofences_stmt(ctx: ScopeContext) -> Select[Any]:
    """Org-scoped geo-fence list. Like shifts, org-level location reference data."""
    return (
        select(Geofence)
        .where(Geofence.organization_id == ctx.organization_id, Geofence.deleted_at.is_(None))
        .order_by(Geofence.location_code)
    )


async def get_geofence(
    session: AsyncSession, org_id: uuid.UUID, geofence_id: uuid.UUID
) -> Geofence | None:
    stmt = select(Geofence).where(
        Geofence.id == geofence_id,
        Geofence.organization_id == org_id,
        Geofence.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_geofence_by_location(
    session: AsyncSession, org_id: uuid.UUID, location_code: str
) -> Geofence | None:
    """The fence guarding a location, or None. Backs both the check-in enforcement and the
    duplicate-location guard on create."""
    stmt = select(Geofence).where(
        Geofence.organization_id == org_id,
        Geofence.location_code == location_code,
        Geofence.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- anti-spoofing (R22)


async def get_signal_settings(session: AsyncSession, org_id: uuid.UUID) -> SignalSettings | None:
    """The organization's rules, or None — which means every rule is off.

    Read on every punch, so it is one indexed lookup on the unique `(organization_id)` and nothing
    else: an organization that has never configured a rule pays this and stores nothing.
    """
    stmt = select(SignalSettings).where(
        SignalSettings.organization_id == org_id, SignalSettings.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_previous_located_signal(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, *, before: datetime
) -> PunchSignal | None:
    """This employee's most recent punch that carried a location fix, before `before`.

    The travel rule's one read. Both coordinates are in the predicate because the partial index
    behind it (`ix_punch_signals_org_employee_punched`) only holds located rows.
    """
    stmt = (
        select(PunchSignal)
        .where(
            PunchSignal.organization_id == org_id,
            PunchSignal.employee_id == employee_id,
            PunchSignal.lat.is_not(None),
            PunchSignal.lng.is_not(None),
            PunchSignal.punched_at < before,
            PunchSignal.deleted_at.is_(None),
        )
        .order_by(PunchSignal.punched_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_other_device_users(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    device_index: str,
    employee_id: uuid.UUID,
    since: datetime,
) -> int:
    """How many *different* employees punched from this device since `since`.

    The shared-device rule's one read, keyed on the blind index — the sealed device id itself is
    never decrypted to answer it. Distinct employees, not punches: one person punching twice from
    their own phone is not sharing it.
    """
    stmt = select(func.count(func.distinct(PunchSignal.employee_id))).where(
        PunchSignal.organization_id == org_id,
        PunchSignal.device_index == device_index,
        PunchSignal.employee_id != employee_id,
        PunchSignal.punched_at >= since,
        PunchSignal.deleted_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one())


def flags_stmt(
    ctx: ScopeContext,
    *,
    employee_id: uuid.UUID | None = None,
    reason: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[Any]:
    """Scope-filtered flag list, newest first. `ctx.apply` is the enforcement point.

    A flag is narrowed exactly as the attendance record it describes is — OWN by the record's
    employee, TEAM through the reporting-line projection, LOCATION by the record's location — so a
    manager can never see a flag on someone whose attendance they could not see. The record is
    joined for that and for the day filter. `flag_read`'s route floor keeps OWN out entirely.
    """
    stmt = (
        select(AttendanceFlag)
        .join(
            AttendanceRecord,
            and_(
                AttendanceRecord.id == AttendanceFlag.attendance_id,
                AttendanceRecord.organization_id == AttendanceFlag.organization_id,
            ),
        )
        .where(
            AttendanceFlag.organization_id == ctx.organization_id,
            AttendanceFlag.deleted_at.is_(None),
            AttendanceRecord.organization_id == ctx.organization_id,
            AttendanceRecord.deleted_at.is_(None),
        )
    )
    stmt = _apply_scope(
        ctx,
        stmt,
        org_col=AttendanceFlag.organization_id,
        employee_col=AttendanceFlag.employee_id,
        location_col=AttendanceRecord.location_code,
    )
    if employee_id:
        stmt = stmt.where(AttendanceFlag.employee_id == employee_id)
    if reason:
        stmt = stmt.where(AttendanceFlag.reason_code == reason)
    if date_from:
        stmt = stmt.where(AttendanceRecord.work_date >= date_from)
    if date_to:
        stmt = stmt.where(AttendanceRecord.work_date <= date_to)
    return stmt.order_by(AttendanceFlag.created_at.desc(), AttendanceFlag.id)


async def get_signals_by_ids(
    session: AsyncSession, org_id: uuid.UUID, signal_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PunchSignal]:
    """The signals behind a page of flags, in one query rather than one per row."""
    if not signal_ids:
        return {}
    stmt = select(PunchSignal).where(
        PunchSignal.organization_id == org_id,
        PunchSignal.id.in_(signal_ids),
        PunchSignal.deleted_at.is_(None),
    )
    return {s.id: s for s in (await session.execute(stmt)).scalars().all()}


async def get_work_dates(
    session: AsyncSession, org_id: uuid.UUID, attendance_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """The day each record belongs to, for a page of flags, in one query rather than one per row."""
    if not attendance_ids:
        return {}
    stmt = select(AttendanceRecord.id, AttendanceRecord.work_date).where(
        AttendanceRecord.organization_id == org_id,
        AttendanceRecord.id.in_(attendance_ids),
        AttendanceRecord.deleted_at.is_(None),
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def get_signal(
    session: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID
) -> PunchSignal | None:
    """Org-scoped fetch by id. None on a tenant mismatch: confirming an id exists in *some*
    organization is itself a disclosure."""
    stmt = select(PunchSignal).where(
        PunchSignal.id == signal_id,
        PunchSignal.organization_id == org_id,
        PunchSignal.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()
