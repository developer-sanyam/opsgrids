"""Customer HTTP surface for subscription — the lifecycle a customer drives themselves.

Fronted by the product gateway at `/api/v1/subscriptions`. Subscribing is a **platform** act
that creates a financial commitment, so these routes reuse the platform vocabulary rather than
inventing a new one (the same choice catalog made in reusing `core.product.catalogue.read`):

* `core.product.activation.write` — "Activate and deactivate products", granted to the org owner
  only because it is a spend decision — guards subscribe, cancel and change-plan;
* `core.product.activation.read` — "See which products are active" — guards the reads.

Both are floored at `ORG`: a subscription is an account-level resource, not one user's, so a
narrower grant has no business managing it. Every route runs through `get_tenant_session`, the
tenancy chokepoint, and every list carries a negative-authz test (ADR-0003 §3).

The plan reads behind the Subscriptions page (ADR-0034) follow the same split: the per-row plan
line is a read (`activation.read`), the Change plan picker and its preview are part of changing a
plan (`activation.write`). Neither shows a list price; an amount appears only for the
organization's own negotiated price, to a caller who may read its invoices (`app/plan_options.py`).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, plan_options, selectors, services
from app.catalog import PlanCatalog
from app.config import settings
from app.models import SCHEMA, DunningCycle, Subscription
from app.schemas import (
    CancelIn,
    ChangePlanIn,
    ChangePlanPreviewIn,
    CustomerChangePlanPreviewOut,
    DunningOut,
    PlanOptionsOut,
    ServiceInfo,
    SubscribeIn,
    SubscriptionOut,
    SubscriptionPlanSummaryOut,
)
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session

router = APIRouter(tags=["subscription"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
ManageCtx = Annotated[
    ScopeContext, Depends(require("core.product.activation.write", minimum=Scope.ORG))
]
ReadCtx = Annotated[
    ScopeContext, Depends(require("core.product.activation.read", minimum=Scope.ORG))
]


def get_catalog(request: Request) -> PlanCatalog:
    """The catalog pin-lookup client, assembled once in the app lifespan.

    A dependency so a test can override it with a fake and never make a real HTTP call — the same
    seam auth-service uses for its entitlement client.
    """
    return request.app.state.catalog  # type: ignore[no-any-return]


CatalogDep = Annotated[PlanCatalog, Depends(get_catalog)]


def _dunning_out(cycle: DunningCycle | None) -> DunningOut | None:
    """The open grace window, or nothing. `max_attempts` comes from the policy rather than the
    row: the ladder's length is a platform decision (ADR-0005), not a per-customer one."""
    if cycle is None:
        return None
    return DunningOut(
        cycle_id=cycle.id,
        opened_at=cycle.opened_at,
        grace_ends_at=cycle.grace_ends_at,
        next_attempt_at=cycle.next_action_at,
        attempts_made=cycle.attempts_made,
        max_attempts=domain.POLICY.max_attempts,
        amount_minor=cycle.amount_minor,
        currency=cycle.currency,
    )


def _out(subscription: Subscription, cycle: DunningCycle | None = None) -> SubscriptionOut:
    """One subscription with its grace window attached, if it has one.

    Built here rather than through a relationship because a read must not be able to trigger a
    lazy load — async SQLAlchemy forbids one, and the routes that write hold a subscription whose
    collections were populated for exactly what they needed. Those return no window, correctly:
    a subscribe or change-plan cannot have one, and a cancel has just closed whatever there was.
    """
    out = SubscriptionOut.model_validate(subscription)
    out.dunning = _dunning_out(cycle)
    return out


@router.get("/subscription/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a subscription (trial or paid), pinning a catalog plan version",
)
async def create_subscription(
    payload: SubscribeIn,
    session: SessionDep,
    ctx: ManageCtx,
    catalog: CatalogDep,
    request: Request,
) -> SubscriptionOut:
    subscription = await services.subscribe(session, ctx, catalog, payload, request=request)
    return SubscriptionOut.model_validate(subscription)


@router.get(
    "/subscriptions",
    response_model=list[SubscriptionOut],
    summary="This organization's subscriptions",
)
async def list_subscriptions(session: SessionDep, ctx: ReadCtx) -> list[SubscriptionOut]:
    subscriptions = await selectors.list_subscriptions(session, ctx)
    cycles = await selectors.open_cycles_by_subscription(session, [s.id for s in subscriptions])
    return [_out(s, cycles.get(s.id)) for s in subscriptions]


# Registered before `/subscriptions/{subscription_id}`: that route's path parameter is a UUID, and
# FastAPI matches in order, so this literal segment would otherwise be read as a malformed id.
@router.get(
    "/subscriptions/plan-summaries",
    response_model=list[SubscriptionPlanSummaryOut],
    summary="The plan each of this organization's subscriptions is on, and any scheduled change",
)
async def list_plan_summaries(
    session: SessionDep, ctx: ReadCtx, catalog: CatalogDep
) -> list[SubscriptionPlanSummaryOut]:
    """One row per subscription `GET /subscriptions` returns — the same scoped selector — with its
    plan's name, cadence and version, the plan a scheduled change moves it to, and when. An amount
    only for this organization's own negotiated price, and only to a caller who may read its
    invoices; never a list price. 503 `catalog_unavailable` when catalog cannot be read: the page
    still has `GET /subscriptions` and renders without the plan line."""
    subscriptions = await selectors.list_subscriptions(session, ctx)
    return await plan_options.plan_summaries(
        session,
        organization_id=ctx.organization_id,
        principal=ctx.principal,
        catalog=catalog,
        subscriptions=subscriptions,
    )


