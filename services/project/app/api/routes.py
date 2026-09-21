"""HTTP surface for the project service — PulseBoard's engagements and board.

Mounted behind the gateway's `/api/v1/projects` prefix (see `services/app-gateway/routes.json`),
so every route path here begins `/projects/...`. The board, the roster and the summary sit under
that same prefix (`/projects/tasks`, `/projects/people`, `/projects/summary`) — longest-prefix
routing brings them here, and the static segments are declared **before** `/projects/{code}` so
FastAPI never swallows them as a project code.

The read endpoints are the priority — they are what flips the PulseBoard SPA from fixtures to
live — and each returns exactly the shape `frontend/product-pulseboard/src/api/types.ts`
declares, inside the platform's `Page` envelope (`items` / `total`). Every route declares the
permission it needs and is guarded by `require_product("pulseboard")` as defence in depth behind
the gateway's own entitlement gate. Reads go through `selectors`, writes through `services`; a
router never builds its own query. Every route takes the tenant-bound session and opens no
transaction of its own — the chokepoint owns it.

Object-level scope is re-applied on the detail route: filtering the list and forgetting the
detail is the named vulnerability, so a caller who can only see their own engagements is
refused another's by code.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    AttentionItem,
    HealthBucket,
    PersonIn,
    PersonOut,
    PersonPatch,
    ProjectIn,
    ProjectOut,
    ProjectSummaryOut,
    ProjectUpdate,
    ServiceInfo,
    TaskIn,
    TaskOut,
    TaskUpdate,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the pulseboard scope resolver at import of the api module ties it to the product
# it serves: the process that mounts these routes is the one that answers its scope questions.
scope.register()

router = APIRouter(tags=["projects"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `pulseboard` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("pulseboard"))

ProjectRead = Annotated[ScopeContext, Depends(require("pulseboard.engagements.project.read"))]
ProjectWrite = Annotated[ScopeContext, Depends(require("pulseboard.engagements.project.write"))]
TaskRead = Annotated[ScopeContext, Depends(require("pulseboard.board.task.read"))]
TaskWrite = Annotated[ScopeContext, Depends(require("pulseboard.board.task.write"))]
PersonRead = Annotated[ScopeContext, Depends(require("pulseboard.team.person.read"))]
PersonWrite = Annotated[ScopeContext, Depends(require("pulseboard.team.person.write"))]


@router.get("/projects/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ summary


@router.get(
    "/projects/summary",
    response_model=ProjectSummaryOut,
    summary="Engagement-side overview rollups",
    dependencies=[Entitled],
)
async def summary(session: SessionDep, ctx: ProjectRead) -> ProjectSummaryOut:
    """Active engagements, tasks due this week, the health breakdown and the needs-attention
    list — the same arithmetic the SPA's `derive.ts` runs over the lists. Utilization and
    billable hours live on the timesheet service's `/timesheets/summary`."""
    projects = await selectors.all_projects(session, ctx)
    tasks = await selectors.all_tasks(session, ctx)
    today = date.today()

    return ProjectSummaryOut(
        active_projects=domain.active_projects(projects),
        tasks_due_this_week=domain.tasks_due_in(tasks, domain.week_dates(today)),
        health=[HealthBucket(**bucket) for bucket in domain.health_breakdown(projects)],
        needs_attention=[AttentionItem(**item) for item in domain.needs_attention(projects, today)],
    )


# ------------------------------------------------------------------ people (the roster)


@router.get(
    "/projects/people",
    response_model=Page[PersonOut],
    summary="The delivery-team roster",
    dependencies=[Entitled],
)
async def list_people(session: SessionDep, ctx: PersonRead, params: PageQuery) -> Page[PersonOut]:
    stmt = selectors.people_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.person_out(p) for p in rows], total=total, params=params)


@router.post(
    "/projects/people",
    response_model=PersonOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a roster person",
    dependencies=[Entitled],
)
async def create_person(
    payload: PersonIn, session: SessionDep, ctx: PersonWrite, request: Request
) -> PersonOut:
    person = await services.create_person_from(session, ctx, payload, request=request)
    return selectors.person_out(person)


@router.patch(
    "/projects/people/{ref}",
    response_model=PersonOut,
    summary="Edit a roster person",
    dependencies=[Entitled],
)
async def update_person(
    ref: str, payload: PersonPatch, session: SessionDep, ctx: PersonWrite, request: Request
) -> PersonOut:
    person = await selectors.get_person_by_ref(session, ctx.organization_id, ref)
    if person is None:
        raise NotFoundProblem("That person is not part of this organization")
    updated = await services.update_person(session, ctx, person, payload, request=request)
    return selectors.person_out(updated)


@router.delete(
    "/projects/people/{ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a roster person",
    dependencies=[Entitled],
)
async def delete_person(
    ref: str, session: SessionDep, ctx: PersonWrite, request: Request
) -> Response:
    person = await selectors.get_person_by_ref(session, ctx.organization_id, ref)
    if person is None:
        raise NotFoundProblem("That person is not part of this organization")
    await services.delete_person(session, ctx, person, request=request)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ board / tasks


