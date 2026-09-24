"""HTTP surface for the timesheet service — PulseBoard's weekly time grid.

Mounted behind the gateway's `/api/v1/timesheets` prefix (see
`services/app-gateway/routes.json`), so every route path here begins `/timesheets/...`. The
static segments (`/timesheets/summary`, `/timesheets/info`) are declared before any dynamic
route so FastAPI never swallows them.

The read endpoints are the priority — they are what flips the PulseBoard SPA from fixtures to
live — and each returns exactly the row/cell shape
`frontend/product-pulseboard/src/api/types.ts` declares (`TimesheetRow` / `TimesheetCell`),
inside the platform's `Page` envelope. `week` defaults to the current week's Monday, matching
the SPA's `?week=` query. Every route declares the permission it needs and is guarded by
`require_product("pulseboard")` as defence in depth behind the gateway's own entitlement gate.
Reads go through `selectors`, writes through `services`; a router never builds its own query.
Every route takes the tenant-bound session and opens no transaction of its own — the
chokepoint owns it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, Timesheet
from app.schemas import (
    ServiceInfo,
    TimesheetRowOut,
    TimesheetStatusIn,
    TimesheetSummaryOut,
    TimesheetWeekIn,
    TrendPoint,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the pulseboard scope resolver at import of the api module ties it to the product
# it serves: the process that mounts these routes is the one that answers its scope questions.
scope.register()

router = APIRouter(tags=["timesheets"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `pulseboard` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("pulseboard"))

TimesheetRead = Annotated[ScopeContext, Depends(require("pulseboard.time.timesheet.read"))]
TimesheetWrite = Annotated[ScopeContext, Depends(require("pulseboard.time.timesheet.write"))]

WeekParam = Annotated[
    date | None,
    Query(description="The week's Monday (ISO date). Defaults to the current week."),
]


def _monday(week: date | None) -> date:
    """The grid is weekly: any date normalises to its Monday, absent means this week."""
    return domain.week_monday(week if week is not None else date.today())


@router.get("/timesheets/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ summary


@router.get(
    "/timesheets/summary",
    response_model=TimesheetSummaryOut,
    summary="Time-side overview rollups for one week",
    dependencies=[Entitled],
)
async def summary(
    session: SessionDep, ctx: TimesheetRead, week: WeekParam = None
) -> TimesheetSummaryOut:
    """Entered / billable hours, snapshotted capacity and the utilization percentage for the
    week, plus the real utilization-by-week series (the queried week's own figure as its final
    point). The engagement-side numbers live on the project service's `/projects/summary`."""
    monday = _monday(week)
    history_mondays = [
        monday - timedelta(weeks=offset)
        for offset in range(domain.UTILIZATION_HISTORY_WEEKS, 0, -1)
    ]
    all_mondays = [*history_mondays, monday]

    # One query across every week the series needs — a loop calling `weeks_for` once per week
    # is exactly the N+1 CLAUDE.md forbids.
    sheets = await selectors.weeks_in_range(session, ctx, all_mondays)
    entries = await selectors.entries_by_sheet(session, ctx.organization_id, [s.id for s in sheets])
    sheets_by_week: dict[date, list[Timesheet]] = {wk: [] for wk in all_mondays}
    for sheet in sheets:
        sheets_by_week.setdefault(sheet.week_start, []).append(sheet)

    series = []
    for w in history_mondays:
        _, w_billable, w_capacity = selectors.week_totals(sheets_by_week[w], entries)
        point = domain.utilization_point(w_billable, w_capacity, has_data=bool(sheets_by_week[w]))
        series.append(TrendPoint(label=domain.week_label(w), value=point))

    this_week_sheets = sheets_by_week[monday]
    entered, billable, capacity = selectors.week_totals(this_week_sheets, entries)
    live = domain.utilization_pct(billable, capacity)
    series.append(TrendPoint(label=domain.week_label(monday), value=live))

    return TimesheetSummaryOut(
        week_start=monday,
        entered_hours=domain.hours(entered),
        billable_hours=domain.hours(billable),
        capacity_hours=domain.hours(capacity),
        utilization_pct=live,
        series=series,
    )


# ------------------------------------------------------------------ the grid


@router.get(
    "/timesheets",
    response_model=Page[TimesheetRowOut],
    summary="The weekly grid — one row per person",
    dependencies=[Entitled],
)
async def list_timesheets(
    session: SessionDep,
    ctx: TimesheetRead,
    params: PageQuery,
    week: WeekParam = None,
    sheet_status: Annotated[
        str | None, Query(alias="status", description="draft | submitted | approved")
    ] = None,
) -> Page[TimesheetRowOut]:
    monday = _monday(week)
    stmt = selectors.timesheets_stmt(ctx, week_start=monday, status=sheet_status)
    rows, total = await paginate(session, stmt, params)
    entries = await selectors.entries_by_sheet(session, ctx.organization_id, [s.id for s in rows])
    items = [selectors.row_out(sheet, entries.get(sheet.id, [])) for sheet in rows]
    return Page.build(items, total=total, params=params)


@router.put(
    "/timesheets",
    response_model=TimesheetRowOut,
    status_code=status.HTTP_200_OK,
    summary="Record (or re-record) one person's week",
    dependencies=[Entitled],
)
async def record_week(
    payload: TimesheetWeekIn, session: SessionDep, ctx: TimesheetWrite, request: Request
) -> TimesheetRowOut:
    sheet = await services.record_week_from(session, ctx, payload, request=request)
    entries = await selectors.entries_by_sheet(session, ctx.organization_id, [sheet.id])
    return selectors.row_out(sheet, entries.get(sheet.id, []))


@router.post(
    "/timesheets/status",
    response_model=TimesheetRowOut,
    summary="Submit or approve one person's week",
    dependencies=[Entitled],
)
async def set_status(
    payload: TimesheetStatusIn, session: SessionDep, ctx: TimesheetWrite, request: Request
) -> TimesheetRowOut:
    sheet = await selectors.get_week(
        session, ctx.organization_id, payload.person_ref, payload.week_start
    )
    if sheet is None:
        raise NotFoundProblem("No timesheet for that person and week")
    updated = await services.set_status(session, ctx, sheet, payload.status, request=request)
    entries = await selectors.entries_by_sheet(session, ctx.organization_id, [updated.id])
    return selectors.row_out(updated, entries.get(updated.id, []))
