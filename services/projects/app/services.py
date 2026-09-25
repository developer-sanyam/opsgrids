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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, Member, Milestone, OutboxEvent, Project, Task
from app.schemas import (
    MemberIn,
    MemberUpdate,
    MilestoneIn,
    MilestoneUpdate,
    ProjectIn,
    ProjectUpdate,
    TaskIn,
    TaskUpdate,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event
from platform_core.models import utcnow

# Event names. `projects.*` because these are the product's own, not the platform's.
EVT_PROJECT_CREATED = "projects.portfolio.project.created"
EVT_PROJECT_UPDATED = "projects.portfolio.project.updated"
EVT_PROJECT_DELETED = "projects.portfolio.project.deleted"
EVT_MILESTONE_CREATED = "projects.delivery.milestone.created"
EVT_MILESTONE_UPDATED = "projects.delivery.milestone.updated"
EVT_MILESTONE_DELETED = "projects.delivery.milestone.deleted"
EVT_TASK_CREATED = "projects.board.task.created"
EVT_TASK_UPDATED = "projects.board.task.updated"
EVT_TASK_DELETED = "projects.board.task.deleted"
EVT_MEMBER_CREATED = "projects.team.member.created"
EVT_MEMBER_UPDATED = "projects.team.member.updated"
EVT_MEMBER_DELETED = "projects.team.member.deleted"


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
    request: Request | None = None,
) -> Member:
    """Create a delivery-org member — the writer both the HTTP surface and the demo seed use.

    The duplicate check matches active **and soft-deleted** rows: `uq_members_org_ref` is not
    a partial index, so a soft-deleted member still occupies the ref and an unmatched insert
    would fall through to the database as an unhandled `IntegrityError`.
    """
    if await selectors.member_ref_taken(session, ctx.organization_id, ref):
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

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="member.created",
        entity="member",
        entity_id=member.id,
        after={"ref": ref, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MEMBER_CREATED,
        aggregate_type="member",
        aggregate_id=member.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "member_id": str(member.id),
            "ref": ref,
        },
        actor=_actor(ctx),
    )
    return member


async def create_member_from(
    session: AsyncSession, ctx: ScopeContext, payload: MemberIn, *, request: Request | None = None
) -> Member:
    """HTTP convenience over `create_member`."""
    return await create_member(
        session,
        ctx,
        ref=payload.ref,
        name=payload.name,
        role=payload.role,
        directory_id=payload.directory_id,
        request=request,
    )


async def update_member(
    session: AsyncSession,
    ctx: ScopeContext,
    member: Member,
    payload: MemberUpdate,
    *,
    request: Request | None = None,
) -> Member:
    """Patch a member's plain fields. `ref` is immutable — it is the value every `owner_ref` /
    `assignee_ref` on the wire carries."""
    changes = payload.model_dump(exclude_unset=True)
    before = {
        "name": member.name,
        "role": member.role,
        "directory_id": str(member.directory_id) if member.directory_id else None,
    }
    for field, value in changes.items():
        setattr(member, field, value)
    member.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "name": member.name,
        "role": member.role,
        "directory_id": str(member.directory_id) if member.directory_id else None,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="member.updated",
        entity="member",
        entity_id=member.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MEMBER_UPDATED,
        aggregate_type="member",
        aggregate_id=member.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "member_id": str(member.id),
            "ref": member.ref,
        },
        actor=_actor(ctx),
    )
    return member


