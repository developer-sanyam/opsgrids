"""Reads for metering. Visibility rules live here, in one place.

Every scoped query filters `organization_id` explicitly **and** runs under row-level security —
the filter is not redundant with RLS: RLS is the net under the tightrope, not permission to walk
without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds its own query instead
of coming through a selector is how an organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent


async def period_usage(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[tuple[str, str, int, int, int]]:
    """Usage for one organization over `[period_start, period_end)`, aggregated per meter.

    Returns `(meter_key, unit, total_quantity, billable_quantity, event_count)` rows. The
    `organization_id` predicate sits beside row-level security, not in place of it — under RLS an
    unbound tenant would already read nothing, and the explicit filter keeps the intent legible
    and the index (which leads with `organization_id`) doing its job.

    Summed straight off the append-only `usage_events`, so the number is always the log's truth;
    `usage_rollups` is only ever a cache of exactly this query.
    """
    billable_qty = func.coalesce(
        func.sum(UsageEvent.quantity).filter(UsageEvent.is_billable.is_(True)), 0
    )
    stmt = (
        select(
            UsageEvent.meter_key,
            func.min(UsageEvent.unit),
            cast(func.coalesce(func.sum(UsageEvent.quantity), 0), BigInteger),
            cast(billable_qty, BigInteger),
            func.count(),
        )
        .where(
            UsageEvent.organization_id == org_id,
            UsageEvent.deleted_at.is_(None),
            UsageEvent.occurred_at >= period_start,
            UsageEvent.occurred_at < period_end,
        )
        .group_by(UsageEvent.meter_key)
        .order_by(UsageEvent.meter_key)
    )
    rows = (await session.execute(stmt)).all()
    return [
        (mk, unit, int(total), int(billable), int(count))
        for mk, unit, total, billable, count in rows
    ]
