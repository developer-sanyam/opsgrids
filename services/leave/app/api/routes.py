"""HTTP surface for leave.

Every route declares the permission it needs and, where the action has no meaningful "own"
reading, a **scope floor** — the same discipline `attendance.api.routes` states in its own
docstring. Leave types, settings and balance grants are administrative acts on org-wide or
someone-else's data, so they float at `minimum=Scope.ORG`; applying for leave and withdrawing
your own request are self-service by construction, so their permission's own ceiling (OWN) is
the only floor that exists.

Every principal route is also guarded by `require_product("hrms")` — defence in depth. Reads go
through `selectors`, writes through `services`; a router that builds its own query is how an
organization leaks, so none of them do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA, LeaveRequest, LeaveType
from app.schemas import (
    LeaveBalanceGrantIn,
    LeaveBalanceOut,
    LeaveCancelIn,
    LeaveLedgerEntryOut,
    LeaveRequestIn,
    LeaveRequestOut,
    LeaveSettingsIn,
    LeaveSettingsOut,
    LeaveTypeIn,
    LeaveTypeOut,
    LeaveTypePatch,
    ServiceInfo,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import ForbiddenProblem, NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the HRMS scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers HRMS scope questions.
scope.register()

router = APIRouter(tags=["leave"])

#: The tenancy chokepoint. Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `hrms` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("hrms"))

LeaveTypeRead = Annotated[ScopeContext, Depends(require("hrms.time.leave_type.read"))]
LeaveTypeWrite = Annotated[
    ScopeContext, Depends(require("hrms.time.leave_type.write", minimum=Scope.ORG))
]
LeaveBalanceRead = Annotated[ScopeContext, Depends(require("hrms.time.leave_balance.read"))]
LeaveBalanceWrite = Annotated[
    ScopeContext, Depends(require("hrms.time.leave_balance.write", minimum=Scope.ORG))
]
#: Applying for leave and cancelling your own request — the permission's own ceiling is OWN
#: (`app/catalogue.py`), so there is no floor to set: an ORG-wide grant of this permission is
#: not something the normal install path can even produce.
LeaveRequestWrite = Annotated[ScopeContext, Depends(require("hrms.time.leave_request.write"))]
LeaveRequestRead = Annotated[ScopeContext, Depends(require("hrms.time.leave_request.read"))]
#: HR's documented override — cancel or adjust any employee's request directly (ADR-0006 §6's
#: "the product's own override... stays audited in its own domain").
LeaveRequestManage = Annotated[
    ScopeContext, Depends(require("hrms.time.leave_request.manage", minimum=Scope.ORG))
]


@router.get("/leave/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


def _request_out(req: LeaveRequest) -> LeaveRequestOut:
    """`approver_unassigned` is computed here, not stored — see the field's docstring on the
    schema for why `approval_opened_at`/`approver_employee_id` stay the one source of truth."""
    approver_unassigned = (
        None if req.approval_opened_at is None else req.approver_employee_id is None
    )
    return LeaveRequestOut.model_validate(req).model_copy(
        update={"approver_unassigned": approver_unassigned}
    )


def _assert_requesting_own_record(ctx: ScopeContext, req: LeaveRequest) -> None:
    """`hrms.time.leave_request.write`'s ceiling is OWN, so no legitimately-issued grant reaches
    another employee's request — but a caller who could still name another person's request by
    id must be refused *here*, the same "the permission's ceiling is not itself an object-level
    check" reasoning `attendance.api.routes._assert_requesting_own_record` states verbatim."""
    subject = ctx.subject_ref
    if subject is None or req.employee_id != subject:
        raise ForbiddenProblem("You may only act on your own leave request")


# --------------------------------------------------------------------- settings


@router.get(
    "/leave/settings",
    response_model=LeaveSettingsOut,
    summary="This organization's leave settings",
    dependencies=[Entitled],
)
async def read_leave_settings(session: SessionDep, ctx: LeaveTypeRead) -> LeaveSettingsOut:
    row = await selectors.get_leave_settings(session, ctx.organization_id)
    if row is None:
        timezone = await services.resolve_timezone(session, ctx.organization_id)
        return LeaveSettingsOut(
            organization_id=ctx.organization_id, timezone=timezone, updated_at=datetime.now(UTC)
        )
    return LeaveSettingsOut.model_validate(row)


@router.put(
    "/leave/settings",
    response_model=LeaveSettingsOut,
    summary="Set this organization's leave timezone",
    dependencies=[Entitled],
)
async def update_leave_settings(
    payload: LeaveSettingsIn, session: SessionDep, ctx: LeaveTypeWrite, request: Request
) -> LeaveSettingsOut:
    row = await services.update_leave_settings(session, ctx, payload, request=request)
    return LeaveSettingsOut.model_validate(row)


