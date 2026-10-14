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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, OutboxEvent, Person, Project, Task
from app.schemas import PersonIn, PersonPatch, ProjectIn, ProjectUpdate, TaskIn, TaskUpdate
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import actor_for, publish_event
from platform_core.models import utcnow

# Event names. `pulseboard.*` because these are the product's own, not the platform's.
EVT_PROJECT_CREATED = "pulseboard.engagements.project.created"
EVT_PROJECT_UPDATED = "pulseboard.engagements.project.updated"
EVT_PROJECT_DELETED = "pulseboard.engagements.project.deleted"
EVT_TASK_CREATED = "pulseboard.board.task.created"
EVT_TASK_UPDATED = "pulseboard.board.task.updated"
EVT_TASK_DELETED = "pulseboard.board.task.deleted"
EVT_PERSON_CREATED = "pulseboard.team.person.created"
EVT_PERSON_UPDATED = "pulseboard.team.person.updated"
EVT_PERSON_DELETED = "pulseboard.team.person.deleted"


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
    request: Request | None = None,
) -> Person:
    """Create a roster person. `person_id` lets the demo seed use a deterministic UUID so the
    timesheet and standup services carry the same person id without a cross-schema read.

    The duplicate check matches active **and soft-deleted** rows: `uq_people_org_ref` is not a
    partial index, so a soft-deleted person still occupies the ref and an unmatched insert
    would fall through to the database as an unhandled `IntegrityError`.
    """
    if await selectors.person_ref_taken(session, ctx.organization_id, ref):
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

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="person.created",
        entity="person",
        entity_id=person.id,
        after={"ref": ref, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PERSON_CREATED,
        aggregate_type="person",
        aggregate_id=person.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "person_id": str(person.id),
            "ref": ref,
        },
        actor=actor_for(ctx),
    )
    return person


async def create_person_from(
    session: AsyncSession, ctx: ScopeContext, payload: PersonIn, *, request: Request | None = None
) -> Person:
    """HTTP convenience over `create_person`."""
    return await create_person(
        session,
        ctx,
        ref=payload.ref,
        name=payload.name,
        role=payload.role,
        location=payload.location,
        capacity_hours=payload.capacity_hours,
        request=request,
    )


async def update_person(
    session: AsyncSession,
    ctx: ScopeContext,
    person: Person,
    payload: PersonPatch,
    *,
    request: Request | None = None,
) -> Person:
    """Patch the plain roster fields. `ref` is immutable — it is the cross-service join
    value the timesheet and standup schemas carry as a plain string."""
    changes = payload.model_dump(exclude_unset=True)
    before = {
        "name": person.name,
        "role": person.role,
        "location": person.location,
        "capacity_hours": person.capacity_hours,
    }
    for field, value in changes.items():
        setattr(person, field, value)
    person.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "name": person.name,
        "role": person.role,
        "location": person.location,
        "capacity_hours": person.capacity_hours,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="person.updated",
        entity="person",
        entity_id=person.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PERSON_UPDATED,
        aggregate_type="person",
        aggregate_id=person.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "person_id": str(person.id),
            "ref": person.ref,
        },
        actor=actor_for(ctx),
    )
    return person


