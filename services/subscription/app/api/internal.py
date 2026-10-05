"""The internal API — service-to-service, never reachable from outside the cluster.

Operator delegates: a suspension and its inverse, a reactivation, and the subscribe and
change-plan that are the only way a bundle is sold (the customer routes refuse bundles). The
operator console delegates here rather than reaching into this service's schema. Overrides are
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

from app import selectors, services
from app.api.routes import CatalogDep
from app.registry import ProductRegistry
from app.schemas import (
    ChangePlanPreviewOut,
    OperatorChangePlanIn,
    OperatorChangePlanPreviewIn,
    OperatorSubscribeIn,
    ReactivateIn,
    SubscriptionOperatorOut,
    SubscriptionOut,
    SuspendIn,
)
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("subscription.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Console is the only caller of every route in this file. Each is a cross-tenant staff read or
#: write — a subscription listing, an operator suspend, reactivate, subscribe or re-pin — and none
#: is something "any service holding a valid token" should reach. (Suspend and subscribe predated
#: the narrower check and took only the router's token check, so any service with a token for
#: subscription could open or suspend a subscription for any organization; both now take it.)
TRUSTED_CALLER = "console"


def require_console_caller(request: Request) -> None:
    """The narrower caller check: valid, addressed to subscription (the router-level
    `require_service_token` above already checked that), and issued by console specifically."""
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


ConsoleCallerDep = Annotated[None, Depends(require_console_caller)]


@router.post(
    "/subscriptions/{subscription_id}/suspend",
    response_model=SubscriptionOut,
    summary="Suspend a subscription — the console's operator delegate",
)
async def suspend_subscription(
    subscription_id: uuid.UUID,
    payload: SuspendIn,
    _caller: ConsoleCallerDep,
    session: SessionDep,
    request: Request,
) -> SubscriptionOut:
    """Enact an operator suspension on the subscription's owning organization.

    The transaction is opened here and bound to the named organization as its first statement,
    because a request driven by a service token has no principal to take a chokepoint session
    from. RLS then does the isolation: a subscription id belonging to a different organization is
    invisible under this binding and resolves to a 404. Console-only (`ConsoleCallerDep`): a
    cross-tenant staff write that any service holding a token for subscription could otherwise
    make against any organization.
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


@router.post(
    "/subscriptions/{subscription_id}/reactivate",
    response_model=SubscriptionOut,
    summary="Reactivate a suspended subscription — the console's operator delegate",
)
async def reactivate_subscription(
    subscription_id: uuid.UUID,
    payload: ReactivateIn,
    _caller: ConsoleCallerDep,
    session: SessionDep,
    request: Request,
) -> SubscriptionOut:
    """Lift an operator's (or dunning's) suspension on the subscription's owning organization.

    `suspend_subscription`'s inverse, bound and isolated the same way: the transaction is opened
    here and bound to the named organization as its first statement, and RLS confirms the
    subscription really belongs to it (another tenant's id is invisible, a 404). Console-only —
    the narrower `ConsoleCallerDep`, which `suspend_subscription` predates — because lifting a
    hold is a cross-tenant staff write and no other service has any business reaching it. A
    subscription that is not `suspended` is a 409.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        subscription = await services.require_subscription(session, subscription_id)
        subscription = await services.reactivate(
            session,
            subscription,
            operator_id=payload.operator_id or "console",
            reason=payload.reason,
            request=request,
        )
        result = SubscriptionOut.model_validate(subscription)
    log.info(
        "subscription.reactivated",
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
    _caller: ConsoleCallerDep,
    session: SessionDep,
    catalog: CatalogDep,
    registry: RegistryDep,
    request: Request,
) -> SubscriptionOut:
    """Bundles are sales-led (docs/commerce-packaging.md), so the customer route refuses them and
    this one — a console service token, an organization bound as the first statement, an
    accountable operator on the audit row — is how an operator surface sells one."""
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
    _caller: ConsoleCallerDep,
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
    log.info(
        "subscription.plan_change_requested",
        subscription_id=str(subscription_id),
        organization_id=str(payload.organization_id),
        operator_id=payload.operator_id,
        pending=result.pending_plan_version_id is not None,
    )
    return result


@router.post(
    "/subscriptions/{subscription_id}/change-plan/preview",
    response_model=ChangePlanPreviewOut,
    summary="What a plan change would do to this subscription — read-only, writes nothing",
)
async def preview_change_plan_as_operator(
    subscription_id: uuid.UUID,
    payload: OperatorChangePlanPreviewIn,
    _caller: ConsoleCallerDep,
    session: SessionDep,
    catalog: CatalogDep,
    registry: RegistryDep,
    request: Request,
) -> ChangePlanPreviewOut:
    """The confirm screen's server-computed preview: direction, when it lands, what it replaces,
    which products it adds or removes — and the `expected` state to send back with the write.

    A POST because it carries a body, but a read: no row is written or locked, and no audit or
    outbox row is staged. Bound to the named organization and isolated by RLS exactly like the
    write — another organization's subscription id is a 404 and discloses nothing.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, payload.organization_id)
        if not settings.is_production:
            await assert_tenant(session, payload.organization_id)
        subscription = await services.require_subscription(session, subscription_id)
        return await services.preview_change_plan_as_operator(
            session, catalog, registry, subscription, payload
        )


@router.get(
    "/organizations/{organization_id}/subscriptions",
    response_model=list[SubscriptionOperatorOut],
    summary="Every subscription this organization holds — console's cross-tenant pricing read",
)
async def list_subscriptions_for_operator(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    _caller: ConsoleCallerDep,
) -> list[SubscriptionOperatorOut]:
    """What `GET /admin/organizations/{id}/pricing`, the pricing POST's re-pin decision, and the
    one-off backfill (`services/console/app/backfill.py`) all need: this organization's live
    subscriptions, with the id, `current_period_end` and `updated_at` the console projection
    (`console.OrgCommercialState`) does not carry. Bound to the named organization as the first
    statement, the same shape every other route in this file uses — there is no principal to take
    a chokepoint session from on a service-token request, so RLS isolation depends on this binding
    rather than on a caller-supplied filter alone.

    `SubscriptionOperatorOut`, not the plain `SubscriptionOut` every customer-facing route in
    `app/api/routes.py` returns — see that schema's own docstring for why `updated_at` lives only
    here.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        subscriptions = await selectors.list_by_organization(session, organization_id)
        return [SubscriptionOperatorOut.model_validate(s) for s in subscriptions]
