"""The hourly pulled stock capture (ADR-0020 §5, slice I5, `docs/insights/metrics.md` §8):
`stock_value`, `low_stock_items` and `out_of_stock_items` from operations' own
`GET /internal/v1/insights/snapshot` (`app/operations_snapshot_client.py`) — one pull writes all
three, because that is what one call to operations already returns; three separate `capture()`s
would triple the hourly load on operations for the same numbers.

"An organization whose response reports `item_count = 0`, and which has no earlier capture, is
skipped rather than recorded as zero" (`docs/insights/metrics.md` §8) — a zero the first time
insights ever asks is far more likely "operations has never priced anything for this org yet"
than "this org genuinely holds no stock", and recording it would poison `metric_coverage`'s
`first_capture` basis with a day that is not real history.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import operations_snapshot_client, services
from app.config import Settings
from app.models import DailySnapshot
from platform_core.logging import get_logger

log = get_logger("insights.stock_capture")

METRIC_STOCK_VALUE = "stock_value"
METRIC_LOW_STOCK = "low_stock_items"
METRIC_OUT_OF_STOCK = "out_of_stock_items"


class StockCapture:
    """Implements `app.snapshot.SnapshotCapture`. `metric` names the primary one for logging;
    `capture()` writes all three this pull produces."""

    metric = METRIC_STOCK_VALUE

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        snapshot = await operations_snapshot_client.fetch_stock_snapshot(
            self._settings, self._client, organization_id=organization_id
        )
        if snapshot.item_count == 0:
            has_prior = (
                await session.execute(
                    select(
                        exists().where(
                            DailySnapshot.organization_id == organization_id,
                            DailySnapshot.metric == METRIC_STOCK_VALUE,
                        )
                    )
                )
            ).scalar_one()
            if not has_prior:
                log.info(
                    "insights.snapshot.stock_skipped_no_items",
                    organization_id=str(organization_id),
                )
                return

        for currency, value in snapshot.currency_totals.items():
            await services.upsert_daily_snapshot(
                session,
                organization_id=organization_id,
                metric=METRIC_STOCK_VALUE,
                dimension=currency,
                day=day,
                value=value,
                currency=currency,
                captured_at=now,
            )
        await services.upsert_daily_snapshot(
            session,
            organization_id=organization_id,
            metric=METRIC_LOW_STOCK,
            dimension="",
            day=day,
            value=snapshot.low_stock_count,
            currency=None,
            captured_at=now,
        )
        await services.upsert_daily_snapshot(
            session,
            organization_id=organization_id,
            metric=METRIC_OUT_OF_STOCK,
            dimension="",
            day=day,
            value=snapshot.out_of_stock_count,
            currency=None,
            captured_at=now,
        )