async def delete_member(
    session: AsyncSession,
    ctx: ScopeContext,
    member: Member,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a member. Hard delete is refused (`Must not be built`) — audit rows
    reference this member's id, and `directory_id` is the intended OWN-scope link.

    No cascade write is needed on the project/milestone/task rows that name this member as
    owner/assignee: `members_by_id` already filters `deleted_at IS NULL`, so `person_of` falls
    back to "Unassigned" for any FK that now points at a row outside that map, without the FK
    itself ever being touched.
    """
    if member.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    member.deleted_at = now
    member.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="member.deleted",
        entity="member",
        entity_id=member.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat(), "ref": member.ref},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MEMBER_DELETED,
        aggregate_type="member",
        aggregate_id=member.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "member_id": str(member.id),
            "ref": member.ref,
        },
        actor=_actor(ctx),
    )


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


async def _owner_directory_id(
    session: AsyncSession, org_id: uuid.UUID, member_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The account-directory id behind a `members.id`, for an object-level OWN check.

    `ctx.assert_can_touch` compares its `owner_id` against `ctx.subject_ref`, which is the JWT
    `sref` claim — the caller's account-service employee id, not a `members.id` (ADR-0008 §1).
    A task's `assignee_id` is a `members.id`, so a raw comparison would never match and
    `PROJECTS_MEMBER`'s `task.write:OWN` would authorise nothing. `members.directory_id` is the
    link the model carries for exactly this: resolve the assignee, and compare through it
    instead. `None` when there is no assignee, or the assignee has no linked login — either way
    an OWN-scoped caller correctly fails to match rather than matching by accident.
    """
    if member_id is None:
        return None
    member = await selectors.get_member_by_id(session, org_id, member_id)
    return member.directory_id if member else None


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
    `created` event — all in the caller's transaction. Money is integer minor units.

    The duplicate check matches active **and soft-deleted** rows — `uq_projects_org_code` is
    not a partial index, so a soft-deleted project still occupies the code."""
    if await selectors.project_code_taken(session, ctx.organization_id, code):
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
    """HTTP convenience over `create_project`, resolving the owner by ref.

    Object-level scope is checked here too, not only on the route: an ORG-scoped writer may
    create for anyone, but an OWN-scoped one — a custom role could grant `project.write:OWN` —
    could otherwise plant a project owned by (or unowned, which `assert_can_touch` also
    refuses) someone else, which the list-level `ctx.apply` scoping on reads would never catch
    because it only narrows what already exists.
    """
    owner_id = await _resolve_member_id(session, ctx, payload.owner_ref)
    ctx.assert_can_touch(owner_id=owner_id)
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


async def update_project(
    session: AsyncSession,
    ctx: ScopeContext,
    project: Project,
    payload: ProjectUpdate,
    *,
    request: Request | None = None,
) -> Project:
    """Patch the plain project fields. `code` is immutable — it is the URL key and travels in
    every already-published event. Object check mirrors the detail route
    (`ctx.assert_can_touch(owner_id=project.owner_id)`, `routes.py`'s `read_project`)."""
    ctx.assert_can_touch(owner_id=project.owner_id)
    changes = payload.model_dump(exclude_unset=True)

    before = {
        "name": project.name,
        "client": project.client,
        "owner_id": str(project.owner_id) if project.owner_id else None,
        "phase": project.phase,
        "health": project.health,
        "progress_pct": project.progress_pct,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_date": project.target_date.isoformat() if project.target_date else None,
        "budget_minor": project.budget_minor,
        "spent_minor": project.spent_minor,
        "currency": project.currency,
        "team_size": project.team_size,
        "summary": project.summary,
    }

    if "owner_ref" in changes:
        project.owner_id = await _resolve_member_id(session, ctx, changes.pop("owner_ref"))
    if "currency" in changes and changes["currency"] is not None:
        changes["currency"] = changes["currency"].upper()
    for field, value in changes.items():
        setattr(project, field, value)

    if (
        project.start_date is not None
        and project.target_date is not None
        and project.target_date < project.start_date
    ):
        raise ValidationProblem(
            "target_date must be on or after start_date",
            errors=[{"field": "target_date", "message": "must be on or after start_date"}],
        )

    project.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "name": project.name,
        "client": project.client,
        "owner_id": str(project.owner_id) if project.owner_id else None,
        "phase": project.phase,
        "health": project.health,
        "progress_pct": project.progress_pct,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "target_date": project.target_date.isoformat() if project.target_date else None,
        "budget_minor": project.budget_minor,
        "spent_minor": project.spent_minor,
        "currency": project.currency,
        "team_size": project.team_size,
        "summary": project.summary,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="project.updated",
        entity="project",
        entity_id=project.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PROJECT_UPDATED,
        aggregate_type="project",
        aggregate_id=project.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "project_id": str(project.id),
            "code": project.code,
            "phase": project.phase,
            "health": project.health,
        },
        actor=_actor(ctx),
    )
    return project


