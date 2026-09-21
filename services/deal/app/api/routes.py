"""HTTP surface for the deal service — the CRM pipeline.

Mounted behind the gateway's `/api/v1/deals` prefix, so every route path here begins
`/deals/...`. The read endpoints are what flips the CRM SPA from fixtures to live, and
each returns exactly the shape `frontend/product-crm/src/api/types.ts` declares (the
SPA's `/activities` call maps onto `/deals/activities` — the timeline lives with the
pipeline, and `/api/v1/deals` is the prefix already routed to this service).

Every route declares the permission it needs and is guarded by `require_product("crm")`
as defence in depth behind the gateway's own entitlement gate. Reads go through
`selectors`, writes through `services`; rollups through `domain`; a router never builds
its own query and never holds a business rule. Every route takes the tenant-bound session
and opens no transaction of its own — the chokepoint owns it, so a change, its audit row
and its outbox event commit together or not at all.

Object-level scope is re-applied on every route that names a single deal: filtering the
list and forgetting the detail is the named vulnerability, so a caller who can only list
their own deals is refused another's by id — to read it, to move it, or to log against it.

A stage move is a transition rather than a field edit, so it is its own endpoint
(`POST /deals/{deal_id}/stage`) and the legal moves stay `domain.can_transition`'s
business rather than a PATCH body's.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    ActivityIn,
    ActivityOut,
    DealIn,
    DealOut,
    ServiceInfo,
    StageIn,
    SummaryOut,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the CRM scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers CRM scope questions.
scope.register()

router = APIRouter(tags=["deal"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's
#: organization before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `crm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("crm"))

DealRead = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.read"))]
DealWrite = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.write"))]
ActivityRead = Annotated[ScopeContext, Depends(require("crm.pipeline.activity.read"))]
ActivityWrite = Annotated[ScopeContext, Depends(require("crm.pipeline.activity.write"))]


def _owner_for(ctx: ScopeContext, requested: uuid.UUID | None) -> uuid.UUID:
    """Whose row this is — the OWN-scope column, so writing it is an authorization
    question, not a field assignment.

    Omitted, the row belongs to the calling rep. Naming another rep goes through
    `assert_can_touch`: an ORG-scoped manager may assign and may backfill on a rep's
    behalf, an OWN-scoped rep may only act for themselves. A caller with no rep identity
    at all has nobody to attribute the row to, which is a 422 rather than a row owned by
    nothing.
    """
    owner_id = requested if requested is not None else ctx.subject_ref
    if owner_id is None:
        raise ValidationProblem("No owner to attribute this to — name an owner_id")
    ctx.assert_can_touch(owner_id=owner_id)
    return owner_id


@router.get("/deal/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ deals

# NOTE: static paths (`/deals/summary`, `/deals/activities`) are declared before the
# dynamic detail route; Starlette matches in declaration order, so neither ever falls
# into `{deal_id}`.


@router.get(
    "/deals",
    response_model=Page[DealOut],
    summary="Deals in the pipeline",
    dependencies=[Entitled],
)
async def list_deals(
    session: SessionDep,
    ctx: DealRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Deal name")] = None,
    stage: Annotated[str | None, Query()] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[DealOut]:
    stmt = selectors.deals_stmt(ctx, q=q, stage=stage, owner_id=owner_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.deal_out(d) for d in rows], total=total, params=params)


@router.post(
    "/deals",
    response_model=DealOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a deal",
    dependencies=[Entitled],
)
async def create_deal(
    payload: DealIn, session: SessionDep, ctx: DealWrite, request: Request
) -> DealOut:
    """`request` is passed through because the audit row records the actor and the origin
    of the call, not merely that a deal appeared."""
    deal = await services.create_deal(
        session,
        ctx,
        name=payload.name,
        company_id=payload.company_id,
        owner_id=_owner_for(ctx, payload.owner_id),
        value_minor=payload.value_minor,
        close_date=payload.close_date,
        currency=payload.currency,
        stage=payload.stage,
        probability=payload.probability,
        primary_contact_id=payload.primary_contact_id,
        source=payload.source,
        request=request,
    )
    return selectors.deal_out(deal)


@router.get(
    "/deals/summary",
    response_model=SummaryOut,
    summary="Pipeline summary — the Overview rollups",
    dependencies=[Entitled],
)
async def summary(session: SessionDep, ctx: DealRead) -> SummaryOut:
    """The same arithmetic the SPA's `data/derive.ts` runs over the deal list, computed
    server-side over the caller's scope: an OWN-scoped rep gets their own numbers."""
    deals = await selectors.all_deals(session, ctx)
    activities = await selectors.all_activities(session, ctx)
    open_value = domain.pipeline_value_minor(deals)
    currency = deals[0].currency if deals else "INR"
    return SummaryOut(
        pipeline_value_minor=open_value,
        weighted_pipeline_minor=domain.weighted_pipeline_minor(deals),
        open_deals=domain.open_count(deals),
        win_rate_pct=domain.win_rate_pct(deals),
        won_value_minor=domain.won_value_minor(deals),
        activities_this_week=domain.activities_this_week(activities),
        currency=currency,
        trend=domain.pipeline_trend(open_value),
        funnel=domain.stage_funnel(deals),
    )


