"""The internal API — service-to-service, never reachable from outside the cluster.

Three endpoints, all operator delegates: a suspension, and the subscribe and change-plan that
are the only way a bundle is sold (the customer routes refuse bundles). The operator console (a
later phase) delegates here rather than reaching into this service's schema. Overrides are
enacted in the service that owns the state —
`subscription` owns the lifecycle, so a staff suspension is a `commerce.subscription.suspended`
event emitted from here, which the product-service projection turns into a de-entitled
activation and the gateway into a 402 (ADR-0004 §5). The console never writes subscription rows.

Guarded by a short-lived service token addressed to this service by name. The organization is
named in the body: this process connects as the NOBYPASSRLS app role, so the write only passes
the tenant policy when the transaction is bound to that organization — the same GUC discipline
the projection consumer depends on, applied here. Binding an organization the caller names and
then letting RLS confirm the subscription actually belongs to it (an id from another tenant is
invisible, a 404) is the isolation, not a trusted body field.

Mounted at the root and excluded from the OpenAPI document: not public API, and it must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import services
from app.api.routes import CatalogDep
from app.registry import ProductRegistry
from app.schemas import OperatorChangePlanIn, OperatorSubscribeIn, SubscriptionOut, SuspendIn
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.logging import get_logger

log = get_logger("subscription.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/subscriptions/{subscription_id}/suspend",
    response_model=SubscriptionOut,
    summary="Suspend a subscription — the console's operator delegate",
)
async def suspend_subscription(
    subscription_id: uuid.UUID,
    payload: SuspendIn,
    session: SessionDep,
    request: Request,
) -> SubscriptionOut:
    """Enact an operator suspension on the subscription's owning organization.

    The transaction is opened here and bound to the named organization as its first statement,
    because a request driven by a service token has no principal to take a chokepoint session
    from. RLS then does the isolation: a subscription id belonging to a different organization is
    invisible under this binding and resolves to a 404.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        subscription = await services.require_subscription(session, subscription_id)
        subscription = await services.suspend(
            session,
            subscription,
            operator_id=payload.operator_id or "console",
            reason=payload.reason,
            request=request,
        )
        result = SubscriptionOut.model_validate(subscription)
    log.info(
        "subscription.suspended",
        subscription_id=str(subscription_id),
        organization_id=str(payload.organization_id),
    )
    return result


def get_product_registry(request: Request) -> ProductRegistry:
    """The registry client, assembled in the lifespan; a dependency so tests substitute a fake."""
    return request.app.state.product_registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ProductRegistry, Depends(get_product_registry)]


@router.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=201,
    summary="Open a subscription on the platform team's behalf — the only way a bundle is sold",
)
async def subscribe_as_operator(
    payload: OperatorSubscribeIn,
    session: SessionDep,
    catalog: CatalogDep,
    registry: RegistryDep,
    request: Request,
) -> SubscriptionOut:
    """Bundles are sales-led (docs/commerce-packaging.md), so the customer route refuses them and
    this one — a service token, an organization bound as the first statement, an accountable
    operator on the audit row — is how an operator surface sells one."""
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        subscription = await services.subscribe_as_operator(
            session, catalog, registry, payload, request=request
        )
        result = SubscriptionOut.model_validate(subscription)
    log.info(
        "subscription.opened_by_operator",
        subscription_id=str(result.id),
        organization_id=str(payload.organization_id),
        product_key=payload.product_key,
    )
    return result


@router.post(
    "/subscriptions/{subscription_id}/change-plan",
    response_model=SubscriptionOut,
    summary="Re-pin a subscription on the platform team's behalf — how a bundle changes rung",
)
async def change_plan_as_operator(
    subscription_id: uuid.UUID,
    payload: OperatorChangePlanIn,
    session: SessionDep,
    catalog: CatalogDep,
    registry: RegistryDep,
    request: Request,
) -> SubscriptionOut:
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        # RLS does the isolation: another organization's id is invisible here, a 404.
        subscription = await services.require_subscription(session, subscription_id)
        subscription = await services.change_plan_as_operator(
            session, catalog, registry, subscription, payload, request=request
        )
        result = SubscriptionOut.model_validate(subscription)
    return result