async def delete_project(
    session: AsyncSession,
    ctx: ScopeContext,
    project: Project,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a project, and its milestones and board cards with it, in the same
    transaction.

    The DB-level `ON DELETE CASCADE` on `milestones.project_id` / `tasks.project_id`
    (`models.py`) only fires on a real row removal, so a soft-deleted project with live
    children would leave them listed with an empty `project_code` / `project_name`
    (`selectors.milestone_out` / `task_out` fall back to `""` once the project is filtered out
    of `projects_by_id`) — the cascade is done here instead, so they disappear together.
    """
    ctx.assert_can_touch(owner_id=project.owner_id)
    if project.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    project.deleted_at = now
    project.updated_by = str(ctx.principal.sub)

    await session.execute(
        update(Milestone)
        .where(
            Milestone.organization_id == ctx.organization_id,
            Milestone.project_id == project.id,
            Milestone.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_by=str(ctx.principal.sub))
    )
    await session.execute(
        update(Task)
        .where(
            Task.organization_id == ctx.organization_id,
            Task.project_id == project.id,
            Task.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_by=str(ctx.principal.sub))
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="project.deleted",
        entity="project",
        entity_id=project.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat(), "code": project.code},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PROJECT_DELETED,
        aggregate_type="project",
        aggregate_id=project.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "project_id": str(project.id),
            "code": project.code,
        },
        actor=_actor(ctx),
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
    """HTTP convenience over `create_milestone`, resolving the project by code and owner by ref.

    Object-level scope is checked here too — see `create_project_from`."""
    project = await selectors.get_project(session, ctx.organization_id, payload.project_code)
    if project is None:
        raise NotFoundProblem(f"No project {payload.project_code!r} in this organization")
    owner_id = await _resolve_member_id(session, ctx, payload.owner_ref)
    ctx.assert_can_touch(owner_id=owner_id)
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


async def update_milestone(
    session: AsyncSession,
    ctx: ScopeContext,
    milestone: Milestone,
    payload: MilestoneUpdate,
    *,
    request: Request | None = None,
) -> Milestone:
    """Patch a milestone. Not `project_code` — a milestone never changes project through this
    route."""
    ctx.assert_can_touch(owner_id=milestone.owner_id)
    changes = payload.model_dump(exclude_unset=True)

    before = {
        "title": milestone.title,
        "owner_id": str(milestone.owner_id) if milestone.owner_id else None,
        "due_date": milestone.due_date.isoformat() if milestone.due_date else None,
        "status": milestone.status,
        "progress_pct": milestone.progress_pct,
    }

    if "owner_ref" in changes:
        milestone.owner_id = await _resolve_member_id(session, ctx, changes.pop("owner_ref"))
    for field, value in changes.items():
        setattr(milestone, field, value)
    milestone.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "title": milestone.title,
        "owner_id": str(milestone.owner_id) if milestone.owner_id else None,
        "due_date": milestone.due_date.isoformat() if milestone.due_date else None,
        "status": milestone.status,
        "progress_pct": milestone.progress_pct,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="milestone.updated",
        entity="milestone",
        entity_id=milestone.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MILESTONE_UPDATED,
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "milestone_id": str(milestone.id),
            "project_id": str(milestone.project_id),
            "status": milestone.status,
        },
        actor=_actor(ctx),
    )
    return milestone


async def delete_milestone(
    session: AsyncSession,
    ctx: ScopeContext,
    milestone: Milestone,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a milestone. It has no children to cascade."""
    ctx.assert_can_touch(owner_id=milestone.owner_id)
    if milestone.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    milestone.deleted_at = now
    milestone.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="milestone.deleted",
        entity="milestone",
        entity_id=milestone.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat()},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_MILESTONE_DELETED,
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "milestone_id": str(milestone.id),
            "project_id": str(milestone.project_id),
        },
        actor=_actor(ctx),
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
    """Create a board card on a project. `project_id` is validated by the caller.

    `completed_at` is stamped only for a card created directly in `done` (`docs/insights/
    events.md` §2) — the ordinary path through `backlog` never sets it here.
    """
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
        completed_at=utcnow() if stage == "done" else None,
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
            # `docs/insights/events.md` §2 (I6): the fields `insights.task_facts` needs beyond
            # the id and stage this already carried.
            "title": task.title,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "points": task.points,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        },
        actor=_actor(ctx),
    )
    return task


