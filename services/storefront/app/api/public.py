"""The anonymous buyer surface. **One route, and every line of it is load-bearing.**

────────────────────────────────────────────────────────────────────────────────────────────
**This module contains no `try` and no `except`, and it must stay that way.**

ADR-0010 §13's rule, and the most important sentence in the ADR for a buyer:

    A check that fails renders as "we couldn't check", never as "we don't deliver here".
    Unknown is not no.

The server never returns an `UNSERVED` outcome it did not compute. `UNKNOWN` is a *client*
state produced by a transport failure — a 429, a 503, a dropped connection. The failure this
guards against is the one a naive `catch → not serviceable` produces by default, and it is
expensive in exactly the way that is hard to notice: a buyer in a served area is told the shop
does not reach them, and leaves. So the structure is the control — there is nothing here that
*could* turn an exception into an answer — and `tests/test_public_serviceability.py` asserts it
against the module's own syntax tree rather than trusting this paragraph.
────────────────────────────────────────────────────────────────────────────────────────────

**The path is exact, and it has no path variable — a standing constraint on the whole future
buyer API.** `GatewayPolicy.public_paths` is an exact-path frozenset (`full_path in
policy.public_paths`), and `full_path` is `f"/api/{path}"` from the catch-all, so it excludes
the query string. A public *prefix* is not configurable and `/public/store/{slug}` is not
expressible at all. Every anonymous storefront endpoint must therefore carry its variables in
the query string or the request body. The store slug travels in the body here for that reason,
and the catalogue and store-document ADRs inherit the rule rather than rediscovering it against
a route table they have already designed.

**Rate limiting belongs to the gateway, not here.** 60 requests per minute per client IP, on the
public branch, before this service is reached (ADR-0010 §4). A second limiter in front of this
route would double the fail-closed surface of a limiter that is already correctly fail-closed —
the consequence, stated plainly in ADR-0010 §13, is that a Redis outage takes the buyer-facing
check down entirely, and the client renders that as "couldn't check".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.schemas import (
    FeesOut,
    PromiseOut,
    PublicServiceabilityIn,
    ServiceabilityOut,
)
from app.selectors import ServiceabilityAnswer
from platform_core.db import get_directory_session
from platform_core.errors import NotFoundProblem
from platform_core.logging import get_logger
from platform_core.models import utcnow

log = get_logger("storefront.public")

router = APIRouter(tags=["storefront (public)"])

#: The **BYPASSRLS, SELECT-only** reader, on `storefront.storefronts` and nothing else. An
#: anonymous request arrives with no organization — the gateway writes `x-org-id` only after
#: decoding a token, and the public branch returns before that — so the tenant has to be
#: resolved before any tenant-bound transaction can be opened, which means before row-level
#: security can protect anything.
#:
#: `get_directory_session` **falls back to the ordinary session when no directory role is
#: configured**, which here would mean the lookup binds no tenant, matches nothing, and every
#: buyer in the world is told the store does not deliver to them — HTTP 200, no error, no log.
#: `config.assert_directory_role_configured` refuses to boot rather than let that happen, and
#: is the reason this dependency can be trusted.
DirectorySession = Annotated[AsyncSession, Depends(get_directory_session)]


def answer_out(answer: ServiceabilityAnswer, *, currency: str) -> ServiceabilityOut:
    """The one place a computed answer becomes a response body.

    Shared with the merchant's tester (`api/routes.py`), which widens it with the matched zone.
    One selector computes the answer, one function renders it — so the buyer and the merchant
    testing that buyer's experience cannot be shown two different shapes of the same decision.
    """
    zone = answer.zone
    promise = (
        PromiseOut(
            kind=answer.promise_kind,  # non-null whenever mode is PROMISED
            minutes=answer.promise_minutes,
            date=answer.promise_date,
        )
        if answer.serviceable and answer.promise_kind is not None
        else None
    )
    fees = (
        FeesOut(
            currency=currency,
            delivery_fee_minor=zone.delivery_fee_minor,
            free_delivery_above_minor=zone.free_delivery_above_minor,
            min_order_minor=zone.min_order_minor,
        )
        if answer.serviceable and zone is not None
        else None
    )
    return ServiceabilityOut(
        postal_code=answer.postal_code,
        serviceable=answer.serviceable,
        mode=answer.mode,  # the column's check constraint is the enum
        reason=answer.reason,
        promise=promise,
        fees=fees,
        buyer_note=answer.buyer_note if answer.serviceable else None,
        checked_at=utcnow(),
    )


@router.post(
    "/storefront/public/serviceability",
    response_model=ServiceabilityOut,
    summary="Does this store deliver to this postal code?",
    responses={
        404: {"description": "No published store with that slug"},
        422: {"description": "That is not a postal code — distinct from 'we do not deliver there'"},
        429: {"description": "Rate limited by the gateway; render as 'couldn't check'"},
        503: {"description": "Could not check; render as 'couldn't check', never as 'no'"},
    },
)
async def check_serviceability(
    payload: PublicServiceabilityIn,
    directory: DirectorySession,
) -> ServiceabilityOut:
    """**No token, no account, no session.** The one thing a stranger may ask this platform.

    Four steps, in this order and no other:

    1. the postal code is parsed and validated — a typo is a 422, never an answer, because "that
       is not a postal code" and "we do not deliver there" are different facts;
    2. the slug is resolved on the bypass reader, which returns two ids and a status and buys
       *discovery only*;
    3. an unresolved or unpublished store is a **404**, logged at warning with the slug, and is
       distinct in both status and log from "resolved, and not served" — a draft store is
       invisible so a merchant can map zones and use the tester before any buyer can ask;
    4. everything after that runs tenant-bound on the NOBYPASSRLS application role, under the
       same row policies an authenticated request gets.

    The answer is **not** cached server-side and **must not** be cached by the client: the
    buyer's postal code lives in their browser, the answer does not, and it is re-fetched on
    every page load. A client-side check that is trusted is not a check (ADR-0010 §5).

    **Entitlement is not checked here and cannot cheaply be.** The gateway's public branch
    returns before its entitlement gate, and asking product-service would put an HTTP hop on
    every page view. A merchant whose `storefront` entitlement lapses therefore keeps answering
    until somebody unpublishes them; the consumer that would flip `status` to `suspended` on
    deactivation is named in ADR-0010 §4, is **not built here, and has no owner**.
    """
    postal_code = domain.validate_postal_code(payload.postal_code)

    store = await selectors.resolve_public_store(directory, payload.store)
    if store is None or not store.is_published:
        # Loud, because the policy is quiet (ADR-0002 §4). A slug that resolves to nothing is
        # either a typo in someone's runtime config or an unpublished store, and both are worth
        # finding in a log rather than inferring from a support ticket.
        log.warning(
            "storefront.public.store_unresolved",
            slug=payload.store,
            status=store.status if store is not None else None,
        )
        raise NotFoundProblem("No published store with that name.")

    answer = await services.check_serviceability_for_store(store, postal_code)
    return answer_out(answer, currency=store.currency)
