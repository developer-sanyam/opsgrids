"""The internal API — service-to-service, never reachable from outside the cluster.

`GET /internal/v1/insights/export/enquiries` — insights' backfill read (ADR-0020 slice I7,
`docs/insights/api.md` §6.1): a keyset page over every enquiry in one organization, shaped like
`enquiry.created`'s own full-state event payload plus `status` (export-only per the table there;
already carried on the row).

A separate module from `app/api/internal.py` (Caddy's `tls-authorize`, mounted without an
`/internal/v1` prefix and reached on the compose network directly): that one is a different
caller, a different trust boundary and a different path shape. This one follows the same
`TRUSTED_CALLER`/`require_*_caller` shape `subscription`'s, `account`'s, `operations`'s and
`projects`'s own internal modules use. `set_tenant()` is the first statement, on the NOBYPASSRLS
app role, so row-level security confirms every row actually belongs to the named organization
rather than trusting the query filter alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import Enquiry
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("storefront.internal.insights")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: insights is the only caller of this route — a cross-tenant enquiry export is narrower than
#: "any service holding a storefront-audience token" should reach.
TRUSTED_CALLER = "insights"


def require_insights_caller(request: Request) -> None:
    """The narrower caller check: valid and addressed to storefront (`require_service_token`,
    applied to the whole router, already checked that) and issued by insights specifically."""
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


InsightsCallerDep = Annotated[None, Depends(require_insights_caller)]


def _enquiry_row(enquiry: Enquiry, store_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(enquiry.id),
        "enquiry_id": str(enquiry.id),
        "store_id": str(store_id) if store_id else None,
        "company": enquiry.company,
        "status": enquiry.status,
        "line_count": len(enquiry.lines),
        "lines": enquiry.lines,
        "created_at": enquiry.created_at.isoformat(),
        "updated_at": enquiry.updated_at.isoformat(),
        "deleted_at": enquiry.deleted_at.isoformat() if enquiry.deleted_at else None,
    }


@router.get(
    "/insights/export/enquiries",
    summary="Every enquiry's tenant-scoped state, paginated — insights' backfill read",
)
async def enquiries_export(
    session: SessionDep,
    _insights: InsightsCallerDep,
    request: Request,
    organization_id: Annotated[uuid.UUID, Query()],
    after_updated_at: Annotated[datetime | None, Query()] = None,
    after_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> dict[str, Any]:
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)

    rows = (
        await session.execute(
            selectors.enquiries_export_stmt(
                organization_id=organization_id,
                after_updated_at=after_updated_at,
                after_id=after_id,
                limit=limit,
            )
        )
    ).all()
    next_cursor = (
        {"after_updated_at": rows[-1][0].updated_at.isoformat(), "after_id": str(rows[-1][0].id)}
        if len(rows) == limit
        else None
    )
    return {
        "rows": [_enquiry_row(enquiry, store_id) for enquiry, store_id in rows],
        "next": next_cursor,
    }
