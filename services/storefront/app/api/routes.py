"""The merchant's surface for storefront — store identity and the delivery map.

Fronted by `app-gateway` at `/api/v1/storefront`, **with** a `product` key, so a customer who
has not activated Storefront gets a 402 here. The *public* buyer route is a second, longer
prefix with no product key, so "the buyer surface is not entitlement-gated" is a fact in the
route table rather than a consequence of statement ordering inside the gateway (ADR-0010 §4).

Every route declares its permission and is floored at `minimum=Scope.ORG`. All four permissions
are ceilinged **and** defaulted at ORG in `app/catalogue.py`, so the floor is redundant today
and is written anyway: the ceiling is catalogue data a later edit can widen, and the floor is
code in front of the query (ADR-0010 §9). `require_product("storefront")` is defence in depth
behind the gateway's own gate — a routing-table mistake must not become an entitlement bypass.

Reads go through `selectors`, writes through `services`; a router never builds its own query.
Every route takes the tenant-bound session and opens no transaction of its own.

**Two routes here exist because a wrong zone map is otherwise undetectable.** Nothing in this
design notices that a postal code is in the wrong zone — the answer comes back confident and
wrong. The tester (`POST /delivery/check`) and the demand report (`GET /delivery/demand`) are
the only two instruments, which is why both ship in slice 1 rather than being deferred as
internal tooling (ADR-0010 §13).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.api.public import answer_out
from app.config import settings
from app.models import SCHEMA, CustomDomainClaim, Storefront
from app.schemas import (
    CategoryCreate,
    CategoryOut,
    CategoryPatch,
    DeliveryCheckIn,
    DemandRowOut,
    DnsRecordOut,
    DomainChecksOut,
    DomainCheckStateOut,
    DomainClaimIn,
    DomainDnsOut,
    DomainOut,
    DomainVerificationOut,
    EnquiryOut,
    EnquiryPatch,
    EnquiryRevealed,
    ImportResultOut,
    MerchantServiceabilityOut,
    ProductCreate,
    ProductOut,
    ProductPatch,
    ProductVariantOut,
    ServiceInfo,
    StoreOut,
    StorePut,
    ThemeDocOut,
    ThemeDocPut,
    ZoneCreate,
    ZoneOut,
    ZonePatch,
    ZonePostalCodesOut,
    ZonePostalCodesPut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.cache import enforce_rate_limit
from platform_core.db import get_directory_session, get_tenant_session
from platform_core.errors import NotFoundProblem, PayloadTooLargeProblem
from platform_core.models import utcnow
from platform_core.pagination import Page, PageQuery

router = APIRouter(tags=["storefront"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `storefront` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("storefront"))

#: The BYPASSRLS, SELECT-only directory reader — needed by `PUT /store/domain` for the one
#: cross-tenant read this service ever does on an authenticated route: whether another
#: organization already holds the domain's `www` partner (ADR-0012 §3). Every other route in
#: this file reads and writes tenant-bound only.
DirectorySession = Annotated[AsyncSession, Depends(get_directory_session)]

StoreRead = Annotated[
    ScopeContext, Depends(require("storefront.store.settings.read", minimum=Scope.ORG))
]
StoreWrite = Annotated[
    ScopeContext, Depends(require("storefront.store.settings.write", minimum=Scope.ORG))
]
ZoneRead = Annotated[
    ScopeContext, Depends(require("storefront.delivery.zone.read", minimum=Scope.ORG))
]
ZoneWrite = Annotated[
    ScopeContext, Depends(require("storefront.delivery.zone.write", minimum=Scope.ORG))
]
CatalogueRead = Annotated[
    ScopeContext, Depends(require("storefront.catalogue.product.read", minimum=Scope.ORG))
]
CatalogueWrite = Annotated[
    ScopeContext, Depends(require("storefront.catalogue.product.write", minimum=Scope.ORG))
]
EnquiryRead = Annotated[
    ScopeContext, Depends(require("storefront.sales.enquiry.read", minimum=Scope.ORG))
]
EnquiryWrite = Annotated[
    ScopeContext, Depends(require("storefront.sales.enquiry.write", minimum=Scope.ORG))
]
#: Revealing a real email and phone number. The dangerous permission **and** a second factor —
#: the same pairing `services/customer` requires to unmask a contact's phone.
EnquiryUnmask = Annotated[
    ScopeContext,
    Depends(require("storefront.sales.enquiry.unmask", amr={"otp"}, minimum=Scope.ORG)),
]

#: A refusal ceiling for the CSV body, enforced in this service as well as at the gateway.
#: ADR-0010 §9's arithmetic: about 19,000 rows at roughly 12 bytes each is ~230 KB, comfortably
#: inside the gateway's 2 MiB default, so **no `max_body_bytes` override is needed in the route
#: table**. This is the
#: same number again, because a service must not depend on a gateway to bound its own memory —
#: `bin/api` is reachable from inside the cluster with no gateway in front of it.
MAX_IMPORT_BODY_BYTES = 2 * 1024 * 1024

#: The demand report's default window when the caller names none.
DEFAULT_DEMAND_DAYS = 30
#: 400 rather than 365, so a merchant can compare this September with last September — the same
#: number as the retention sweep ADR-0010 §7 specifies and this slice does not build.
MAX_DEMAND_DAYS = 400


@router.get("/storefront/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ the store


#: Every named refusal the two store-identity routes can produce, written into the OpenAPI
#: document rather than left in prose. **ADR-0010's errata record that an ADR describing a wire
#: format without printing it cost a rebuild**; the same applies to a route's failure modes, and
#: the generated document is the contract a client branches on.
_STORE_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": (
            "A named conflict. `errors[0].code` is one of `slug_immutable` (the store address "
            "cannot be changed), `slug_taken` or `storefront_exists`."
        )
    },
    422: {
        "description": (
            "A rule refused it. `errors[0].code` is one of `name_required`, `name_too_long`, "
            "`name_invalid`, `tagline_too_long`, `tagline_invalid`, `logo_text_too_long`, "
            "`logo_text_invalid`, `timezone_invalid`, or a `value_error` naming "
            "`custom_domain` — that field no longer lives on this payload (ADR-0012 review "
            "finding 2b); use `PUT /storefront/store/domain`."
        )
    },
}


@router.get(
    "/storefront/store",
    response_model=StoreOut,
    summary="The store's identity — name, address, brand and status",
    dependencies=[Entitled],
)
async def read_store(session: SessionDep, ctx: StoreRead) -> StoreOut:
    """`storefront.store.settings.read`, floored at ORG.

    **This is the identity row, not the theme document.** The two collided on this path: the
    SPA's `getStore()` called it expecting a `StoreDoc`. Identity stayed, because it is in the
    published schema and both the delivery admin and the merchant surface read it; the theme
    document moved to `/api/v1/storefront/theme`, which ADR-0011 §3 reserves and the
    storefront-document ADR builds. Nothing serves that path yet.
    """
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet.")
    return StoreOut.model_validate(store)


@router.put(
    "/storefront/store",
    response_model=StoreOut,
    summary="Create or update the store's identity and brand",
    dependencies=[Entitled],
    responses=_STORE_WRITE_ERRORS,
)
async def put_store(
    payload: StorePut, session: SessionDep, ctx: StoreWrite, request: Request
) -> StoreOut:
    """`storefront.store.settings.write`, floored at ORG.

    **This route cannot publish a store.** `status` is not on the payload: a save that could put
    a store in front of the internet as a side effect of editing a tagline is the failure a
    dropdown produces by default, so publishing is `POST /storefront/store/publish` and nothing
    else (ADR-0011 §4). A store created here is always `draft`.

    **The slug is immutable** once the store exists (ADR-0011 §7): send the stored value back
    unchanged, or get a 409 `slug_immutable`. It is required rather than omittable because a
    full-replacement payload that silently ignores a field is worse than one that refuses it.

    **`custom_domain` is not a field on this payload.** ADR-0011 shipped it here first, and a
    merchant could set it with no verification token and no `custom_domain_claims` row behind
    it — a claim the domain sweep could never pick up, and one that skipped the `www`-partner
    collision check `PUT /storefront/store/domain` runs (ADR-0012 review finding 2b). A request
    that still sends it is refused with a 422 naming `PUT /storefront/store/domain` instead.
    """
    store = await services.put_storefront(session, ctx, payload, request=request)
    # `updated_at` carries a SQL `onupdate`, so the ORM expires it after an UPDATE and reading it
    # would emit a lazy SELECT from outside the async driver's context. One explicit round trip
    # here is the house answer (`services/support`), and it means the response's timestamp is the
    # one actually stored rather than the one the row held before the write.
    await session.refresh(store)
    return StoreOut.model_validate(store)


async def _transition(
    session: AsyncSession, ctx: ScopeContext, *, target: str, request: Request
) -> StoreOut:
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet. Create the store first.")
    store = await services.set_store_status(session, ctx, store, target=target, request=request)
    await session.refresh(store)
    return StoreOut.model_validate(store)


#: Shared by both transitions: the only way either fails on a store that exists.
_TRANSITION_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "This organization has no storefront yet."},
    409: {
        "description": (
            "`errors[0].code` is `store_suspended`. A suspended store is suspended by the "
            "platform and cannot be published or unpublished from here."
        )
    },
}


@router.post(
    "/storefront/store/publish",
    response_model=StoreOut,
    summary="Publish the store — make it reachable by anyone",
    dependencies=[Entitled],
    responses=_TRANSITION_ERRORS,
)
async def publish_store(session: SessionDep, ctx: StoreWrite, request: Request) -> StoreOut:
    """`storefront.store.settings.write`, floored at ORG. **The act with consequences.**

    Publishing is what makes the anonymous serviceability check answer for this store, and with
    it makes that merchant's delivery map readable by anyone who knows the slug — a deliberate
    disclosure, accepted because a merchant publishes both on their own website, and not
    reversible once the slug is in circulation (ADR-0010 §4). It is its own endpoint, its own
    audit action and its own control in the UI for exactly that reason.

    Idempotent: publishing a published store returns 200 and changes nothing.

    **This does not put the store on a custom domain.** Nothing in this platform serves one.
    """
    return await _transition(session, ctx, target="published", request=request)


@router.post(
    "/storefront/store/unpublish",
    response_model=StoreOut,
    summary="Unpublish the store — take it back to draft",
    dependencies=[Entitled],
    responses=_TRANSITION_ERRORS,
)
async def unpublish_store(session: SessionDep, ctx: StoreWrite, request: Request) -> StoreOut:
    """`storefront.store.settings.write`, floored at ORG.

    The store goes back to `draft` and the anonymous check 404s for it again — after the slug
    cache's five minutes, which is the same staleness ADR-0010 §13 accepts in the other
    direction. Idempotent.
    """
    return await _transition(session, ctx, target="draft", request=request)


# ------------------------------------------------------------------ the custom domain (ADR-0012)

_DOMAIN_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "This organization has no storefront yet."},
    409: {
        "description": (
            "`errors[0].code` is `custom_domain_taken` — another organization already holds "
            "this domain or its `www` partner. Nothing about that organization is disclosed."
        )
    },
    422: {
        "description": (
            "`errors[0].code` is one of `custom_domain_required`, `custom_domain_invalid`, "
            "`custom_domain_too_long` or `custom_domain_reserved`."
        )
    },
}


def _check_out(
    state: str,
    *,
    claim: CustomDomainClaim | None,
    ok_detail: str | None,
    is_primary_failure: bool,
    not_after: dt.datetime | None = None,
) -> DomainCheckStateOut:
    """One probe's row in the `checks` block. `claim` is `None` exactly when `state` is
    `pending` and nothing has ever run — see `_domain_out`.

    **Only the claim's single stored `last_error_code`/`last_error_detail` pair is shown, and
    only for the probe currently identified as the primary failure** (the first of ownership,
    routing, certificate that is `failing`, recomputed here from the three state columns rather
    than stored a second time). A second, simultaneously-failing probe still reports its own
    `state`, but not its own reason — the schema (ADR-0012 §9) budgets one error pair per claim,
    not one per probe, and a fabricated second reason would be worse than none. Named here
    rather than smoothed over.
    """
    if state == "pending" or claim is None:
        return DomainCheckStateOut(
            state="pending", last_checked_at=None, next_check_at=None, code=None, detail=None
        )
    if state == "ok":
        return DomainCheckStateOut(
            state="ok",
            last_checked_at=claim.last_checked_at,
            next_check_at=claim.next_check_at,
            code=None,
            detail=ok_detail,
            not_after=not_after,
        )
    return DomainCheckStateOut(
        state="failing",
        last_checked_at=claim.last_checked_at,
        next_check_at=claim.next_check_at,
        code=claim.last_error_code if is_primary_failure else None,
        detail=claim.last_error_detail if is_primary_failure else None,
        not_after=not_after,
    )


def _domain_dns_out(host: str, cfg: Any) -> DomainDnsOut:
    """The record table a merchant is told to publish (ADR-0012 §4). `apex` is exactly two
    labels — the same simple, deliberately PSL-free rule `domain.www_partner` uses, because a
    Public Suffix List is a bundled dependency that goes stale on somebody else's schedule."""
    apex = host.count(".") == 1
    if apex:
        preferred = DnsRecordOut(
            record_type="ALIAS",
            name=host,
            value=cfg.edge_host,
            note=(
                "If your DNS provider offers ALIAS, ANAME or CNAME flattening, use this. It "
                "keeps working if our address changes."
            ),
        )
        fallback = [
            DnsRecordOut(record_type="A", name=host, value=cfg.edge_reserved_ip or "(ask support)"),
            DnsRecordOut(
                record_type="A",
                name=domain.www_partner(host),
                value=cfg.edge_reserved_ip or "(ask support)",
            ),
        ]
    else:
        preferred = DnsRecordOut(record_type="CNAME", name=host, value=cfg.edge_host, note=None)
        fallback = []
    return DomainDnsOut(
        apex=apex,
        preferred=preferred,
        fallback=fallback,
        proxy_warning=(
            "If you use Cloudflare, set these records to DNS only (grey cloud). A proxied "
            "record stops us issuing your certificate."
        ),
        also_served=[domain.www_partner(host)],
    )


