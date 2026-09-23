"""HTTP surface for the standup service — PulseBoard's daily check-ins.

Mounted behind the gateway's `/api/v1/standups` prefix (see
`services/app-gateway/routes.json`), so every route path here begins `/standups/...`.

The read endpoint is the priority — it is what flips the PulseBoard SPA from fixtures to live —
and returns exactly the entry shape `frontend/product-pulseboard/src/api/types.ts` declares
(`StandupEntry`), inside the platform's `Page` envelope. `date` defaults to today, matching the
SPA's `?date=` query. Every route declares the permission it needs and is guarded by
`require_product("pulseboard")` as defence in depth behind the gateway's own entitlement gate.
Reads go through `selectors`, writes through `services`; a router never builds its own query.
Every route takes the tenant-bound session and opens no transaction of its own — the
chokepoint owns it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import ServiceInfo, StandupEntryIn, StandupEntryOut
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.pagination import Page, PageQuery, paginate

# Registering the pulseboard scope resolver at import of the api module ties it to the product
# it serves: the process that mounts these routes is the one that answers its scope questions.
scope.register()

router = APIRouter(tags=["standups"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `pulseboard` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("pulseboard"))

EntryRead = Annotated[ScopeContext, Depends(require("pulseboard.standup.entry.read"))]
EntryWrite = Annotated[ScopeContext, Depends(require("pulseboard.standup.entry.write"))]


@router.get("/standups/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ the wall


@router.get(
    "/standups",
    response_model=Page[StandupEntryOut],
    summary="The team's check-ins for one day",
    dependencies=[Entitled],
)
async def list_standups(
    session: SessionDep,
    ctx: EntryRead,
    params: PageQuery,
    entry_date: Annotated[
        date | None, Query(alias="date", description="The day (ISO date). Defaults to today.")
    ] = None,
    blocked: Annotated[
        bool, Query(description="Only entries with something blocking them")
    ] = False,
) -> Page[StandupEntryOut]:
    day = entry_date if entry_date is not None else date.today()
    stmt = selectors.entries_stmt(ctx, entry_date=day, blocked_only=blocked)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.entry_out(e) for e in rows], total=total, params=params)


@router.post(
    "/standups",
    response_model=StandupEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post (or repost) a day's check-in",
    dependencies=[Entitled],
)
async def post_standup(
    payload: StandupEntryIn, session: SessionDep, ctx: EntryWrite, request: Request
) -> StandupEntryOut:
    entry = await services.post_entry_from(session, ctx, payload, request=request)
    return selectors.entry_out(entry)
