"""The internal API — service-to-service, never reachable from outside the cluster.

One route so far: insights' backfill export (ADR-0020 §3, `docs/insights/api.md` §6.1).
Restricted to `service_caller == "insights"` and bound to the named organization as the first
statement inside the transaction, the same shape `services/deal/app/api/internal.py` and
`services/customer/app/api/internal.py` already take for their own internal routes.

Mounted at the root and excluded from the OpenAPI document: not public API, and it must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.schemas import LeadExportCursorOut, LeadExportPageOut, LeadExportRowOut
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("lead.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: insights is the only caller of this router today (ADR-0020 §3) — a narrow, named allow-list
#: rather than "any service holding a valid token", the same pattern account's
#: `require_insights_caller` and deal's `_require_insights_caller` take for their own export.
TRUSTED_INSIGHTS_CALLER = "insights"


def _require_insights_caller(request: Request) -> None:
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_INSIGHTS_CALLER:
        raise UnauthorizedProblem("Valid service token required")


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the caller's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


@router.get(
    "/insights/export/leads",
    response_model=LeadExportPageOut,
    summary="Every lead, paginated — insights' backfill read (ADR-0020 slice I4)",
)
async def export_leads(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    after_updated_at: Annotated[datetime | None, Query()] = None,
    after_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> LeadExportPageOut:
    """`docs/insights/api.md` §6.1: keyset-paginated on `(updated_at, id)`, soft-deleted rows
    included so a deletion propagates. No name, email or phone leaves lead-service on this
    route — see `LeadExportRowOut`'s own docstring.
    """
    _require_insights_caller(request)
    async with session.begin():
        await _bind(session, organization_id, request)
        rows = (
            (
                await session.execute(
                    selectors.leads_export_stmt(
                        after_updated_at=after_updated_at, after_id=after_id, limit=limit
                    )
                )
            )
            .scalars()
            .all()
        )
    next_cursor = (
        LeadExportCursorOut(after_updated_at=rows[-1].updated_at, after_id=rows[-1].id)
        if len(rows) == limit
        else None
    )
    return LeadExportPageOut(
        rows=[LeadExportRowOut.model_validate(row) for row in rows], next=next_cursor
    )
