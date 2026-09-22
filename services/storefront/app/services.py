"""Writes for storefront.

Business rules, audit rows and outbox rows. **No outbox rows in slice 1**: ADR-0010 §14
publishes nothing, on ADR-0008 §3's precedent that an event type with no consumer is a contract
kept compatible forever in exchange for nothing.

Two transaction shapes live here, and the difference is the whole of ADR-0010 §4.

**The merchant's writes** take their session from `get_tenant_session`, which opens the
request's one transaction and binds it to the caller's organization as its first statement
(ADR-0002 §1). Nothing in this module opens or commits one of those.

**The anonymous check opens its own**, because there is no principal to open one from. It
resolves the tenant on the bypass reader first — two ids and a status, nothing else — and then
runs *every* subsequent statement inside `session_scope(organization_id=…)` on the ordinary
NOBYPASSRLS role, so row-level security applies to a stranger's request exactly as it does to a
member's. This is `account`'s `POST /invitations/accept` pattern (ADR-0002, "What breaks",
path 4): bypass for the lookup only, then narrow.

**The demand counter is written in the same transaction as the read, on purpose.** The row
returned and the row counted are then the same decision by construction; there is no "recorded
served, returned unserved" drift to debug. If the upsert fails, the whole request fails with a
503 — which the client renders as *could not check*, never as *not served*.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from sqlalchemy import CursorResult, delete, func, insert, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import settings
from app.models import (
    AuditLog,
    Category,
    CustomDomainClaim,
    DeliveryZone,
    Enquiry,
    OutboxEvent,
    Product,
    ProductVariant,
    ServiceabilityDemand,
    Storefront,
    ThemeDocument,
    ZonePostalCode,
)
from app.probes import ProbeSet, default_probe_set
from app.schemas import (
    CategoryCreate,
    CategoryPatch,
    DomainClaimIn,
    EnquiryCreate,
    EnquiryPatch,
    ProductCreate,
    ProductPatch,
    ProductVariantIn,
    StorePut,
    ThemeDocPut,
    ZoneCreate,
    ZonePatch,
)
from app.selectors import PublicStore, ServiceabilityAnswer
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.cache import cache_delete
from platform_core.db import session_scope
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.models import utcnow
from platform_core.pagination import PageParams
from platform_core.pii import open_sealed, seal

log = get_logger("storefront.services")

#: How many offending values a refusal names before it stops listing them. Long enough to fix a
#: paste by hand, short enough that the problem body stays a response rather than a file.
_MAX_NAMED = 20


# --------------------------------------------------------------------- the anonymous check


async def check_serviceability_for_store(
    store: PublicStore,
    postal_code: str,
    *,
    now: dt.datetime | None = None,
) -> ServiceabilityAnswer:
    """Answer one anonymous check, and count it, in one tenant-bound transaction.

    The tenant has already been resolved on the bypass reader by the caller; from here on
    nothing is privileged. `session_scope` binds `app.organization_id` transaction-locally as
    its first statement, so every read and the counter write are policied.

    **There is no `except` in this function and there must never be one.** A failure here is a
    503 that the client renders as "couldn't check". Converting it into an answer would produce
    the single worst outcome in the design: telling a buyer in a served area that the shop does
    not reach them (ADR-0010 §13).

    The returned answer carries a `DeliveryZone` that outlives this transaction. That is safe
    and not accidental: the platform's sessionmaker sets `expire_on_commit=False`, so the loaded
    columns stay loaded after the commit and the route serialises them without a second query.
    """
    now = now or utcnow()
    async with session_scope(organization_id=store.organization_id) as session:
        answer = await selectors.resolve_serviceability(
            session,
            store.organization_id,
            postal_code,
            timezone=store.timezone,
            now=now,
        )
        await record_demand(
            session,
            store.organization_id,
            check_date=domain.today_in(store.timezone, now=now),
            postal_code=postal_code,
            outcome=answer.outcome,
        )
    return answer


async def record_demand(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    check_date: dt.date,
    postal_code: str,
    outcome: str,
) -> None:
    """`INSERT … ON CONFLICT DO UPDATE SET checks = checks + 1`. One statement, one row.

    **A row per check was rejected for a security reason rather than a storage one**: the public
    endpoint is an unauthenticated write, so a row-per-check table is a free way for any stranger
    to grow a merchant's table without bound, throttled only by a per-IP limit a botnet does not
    respect. A counter is bounded by the number of days, distinct postal codes and outcomes
    rather than by traffic — a few hundred rows per merchant per day at the absolute worst
    (ADR-0010 §7).

    This is also the component that saturates first, and specifically **one hot row**: every
    check for a merchant's busiest postal code serialises on it. The named fix, deferred with
    its trigger (a few hundred writes per second on one row), is to buffer counts in Redis and
    flush them per minute from `bin/cron`.
    """
    stmt = (
        pg_insert(ServiceabilityDemand)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            check_date=check_date,
            postal_code=postal_code,
            outcome=outcome,
            checks=1,
        )
        .on_conflict_do_update(
            constraint="uq_serviceability_demand_org_date_postal_outcome",
            # `updated_at`'s `onupdate=func.now()` is a Core/ORM update default; `set_` is taken
            # literally and does not consult it, so without this line the column would keep its
            # insert-time value across every later increment.
            set_={"checks": ServiceabilityDemand.checks + 1, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)


# --------------------------------------------------------------------- the store


#: Which unique constraint fired, and what the merchant should be told about it. The platform's
#: own `IntegrityError` handler turns any of these into an anonymous 409 — right for a surprise,
#: useless for an expectable collision, because it names no field and a client cannot branch on
#: it. These three are expectable, so each gets a name (ADR-0011 §5).
_NAMED_CONFLICTS: dict[str, tuple[str, str, str]] = {
    "uq_storefronts_slug": (
        "slug_taken",
        "slug",
        "That store address is already in use. Store addresses are unique across the platform — "
        "pick another.",
    ),
    "uq_storefronts_custom_domain": (
        "custom_domain_taken",
        "custom_domain",
        "That domain has already been claimed. A domain can be claimed by one store at a time; "
        "if it is yours, contact support — nothing here can prove ownership, which is why this "
        "is settled by a person.",
    ),
    "uq_storefronts_organization_id": (
        "storefront_exists",
        "slug",
        "This organization already has a storefront. Reload and edit the one that exists.",
    ),
}


def _named_conflict(exc: IntegrityError) -> ConflictProblem | None:
    """Turn a unique violation into a 409 that names the field, or return None to re-raise.

    asyncpg carries `constraint_name` on the original exception; SQLAlchemy wraps it one or two
    layers deep depending on the driver adapter, so both are checked and the statement text is
    the last resort. An unrecognised constraint is deliberately **not** translated — it falls
    through to the platform handler's anonymous 409 rather than being given a name this module
    invented.
    """
    orig: Any = exc.orig
    name = getattr(orig, "constraint_name", None) or getattr(
        getattr(orig, "__cause__", None), "constraint_name", None
    )
    if name is None:
        rendered = str(orig)
        name = next((key for key in _NAMED_CONFLICTS if key in rendered), None)
    if name is None:
        return None
    named = _NAMED_CONFLICTS.get(str(name))
    if named is None:
        return None
    code, field, message = named
    return domain.Conflicted(code, message, field=field)


@asynccontextmanager
async def _conflicts_are_named(session: AsyncSession) -> AsyncIterator[None]:
    """Run the store write inside a savepoint, so a collision is a named 409 and not a 500.

    The savepoint is what keeps the request's own transaction usable after the violation: the
    statement that fired the constraint is rolled back to the savepoint, and the 409 is raised
    from clean ground rather than from an aborted transaction that would fail again on the next
    statement anything happened to run.

    **The check is the constraint, not a SELECT first.** A "is this domain taken?" query before
    the write would be a cross-tenant read — the colliding row belongs to another organization
    and row-level security correctly hides it — and it would still race with a concurrent claim.
    The unique index is the arbiter; this only gives its refusal a name.
    """
    try:
        async with session.begin_nested():
            yield
    except IntegrityError as exc:
        conflict = _named_conflict(exc)
        if conflict is None:
            raise
        raise conflict from exc


def _identity_snapshot(store: Storefront) -> dict[str, Any]:
    """What the audit trail records before and after a store write."""
    return {
        "slug": store.slug,
        "name": store.name,
        "tagline": store.tagline,
        "logo_text": store.logo_text,
        "status": store.status,
        "currency": store.currency,
        "timezone": store.timezone,
        "custom_domain": store.custom_domain,
        "custom_domain_status": store.custom_domain_status,
    }


async def _forget_slug(*slugs: str | None) -> None:
    """Drop the cached slug→store entries for these slugs.

    Best effort, and honestly racy: the request's transaction has not committed yet, so a
    concurrent anonymous check could repopulate an entry from the pre-commit row and keep it for
    another TTL. The TTL bounds it either way — this only shortens the window ADR-0010 §13
    already accepts ("a newly published store may be 404 for five minutes").
    """
    keys = {selectors.slug_cache_key(s) for s in slugs if s}
    if keys:
        await cache_delete(*keys)


async def _forget_host(*domains: str | None) -> None:
    """Drop the cached host→store entries for these domains **and their `www` partners**.

    ADR-0012 §2: every transition that changes what a host resolves to deletes the host key in
    the same transaction that writes the state. Without it, releasing a domain leaves it serving
    a store for the length of the TTL, and *claiming* one leaves the negative entry a previous
    buyer's 404 wrote.

    Both members of the pair, because the lookup caches under the key it was *asked* about: a
    buyer who arrived at `www.merchant.com` while the claim was on the apex has an entry under
    the `www` key, and forgetting only the claimed value would leave it.

    The platform host is not here on purpose — that branch reuses the slug key, which
    `_forget_slug` already owns. One host, one invalidation, no key that two functions both
    half-remember.
    """
    hosts = {h for value in domains if value for h in (value, domain.www_partner(value))}
    if hosts:
        await cache_delete(*(selectors.host_cache_key(h) for h in hosts))


async def put_storefront(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: StorePut,
    *,
    request: Request | None = None,
) -> Storefront:
    """Create or update the organization's single storefront — its **identity**.

    Three rules live here rather than in the schema, because each is about the row rather than
    about the payload (ADR-0011):

    * **The brand is trimmed and bounded before it is stored.** It is merchant-authored text
      destined for a public page. It is *not* escaped here — output encoding is the renderer's.
    * **The slug cannot change.** Every published link, the buyer's `localStorage` key and the
      five-minute slug cache all key on it.
    * **A custom domain is a stated intention.** Claiming one writes `custom_domain_status =
      'unverified'` and nothing else happens: no request is served on it, and nothing in this
      platform checks that the merchant owns it. Changing it resets the status, because a
      verification that ever exists must not survive the value it verified.

    **`status` is not on this payload at all**, so a settings save cannot put a store in front
    of the internet. `publish_storefront` / `unpublish_storefront` are the only ways it moves.
    """
    # Refuse a timezone the server cannot resolve, in the merchant's words rather than as a
    # 500 six weeks later: a bad IANA name would silently produce promised dates in UTC, an
    # answer that is wrong by hours and looks perfectly well-formed.
    domain.today_in(payload.timezone, now=utcnow())

    name = domain.clean_brand_text(payload.name, field="name", required=True)
    tagline = domain.clean_brand_text(payload.tagline, field="tagline")
    logo_text = domain.clean_brand_text(payload.logo_text, field="logo_text")
    custom_domain = domain.normalise_custom_domain(payload.custom_domain)

    existing = await selectors.get_storefront(session, ctx)
    before: dict[str, Any] | None = None

    if existing is None:
        store = Storefront(
            organization_id=ctx.organization_id,
            slug=payload.slug,
            name=name or "",
            tagline=tagline,
            logo_text=logo_text,
            # **A store is born `draft`, always.** Publishing is a deliberate, named act, and a
            # creation payload that could arrive published would be the dropdown by another
            # route (ADR-0011 §4).
            status="draft",
            currency=payload.currency,
            timezone=payload.timezone,
            custom_domain=custom_domain,
            custom_domain_status="unverified" if custom_domain else None,
        )
        session.add(store)
        action = "storefront.created"
    else:
        store = existing
        domain.assert_slug_is_unchanged(store.slug, payload.slug)
        before = _identity_snapshot(store)
        store.name = name or ""
        store.tagline = tagline
        store.logo_text = logo_text
        store.currency = payload.currency
        store.timezone = payload.timezone
        if custom_domain != store.custom_domain:
            store.custom_domain = custom_domain
            # A verification attaches to a value, not to a row. The day one exists, keeping it
            # across a change of domain would mean a merchant verifying one name and serving
            # another — so the reset is written now, while it costs a line.
            store.custom_domain_status = "unverified" if custom_domain else None
        action = "storefront.updated"

    domain_changed = before is None or before["custom_domain"] != store.custom_domain
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="storefront",
        entity_id=store.id,
        before=before,
        after=_identity_snapshot(store),
        request=request,
    )
    if domain_changed and (store.custom_domain or (before and before["custom_domain"])):
        # Its own row: a domain claim is the one field here that can be contested between two
        # organizations, and support adjudicating one needs to see who claimed what and when
        # without reading it out of a generic update's `after` blob.
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action=(
                "storefront.custom_domain.claimed"
                if store.custom_domain
                else "storefront.custom_domain.released"
            ),
            entity="storefront",
            entity_id=store.id,
            before={"custom_domain": before["custom_domain"]} if before else None,
            after={
                "custom_domain": store.custom_domain,
                "custom_domain_status": store.custom_domain_status,
                # Written into the trail rather than inferred later: at the time of this claim,
                # nothing in the platform could verify or serve it.
                "verified": False,
                "served": False,
            },
            request=request,
        )

    async with _conflicts_are_named(session):
        if domain_changed:
            # **The seam between this route and ADR-0012's dedicated domain route.** A claim
            # row (`custom_domain_claims`) is evidence for *one specific* domain string — a
            # token minted for `merchant.com`, an `ownership_state` that says its TXT record
            # was seen. None of that survives this field changing to a different string
            # through this older, evidence-free path: without clearing it, a claim already
            # `verified` for one domain would silently keep answering `verified` for whatever
            # this route just set the column to, for a domain nobody has ever proved.
            # `custom_domain_status` is already reset to `unverified` above for the same
            # reason; this is that reset's other half. Inside the savepoint, alongside the
            # flush, so a collision on the store's own write is still what names the 409 —
            # `session.execute` autoflushes, and a flush outside this context would raise the
            # collision before `_conflicts_are_named`'s `except` could see it.
            await session.execute(
                delete(CustomDomainClaim).where(
                    CustomDomainClaim.organization_id == ctx.organization_id
                )
            )
        await session.flush()

    await _forget_slug(store.slug)
    # Both the value being claimed and the one being released: a change has to stop the old
    # domain serving *and* clear the negative entry that made the new one look unclaimed.
    await _forget_host(store.custom_domain, before["custom_domain"] if before else None)
    log.info(
        "storefront.store.saved",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
        action=action,
        status=store.status,
        custom_domain_claimed=bool(store.custom_domain),
    )
    return store


async def set_store_status(
    session: AsyncSession,
    ctx: ScopeContext,
    store: Storefront,
    *,
    target: str,
    request: Request | None = None,
) -> Storefront:
    """`draft ⇄ published`, and nothing else — the one transition a merchant may drive.

    **This is the act that makes a store reachable by strangers**, and with it that merchant's
    delivery map readable by anyone who knows the slug (ADR-0010 §4). It is a named transition
    rather than a field on the settings form precisely so it cannot happen as a side effect of
    saving a tagline.

    `suspended` is refused in both directions by `domain.assert_publishable` /
    `assert_unpublishable`: it belongs to the entitlement-lapse consumer ADR-0010 §4 describes
    and does not own, and a merchant who could leave it would make that mechanism advisory.

    Idempotent: publishing a published store is a no-op that still returns 200, because a
    double-click on the one control in this product that has consequences should not be an
    error.
    """
    if target == "published":
        domain.assert_publishable(store.status)
    else:
        domain.assert_unpublishable(store.status)

    if store.status == target:
        return store

    before = _identity_snapshot(store)
    store.status = target
    action = "storefront.published" if target == "published" else "storefront.unpublished"
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="storefront",
        entity_id=store.id,
        before=before,
        after=_identity_snapshot(store),
        request=request,
    )
    await session.flush()
    # Publishing has to clear the cache, and the *negative* entry is why: a 404 for this slug is
    # cached for five minutes exactly like a hit, so without this a merchant publishes and then
    # watches their own store stay invisible with nothing to show for it.
    await _forget_slug(store.slug)
    # And the host entry, for the same reason at the other address: `status` is on the cached
    # row, so unpublishing a store that is live on its own domain has to stop that domain
    # serving too. Both addresses answer, so both caches move together (ADR-0012 §2, §8).
    await _forget_host(store.custom_domain)
    log.info(
        "storefront.store.status_changed",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
        action=action,
        status=store.status,
    )
    return store


# --------------------------------------------------------------------- the custom domain claim


async def custom_domain_claimed_by_other_org(
    directory_session: AsyncSession, host: str, *, excluding_organization_id: uuid.UUID
) -> bool:
    """Whether **some other organization** already holds `host` (ADR-0012 §3's `www`-partner
    refusal). Runs on the BYPASSRLS SELECT-only directory reader — the exact value's own
    collision is already enforced by `uq_storefronts_custom_domain`; this is the one collision
    no unique index can express, because the apex and its `www` sibling are two different
    strings. Uncached, deliberately: this runs once per write, never on the buyer's path, and a
    cached negative here would let a just-claimed `www` partner slip through for a TTL.
    """
    row = (
        await directory_session.execute(
            select(Storefront.id).where(
                Storefront.custom_domain == host,
                Storefront.organization_id != excluding_organization_id,
                Storefront.deleted_at.is_(None),
            )
        )
    ).first()
    return row is not None


async def claim_custom_domain(
    session: AsyncSession,
    directory_session: AsyncSession,
    ctx: ScopeContext,
    payload: DomainClaimIn,
    *,
    request: Request | None = None,
) -> tuple[Storefront, CustomDomainClaim]:
    """`PUT /storefront/store/domain` (ADR-0012 §3, §11). Mints a fresh token, replaces any
    existing claim row, and leaves `storefronts.custom_domain_status` at `unverified` — exactly
    where a fresh claim through `StorePut` leaves it, so the two write paths cannot disagree
    about what a brand-new claim looks like.

    **Replacing an existing claim releases the old one and mints a new token** (ADR-0012 §11):
    a verification proves control of the value claimed, and must not survive that value
    changing — the same rule `put_storefront` enforces for the older path.
    """
    host = domain.normalise_custom_domain(payload.custom_domain)
    if host is None:
        raise domain.Rejected(
            "custom_domain_required", "Enter a domain to claim.", field="custom_domain"
        )

    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet. Create the store first.")

    partner = domain.www_partner(host)
    if await custom_domain_claimed_by_other_org(
        directory_session, partner, excluding_organization_id=ctx.organization_id
    ):
        raise domain.Conflicted(
            "custom_domain_taken",
            f"{partner} is already connected to another OpsGrids store, and {host} is served "
            "as its `www` pair. If it is yours, disconnect it there first, or contact support.",
            field="custom_domain",
        )

    before_domain = store.custom_domain
    now = utcnow()
    token = domain.mint_verification_token()

    async with _conflicts_are_named(session):
        store.custom_domain = host
        store.custom_domain_status = "unverified"
        # Its own row, so a re-claim of the same string is still a fresh token and a fresh
        # evidence row — whatever the previous claim (of this domain, or of a domain since
        # changed away from) had accumulated does not survive.
        await session.execute(
            delete(CustomDomainClaim).where(
                CustomDomainClaim.organization_id == ctx.organization_id
            )
        )
        claim = CustomDomainClaim(
            organization_id=ctx.organization_id,
            storefront_id=store.id,
            verification_token=token,
            token_issued_at=now,
            claim_expires_at=domain.claim_expires_at(now),
            # Due immediately: the sweep's own poll interval is the only delay before the first
            # real check, rather than this claim also waiting out its own first backoff step.
            next_check_at=now,
        )
        session.add(claim)
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="storefront.domain.claimed",
        entity="storefront",
        entity_id=store.id,
        # The token is not in the row (ADR-0012 §15).
        before={"custom_domain": before_domain},
        after={"custom_domain": host},
        request=request,
    )
    await _forget_host(host, before_domain)
    log.info(
        "storefront.domain.claimed",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
    )
    return store, claim


async def release_custom_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    store: Storefront,
    *,
    request: Request | None = None,
) -> None:
    """`DELETE /storefront/store/domain` (ADR-0012 §7, §11). **Idempotent**: releasing an
    already-unclaimed store is a no-op, because a client that retries a 204 must see another
    204, not a 404 for something that is not there to be missing.

    Clears the identity row, deletes the evidence row, invalidates the cache and audits — in
    one transaction, so a reader never sees a domain with a status and no evidence, or evidence
    for a domain that is already gone.
    """
    if store.custom_domain is None:
        return

    before_domain = store.custom_domain
    store.custom_domain = None
    store.custom_domain_status = None
    await session.execute(
        delete(CustomDomainClaim).where(CustomDomainClaim.organization_id == ctx.organization_id)
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="storefront.domain.released",
        entity="storefront",
        entity_id=store.id,
        before={"custom_domain": before_domain},
        after={"custom_domain": None},
        request=request,
    )
    await _forget_host(before_domain)
    log.info(
        "storefront.domain.released",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
    )


@dataclass(frozen=True, slots=True)
class ClaimAdvanceOutcome:
    """What one probe pass did to a claim, returned so the caller decides what to audit — the
    merchant's "Check now" audits every call; the sweep audits only an actual transition."""

    old_status: str
    new_status: str


