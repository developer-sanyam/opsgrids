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
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from sqlalchemy import CursorResult, delete, func, insert, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.models import (
    AuditLog,
    DeliveryZone,
    ServiceabilityDemand,
    Storefront,
    ZonePostalCode,
)
from app.schemas import StorePut, ZoneCreate, ZonePatch
from app.selectors import PublicStore, ServiceabilityAnswer
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.cache import cache_delete
from platform_core.db import session_scope
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from platform_core.logging import get_logger
from platform_core.models import utcnow

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


async def put_storefront(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: StorePut,
    *,
    request: Request | None = None,
) -> Storefront:
    """Create or update the organization's single storefront.

    **Publishing is audited as its own act.** A status change is what makes a store visible to
    the internet — and, with it, that merchant's delivery map readable by anyone who knows the
    slug — so `storefront.published` / `.unpublished` are separate audit actions rather than a
    field inside a generic update row (ADR-0010 §14).
    """
    # Refuse a timezone the server cannot resolve, in the merchant's words rather than as a
    # 500 six weeks later: a bad IANA name would silently produce promised dates in UTC, an
    # answer that is wrong by hours and looks perfectly well-formed.
    domain.today_in(payload.timezone, now=utcnow())

    existing = await selectors.get_storefront(session, ctx)
    before: dict[str, Any] | None = None
    old_slug: str | None = None

    if existing is None:
        store = Storefront(
            organization_id=ctx.organization_id,
            slug=payload.slug,
            status=payload.status,
            currency=payload.currency,
            timezone=payload.timezone,
        )
        session.add(store)
        action = "storefront.created"
    else:
        store = existing
        old_slug = store.slug
        before = {
            "slug": store.slug,
            "status": store.status,
            "currency": store.currency,
            "timezone": store.timezone,
        }
        store.slug = payload.slug
        store.status = payload.status
        store.currency = payload.currency
        store.timezone = payload.timezone
        action = "storefront.updated"
        if before["status"] != payload.status:
            action = (
                "storefront.published"
                if payload.status == "published"
                else "storefront.unpublished"
            )

    after = {
        "slug": store.slug,
        "status": store.status,
        "currency": store.currency,
        "timezone": store.timezone,
    }
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="storefront",
        entity_id=store.id,
        before=before,
        after=after,
        request=request,
    )
    # Best effort, and honestly racy: the request's transaction has not committed yet, so a
    # concurrent anonymous check could repopulate the entry from the pre-commit row and keep it
    # for another TTL. The TTL bounds it either way — this only shortens the window that
    # ADR-0010 §13 already accepts ("a newly published store may be 404 for five minutes").
    await cache_delete(*{selectors.slug_cache_key(s) for s in (old_slug, store.slug) if s})
    log.info(
        "storefront.store.saved",
        organization_id=str(ctx.organization_id),
        storefront_id=str(store.id),
        action=action,
        status=store.status,
    )
    return store


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
