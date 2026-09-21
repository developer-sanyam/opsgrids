"""HTTP surface for the projects service.

Mounted behind the gateway's `/api/v1/projects-workspace` prefix, so every route path here begins
`/projects-workspace/...`. (The bare `/api/v1/projects` prefix belongs to a different product,
pulseboard; longest-prefix routing sends `/projects-workspace/*` here — see
`services/app-gateway/routes.json`.) When the SPA flips from fixtures to live it points its
`API_BASE` at `/api/v1/projects-workspace`, so its `/projects`, `/milestones` and `/tasks` calls
land on the matching routes below — a base-path flip, not a rewrite.

The read endpoints are the priority — they are what flips the Projects SPA from fixtures to live —
and each returns exactly the shape `frontend/product-projects/src/api/types.ts` declares. Every
route declares the permission it needs and is guarded by `require_product("projects")` as defence
in depth behind the gateway's own entitlement gate. Reads go through `selectors`, writes through
`services`; a router never builds its own query. Every route takes the tenant-bound session and
opens no transaction of its own — the chokepoint owns it.

Object-level scope is re-applied on every detail route: filtering the list and forgetting the
detail is the named vulnerability, so a caller who can only see their own projects is refused
another's by code.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    BoardColumn,
    BoardOut,
    HealthSlice,
    MemberIn,
    MilestoneIn,
    MilestoneOut,
    OverviewOut,
    Person,
    ProjectIn,
    ProjectOut,
    ServiceInfo,
    TaskIn,
    TaskOut,
    TimelineMilestone,
    TimelineOut,
    TimelineProject,
    TimelineWeek,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the projects scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers projects scope questions.
scope.register()

router = APIRouter(tags=["projects"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `projects` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("projects"))

ProjectRead = Annotated[ScopeContext, Depends(require("projects.portfolio.project.read"))]
ProjectWrite = Annotated[ScopeContext, Depends(require("projects.portfolio.project.write"))]
MilestoneRead = Annotated[ScopeContext, Depends(require("projects.delivery.milestone.read"))]
MilestoneWrite = Annotated[ScopeContext, Depends(require("projects.delivery.milestone.write"))]
TaskRead = Annotated[ScopeContext, Depends(require("projects.board.task.read"))]
TaskWrite = Annotated[ScopeContext, Depends(require("projects.board.task.write"))]
#: Staffing the roster. There is no OWN reading of "the people I am" — nobody owns the member
#: they are adding — so the catalogue floors this permission at ORG.
MemberWrite = Annotated[ScopeContext, Depends(require("projects.team.member.write"))]


@router.get("/projects-workspace/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ the delivery team


@router.post(
    "/projects-workspace/members",
    response_model=Person,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member to the delivery team",
    dependencies=[Entitled],
)
async def create_member(payload: MemberIn, session: SessionDep, ctx: MemberWrite) -> Person:
    """Create the roster row a project owner or task assignee is addressed by.

    `services.create_member` refuses a `ref` already taken in this organization (409), which is
    what makes the human handle safe to key on. It stages no event: a roster row is a local
    directory entry, not a delivery fact another service reacts to.
    """
    member = await services.create_member(
        session,
        ctx,
        ref=payload.ref,
        name=payload.name,
        role=payload.role,
        directory_id=payload.directory_id,
    )
    return selectors.person_out(member)


# ------------------------------------------------------------------ portfolio


@router.get(
    "/projects-workspace/projects",
    response_model=Page[ProjectOut],
    summary="The delivery portfolio",
    dependencies=[Entitled],
)
async def list_projects(
    session: SessionDep,
    ctx: ProjectRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, code, client or summary")] = None,
    health: Annotated[str | None, Query()] = None,
    phase: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query(description="code | progress | target")] = None,
) -> Page[ProjectOut]:
    stmt = selectors.projects_stmt(ctx, q=q, health=health, phase=phase, sort=sort)
    rows, total = await paginate(session, stmt, params)
    members = await selectors.members_by_id(session, ctx.organization_id)
    counts = await selectors.milestone_count_map(session, ctx.organization_id)
    items = [
        selectors.project_out(p, members=members, milestone_count=counts.get(p.id, 0)) for p in rows
    ]
    return Page.build(items, total=total, params=params)


@router.post(
    "/projects-workspace/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    dependencies=[Entitled],
)
async def create_project(
    payload: ProjectIn, session: SessionDep, ctx: ProjectWrite, request: Request
) -> ProjectOut:
    project = await services.create_project_from(session, ctx, payload, request=request)
    members = await selectors.members_by_id(session, ctx.organization_id)
    return selectors.project_out(project, members=members, milestone_count=0)


@router.get(
    "/projects-workspace/projects/{code}",
    response_model=ProjectOut,
    summary="One project",
    dependencies=[Entitled],
)
async def read_project(code: str, session: SessionDep, ctx: ProjectRead) -> ProjectOut:
    project = await selectors.get_project(session, ctx.organization_id, code)
    if project is None:
        raise NotFoundProblem("That project is not part of this organization")
    ctx.assert_can_touch(owner_id=project.owner_id)
    members = await selectors.members_by_id(session, ctx.organization_id)
    counts = await selectors.milestone_count_map(session, ctx.organization_id)
    return selectors.project_out(
        project, members=members, milestone_count=counts.get(project.id, 0)
    )


# ------------------------------------------------------------------ milestones


@router.get(
    "/projects-workspace/milestones",
    response_model=Page[MilestoneOut],
    summary="Milestones across the portfolio",
    dependencies=[Entitled],
)
async def list_milestones(
    session: SessionDep,
    ctx: MilestoneRead,
    params: PageQuery,
    milestone_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[MilestoneOut]:
    stmt = selectors.milestones_stmt(ctx, status=milestone_status)
    rows, total = await paginate(session, stmt, params)
    members = await selectors.members_by_id(session, ctx.organization_id)
    projects = await selectors.projects_by_id(session, ctx.organization_id)
    items = [selectors.milestone_out(m, members=members, projects=projects) for m in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/projects-workspace/milestones",
    response_model=MilestoneOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a milestone",
    dependencies=[Entitled],
)
async def create_milestone(
    payload: MilestoneIn, session: SessionDep, ctx: MilestoneWrite, request: Request
) -> MilestoneOut:
    milestone = await services.create_milestone_from(session, ctx, payload, request=request)
    members = await selectors.members_by_id(session, ctx.organization_id)
    projects = await selectors.projects_by_id(session, ctx.organization_id)
    return selectors.milestone_out(milestone, members=members, projects=projects)


# ------------------------------------------------------------------ board / tasks


@router.get(
    "/projects-workspace/tasks",
    response_model=Page[TaskOut],
    summary="Every card on the workflow board",
    dependencies=[Entitled],
)
async def list_tasks(
    session: SessionDep,
    ctx: TaskRead,
    params: PageQuery,
    stage: Annotated[str | None, Query()] = None,
) -> Page[TaskOut]:
    stmt = selectors.tasks_stmt(ctx, stage=stage)
    rows, total = await paginate(session, stmt, params)
    members = await selectors.members_by_id(session, ctx.organization_id)
    projects = await selectors.projects_by_id(session, ctx.organization_id)
    items = [selectors.task_out(t, members=members, projects=projects) for t in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/projects-workspace/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a board card",
    dependencies=[Entitled],
)
async def create_task(
    payload: TaskIn, session: SessionDep, ctx: TaskWrite, request: Request
) -> TaskOut:
    task = await services.create_task_from(session, ctx, payload, request=request)
    members = await selectors.members_by_id(session, ctx.organization_id)
    projects = await selectors.projects_by_id(session, ctx.organization_id)
    return selectors.task_out(task, members=members, projects=projects)


@router.get(
    "/projects-workspace/board",
    response_model=BoardOut,
    summary="The workflow board — tasks grouped by stage",
    dependencies=[Entitled],
)
async def board(session: SessionDep, ctx: TaskRead) -> BoardOut:
    tasks = await selectors.all_tasks(session, ctx)
    members = await selectors.members_by_id(session, ctx.organization_id)
    projects = await selectors.projects_by_id(session, ctx.organization_id)
    outs = [selectors.task_out(t, members=members, projects=projects) for t in tasks]
    columns = [
        BoardColumn(
            stage=stage,
            label=domain.STAGE_LABELS[stage],
            tasks=[t for t in outs if t.stage == stage],
        )
        for stage in domain.STAGE_ORDER
    ]
    return BoardOut(columns=columns)


# ------------------------------------------------------------------ timeline


@router.get(
    "/projects-workspace/timeline",
    response_model=TimelineOut,
    summary="The delivery timeline — projects and milestones across weeks",
    dependencies=[Entitled],
)
async def timeline(session: SessionDep, ctx: ProjectRead) -> TimelineOut:
    projects = await selectors.all_projects(session, ctx)
    milestones = await selectors.all_milestones(session, ctx)

    by_project: dict[str, list[TimelineMilestone]] = {}
    for m in milestones:
        if m.due_date is None:
            continue
        by_project.setdefault(str(m.project_id), []).append(
            TimelineMilestone(id=str(m.id), title=m.title, due_date=m.due_date, status=m.status)
        )

    rows = [
        TimelineProject(
            id=str(p.id),
            code=p.code,
            name=p.name,
            phase=p.phase,
            health=p.health,
            progress_pct=p.progress_pct,
            start_date=p.start_date,
            target_date=p.target_date,
            milestones=sorted(by_project.get(str(p.id), []), key=lambda x: x.due_date),
        )
        for p in projects
    ]
    return TimelineOut(weeks=_week_axis(projects), projects=rows)


def _week_axis(projects: list) -> list[TimelineWeek]:  # type: ignore[type-arg]
    """The Monday-aligned week columns spanning the whole portfolio, capped so the axis stays
    readable. Empty when the portfolio has no dated projects."""
    starts = [p.start_date for p in projects if p.start_date]
    ends = [p.target_date for p in projects if p.target_date]
    if not starts or not ends:
        return []
    first = min(starts)
    last = max(ends)
    monday = first - timedelta(days=first.weekday())
    weeks: list[TimelineWeek] = []
    cursor = monday
    # Cap at 80 weeks so a stray far-future target cannot balloon the axis.
    while cursor <= last and len(weeks) < 80:
        weeks.append(TimelineWeek(label=cursor.strftime("%d %b"), start_date=cursor))
        cursor += timedelta(weeks=1)
    return weeks


# ------------------------------------------------------------------ overview


@router.get(
    "/projects-workspace/overview",
    response_model=OverviewOut,
    summary="Delivery overview rollups",
    dependencies=[Entitled],
)
async def overview(session: SessionDep, ctx: ProjectRead) -> OverviewOut:
    projects = await selectors.all_projects(session, ctx)
    milestones = await selectors.all_milestones(session, ctx)
    active = [p for p in projects if p.phase != "live"]
    currency = projects[0].currency if projects else "INR"

    return OverviewOut(
        active_projects=domain.active_projects(projects),
        on_time_pct=round(domain.on_time_fraction(milestones) * 1000) / 10,
        milestones_due=domain.milestones_due(milestones, date.today()),
        utilization_pct=domain.RESOURCE_UTILIZATION_PCT,
        avg_progress_pct=domain.average_progress(active),
        total_budget_minor=domain.total_budget_minor(projects),
        total_spent_minor=domain.total_spent_minor(projects),
        currency=currency,
        health_breakdown=[HealthSlice(**slice_) for slice_ in domain.health_breakdown(projects)],
        burndown_sprint=domain.BURNDOWN_SPRINT,
        burndown_release=domain.BURNDOWN_RELEASE,
        burndown_delta_sprint=domain.BURNDOWN_DELTA_SPRINT,
        burndown_delta_release=domain.BURNDOWN_DELTA_RELEASE,
    )