async def advance_domain_claim(
    store: Storefront,
    claim: CustomDomainClaim,
    *,
    probes: ProbeSet,
    now: dt.datetime,
) -> ClaimAdvanceOutcome:
    """Run the probes this claim's current status calls for, and mutate `store` and `claim` in
    place. **Never commits, never audits, never touches the cache** — those are the caller's,
    because a synchronous "Check now" and a sweep tick need different amounts of each (ADR-0012
    §6, §15).

    While `unverified`, only the ownership probe runs: the certificate cannot be authorized
    before ownership is proved, and a routing check that cannot yet matter to a merchant tells
    them nothing they can act on (§6's wire example shows routing and certificate `pending` for
    the whole of that state). Once past `unverified`, every probe runs and the row's own
    per-probe error is whichever failed first, in ownership → routing → certificate order.
    """
    old_status = store.custom_domain_status
    host = store.custom_domain
    if old_status is None or host is None:  # pragma: no cover - callers already guard this
        raise RuntimeError("advance_domain_claim called on a store with no claim")

    claim.last_checked_at = now

    if old_status == "unverified":
        ownership = await probes.ownership.check(host, token=claim.verification_token)
        claim.ownership_state = "ok" if ownership.ok else "failing"
        if ownership.ok:
            claim.verified_at = claim.verified_at or now
            claim.last_error_code = None
            claim.last_error_detail = None
            new_status = "verified"
            claim.next_check_at = now + domain.VERIFIED_CHECK_INTERVAL
        else:
            claim.last_error_code = ownership.code
            claim.last_error_detail = ownership.detail
            new_status = "unverified"
            claim.next_check_at = now + domain.unverified_check_interval(
                now - claim.token_issued_at
            )
        store.custom_domain_status = new_status
        return ClaimAdvanceOutcome(old_status=old_status, new_status=new_status)

    ownership = await probes.ownership.check(host, token=claim.verification_token)
    routing = await probes.routing.check(host)
    certificate = await probes.certificate.check(host)

    claim.ownership_state = "ok" if ownership.ok else "failing"
    claim.routing_state = "ok" if routing.ok else "failing"
    claim.certificate_state = "ok" if certificate.ok else "failing"
    if certificate.ok and certificate.not_after is not None:
        claim.tls_not_after = certificate.not_after
        claim.tls_last_ok_at = now

    failing = next((r for r in (ownership, routing, certificate) if not r.ok), None)
    claim.last_error_code = failing.code if failing else None
    claim.last_error_detail = failing.detail if failing else None

    advance = domain.advance_verified_or_later(
        store_published=store.status == "published",
        ownership_ok=ownership.ok,
        routing_ok=routing.ok,
        certificate_ok=certificate.ok,
        consecutive_failures=claim.consecutive_failures,
    )
    claim.consecutive_failures = advance.consecutive_failures
    if advance.status == "live" and old_status != "live":
        claim.live_since = now
    store.custom_domain_status = advance.status
    claim.next_check_at = now + domain.VERIFIED_CHECK_INTERVAL
    return ClaimAdvanceOutcome(old_status=old_status, new_status=advance.status)


