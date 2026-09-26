"""HTTP surface for Engage automations (slice A1): the workflow engine's authoring and run APIs.

Mounted under `/engage/automations`. Reads go through `a1_selectors`, writes through
`a1_services`; no route builds a query or opens a transaction of its own. Permissions reuse the
existing Engage campaign catalogue rather than minting a new code: an automation is a standing
campaign, so viewing takes `engage.campaign.campaign.read`, authoring, pausing and retrying take
`.write`, and the two actions that put messages in front of customers (publish, resume) take
`.send`. Every route is also behind the `engage` entitlement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import a1_selectors, a1_services
from app.a1_models import Automation, AutomationRun
from app.a1_schemas import (
    AutomationDetail,
    AutomationIn,
    AutomationOut,
    AutomationPatch,
    AutomationStatus,
    OkOut,
    PerAutomationOut,
    ProblemOut,
    ReasonOut,
    RunDetail,
    RunOut,
    RunState,
    SeriesPoint,
    StatsOut,
    StepOut,
    TriggerOut,
    TriggersOut,
    ValidationOut,
)
from app.config import settings
from app.targets import make_resolver as make_target_resolver
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["engage-automations"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("engage"))

AutomationRead = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.read", minimum=Scope.ORG))
]
AutomationWrite = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.write", minimum=Scope.ORG))
]
#: Publishing and resuming make an automation start messaging customers: the same grant a
#: campaign send takes, not the draft-level one.
AutomationGoLive = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.send", minimum=Scope.ORG))
]


def _rate(completed: int, failed: int) -> float | None:
    finished = completed + failed
    return round(completed / finished, 4) if finished else None


def _out(row: Automation, stats: dict[uuid.UUID, tuple[int, int, int]]) -> AutomationOut:
    executions, completed, failed = stats.get(row.id, (0, 0, 0))
    return AutomationOut(
        id=row.id,
        name=row.name,
        description=row.description,
        status=row.status,
        trigger_event=row.trigger_event,
        module=row.module,
        version=row.current_version,
        executions_30d=executions,
        success_rate_30d=_rate(completed, failed),
        last_run_at=row.last_run_at,
        updated_at=row.updated_at,
    )


async def _detail(session: AsyncSession, row: Automation) -> AutomationDetail:
    now = datetime.now(UTC)
    stats = await a1_selectors.run_stats_30d(session, row.organization_id, [row.id], now=now)
    problems = await a1_services.collect_problems(session, row.organization_id, row.draft_graph)
    base = _out(row, stats)
    return AutomationDetail(
        **base.model_dump(),
        graph=row.draft_graph,
        validation=ValidationOut(
            ok=not a1_services.has_errors(problems),
            problems=[ProblemOut(**p.as_dict()) for p in problems],
        ),
    )


def _run_out(run: AutomationRun) -> RunOut:
    return RunOut(
        id=run.id,
        automation_id=run.automation_id,
        version=run.version,
        state=run.state,
        started_at=run.started_at,
        finished_at=run.finished_at,
        source_event_id=run.source_event_id,
        subject=run.subject,
        failure_code=run.failure_code,
        next_wake_at=run.next_wake_at,
    )


# ------------------------------------------------------------------ static paths first


@router.get(
    "/engage/automations/triggers",
    response_model=TriggersOut,
    summary="The events an automation can start on, with the variables each carries",
    dependencies=[Entitled],
)
async def list_triggers(ctx: AutomationRead) -> TriggersOut:
    return TriggersOut(items=[TriggerOut(**item) for item in a1_selectors.trigger_catalogue()])


@router.get(
    "/engage/automations/stats",
    response_model=StatsOut,
    summary="Execution totals, daily series, failure reasons and per-automation results",
    dependencies=[Entitled],
)
async def automation_stats(
    session: SessionDep,
    ctx: AutomationRead,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> StatsOut:
    if from_ is not None and to is not None and from_ > to:
        raise ValidationProblem("from must not be after to")
    if from_ is not None and to is not None and (to - from_).days > 366:
        raise ValidationProblem("the range is at most a year")
    today = datetime.now(UTC).date()
    last_day = to or today
    first_day = from_ or (last_day - timedelta(days=a1_selectors.WINDOW_DAYS - 1))
    start, end = a1_selectors.day_bounds(first_day, last_day)
    raw = await a1_selectors.stats(session, ctx.organization_id, start=start, end=end)
    total, completed, failed, avg = raw["totals"]
    names = await a1_selectors.names_for(session, ctx.organization_id, [r[0] for r in raw["per"]])
    return StatsOut(
        executions=int(total),
        successful=int(completed),
        failed=int(failed),
        avg_duration_ms=int(avg) if avg is not None else None,
        series=[
            SeriesPoint(date=r[0].isoformat(), successful=int(r[1]), failed=int(r[2]))
            for r in raw["series"]
        ],
        failures_by_reason=[
            ReasonOut(
                code=str(r[0] or "unknown"),
                label=a1_services.FAILURE_LABELS.get(str(r[0]), str(r[0] or "Unknown")),
                count=int(r[1]),
            )
            for r in raw["reasons"]
        ],
        per_automation=[
            PerAutomationOut(
                automation_id=r[0],
                name=names.get(r[0], "Deleted automation"),
                executions=int(r[1]),
                success_rate=_rate(int(r[2]), int(r[3])) or 0.0,
                failed=int(r[3]),
                avg_duration_ms=int(r[4]) if r[4] is not None else None,
                last_run_at=r[5],
            )
            for r in raw["per"]
        ],
    )


@router.get(
    "/engage/automations/runs/{run_id}",
    response_model=RunDetail,
    summary="One run with its step trail",
    dependencies=[Entitled],
)
async def read_run(run_id: uuid.UUID, session: SessionDep, ctx: AutomationRead) -> RunDetail:
    run = await a1_selectors.get_run(session, ctx.organization_id, run_id)
    if run is None:
        raise NotFoundProblem("No such run")
    steps = await a1_selectors.steps_for_run(session, ctx.organization_id, run.id)
    return RunDetail(
        **_run_out(run).model_dump(),
        idempotency_key=run.idempotency_key,
        steps=[
            StepOut(
                node_id=s.node_id,
                type=s.type,
                state=s.state,
                started_at=s.started_at,
                finished_at=s.finished_at,
                attempt=s.attempt,
                detail=s.detail,
                message_id=s.message_id,
            )
            for s in steps
        ],
    )


@router.post(
    "/engage/automations/runs/{run_id}/retry",
    response_model=OkOut,
    summary="Retry a failed run from the step that failed",
    dependencies=[Entitled],
)
async def retry_run(
    run_id: uuid.UUID, session: SessionDep, ctx: AutomationWrite, request: Request
) -> OkOut:
    await a1_services.retry_run(
        session,
        ctx,
        run_id=run_id,
        resolve_target=make_target_resolver(settings()),
        request=request,
    )
    return OkOut()


# ------------------------------------------------------------------ automations


@router.post(
    "/engage/automations",
    response_model=AutomationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an automation as a draft",
    dependencies=[Entitled],
)
async def create_automation(
    body: AutomationIn, session: SessionDep, ctx: AutomationWrite, request: Request
) -> AutomationDetail:
    row = await a1_services.create_automation(
        session,
        ctx,
        name=body.name,
        description=body.description,
        graph=body.graph.as_dict(),
        request=request,
    )
    return await _detail(session, row)


@router.get(
    "/engage/automations",
    response_model=Page[AutomationOut],
    summary="Automations, newest edit first",
    dependencies=[Entitled],
)
async def list_automations(
    session: SessionDep,
    ctx: AutomationRead,
    params: PageQuery,
    status_filter: Annotated[AutomationStatus | None, Query(alias="status")] = None,
    module: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[AutomationOut]:
    stmt = a1_selectors.automations_stmt(ctx, status=status_filter, module=module, q=q)
    rows, total = await paginate(session, stmt, params)
    stats = await a1_selectors.run_stats_30d(
        session, ctx.organization_id, [r.id for r in rows], now=datetime.now(UTC)
    )
    return Page.build([_out(r, stats) for r in rows], total=total, params=params)


async def _get(session: AsyncSession, ctx: ScopeContext, automation_id: uuid.UUID) -> Automation:
    row = await a1_selectors.get_automation(session, ctx.organization_id, automation_id)
    if row is None:
        raise NotFoundProblem("No such automation")
    return row


@router.get(
    "/engage/automations/{automation_id}",
    response_model=AutomationDetail,
    summary="One automation with its graph and validation",
    dependencies=[Entitled],
)
async def read_automation(
    automation_id: uuid.UUID, session: SessionDep, ctx: AutomationRead
) -> AutomationDetail:
    return await _detail(session, await _get(session, ctx, automation_id))


@router.patch(
    "/engage/automations/{automation_id}",
    response_model=AutomationDetail,
    summary="Edit a draft or paused automation",
    dependencies=[Entitled],
)
async def edit_automation(
    automation_id: uuid.UUID,
    body: AutomationPatch,
    session: SessionDep,
    ctx: AutomationWrite,
    request: Request,
) -> AutomationDetail:
    fields: dict[str, Any] = {}
    for name in body.model_fields_set:
        fields[name] = (
            body.graph.as_dict() if name == "graph" and body.graph else getattr(body, name)
        )
    row = await a1_services.update_automation(
        session, ctx, automation_id=automation_id, fields=fields, request=request
    )
    return await _detail(session, row)


@router.delete(
    "/engage/automations/{automation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a draft or paused automation",
    dependencies=[Entitled],
)
async def remove_automation(
    automation_id: uuid.UUID, session: SessionDep, ctx: AutomationWrite, request: Request
) -> Response:
    await a1_services.delete_automation(session, ctx, automation_id=automation_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/engage/automations/{automation_id}/publish",
    response_model=AutomationDetail,
    summary="Validate the draft and publish it as the next immutable version",
    dependencies=[Entitled],
)
async def publish_automation(
    automation_id: uuid.UUID, session: SessionDep, ctx: AutomationGoLive, request: Request
) -> AutomationDetail:
    row = await a1_services.publish(session, ctx, automation_id=automation_id, request=request)
    return await _detail(session, row)


@router.post(
    "/engage/automations/{automation_id}/pause",
    response_model=AutomationDetail,
    summary="Stop starting new runs (runs in flight finish)",
    dependencies=[Entitled],
)
async def pause_automation(
    automation_id: uuid.UUID, session: SessionDep, ctx: AutomationWrite, request: Request
) -> AutomationDetail:
    row = await a1_services.set_status(
        session, ctx, automation_id=automation_id, target="paused", request=request
    )
    return await _detail(session, row)


@router.post(
    "/engage/automations/{automation_id}/resume",
    response_model=AutomationDetail,
    summary="Resume a paused automation on its last published version",
    dependencies=[Entitled],
)
async def resume_automation(
    automation_id: uuid.UUID, session: SessionDep, ctx: AutomationGoLive, request: Request
) -> AutomationDetail:
    row = await a1_services.set_status(
        session, ctx, automation_id=automation_id, target="active", request=request
    )
    return await _detail(session, row)


@router.get(
    "/engage/automations/{automation_id}/runs",
    response_model=Page[RunOut],
    summary="An automation's runs, newest first",
    dependencies=[Entitled],
)
async def list_runs(
    automation_id: uuid.UUID,
    session: SessionDep,
    ctx: AutomationRead,
    params: PageQuery,
    state: Annotated[RunState | None, Query()] = None,
) -> Page[RunOut]:
    await _get(session, ctx, automation_id)
    stmt = a1_selectors.runs_stmt(ctx, automation_id=automation_id, state=state)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_run_out(r) for r in rows], total=total, params=params)