def _domain_out(store: Storefront, claim: CustomDomainClaim | None, cfg: Any) -> DomainOut:
    platform_host = domain.platform_host_for(store.slug, suffix=cfg.platform_host_suffix)
    canonical = domain.canonical_host(
        slug=store.slug,
        custom_domain=store.custom_domain,
        custom_domain_status=store.custom_domain_status,
        suffix=cfg.platform_host_suffix,
    )
    if store.custom_domain is None:
        return DomainOut(
            custom_domain=None,
            status=None,
            platform_host=platform_host,
            canonical_host=canonical,
            verification=None,
            dns=None,
            checks=None,
        )

    verification = (
        DomainVerificationOut(
            name=domain.verification_txt_name(store.custom_domain),
            value=domain.verification_txt_value(claim.verification_token),
            issued_at=claim.token_issued_at,
            claim_expires_at=claim.claim_expires_at,
        )
        if claim is not None
        else None
    )
    dns = _domain_dns_out(store.custom_domain, cfg)

    checks: DomainChecksOut | None = None
    if claim is not None:
        first_failing = next(
            (
                name
                for name, state in (
                    ("ownership", claim.ownership_state),
                    ("routing", claim.routing_state),
                    ("certificate", claim.certificate_state),
                )
                if state == "failing"
            ),
            None,
        )
        checks = DomainChecksOut(
            ownership=_check_out(
                claim.ownership_state,
                claim=claim,
                ok_detail=f"Found {domain.verification_txt_value(claim.verification_token)}",
                is_primary_failure=first_failing == "ownership",
            ),
            routing=_check_out(
                claim.routing_state,
                claim=claim,
                ok_detail=(
                    f"{store.custom_domain} resolves to {cfg.edge_reserved_ip}"
                    if cfg.edge_reserved_ip
                    else f"{store.custom_domain} resolves to the platform address"
                ),
                is_primary_failure=first_failing == "routing",
            ),
            certificate=_check_out(
                claim.certificate_state,
                claim=claim,
                ok_detail=(
                    f"Valid until {claim.tls_not_after.isoformat()}"
                    if claim.tls_not_after is not None
                    else "The certificate is valid."
                ),
                is_primary_failure=first_failing == "certificate",
                not_after=claim.tls_not_after,
            ),
        )

    return DomainOut(
        custom_domain=store.custom_domain,
        status=store.custom_domain_status,
        platform_host=platform_host,
        canonical_host=canonical,
        verification=verification,
        dns=dns,
        checks=checks,
    )


