"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint: the pin lookup a subscription makes when it pins — or re-pins — to a plan version
(ADR-0004 §2, ADR-0000 §9). Catalog is the authority for whether a version exists and what it
costs, so subscription asks here rather than reaching across the schema boundary — the rule that
keeps a later database-per-service split a configuration change.

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

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.schemas import FeatureOut, InternalPlanVersionOut, InternalPriceOut, SeatClassOut
from platform_core.auth import require_service_token
from platform_core.db import get_session
from platform_core.errors import NotFoundProblem
from platform_core.logging import get_logger

log = get_logger("catalog.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
        version_no=version.version_no,
        state=version.state,
        billing_interval=version.billing_interval,
        currency=version.currency,
        restricted_to_org_id=version.restricted_to_org_id,
        prices=[InternalPriceOut.model_validate(p) for p in version.prices],
        features=[FeatureOut.model_validate(f) for f in version.features],
        seat_classes=[SeatClassOut.model_validate(sc) for sc in version.seat_classes],
    )
