"""Reads for the attendance service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.

The scope columns for an attendance record are:

    OWN      -> AttendanceRecord.employee_id    (the caller's own records)
    LOCATION -> AttendanceRecord.location_code  (a single code column)
    TEAM     -> deferred (no team column; a TEAM-scoped caller is refused — see scope.py)
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceRecord, Geofence, Shift
from platform_core.auth import ScopeContext


def attendance_stmt(
    ctx: ScopeContext,
    *,
    employee_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered attendance list. `ctx.apply` is the enforcement point.

    `team_col` is `None`: a TEAM-scoped caller is refused here, which is the deferral in
    `scope.py` made concrete (the reporting hierarchy lives in employee-service).
    """
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.organization_id == ctx.organization_id,
        AttendanceRecord.deleted_at.is_(None),
    )
    stmt = ctx.apply(
        stmt,
        owner_col=AttendanceRecord.employee_id,
        team_col=None,
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
