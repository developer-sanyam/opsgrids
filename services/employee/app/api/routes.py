"""HTTP surface for the employee service.

Every route declares the permission it needs and, where the action has no meaningful "own"
reading, a **scope floor**. That distinction is the security boundary: every HRMS user holds
`hrms.people.employee.read` at OWN so they can see their own record, so a write route guarded
only by the permission *name* would let anyone create or transition employees. The write
routes therefore set `minimum=Scope.ORG`.

Every principal route is also guarded by `require_product("hrms")` — defence in depth. The
gateway already refuses a request for a product the organization has not activated (402), but
a routing-table mistake must not become an entitlement bypass.

Reads go through `selectors`, writes through `services`. A router that builds its own query is
how an organization leaks, so none of them do. Every route takes the tenant-bound session and
opens no transaction of its own — the chokepoint owns it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    EmployeeIn,
    EmployeeOut,
    EmployeePatch,
    ServiceInfo,
    TransitionIn,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the HRMS scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers HRMS scope questions.
scope.register()

router = APIRouter(tags=["employee"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one. Routes therefore do
#: not open transactions of their own.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `hrms` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("hrms"))

# --- read / write contexts ----------------------------------------------------------
EmployeeRead = Annotated[ScopeContext, Depends(require("hrms.people.employee.read"))]
#: Writing an employee record has no self-service reading in this slice, so the floor is ORG
#: — every HRMS user holds the OWN grant, and without the floor it would administer people.
EmployeeWrite = Annotated[
    ScopeContext, Depends(require("hrms.people.employee.write", minimum=Scope.ORG))
]


@router.get("/employees/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/employees",
    response_model=Page[EmployeeOut],
    summary="People employed by the organization",
    dependencies=[Entitled],
)
async def list_employees(
    session: SessionDep,
    ctx: EmployeeRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, number or designation")] = None,
    department_id: Annotated[uuid.UUID | None, Query()] = None,
    employee_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[EmployeeOut]:
    stmt = selectors.employees_stmt(ctx, q=q, department_id=department_id, status=employee_status)
    rows, total = await paginate(session, stmt, params)
    items = [EmployeeOut.model_validate(e) for e in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/employees",
    response_model=EmployeeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Onboard an employee",
    dependencies=[Entitled],
)
async def create_employee(
    payload: EmployeeIn, session: SessionDep, ctx: EmployeeWrite
) -> EmployeeOut:
    employee = await services.create_employee(session, ctx, payload)
    return EmployeeOut.model_validate(employee)


@router.get(
    "/employees/{employee_id}",
    response_model=EmployeeOut,
    summary="One employee",
    dependencies=[Entitled],
)
async def read_employee(
    employee_id: uuid.UUID, session: SessionDep, ctx: EmployeeRead
) -> EmployeeOut:
    employee = await selectors.get_employee(session, ctx.organization_id, employee_id)
    if employee is None:
        raise NotFoundProblem("That employee is not part of this organization")
    # Filtering the list and forgetting the detail is the named vulnerability. This closes it:
    # the object-level check re-applies the caller's scope to the single row they named.
    ctx.assert_can_touch(
        owner_id=employee.id,
        team_owner_id=employee.reporting_manager_id,
        location_code=employee.location_code,
    )
    return EmployeeOut.model_validate(employee)


@router.patch(
    "/employees/{employee_id}",
    response_model=EmployeeOut,
    summary="Update an employee",
    dependencies=[Entitled],
)
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeePatch,
    session: SessionDep,
    ctx: EmployeeWrite,
) -> EmployeeOut:
    employee = await selectors.get_employee(session, ctx.organization_id, employee_id)
    if employee is None:
        raise NotFoundProblem("That employee is not part of this organization")
    updated = await services.update_employee(session, ctx, employee, payload)
    return EmployeeOut.model_validate(updated)


@router.post(
    "/employees/{employee_id}/transition",
    response_model=EmployeeOut,
    summary="Move an employee through the lifecycle",
    dependencies=[Entitled],
)
async def transition_employee(
    employee_id: uuid.UUID,
    payload: TransitionIn,
    session: SessionDep,
    ctx: EmployeeWrite,
) -> EmployeeOut:
    employee = await selectors.get_employee(session, ctx.organization_id, employee_id)
    if employee is None:
        raise NotFoundProblem("That employee is not part of this organization")
    updated = await services.transition_status(
        session, ctx, employee, payload.to, reason=payload.reason
    )
    return EmployeeOut.model_validate(updated)


@router.get(
    "/employees/{employee_id}/reports",
    response_model=list[EmployeeOut],
    summary="An employee's direct reports",
    dependencies=[Entitled],
)
async def list_reports(
    employee_id: uuid.UUID, session: SessionDep, ctx: EmployeeRead
) -> list[EmployeeOut]:
    manager = await selectors.get_employee(session, ctx.organization_id, employee_id)
    if manager is None:
        raise NotFoundProblem("That employee is not part of this organization")
    ctx.assert_can_touch(
        owner_id=manager.id,
        team_owner_id=manager.reporting_manager_id,
        location_code=manager.location_code,
    )
    reports = await selectors.direct_reports(session, ctx.organization_id, manager.id)
    return [EmployeeOut.model_validate(e) for e in reports]
