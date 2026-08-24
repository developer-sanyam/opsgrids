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

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.schemas import UsageBatchIn, UsageIngestOut
from platform_core.auth import require_service_token
from platform_core.db import get_session
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
