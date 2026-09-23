"""The internal API — service-to-service, never reachable from outside the cluster.

Product services emit usage here, server-to-server, as they do work. This is not a user surface:
there is no person and no tenant context on the call, so it is guarded by a short-lived service
token addressed to *this* service by name (`require_service_token`), not by a permission on a
principal. The organization each event belongs to travels in the payload, and the service layer
binds the tenant GUC per organization before writing (see `services.record_usage_batch`).

Ingest is idempotent on each event's `idempotency_key` (unique per organization): posting the
same key twice yields exactly one row and one event, so a product service can retry a batch after
a timeout without double-counting.

Mounted at the root rather than under `/api/v1`, and excluded from the OpenAPI document: this is
not public API and must not appear as though it were.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.schemas import OrganizationUsageOut, UsageBatchIn, UsageIngestOut, UsageLineOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import BusinessRuleProblem, ForbiddenProblem
from platform_core.logging import get_logger

log = get_logger("metering.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # The whole router, not each route: a route added here later cannot leak into the public
    # document by somebody forgetting the flag.
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

#: A plain, un-bound session: the ingest groups events by organization and binds the tenant GUC
#: per group inside the service layer, so this dependency must not open a transaction of its own.
#: Overriding `get_session` in tests routes ingest through the test's session while leaving that
#: per-group binding intact.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/usage", response_model=UsageIngestOut, summary="Record a batch of usage events")
async def ingest_usage(payload: UsageBatchIn, session: SessionDep) -> UsageIngestOut:
    """Record usage server-to-server. Idempotent on `idempotency_key` per organization."""
    result = await services.record_usage_batch(session, payload.events)
    log.info(
        "usage.ingested",
        recorded=result.recorded,
        duplicates=result.duplicates,
        batch=len(payload.events),
    )
    return result


#: Who may read an organization's usage over the internal API. The billing run is the one
#: reader: usage is a commercial fact, and a sibling service holding a valid metering token has
#: no business enumerating another product's consumption through it.
USAGE_READERS = frozenset({"billing"})


@router.get(
    "/organizations/{organization_id}/usage",
    response_model=OrganizationUsageOut,
    summary="An organization's usage over a closed period, per meter — billing's read",
)
async def organization_usage(
    organization_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    period_start: Annotated[datetime, Query()],
    period_end: Annotated[datetime, Query()],
) -> OrganizationUsageOut:
    """Billable and total quantity per meter over `[period_start, period_end)`.

    Bound to the organization in the path as the transaction's first statement, exactly as a
    customer request is bound to its principal's, so the read runs under row-level security on
    the NOBYPASSRLS app role — and the selector filters `organization_id` explicitly as well.
    """
    caller = getattr(request.state, "service_caller", None)
    if caller not in USAGE_READERS:
        raise ForbiddenProblem("this service may not read usage")
    if period_end <= period_start:
        raise BusinessRuleProblem("period_end must be after period_start")
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        rows = await selectors.period_usage(
            session, organization_id, period_start=period_start, period_end=period_end
        )
    return OrganizationUsageOut(
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
        meters=[
            UsageLineOut(
                meter_key=meter_key,
                unit=unit,
                total_quantity=total,
                billable_quantity=billable,
                event_count=count,
            )
            for meter_key, unit, total, billable, count in rows
        ],
    )