@router.get(
    "/storefront/store/domain",
    response_model=DomainOut,
    summary="The store's custom domain, its verification record and its three checks",
    dependencies=[Entitled],
)
async def read_domain(session: SessionDep, ctx: StoreRead) -> DomainOut:
    """`storefront.store.settings.read`, floored at ORG.

    No claim — every field but `platform_host` and `canonical_host` is `null`. Claimed — the
    verification record, the DNS table to publish, and the three probes' last results, each
    with its own `next_check_at` so "pending" always has a clock on it (ADR-0012 §6, §11).
    """
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet.")
    claim = await selectors.get_domain_claim(session, ctx)
    return _domain_out(store, claim, settings())


@router.put(
    "/storefront/store/domain",
    response_model=DomainOut,
    summary="Claim a custom domain — mints a fresh verification token",
    dependencies=[Entitled],
    responses=_DOMAIN_WRITE_ERRORS,
)
async def put_domain(
    payload: DomainClaimIn,
    session: SessionDep,
    directory: DirectorySession,
    ctx: StoreWrite,
    request: Request,
) -> DomainOut:
    """`storefront.store.settings.write`, floored at ORG.

    Replacing an existing claim releases the old one and mints a new token — a verification
    proves control of the value claimed, and must not survive that value changing. The response
    is the `GET` body, freshly `unverified`.
    """
    store, claim = await services.claim_custom_domain(
        session, directory, ctx, payload, request=request
    )
    await session.refresh(store)
    await session.refresh(claim)
    return _domain_out(store, claim, settings())


