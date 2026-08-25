"""Reads for the projects service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

Projects scope is org-wide with an OWN reading of "the projects I own / the tasks assigned to
me" — the owner column is the member id (see `scope.py`). TEAM and LOCATION are not offered; a
selector passes only `owner_col`, and `ScopeContext.apply` refuses those rungs rather than
widening.

This module also holds the ORM→schema projections, because shaping a row for the wire is part
of reading it: the embedded `Person`, the denormalised project code/name and the derived
`milestone_count` are all resolved here so routes stay thin.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Member, Milestone, Project, Task
from app.schemas import MilestoneOut, Person, ProjectOut, TaskOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ members (people)

_FALLBACK_PERSON = Person(id="", name="Unassigned", role="")


async def members_by_id(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Member]:
    """Every member of the org, keyed by id — for building the embedded `Person` objects."""
    stmt = select(Member).where(Member.organization_id == org_id, Member.deleted_at.is_(None))
    return {m.id: m for m in (await session.execute(stmt)).scalars().all()}


async def get_member_by_ref(session: AsyncSession, org_id: uuid.UUID, ref: str) -> Member | None:
    """A member by their stable `ref` (`p-01`), within one organization."""
    stmt = select(Member).where(
        Member.organization_id == org_id,
        Member.ref == ref,
        Member.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def person_of(members: dict[uuid.UUID, Member], member_id: uuid.UUID | None) -> Person:
    """The embedded `Person` for a (possibly absent) member id."""
    member = members.get(member_id) if member_id is not None else None
    if member is None:
        return _FALLBACK_PERSON
    return Person(id=member.ref, name=member.name, role=member.role)


# ------------------------------------------------------------------ projects


def projects_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    health: str | None = None,
    phase: str | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Scope-filtered project list. `ctx.apply` is the enforcement point."""
    stmt = select(Project).where(
        Project.organization_id == ctx.organization_id,
        Project.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Project.owner_id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Project.name).like(needle),
                func.lower(Project.code).like(needle),
                func.lower(Project.client).like(needle),
                func.lower(Project.summary).like(needle),
            )
        )
    if health:
        stmt = stmt.where(Project.health == health)
    if phase:
        stmt = stmt.where(Project.phase == phase)

    if sort == "progress":
        return stmt.order_by(Project.progress_pct.desc(), Project.code.asc())
    if sort == "target":
        return stmt.order_by(Project.target_date.asc(), Project.code.asc())
    return stmt.order_by(Project.code.asc())


async def get_project(session: AsyncSession, org_id: uuid.UUID, code: str) -> Project | None:
    """Org-scoped fetch by human code. None on a tenant mismatch or a soft-deleted row —
    confirming a code exists in *some* organization is itself a disclosure."""
    stmt = select(Project).where(
        Project.code == code,
        Project.organization_id == org_id,
        Project.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_projects(session: AsyncSession, ctx: ScopeContext) -> list[Project]:
    """Every in-scope project — powers the overview, board and timeline rollups."""
    return list((await session.execute(projects_stmt(ctx))).scalars().all())


async def milestone_count_map(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Milestones per project id — the derived `milestone_count` on a project row."""
    stmt = (
        select(Milestone.project_id, func.count())
        .where(Milestone.organization_id == org_id, Milestone.deleted_at.is_(None))
        .group_by(Milestone.project_id)
    )
    return {row[0]: int(row[1]) for row in (await session.execute(stmt)).all()}


# ------------------------------------------------------------------ milestones


def milestones_stmt(ctx: ScopeContext, *, status: str | None = None) -> Select[Any]:
    """Scope-filtered milestone list, soonest due first."""
    stmt = select(Milestone).where(
        Milestone.organization_id == ctx.organization_id,
        Milestone.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Milestone.owner_id)
    if status:
        stmt = stmt.where(Milestone.status == status)
    return stmt.order_by(Milestone.due_date.asc())


async def all_milestones(session: AsyncSession, ctx: ScopeContext) -> list[Milestone]:
    return list((await session.execute(milestones_stmt(ctx))).scalars().all())


async def milestones_for_project(
    session: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Milestone]:
    stmt = (
        select(Milestone)
        .where(
            Milestone.organization_id == org_id,
            Milestone.project_id == project_id,
            Milestone.deleted_at.is_(None),
        )
        .order_by(Milestone.due_date.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------------------------ tasks


def tasks_stmt(
    ctx: ScopeContext, *, stage: str | None = None, project_code: str | None = None
) -> Select[Any]:
    """Scope-filtered board task list."""
    stmt = select(Task).where(
        Task.organization_id == ctx.organization_id,
        Task.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Task.assignee_id)
    if stage:
        stmt = stmt.where(Task.stage == stage)
    return stmt.order_by(Task.created_at.asc())


async def all_tasks(session: AsyncSession, ctx: ScopeContext) -> list[Task]:
    return list((await session.execute(tasks_stmt(ctx))).scalars().all())


# ------------------------------------------------------------------ project lookup maps


async def projects_by_id(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Project]:
    """Every project keyed by id — for the denormalised code/name on a milestone or task row."""
    stmt = select(Project).where(Project.organization_id == org_id, Project.deleted_at.is_(None))
    return {p.id: p for p in (await session.execute(stmt)).scalars().all()}


# ------------------------------------------------------------------ projections (ORM → wire)


def project_out(
    project: Project,
    *,
    members: dict[uuid.UUID, Member],
    milestone_count: int = 0,
) -> ProjectOut:
    return ProjectOut(
        id=str(project.id),
        code=project.code,
        name=project.name,
        client=project.client,
        owner=person_of(members, project.owner_id),
        phase=project.phase,
        health=project.health,
        progress_pct=project.progress_pct,
        start_date=project.start_date,
        target_date=project.target_date,
        budget_minor=project.budget_minor,
        spent_minor=project.spent_minor,
        currency=project.currency,
        team_size=project.team_size,
        open_tasks=project.open_tasks,
        milestone_count=milestone_count,
        summary=project.summary,
    )


def milestone_out(
    milestone: Milestone,
    *,
    members: dict[uuid.UUID, Member],
    projects: dict[uuid.UUID, Project],
) -> MilestoneOut:
    project = projects.get(milestone.project_id)
    return MilestoneOut(
        id=str(milestone.id),
        project_id=str(milestone.project_id),
        project_code=project.code if project else "",
        project_name=project.name if project else "",
        title=milestone.title,
        owner=person_of(members, milestone.owner_id),
        due_date=milestone.due_date,
        status=milestone.status,
        progress_pct=milestone.progress_pct,
    )


def task_out(
    task: Task,
    *,
    members: dict[uuid.UUID, Member],
    projects: dict[uuid.UUID, Project],
) -> TaskOut:
    project = projects.get(task.project_id)
    return TaskOut(
        id=str(task.id),
        project_id=str(task.project_id),
        project_code=project.code if project else "",
        title=task.title,
        assignee=person_of(members, task.assignee_id),
        stage=task.stage,
        priority=task.priority,
        due_date=task.due_date,
        points=task.points,
    )