async def run_domain_check(
    session: AsyncSession,
    ctx: ScopeContext,
    store: Storefront,
    claim: CustomDomainClaim,
    *,
    probes: ProbeSet | None = None,
    request: Request | None = None,
) -> CustomDomainClaim:
    """`POST /storefront/store/domain/check` (ADR-0012 §6). Runs all three probes synchronously
    and audits the call itself — `storefront.domain.checked`, the one probe run this ADR audits
    (§15), because a rate-limited "Check now" is a human act and 150 unattended probes a day of
    a published fact is not.
    """
    now = utcnow()
    outcome = await advance_domain_claim(
        store, claim, probes=probes or default_probe_set(), now=now
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="storefront.domain.checked",
        entity="storefront",
        entity_id=store.id,
        after={"status": outcome.new_status, "last_error_code": claim.last_error_code},
        request=request,
    )
    if outcome.old_status != outcome.new_status:
        await _forget_host(store.custom_domain)
    log.info(
        "storefront.domain.checked",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
        status=outcome.new_status,
    )
    return claim


# --------------------------------------------------------------------- zones


def _promise_input(source: ZoneCreate | dict[str, Any]) -> domain.PromiseInput:
    if isinstance(source, ZoneCreate):
        return domain.PromiseInput(
            kind=source.promise_kind,
            minutes=source.promise_minutes,
            offset_days=source.promise_offset_days,
            cutoff=source.promise_cutoff_time,
        )
    return domain.PromiseInput(
        kind=source.get("promise_kind"),
        minutes=source.get("promise_minutes"),
        offset_days=source.get("promise_offset_days"),
        cutoff=source.get("promise_cutoff_time"),
    )


