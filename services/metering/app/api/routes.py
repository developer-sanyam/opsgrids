"""Customer HTTP surface for metering — a read-only view of this organization's usage.

Fronted by the product gateway at `/api/v1/usage`. There is no customer *write* here: usage is
recorded server-to-server by product services through the internal API, never by a user token.
A customer may only *see* what they have consumed.

The read goes through the tenancy chokepoint (`get_tenant_session`), which binds the transaction
to the caller's organization, and is guarded by a permission — so a leak needs both a wrong
tenant binding (which row-level security refuses) and a missing permission check (covered by a
negative-authz test).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import settings
from app.models import SCHEMA
from app.schemas import ServiceInfo, UsageLineOut, UsageSummaryOut
from platform_core.auth import ScopeContext, require
from platform_core.db import get_tenant_session

router = APIRouter(tags=["metering"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so the usage read cannot see another tenant's events.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: Viewing your own metered consumption is a read on the commercial side of your product
#: activations, so it reuses the seeded `core.product.activation.read` permission — held by the
#: owner/administrator/billing roles that also read activations and plans. Reusing an existing
#: platform permission rather than minting an unseeded one keeps the endpoint reachable by real
#: tokens today, the same choice catalog made for its plan read. No scope floor: usage is
#: organization-wide here, and RLS plus the org filter is the isolation.
UsageRead = Annotated[ScopeContext, Depends(require("core.product.activation.read"))]


@router.get("/usage/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/usage",
    response_model=UsageSummaryOut,
    summary="This organization's usage for the current period",
)
async def current_usage(session: SessionDep, ctx: UsageRead) -> UsageSummaryOut:
    """Current-period usage for the caller's organization, one line per meter.

    The period is the calendar month in UTC (`domain.current_period`) until a subscription's own
    billing window is wired in. Summed directly off the append-only `usage_events`, so the figure
    is the log's truth rather than a possibly-stale rollup.
    """
    period_start, period_end = domain.current_period()
    rows = await selectors.period_usage(
        session, ctx.organization_id, period_start=period_start, period_end=period_end
    )
    lines = [
        UsageLineOut(
            meter_key=meter_key,
            unit=unit,
            total_quantity=total,
            billable_quantity=billable,
            event_count=count,
        )
        for meter_key, unit, total, billable, count in rows
    ]
    return UsageSummaryOut(
        organization_id=ctx.organization_id,
        period_start=period_start,
        period_end=period_end,
        lines=lines,
    )
