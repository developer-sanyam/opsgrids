"""HTTP surface for the deal service — the CRM pipeline.

Mounted behind the gateway's `/api/v1/deals` prefix, so every route path here begins
`/deals/...`. The read endpoints are what flips the CRM SPA from fixtures to live, and
each returns exactly the shape `frontend/product-crm/src/api/types.ts` declares (the
SPA's `/activities` call maps onto `/deals/activities` — the timeline lives with the
pipeline, and `/api/v1/deals` is the prefix already routed to this service).

Every route declares the permission it needs and is guarded by `require_product("crm")`
as defence in depth behind the gateway's own entitlement gate. Reads go through
`selectors`; rollups through `domain`; a router never builds its own query. Every route
takes the tenant-bound session and opens no transaction of its own — the chokepoint owns
it.

Object-level scope is re-applied on the detail route: filtering the list and forgetting
the detail is the named vulnerability, so a caller who can only list their own deals is
refused another's by id.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.pipeline import Outcome
from app.schemas import (
    ActivityIn,
    ActivityOut,
    DealIn,
    DealOut,
    DealPatch,
    ServiceInfo,
    StageMoveIn,
    SummaryOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the CRM scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers CRM scope questions.
scope.register()

router = APIRouter(tags=["deal"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's
#: organization before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `crm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("crm"))

DealRead = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.read"))]
DealWrite = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.write"))]
DealDelete = Annotated[ScopeContext, Depends(require("crm.pipeline.deal.delete"))]
ActivityRead = Annotated[ScopeContext, Depends(require("crm.pipeline.activity.read"))]
ActivityWrite = Annotated[ScopeContext, Depends(require("crm.pipeline.activity.write"))]


@router.get("/deal/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ deals

# NOTE: static paths (`/deals/summary`, `/deals/activities`) are declared before the
# dynamic detail route; Starlette matches in declaration order, so neither ever falls
# into `{deal_id}`. `POST /deals/activities` lives beside its `GET` for the same reason.


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
    outcome: Annotated[
        Outcome | None,
        Query(description="Select by what the stage means: open, won or lost — never by key"),
    ] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    company_id: Annotated[uuid.UUID | None, Query(description="One account's deals only")] = None,
) -> Page[DealOut]:
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    stmt = selectors.deals_stmt(
        ctx,
        q=q,
        stage=stage,
        outcome=outcome,
        owner_id=owner_id,
        company_id=company_id,
        spec=spec,
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.deal_out(d, spec) for d in rows], total=total, params=params)


@router.post(
    "/deals",
    response_model=DealOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a deal",
    dependencies=[Entitled],
)
async def create_deal(
    payload: DealIn, session: SessionDep, ctx: DealWrite, request: Request
) -> DealOut:
    """`owner_id` defaults to the calling rep. Naming a different one is an ORG-scoped
    act: `assert_can_touch` re-applies the caller's scope to the named owner exactly as
    the detail routes re-apply it to a named row, so an OWN-scoped rep cannot hand a new
    deal to a colleague any more than they could edit that colleague's existing one."""
    owner_id = payload.owner_id if payload.owner_id is not None else ctx.subject_ref
    if owner_id is None:
        raise ValidationProblem("No owner could be determined for this deal")
    ctx.assert_can_touch(owner_id=owner_id)
    deal = await services.create_deal(
        session,
        ctx,
        name=payload.name,
        company_id=payload.company_id,
        owner_id=owner_id,
        value_minor=payload.value_minor,
        close_date=payload.close_date,
        currency=payload.currency,
        stage=payload.stage,
        probability=payload.probability,
        primary_contact_id=payload.primary_contact_id,
        source=payload.source,
        request=request,
    )
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    return selectors.deal_out(deal, spec)


