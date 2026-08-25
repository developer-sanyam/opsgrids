"""Writes for the project service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000).

These writers serve both the HTTP surface (`create_project_from`, `create_task_from`) and the
demo seed, which calls the lower-level `create_person` / `create_project` / `create_task` with
explicit refs, codes and dates so the live screens mirror the fixtures the user already
approved.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, OutboxEvent, Person, Project, Task
from app.schemas import ProjectIn, TaskIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `pulseboard.*` because these are the product's own, not the platform's.
EVT_PROJECT_CREATED = "pulseboard.engagements.project.created"
EVT_TASK_CREATED = "pulseboard.board.task.created"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ people


async def create_person(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    ref: str,
    name: str,
    role: str = "",
    location: str = "",
    capacity_hours: int = 40,
    person_id: uuid.UUID | None = None,
    directory_id: uuid.UUID | None = None,
) -> Person:
    """Create a roster person. `person_id` lets the demo seed use a deterministic UUID so the
    timesheet and standup services carry the same person id without a cross-schema read."""
    if await selectors.get_person_by_ref(session, ctx.organization_id, ref):
        raise ConflictProblem(f"A person with ref {ref!r} already exists")

    person = Person(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ref=ref,
        name=name,
        role=role,
        location=location,
        capacity_hours=capacity_hours,
        directory_id=directory_id,
    )
    if person_id is not None:
        person.id = person_id
    session.add(person)
    await session.flush()
    return person


async def _resolve_person_id(
    session: AsyncSession, ctx: ScopeContext, ref: str | None
) -> uuid.UUID | None:
    """A person id from a (possibly absent) ref, scoped to the organization."""
    if not ref:
        return None
    person = await selectors.get_person_by_ref(session, ctx.organization_id, ref)
    if person is None:
        raise NotFoundProblem(f"No person with ref {ref!r} in this organization")
    return person.id


# ------------------------------------------------------------------ projects


async def create_project(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    client: str = "",
    status: str = "on_track",
    progress_pct: int = 0,
    budget_minor: int = 0,
    spent_minor: int = 0,
    currency: str = "USD",
    owner_id: uuid.UUID | None = None,
    start_date: date | None = None,
    due_date: date | None = None,
    billable: bool = True,
    open_tasks: int = 0,
    request: Request | None = None,
) -> Project:
    """The core engagement writer. Inserts the project, records the audit row and stages the
    `created` event — all in the caller's transaction. Money is integer minor units."""
    if await selectors.get_project(session, ctx.organization_id, code):
        raise ConflictProblem(f"A project coded {code!r} already exists")

    project = Project(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        client=client,
        status=status,
        progress_pct=progress_pct,
        budget_minor=budget_minor,
        spent_minor=spent_minor,
        currency=currency.upper(),
        owner_id=owner_id,
        start_date=start_date,
        due_date=due_date,
        billable=billable,
        open_tasks=open_tasks,
    )
    session.add(project)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="project.created",
        entity="project",
        entity_id=project.id,
        after={"code": code, "status": status, "client": client},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PROJECT_CREATED,
        aggregate_type="project",
        aggregate_id=project.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "project_id": str(project.id),
            "code": code,
            "status": status,
        },
        actor=_actor(ctx),
    )
    return project


async def create_project_from(
    session: AsyncSession, ctx: ScopeContext, payload: ProjectIn, *, request: Request | None = None
) -> Project:
    """HTTP convenience over `create_project`, resolving the owner by ref."""
    owner_id = await _resolve_person_id(session, ctx, payload.owner_ref)
    return await create_project(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        client=payload.client,
        status=payload.status,
        progress_pct=payload.progress_pct,
        budget_minor=payload.budget_minor,
        spent_minor=payload.spent_minor,
        currency=payload.currency,
        owner_id=owner_id,
        start_date=payload.start_date,
        due_date=payload.due_date,
        billable=payload.billable,
        open_tasks=payload.open_tasks,
        request=request,
    )


# ------------------------------------------------------------------ tasks


async def create_task(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    project_id: uuid.UUID,
    key: str,
    title: str,
    stage: str = "backlog",
    assignee_id: uuid.UUID | None = None,
    estimate_hours: int = 0,
    priority: str = "medium",
    tags: list[str] | None = None,
    due_date: date | None = None,
    request: Request | None = None,
) -> Task:
    """Create a board card on an engagement. `project_id` is validated by the caller."""
    if await selectors.get_task_by_key(session, ctx.organization_id, key):
        raise ConflictProblem(f"A task keyed {key!r} already exists")

    task = Task(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        project_id=project_id,
        key=key,
        title=title,
        stage=stage,
        assignee_id=assignee_id,
        estimate_hours=estimate_hours,
        priority=priority,
        tags=list(tags or []),
        due_date=due_date,
    )
    session.add(task)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="task.created",
        entity="task",
        entity_id=task.id,
        after={"key": key, "project_id": str(project_id), "stage": stage},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TASK_CREATED,
        aggregate_type="task",
        aggregate_id=task.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "task_id": str(task.id),
            "project_id": str(project_id),
            "key": key,
            "stage": stage,
        },
        actor=_actor(ctx),
    )
    return task


async def create_task_from(
    session: AsyncSession, ctx: ScopeContext, payload: TaskIn, *, request: Request | None = None
) -> Task:
    """HTTP convenience over `create_task`, resolving the project by code and assignee by ref."""
    project = await selectors.get_project(session, ctx.organization_id, payload.project_code)
    if project is None:
        raise NotFoundProblem(f"No project {payload.project_code!r} in this organization")
    assignee_id = await _resolve_person_id(session, ctx, payload.assignee_ref)
    return await create_task(
        session,
        ctx,
        project_id=project.id,
        key=payload.key,
        title=payload.title,
        stage=payload.status,
        assignee_id=assignee_id,
        estimate_hours=payload.estimate_hours,
        priority=payload.priority,
        tags=payload.tags,
        due_date=payload.due_date,
        request=request,
    )
