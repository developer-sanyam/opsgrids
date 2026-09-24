"""Customer HTTP surface for insights (`docs/insights/api.md`).

Fronted by `app-gateway` at `/api/v1/insights`, with **no `product` key** in the route table
(ADR-0020 §7), the same shape as `/api/v1/approvals`: this surface spans every product, and each
widget checks its own entitlement and permission inside the service
(`app/access.py:gate_*`) rather than the route declaring one gate for the whole endpoint. The
only thing every route needs from FastAPI's own dependency is an authenticated tenant principal
— `require(...)` never appears here.

Every endpoint is read-only and every widget it returns carries a `state`
(`docs/insights/api.md` §1); a caller who can see nothing still gets `200` with every widget
`"no_access"`/`"not_active"` — a denial is never the reason the whole request fails.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, domain, selectors
from app.schemas import ActivityOut, RankingsOut, SeriesOut, SummaryOut
from platform_core.auth import CurrentPrincipal
from platform_core.db import get_tenant_session
from platform_core.errors import ValidationProblem
from platform_core.logging import get_logger

log = get_logger("insights.routes")

router = APIRouter(tags=["insights"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: `docs/insights/api.md` §3: "up to 16 metric keys in one call."
MAX_SUMMARY_METRICS = 16

PeriodQuery = Annotated[
    str, Query(description="today|this_week|this_month|this_quarter|last_30_days|last_month|custom")
]
FromQuery = Annotated[date | None, Query(alias="from")]
ToQuery = Annotated[date | None, Query()]
CompareQuery = Annotated[str, Query(description="previous_period|same_period_last_year|none")]


def _parse_metric_keys(raw: str) -> list[str]:
    """1-16 comma-separated keys, every one a real metric — "an unknown key is a 422, whether or
    not the caller could see it: keys are public vocabulary, not data" (`docs/insights/api.md`
    §3)."""
    keys = [key.strip() for key in raw.split(",") if key.strip()]
    if not keys:
        raise ValidationProblem("metrics must name at least one key")
    if len(keys) > MAX_SUMMARY_METRICS:
        raise ValidationProblem(f"metrics accepts at most {MAX_SUMMARY_METRICS} keys")
    unknown = [key for key in keys if key not in access.METRICS]
    if unknown:
        raise ValidationProblem(f"unknown metric key(s): {', '.join(unknown)}")
    return keys


def _parse_products(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


@router.get(
    "/insights/summary",
    response_model=SummaryOut,
    response_model_exclude_none=True,
    summary="KPI values and their comparisons",
)
async def summary(
    session: SessionDep,
    principal: CurrentPrincipal,
    metrics: Annotated[str, Query(description="comma-separated metric keys, 1-16")],
    period: PeriodQuery = "this_month",
    from_: FromQuery = None,
    to: ToQuery = None,
    compare: CompareQuery = "previous_period",
) -> SummaryOut:
    metric_keys = _parse_metric_keys(metrics)
    try:
        return await selectors.build_summary(
            session,
            principal,
            metric_keys=metric_keys,
            period=period,
            compare=compare,
            from_=from_,
            to=to,
        )
    except domain.InvalidPeriodError as exc:
        raise ValidationProblem(str(exc)) from exc


@router.get(
    "/insights/series",
    response_model=SeriesOut,
    response_model_exclude_none=True,
    summary="One metric over time",
)
async def series(
    session: SessionDep,
    principal: CurrentPrincipal,
    metric: Annotated[str, Query(description="one metric key")],
    grain: Annotated[str, Query(description="day|week|month")],
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
    breakdown: Annotated[str | None, Query()] = None,
) -> SeriesOut:
    if metric not in access.METRICS:
        raise ValidationProblem(f"unknown metric key: {metric}")
    if access.METRICS[metric].kind == "current":
        raise ValidationProblem(f"{metric} has no series — it is a current-only metric")
    if breakdown is not None and breakdown != "source":
        raise ValidationProblem("breakdown accepts only 'source', and only for leads_created")
    try:
        return await selectors.build_series(
            session, principal, metric=metric, grain=grain, from_=from_, to=to
        )
    except domain.InvalidPeriodError as exc:
        raise ValidationProblem(str(exc)) from exc


@router.get(
    "/insights/activity",
    response_model=ActivityOut,
    response_model_exclude_none=True,
    summary="Cross-product recent activity",
)
async def activity(
    session: SessionDep,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    before: Annotated[str | None, Query()] = None,
    products: Annotated[str | None, Query()] = None,
) -> ActivityOut:
    """No fact table exists yet to union over (ADR-0020 build plan, I2-I7), so this always
    answers the honest empty page: `items: []`, no cursor. The gate is still real — a caller's
    admitted kinds are computed the same way I2+'s query will filter on them
    (`selectors.visible_activity_kinds`) — nothing here waits on a fact table to be correct.
    """
    if before is not None:
        try:
            domain.decode_cursor(before)
        except domain.InvalidCursorError as exc:
            raise ValidationProblem(str(exc)) from exc
    selectors.visible_activity_kinds(principal, _parse_products(products))
    return ActivityOut(as_of=datetime.now(UTC), items=[], next_cursor=None)


@router.get(
    "/insights/rankings",
    response_model=RankingsOut,
    response_model_exclude_none=True,
    summary="Team performance and top items",
)
async def rankings(
    session: SessionDep,
    principal: CurrentPrincipal,
    board: Annotated[str, Query(description="sales_reps|task_assignees|enquired_products")],
    period: PeriodQuery = "this_month",
    from_: FromQuery = None,
    to: ToQuery = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> RankingsOut:
    if board not in access.BOARDS:
        raise ValidationProblem(f"unknown board: {board}")
    try:
        return await selectors.build_rankings(
            session, principal, board=board, period=period, from_=from_, to=to
        )
    except domain.InvalidPeriodError as exc:
        raise ValidationProblem(str(exc)) from exc
