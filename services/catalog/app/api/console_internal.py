"""Console's narrow authoring surface: one bespoke flat price, clone-and-publish, idempotent.

`POST /api/v1/admin/organizations/{id}/pricing` on console needs catalog to do exactly one
thing — clone the product's public flat plan into a draft restricted to one organization, set its
price, and publish it (ADR-0004 §2) — and nothing wider. Rather than have console drive the
general-purpose `/admin/catalog` authoring API (two calls, no idempotency, and console would have
to know catalog's own domain rules to find "the" public flat plan to clone), this is a single,
purpose-built endpoint that does that one thing and nothing else.

**Two credentials, both required** — the same shape `account/app/api/admin.py`'s directory read
uses, and for the same reason: a service token alone only proves *some* caller holds the shared
internal-token secret, and an operator bearer token alone does not prove the call came through
console rather than straight from a browser that found this port.

1. **A service token from console** (`X-Internal-Token`, `aud="catalog"`, `iss="console"`) — *who
   is calling*. Checked first; nothing else here is reachable without it.
2. **The operator's own access token**, forwarded verbatim as `Authorization: Bearer …` — *on
   whose behalf, and with what role*. Verified the ordinary way `require_operator` verifies any
   operator-audience route (`get_principal` accepts a directly-presented bearer token, not only a
   gateway-forwarded one), so catalog does not trust console's word for who is acting or what role
   they hold — the same authoring routes on `/admin/catalog` demand `owner`, and this demands it
   too, because a bespoke price is exactly the "commercial catalogue" write that role gate exists
   for.

**Idempotent by content, not by a client-supplied key.** A version of this plan already
restricted to this organization at exactly this price (`selectors.find_bespoke_version`) is
reused — published if it was left in draft, returned unchanged if it was already published —
instead of minting a new one. That is what gives console's own claim/call/record a safe recovery
path (docs/io-outside-transactions.md): a retried request, or a resumed one after a crash between
this call and console's record of it, cannot create two versions selling the same organization the
same price under different ids.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.models import PLATFORM_ORG
from app.schemas import BespokePriceIn, FeatureIn, PlanVersionOut, PriceIn, VersionCreateIn
from platform_core.auth import Principal, require_operator, require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import ConflictProblem, NotFoundProblem, UnauthorizedProblem

router = APIRouter(
    prefix="/admin/internal/catalog",
    tags=["catalog-console-internal"],
    include_in_schema=False,
)

#: Only console composes this call: a service token from anywhere else — however genuine — is
#: not a call this route recognises, whatever operator token rides along with it.
TRUSTED_CALLER = "console"


def require_console_caller(request: Request) -> None:
    """The service-token half: valid, addressed to catalog, and issued by console."""
    require_service_token(request)
    if getattr(request.state, "service_caller", None) != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


ConsoleCallerDep = Annotated[None, Depends(require_console_caller)]

#: The operator-token half: forwarded verbatim, verified via JWKS like any operator route. Owner
#: only — the same gate `/admin/catalog` puts on every authoring route (ADR-0003 addendum).
OperatorDep = Annotated[Principal, Depends(require_operator(roles={"owner"}))]


async def get_operator_session(
    _caller: ConsoleCallerDep,
    operator: OperatorDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """Open the request's transaction bound to the platform sentinel organization — the same
    binding `app/api/admin.py` uses for every other authoring route, and for the same reason:
    authoring is a platform act, and catalog's policied `audit_logs`/`outbox` need a tenant to
    satisfy their `WITH CHECK` under."""
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, PLATFORM_ORG)
        if not settings.is_production:
            await assert_tenant(session, PLATFORM_ORG)
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_operator_session, scope="function")]


@router.post(
    "/organizations/{organization_id}/products/{product_key}/bespoke-price",
    response_model=PlanVersionOut,
    summary="Clone the product's public flat plan into a published, audience-of-one price",
)
async def ensure_bespoke_price(
    organization_id: uuid.UUID,
    product_key: str,
    payload: BespokePriceIn,
    session: SessionDep,
    operator: OperatorDep,
    request: Request,
) -> PlanVersionOut:
    if not await selectors.product_has_any_plan(session, product_key):
        raise NotFoundProblem(f"No such product: {product_key!r}")

    candidate = await selectors.get_public_flat_plan(session, product_key)
    if candidate is None:
        raise ConflictProblem(
            f"{product_key!r} has no public flat plan to clone a bespoke price from"
        )
    plan, source_version = candidate

    existing = await selectors.find_bespoke_version(
        session,
        plan_id=plan.id,
        organization_id=organization_id,
        currency=payload.currency,
        amount_minor=payload.amount_minor,
        billing_interval=payload.billing_interval,
    )
    if existing is not None and existing.state == "published":
        version = existing
    elif existing is not None:
        # A draft left over from a crashed attempt (call succeeded, console's record did not) —
        # finish it rather than mint a rival with the same content.
        version = await services.publish_version(session, operator, existing, request=request)
    else:
        draft_payload = VersionCreateIn(
            billing_interval=payload.billing_interval,
            currency=payload.currency,
            restricted_to_org_id=organization_id,
            prices=[
                PriceIn(kind="flat", amount_minor=payload.amount_minor, currency=payload.currency)
            ],
            # Carried over from the public plan so a bespoke price does not silently drop
            # whatever trial length (or other feature) the public version declares.
            features=[
                FeatureIn(feature_key=f.feature_key, value=f.value) for f in source_version.features
            ],
            seat_classes=[],
        )
        drafted = await services.create_version(
            session, operator, plan, draft_payload, request=request, registry=None
        )
        version = await services.publish_version(session, operator, drafted, request=request)

    fresh = await selectors.get_version(session, version.id)
    assert fresh is not None  # just written or just resolved in this same transaction
    return PlanVersionOut.model_validate(fresh)