async def create_zone(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ZoneCreate,
    *,
    request: Request | None = None,
) -> DeliveryZone:
    """Create a zone. `SLOTTED` is refused here, by name, until slice 2."""
    domain.assert_mode_is_available(payload.mode)
    domain.validate_promise(payload.mode, _promise_input(payload))

    if await selectors.get_zone_by_code(session, ctx, payload.code) is not None:
        raise ConflictProblem(f"A zone with the code {payload.code!r} already exists.")

    zone = DeliveryZone(
        organization_id=ctx.organization_id,
        code=payload.code,
        name=payload.name,
        mode=payload.mode,
        promise_kind=payload.promise_kind,
        promise_minutes=payload.promise_minutes,
        promise_offset_days=payload.promise_offset_days,
        promise_cutoff_time=payload.promise_cutoff_time,
        delivery_fee_minor=payload.delivery_fee_minor,
        free_delivery_above_minor=payload.free_delivery_above_minor,
        min_order_minor=payload.min_order_minor,
        is_active=payload.is_active,
        buyer_note=payload.buyer_note,
    )
    session.add(zone)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="delivery.zone.created",
        entity="delivery_zone",
        entity_id=zone.id,
        after=_zone_snapshot(zone),
        request=request,
    )
    log.info(
        "storefront.zone.created",
        organization_id=str(ctx.organization_id),
        zone_id=str(zone.id),
        mode=zone.mode,
    )
    return zone


