"""HTTP surface for the marketing service.

The customer-facing routes sit under the `/api/v1/marketing` gateway prefix and mirror the
marketing SPA's client exactly (`frontend/product-marketing/src/api/client.ts`), so flipping the
UI from fixtures to live data is a flag flip:

    GET  /marketing/campaigns                     → Page[CampaignOut]
    GET  /marketing/campaigns/{id}                → CampaignOut
    GET  /marketing/campaigns/{id}/performance    → list[RevenueSpendPoint]  (weekly)
    GET  /marketing/audiences                     → Page[AudienceOut]
    GET  /marketing/analytics/revenue-vs-spend    → list[RevenueSpendPoint]  (monthly)
    GET  /marketing/overview                      → OverviewSummary          (server rollup)
    GET  /marketing/channels                      → list[ChannelPerformance] (server rollup)
    POST /marketing/campaigns                      → CampaignOut              (write)
    POST /marketing/audiences                      → AudienceOut              (write)

The SPA derives the Overview KPIs and the Channels table from the campaign list; `overview` and
`channels` expose the same rollups server-side (`app.domain`) so a later wiring pass can choose
either. Reads go through `selectors`, writes through `services`. Every route declares its
permission and is gated by `require_product("marketing")` — defence in depth behind the gateway's
own entitlement check. Every route takes the tenant-bound session and opens no transaction of its
own; the chokepoint owns it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, Audience, Campaign
from app.schemas import (
    AudienceIn,
    AudienceOut,
    CampaignIn,
    CampaignOut,
    ChannelPerformance,
    OverviewSummary,
    RevenueSpendPoint,
    ServiceInfo,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the marketing scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers marketing scope questions.
scope.register()

router = APIRouter(tags=["marketing"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `marketing` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("marketing"))

# --- read / write contexts ----------------------------------------------------------
Reader = Annotated[ScopeContext, Depends(require("marketing.analytics.campaign.read"))]
#: Writing has no self-service reading, so the floor is ORG — every seat holds the read grant, and
#: without the floor a write route would be reachable by anyone entitled to the product.
Writer = Annotated[
    ScopeContext, Depends(require("marketing.analytics.campaign.write", minimum=Scope.ORG))
]


def _audience_out(row: Audience) -> AudienceOut:
    """Project a stored audience to its wire shape — `created_at` is the definition date the UI
    shows, taken from `defined_on`, not the row's own write timestamp."""
    return AudienceOut(
        id=row.id,
        name=row.name,
        size=row.size,
        criteria=row.criteria,
        created_at=row.defined_on,
    )


def _amounts(c: Campaign) -> domain.CampaignAmounts:
    return domain.CampaignAmounts(
        channel=c.channel,
        spent_minor=c.spent_minor,
        revenue_minor=c.revenue_minor,
        pipeline_minor=c.pipeline_minor,
        leads=c.leads,
        conversions=c.conversions,
    )


def _channel_out(r: domain.ChannelRollup) -> ChannelPerformance:
    return ChannelPerformance(
        channel=r.channel,
        campaigns=r.campaigns,
        spend_minor=r.spend_minor,
        revenue_minor=r.revenue_minor,
        pipeline_minor=r.pipeline_minor,
        leads=r.leads,
        conversions=r.conversions,
        roas=r.roas,
        cac_minor=r.cac_minor,
        cpl_minor=r.cpl_minor,
    )