async def delete_person(
    session: AsyncSession,
    ctx: ScopeContext,
    person: Person,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a roster person.

    Hard delete is refused (`Must not be built`): timesheets and standups carry this person's
    id as a plain cross-schema UUID, and audit rows reference it too. Any engagement or task
    that names this person as owner/assignee is nulled in the same transaction, so the board
    and the portfolio keep rendering "Unassigned" / "Unknown" instead of joining to a roster
    entry that no longer exists — the FK's `ON DELETE SET NULL` only fires on a hard delete.
    """
    if person.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    person.deleted_at = now
    person.updated_by = str(ctx.principal.sub)

    await session.execute(
        update(Project)
        .where(Project.organization_id == ctx.organization_id, Project.owner_id == person.id)
        .values(owner_id=None, updated_by=str(ctx.principal.sub))
    )
    await session.execute(
        update(Task)
        .where(Task.organization_id == ctx.organization_id, Task.assignee_id == person.id)
        .values(assignee_id=None, updated_by=str(ctx.principal.sub))
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="person.deleted",
        entity="person",
        entity_id=person.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat(), "ref": person.ref},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PERSON_DELETED,
        aggregate_type="person",
        aggregate_id=person.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "person_id": str(person.id),
            "ref": person.ref,
        },
        actor=actor_for(ctx),
    )


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
        actor=actor_for(ctx),
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


async def update_project(
    session: AsyncSession,
    ctx: ScopeContext,
    project: Project,
    payload: ProjectUpdate,
    *,
    request: Request | None = None,
) -> Project:
    """Patch the plain engagement fields. `code` is immutable — it is the URL key and the
    timesheet `project_ref`, so a rename would silently orphan rows in another schema."""
    ctx.assert_can_touch(owner_id=project.owner_id)
    changes = payload.model_dump(exclude_unset=True)

    before = {
        "name": project.name,
        "client": project.client,
        "status": project.status,
        "progress_pct": project.progress_pct,
        "budget_minor": project.budget_minor,
        "spent_minor": project.spent_minor,
        "currency": project.currency,
        "owner_id": str(project.owner_id) if project.owner_id else None,
        "billable": project.billable,
        "open_tasks": project.open_tasks,
    }

    if "owner_ref" in changes:
        project.owner_id = await _resolve_person_id(session, ctx, changes.pop("owner_ref"))
    if "currency" in changes and changes["currency"] is not None:
        changes["currency"] = changes["currency"].upper()
    for field, value in changes.items():
        setattr(project, field, value)
    project.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "name": project.name,
        "client": project.client,
        "status": project.status,
        "progress_pct": project.progress_pct,
        "budget_minor": project.budget_minor,
        "spent_minor": project.spent_minor,
        "currency": project.currency,
        "owner_id": str(project.owner_id) if project.owner_id else None,
        "billable": project.billable,
        "open_tasks": project.open_tasks,
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
            "status": project.status,
        },
        actor=actor_for(ctx),
    )
    return project


async def delete_project(
    session: AsyncSession,
    ctx: ScopeContext,
    project: Project,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete an engagement, and its board cards with it.

    A hard delete's FK `ON DELETE CASCADE` only fires on a real row removal, so a soft-deleted
    project with live tasks would leave orphan cards rendering their project as "—"
    (`KanbanBoard.tsx:155`) — the cascade is done here, in the same transaction, instead.
    """
    ctx.assert_can_touch(owner_id=project.owner_id)
    if project.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = utcnow()
    project.deleted_at = now
    project.updated_by = str(ctx.principal.sub)

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
        actor=actor_for(ctx),
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
    """Create a board card on an engagement. `project_id` is validated by the caller.

    The duplicate check matches active **and soft-deleted** rows — `uq_tasks_org_key` is not a
    partial index, so a soft-deleted task still occupies the key."""
    if await selectors.task_key_taken(session, ctx.organization_id, key):
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
        actor=actor_for(ctx),
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


async def update_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: Task,
    payload: TaskUpdate,
    *,
    request: Request | None = None,
) -> Task:
    """Move or edit a board card — this is what persists a Kanban drag. `key` and the owning
    engagement are immutable; `status` maps onto the stored `stage`, and `assignee_ref`
    resolves through the roster same as at create time."""
    ctx.assert_can_touch(owner_id=task.assignee_id)
    changes = payload.model_dump(exclude_unset=True)

    before = {
        "title": task.title,
        "stage": task.stage,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "estimate_hours": task.estimate_hours,
        "priority": task.priority,
        "tags": list(task.tags),
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }

    if "status" in changes:
        task.stage = changes.pop("status")
    if "assignee_ref" in changes:
        task.assignee_id = await _resolve_person_id(session, ctx, changes.pop("assignee_ref"))
    for field, value in changes.items():
        setattr(task, field, value)
    task.updated_by = str(ctx.principal.sub)
    await session.flush()

    after = {
        "title": task.title,
        "stage": task.stage,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "estimate_hours": task.estimate_hours,
        "priority": task.priority,
        "tags": list(task.tags),
        "due_date": task.due_date.isoformat() if task.due_date else None,
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
            "key": task.key,
            "stage": task.stage,
        },
        actor=actor_for(ctx),
    )
    return task


async def delete_task(
    session: AsyncSession,
    ctx: ScopeContext,
    task: Task,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a board card. Hard delete is refused (`Must not be built`) — audit rows
    reference this task's id."""
    ctx.assert_can_touch(owner_id=task.assignee_id)
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
        after={"deleted_at": now.isoformat(), "key": task.key},
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
            "key": task.key,
        },
        actor=actor_for(ctx),
    )
