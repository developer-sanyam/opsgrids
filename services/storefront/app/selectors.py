"""Reads for storefront. Visibility rules live here, in one place.

Every tenant-scoped query filters on `organization_id` explicitly **and** calls `ctx.apply(...)`.
Row-level security is the net, not the filter (CLAUDE.md). `ctx.apply()` is a no-op for all four
of this product's permissions, because every one is ceilinged *and* defaulted at ORG and there is
no narrower rung to represent — it is called anyway, because the ceiling is catalogue data a
later edit can widen and the day it widens this is the line that has to already mean something.

────────────────────────────────────────────────────────────────────────────────────────────
**The chokepoint sentence, and the one a later contributor is most likely to break for
convenience:**

    Every serviceability answer — public, admin, and eventually order placement — is produced
    by `resolve_serviceability(session, organization_id, postal_code)`, inside a tenant-bound
    transaction. No caller computes it, caches it, or passes one in.

It earns that on **two concrete callers today** — the anonymous buyer endpoint and the
merchant's tester. The third, order placement, is what the rule exists to protect: ADR-0010 §5
inherits it as "the decision is re-evaluated server-side inside the placement transaction,
through this selector, against the postal code on the order; the client's answer is never an
input to it".
────────────────────────────────────────────────────────────────────────────────────────────

**There is no server-side cache of an answer, deliberately.** The lookup is one unique-index
probe on a table that fits in shared buffers; a Redis round trip is a network hop to avoid a
memory read, and a TTL is the mechanism by which a merchant edits their map and watches nothing
happen. The *slug* and *host* lookups **are** cached, and the contrast is the rule: cache the
thing that crosses an engine boundary and never changes, not the thing that is cheap and
changes on purpose (ADR-0010 §5, §12; ADR-0012 §2).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.config import settings
from app.models import (
    DeliveryZone,
    ServiceabilityDemand,
    Storefront,
    ZonePostalCode,
)
from platform_core.auth import ScopeContext
from platform_core.cache import cache_get, cache_set
from platform_core.logging import get_logger
from platform_core.models import utcnow
from platform_core.pagination import PageParams, paginate

log = get_logger("storefront.selectors")


# --------------------------------------------------------------------- the bypass reader


@dataclass(frozen=True, slots=True)
class PublicStore:
    """What the bypass reader is allowed to learn: **one row of `storefront.storefronts`**.

    A bypass buys *discovery only* (ADR-0002 §2). No delivery data, no postal codes, no writes,
    ever — the next statement runs on the ordinary NOBYPASSRLS role inside a tenant-bound
    transaction, so row-level security applies to the anonymous request exactly as it does to an
    authenticated one.

    ADR-0012 widened this from two ids and a status to the identity row's public columns,
    because the buyer's bootstrap renders the brand and has to name the canonical host. **The
    surface did not widen**: it is still `storefront.storefronts` and still nothing else, which
    is the thing ADR-0011 bought by keeping `custom_domain` on this table rather than in a side
    table (ADR-0012 §2). One reader, one table, two lookups into it.

    **Holding the brand here does not publish it.** The route decides what a stranger is shown,
    and it shows nothing at all for a store that is not `published`.
    """

    organization_id: uuid.UUID
    storefront_id: uuid.UUID
    slug: str
    status: str
    currency: str
    timezone: str
    name: str
    tagline: str | None
    logo_text: str | None
    custom_domain: str | None
    custom_domain_status: str | None

    @property
    def is_published(self) -> bool:
        return self.status == "published"


_MISS = "\0"
"""Cached sentinel for "no such slug", and for "no store at that host".

