"""The internal API — service-to-service, never reachable from outside the cluster.

The pin lookup a subscription makes when it pins — or re-pins — to a plan version (ADR-0004 §2,
ADR-0000 §9), plus two reads console composes into the operator screens: the public flat plans
and, in batch, the display names of many plan versions. Catalog is the authority for whether a
version exists, what it costs and what it is called, so siblings ask here rather than reaching
across the schema boundary — the rule that keeps a later database-per-service split a
configuration change.

The definitional tables are global and un-policied, so this reads on the ordinary app session
with no tenant binding: there is no tenant to bind for a global catalogue, and the app role reads
the un-policied tables fine. The read is deliberately **unfiltered** — it returns the version
whatever its state and whatever its audience — and the calling service applies the
published-and-visible rule. Pushing that decision to the caller keeps this a plain read and puts
the commercial policy where the subscription lives.

Guarded by a short-lived service token addressed to catalog by name. Mounted at the root and
excluded from the OpenAPI document: not public API, and it must not appear as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.schemas import (
    FeatureOut,
    InternalPlanVersionNameOut,
    InternalPlanVersionNamesOut,
    InternalPlanVersionOut,
    InternalPriceOut,
    OfferableVersionOut,
    OfferableVersionsOut,
    PublicFlatPlanOut,
    PublicFlatPlansOut,
    SeatClassOut,
)
from platform_core.auth import require_service_token
from platform_core.db import get_session
from platform_core.errors import NotFoundProblem, UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("catalog.internal")

#: Every product key on this platform is this shape (hrms, crm, storefront, ...). The same bound
#: console's bespoke-price route puts on its path parameter (`console_internal.py`), here on a query
#: value, so a stray string is a 422 rather than a lookup.
_PRODUCT_KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


#: How many ids one batch call may name. A subscription list is bounded by the number of
#: organizations times products, and console chunks beyond this — the cap only keeps a single
#: request from being an unbounded `IN (...)`.
MAX_VERSION_NAMES_PER_CALL = 200


@router.get(
    "/plan-versions",
    response_model=InternalPlanVersionNamesOut,
    summary="Plan and product names for many plan versions at once — console's subscription list",
)
async def list_plan_version_names(
    session: SessionDep,
    ids: Annotated[list[uuid.UUID], Query(max_length=MAX_VERSION_NAMES_PER_CALL)],
) -> InternalPlanVersionNamesOut:
    """The batch counterpart of the single-version read below, returning **only** display names —
    so a list of N subscriptions costs one call, not N. Registered before `/{version_id}` and on a
    different path shape (no trailing id), so it can never be mistaken for it. Unfiltered like its
    sibling: a name is not a commercial decision, and the service-token gate is the same one."""
    rows = await selectors.get_version_names(session, ids)
    return InternalPlanVersionNamesOut(
        versions=[
            InternalPlanVersionNameOut(
                plan_version_id=version.id,
                plan_id=plan.id,
                plan_name=plan.name,
                product_key=plan.product_key,
            )
            for version, plan in rows
        ]
    )


@router.get(
    "/plan-versions/{version_id}",
    response_model=InternalPlanVersionOut,
    summary="Resolve a plan version for a sibling service pinning it",
)
async def get_plan_version(version_id: uuid.UUID, session: SessionDep) -> InternalPlanVersionOut:
    """The version, its prices, features and seat classes, and the product it belongs to —
    unfiltered.

    The feature sheet rides along because packaging is declared there (`bundled_products`,
    `bundle_tier`), and a caller that has to make a second call to learn what it just pinned
    will eventually make it against a catalogue that has moved on.

    404 when it does not exist, which the caller treats as a definite "no such version"; anything
    else (unpublished, restricted to another org) is returned as data for the caller to judge,
    because those are commercial decisions a subscription makes, not existence facts catalog hides.
    """
    version = await selectors.get_version(session, version_id)
    if version is None:
        raise NotFoundProblem("No such plan version")
    plan = await selectors.get_plan(session, version.plan_id)

    log.info("plan_version.resolved", version_id=str(version_id), state=version.state)
    return InternalPlanVersionOut(
        id=version.id,
        plan_id=version.plan_id,
        product_key=plan.product_key if plan else None,
        plan_name=plan.name if plan else None,
        version_no=version.version_no,
        state=version.state,
        billing_interval=version.billing_interval,
        currency=version.currency,
        restricted_to_org_id=version.restricted_to_org_id,
        prices=[InternalPriceOut.model_validate(p) for p in version.prices],
        features=[FeatureOut.model_validate(f) for f in version.features],
        seat_classes=[SeatClassOut.model_validate(sc) for sc in version.seat_classes],
    )


#: The one caller of the offerable-versions read below. It carries amounts for every plan a given
#: organization could be moved onto, including a version negotiated for that organization alone —
#: narrower than "any service holding a token for catalog", unlike the unfiltered pin lookup above
#: it, which predates this and returns one version by id.
TRUSTED_CALLER = "console"


def require_console_caller(request: Request) -> None:
    """Issued by console specifically; the router-level `require_service_token` has already
    checked the token is valid and addressed to catalog, and recorded who minted it."""
    if getattr(request.state, "service_caller", None) != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


ConsoleCallerDep = Annotated[None, Depends(require_console_caller)]


@router.get(
    "/organizations/{organization_id}/offerable-versions",
    response_model=OfferableVersionsOut,
    summary="The plan versions one organization may be switched onto — console's plan picker",
)
async def list_offerable_versions(
    organization_id: uuid.UUID,
    _caller: ConsoleCallerDep,
    session: SessionDep,
    product_key: Annotated[
        str, Query(pattern=_PRODUCT_KEY_PATTERN, description="The product whose plans to offer")
    ],
) -> OfferableVersionsOut:
    """Active plans' published, current versions that are public or negotiated for exactly this
    organization — `selectors.visible_plans`' audience rule, then superseded versions dropped
    (`selectors.offerable_versions`). Console's `OperatorBilling` gate is the role check (owner and
    billing_ops), so this cannot reuse the owner-only authoring router, and it must never reuse
    the amount-free tenant `/plans` read, which is for a customer.

    Read-only, and a global un-policied read like the rest of this file: the organization in the
    path is a *filter* on whose bespoke prices are visible, not a tenant binding.
    """
    pairs = await selectors.offerable_versions(
        session, product_key=product_key, org_id=organization_id
    )
    return OfferableVersionsOut(
        versions=[
            OfferableVersionOut(
                plan_id=plan.id,
                plan_code=plan.code,
                plan_name=plan.name,
                product_key=plan.product_key,
                plan_version_id=version.id,
                version_no=version.version_no,
                state=version.state,
                billing_interval=version.billing_interval,
                currency=version.currency,
                restricted_to_org_id=version.restricted_to_org_id,
                published_at=version.published_at,
                prices=[InternalPriceOut.model_validate(p) for p in version.prices],
                features=[FeatureOut.model_validate(f) for f in version.features],
            )
            for plan, version in pairs
        ]
    )


@router.get(
    "/public-flat-plans",
    response_model=PublicFlatPlansOut,
    summary="Every product's public flat plan — the console pricing screen's baseline",
)
async def list_public_flat_plans(session: SessionDep) -> PublicFlatPlansOut:
    """One row per product that sells a public flat plan (ADR-0013 §2). Read by console to render
    "what this product publicly costs" beside whatever an organization is bespoke-priced at; open
    to any service holding a valid service token, like the pin lookup above — it discloses nothing
    an unauthenticated customer browse would not already show."""
    rows = await selectors.all_public_flat_plans(session)
    return PublicFlatPlansOut(
        plans=[
            PublicFlatPlanOut(
                product_key=plan.product_key,
                product_name=plan.name,
                plan_id=plan.id,
                plan_version_id=version.id,
                amount_minor=next(
                    (p.amount_minor or 0 for p in version.prices if p.kind == "flat"), 0
                ),
                currency=version.currency,
                billing_interval=version.billing_interval,
            )
            for plan, version in rows
        ]
    )
