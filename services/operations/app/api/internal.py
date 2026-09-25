"""The internal API — service-to-service, never reachable from outside the cluster.

Two routes, both for insights (ADR-0020 slice I5, `docs/insights/api.md` §6.1 and §6.3):

- `GET /internal/v1/insights/export/purchase_orders` — the backfill runner's keyset page over
  every purchase order in one organization, shaped like `purchase_order.raised`/`.updated`'s own
  full-state event payload plus `received_date` (export-only).
- `GET /internal/v1/insights/snapshot` — the hourly stock-value pull (ADR-0020 §5): insights
  never consumes `stock.*` events or re-implements ADR-0014's rounding rule, it reads what this
  service's own `domain` functions already compute for `/operations/overview`.

Guarded by a short-lived service token addressed to `operations` by name
(`require_service_token`), narrowed further to caller `insights` — the same
`TRUSTED_CALLER`/`require_*_caller` shape `subscription`'s and `account`'s own internal modules
use for their own narrower routes. `set_tenant()` is the first statement in the export route, on
the NOBYPASSRLS app role, so row-level security confirms every row actually belongs to the named
organization rather than trusting the query filter alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.inventory import selectors as inv_selectors
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("operations.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: insights is the only caller of either route below — a cross-tenant purchase-order export and
#: a pulled stock valuation are both narrower than "any service holding an operations-audience
#: token" should reach.
TRUSTED_CALLER = "insights"


def require_insights_caller(request: Request) -> None:
    """The narrower caller check: valid and addressed to operations (`require_service_token`,
    applied to the whole router, already checked that) and issued by insights specifically."""
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


InsightsCallerDep = Annotated[None, Depends(require_insights_caller)]


def _po_row(po: Any, total_minor: int) -> dict[str, Any]:
    return {
        "id": str(po.id),
        "purchase_order_id": str(po.id),
        "number": po.number,
        "status": po.status,
        "supplier_id": str(po.supplier_id),
        "supplier_name": po.supplier_name,
        "raised_by_id": str(po.raised_by_id) if po.raised_by_id else None,
        "created_date": po.created_date.isoformat(),
        "expected_date": po.expected_date.isoformat(),
        "currency": po.currency,
        "total_minor": total_minor,
        # Export-only, beyond the event's own fields (`docs/insights/api.md` §6.1 table).
        "received_date": po.received_date.isoformat() if po.received_date else None,
        "created_at": po.created_at.isoformat(),
        "updated_at": po.updated_at.isoformat(),
        "deleted_at": po.deleted_at.isoformat() if po.deleted_at else None,
    }


@router.get(
    "/insights/export/purchase_orders",
    summary="Every purchase order's tenant-scoped state, paginated — insights' backfill read",
)
async def purchase_orders_export(
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
        (
            await session.execute(
                selectors.purchase_orders_export_stmt(
                    organization_id=organization_id,
                    after_updated_at=after_updated_at,
                    after_id=after_id,
                    limit=limit,
                )
            )
        )
        .scalars()
        .all()
    )
    lines_by_po = await selectors.lines_map(session, organization_id, [po.id for po in rows])
    out_rows = [_po_row(po, domain.po_total_minor(lines_by_po.get(po.id, []))) for po in rows]
    next_cursor = (
        {"after_updated_at": rows[-1].updated_at.isoformat(), "after_id": str(rows[-1].id)}
        if len(rows) == limit
        else None
    )
    return {"rows": out_rows, "next": next_cursor}


@router.get(
    "/insights/snapshot",
    summary="Stock value, low/out-of-stock counts — insights' hourly pulled capture",
)
async def insights_snapshot(
    session: SessionDep,
    _insights: InsightsCallerDep,
    request: Request,
    organization_id: Annotated[uuid.UUID, Query()],
) -> dict[str, Any]:
    """ADR-0020 §5: computed by this service's own `domain` functions — the same ones
    `/operations/overview` renders — never re-derived from the stock ledger by insights."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)

    items = await inv_selectors.all_inventory(session, organization_id)
    by_currency = domain.inventory_value_by_currency(items)
    body: dict[str, Any] = {
        "organization_id": str(organization_id),
        "low_stock_count": domain.low_stock_count(items),
        "out_of_stock_count": domain.out_of_stock_count(items),
        "item_count": len(items),
        "computed_at": datetime.now(UTC).isoformat(),
    }
    if len(by_currency) <= 1:
        currency, value = next(iter(by_currency.items()), ("INR", 0))
        body["currency"] = currency
        body["inventory_value_minor"] = value
    else:
        body["inventory_value_by_currency"] = by_currency
    return body
