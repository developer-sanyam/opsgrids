"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint, and it is the source of truth for every 402 in the platform. auth-service calls
it while minting a token; the resulting list becomes the `ent` claim, and the gateway compares
a route's product against that claim on every request.

Cross-tenant by necessity: auth asks about an organization it is issuing a token *for*, at a
moment when there is no tenant context yet — the token being minted is what would establish
one. Guarded by a short-lived service token addressed to this service by name.

Mounted at the root rather than under `/api/v1`, and excluded from the OpenAPI document: this
is not public API and must not appear as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from platform_core.auth import require_service_token
from platform_core.db import get_session
from platform_core.logging import get_logger

log = get_logger("product.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # The whole router, not each route: a route added here later cannot leak into the public
    # document by somebody forgetting the flag.
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "/organizations/{organization_id}/entitlements",
    summary="Which products this organization may currently use",
)
async def entitlements(session: SessionDep, organization_id: uuid.UUID) -> dict[str, Any]:
    """What the `ent` claim is built from.

    Returns an empty list for an organization with no activations rather than 404. That is the
    honest answer — "this customer has bought nothing" is a real state, and the platform
    products every customer gets are still included — and it keeps auth's sign-in path free of
    a not-found branch that would otherwise have to decide whether an unknown organization is
    an error or an empty one.
    """
    keys = await selectors.entitlements_for(session, organization_id)
    log.info("entitlements.resolved", organization_id=str(organization_id), count=len(keys))
    return {"entitlements": keys}
