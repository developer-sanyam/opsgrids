"""Reads for the employee service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.

The scope columns for an employee are:

    OWN      -> Employee.id                    (the caller's own record)
    TEAM     -> Employee.reporting_manager_id  (people who report to the caller)
    LOCATION -> Employee.location_code         (a single code column)
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, EmployeePii
from platform_core.auth import ScopeContext


def employees_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    department_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered employee list. `ctx.apply` is the enforcement point.

    Unlike account's membership list, LOCATION is a single-column comparison here, so the
    shared `apply` helper handles all three rungs directly — there is no CSV to unpack.
    """
    stmt = select(Employee).where(
        Employee.organization_id == ctx.organization_id,
        Employee.deleted_at.is_(None),
    )
    stmt = ctx.apply(
        stmt,
        owner_col=Employee.id,
        team_col=Employee.reporting_manager_id,
        location_col=Employee.location_code,
    )

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Employee.full_name).like(needle),
                func.lower(Employee.employee_no).like(needle),
                func.lower(func.coalesce(Employee.designation, "")).like(needle),
            )
        )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if status:
        stmt = stmt.where(Employee.status == status)

    return stmt.order_by(Employee.full_name)


async def get_employee(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> Employee | None:
    """Org-scoped fetch by id. Returns None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Employee).where(
        Employee.id == employee_id,
        Employee.organization_id == org_id,
        Employee.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_employee_pii(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> EmployeePii | None:
    """The sealed PII row for one employee, or None when it has none.

    Org-scoped like every read here: the explicit `organization_id` filter sits beside row-level
    security, not in place of it. Returns the row with its sealed columns intact — this selector
    reads, it never decrypts; opening a value is `services.unmask_employee_pii`, and only there.
    """
    stmt = select(EmployeePii).where(
        EmployeePii.employee_id == employee_id,
        EmployeePii.organization_id == org_id,
        EmployeePii.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_employee_no(
    session: AsyncSession, org_id: uuid.UUID, employee_no: str
) -> Employee | None:
    """Looked up within one organization — `employee_no` is unique per org, not globally."""
    stmt = select(Employee).where(
        Employee.organization_id == org_id,
        Employee.employee_no == employee_no,
        Employee.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def direct_reports(
    session: AsyncSession, org_id: uuid.UUID, manager_id: uuid.UUID
) -> list[Employee]:
    """Everyone who reports directly to `manager_id`, within the organization.

    Read directly off `reporting_manager_id` rather than walked, so this is one query and a
    reporting cycle cannot make it loop.
    """
    stmt = (
        select(Employee)
        .where(
            Employee.organization_id == org_id,
            Employee.reporting_manager_id == manager_id,
            Employee.deleted_at.is_(None),
        )
        .order_by(Employee.full_name)
    )
    return list((await session.execute(stmt)).scalars().all())