# --------------------------------------------------------------------- leave types


@router.get(
    "/leave/types",
    response_model=Page[LeaveTypeOut],
    summary="Leave types",
    dependencies=[Entitled],
)
async def list_leave_types(
    session: SessionDep,
    ctx: LeaveTypeRead,
    params: PageQuery,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[LeaveTypeOut]:
    stmt = selectors.leave_types_stmt(ctx, active_only=not include_inactive)
    rows, total = await paginate(session, stmt, params)
    return Page.build([LeaveTypeOut.model_validate(r) for r in rows], total=total, params=params)


@router.post(
    "/leave/types",
    response_model=LeaveTypeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Define a leave type",
    dependencies=[Entitled],
)
async def create_leave_type(
    payload: LeaveTypeIn, session: SessionDep, ctx: LeaveTypeWrite, request: Request
) -> LeaveTypeOut:
    leave_type = await services.create_leave_type(session, ctx, payload, request=request)
    return LeaveTypeOut.model_validate(leave_type)


@router.patch(
    "/leave/types/{leave_type_id}",
    response_model=LeaveTypeOut,
    summary="Update a leave type",
    dependencies=[Entitled],
)
async def update_leave_type(
    leave_type_id: uuid.UUID,
    payload: LeaveTypePatch,
    session: SessionDep,
    ctx: LeaveTypeWrite,
    request: Request,
) -> LeaveTypeOut:
    leave_type = await selectors.get_leave_type(session, ctx.organization_id, leave_type_id)
    if leave_type is None:
        raise NotFoundProblem("That leave type is not part of this organization")
    updated = await services.update_leave_type(session, ctx, leave_type, payload, request=request)
    return LeaveTypeOut.model_validate(updated)


@router.delete(
    "/leave/types/{leave_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a leave type",
    dependencies=[Entitled],
)
async def delete_leave_type(
    leave_type_id: uuid.UUID, session: SessionDep, ctx: LeaveTypeWrite, request: Request
) -> Response:
    leave_type = await selectors.get_leave_type(session, ctx.organization_id, leave_type_id)
    if leave_type is None:
        raise NotFoundProblem("That leave type is not part of this organization")
    await services.delete_leave_type(session, ctx, leave_type, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- balances


@router.get(
    "/leave/balances",
    response_model=list[LeaveBalanceOut],
    summary="Leave balances",
    dependencies=[Entitled],
)
async def list_leave_balances(
    session: SessionDep,
    ctx: LeaveBalanceRead,
    employee_id: Annotated[uuid.UUID, Query()],
) -> list[LeaveBalanceOut]:
    """`employee_id` is required, not optional — the one thing that keeps this endpoint bounded
    without a page parameter (CLAUDE.md: no unbounded list query). One employee's balances are at
    most the handful of leave types an organization defines; "every employee's balance" is
    deliberately not a read this endpoint offers in this slice — an ORG-scoped caller asking for
    it would otherwise return one row per employee per type with nothing to page it by. `ctx.apply`
    still narrows an OWN-scoped caller to their own id regardless of what they pass here."""
    stmt = selectors.balances_stmt(ctx, employee_id=employee_id)
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []
    type_ids = {r.leave_type_id for r in rows}
    types = (
        await session.execute(
            select(LeaveType.id, LeaveType.code).where(
                LeaveType.organization_id == ctx.organization_id, LeaveType.id.in_(type_ids)
            )
        )
    ).all()
    codes = {t.id: t.code for t in types}
    return [
        LeaveBalanceOut(
            employee_id=r.employee_id,
            leave_type_id=r.leave_type_id,
            leave_type_code=codes.get(r.leave_type_id, ""),
            balance_half_days=int(r.balance_half_days),
        )
        for r in rows
    ]


@router.get(
    "/leave/balances/ledger",
    response_model=list[LeaveLedgerEntryOut],
    summary="One employee's posting history for one leave type",
    dependencies=[Entitled],
)
async def read_ledger(
    session: SessionDep,
    ctx: LeaveBalanceRead,
    employee_id: Annotated[uuid.UUID, Query()],
    leave_type_id: Annotated[uuid.UUID, Query()],
) -> list[LeaveLedgerEntryOut]:
    # Object-level check, the same "filter the list, re-check the single-subject read" shape
    # `attendance.read_attendance` uses: `balances_stmt` above already enforces this for the list
    # read, but this endpoint takes the employee id directly rather than through that statement.
    ctx.assert_can_touch(owner_id=employee_id, team_owner_id=None, location_code=None)
    entries = await selectors.list_ledger_entries(
        session, ctx.organization_id, employee_id, leave_type_id
    )
    return [LeaveLedgerEntryOut.model_validate(e) for e in entries]


@router.post(
    "/leave/balances/grant",
    response_model=LeaveLedgerEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Grant, accrue, or adjust an employee's leave balance",
    dependencies=[Entitled],
)
async def grant_leave_balance(
    payload: LeaveBalanceGrantIn, session: SessionDep, ctx: LeaveBalanceWrite, request: Request
) -> LeaveLedgerEntryOut:
    entry = await services.grant_leave_balance(session, ctx, payload, request=request)
    return LeaveLedgerEntryOut.model_validate(entry)


# --------------------------------------------------------------------- leave requests


@router.get(
    "/leave/requests",
    response_model=Page[LeaveRequestOut],
    summary="Leave requests",
    dependencies=[Entitled],
)
async def list_leave_requests(
    session: SessionDep,
    ctx: LeaveRequestRead,
    params: PageQuery,
    employee_id: Annotated[uuid.UUID | None, Query()] = None,
    request_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[LeaveRequestOut]:
    stmt = selectors.leave_requests_stmt(
        ctx, employee_id=employee_id, request_status=request_status
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([_request_out(r) for r in rows], total=total, params=params)


@router.get(
    "/leave/requests/{request_id}",
    response_model=LeaveRequestOut,
    summary="One leave request",
    dependencies=[Entitled],
)
async def read_leave_request(
    request_id: uuid.UUID, session: SessionDep, ctx: LeaveRequestRead
) -> LeaveRequestOut:
    req = await selectors.get_leave_request(session, ctx.organization_id, request_id)
    if req is None:
        raise NotFoundProblem("That leave request is not part of this organization")
    # Filtering the list and forgetting the detail is the named vulnerability (see
    # `attendance.api.routes.read_attendance`'s identical comment). TEAM reaches a request whose
    # `reporting_manager_id` snapshot is the caller; LOCATION is not supported (`scope.py`).
    ctx.assert_can_touch(
        owner_id=req.employee_id, team_owner_id=req.reporting_manager_id, location_code=None
    )
    return _request_out(req)


@router.post(
    "/leave/requests",
    response_model=LeaveRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Apply for leave",
    dependencies=[Entitled],
)
async def apply_for_leave(
    payload: LeaveRequestIn, session: SessionDep, ctx: LeaveRequestWrite, request: Request
) -> LeaveRequestOut:
    """Self-service (ADR-0006). Opens a `pending` request and publishes
    `hrms.time.leave.requested`; approval routes it and reports the decision back on
    `core.approval.request.decided`, applied by `app.consumers`."""
    req = await services.apply_for_leave(session, ctx, payload, request=request)
    return _request_out(req)


@router.post(
    "/leave/requests/{request_id}/cancel",
    response_model=LeaveRequestOut,
    summary="Withdraw your own leave request",
    dependencies=[Entitled],
)
async def cancel_leave_request(
    request_id: uuid.UUID,
    payload: LeaveCancelIn,
    session: SessionDep,
    ctx: LeaveRequestWrite,
    request: Request,
) -> LeaveRequestOut:
    # Locked before anything else — the same "lock, then decide" order
    # `approval.services.decide_request` uses, so a cancel racing the decision consumer (or a
    # second concurrent cancel) serialises here rather than double-posting a reversal. See
    # `selectors.get_leave_request_for_update`'s own docstring.
    req = await selectors.get_leave_request_for_update(session, ctx.organization_id, request_id)
    if req is None:
        raise NotFoundProblem("That leave request is not part of this organization")
    _assert_requesting_own_record(ctx, req)
    updated = await services.cancel_leave_request(session, ctx, req, payload, request=request)
    return _request_out(updated)


@router.post(
    "/leave/requests/{request_id}/force-cancel",
    response_model=LeaveRequestOut,
    summary="Cancel any employee's leave request directly (HR override)",
    dependencies=[Entitled],
)
async def force_cancel_leave_request(
    request_id: uuid.UUID,
    payload: LeaveCancelIn,
    session: SessionDep,
    ctx: LeaveRequestManage,
    request: Request,
) -> LeaveRequestOut:
    """The documented escape hatch (ADR-0006 §6) — HR cancels a stuck or mistaken request
    directly, audited in its own domain, without going through approval. `ctx` here is already
    guarded at `minimum=Scope.ORG`, so no object-level identity check is needed: an ORG-scoped
    caller may touch any employee's request by construction (`ScopeContext.assert_can_touch`)."""
    req = await selectors.get_leave_request_for_update(session, ctx.organization_id, request_id)
    if req is None:
        raise NotFoundProblem("That leave request is not part of this organization")
    updated = await services.cancel_leave_request(session, ctx, req, payload, request=request)
    return _request_out(updated)