@router.get(
    "/deals/activities",
    response_model=Page[ActivityOut],
    summary="The activity timeline",
    dependencies=[Entitled],
)
async def list_activities(
    session: SessionDep,
    ctx: ActivityRead,
    params: PageQuery,
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    activity_type: Annotated[str | None, Query(alias="type")] = None,
) -> Page[ActivityOut]:
    stmt = selectors.activities_stmt(
        ctx,
        deal_id=deal_id,
        company_id=company_id,
        contact_id=contact_id,
        activity_type=activity_type,
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.activity_out(a) for a in rows], total=total, params=params)


@router.get(
    "/deals/{deal_id}",
    response_model=DealOut,
    summary="One deal",
    dependencies=[Entitled],
)
async def read_deal(deal_id: uuid.UUID, session: SessionDep, ctx: DealRead) -> DealOut:
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    return selectors.deal_out(deal)


@router.post(
    "/deals/{deal_id}/stage",
    response_model=DealOut,
    summary="Move a deal to another stage",
    dependencies=[Entitled],
)
async def move_stage(
    deal_id: uuid.UUID,
    payload: StageIn,
    session: SessionDep,
    ctx: DealWrite,
    request: Request,
) -> DealOut:
    """A transition, not a field edit: `services.move_stage` is `domain.can_transition`'s
    enforcement point, so a closed deal is refused (409) rather than quietly reopened."""
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    await services.move_stage(
        session,
        ctx,
        deal,
        new_stage=payload.stage,
        probability=payload.probability,
        request=request,
    )
    return selectors.deal_out(deal)


# ------------------------------------------------------------------ activities


@router.post(
    "/deals/{deal_id}/activities",
    response_model=ActivityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Log a touch against a deal (append-only)",
    dependencies=[Entitled],
)
async def log_activity(
    deal_id: uuid.UUID,
    payload: ActivityIn,
    session: SessionDep,
    ctx: ActivityWrite,
    request: Request,
) -> ActivityOut:
    """The timeline is append-only — this adds an entry and there is deliberately no route
    that edits or removes one. The company is the deal's, rather than whatever the body
    claimed, so an entry cannot name a deal and a company that disagree.
    """
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    activity = await services.log_activity(
        session,
        ctx,
        activity_type=payload.type,
        subject=payload.subject,
        owner_id=_owner_for(ctx, payload.owner_id),
        body=payload.body,
        deal_id=deal.id,
        company_id=deal.company_id,
        contact_id=payload.contact_id,
        direction=payload.direction,
        occurred_at=payload.occurred_at,
        request=request,
    )
    return selectors.activity_out(activity)