async def update_zone(
    session: AsyncSession,
    ctx: ScopeContext,
    zone: DeliveryZone,
    payload: ZonePatch,
    *,
    request: Request | None = None,
) -> DeliveryZone:
    """Partial edit. Absent leaves a field alone; an explicit `null` clears it."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return zone

    before = _zone_snapshot(zone)
    merged = {**before, **changes}
    mode = str(merged["mode"])
    domain.assert_mode_is_available(mode)
    domain.validate_promise(mode, _promise_input(merged))

    new_code = changes.get("code")
    if new_code is not None and new_code != zone.code:
        clash = await selectors.get_zone_by_code(session, ctx, str(new_code))
        if clash is not None:
            raise ConflictProblem(f"A zone with the code {new_code!r} already exists.")

    for field, value in changes.items():
        setattr(zone, field, value)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="delivery.zone.updated",
        entity="delivery_zone",
        entity_id=zone.id,
        before=before,
        after=_zone_snapshot(zone),
        request=request,
    )
    log.info(
        "storefront.zone.updated",
        organization_id=str(ctx.organization_id),
        zone_id=str(zone.id),
        fields=sorted(changes),
    )
    return zone


async def delete_zone(
    session: AsyncSession,
    ctx: ScopeContext,
    zone: DeliveryZone,
    *,
    request: Request | None = None,
) -> None:
    """Remove a zone. **Refused while postal codes are still mapped to it.**

    Cascading would silently un-serve every one of those areas, and a merchant finding that out
    from a buyer is exactly the failure this module exists to prevent. The foreign key is
    `ON DELETE RESTRICT` as the net under this check.

    The row is removed for real rather than soft-deleted: `zone_postal_codes` carries
    `UNIQUE (organization_id, postal_code)` over live and dead rows alike, so a soft-deleted
    graph would keep occupying postal codes nobody can see.
    """
    mapped = (
        await session.execute(
            select(func.count())
            .select_from(ZonePostalCode)
            .where(
                ZonePostalCode.organization_id == ctx.organization_id,
                ZonePostalCode.zone_id == zone.id,
            )
        )
    ).scalar_one()
    if mapped:
        raise BusinessRuleProblem(
            f"That zone still has {mapped} postal code(s) mapped to it. Move them to another "
            "zone or clear the list first — deleting the zone would stop serving them with no "
            "other sign."
        )

    snapshot = _zone_snapshot(zone)
    await session.delete(zone)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="delivery.zone.deleted",
        entity="delivery_zone",
        entity_id=zone.id,
        before=snapshot,
        request=request,
    )
    log.info(
        "storefront.zone.deleted",
        organization_id=str(ctx.organization_id),
        zone_id=str(zone.id),
    )


def _zone_snapshot(zone: DeliveryZone) -> dict[str, Any]:
    """What the audit trail records before and after — mode, promise and fees (ADR-0010 §14)."""
    return {
        "code": zone.code,
        "name": zone.name,
        "mode": zone.mode,
        "promise_kind": zone.promise_kind,
        "promise_minutes": zone.promise_minutes,
        "promise_offset_days": zone.promise_offset_days,
        "promise_cutoff_time": zone.promise_cutoff_time,
        "delivery_fee_minor": zone.delivery_fee_minor,
        "free_delivery_above_minor": zone.free_delivery_above_minor,
        "min_order_minor": zone.min_order_minor,
        "is_active": zone.is_active,
        "buyer_note": zone.buyer_note,
    }


# --------------------------------------------------------------------- postal codes


@dataclass(frozen=True, slots=True)
class ImportCounters:
    rows_read: int
    inserted: int
    updated: int
    deleted: int


async def replace_zone_postal_codes(
    session: AsyncSession,
    ctx: ScopeContext,
    zone: DeliveryZone,
    raw: str,
    *,
    request: Request | None = None,
) -> list[str]:
    """The textarea path: this list becomes the zone's complete set of postal codes.

    Replace rather than append, for the reason the CSV import has a `replace` mode at all —
    without it a merchant who **shrinks** a zone watches the removal do nothing and the old area
    keep delivering.

    A code that belongs to another zone is **refused, naming that zone**, rather than quietly
    moved. One postal code maps to exactly one zone per organization, and silently reassigning
    an area because it was typed into a second box is the kind of change a merchant discovers
    from a customer.
    """
    codes = domain.parse_postal_code_list(raw)

    # One statement, not one per code: the conflicting rows across the whole submitted list.
    clashes = (
        (
            await session.execute(
                select(ZonePostalCode.postal_code, DeliveryZone.code)
                .join(DeliveryZone, DeliveryZone.id == ZonePostalCode.zone_id)
                .where(
                    ZonePostalCode.organization_id == ctx.organization_id,
                    ZonePostalCode.postal_code.in_(codes),
                    ZonePostalCode.zone_id != zone.id,
                )
                .order_by(ZonePostalCode.postal_code.asc())
            )
        ).all()
        if codes
        else []
    )
    if clashes:
        named = ", ".join(f"{code} (zone {zone_code})" for code, zone_code in clashes[:_MAX_NAMED])
        raise ConflictProblem(
            f"{len(clashes)} postal code(s) already belong to another zone: {named}. "
            "Remove them there first — a postal code belongs to exactly one zone."
        )

    # `session.execute` is typed to return the generic `Result`, but a DELETE always produces a
    # `CursorResult` at runtime — which is what actually carries `rowcount`.
    removed = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(ZonePostalCode).where(
                ZonePostalCode.organization_id == ctx.organization_id,
                ZonePostalCode.zone_id == zone.id,
            )
        ),
    ).rowcount
    if codes:
        await session.execute(
            insert(ZonePostalCode),
            [
                {
                    "id": uuid.uuid4(),
                    "organization_id": ctx.organization_id,
                    "zone_id": zone.id,
                    "postal_code": code,
                }
                for code in codes
            ],
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="delivery.postal_codes.replaced",
        entity="delivery_zone",
        entity_id=zone.id,
        # Counters, not 20,000 values: an audit row that carries the payload is a payload
        # nobody reads (ADR-0010 §14's shape, applied to the textarea path too).
        before={"postal_codes": int(removed or 0)},
        after={"postal_codes": len(codes)},
        request=request,
    )
    log.info(
        "storefront.postal_codes.replaced",
        organization_id=str(ctx.organization_id),
        zone_id=str(zone.id),
        removed=int(removed or 0),
        inserted=len(codes),
    )
    return codes


async def import_postal_codes(
    session: AsyncSession,
    ctx: ScopeContext,
    csv_text: str,
    *,
    mode: str,
    request: Request | None = None,
) -> ImportCounters:
    """Bulk import, `postal_code,zone_code`, **all or nothing, in one transaction**.

    A half-applied import is worse than a failed one, because the merchant cannot tell which
    half. The transaction is the request's own — raising anywhere in here rolls back every
    statement below.

    Duplicates are caught in `domain.parse_import_csv` **before any SQL**, naming the line
    numbers: last-wins on a file a merchant cannot read back is the silent wrong answer, and a
    silent wrong answer in 19,000 rows is found by a customer rather than by the merchant.

    `replace` deletes every mapping for the organization and re-inserts; `merge` upserts. Both
    are two statements regardless of file size — one zone-code resolution and one multi-row
    write — because a query inside the row loop would be an N+1 with 19,000 iterations.
    """
    parsed = domain.parse_import_csv(csv_text)
    zones = await selectors.zones_by_code(session, ctx, parsed.zone_codes)

    missing = sorted(parsed.zone_codes - set(zones))
    if missing:
        raise BusinessRuleProblem(
            f"{len(missing)} zone code(s) in that file do not exist: "
            f"{', '.join(missing[:_MAX_NAMED])}. Nothing was imported — create the zones first."
        )

    deleted = 0
    if mode == "replace":
        deleted = int(
            cast(
                "CursorResult[Any]",
                await session.execute(
                    delete(ZonePostalCode).where(
                        ZonePostalCode.organization_id == ctx.organization_id
                    )
                ),
            ).rowcount
            or 0
        )

    rows = [
        {
            "id": uuid.uuid4(),
            "organization_id": ctx.organization_id,
            "zone_id": zones[row.zone_code].id,
            "postal_code": row.postal_code,
        }
        for row in parsed.rows
    ]
    # `xmax = 0` is true for a tuple this statement inserted and false for one it updated — the
    # standard way to get insert/update counts out of a single upsert without a second query.
    upsert = pg_insert(ZonePostalCode).values(rows)
    result: Any = await session.execute(
        upsert.on_conflict_do_update(
            constraint="uq_zone_postal_codes_org_postal_code",
            set_={"zone_id": upsert.excluded.zone_id, "updated_at": func.now()},
            # `xmax` is a system column, so it is named as a literal rather than through a
            # mapped attribute.
        ).returning(ZonePostalCode.id, literal_column("(xmax = 0)").label("was_inserted"))
    )
    outcomes = result.all()
    inserted = sum(1 for row in outcomes if row.was_inserted)
    counters = ImportCounters(
        rows_read=len(parsed.rows),
        inserted=inserted,
        updated=len(outcomes) - inserted,
        deleted=deleted,
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="delivery.postal_codes.imported",
        entity="delivery_zone",
        # **One audit row per import**, carrying the counters — not 19,000 rows. The mode is on
        # the row because a `replace` is a destructive act and a `merge` is not, and the trail
        # has to distinguish them (ADR-0010 §14).
        after={
            "mode": mode,
            "rows_read": counters.rows_read,
            "inserted": counters.inserted,
            "updated": counters.updated,
            "deleted": counters.deleted,
        },
        request=request,
    )
    log.info(
        "storefront.postal_codes.imported",
        organization_id=str(ctx.organization_id),
        mode=mode,
        rows_read=counters.rows_read,
        inserted=counters.inserted,
        updated=counters.updated,
        deleted=counters.deleted,
    )
    return counters


# --------------------------------------------------------------------- the theme document


def _theme_snapshot(doc: ThemeDocument) -> dict[str, Any]:
    return {"theme_key": doc.theme_key, "mode": doc.mode, "version": doc.version}


async def put_theme_document(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ThemeDocPut,
    *,
    request: Request | None = None,
) -> ThemeDocument:
    """Upsert the organization's one theme document — this **is** adopt (ADR-0011 §3: the front
    end forks a theme client-side and PUTs the result, so there is no separate `/adopt` route).

    **Optimistic concurrency.** A `PUT` whose `version` does not match the stored one is a named
    409, never a silent overwrite of whatever a second tab just saved. `tokens` and `pages` are
    opaque JSON to this service; `domain.validate_theme_document` bounds only the envelope.
    """
    domain.validate_theme_document(
        theme_key=payload.theme_key, mode=payload.mode, tokens=payload.tokens, pages=payload.pages
    )

    existing = await selectors.get_theme_document(session, ctx)
    before: dict[str, Any] | None = None

    if existing is None:
        doc = ThemeDocument(
            organization_id=ctx.organization_id,
            theme_key=payload.theme_key,
            mode=payload.mode,
            tokens=payload.tokens,
            pages=payload.pages,
            version=1,
        )
        session.add(doc)
        action = "theme.adopted"
    else:
        if payload.version != existing.version:
            raise domain.Conflicted(
                "theme_version_conflict",
                "This theme document changed since you loaded it. Reload and reapply your "
                "edit — saving now would silently overwrite someone else's change.",
                field="version",
            )
        doc = existing
        before = _theme_snapshot(doc)
        doc.theme_key = payload.theme_key
        doc.mode = payload.mode
        doc.tokens = payload.tokens
        doc.pages = payload.pages
        doc.version = existing.version + 1
        action = "theme.updated"

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="theme_document",
        entity_id=doc.id,
        before=before,
        after=_theme_snapshot(doc),
        request=request,
    )
    await session.flush()
    log.info(
        "storefront.theme.saved",
        organization_id=str(ctx.organization_id),
        action=action,
        version=doc.version,
    )
    return doc


async def delete_theme_document(
    session: AsyncSession,
    ctx: ScopeContext,
    doc: ThemeDocument,
    *,
    request: Request | None = None,
) -> None:
    """Soft delete — "Remove this store" in Gallery. A re-adopt afterwards is a fresh `PUT`
    starting at `version=1` again, since the row it would have conflicted with is gone."""
    before = _theme_snapshot(doc)
    doc.deleted_at = utcnow()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="theme.removed",
        entity="theme_document",
        entity_id=doc.id,
        before=before,
        request=request,
    )
    await session.flush()
    log.info("storefront.theme.removed", organization_id=str(ctx.organization_id))


# --------------------------------------------------------------------- categories


def _category_snapshot(category: Category) -> dict[str, Any]:
    return {
        "key": category.key,
        "label": category.label,
        "blurb": category.blurb,
        "image_url": category.image_path,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
    }


async def create_category(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CategoryCreate,
    *,
    request: Request | None = None,
) -> Category:
    if await selectors.get_category_by_key(session, ctx, payload.key) is not None:
        raise ConflictProblem(f"A category with the key {payload.key!r} already exists.")

    category = Category(
        organization_id=ctx.organization_id,
        key=payload.key,
        label=payload.label,
        blurb=payload.blurb,
        image_path=payload.image_url,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    session.add(category)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.category.created",
        entity="category",
        entity_id=category.id,
        after=_category_snapshot(category),
        request=request,
    )
    await session.flush()
    log.info(
        "storefront.category.created",
        organization_id=str(ctx.organization_id),
        category_id=str(category.id),
    )
    return category


async def update_category(
    session: AsyncSession,
    ctx: ScopeContext,
    category: Category,
    payload: CategoryPatch,
    *,
    request: Request | None = None,
) -> Category:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return category

    before = _category_snapshot(category)
    if "image_url" in changes:
        category.image_path = changes.pop("image_url")
    for field, value in changes.items():
        setattr(category, field, value)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.category.updated",
        entity="category",
        entity_id=category.id,
        before=before,
        after=_category_snapshot(category),
        request=request,
    )
    await session.flush()
    return category


async def delete_category(
    session: AsyncSession,
    ctx: ScopeContext,
    category: Category,
    *,
    request: Request | None = None,
) -> None:
    """Soft delete. **Refused while a live product still carries this category** — cascading
    would silently orphan every one of them, the same rule `delete_zone` applies to a zone with
    postal codes still mapped."""
    counts = await selectors.category_product_counts(session, ctx, [category.id])
    in_use = counts.get(category.id, 0)
    if in_use:
        raise BusinessRuleProblem(
            f"That category still has {in_use} product(s) in it. Move or delete them first — "
            "deleting the category would leave them pointing at nothing."
        )

    before = _category_snapshot(category)
    category.deleted_at = utcnow()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.category.deleted",
        entity="category",
        entity_id=category.id,
        before=before,
        request=request,
    )
    await session.flush()


# --------------------------------------------------------------------- products


def _variant_snapshot(variant: ProductVariant) -> dict[str, Any]:
    return {
        "label": variant.label,
        "price_min_minor": variant.price_min_minor,
        "price_max_minor": variant.price_max_minor,
        "mrp_minor": variant.mrp_minor,
        "in_stock": variant.in_stock,
        "position": variant.position,
    }


def _product_snapshot(product: Product) -> dict[str, Any]:
    return {
        "code": product.code,
        "name": product.name,
        "brand": product.brand,
        "category_id": str(product.category_id),
        "spec": product.spec,
        "unit": product.unit,
        "price_min_minor": product.price_min_minor,
        "price_max_minor": product.price_max_minor,
        "mrp_minor": product.mrp_minor,
        "gst_inclusive": product.gst_inclusive,
        "moq": product.moq,
        "moq_unit": product.moq_unit,
        "in_stock": product.in_stock,
        "lead_time_days": product.lead_time_days,
        "delivery_eta_minutes": product.delivery_eta_minutes,
        "featured": product.featured,
        "image_url": product.image_path,
        "status": product.status,
        "is_active": product.is_active,
    }


async def _assert_category_exists(
    session: AsyncSession, ctx: ScopeContext, category_id: uuid.UUID
) -> None:
    if await selectors.get_category(session, ctx, category_id) is None:
        raise domain.Rejected(
            "category_not_found",
            "That category does not exist in this organization.",
            field="category_id",
        )


def _apply_variant_price_sync(
    *,
    price_min_minor: int,
    price_max_minor: int,
    mrp_minor: int | None,
    variants: list[ProductVariantIn],
) -> tuple[int, int, int | None]:
    """The invariant `types.ts:100-104` states and the client relies on: when variants exist,
    the product's own price band and MRP equal the first (default) variant's — enforced here
    rather than trusted from the client, so a rail with no variant selector still prints a true
    price."""
    if not variants:
        return price_min_minor, price_max_minor, mrp_minor
    first = variants[0]
    return first.price_min_minor, first.price_max_minor, first.mrp_minor


async def create_product(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ProductCreate,
    *,
    request: Request | None = None,
) -> Product:
    await _assert_category_exists(session, ctx, payload.category_id)
    if await selectors.get_product_by_code(session, ctx, payload.code) is not None:
        raise ConflictProblem(f"A product with the code {payload.code!r} already exists.")

    price_min, price_max, mrp = _apply_variant_price_sync(
        price_min_minor=payload.price_min_minor,
        price_max_minor=payload.price_max_minor,
        mrp_minor=payload.mrp_minor,
        variants=payload.variants,
    )
    domain.assert_price_band(price_min, price_max, mrp)
    for variant in payload.variants:
        domain.assert_price_band(
            variant.price_min_minor, variant.price_max_minor, variant.mrp_minor
        )

    product = Product(
        organization_id=ctx.organization_id,
        code=payload.code,
        name=payload.name,
        brand=payload.brand,
        category_id=payload.category_id,
        spec=payload.spec,
        unit=payload.unit,
        description=payload.description,
        price_min_minor=price_min,
        price_max_minor=price_max,
        mrp_minor=mrp,
        gst_inclusive=payload.gst_inclusive,
        moq=payload.moq,
        moq_unit=payload.moq_unit,
        in_stock=payload.in_stock,
        lead_time_days=payload.lead_time_days,
        delivery_eta_minutes=payload.delivery_eta_minutes,
        featured=payload.featured,
        image_path=payload.image_url,
        status=payload.status,
        is_active=payload.is_active,
    )
    session.add(product)
    await session.flush()

    for position, variant in enumerate(payload.variants):
        session.add(
            ProductVariant(
                organization_id=ctx.organization_id,
                product_id=product.id,
                label=variant.label,
                price_min_minor=variant.price_min_minor,
                price_max_minor=variant.price_max_minor,
                mrp_minor=variant.mrp_minor,
                in_stock=variant.in_stock,
                position=position,
            )
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.product.created",
        entity="product",
        entity_id=product.id,
        after=_product_snapshot(product),
        request=request,
    )
    await session.flush()
    log.info(
        "storefront.product.created",
        organization_id=str(ctx.organization_id),
        product_id=str(product.id),
    )
    return product


async def _replace_variants(
    session: AsyncSession, ctx: ScopeContext, product: Product, variants: list[ProductVariantIn]
) -> None:
    """Wholesale replace — the same `ZonePostalCodesPut` shape: variants carry no id a client
    holds across two requests, so a partial patch of them would be a merge nobody asked for."""
    existing = (
        (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.organization_id == ctx.organization_id,
                    ProductVariant.product_id == product.id,
                    ProductVariant.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        await session.delete(row)
    for position, variant in enumerate(variants):
        session.add(
            ProductVariant(
                organization_id=ctx.organization_id,
                product_id=product.id,
                label=variant.label,
                price_min_minor=variant.price_min_minor,
                price_max_minor=variant.price_max_minor,
                mrp_minor=variant.mrp_minor,
                in_stock=variant.in_stock,
                position=position,
            )
        )


async def update_product(
    session: AsyncSession,
    ctx: ScopeContext,
    product: Product,
    payload: ProductPatch,
    *,
    request: Request | None = None,
) -> Product:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return product

    if "category_id" in changes and changes["category_id"] is not None:
        await _assert_category_exists(session, ctx, changes["category_id"])

    before = _product_snapshot(product)

    variants_changed = "variants" in changes
    new_variants = changes.pop("variants", None)

    if "image_url" in changes:
        product.image_path = changes.pop("image_url")
    for field, value in changes.items():
        setattr(product, field, value)

    if variants_changed and new_variants is not None:
        variant_inputs = [ProductVariantIn.model_validate(v) for v in new_variants]
        price_min, price_max, mrp = _apply_variant_price_sync(
            price_min_minor=product.price_min_minor,
            price_max_minor=product.price_max_minor,
            mrp_minor=product.mrp_minor,
            variants=variant_inputs,
        )
        product.price_min_minor = price_min
        product.price_max_minor = price_max
        product.mrp_minor = mrp
        for variant in variant_inputs:
            domain.assert_price_band(
                variant.price_min_minor, variant.price_max_minor, variant.mrp_minor
            )
        await _replace_variants(session, ctx, product, variant_inputs)

    domain.assert_price_band(product.price_min_minor, product.price_max_minor, product.mrp_minor)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.product.updated",
        entity="product",
        entity_id=product.id,
        before=before,
        after=_product_snapshot(product),
        request=request,
    )
    await session.flush()
    return product


async def delete_product(
    session: AsyncSession,
    ctx: ScopeContext,
    product: Product,
    *,
    request: Request | None = None,
) -> None:
    before = _product_snapshot(product)
    product.deleted_at = utcnow()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalogue.product.deleted",
        entity="product",
        entity_id=product.id,
        before=before,
        request=request,
    )
    await session.flush()


# --------------------------------------------------------------------- public catalogue


@dataclass(frozen=True, slots=True)
class PublicCatalogue:
    """What the buyer bundle's catalogue call needs — raw rows, not the wire shape. `public.py`
    builds `PublicCatalogueOut` from this, the same split `resolve_serviceability` /
    `answer_out` uses for the serviceability check."""

    categories: list[tuple[Category, int]]
    products: list[Product]
    products_total: int
    category_keys: dict[uuid.UUID, str]
    variants: dict[uuid.UUID, list[ProductVariant]]


async def get_public_catalogue_for_store(
    store: PublicStore, params: PageParams, *, category_key: str | None = None
) -> PublicCatalogue:
    """A published store's active catalogue, anonymous, tenant-bound (gap 4).

    Its own `session_scope`, the same bypass-then-narrow shape as every other anonymous read
    here: the tenant is already resolved by the caller on the bypass reader, and everything
    below runs on the ordinary NOBYPASSRLS role under row-level security.
    """
    async with session_scope(organization_id=store.organization_id) as session:
        categories = await selectors.list_public_categories(session, store.organization_id)
        products, total = await selectors.list_public_products(
            session, store.organization_id, params, category_key=category_key
        )
        variants = await selectors.public_variants_for_products(
            session, store.organization_id, [p.id for p in products]
        )
        category_rows = await selectors.public_categories_by_id(
            session, store.organization_id, {p.category_id for p in products}
        )
    return PublicCatalogue(
        categories=categories,
        products=products,
        products_total=total,
        category_keys={cid: row.key for cid, row in category_rows.items()},
        variants=variants,
    )


async def get_theme_for_store(store: PublicStore) -> ThemeDocument | None:
    """The buyer bootstrap's theme lookup — its own `session_scope`, the same shape
    `check_serviceability_for_store` uses, per `public.py`'s own rule: the moment
    `store-context` grows something that is not on `storefronts`, the extension goes through
    `session_scope` exactly as serviceability's does."""
    async with session_scope(organization_id=store.organization_id) as session:
        return await selectors.get_theme_document_for_org(session, store.organization_id)


