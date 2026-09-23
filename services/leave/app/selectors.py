"""Reads for leave. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly — the
filter is not redundant with row-level security; RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002).

The scope columns:

    leave_requests  OWN -> LeaveRequest.employee_id
                    TEAM -> LeaveRequest.reporting_manager_id (a snapshot, see app/models.py)
    leave_balances  OWN -> LeaveLedgerEntry.employee_id (no TEAM arm in this slice; see below)

A list endpoint that builds its own query instead of coming through this module is how an
organization leaks, so `api/routes.py` never writes SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmployeeDirectory, LeaveLedgerEntry, LeaveRequest, LeaveSettings, LeaveType
from platform_core.auth import ScopeContext

# --------------------------------------------------------------------- settings


async def get_leave_settings(session: AsyncSession, org_id: uuid.UUID) -> LeaveSettings | None:
    stmt = select(LeaveSettings).where(LeaveSettings.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- leave types


def leave_types_stmt(ctx: ScopeContext, *, active_only: bool = True) -> Select[Any]:
    """Org-scoped leave-type list — reference data, the same shape `attendance.shifts_stmt`
    uses: there is no per-row narrowing beyond the tenant, because the permission never grants
    below ORG (`app/catalogue.py`)."""
    stmt = select(LeaveType).where(
        LeaveType.organization_id == ctx.organization_id, LeaveType.deleted_at.is_(None)
    )
    if active_only:
        stmt = stmt.where(LeaveType.active.is_(True))
    return stmt.order_by(LeaveType.code)


async def get_leave_type(
    session: AsyncSession, org_id: uuid.UUID, leave_type_id: uuid.UUID
) -> LeaveType | None:
    stmt = select(LeaveType).where(
        LeaveType.id == leave_type_id,
        LeaveType.organization_id == org_id,
        LeaveType.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_leave_type_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> LeaveType | None:
    """Looked up within one organization — `code` is unique per org, not globally."""
    stmt = select(LeaveType).where(
        LeaveType.organization_id == org_id,
        LeaveType.code == code,
        LeaveType.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- balances (the ledger)


async def balance_half_days(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, leave_type_id: uuid.UUID
) -> int:
    """The current balance for one employee and leave type — always the sum of every posting
    ever made, never a stored running total (CLAUDE.md's append-only rule)."""
    stmt = select(func.coalesce(func.sum(LeaveLedgerEntry.delta_half_days), 0)).where(
        LeaveLedgerEntry.organization_id == org_id,
        LeaveLedgerEntry.employee_id == employee_id,
        LeaveLedgerEntry.leave_type_id == leave_type_id,
    )
    return int((await session.execute(stmt)).scalar_one())


def balances_stmt(ctx: ScopeContext, *, employee_id: uuid.UUID | None) -> Select[Any]:
    """One row per `(employee, leave_type)` this caller may see, balance already summed in the
    database rather than in Python (a loop that summed per type in the app would be the
    "query in a loop" CLAUDE.md calls a bug once there is more than a handful of types).

    No TEAM arm: `hrms.time.leave_balance.read` never defaults a manager into TEAM in this slice
    (`app/catalogue.py`), so `ctx.apply` only ever sees OWN or ORG here — a caller who somehow
    holds TEAM is refused by `ctx.apply` itself, the same "ungranted but not silently widened"
    shape `scope.py`'s LOCATION deferral uses.
    """
    stmt = (
        select(
            LeaveLedgerEntry.employee_id,
            LeaveLedgerEntry.leave_type_id,
            func.sum(LeaveLedgerEntry.delta_half_days).label("balance_half_days"),
        )
        .where(LeaveLedgerEntry.organization_id == ctx.organization_id)
        .group_by(LeaveLedgerEntry.employee_id, LeaveLedgerEntry.leave_type_id)
    )
    stmt = ctx.apply(stmt, owner_col=LeaveLedgerEntry.employee_id, team_col=None, location_col=None)
    if employee_id is not None:
        stmt = stmt.where(LeaveLedgerEntry.employee_id == employee_id)
    return stmt.order_by(LeaveLedgerEntry.employee_id, LeaveLedgerEntry.leave_type_id)


async def list_ledger_entries(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, leave_type_id: uuid.UUID
) -> Sequence[LeaveLedgerEntry]:
    """One employee's full posting history for one leave type, oldest first — the audit trail a
    balance is built from. Object-level scope is checked by the route before this runs, the same
    "filter the list, re-check the detail" shape `attendance.read_attendance` uses."""
    stmt = (
        select(LeaveLedgerEntry)
        .where(
            LeaveLedgerEntry.organization_id == org_id,
            LeaveLedgerEntry.employee_id == employee_id,
            LeaveLedgerEntry.leave_type_id == leave_type_id,
        )
        .order_by(LeaveLedgerEntry.created_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_ledger_entry_by_idempotency_key(
    session: AsyncSession, org_id: uuid.UUID, idempotency_key: str
) -> LeaveLedgerEntry | None:
    stmt = select(LeaveLedgerEntry).where(
        LeaveLedgerEntry.organization_id == org_id,
        LeaveLedgerEntry.idempotency_key == idempotency_key,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- leave requests


def leave_requests_stmt(
    ctx: ScopeContext,
    *,
    employee_id: uuid.UUID | None = None,
    request_status: str | None = None,
) -> Select[Any]:
    stmt = select(LeaveRequest).where(
        LeaveRequest.organization_id == ctx.organization_id, LeaveRequest.deleted_at.is_(None)
    )
    stmt = ctx.apply(
        stmt,
        owner_col=LeaveRequest.employee_id,
        team_col=LeaveRequest.reporting_manager_id,
        location_col=None,
    )
    if employee_id is not None:
        stmt = stmt.where(LeaveRequest.employee_id == employee_id)
    if request_status is not None:
        stmt = stmt.where(LeaveRequest.status == request_status)
    return stmt.order_by(LeaveRequest.created_at.desc())


async def get_leave_request(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> LeaveRequest | None:
    stmt = select(LeaveRequest).where(
        LeaveRequest.id == request_id,
        LeaveRequest.organization_id == org_id,
        LeaveRequest.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_leave_request_by_source(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> LeaveRequest | None:
    """The consumer's lookup by `source_ref` (ADR-0006 §12) — same shape as `get_leave_request`,
    named for the call site that reads it."""
    return await get_leave_request(session, org_id, request_id)


async def has_overlapping_request(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    start_date: Any,
    end_date: Any,
    *,
    exclude_request_id: uuid.UUID | None = None,
) -> bool:
    """A friendly pre-check for the same rule `ex_leave_requests_no_overlap` enforces at the
    database (`app/models.py`) — this is what turns a `23P01` exclusion violation into a readable
    `ConflictProblem` for the ordinary case; the constraint is still the backstop for a race
    between two concurrent applications."""
    stmt = select(LeaveRequest.id).where(
        LeaveRequest.organization_id == org_id,
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_(("pending", "approved")),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    )
    if exclude_request_id is not None:
        stmt = stmt.where(LeaveRequest.id != exclude_request_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


# --------------------------------------------------------------------- employee directory


async def get_directory_entry(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> EmployeeDirectory | None:
    stmt = select(EmployeeDirectory).where(
        EmployeeDirectory.organization_id == org_id, EmployeeDirectory.employee_id == employee_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
