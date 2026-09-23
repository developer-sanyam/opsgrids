"""The internal API — service-to-service, never reachable from outside the cluster.

Two endpoints. The registry read (`/products`) is the minor one — catalog validates a bundle's
member keys against it. The entitlements read is the other, and it is the source of truth for
every 402 in the platform. auth-service calls it while minting a token; the resulting list
becomes the `ent` claim, and the gateway compares a route's product against that claim on every
request.

Cross-tenant by necessity: auth asks about an organization it is issuing a token *for*, at a
moment when there is no tenant context yet — the token being minted is what would establish
one. Guarded by a short-lived service token addressed to this service by name.

Because there is no tenant to bind, the read runs on the **directory session**: BYPASSRLS and
SELECT-only, exactly the shape of account-service's directory reads (ADR-0002, "the cross-tenant
reader"). The application role would be wrong here — `activations` carries a forced row-level
security policy `USING (organization_id = current_org())`, and with no tenant GUC `current_org()`
is NULL, so the organization's real activation row is invisible and the endpoint returns only
the platform products. That is the 402 blocker: an org that activated a product still signs in
without it. Entitlements is the only route in this module that reads across tenants; the
registry read shares the directory session because it is SELECT-only on the global `products`
table, which that role is already granted, and a read-only role is the right one for it.

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
from platform_core.db import get_directory_session
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

#: The cross-tenant reader: BYPASSRLS, SELECT-only, and used by nothing else. The entitlements
#: read resolves an organization's activations before any tenant is bound, so it cannot carry a
#: tenant binding and cannot be served by the application role.
SessionDep = Annotated[AsyncSession, Depends(get_directory_session)]


@router.get(
    "/organizations/{organization_id}/entitlements",
    summary="Which products this organization may currently use",
)
async def entitlements(session: SessionDep, organization_id: uuid.UUID) -> dict[str, Any]:
    """What the `ent` claim is built from.

    Resolved on the SELECT-only bypass reader (`get_directory_session`), which sees the
    organization's activations before any tenant is bound — the same shape as account-service's
    directory reads (ADR-0002). On the application role this read returns nothing: `activations`
    is under a forced tenant policy and no GUC is set at token-minting time, so every product's
    routes would 402.

    Returns an empty list for an organization with no activations rather than 404. That is the
    honest answer — "this customer has bought nothing" is a real state, and the platform
    products every customer gets are still included — and it keeps auth's sign-in path free of
    a not-found branch that would otherwise have to decide whether an unknown organization is
    an error or an empty one.
    """
    keys = await selectors.entitlements_for(session, organization_id)
    log.info("entitlements.resolved", organization_id=str(organization_id), count=len(keys))
    return {"entitlements": keys}


@router.get("/products", summary="The product registry, for a sibling validating a product key")
async def registry(session: SessionDep) -> dict[str, Any]:
    """Every registered product key and the two flags a caller needs to judge it.

    Catalog asks this when a bundle is authored: every key a `bundled_products` list names must
    be a registered product, must not be a platform product (every customer already has those),
    and must not itself be a bundle. `products` is global platform vocabulary with no tenant in
    it, so there is nothing to bind; it is read on this router's SELECT-only session like the
    entitlements read beside it.
    """
    products = await selectors.list_products(session)
    return {
        "products": [
            {
                "key": p.key,
                "status": p.status,
                "is_platform": p.is_platform,
                "is_bundle": p.is_bundle,
            }
            for p in products
        ]
    }