# --------------------------------------------------------------------- enquiries


def _enquiry_snapshot(enquiry: Enquiry) -> dict[str, Any]:
    return {"status": enquiry.status}


async def create_enquiry_for_store(
    store: PublicStore,
    payload: EnquiryCreate,
    *,
    now: dt.datetime | None = None,
) -> Enquiry:
    """A buyer's enquiry against a published store — anonymous, its own transaction, the same
    bypass-then-narrow shape `check_serviceability_for_store` uses (ADR-0002, "What breaks",
    path 4).

    Contact fields are sealed before they are ever written: `email`/`phone` get an
    AES-256-GCM envelope (`platform_core.pii.seal`) plus a masked hint, and no raw value is
    stored in the clear at any point.

    Every `product_id` on `payload.lines` is validated against *this* store's active catalogue
    in one batched query — an anonymous buyer's fabricated or cross-tenant id must not read back
    to the merchant as a real line — and each line's `name` is taken from the catalogue rather
    than trusted from the body (`EnquiryLineIn`'s docstring). An unknown or cross-store id is a
    422 that names no product, so the response cannot be used to probe whether an id belongs to
    another organization's catalogue.

    The outbox event is staged in the same transaction, so a notification can never fire for an
    enquiry that failed to commit — and never silently vanish for one that did.
    """
    now = now or utcnow()
    cfg = settings()
    email_enc, email_masked = seal(payload.email, kind="email", settings=cfg)
    phone_enc: bytes | None = None
    phone_masked = ""
    if payload.phone:
        phone_enc, phone_masked = seal(payload.phone, kind="phone", settings=cfg)

    async with session_scope(organization_id=store.organization_id) as session:
        product_ids = {line.product_id for line in payload.lines}
        products = await selectors.public_products_by_id(
            session, store.organization_id, product_ids
        )
        if any(line.product_id not in products for line in payload.lines):
            raise domain.Rejected(
                "enquiry_unknown_product",
                "One or more items are not available from this store.",
                field="lines",
            )
        lines = [
            {
                "product_id": str(line.product_id),
                "name": products[line.product_id].name,
                "quantity": line.quantity,
            }
            for line in payload.lines
        ]

        enquiry = Enquiry(
            organization_id=store.organization_id,
            name=payload.name,
            company=payload.company,
            email_masked=email_masked,
            email_enc=email_enc,
            phone_masked=phone_masked,
            phone_enc=phone_enc,
            gstin=payload.gstin,
            requirement=payload.requirement,
            quantity=payload.quantity,
            lines=lines,
            status="new",
        )
        session.add(enquiry)
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=store.organization_id,
            actor_id=None,
            action="sales.enquiry.received",
            entity="enquiry",
            entity_id=enquiry.id,
            after={"status": enquiry.status, "company": enquiry.company},
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=store.organization_id,
            event_type="storefront.enquiry.created",
            aggregate_type="enquiry",
            aggregate_id=enquiry.id,
            payload={
                "enquiry_id": str(enquiry.id),
                "store_id": str(store.storefront_id),
                "company": enquiry.company,
                "line_count": len(enquiry.lines),
            },
        )
    return enquiry