@router.delete(
    "/storefront/store/domain",
    status_code=204,
    summary="Disconnect the custom domain",
    dependencies=[Entitled],
)
async def delete_domain(session: SessionDep, ctx: StoreWrite, request: Request) -> Response:
    """`storefront.store.settings.write`, floored at ORG. **Idempotent**: deleting when there
    is no claim is also a 204 (ADR-0012 §7, §11).

    **From the next request the host 404s, not 410** — a correction to this docstring's own
    former claim (ADR-0012 review finding 9). `POST /public/store-context`, the JSON bootstrap
    call this host actually answers through, returns 404 `not-found` for "no published store at
    this address" exactly as it does for a host nobody ever claimed (§11's own wire format
    already specifies 404 here). A `410 Gone` on a raw, unmatched browser request to the host
    itself is §7's design for the **edge** — a static page Caddy serves with no JSON involved —
    and `infrastructure/edge/caddy/Caddyfile` does not implement that catch-all yet; nothing in
    this service ever returns a 410. The certificate stays in Caddy's storage until it expires
    either way.
    """
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet.")
    await services.release_custom_domain(session, ctx, store, request=request)
    return Response(status_code=204)


@router.post(
    "/storefront/store/domain/check",
    response_model=DomainOut,
    summary='The "Check now" button — runs all three probes synchronously',
    dependencies=[Entitled],
    responses={
        404: {"description": "This organization has no storefront, or no domain is claimed."},
        409: {
            "description": (
                "`errors[0].code` is `domain_check_in_progress` — a probe pass for this domain "
                '(the daily sweep, or another tab\'s "Check now") is already running. Retry in '
                "a few seconds."
            )
        },
        429: {
            "description": (
                "Checks are limited to one every `domain_check_rate_limit_window_seconds` "
                "(30 by default) per organization."
            )
        },
    },
)
async def check_domain(session: SessionDep, ctx: StoreWrite, request: Request) -> DomainOut:
    """`storefront.store.settings.write`, floored at ORG.

    Rate-limited to one call per organization every
    `domain_check_rate_limit_window_seconds` (30 by default) — the merchant has just pasted a
    record and wants an answer now (ADR-0012 §6). Runs synchronously, against the live network,
    so it is not fast; it is not meant to be polled.

    Takes a row lock on the claim before probing (ADR-0012 review finding 6), so this cannot
    race the sweep or a second call into overwriting each other's result — see
    `services.run_domain_check`.
    """
    cfg = settings()
    await enforce_rate_limit(
        f"storefront.domain.check:{ctx.organization_id}",
        limit=1,
        window_seconds=cfg.domain_check_rate_limit_window_seconds,
    )

    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet.")
    claim = await selectors.get_domain_claim(session, ctx)
    if store.custom_domain is None or claim is None:
        raise NotFoundProblem(
            "No domain is claimed for this store yet — connect one with "
            "PUT /storefront/store/domain first."
        )

    await services.run_domain_check(session, ctx, store, claim, request=request)
    await session.refresh(store)
    await session.refresh(claim)
    return _domain_out(store, claim, cfg)


# ------------------------------------------------------------------ zones


def _zone_out(zone: Any, *, postal_code_count: int = 0) -> ZoneOut:
    out = ZoneOut.model_validate(zone)
    return out.model_copy(update={"postal_code_count": postal_code_count})


@router.get(
    "/storefront/delivery/zones",
    response_model=Page[ZoneOut],
    summary="The delivery zones, with how many postal codes each holds",
    dependencies=[Entitled],
)
async def list_zones(session: SessionDep, ctx: ZoneRead, params: PageQuery) -> Page[ZoneOut]:
    """`storefront.delivery.zone.read`, floored at ORG. Paginated, server-capped at 200.

    The counts arrive as one grouped query for the whole page — a per-zone count inside the loop
    would be an N+1 on the screen a merchant opens first.
    """
    rows, total, counts = await selectors.list_zones(session, ctx, params)
    return Page.build(
        [_zone_out(row, postal_code_count=counts.get(row.id, 0)) for row in rows],
        total=total,
        params=params,
    )


@router.post(
    "/storefront/delivery/zones",
    response_model=ZoneOut,
    status_code=201,
    summary="Create a delivery zone",
    dependencies=[Entitled],
)
async def create_zone(
    payload: ZoneCreate, session: SessionDep, ctx: ZoneWrite, request: Request
) -> ZoneOut:
    """`storefront.delivery.zone.write`, floored at ORG.

    `SLOTTED` is refused with a 422 naming slice 2 — the mode exists in the column's check
    constraint from day one so slice 2 is a validation change rather than a schema change, and
    the writer is what holds the line until then.
    """
    zone = await services.create_zone(session, ctx, payload, request=request)
    await session.flush()
    return _zone_out(zone)


@router.patch(
    "/storefront/delivery/zones/{zone_id}",
    response_model=ZoneOut,
    summary="Edit a delivery zone",
    dependencies=[Entitled],
)
async def patch_zone(
    zone_id: uuid.UUID,
    payload: ZonePatch,
    session: SessionDep,
    ctx: ZoneWrite,
    request: Request,
) -> ZoneOut:
    """`storefront.delivery.zone.write`, floored at ORG. Absent leaves a field alone; an explicit
    `null` clears it.

    A zone in another organization is a 404 and never a 403: confirming that an id exists in
    *some* organization is itself a disclosure.
    """
    zone = await selectors.get_zone(session, ctx, zone_id)
    if zone is None:
        raise NotFoundProblem("No such delivery zone")
    zone = await services.update_zone(session, ctx, zone, payload, request=request)
    await session.flush()
    await session.refresh(zone)
    counts = await selectors.postal_code_counts(session, ctx, [zone.id])
    return _zone_out(zone, postal_code_count=counts.get(zone.id, 0))


