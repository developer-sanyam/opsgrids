"""The anonymous buyer surface. **The first two routes are load-bearing down to the line**;
`public_catalogue` and `public_enquiry` (gaps 4 and 6) reuse the same discipline: resolve the
slug on the bypass reader, refuse anything that is not a published store with one identical
404, then run every further read or write tenant-bound through `session_scope`.

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

**The paths are exact, and neither has a path variable — a standing constraint on the whole
future buyer API.** `GatewayPolicy.public_paths` is an exact-path frozenset (`full_path in
policy.public_paths`), and `full_path` is `f"/api/{path}"` from the catch-all, so it excludes
the query string. A public *prefix* is not configurable and `/public/store/{slug}` is not
expressible at all. Every anonymous storefront endpoint must therefore carry its variables in
the query string or the request body. The store slug travels in the body here for that reason,
the buyer's hostname travels in the next route's body for the same one, and the catalogue and
store-document ADRs inherit the rule rather than rediscovering it against a route table they
have already designed.

**The order these two are called in is the order they are written in.** A browser lands on a
hostname, asks `store-context` which store that is, and then asks `serviceability` about that
store *by slug*. ADR-0010's wire format is unchanged by the second address existing, which is
the whole reason the host is resolved once in a bootstrap rather than added as a field to every
public endpoint (ADR-0012 §2).

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
from app.config import settings
from app.schemas import (
    CategoryPublicOut,
    EnquiryCreate,
    EnquiryReceiptOut,
    FeesOut,
    ProductPublicOut,
    ProductVariantOut,
    PromiseOut,
    PublicCatalogueIn,
    PublicCatalogueOut,
    PublicServiceabilityIn,
    PublicStoreContextIn,
    PublicThemeOut,
    ServiceabilityOut,
    StoreContextOut,
)
from app.selectors import ServiceabilityAnswer
from platform_core.db import get_directory_session
from platform_core.errors import NotFoundProblem
from platform_core.logging import get_logger
from platform_core.models import utcnow
from platform_core.pagination import Page, PageQuery

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


@router.post(
    "/storefront/public/store-context",
    response_model=StoreContextOut,
    summary="Which store is this address, and what is it called?",
    responses={
        404: {"description": "No published store at this address — and it says nothing more"},
        422: {"description": "That is not a hostname — distinct from 'nothing is published there'"},
        429: {"description": "Rate limited by the gateway; retry, never render an empty store"},
        503: {"description": "Could not look it up; render as unavailable, never as 'no store'"},
    },
)
async def store_context(
    payload: PublicStoreContextIn,
    directory: DirectorySession,
) -> StoreContextOut:
    """**No token, no account, no session** — the second thing a stranger may ask this platform.

    A buyer's browser arrives at a hostname and needs to know which store it is looking at. The
    bundle is identical for every merchant, so this is what turns `window.location.hostname`
    into a slug, a brand and a canonical address (ADR-0012 §2).

    Three steps, in this order and no other:

    1. the host is normalised or refused — **a string that is not a hostname is a 422, never a
       404**, because "that is not an address" and "nothing is published there" are different
       facts, exactly as a malformed postal code is distinct from an unserved one;
    2. it is resolved on the bypass reader, which reads one row of one table and buys *discovery
       only* — the platform subdomain through the existing slug lookup, a custom domain only at
       a status that means the claim was proved;
    3. **anything that is not a published store is the same 404, with the same sentence.** An
       unknown host, a `draft` store, a `suspended` store and an unverified claim are one answer
       on the wire. Distinguishing them would hand a stranger an oracle for which domains are
       connected to which stores and which tenants exist but are not live yet — exactly the
       enumeration ADR-0011 §5's global uniqueness rule already makes tempting.

    Nothing tenant-bound runs here, and that is not an omission: this route reads one identity
    row and returns columns from it. There is no second query to open a transaction for, so
    there is no place for row-level security to apply and nothing for it to protect. The moment
    this payload grows something that is not on `storefronts` — the store document, a catalogue
    — that stops being true, and the extension goes through `session_scope` exactly as
    serviceability's does.

    **Entitlement is not checked, and cannot cheaply be.** The gateway's public branch returns
    before its entitlement gate. A merchant whose `storefront` entitlement lapses keeps
    answering here until somebody unpublishes them; the consumer that would flip `status` to
    `suspended` is ADR-0010 §4's, is still unowned, and is named again in ADR-0012 slice 3.
    """
    cfg = settings()
    host = domain.normalise_request_host(payload.host)

    store = await selectors.resolve_public_store_by_host(
        directory, host, platform_suffix=cfg.platform_host_suffix
    )
    if store is None or not store.is_published:
        # Loud in the log, silent on the wire. The status is here because "a merchant has
        # published nothing" and "a hostname points at us and never will resolve" are two
        # different operational problems behind one identical response, and only one of them is
        # somebody's DNS. The buyer is told neither.
        log.warning(
            "storefront.public.host_unresolved",
            host=host,
            status=store.status if store is not None else None,
        )
        raise NotFoundProblem("No published store at this address.")

    canonical = domain.canonical_host(
        slug=store.slug,
        custom_domain=store.custom_domain,
        custom_domain_status=store.custom_domain_status,
        suffix=cfg.platform_host_suffix,
    )
    # ADR-0012 §11's coordination note: the theme document extends *this* payload rather than
    # adding a second call on the first-paint path. Its own `session_scope`, tenant-bound —
    # nothing about the buyer's request runs on the bypass reader past this point.
    theme_doc = await services.get_theme_for_store(store)
    theme = (
        PublicThemeOut(
            theme_key=theme_doc.theme_key,
            mode=theme_doc.mode,
            tokens=theme_doc.tokens,
            pages=theme_doc.pages,
        )
        if theme_doc is not None
        else None
    )
    return StoreContextOut(
        store=store.slug,
        # Verbatim, all three. Output encoding is the renderer's (ADR-0011 §6).
        name=store.name,
        tagline=store.tagline,
        logo_text=store.logo_text,
        currency=store.currency,
        timezone=store.timezone,
        canonical_host=canonical,
        is_canonical=host == canonical,
        theme=theme,
        checked_at=utcnow(),
    )


@router.post(
    "/storefront/public/catalogue",
    response_model=PublicCatalogueOut,
    summary="A published store's active categories and products",
    responses={
        404: {"description": "No published store with that slug"},
        422: {"description": "That is not a store slug"},
        429: {"description": "Rate limited by the gateway"},
    },
)
async def public_catalogue(
    payload: PublicCatalogueIn,
    directory: DirectorySession,
    params: PageQuery,
) -> PublicCatalogueOut:
    """**No token, no account, no session.** The buyer bundle's catalogue read (gap 4).

    Same three-step shape as `store_context`: resolve the slug on the bypass reader, refuse
    anything that is not a published store with the identical 404, then run every read
    tenant-bound through `session_scope` — never on the bypass role past the lookup. **Only
    `active`, non-deleted, `is_active` rows are ever returned**; a merchant's draft or archived
    line is invisible here regardless of what the buyer asks for.
    """
    store = await selectors.resolve_public_store(directory, payload.store)
    if store is None or not store.is_published:
        log.warning(
            "storefront.public.catalogue_store_unresolved",
            slug=payload.store,
            status=store.status if store is not None else None,
        )
        raise NotFoundProblem("No published store with that name.")

    catalogue = await services.get_public_catalogue_for_store(
        store, params, category_key=payload.category
    )
    categories_out = [
        CategoryPublicOut(
            key=category.key,
            label=category.label,
            blurb=category.blurb,
            image_url=category.image_path,
            line_count=count,
        )
        for category, count in catalogue.categories
    ]
    products_out = [
        ProductPublicOut(
            id=product.id,
            code=product.code,
            name=product.name,
            brand=product.brand,
            category=catalogue.category_keys.get(product.category_id, ""),
            spec=product.spec,
            unit=product.unit,
            price_min_minor=product.price_min_minor,
            price_max_minor=product.price_max_minor,
            currency=store.currency,
            gst_inclusive=product.gst_inclusive,
            moq=product.moq,
            moq_unit=product.moq_unit,
            in_stock=product.in_stock,
            lead_time_days=product.lead_time_days,
            featured=product.featured,
            image_url=product.image_path,
            mrp_minor=product.mrp_minor,
            delivery_eta_minutes=product.delivery_eta_minutes,
            variants=[
                ProductVariantOut.model_validate(v) for v in catalogue.variants.get(product.id, [])
            ],
        )
        for product in catalogue.products
    ]
    return PublicCatalogueOut(
        categories=categories_out,
        products=Page.build(products_out, total=catalogue.products_total, params=params),
    )


@router.post(
    "/storefront/public/enquiry",
    response_model=EnquiryReceiptOut,
    status_code=201,
    summary="Send an enquiry to a published store",
    responses={
        404: {"description": "No published store with that slug"},
        422: {"description": "A required field is missing or malformed"},
        429: {"description": "Rate limited by the gateway"},
    },
)
async def public_enquiry(
    payload: EnquiryCreate,
    directory: DirectorySession,
) -> EnquiryReceiptOut:
    """**No token, no account, no session.** The buyer's line list, sent as an enquiry (gap 6) —
    this product's order-like flow; there is no cart and no checkout to submit instead.

    Contact fields are sealed before they are ever written (`services.create_enquiry_for_store`)
    and an outbox event is staged in the same transaction, so a merchant's notification can
    never fire for an enquiry that failed to commit.
    """
    store = await selectors.resolve_public_store(directory, payload.store)
    if store is None or not store.is_published:
        log.warning(
            "storefront.public.enquiry_store_unresolved",
            slug=payload.store,
            status=store.status if store is not None else None,
        )
        raise NotFoundProblem("No published store with that name.")

    enquiry = await services.create_enquiry_for_store(store, payload)
    return EnquiryReceiptOut(id=enquiry.id, received_at=enquiry.created_at)