Negative results are cached at the same TTL as positive ones, which is the same five minutes
ADR-0010 §13 already accepts for a newly published store. The alternative — caching hits only —
puts every enumeration attempt straight onto the directory engine's **five** connections, which
is the second saturation point the ADR names, on the path of every anonymous request.
"""


def slug_cache_key(slug: str) -> str:
    """Public because the store writer invalidates it. One key shape, one place."""
    return f"storefront:slug:{slug}"


def host_cache_key(host: str) -> str:
    """The custom-domain lookup's key (ADR-0012 §2). Public for the same reason as the slug's.

    **The host is already normalised and bounded by `domain.normalise_request_host` before it
    reaches here**, and that is what makes an unauthenticated lookup safe to key a cache on: an
    attacker can mint distinct hostnames, but each is at most 253 ASCII characters of hostname
    shape, each entry expires in `store_host_cache_seconds`, and the gateway's anonymous limit
    is 60 requests per minute per address. So one address can hold at most a few hundred keys of
    a few hundred bytes resident at a time, and nothing a caller sends decides the key's size.
    A string that is not a hostname is a 422 and never becomes a key at all.
    """
    return f"storefront:host:{host}"


#: Every column the cached row carries. A cache entry written by an older build is missing the
#: ones ADR-0012 added, and **treating that as a miss is the only correct reading**: a rolling
#: deploy would otherwise answer a buyer from a record with no brand in it. Cheaper than a key
#: version, and it needs no second key shape for an invalidation to remember.
_CACHED_FIELDS = frozenset(
    {
        "organization_id",
        "storefront_id",
        "slug",
        "status",
        "currency",
        "timezone",
        "name",
        "tagline",
        "logo_text",
        "custom_domain",
        "custom_domain_status",
    }
)

#: The columns the bypass reader selects — ADR-0012 §2's list, written once so the two lookups
#: cannot drift into reading different halves of the same row.
_PUBLIC_COLUMNS = (
    Storefront.id,
    Storefront.organization_id,
    Storefront.slug,
    Storefront.status,
    Storefront.currency,
    Storefront.timezone,
    Storefront.name,
    Storefront.tagline,
    Storefront.logo_text,
    Storefront.custom_domain,
    Storefront.custom_domain_status,
)


def _public_store(row: Any) -> PublicStore:
    return PublicStore(
        organization_id=row.organization_id,
        storefront_id=row.id,
        slug=row.slug,
        status=row.status,
        currency=row.currency,
        timezone=row.timezone,
        name=row.name,
        tagline=row.tagline,
        logo_text=row.logo_text,
        custom_domain=row.custom_domain,
        custom_domain_status=row.custom_domain_status,
    )


def _encode(store: PublicStore) -> str:
    return json.dumps(
        {
            "organization_id": str(store.organization_id),
            "storefront_id": str(store.storefront_id),
            "slug": store.slug,
            "status": store.status,
            "currency": store.currency,
            "timezone": store.timezone,
            "name": store.name,
            "tagline": store.tagline,
            "logo_text": store.logo_text,
            "custom_domain": store.custom_domain,
            "custom_domain_status": store.custom_domain_status,
        }
    )


def _decode(cached: str) -> PublicStore | None:
    """A cached row, or `None` when the entry predates a column this build needs."""
    record = json.loads(cached)
    if not record.keys() >= _CACHED_FIELDS:
        return None
    return PublicStore(
        organization_id=uuid.UUID(record["organization_id"]),
        storefront_id=uuid.UUID(record["storefront_id"]),
        slug=record["slug"],
        status=record["status"],
        currency=record["currency"],
        timezone=record["timezone"],
        name=record["name"],
        tagline=record["tagline"],
        logo_text=record["logo_text"],
        custom_domain=record["custom_domain"],
        custom_domain_status=record["custom_domain_status"],
    )


async def resolve_public_store(session: AsyncSession, slug: str) -> PublicStore | None:
    """Slug → organization, on the BYPASSRLS **SELECT-only** reader. The feature's chokepoint.

    ────────────────────────────────────────────────────────────────────────────────────────
    **This function is the entire surface of `opsgrids_storefront_directory`.** The role holds
    SELECT on `storefront.storefronts` and on nothing else, and a second query added here "for
    convenience" is a cross-tenant read of whatever it touches. It inherits ADR-0002's
    obligations: the blast radius is one table, one column set, and it stays that way.
    ────────────────────────────────────────────────────────────────────────────────────────

    An anonymous request arrives with no organization — the gateway sets `x-org-id` only after
    decoding a token, and it returns before that on the public branch. So the tenant must be
    resolved *before* a tenant-bound transaction can be opened, which means before row-level
    security can protect anything. That is what this role is for, and why it may only read.

    Cached in Redis for `store_slug_cache_seconds`: it crosses an engine boundary and changes
    approximately never, which is precisely the property that makes a cache correct here and
    wrong for the answer itself. A cache failure falls through to the query and is never fatal.
    """
    key = slug_cache_key(slug)
    cached = await cache_get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        hit = _decode(cached)
        if hit is not None:
            return hit

    row = (
        await session.execute(
            select(*_PUBLIC_COLUMNS).where(Storefront.slug == slug, Storefront.deleted_at.is_(None))
        )
    ).one_or_none()

    ttl = settings().store_slug_cache_seconds
    if row is None:
        await cache_set(key, _MISS, ttl=ttl)
        return None

    store = _public_store(row)
    await cache_set(key, _encode(store), ttl=ttl)
    return store


async def resolve_public_store_by_host(
    session: AsyncSession, host: str, *, platform_suffix: str
) -> PublicStore | None:
    """Host → store, on the same BYPASSRLS **SELECT-only** reader (ADR-0012 §2).

    ────────────────────────────────────────────────────────────────────────────────────────
    **Both branches read `storefront.storefronts` and nothing else**, so ADR-0010 §4's
    chokepoint sentence survives intact: the role holds SELECT on that one table and on nothing
    else, and it still buys *discovery only*. That is a direct consequence of ADR-0011 putting
    the domain on the identity row rather than in a side table, and it is the single best thing
    that decision bought — a second table here would have meant a second grant on the path of
    every anonymous request.
    ────────────────────────────────────────────────────────────────────────────────────────

    Two branches, and the first is not a special case of the second:

    * `<slug>.stores.opsgrids.com` is parsed back to its slug and answered by
      `resolve_public_store`, reusing the existing lookup **and its existing cache key**. The
      platform host never retires (ADR-0012 §8), so this is the branch that has to keep working
      when a custom domain breaks, and it has no dependency on any domain state at all.
    * anything else is a claimed domain, matched against the claim **and its `www` partner**,
      and only at a status that means the claim was proved: `SERVING_DOMAIN_STATUSES`. An
      `unverified` claim is stored on the word of whoever typed it, and serving one is how a
      merchant takes another merchant's traffic.

    `host` is already normalised — lower-cased, port and root dot stripped, punycode — by
    `domain.normalise_request_host`. Passing an un-normalised one would miss a row that exists,
    which on this path reads as "there is no such store".

    **Two rows can match**, if one store claims `merchant.com` and another claims
    `www.merchant.com`: each is globally unique on its own, and the pair is a rule rather than a
    constraint. The exact host wins, deterministically, and the collision is logged — a 500 here
    would be a failure rendering as an absent store, which is the one thing this path must never
    do.
    """
    slug = domain.platform_slug(host, suffix=platform_suffix)
    if slug is not None:
        return await resolve_public_store(session, slug)

    key = host_cache_key(host)
    cached = await cache_get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        hit = _decode(cached)
        if hit is not None:
            return hit

    partner = domain.www_partner(host)
    rows = (
        await session.execute(
            select(*_PUBLIC_COLUMNS)
            .where(
                Storefront.custom_domain.in_([host, partner]),
                Storefront.custom_domain_status.in_(domain.SERVING_DOMAIN_STATUSES),
                Storefront.deleted_at.is_(None),
            )
            # The exact host first, so which store answers is decided by the query rather than
            # by the order the two claims happened to be written in.
            .order_by((Storefront.custom_domain == host).desc())
            .limit(2)
        )
    ).all()

    ttl = settings().store_host_cache_seconds
    if not rows:
        await cache_set(key, _MISS, ttl=ttl)
        return None
    if len(rows) > 1:
        log.warning(
            "storefront.public.host_claimed_twice",
            host=host,
            partner=partner,
            served_storefront_id=str(rows[0].id),
        )

    store = _public_store(rows[0])
    await cache_set(key, _encode(store), ttl=ttl)
    return store


# --------------------------------------------------------------------- the answer


@dataclass(frozen=True, slots=True)
class ServiceabilityAnswer:
    """One computed answer.

    **Built in exactly one place — `resolve_serviceability` below.** That is what makes
    ADR-0010 §13's rule structural rather than a comment: there is no code path anywhere that
    can turn a failure into an answer, because there is no other constructor of this type and no
    `except` clause on the public route. A check that cannot complete raises, and the client
    renders "couldn't check". *Unknown is not no.*
    """

    postal_code: str
    mode: str
    #: Null when serviceable; `no_mapping` | `excluded` | `zone_inactive` otherwise.
    reason: str | None
    #: The demand counter's key: SERVED | NO_MAPPING | EXCLUDED | ZONE_INACTIVE.
    outcome: str
    zone: DeliveryZone | None
    promise_kind: str | None = None
    promise_minutes: int | None = None
    promise_date: dt.date | None = None
    buyer_note: str | None = None

    @property
    def serviceable(self) -> bool:
        return self.reason is None


def _unserved(postal_code: str, reason: str, *, zone: DeliveryZone | None) -> ServiceabilityAnswer:
    return ServiceabilityAnswer(
        postal_code=postal_code,
        mode="UNSERVED",
        reason=reason,
        outcome={
            "no_mapping": "NO_MAPPING",
            "excluded": "EXCLUDED",
            "zone_inactive": "ZONE_INACTIVE",
        }[reason],
        zone=zone,
    )


async def resolve_serviceability(
    session: AsyncSession,
    organization_id: uuid.UUID,
    postal_code: str,
    *,
    timezone: str,
    now: dt.datetime | None = None,
) -> ServiceabilityAnswer:
    """**The only place a serviceability answer is computed.** See the module docstring.

    `postal_code` arrives already normalised and validated by `domain.validate_postal_code` —
    a malformed one is a 422 at the boundary and never reaches here, because "that is not a
    postal code" and "we do not deliver there" are different facts.

    `timezone` and `now` are keyword-only extras on top of ADR-0010 §5's three positional
    arguments: the promised *date* is the store's, resolved from `storefronts.timezone`, and
    passing the clock in keeps this deterministic under test. The browser's timezone is never an
    input.

    One statement: a unique-index probe on `(organization_id, postal_code)` joined to its zone.
    """
    now = now or utcnow()
    row = (
        await session.execute(
            select(DeliveryZone)
            .join(ZonePostalCode, ZonePostalCode.zone_id == DeliveryZone.id)
            .where(
                ZonePostalCode.organization_id == organization_id,
                ZonePostalCode.postal_code == postal_code,
                ZonePostalCode.deleted_at.is_(None),
                DeliveryZone.organization_id == organization_id,
                DeliveryZone.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    # Never mapped. Distinct from "mapped and excluded" in the response *and* in the demand
    # report, because "we have not got round to it" is the row a merchant expands into.
    if row is None:
        return _unserved(postal_code, "no_mapping", zone=None)
    if not row.is_active:
        return _unserved(postal_code, "zone_inactive", zone=row)
    if row.mode == "UNSERVED":
        return _unserved(postal_code, "excluded", zone=row)

    promise_date: dt.date | None = None
    if row.mode == "PROMISED" and row.promise_kind == "DATE_OFFSET":
        # The server computes the date, in the store's timezone, cutoff applied. A buyer in
        # Singapore looking at a Mumbai store sees the Mumbai answer (ADR-0000 "Also settled").
        promise_date = domain.resolve_promise_date(
            now=now,
            timezone=timezone,
            offset_days=row.promise_offset_days or 0,
            cutoff=row.promise_cutoff_time or dt.time(23, 59),
        )

    return ServiceabilityAnswer(
        postal_code=postal_code,
        mode=row.mode,
        reason=None,
        outcome="SERVED",
        zone=row,
        promise_kind=row.promise_kind,
        promise_minutes=row.promise_minutes if row.promise_kind == "DURATION" else None,
        promise_date=promise_date,
        buyer_note=row.buyer_note,
    )


# --------------------------------------------------------------------- the merchant's reads


async def get_storefront(session: AsyncSession, ctx: ScopeContext) -> Storefront | None:
    """The organization's single storefront row, or None before it has been created."""
    stmt = select(Storefront).where(
        Storefront.organization_id == ctx.organization_id,
        Storefront.deleted_at.is_(None),
    )
    return (await session.execute(ctx.apply(stmt, owner_col=Storefront.id))).scalars().one_or_none()