async def update_enquiry_status(
    session: AsyncSession,
    ctx: ScopeContext,
    enquiry: Enquiry,
    payload: EnquiryPatch,
    *,
    request: Request | None = None,
) -> Enquiry:
    domain.assert_enquiry_transition(enquiry.status, payload.status)
    if enquiry.status == payload.status:
        return enquiry

    before = _enquiry_snapshot(enquiry)
    enquiry.status = payload.status
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="sales.enquiry.status_changed",
        entity="enquiry",
        entity_id=enquiry.id,
        before=before,
        after=_enquiry_snapshot(enquiry),
        request=request,
    )
    await session.flush()
    return enquiry


async def unmask_enquiry_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    enquiry: Enquiry,
    *,
    request: Request | None = None,
) -> tuple[str | None, str | None]:
    """Reveal an enquiry's real email and phone — the audited, stepped-up path, the same shape
    as `services/customer`'s `unmask_contact_phone`. Writes `entity.sensitive_read` in the
    caller's transaction whether or not anything was sealed, because the auditable act is the
    attempt."""
    cfg = settings()
    email = open_sealed(enquiry.email_enc, settings=cfg) if enquiry.email_enc is not None else None
    phone = open_sealed(enquiry.phone_enc, settings=cfg) if enquiry.phone_enc is not None else None

    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="enquiry",
        entity_id=enquiry.id,
        fields=["email", "phone"],
        request=request,
    )
    return email, phone
