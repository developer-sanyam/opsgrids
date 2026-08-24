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
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.catalog import PlanCatalog
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    CancelIn,
    ChangePlanIn,
    ServiceInfo,
    SubscribeIn,
    SubscriptionOut,
)
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session

router = APIRouter(tags=["subscription"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]
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
    return [SubscriptionOut.model_validate(s) for s in subscriptions]


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
    return SubscriptionOut.model_validate(subscription)


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
    subscription = await services.require_subscription(session, subscription_id)
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
    subscription = await services.require_subscription(session, subscription_id)
    ctx.assert_can_touch(owner_id=None)
    subscription = await services.change_plan(
        session, ctx, catalog, subscription, payload, request=request
    )
    return SubscriptionOut.model_validate(subscription)