@router.delete(
    "/storefront/delivery/zones/{zone_id}",
    status_code=204,
    summary="Delete a delivery zone",
    dependencies=[Entitled],
)
async def delete_zone(
    zone_id: uuid.UUID, session: SessionDep, ctx: ZoneWrite, request: Request
) -> Response:
    """`storefront.delivery.zone.write`, floored at ORG.

    **Refused while postal codes are still mapped to it**, with a 422 naming the count. Cascading
    would silently un-serve every one of those areas, and a merchant would find out from a buyer.
    """
    zone = await selectors.get_zone(session, ctx, zone_id)
    if zone is None:
        raise NotFoundProblem("No such delivery zone")
    await services.delete_zone(session, ctx, zone, request=request)
    await session.flush()
    return Response(status_code=204)


# ------------------------------------------------------------------ postal codes


@router.get(
    "/storefront/delivery/zones/{zone_id}/postal-codes",
    response_model=ZonePostalCodesOut,
    summary="Every postal code in one zone",
    dependencies=[Entitled],
)
async def read_zone_postal_codes(
    zone_id: uuid.UUID, session: SessionDep, ctx: ZoneRead
) -> ZonePostalCodesOut:
    """`storefront.delivery.zone.read`, floored at ORG.

    Deliberately unpaginated, and bounded instead by the write: this is one textarea holding one
    list, and a merchant editing a page of their own zone would save the page and silently drop
    the rest. `domain.MAX_POSTAL_CODES_PER_ZONE` is what keeps this finite.
    """
    zone = await selectors.get_zone(session, ctx, zone_id)
    if zone is None:
        raise NotFoundProblem("No such delivery zone")
    codes = await selectors.zone_postal_codes(session, ctx, zone.id)
    return ZonePostalCodesOut(
        zone_id=zone.id, zone_code=zone.code, postal_codes=codes, total=len(codes)
    )


@router.put(
    "/storefront/delivery/zones/{zone_id}/postal-codes",
    response_model=ZonePostalCodesOut,
    summary="Replace the zone's postal codes with this list",
    dependencies=[Entitled],
)
async def put_zone_postal_codes(
    zone_id: uuid.UUID,
    payload: ZonePostalCodesPut,
    session: SessionDep,
    ctx: ZoneWrite,
    request: Request,
) -> ZonePostalCodesOut:
    """`storefront.delivery.zone.write`, floored at ORG.

    Replace, not append: without that a merchant who **shrinks** a zone watches the removal do
    nothing and the old area keep delivering. A code that belongs to another zone is refused
    naming that zone, rather than silently moved — one postal code belongs to exactly one zone.
    """
    zone = await selectors.get_zone(session, ctx, zone_id)
    if zone is None:
        raise NotFoundProblem("No such delivery zone")
    codes = await services.replace_zone_postal_codes(
        session, ctx, zone, payload.postal_codes, request=request
    )
    await session.flush()
    return ZonePostalCodesOut(
        zone_id=zone.id, zone_code=zone.code, postal_codes=sorted(codes), total=len(codes)
    )


#: The published contract for the import body. Written out because the body is `text/csv` read
#: by hand rather than a declared model, and this module is the OpenAPI surface the frontend
#: generates its client from.
_IMPORT_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "text/csv": {
                "schema": {
                    "type": "string",
                    "description": (
                        "Two columns with a header row: `postal_code,zone_code`. Every zone code "
                        "must already exist. A postal code appearing twice is refused with the "
                        "offending line numbers — last-wins on a file you cannot read back is "
                        "the silent wrong answer."
                    ),
                    "example": "postal_code,zone_code\n400050,BANDRA\n400051,BANDRA\n",
                }
            }
        },
    }
}


@router.post(
    "/storefront/delivery/postal-codes/import",
    response_model=ImportResultOut,
    summary="Bulk-import postal codes from a CSV",
    dependencies=[Entitled],
    openapi_extra=_IMPORT_BODY,
)
async def import_postal_codes(
    request: Request,
    session: SessionDep,
    ctx: ZoneWrite,
    mode: Annotated[
        Literal["merge", "replace"],
        Query(
            description=(
                "`merge` upserts each row and leaves everything else alone. `replace` deletes "
                "every postal-code mapping in the organization first — which is what a merchant "
                "re-uploading their master file means, and without it their removals never take "
                "effect."
            )
        ),
    ] = "merge",
) -> ImportResultOut:
    """`storefront.delivery.zone.write`, floored at ORG. **All or nothing, in one transaction.**

    A half-applied import is worse than a failed one, because the merchant cannot tell which
    half. Duplicates are detected in pure code before a single statement runs, and refused with
    the line numbers.

    One audit row carries the mode and five counters — not 19,000 rows. The mode is on the row
    because a `replace` is destructive and a `merge` is not.

    A 19,000-row batched upsert runs in one to three seconds against the gateway's 30-second
    upstream timeout. If a real file ever approaches that, ADR-0010 §9 names the next step in
    order: the import becomes a job with a status row, or the route takes a `max_body_bytes`
    override — and nobody reaches for chunking first.
    """
    raw = await request.body()
    if len(raw) > MAX_IMPORT_BODY_BYTES:
        raise PayloadTooLargeProblem(
            f"That file is larger than the {MAX_IMPORT_BODY_BYTES // (1024 * 1024)} MiB limit."
        )
    counters = await services.import_postal_codes(
        session, ctx, raw.decode("utf-8", errors="replace"), mode=mode, request=request
    )
    await session.flush()
    return ImportResultOut(
        mode=mode,
        rows_read=counters.rows_read,
        inserted=counters.inserted,
        updated=counters.updated,
        deleted=counters.deleted,
    )


# ------------------------------------------------------------------ the tester and the report