@router.get(
    "/deals/summary",
    response_model=SummaryOut,
    summary="Pipeline summary — the Overview rollups",
    dependencies=[Entitled],
)
async def summary(
    session: SessionDep,
    ctx: DealRead,
    owner_id: Annotated[
        uuid.UUID | None, Query(description="Narrow the rollup to one rep's own pipeline")
    ] = None,
    owner: Annotated[
        Literal["me"] | None,
        Query(description="`me` narrows to the caller's own pipeline, resolved server-side"),
    ] = None,
) -> SummaryOut:
    """The same arithmetic the SPA's `data/derive.ts` runs over the deal list, computed
    server-side over the caller's scope: an OWN-scoped rep gets their own numbers.

    `owner_id`/`owner=me` narrow *further*, the way `/deals`'s own `owner_id` filter does — both
    compose with `ctx.apply`'s scope filter rather than replacing it, so an OWN-scoped caller who
    names someone else's id still gets nothing back, never that colleague's numbers. `owner=me`
    resolves the caller's own id from the token (`ctx.subject_ref`) rather than trusting a
    caller-chosen id for "mine"; a caller with no `subject_ref` gets the all-zero rollup, never
    the whole book.
    """
    resolved_owner_id = owner_id
    if owner == "me":
        if ctx.subject_ref is None:
            return SummaryOut(
                pipeline_value_minor=0,
                weighted_pipeline_minor=0,
                open_deals=0,
                win_rate_pct=0,
                won_value_minor=0,
                activities_this_week=0,
                currency="INR",
                trend=None,
                funnel=[],
            )
        resolved_owner_id = ctx.subject_ref

    deals = await selectors.all_deals(session, ctx, owner_id=resolved_owner_id)
    activities = await selectors.all_activities(session, ctx, owner_id=resolved_owner_id)
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
        trend=None,
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


@router.post(
    "/deals/activities",
    response_model=ActivityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Log a call, email, meeting, note or task",
    dependencies=[Entitled],
)
async def log_activity(
    payload: ActivityIn, session: SessionDep, ctx: ActivityWrite, request: Request
) -> ActivityOut:
    """Append one entry to the timeline.

    An OWN-scoped caller can only ever log against themselves: `owner_id` is forced to
    `ctx.subject_ref` regardless of what the payload says. Only an ORG-scoped caller may
    attribute the entry to a different rep, and only if they name one. When `deal_id` is
    given, the object-level check re-applies the caller's scope to that deal — logging
    against a deal you cannot touch is refused exactly like reading or moving it would be
    — and the entry's `company_id` is taken from the deal, overriding anything the body
    named: an entry cannot claim two accounts.
    """
    if ctx.scope is Scope.OWN:
        owner_id = ctx.subject_ref
    else:
        owner_id = payload.owner_id if payload.owner_id is not None else ctx.subject_ref
    if owner_id is None:
        raise ValidationProblem("No owner could be determined for this activity")

    #: The company is the deal's, not the body's — an entry cannot name two accounts.
    company_id = payload.company_id
    if payload.deal_id is not None:
        deal = await selectors.get_deal(session, ctx.organization_id, payload.deal_id)
        if deal is None:
            raise NotFoundProblem("That deal is not part of this organization")
        ctx.assert_can_touch(owner_id=deal.owner_id)
        company_id = deal.company_id

    activity = await services.log_activity(
        session,
        ctx,
        activity_type=payload.type,
        subject=payload.subject,
        owner_id=owner_id,
        body=payload.body,
        deal_id=payload.deal_id,
        company_id=company_id,
        contact_id=payload.contact_id,
        direction=payload.direction,
        occurred_at=payload.occurred_at,
        request=request,
    )
    return selectors.activity_out(activity)


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
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    return selectors.deal_out(deal, spec)


@router.patch(
    "/deals/{deal_id}",
    response_model=DealOut,
    summary="Update a deal",
    dependencies=[Entitled],
)
async def update_deal(
    deal_id: uuid.UUID,
    payload: DealPatch,
    session: SessionDep,
    ctx: DealWrite,
    request: Request,
) -> DealOut:
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    updated = await services.update_deal(session, ctx, deal, payload, request=request)
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    return selectors.deal_out(updated, spec)


@router.delete(
    "/deals/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a deal",
    dependencies=[Entitled],
)
async def delete_deal(
    deal_id: uuid.UUID, session: SessionDep, ctx: DealDelete, request: Request
) -> Response:
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    await services.delete_deal(session, ctx, deal, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/deals/{deal_id}/stage",
    response_model=DealOut,
    summary="Move a deal through the pipeline",
    dependencies=[Entitled],
)
async def move_deal_stage(
    deal_id: uuid.UUID,
    payload: StageMoveIn,
    session: SessionDep,
    ctx: DealWrite,
    request: Request,
) -> DealOut:
    deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
    if deal is None:
        raise NotFoundProblem("That deal is not part of this organization")
    ctx.assert_can_touch(owner_id=deal.owner_id)
    updated = await services.move_stage(
        session,
        ctx,
        deal,
        new_stage=payload.stage,
        probability=payload.probability,
        request=request,
    )
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    return selectors.deal_out(updated, spec)