async def create_task_from(
    session: AsyncSession, ctx: ScopeContext, payload: TaskIn, *, request: Request | None = None
) -> Task:
    """HTTP convenience over `create_task`, resolving the project by code and assignee by ref.

    Object-level scope is checked here too: without it, an OWN-scoped `task.write` holder —
    exactly `PROJECTS_MEMBER` — could plant a card assigned to anyone, not just themselves. The
    check resolves through `assignee_id`'s `directory_id` the same way `update_task` does, so an
    OWN-scoped create is forced to name the caller as assignee (or is refused if left
    unassigned — there is no "own" reading of an unowned card).
    """
    project = await selectors.get_project(session, ctx.organization_id, payload.project_code)
    if project is None:
        raise NotFoundProblem(f"No project {payload.project_code!r} in this organization")
    assignee_id = await _resolve_member_id(session, ctx, payload.assignee_ref)
    owner_directory_id = await _owner_directory_id(session, ctx.organization_id, assignee_id)
    ctx.assert_can_touch(owner_id=owner_directory_id)
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


async def update_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: Task,
    payload: TaskUpdate,
    *,
    request: Request | None = None,
) -> Task:
    """Move or edit a board card — this is what persists a Kanban drag. Not `project_code` — a
    card never changes project through this route. See `_owner_directory_id` for why the object
    check resolves through the assignee's `directory_id` rather than comparing `assignee_id`
    directly."""
    owner_directory_id = await _owner_directory_id(session, ctx.organization_id, task.assignee_id)
    ctx.assert_can_touch(owner_id=owner_directory_id)
    changes = payload.model_dump(exclude_unset=True)

    before = {
        "title": task.title,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "stage": task.stage,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "points": task.points,
    }

    stage_before = task.stage
    if "assignee_ref" in changes:
        task.assignee_id = await _resolve_member_id(session, ctx, changes.pop("assignee_ref"))
    for field, value in changes.items():
        setattr(task, field, value)
    # `completed_at` is set when the card enters `done` and cleared when it leaves
    # (`docs/insights/events.md` §2, the `projects.tasks.completed_at` column ADR-0020 §3 adds).
    # A change that never touches `stage` leaves it exactly as it was.
    if task.stage != stage_before:
        task.completed_at = utcnow() if task.stage == "done" else None
    task.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "title": task.title,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "stage": task.stage,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "points": task.points,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="task.updated",
        entity="task",
        entity_id=task.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TASK_UPDATED,
        aggregate_type="task",
        aggregate_id=task.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "task_id": str(task.id),
            "project_id": str(task.project_id),
            "stage": task.stage,
            # `docs/insights/events.md` §2 (I6): the card's full state after the change, not
            # just `stage` — insights folds a full-state upsert, never a diff.
            "title": task.title,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "points": task.points,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        },
        actor=_actor(ctx),
    )
    return task


async def delete_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: Task,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a board card."""
    owner_directory_id = await _owner_directory_id(session, ctx.organization_id, task.assignee_id)
    ctx.assert_can_touch(owner_id=owner_directory_id)
    if task.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    task.deleted_at = now
    task.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="task.deleted",
        entity="task",
        entity_id=task.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat()},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TASK_DELETED,
        aggregate_type="task",
        aggregate_id=task.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "task_id": str(task.id),
            "project_id": str(task.project_id),
        },
        actor=_actor(ctx),
    )