@router.post(
    "/storefront/delivery/check",
    response_model=MerchantServiceabilityOut,
    summary="Test a postal code against this store's delivery map",
    dependencies=[Entitled],
)
async def check_postal_code(
    payload: DeliveryCheckIn, session: SessionDep, ctx: ZoneRead
) -> MerchantServiceabilityOut:
    """`storefront.delivery.zone.read`, floored at ORG.

    **The same selector, the same answer shape, plus the matched zone.** It is the second
    concrete caller that justifies the chokepoint, and it is the only way a merchant verifies
    that a 19,000-row import did what they meant: a mis-mapped postal code returns a confident
    wrong answer and nothing else in the system notices.

    No demand row is written. This is the merchant testing their own map, not a buyer asking —
    counting it would poison the one signal the report exists to give.
    """
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem(
            "This organization has no storefront yet, so there is no timezone to answer in. "
            "Create the store first."
        )
    postal_code = domain.validate_postal_code(payload.postal_code)
    answer = await selectors.resolve_serviceability(
        session,
        ctx.organization_id,
        postal_code,
        timezone=store.timezone,
        now=utcnow(),
    )
    base = answer_out(answer, currency=store.currency)
    zone = answer.zone
    return MerchantServiceabilityOut(
        **base.model_dump(),
        zone_id=zone.id if zone is not None else None,
        zone_code=zone.code if zone is not None else None,
        zone_name=zone.name if zone is not None else None,
    )


@router.get(
    "/storefront/delivery/demand",
    response_model=Page[DemandRowOut],
    summary="Which postal codes were asked for, and how often",
    dependencies=[Entitled],
)
async def read_demand(
    session: SessionDep,
    ctx: ZoneRead,
    params: PageQuery,
    since: Annotated[dt.date | None, Query(description="Inclusive. Defaults to 30 days ago.")] = (
        None
    ),
    until: Annotated[dt.date | None, Query(description="Inclusive. Defaults to today.")] = None,
    outcome: Annotated[
        Literal["SERVED", "NO_MAPPING", "EXCLUDED", "ZONE_INACTIVE"] | None,
        Query(description="Filter to one outcome. `NO_MAPPING` is the expansion list."),
    ] = None,
) -> Page[DemandRowOut]:
    """`storefront.delivery.zone.read`, floored at ORG — **no fifth permission** (ADR-0010 §9).

    Nobody has asked for a read-the-demand-but-not-the-zones role, and a fifth permission is a
    fifth thing to deliver. The trade, plainly: anyone who can see the zones can see which
    postal codes were turned away.

    Ordered by count descending, because the merchant's question is "where should I expand
    next", not "what happened most recently". Dates are the **store's** calendar days, so a
    merchant's totals break at their own midnight rather than at UTC's.
    """
    store = await selectors.get_storefront(session, ctx)
    timezone = store.timezone if store is not None else "UTC"
    today = domain.today_in(timezone, now=utcnow())
    until = until or today
    since = since or until - dt.timedelta(days=DEFAULT_DEMAND_DAYS - 1)
    if (until - since).days >= MAX_DEMAND_DAYS:
        raise domain.Rejected(
            "demand_window_too_wide",
            f"Ask for at most {MAX_DEMAND_DAYS} days at a time — that is how long demand is "
            "kept, so a wider window cannot return more.",
            field="since",
        )
    rows, total = await selectors.list_demand(
        session, ctx, params, since=since, until=until, outcome=outcome
    )
    return Page.build(
        [
            DemandRowOut(
                check_date=row.check_date,
                postal_code=row.postal_code,
                outcome=row.outcome,  # the column's check constraint
                checks=row.checks,
            )
            for row in rows
        ],
        total=total,
        params=params,
    )


# ------------------------------------------------------------------ the theme document


@router.get(
    "/storefront/theme",
    response_model=ThemeDocOut,
    summary="The store's theme document — tokens, pages, sections",
    dependencies=[Entitled],
)
async def read_theme(session: SessionDep, ctx: StoreRead) -> ThemeDocOut:
    """`storefront.store.settings.read`, floored at ORG. Reused rather than a new code — ADR-0011
    §3's own note: anyone who can publish the store can edit its theme.

    **404 with a clear detail is the "no theme adopted" state** — the front end maps this to
    "pick a theme" rather than treating it as an error.
    """
    doc = await selectors.get_theme_document(session, ctx)
    if doc is None:
        raise NotFoundProblem(
            "This store has no theme yet. Pick a theme in the gallery to get started."
        )
    return ThemeDocOut.model_validate(doc)


@router.put(
    "/storefront/theme",
    response_model=ThemeDocOut,
    summary="Adopt a theme, or save an edit to the one already adopted",
    dependencies=[Entitled],
    responses={
        409: {"description": "`errors[0].code` is `theme_version_conflict` — reload and retry"},
        422: {"description": "The envelope is invalid or too large — see `errors[0].code`"},
    },
)
async def put_theme(
    payload: ThemeDocPut, session: SessionDep, ctx: StoreWrite, request: Request
) -> ThemeDocOut:
    """`storefront.store.settings.write`, floored at ORG. **This is adopt** — the front end
    forks a theme client-side and PUTs the result; there is no separate `/adopt` route."""
    doc = await services.put_theme_document(session, ctx, payload, request=request)
    await session.refresh(doc)
    return ThemeDocOut.model_validate(doc)


@router.delete(
    "/storefront/theme",
    status_code=204,
    summary="Remove the store's theme document",
    dependencies=[Entitled],
)
async def delete_theme(session: SessionDep, ctx: StoreWrite, request: Request) -> Response:
    """`storefront.store.settings.write`, floored at ORG. "Remove this store" in Gallery — soft
    delete; a later `PUT` adopts fresh, starting again at `version=1`."""
    doc = await selectors.get_theme_document(session, ctx)
    if doc is None:
        raise NotFoundProblem("This store has no theme to remove.")
    await services.delete_theme_document(session, ctx, doc, request=request)
    await session.flush()
    return Response(status_code=204)


# ------------------------------------------------------------------ categories


def _category_out(category: Any, *, line_count: int = 0) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        key=category.key,
        label=category.label,
        blurb=category.blurb,
        image_url=category.image_path,
        sort_order=category.sort_order,
        is_active=category.is_active,
        line_count=line_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


