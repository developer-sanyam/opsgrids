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

**Idempotent by content, not by a client-supplied key.** A *published* version of this plan
already restricted to this organization with exactly this one flat price
(`selectors.find_bespoke_version`) is returned unchanged instead of minting a new one. That is what
gives console's own claim/call/record a safe recovery path (docs/io-outside-transactions.md): a
retried request, or a resumed one after a crash between this call and console's record of it,
cannot create two versions selling the same organization the same price under different ids.

**It never publishes a draft it did not just write.** This route drafts and publishes in one
transaction, so it never leaves a draft behind; a matching draft can only be someone else's — an
owner's negotiated sheet still under review on the Plans page. Publishing that here would let
`billing_ops` freeze an owner's draft without the owner-only publish gate, its reason or its review
(ADR-0030 §1), so such a draft is left alone and this request writes its own version.

**The product registry is read before the transaction opens** (D3,
docs/io-outside-transactions.md #10), for the same reason the operator routes read it: publish
must know whether the product is a bundle, and a bundle version that names no members entitles a
key that routes nowhere (ADR-0030 §3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.api.admin import RegistryDep
from app.models import PLATFORM_ORG
from app.registry import ProductSnapshot, take_snapshot
from app.schemas import BespokePriceIn, FeatureIn, PlanVersionOut, PriceIn, VersionCreateIn
from platform_core.auth import Principal, require_operator, require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, constraint_name, get_session, set_tenant
from platform_core.errors import ConflictProblem, NotFoundProblem, UnauthorizedProblem

#: Every product key on this platform is this shape (hrms, crm, storefront, ...). Fix for
#: MAJOR-1 (security review, fix round 1): this path parameter used to accept any string, and
#: `..`-and-slash content in it could retarget this request at a different catalog route
#: entirely, carrying this call's own service token and the forwarded operator bearer with it.
#: Console validates the same shape at its own boundary and percent-encodes it again before
#: interpolating it into this URL — this is the second, independent layer, not the only one.
_PRODUCT_KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"


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

#: The operator-token half: forwarded verbatim, verified via JWKS like any operator route.
#: `owner` and `billing_ops` (fix round 1, MAJOR-2) — narrower than `/admin/catalog`'s own
#: authoring routes (`owner`-only, ADR-0003 addendum), which can draft or publish *any* version
#: with *any* content. This route can only ever write one flat price for one organization on an
#: existing public product, so it sits with console's own `OperatorBilling` gate
#: (`console/app/api/routes.py`) rather than the wider catalogue-authoring one.
OperatorDep = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]


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


async def get_product_snapshot(
    _caller: ConsoleCallerDep, _operator: OperatorDep, registry: RegistryDep
) -> ProductSnapshot:
    """The product registry, read before the request's transaction opens and only after both
    credentials have been checked, so a refused caller never reaches product-service. A failed
    read is kept, not raised: publishing fails on it (503), since it cannot tell a bundle apart."""
    return await take_snapshot(registry)


ProductsDep = Annotated[ProductSnapshot, Depends(get_product_snapshot)]


@router.post(
    "/organizations/{organization_id}/products/{product_key}/bespoke-price",
    response_model=PlanVersionOut,
    summary="Clone the product's public flat plan into a published, audience-of-one price",
)
async def ensure_bespoke_price(
    organization_id: uuid.UUID,
    product_key: Annotated[str, Path(pattern=_PRODUCT_KEY_PATTERN)],
    payload: BespokePriceIn,
    products: ProductsDep,
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
    if existing is not None:
        # Published, and exactly this one flat price: the version an earlier call already made.
        version = existing
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
        # Concurrency (fix round 1): two identical requests racing both read "no matching
        # version yet" above and both try to draft one. `next_version_no` is computed from the
        # same read, so they collide on `uq_plan_versions_plan_version` rather than on content —
        # a SAVEPOINT (not a bare `session.add`/`flush`) so the loser can recover on the *same*
        # transaction: once one statement inside `session.begin()` raises, Postgres marks the
        # whole transaction aborted until a ROLLBACK, and `begin_nested()` is the ROLLBACK TO
        # SAVEPOINT that undoes only the failed insert, leaving the request's own tenant binding
        # usable for the recovery read below.
        try:
            async with session.begin_nested():
                drafted = await services.create_version(
                    session, operator, plan, draft_payload, request=request
                )
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_plan_versions_plan_version":
                raise
            winner = await selectors.find_bespoke_version(
                session,
                plan_id=plan.id,
                organization_id=organization_id,
                currency=payload.currency,
                amount_minor=payload.amount_minor,
                billing_interval=payload.billing_interval,
            )
            if winner is None:
                # The collision was with someone else's *different* draft on this plan (a
                # concurrent public-plan bump, say) — a real conflict this route cannot resolve
                # by re-reading, since there is nothing matching this request's own content yet.
                raise ConflictProblem(
                    "a concurrent change to this plan's versions conflicted with this request; "
                    "retry it"
                ) from exc
            # The winner committed its draft and its publish together, so it is published.
            version = winner
        else:
            # Drafted without the registry (`products` stays out of `create_version`), so a
            # version that declares a bundle is still refused here as before; the snapshot goes
            # to publish, which needs it to tell whether this product is a bundle at all.
            version = await services.publish_version(
                session, operator, drafted, request=request, products=products
            )

    fresh = await selectors.get_version(session, version.id)
    assert fresh is not None  # just written or just resolved in this same transaction
    return PlanVersionOut.model_validate(fresh)
