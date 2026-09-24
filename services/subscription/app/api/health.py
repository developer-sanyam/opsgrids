"""`/health/billing-run` — the billing run's vital signs, for an alarm to scrape.

The same kind of probe as `/health/outbox`, for the same reason: a stopped sweep is the quietest
failure there is. It does not error; trials simply stop converting and nothing is invoiced, and
nobody notices until a month's revenue is missing. `overdue` is the number to alert on — the run
polls every few minutes, so any subscription due for longer than `billing_run_overdue_seconds`
means it is not running or not keeping up.

Counted on the outbox engine (BYPASSRLS, SELECT on `subscriptions` and `billing_periods` only),
because the NOBYPASSRLS app role with no tenant bound would count nothing — the probe would be
blind to exactly the stall it exists to catch. **It returns counts and instants, never an id**:
nothing here identifies a tenant, a subscription or an amount. Like every `/health/*` route it is
reachable on the back network only — the gateways route `/api/v1/...` prefixes and do not proxy
it — and it is deliberately not part of readiness: a billing backlog is not a reason to stop
serving subscription reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import Settings
from platform_core.db import get_outbox_engine

router = APIRouter(tags=["health"], include_in_schema=False)


@router.get("/health/billing-run", summary="Billing run backlog and throughput — scrape this")
async def billing_run_health(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    async with AsyncSession(get_outbox_engine()) as session, session.begin():
        return await selectors.billing_run_health(
            session,
            now=now,
            overdue_after=timedelta(seconds=settings.billing_run_overdue_seconds),
        )
