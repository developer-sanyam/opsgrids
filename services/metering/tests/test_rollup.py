"""The rebuildable rollup — a cache of the append-only log, never a second source of truth."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.domain import current_period
from tests.helpers import usage_event

pytestmark = pytest.mark.integration


async def test_a_rollup_sums_the_period_and_is_rebuildable(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """The rollup totals the log, splits out the billable slice, and recomputing is a no-op —
    it is derived, so it can be dropped and rebuilt without touching `usage_events`."""
    await services.record_usage_batch(
        session,
        [
            usage_event(org_id, meter_key="core.api_call", quantity=5, is_billable=True),
            usage_event(org_id, meter_key="core.api_call", quantity=3),
        ],
    )
    start, end = current_period()

    async with session.begin():
        rollup = await services.rebuild_rollup(
            session, org_id, "core.api_call", period_start=start, period_end=end
        )
    assert rollup.total_quantity == 8
    assert rollup.billable_quantity == 5

    # Rebuilding from the same log yields the same numbers — an upsert, not an accumulation.
    async with session.begin():
        again = await services.rebuild_rollup(
            session, org_id, "core.api_call", period_start=start, period_end=end
        )
    assert again.total_quantity == 8
    assert again.billable_quantity == 5
