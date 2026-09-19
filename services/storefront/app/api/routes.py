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
from app.models import SCHEMA
from app.schemas import (
    DeliveryCheckIn,
    DemandRowOut,
    ImportResultOut,
    MerchantServiceabilityOut,
    ServiceInfo,
    StoreOut,
    StorePut,
    ZoneCreate,
    ZoneOut,
    ZonePatch,
    ZonePostalCodesOut,
    ZonePostalCodesPut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, PayloadTooLargeProblem
from platform_core.models import utcnow
from platform_core.pagination import Page, PageQuery

router = APIRouter(tags=["storefront"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `storefront` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("storefront"))

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


@router.get(
    "/storefront/store",
    response_model=StoreOut,
    summary="The store's published identity",
    dependencies=[Entitled],
)
async def read_store(session: SessionDep, ctx: StoreRead) -> StoreOut:
    """`storefront.store.settings.read`, floored at ORG."""
    store = await selectors.get_storefront(session, ctx)
    if store is None:
        raise NotFoundProblem("This organization has no storefront yet.")
    return StoreOut.model_validate(store)


@router.put(
    "/storefront/store",
    response_model=StoreOut,
    summary="Create or update the store, and publish or unpublish it",
    dependencies=[Entitled],
)
async def put_store(
    payload: StorePut, session: SessionDep, ctx: StoreWrite, request: Request
) -> StoreOut:
    """`storefront.store.settings.write`, floored at ORG.

    **Publishing is the act that makes a store visible to the internet**, and with it that
    merchant's delivery map readable by anyone who knows the slug. Accepted deliberately — a
    merchant publishes both on their own website, and it is a shopfront sign — but it is not
    reversible once slugs are in circulation, and the UI has to say so on the control.

    A slug already taken by another organization is a 409 through the platform's integrity
    handler, which deliberately does not echo the constraint name.
    """
    store = await services.put_storefront(session, ctx, payload, request=request)
    await session.flush()
    # `updated_at` carries a SQL `onupdate`, so the ORM expires it after an UPDATE and reading it
    # would emit a lazy SELECT from outside the async driver's context. One explicit round trip
    # here is the house answer (`services/support`), and it means the response's timestamp is the
    # one actually stored rather than the one the row held before the write.
    await session.refresh(store)
    return StoreOut.model_validate(store)


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