@router.get(
    "/projects/tasks",
    response_model=Page[TaskOut],
    summary="Every card on the board",
    dependencies=[Entitled],
)
async def list_tasks(
    session: SessionDep,
    ctx: TaskRead,
    params: PageQuery,
    task_status: Annotated[
        str | None, Query(alias="status", description="backlog | in_progress | review | done")
    ] = None,
    project: Annotated[str | None, Query(description="Filter by engagement code")] = None,
    assignee: Annotated[str | None, Query(description="Filter by person ref")] = None,
) -> Page[TaskOut]:
    stmt = selectors.tasks_stmt(
        ctx, status=task_status, project_code=project, assignee_ref=assignee
    )
    rows, total = await paginate(session, stmt, params)
    people = await selectors.people_by_id(session, ctx.organization_id)
    items = [selectors.task_out(t, people=people) for t in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/projects/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a board card",
    dependencies=[Entitled],
)
async def create_task(
    payload: TaskIn, session: SessionDep, ctx: TaskWrite, request: Request
) -> TaskOut:
    task = await services.create_task_from(session, ctx, payload, request=request)
    people = await selectors.people_by_id(session, ctx.organization_id)
    return selectors.task_out(task, people=people)


@router.patch(
    "/projects/tasks/{key}",
    response_model=TaskOut,
    summary="Move or edit a board card",
    dependencies=[Entitled],
)
async def update_task(
    key: str, payload: TaskUpdate, session: SessionDep, ctx: TaskWrite, request: Request
) -> TaskOut:
    task = await selectors.get_task_by_key(session, ctx.organization_id, key)
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    updated = await services.update_task(session, ctx, task, payload, request=request)
    people = await selectors.people_by_id(session, ctx.organization_id)
    return selectors.task_out(updated, people=people)


@router.delete(
    "/projects/tasks/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a board card",
    dependencies=[Entitled],
)
async def delete_task(key: str, session: SessionDep, ctx: TaskWrite, request: Request) -> Response:
    task = await selectors.get_task_by_key(session, ctx.organization_id, key)
    if task is None:
        raise NotFoundProblem("That task is not part of this organization")
    await services.delete_task(session, ctx, task, request=request)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ engagements


@router.get(
    "/projects",
    response_model=Page[ProjectOut],
    summary="The engagement portfolio",
    dependencies=[Entitled],
)
async def list_projects(
    session: SessionDep,
    ctx: ProjectRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, code or client")] = None,
    project_status: Annotated[
        str | None, Query(alias="status", description="on_track | at_risk | blocked | done")
    ] = None,
    billable: Annotated[bool | None, Query()] = None,
    sort: Annotated[str | None, Query(description="code | progress | due")] = None,
) -> Page[ProjectOut]:
    stmt = selectors.projects_stmt(ctx, q=q, status=project_status, billable=billable, sort=sort)
    rows, total = await paginate(session, stmt, params)
    people = await selectors.people_by_id(session, ctx.organization_id)
    items = [selectors.project_out(p, people=people) for p in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an engagement",
    dependencies=[Entitled],
)
async def create_project(
    payload: ProjectIn, session: SessionDep, ctx: ProjectWrite, request: Request
) -> ProjectOut:
    project = await services.create_project_from(session, ctx, payload, request=request)
    people = await selectors.people_by_id(session, ctx.organization_id)
    return selectors.project_out(project, people=people)


@router.get(
    "/projects/{code}",
    response_model=ProjectOut,
    summary="One engagement",
    dependencies=[Entitled],
)
async def read_project(code: str, session: SessionDep, ctx: ProjectRead) -> ProjectOut:
    project = await selectors.get_project(session, ctx.organization_id, code)
    if project is None:
        raise NotFoundProblem("That project is not part of this organization")
    ctx.assert_can_touch(owner_id=project.owner_id)
    people = await selectors.people_by_id(session, ctx.organization_id)
    return selectors.project_out(project, people=people)


@router.patch(
    "/projects/{code}",
    response_model=ProjectOut,
    summary="Edit an engagement",
    dependencies=[Entitled],
)
async def update_project(
    code: str, payload: ProjectUpdate, session: SessionDep, ctx: ProjectWrite, request: Request
) -> ProjectOut:
    project = await selectors.get_project(session, ctx.organization_id, code)
    if project is None:
        raise NotFoundProblem("That project is not part of this organization")
    updated = await services.update_project(session, ctx, project, payload, request=request)
    people = await selectors.people_by_id(session, ctx.organization_id)
    return selectors.project_out(updated, people=people)


@router.delete(
    "/projects/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive an engagement",
    dependencies=[Entitled],
)
async def delete_project(
    code: str, session: SessionDep, ctx: ProjectWrite, request: Request
) -> Response:
    project = await selectors.get_project(session, ctx.organization_id, code)
    if project is None:
        raise NotFoundProblem("That project is not part of this organization")
    await services.delete_project(session, ctx, project, request=request)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