def _zones(ctx: ScopeContext) -> Select[tuple[DeliveryZone]]:
    stmt = select(DeliveryZone).where(
        DeliveryZone.organization_id == ctx.organization_id,
        DeliveryZone.deleted_at.is_(None),
    )
    return ctx.apply(stmt, owner_col=DeliveryZone.id)


async def list_zones(
    session: AsyncSession, ctx: ScopeContext, params: PageParams
) -> tuple[list[DeliveryZone], int, dict[uuid.UUID, int]]:
    """The zone editor's list, newest-code-first, with each zone's postal-code count.

    The counts come back as **one grouped query for the whole page**, not one per row. A count
    inside the loop is the N+1 this list is most likely to grow, and it would grow it on the
    screen a merchant opens first.
    """
    stmt = _zones(ctx).order_by(DeliveryZone.code.asc(), DeliveryZone.id.asc())
    rows, total = await paginate(session, stmt, params)
    counts = await postal_code_counts(session, ctx, [row.id for row in rows])
    return rows, total, counts


async def postal_code_counts(
    session: AsyncSession, ctx: ScopeContext, zone_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """`zone_id → mapped postal codes`, for a page of zones, in one statement."""
    if not zone_ids:
        return {}
    stmt = (
        select(ZonePostalCode.zone_id, func.count())
        .where(
            ZonePostalCode.organization_id == ctx.organization_id,
            ZonePostalCode.zone_id.in_(zone_ids),
            ZonePostalCode.deleted_at.is_(None),
        )
        .group_by(ZonePostalCode.zone_id)
    )
    return {zone_id: int(count) for zone_id, count in (await session.execute(stmt)).all()}


async def get_zone(
    session: AsyncSession, ctx: ScopeContext, zone_id: uuid.UUID
) -> DeliveryZone | None:
    """One zone, or None. None rather than a 403 on a cross-tenant id: confirming that an id
    exists in *some* organization is itself a disclosure."""
    return (
        (await session.execute(_zones(ctx).where(DeliveryZone.id == zone_id)))
        .scalars()
        .one_or_none()
    )


async def get_zone_by_code(
    session: AsyncSession, ctx: ScopeContext, code: str
) -> DeliveryZone | None:
    return (
        (await session.execute(_zones(ctx).where(DeliveryZone.code == code)))
        .scalars()
        .one_or_none()
    )


async def zones_by_code(
    session: AsyncSession, ctx: ScopeContext, codes: set[str]
) -> dict[str, DeliveryZone]:
    """Every named zone in **one** statement — what the CSV import resolves `zone_code` with.

    A 19,000-row file names at most a few dozen distinct zones; looking each one up inside the
    row loop would be the textbook N+1, on the one route in this service that handles a file.
    """
    if not codes:
        return {}
    rows = (await session.execute(_zones(ctx).where(DeliveryZone.code.in_(codes)))).scalars().all()
    return {row.code: row for row in rows}


async def zone_postal_codes(
    session: AsyncSession, ctx: ScopeContext, zone_id: uuid.UUID
) -> list[str]:
    """Every postal code in one zone, sorted. Bounded by `MAX_POSTAL_CODES_PER_ZONE`.

    Deliberately not paginated: this is one textarea holding one list, and a merchant editing a
    page of their own zone would save the page and silently drop the rest.
    """
    stmt = (
        select(ZonePostalCode.postal_code)
        .where(
            ZonePostalCode.organization_id == ctx.organization_id,
            ZonePostalCode.zone_id == zone_id,
            ZonePostalCode.deleted_at.is_(None),
        )
        .order_by(ZonePostalCode.postal_code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_demand(
    session: AsyncSession,
    ctx: ScopeContext,
    params: PageParams,
    *,
    since: dt.date,
    until: dt.date,
    outcome: str | None = None,
) -> tuple[list[ServiceabilityDemand], int]:
    """The out-of-zone report: which postal codes were asked for, and how often.

    Range-scanned on the unique index's `(organization_id, check_date)` prefix — the reason
    ADR-0010 §10 **rejects** a second `(organization_id, check_date DESC)` index rather than
    deferring it: it would be write amplification on the hottest write in the service, to serve
    a read that is already served.

    Ordered by `checks` descending, because the merchant's question is "where should I expand
    next", not "what happened most recently".
    """
    stmt = select(ServiceabilityDemand).where(
        ServiceabilityDemand.organization_id == ctx.organization_id,
        ServiceabilityDemand.check_date >= since,
        ServiceabilityDemand.check_date <= until,
        ServiceabilityDemand.deleted_at.is_(None),
    )
    if outcome is not None:
        stmt = stmt.where(ServiceabilityDemand.outcome == outcome)
    stmt = ctx.apply(stmt, owner_col=ServiceabilityDemand.id).order_by(
        ServiceabilityDemand.checks.desc(),
        ServiceabilityDemand.check_date.desc(),
        ServiceabilityDemand.postal_code.asc(),
    )
    return await paginate(session, stmt, params)