@router.get(
    "/subscriptions/{subscription_id}",
    response_model=SubscriptionOut,
    summary="One subscription",
)
async def get_subscription(
    subscription_id: uuid.UUID, session: SessionDep, ctx: ReadCtx
) -> SubscriptionOut:
    subscription = await services.require_subscription(session, subscription_id)
    ctx.assert_can_touch(owner_id=None)
    return _out(subscription, await selectors.get_open_cycle(session, subscription.id))


@router.post(
    "/subscriptions/{subscription_id}/cancel",
    response_model=SubscriptionOut,
    summary="Cancel a subscription (immediate)",
)
async def cancel_subscription(
    subscription_id: uuid.UUID,
    payload: CancelIn,
    session: SessionDep,
    ctx: ManageCtx,
    request: Request,
) -> SubscriptionOut:
    """Immediate and terminal. A change scheduled for the next period is cleared with it (an
    audit row says so): a cancelled subscription never reaches that period."""
    subscription = await services.require_subscription(
        session, subscription_id, organization_id=ctx.organization_id
    )
    ctx.assert_can_touch(owner_id=None)
    subscription = await services.cancel(
        session, ctx, subscription, reason=payload.reason, request=request
    )
    return SubscriptionOut.model_validate(subscription)


@router.post(
    "/subscriptions/{subscription_id}/change-plan",
    response_model=SubscriptionOut,
    summary="Re-pin a subscription to a different published version (upgrade or downgrade)",
)
async def change_plan(
    subscription_id: uuid.UUID,
    payload: ChangePlanIn,
    session: SessionDep,
    ctx: ManageCtx,
    catalog: CatalogDep,
    request: Request,
) -> SubscriptionOut:
    """When it lands is decided by the server, never asked for: a trial moves now and free; a paid
    upgrade moves now with a prorated charge for the rest of the period; a paid downgrade waits for
    the next period; an equal-price change moves now. Ask `.../change-plan/preview` first and send
    its `expected` back. Refusals carry `errors[0].code`: 409 `state_changed`,
    `billing_catching_up`; 422 `state`, `cadence`, `currency`, `already_pinned`,
    `product_mismatch`, `not_published`, `not_offerable`, `scheduled_by_opsgrids`; 404
    `not_found`; 403 for a bundle."""
    # Filtered on the caller's organization as well as isolated by RLS (the net, not the licence).
    subscription = await services.require_subscription(
        session, subscription_id, organization_id=ctx.organization_id
    )
    ctx.assert_can_touch(owner_id=None)
    subscription = await services.change_plan(
        session, ctx, catalog, subscription, payload, request=request
    )
    return SubscriptionOut.model_validate(subscription)


@router.post(
    "/subscriptions/{subscription_id}/change-plan/preview",
    response_model=CustomerChangePlanPreviewOut,
    summary="What changing this subscription's plan would do — read-only, writes nothing",
)
async def preview_change_plan(
    subscription_id: uuid.UUID,
    payload: ChangePlanPreviewIn,
    session: SessionDep,
    ctx: ManageCtx,
    catalog: CatalogDep,
) -> CustomerChangePlanPreviewOut:
    """The confirm screen's server-computed answer, from the evaluation `change-plan` itself runs:
    direction, whether it applies `now` or at the `next_period` (and when), whether a prorated
    charge is raised now, what it replaces, the products it adds or removes — and the `expected`
    state to send back with the write. **No amount**: a paid change is priced by the billing run
    when it claims the period. A refusal is part of the answer (`refusal_code`); only a missing
    subscription or version (404) and an unreachable catalog (503) are errors.

    A POST because it carries a body, but a read: no row is written or locked, and no audit or
    outbox row is staged. Same permission as the write it previews."""
    # Filtered on the caller's organization as well as isolated by RLS (the net, not the licence).
    subscription = await services.require_subscription(
        session, subscription_id, organization_id=ctx.organization_id
    )
    ctx.assert_can_touch(owner_id=None)
    return await services.preview_change_plan(session, ctx, catalog, subscription, payload)


@router.get(
    "/subscriptions/{subscription_id}/plan-options",
    response_model=PlanOptionsOut,
    summary="The plans this subscription could move to — the Change plan picker",
)
async def get_plan_options(
    subscription_id: uuid.UUID,
    session: SessionDep,
    ctx: ManageCtx,
    catalog: CatalogDep,
) -> PlanOptionsOut:
    """Catalog's offerable set for this organization and product — active plans' published
    versions, public or negotiated for it, the newest per plan, audience, cadence and currency;
    never a superseded, withdrawn or retired-plan version — each marked with whether this
    subscription can take it, and the plan in force marked. Same permission as changing the plan.
    An amount only for this organization's own negotiated price, and only to a caller who may read
    its invoices; never a list price."""
    subscription = await services.require_subscription(
        session, subscription_id, organization_id=ctx.organization_id
    )
    ctx.assert_can_touch(owner_id=None)
    return await plan_options.plan_options(
        session,
        organization_id=ctx.organization_id,
        principal=ctx.principal,
        catalog=catalog,
        subscription=subscription,
    )