@router.get(
    "/storefront/categories",
    response_model=Page[CategoryOut],
    summary="Catalogue categories",
    dependencies=[Entitled],
)
async def list_categories(
    session: SessionDep,
    ctx: CatalogueRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Key or label")] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> Page[CategoryOut]:
    rows, total, counts = await selectors.list_categories(
        session, ctx, params, q=q, is_active=is_active
    )
    return Page.build(
        [_category_out(row, line_count=counts.get(row.id, 0)) for row in rows],
        total=total,
        params=params,
    )


@router.post(
    "/storefront/categories",
    response_model=CategoryOut,
    status_code=201,
    summary="Create a category",
    dependencies=[Entitled],
)
async def create_category(
    payload: CategoryCreate, session: SessionDep, ctx: CatalogueWrite, request: Request
) -> CategoryOut:
    category = await services.create_category(session, ctx, payload, request=request)
    await session.flush()
    return _category_out(category)


@router.patch(
    "/storefront/categories/{category_id}",
    response_model=CategoryOut,
    summary="Edit a category",
    dependencies=[Entitled],
)
async def patch_category(
    category_id: uuid.UUID,
    payload: CategoryPatch,
    session: SessionDep,
    ctx: CatalogueWrite,
    request: Request,
) -> CategoryOut:
    category = await selectors.get_category(session, ctx, category_id)
    if category is None:
        raise NotFoundProblem("No such category")
    category = await services.update_category(session, ctx, category, payload, request=request)
    await session.flush()
    # `updated_at` carries a SQL `onupdate`, so the ORM expires it after an UPDATE — see
    # `put_store`'s comment above for the full reasoning; same fix, same reason.
    await session.refresh(category)
    counts = await selectors.category_product_counts(session, ctx, [category.id])
    return _category_out(category, line_count=counts.get(category.id, 0))


@router.delete(
    "/storefront/categories/{category_id}",
    status_code=204,
    summary="Delete a category",
    dependencies=[Entitled],
    responses={422: {"description": "The category still has live products in it — named count"}},
)
async def delete_category(
    category_id: uuid.UUID, session: SessionDep, ctx: CatalogueWrite, request: Request
) -> Response:
    """`storefront.catalogue.product.write`, floored at ORG. Soft delete, **refused while a live
    product still carries this category** — the same rule `DELETE /delivery/zones/{id}` applies
    to a zone with postal codes still mapped."""
    category = await selectors.get_category(session, ctx, category_id)
    if category is None:
        raise NotFoundProblem("No such category")
    await services.delete_category(session, ctx, category, request=request)
    await session.flush()
    return Response(status_code=204)


# ------------------------------------------------------------------ products


def _variant_out(variant: Any) -> ProductVariantOut:
    return ProductVariantOut.model_validate(variant)


def _resolved_category_key(categories: dict[uuid.UUID, Any], product: Any) -> str:
    category = categories.get(product.category_id)
    return category.key if category is not None else ""


def _product_out(
    product: Any, *, category_key: str, currency: str, variants: list[Any]
) -> ProductOut:
    return ProductOut(
        id=product.id,
        code=product.code,
        name=product.name,
        brand=product.brand,
        category_id=product.category_id,
        category=category_key,
        spec=product.spec,
        unit=product.unit,
        description=product.description,
        price_min_minor=product.price_min_minor,
        price_max_minor=product.price_max_minor,
        mrp_minor=product.mrp_minor,
        currency=currency,
        gst_inclusive=product.gst_inclusive,
        moq=product.moq,
        moq_unit=product.moq_unit,
        in_stock=product.in_stock,
        lead_time_days=product.lead_time_days,
        delivery_eta_minutes=product.delivery_eta_minutes,
        featured=product.featured,
        image_url=product.image_path,
        status=product.status,
        is_active=product.is_active,
        variants=[_variant_out(v) for v in variants],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


async def _product_out_batch(
    session: AsyncSession, ctx: ScopeContext, products: list[Any], *, currency: str
) -> list[ProductOut]:
    """Resolve category keys and variants for a whole page in two statements — never one per
    row, which is the N+1 this list is most likely to grow."""
    categories = await selectors.categories_by_id(session, ctx, {p.category_id for p in products})
    variants = await selectors.variants_for_products(session, ctx, [p.id for p in products])
    return [
        _product_out(
            p,
            category_key=_resolved_category_key(categories, p),
            currency=currency,
            variants=variants.get(p.id, []),
        )
        for p in products
    ]


async def _store_currency(session: AsyncSession, ctx: ScopeContext) -> str:
    store = await selectors.get_storefront(session, ctx)
    return store.currency if store is not None else "INR"


@router.get(
    "/storefront/products",
    response_model=Page[ProductOut],
    summary="Catalogue products",
    dependencies=[Entitled],
)
async def list_products(
    session: SessionDep,
    ctx: CatalogueRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Code, name or brand")] = None,
    category_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[Literal["draft", "active", "archived"] | None, Query()] = None,
    in_stock: Annotated[bool | None, Query()] = None,
    featured: Annotated[bool | None, Query()] = None,
    sort: Annotated[
        Literal[
            "name",
            "-name",
            "code",
            "-code",
            "price_min_minor",
            "-price_min_minor",
            "updated_at",
            "-updated_at",
        ],
        Query(),
    ] = "-updated_at",
) -> Page[ProductOut]:
    rows, total = await selectors.list_products(
        session,
        ctx,
        params,
        q=q,
        category_id=category_id,
        status=status,
        in_stock=in_stock,
        featured=featured,
        sort=sort,
    )
    currency = await _store_currency(session, ctx)
    items = await _product_out_batch(session, ctx, rows, currency=currency)
    return Page.build(items, total=total, params=params)


@router.get(
    "/storefront/products/{product_id}",
    response_model=ProductOut,
    summary="One product",
    dependencies=[Entitled],
)
async def get_product(product_id: uuid.UUID, session: SessionDep, ctx: CatalogueRead) -> ProductOut:
    product = await selectors.get_product(session, ctx, product_id)
    if product is None:
        raise NotFoundProblem("No such product")
    categories = await selectors.categories_by_id(session, ctx, {product.category_id})
    variants = await selectors.variants_for_products(session, ctx, [product.id])
    currency = await _store_currency(session, ctx)
    return _product_out(
        product,
        category_key=_resolved_category_key(categories, product),
        currency=currency,
        variants=variants.get(product.id, []),
    )


@router.post(
    "/storefront/products",
    response_model=ProductOut,
    status_code=201,
    summary="Create a product",
    dependencies=[Entitled],
    responses={
        422: {
            "description": (
                "`errors[0].code` is `mrp_below_price`, `price_band_invalid` or "
                "`category_not_found`"
            )
        }
    },
)
async def create_product(
    payload: ProductCreate, session: SessionDep, ctx: CatalogueWrite, request: Request
) -> ProductOut:
    product = await services.create_product(session, ctx, payload, request=request)
    await session.flush()
    categories = await selectors.categories_by_id(session, ctx, {product.category_id})
    variants = await selectors.variants_for_products(session, ctx, [product.id])
    currency = await _store_currency(session, ctx)
    return _product_out(
        product,
        category_key=_resolved_category_key(categories, product),
        currency=currency,
        variants=variants.get(product.id, []),
    )


@router.patch(
    "/storefront/products/{product_id}",
    response_model=ProductOut,
    summary="Edit a product — including archive/unarchive via `status`",
    dependencies=[Entitled],
)
async def patch_product(
    product_id: uuid.UUID,
    payload: ProductPatch,
    session: SessionDep,
    ctx: CatalogueWrite,
    request: Request,
) -> ProductOut:
    product = await selectors.get_product(session, ctx, product_id)
    if product is None:
        raise NotFoundProblem("No such product")
    product = await services.update_product(session, ctx, product, payload, request=request)
    await session.flush()
    await session.refresh(product)
    categories = await selectors.categories_by_id(session, ctx, {product.category_id})
    variants = await selectors.variants_for_products(session, ctx, [product.id])
    currency = await _store_currency(session, ctx)
    return _product_out(
        product,
        category_key=_resolved_category_key(categories, product),
        currency=currency,
        variants=variants.get(product.id, []),
    )


@router.delete(
    "/storefront/products/{product_id}",
    status_code=204,
    summary="Delete a product",
    dependencies=[Entitled],
)
async def delete_product(
    product_id: uuid.UUID, session: SessionDep, ctx: CatalogueWrite, request: Request
) -> Response:
    product = await selectors.get_product(session, ctx, product_id)
    if product is None:
        raise NotFoundProblem("No such product")
    await services.delete_product(session, ctx, product, request=request)
    await session.flush()
    return Response(status_code=204)


# ------------------------------------------------------------------ enquiries


def _enquiry_out(enquiry: Any) -> EnquiryOut:
    return EnquiryOut.model_validate(enquiry)


@router.get(
    "/storefront/enquiries",
    response_model=Page[EnquiryOut],
    summary="Enquiries received from buyers",
    dependencies=[Entitled],
)
async def list_enquiries(
    session: SessionDep,
    ctx: EnquiryRead,
    params: PageQuery,
    status: Annotated[Literal["new", "contacted", "won", "lost"] | None, Query()] = None,
    since: Annotated[dt.datetime | None, Query(description="Inclusive, created_at >=")] = None,
    q: Annotated[str | None, Query(description="Name or company")] = None,
) -> Page[EnquiryOut]:
    """`storefront.sales.enquiry.read`, floored at ORG. Contact fields are masked — see
    `POST /storefront/enquiries/{id}:reveal` for the audited, stepped-up read."""
    rows, total = await selectors.list_enquiries(
        session, ctx, params, status=status, since=since, q=q
    )
    return Page.build([_enquiry_out(row) for row in rows], total=total, params=params)


@router.get(
    "/storefront/enquiries/{enquiry_id}",
    response_model=EnquiryOut,
    summary="One enquiry",
    dependencies=[Entitled],
)
async def get_enquiry(enquiry_id: uuid.UUID, session: SessionDep, ctx: EnquiryRead) -> EnquiryOut:
    enquiry = await selectors.get_enquiry(session, ctx, enquiry_id)
    if enquiry is None:
        raise NotFoundProblem("No such enquiry")
    return _enquiry_out(enquiry)


@router.patch(
    "/storefront/enquiries/{enquiry_id}",
    response_model=EnquiryOut,
    summary="Change an enquiry's status",
    dependencies=[Entitled],
    responses={409: {"description": "Illegal transition. Path is new -> contacted -> won or lost"}},
)
async def patch_enquiry(
    enquiry_id: uuid.UUID,
    payload: EnquiryPatch,
    session: SessionDep,
    ctx: EnquiryWrite,
    request: Request,
) -> EnquiryOut:
    """`storefront.sales.enquiry.write`, floored at ORG. Only `status` ever changes — the
    enquiry's content is append-only."""
    enquiry = await selectors.get_enquiry(session, ctx, enquiry_id)
    if enquiry is None:
        raise NotFoundProblem("No such enquiry")
    enquiry = await services.update_enquiry_status(session, ctx, enquiry, payload, request=request)
    await session.flush()
    await session.refresh(enquiry)
    return _enquiry_out(enquiry)


@router.post(
    "/storefront/enquiries/{enquiry_id}:reveal",
    response_model=EnquiryRevealed,
    summary="Reveal an enquiry's real email and phone (audited)",
    dependencies=[Entitled],
)
async def reveal_enquiry(
    enquiry_id: uuid.UUID, session: SessionDep, ctx: EnquiryUnmask, request: Request
) -> EnquiryRevealed:
    """`storefront.sales.enquiry.unmask`, floored at ORG, requires an OTP step-up. A POST rather
    than a GET: this has a side effect — an `entity.sensitive_read` audit row — and must never
    be cached, pre-fetched or replayed from browser history."""
    enquiry = await selectors.get_enquiry(session, ctx, enquiry_id)
    if enquiry is None:
        raise NotFoundProblem("No such enquiry")
    email, phone = await services.unmask_enquiry_contact(session, ctx, enquiry, request=request)
    return EnquiryRevealed(id=enquiry.id, email=email, phone=phone)
