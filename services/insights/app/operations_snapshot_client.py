"""operations' `GET /internal/v1/insights/snapshot` (ADR-0020 §5, slice I5,
`docs/insights/api.md` §6.3) — the hourly sweep's pulled stock-value read
(`app/stock_capture.py`). One request per organization per hour; insights never re-derives
stock value from the ledger (ADR-0020 §5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("insights.operations_snapshot_client")

CALLER = "insights"
AUDIENCE = "operations"
PATH = "/internal/v1/insights/snapshot"

#: `docs/insights/metrics.md` §8: "5 s timeout, 1 retry."
TIMEOUT_SECONDS = 5.0
RETRIES = 1


class OperationsSnapshotError(Exception):
    """operations refused the pull, or was unreachable after its one retry."""


@dataclass(frozen=True, slots=True)
class StockSnapshot:
    currency_totals: dict[str, int]  # {"INR": 45230000} — never summed across currencies
    low_stock_count: int
    out_of_stock_count: int
    item_count: int


async def fetch_stock_snapshot(
    settings: Settings, client: httpx.AsyncClient, *, organization_id: uuid.UUID
) -> StockSnapshot:
    url = f"{settings.operations_service_url}{PATH}"
    headers = {"X-Internal-Token": mint_service_token(settings, audience=AUDIENCE, caller=CALLER)}
    params = {"organization_id": str(organization_id)}

    last_error: Exception | None = None
    for _attempt in range(RETRIES + 1):
        try:
            response = await client.get(
                url, params=params, headers=headers, timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            if "inventory_value_by_currency" in body:
                totals = {k: int(v) for k, v in body["inventory_value_by_currency"].items()}
            elif body.get("currency") is not None:
                totals = {str(body["currency"]): int(body["inventory_value_minor"])}
            else:
                totals = {}
            return StockSnapshot(
                currency_totals=totals,
                low_stock_count=int(body["low_stock_count"]),
                out_of_stock_count=int(body["out_of_stock_count"]),
                item_count=int(body["item_count"]),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
    raise OperationsSnapshotError(f"operations stock snapshot is unavailable: {last_error}")
