"""HTTP surface for messaging analytics and the cost rate card (slice M1).

Mounted behind the gateway's `/api/v1/engage` prefix, so every path here begins
`/engage/analytics/...`. Every route resolves the actor, binds the tenant through the same
`get_tenant_session` chokepoint the rest of Engage uses, and checks a permission floored at ORG
before it touches data. Reads go through `app.m1_analytics`; no route builds a query.

Permissions are **reused**, not new (a new code needs a gated production role reconcile):

  * the three read routes take `engage.campaign.campaign.read` — delivery statistics, which
    every Engage viewer already holds;
  * overriding the rate card takes `engage.audience.contact.write` — the write-level grant an
    administrator holds and a viewer does not.

The date range is two inclusive **UTC** dates, `?from=YYYY-MM-DD&to=YYYY-MM-DD`; omitted, it is the
last 30 days ending today.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import m1_analytics
from app.m1_cost import DateWindow, RangeError, resolve_window
from app.m1_schemas import HubOverview, RateCardIn, RateCardOut, WhatsAppOverview
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import ValidationProblem

router = APIRouter(tags=["engage-analytics"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("engage"))

AnalyticsRead = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.read", minimum=Scope.ORG))
]
RateCardWrite = Annotated[
    ScopeContext, Depends(require("engage.audience.contact.write", minimum=Scope.ORG))
]

#: `from` is a Python keyword, so the parameter is named `date_from` and aliased on the wire.
DateFrom = Annotated[
    date | None,
    Query(alias="from", description="First UTC day, inclusive (YYYY-MM-DD)."),
]
DateTo = Annotated[
    date | None,
    Query(alias="to", description="Last UTC day, inclusive (YYYY-MM-DD)."),
]


def _window(date_from: date | None, date_to: date | None) -> DateWindow:
    try:
        return resolve_window(date_from, date_to)
    except RangeError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": "from", "code": exc.code, "message": str(exc)}]
        ) from exc


@router.get(
    "/engage/analytics/whatsapp/overview",
    response_model=WhatsAppOverview,
    summary="WhatsApp delivery, replies, categories, failures and an estimated cost",
    dependencies=[Entitled],
)
async def whatsapp_overview(
    session: SessionDep,
    ctx: AnalyticsRead,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> WhatsAppOverview:
    window = _window(date_from, date_to)
    return await m1_analytics.whatsapp_overview(session, ctx.organization_id, window)


@router.get(
    "/engage/analytics/overview",
    response_model=HubOverview,
    summary="The communication hub: both channels' numbers and one estimated cost",
    dependencies=[Entitled],
)
async def hub_overview(
    session: SessionDep,
    ctx: AnalyticsRead,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> HubOverview:
    window = _window(date_from, date_to)
    return await m1_analytics.hub_overview(session, ctx.organization_id, window)


@router.get(
    "/engage/analytics/rate-card",
    response_model=RateCardOut,
    summary="The per-1000-message rates behind the cost estimate",
    dependencies=[Entitled],
)
async def read_rate_card(session: SessionDep, ctx: AnalyticsRead) -> RateCardOut:
    today = datetime.now(UTC).date()
    return await m1_analytics.read_rate_card(session, ctx.organization_id, as_of=today)


@router.put(
    "/engage/analytics/rate-card",
    response_model=RateCardOut,
    summary="Override the rate card with the organization's real rates",
    dependencies=[Entitled],
)
async def override_rate_card(
    body: RateCardIn, session: SessionDep, ctx: RateCardWrite, request: Request
) -> RateCardOut:
    today = datetime.now(UTC).date()
    effective_from = body.effective_from or today
    await m1_analytics.override_rate_card(
        session,
        ctx,
        rates={r.category: (r.amount_minor_per_1000_messages, r.currency) for r in body.rates},
        effective_from=effective_from,
        request=request,
    )
    return await m1_analytics.read_rate_card(session, ctx.organization_id, as_of=today)
