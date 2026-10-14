"""Reads for the project service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

PulseBoard scope is org-wide with an OWN reading of "the engagements I own / the cards
assigned to me" — the owner column is the person id (see `scope.py`). TEAM and LOCATION are
not offered; a selector passes only `owner_col`, and `ScopeContext.apply` refuses those rungs
rather than widening.

This module also holds the ORM→schema projections, because shaping a row for the wire is part
of reading it: the person-ref `owner_id` / `assignee_id` the SPA joins on are resolved here so
routes stay thin.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, Project, Task
from app.schemas import PersonOut, ProjectOut, TaskOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ people


def people_stmt(ctx: ScopeContext) -> Select[Any]:
    """The delivery-team roster, in ref order. Roster reads floor at ORG (catalogue), so
    `ctx.apply` is still called with the owner column for uniformity — at ORG it is a no-op,
    and a mis-granted narrower scope narrows rather than leaks."""
    stmt = select(Person).where(
        Person.organization_id == ctx.organization_id,
        Person.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Person.id)
    return stmt.order_by(Person.ref.asc())


async def people_by_id(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Person]:
    """Every person of the org, keyed by id — for resolving refs on project/task rows."""
    stmt = select(Person).where(Person.organization_id == org_id, Person.deleted_at.is_(None))
    return {p.id: p for p in (await session.execute(stmt)).scalars().all()}


async def get_person_by_ref(session: AsyncSession, org_id: uuid.UUID, ref: str) -> Person | None:
    """A person by their stable `ref` (`p01`), within one organization."""
    stmt = select(Person).where(
        Person.organization_id == org_id,
        Person.ref == ref,
        Person.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def person_ref_taken(session: AsyncSession, org_id: uuid.UUID, ref: str) -> bool:
    """Whether `ref` is already used in this organization, active **or soft-deleted**.

    `uq_people_org_ref` is not a partial index, so a soft-deleted row still occupies the
    value; the create-time duplicate check has to see it too or the insert falls through to
    the database and an `IntegrityError` becomes an unhandled 500.
    """
    stmt = select(Person.id).where(Person.organization_id == org_id, Person.ref == ref)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


def ref_of(people: dict[uuid.UUID, Person], person_id: uuid.UUID | None) -> str | None:
    """The wire `ref` for a (possibly absent) person id. None stays None — the SPA renders
    "Unassigned" for a null assignee."""
    if person_id is None:
        return None
    person = people.get(person_id)
    return person.ref if person else None


# ------------------------------------------------------------------ projects


def projects_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    billable: bool | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Scope-filtered engagement list. `ctx.apply` is the enforcement point."""
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
            )
        )
    if status:
        stmt = stmt.where(Project.status == status)
    if billable is not None:
        stmt = stmt.where(Project.billable == billable)

    if sort == "progress":
        return stmt.order_by(Project.progress_pct.desc(), Project.code.asc())
    if sort == "due":
        return stmt.order_by(Project.due_date.asc(), Project.code.asc())
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


async def project_code_taken(session: AsyncSession, org_id: uuid.UUID, code: str) -> bool:
    """Whether `code` is already used in this organization, active **or soft-deleted** — the
    same non-partial-index reasoning as `person_ref_taken`."""
    stmt = select(Project.id).where(Project.organization_id == org_id, Project.code == code)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def all_projects(session: AsyncSession, ctx: ScopeContext) -> list[Project]:
    """Every in-scope engagement — powers the summary rollups."""
    return list((await session.execute(projects_stmt(ctx))).scalars().all())


# ------------------------------------------------------------------ tasks


def tasks_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    project_code: str | None = None,
    assignee_ref: str | None = None,
) -> Select[Any]:
    """Scope-filtered board list, in card-key order (stable for the board columns)."""
    stmt = select(Task).where(
        Task.organization_id == ctx.organization_id,
        Task.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Task.assignee_id)
    if status:
        stmt = stmt.where(Task.stage == status)
    if project_code:
        stmt = stmt.where(
            Task.project_id.in_(
                select(Project.id).where(
                    Project.organization_id == ctx.organization_id,
                    Project.code == project_code,
                    Project.deleted_at.is_(None),
                )
            )
        )
    if assignee_ref:
        stmt = stmt.where(
            Task.assignee_id.in_(
                select(Person.id).where(
                    Person.organization_id == ctx.organization_id,
                    Person.ref == assignee_ref,
                    Person.deleted_at.is_(None),
                )
            )
        )
    return stmt.order_by(Task.key.asc())


async def get_task_by_key(session: AsyncSession, org_id: uuid.UUID, key: str) -> Task | None:
    """A board card by its human key (`PB-231`), within one organization."""
    stmt = select(Task).where(
        Task.organization_id == org_id,
        Task.key == key,
        Task.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def task_key_taken(session: AsyncSession, org_id: uuid.UUID, key: str) -> bool:
    """Whether `key` is already used in this organization, active **or soft-deleted** — the
    same non-partial-index reasoning as `person_ref_taken`."""
    stmt = select(Task.id).where(Task.organization_id == org_id, Task.key == key)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def all_tasks(session: AsyncSession, ctx: ScopeContext) -> list[Task]:
    return list((await session.execute(tasks_stmt(ctx))).scalars().all())


# ------------------------------------------------------------------ projections (ORM → wire)


def person_out(person: Person) -> PersonOut:
    return PersonOut(
        id=person.ref,
        uuid=str(person.id),
        name=person.name,
        role=person.role,
        location=person.location,
        capacity_hours=person.capacity_hours,
    )


def project_out(project: Project, *, people: dict[uuid.UUID, Person]) -> ProjectOut:
    return ProjectOut(
        id=str(project.id),
        code=project.code,
        name=project.name,
        client=project.client,
        status=project.status,
        progress_pct=project.progress_pct,
        budget_minor=project.budget_minor,
        spent_minor=project.spent_minor,
        currency=project.currency,
        owner_id=ref_of(people, project.owner_id),
        start_date=project.start_date,
        due_date=project.due_date,
        billable=project.billable,
        open_tasks=project.open_tasks,
    )


def task_out(task: Task, *, people: dict[uuid.UUID, Person]) -> TaskOut:
    return TaskOut(
        id=str(task.id),
        key=task.key,
        title=task.title,
        status=task.stage,
        assignee_id=ref_of(people, task.assignee_id),
        project_id=str(task.project_id),
        estimate_hours=task.estimate_hours,
        priority=task.priority,
        tags=list(task.tags),
        due_date=task.due_date,
    )