@router.get("/marketing/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# --------------------------------------------------------------------- campaigns


@router.get(
    "/marketing/campaigns",
    response_model=Page[CampaignOut],
    summary="Campaigns for the organization",
    dependencies=[Entitled],
)
async def list_campaigns(
    session: SessionDep,
    ctx: Reader,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name, goal or owner")] = None,
    channel: Annotated[str | None, Query()] = None,
    campaign_status: Annotated[str | None, Query(alias="status")] = None,
    audience_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[CampaignOut]:
    stmt = selectors.campaigns_stmt(
        ctx, q=q, channel=channel, status=campaign_status, audience_id=audience_id
    )
    rows, total = await paginate(session, stmt, params)
    items = [CampaignOut.model_validate(c) for c in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/marketing/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign",
    dependencies=[Entitled],
)
async def create_campaign(
    payload: CampaignIn, session: SessionDep, ctx: Writer, request: Request
) -> CampaignOut:
    campaign = await services.create_campaign(session, ctx, payload, request=request)
    return CampaignOut.model_validate(campaign)


@router.get(
    "/marketing/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="One campaign",
    dependencies=[Entitled],
)
async def read_campaign(campaign_id: uuid.UUID, session: SessionDep, ctx: Reader) -> CampaignOut:
    campaign = await selectors.get_campaign(session, ctx.organization_id, campaign_id)
    if campaign is None:
        raise NotFoundProblem("That campaign is not part of this organization")
    # Filtering the list and forgetting the detail is the named vulnerability. Re-apply the
    # caller's scope to the single row they named.
    ctx.assert_can_touch(owner_id=campaign.id)
    return CampaignOut.model_validate(campaign)


@router.get(
    "/marketing/campaigns/{campaign_id}/performance",
    response_model=list[RevenueSpendPoint],
    summary="A campaign's weekly revenue-and-spend series",
    dependencies=[Entitled],
)
async def campaign_performance(
    campaign_id: uuid.UUID, session: SessionDep, ctx: Reader
) -> list[RevenueSpendPoint]:
    campaign = await selectors.get_campaign(session, ctx.organization_id, campaign_id)
    if campaign is None:
        raise NotFoundProblem("That campaign is not part of this organization")
    ctx.assert_can_touch(owner_id=campaign.id)
    weekly = domain.campaign_weekly(campaign.revenue_minor, campaign.spent_minor)
    return [
        RevenueSpendPoint(label=w.label, revenue_minor=w.revenue_minor, spend_minor=w.spend_minor)
        for w in weekly
    ]


# --------------------------------------------------------------------- audiences


@router.get(
    "/marketing/audiences",
    response_model=Page[AudienceOut],
    summary="Saved audience segments",
    dependencies=[Entitled],
)
async def list_audiences(
    session: SessionDep,
    ctx: Reader,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Name or criteria")] = None,
) -> Page[AudienceOut]:
    stmt = selectors.audiences_stmt(ctx, q=q)
    rows, total = await paginate(session, stmt, params)
    items = [_audience_out(a) for a in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/marketing/audiences",
    response_model=AudienceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an audience segment",
    dependencies=[Entitled],
)
async def create_audience(
    payload: AudienceIn, session: SessionDep, ctx: Writer, request: Request
) -> AudienceOut:
    audience = await services.create_audience(session, ctx, payload, request=request)
    return _audience_out(audience)


# --------------------------------------------------------------------- analytics


@router.get(
    "/marketing/analytics/revenue-vs-spend",
    response_model=list[RevenueSpendPoint],
    summary="The monthly revenue-vs-spend series",
    dependencies=[Entitled],
)
async def revenue_vs_spend(session: SessionDep, ctx: Reader) -> list[RevenueSpendPoint]:
    points = await selectors.revenue_series(session, ctx.organization_id)
    return [
        RevenueSpendPoint(label=p.label, revenue_minor=p.revenue_minor, spend_minor=p.spend_minor)
        for p in points
    ]


@router.get(
    "/marketing/channels",
    response_model=list[ChannelPerformance],
    summary="Per-channel performance, rolled up from the campaigns",
    dependencies=[Entitled],
)
async def channels(session: SessionDep, ctx: Reader) -> list[ChannelPerformance]:
    campaigns = await selectors.list_campaigns(ctx, session)
    rollups = domain.channel_performance([_amounts(c) for c in campaigns])
    return [_channel_out(r) for r in rollups]


@router.get(
    "/marketing/overview",
    response_model=OverviewSummary,
    summary="The Overview screen in one payload — KPIs, the series, and the channel breakdown",
    dependencies=[Entitled],
)
async def overview(session: SessionDep, ctx: Reader) -> OverviewSummary:
    campaigns = await selectors.list_campaigns(ctx, session)
    amounts = [_amounts(c) for c in campaigns]
    totals = domain.overview_totals(amounts)
    rollups = domain.channel_performance(amounts)
    series = await selectors.revenue_series(session, ctx.organization_id)
    # The currency is uniform across the org's campaigns; fall back to INR when there are none.
    currency = campaigns[0].currency if campaigns else "INR"
    return OverviewSummary(
        currency=currency,
        revenue_minor=totals.revenue_minor,
        spend_minor=totals.spend_minor,
        pipeline_minor=totals.pipeline_minor,
        leads=totals.leads,
        conversions=totals.conversions,
        roas=totals.roas,
        cpl_minor=totals.cpl_minor,
        revenue_vs_spend=[
            RevenueSpendPoint(
                label=p.label, revenue_minor=p.revenue_minor, spend_minor=p.spend_minor
            )
            for p in series
        ],
        channels=[_channel_out(r) for r in rollups],
    )
