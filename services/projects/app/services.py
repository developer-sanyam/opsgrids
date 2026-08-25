"""Writes for the projects service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000).

These writers serve both the HTTP surface (`create_project_from`, `create_milestone_from`,
`create_task_from`) and the demo seed, which calls the lower-level `create_member` /
`create_project` / `create_milestone` / `create_task` with explicit refs, codes and dates so the
live screens mirror the fixtures the user already approved.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, Member, Milestone, OutboxEvent, Project, Task
from app.schemas import MilestoneIn, ProjectIn, TaskIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `projects.*` because these are the product's own, not the platform's.
EVT_PROJECT_CREATED = "projects.portfolio.project.created"
EVT_MILESTONE_CREATED = "projects.delivery.milestone.created"
EVT_TASK_CREATED = "projects.board.task.created"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ members


async def create_member(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    ref: str,
    name: str,
    role: str = "",
    directory_id: uuid.UUID | None = None,
) -> Member:
    """Create a delivery-org member. Idempotency is the caller's job (seed checks the ref)."""
    if await selectors.get_member_by_ref(session, ctx.organization_id, ref):
        raise ConflictProblem(f"A member with ref {ref!r} already exists")

    member = Member(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ref=ref,
        name=name,
        role=role,
        directory_id=directory_id,
    )
    session.add(member)
    await session.flush()
    return member


async def _resolve_member_id(
    session: AsyncSession, ctx: ScopeContext, ref: str | None
) -> uuid.UUID | None:
    """A member id from a (possibly absent) ref, scoped to the organization."""
    if not ref:
        return None
    member = await selectors.get_member_by_ref(session, ctx.organization_id, ref)
    if member is None:
        raise NotFoundProblem(f"No member with ref {ref!r} in this organization")
    return member.id


# ------------------------------------------------------------------ projects


async def create_project(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    client: str = "",
    owner_id: uuid.UUID | None = None,
    phase: str = "discovery",
    health: str = "on_track",
    progress_pct: int = 0,
    start_date: date | None = None,
    target_date: date | None = None,
    budget_minor: int = 0,
    spent_minor: int = 0,
    currency: str = "INR",
    team_size: int = 0,
    open_tasks: int = 0,
    summary: str = "",
    request: Request | None = None,
) -> Project:
    """The core project writer. Inserts the project, records the audit row and stages the
    `created` event — all in the caller's transaction. Money is integer minor units."""
    if await selectors.get_project(session, ctx.organization_id, code):
        raise ConflictProblem(f"A project coded {code!r} already exists")

    project = Project(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        client=client,
        owner_id=owner_id,
        phase=phase,
        health=health,
        progress_pct=progress_pct,
        start_date=start_date,
        target_date=target_date,
        budget_minor=budget_minor,
        spent_minor=spent_minor,
        currency=currency.upper(),
        team_size=team_size,
        open_tasks=open_tasks,
        summary=summary,
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
        after={"code": code, "phase": phase, "health": health},
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
            "phase": phase,
            "health": health,
        },
        actor=_actor(ctx),
    )
    return project


async def create_project_from(
    session: AsyncSession, ctx: ScopeContext, payload: ProjectIn, *, request: Request | None = None
) -> Project:
    """HTTP convenience over `create_project`, resolving the owner by ref."""
    owner_id = await _resolve_member_id(session, ctx, payload.owner_ref)
    return await create_project(
        session,
        ctx,
        code=payload.code,
        name=payload.name,
        client=payload.client,
        owner_id=owner_id,
        phase=payload.phase,
        health=payload.health,
        progress_pct=payload.progress_pct,
        start_date=payload.start_date,
        target_date=payload.target_date,
        budget_minor=payload.budget_minor,
        spent_minor=payload.spent_minor,
        currency=payload.currency,
        team_size=payload.team_size,
        open_tasks=payload.open_tasks,
        summary=payload.summary,
        request=request,
    )


# ------------------------------------------------------------------ milestones


async def create_milestone(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    project_id: uuid.UUID,
    title: str,
    owner_id: uuid.UUID | None = None,
    due_date: date | None = None,
    status: str = "on_track",
    progress_pct: int = 0,
    request: Request | None = None,
) -> Milestone:
    """Create a milestone on a project. `project_id` is validated by the caller."""
    milestone = Milestone(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        project_id=project_id,
        title=title,
        owner_id=owner_id,
        due_date=due_date,
        status=status,
        progress_pct=progress_pct,
    )
    session.add(milestone)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="milestone.created",
        entity="milestone",
        entity_id=milestone.id,
        after={"project_id": str(project_id), "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MILESTONE_CREATED,
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "milestone_id": str(milestone.id),
            "project_id": str(project_id),
            "status": status,
        },
        actor=_actor(ctx),
    )
    return milestone


async def create_milestone_from(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: MilestoneIn,
    *,
    request: Request | None = None,
) -> Milestone:
    """HTTP convenience over `create_milestone`, resolving the project by code and owner by ref."""
    project = await selectors.get_project(session, ctx.organization_id, payload.project_code)
    if project is None:
        raise NotFoundProblem(f"No project {payload.project_code!r} in this organization")
    owner_id = await _resolve_member_id(session, ctx, payload.owner_ref)
    return await create_milestone(
        session,
        ctx,
        project_id=project.id,
        title=payload.title,
        owner_id=owner_id,
        due_date=payload.due_date,
        status=payload.status,
        progress_pct=payload.progress_pct,
        request=request,
    )


# ------------------------------------------------------------------ tasks


async def create_task(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    project_id: uuid.UUID,
    title: str,
    assignee_id: uuid.UUID | None = None,
    stage: str = "backlog",
    priority: str = "medium",
    due_date: date | None = None,
    points: int = 0,
    request: Request | None = None,
) -> Task:
    """Create a board card on a project. `project_id` is validated by the caller."""
    task = Task(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        project_id=project_id,
        title=title,
        assignee_id=assignee_id,
        stage=stage,
        priority=priority,
        due_date=due_date,
        points=points,
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
        after={"project_id": str(project_id), "stage": stage, "priority": priority},
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
    assignee_id = await _resolve_member_id(session, ctx, payload.assignee_ref)
    return await create_task(
        session,
        ctx,
        project_id=project.id,
        title=payload.title,
        assignee_id=assignee_id,
        stage=payload.stage,
        priority=payload.priority,
        due_date=payload.due_date,
        points=payload.points,
        request=request,
    )
