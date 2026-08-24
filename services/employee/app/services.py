"""Writes for the employee service.

Every function here follows account-service's shape: change the row, record the audit entry,
stage the event — **all inside one transaction**. That is why `get_tenant_session` opens the
transaction and does not commit for the route: a crash between the state change and the event
cannot leave one without the other, and a rolled-back write never leaves an audit row claiming
it happened.

Events are staged into the outbox, never published inline. Publishing before the commit is
durable is the bug the outbox exists to prevent (ADR-0000).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.domain import EmploymentStatus, IllegalTransition, assert_can_transition
from app.models import AuditLog, Employee, OutboxEvent
from app.schemas import EmployeeIn, EmployeePatch
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `hrms.*` because these are the product's own, not the platform's.
EVT_EMPLOYEE_ONBOARDED = "hrms.people.employee.onboarded"
EVT_EMPLOYEE_UPDATED = "hrms.people.employee.updated"
EVT_EMPLOYEE_TRANSITIONED = "hrms.people.employee.transitioned"

#: Which fields the update audit before/after snapshots record. Deliberately the plain
#: employment columns — none of them sensitive.
_TRACKED_FIELDS = (
    "full_name",
    "designation",
    "job_title",
    "department_id",
    "location_code",
    "employment_type",
    "work_mode",
    "date_of_joining",
    "reporting_manager_id",
)


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def _assert_manager_exists(
    session: AsyncSession, org_id: uuid.UUID, manager_id: uuid.UUID | None
) -> None:
    """A reporting manager must be an employee of the same organization.

    Enforced in application code rather than by a foreign key, because the manager link is a
    plain UUID (it could point across a schema boundary in other shapes of this data) and
    because the check has to be tenant-scoped: naming an employee id from another organization
    must read as "not found here", never resolve.
    """
    if manager_id is None:
        return
    manager = await selectors.get_employee(session, org_id, manager_id)
    if manager is None:
        raise NotFoundProblem("That reporting manager is not an employee of this organization")


async def create_employee(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: EmployeeIn,
    *,
    request: Request | None = None,
) -> Employee:
    """Onboard an employee. Starts on probation; the lifecycle is the only way out of it."""
    if await selectors.get_by_employee_no(session, ctx.organization_id, payload.employee_no):
        raise ConflictProblem(f"An employee numbered {payload.employee_no!r} already exists")

    await _assert_manager_exists(session, ctx.organization_id, payload.reporting_manager_id)

    employee = Employee(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        status=EmploymentStatus.PROBATION.value,
        **payload.model_dump(),
    )
    session.add(employee)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.onboarded",
        entity="employee",
        entity_id=employee.id,
        after={"employee_no": employee.employee_no, "full_name": employee.full_name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_ONBOARDED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "employee_id": str(employee.id),
            "employee_no": employee.employee_no,
            "membership_id": str(employee.membership_id) if employee.membership_id else None,
            "status": employee.status,
        },
        actor=_actor(ctx),
    )
    return employee


async def update_employee(
    session: AsyncSession,
    ctx: ScopeContext,
    employee: Employee,
    payload: EmployeePatch,
    *,
    request: Request | None = None,
) -> Employee:
    """Patch the plain employment fields. Lifecycle status is not among them by construction."""
    changes = payload.model_dump(exclude_unset=True)

    if "reporting_manager_id" in changes:
        new_manager = changes["reporting_manager_id"]
        if new_manager == employee.id:
            raise BusinessRuleProblem("An employee cannot report to themselves")
        await _assert_manager_exists(session, ctx.organization_id, new_manager)

    before = {f: employee.as_dict().get(f) for f in _TRACKED_FIELDS}
    for field, value in changes.items():
        setattr(employee, field, value)
    employee.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.updated",
        entity="employee",
        entity_id=employee.id,
        before=before,
        after={f: employee.as_dict().get(f) for f in _TRACKED_FIELDS},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_UPDATED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "employee_id": str(employee.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return employee


async def transition_status(
    session: AsyncSession,
    ctx: ScopeContext,
    employee: Employee,
    target: str,
    *,
    reason: str | None = None,
    request: Request | None = None,
) -> Employee:
    """Move an employee to a new lifecycle state, refusing any illegal step.

    The legality decision belongs to the pure state machine in `domain.py`; this function is
    only the transaction around it — validate, apply, audit, stage the event. An illegal
    transition surfaces as a business-rule problem naming both states.
    """
    current = EmploymentStatus(employee.status)
    to = EmploymentStatus(target)
    try:
        assert_can_transition(current, to)
    except IllegalTransition as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    employee.status = to.value
    if to is EmploymentStatus.EXITED and employee.date_of_exit is None:
        # An exit without a date is a record nobody can reconcile against payroll later. The
        # transition endpoint carries no date, so stamp today; a correction is a later update.
        employee.date_of_exit = datetime.now(UTC).date()
    employee.updated_by = str(ctx.principal.sub)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="employee.transitioned",
        entity="employee",
        entity_id=employee.id,
        before={"status": current.value},
        after={"status": to.value},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_EMPLOYEE_TRANSITIONED,
        aggregate_type="employee",
        aggregate_id=employee.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "employee_id": str(employee.id),
            "from": current.value,
            "to": to.value,
        },
        actor=_actor(ctx),
    )
    return employee
