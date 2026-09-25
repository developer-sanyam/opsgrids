"""The shared HTTP shape behind every source's `GET /internal/v1/insights/export/{entity}`
(ADR-0020 §3, `docs/insights/api.md` §6.1) — one retrying page fetch, parameterised by which
service and which path, so I5's operations client, I6's projects client and I7's storefront
client (`app/backfill.py`'s `ENTITY_REGISTRY`) do not each reimplement
`app/account_client.py:fetch_page`'s retry loop.

Deliberately generic on the row shape (`list[dict[str, Any]]`): each entity's own `apply_page`
(`app/backfill.py`) parses only the fields its fact table needs, exactly as a consumer fold does
for an event payload (`docs/insights/events.md` §1).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("insights.entity_export_client")

CALLER = "insights"

#: ADR-0020 §3: "timeouts are 5 s to connect and 30 s to read, with 3 retries at 2/8/30 s."
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)


class EntityExportError(Exception):
    """The source refused the export, or was unreachable after every retry."""


@dataclass(frozen=True, slots=True)
class ExportEndpoint:
    """Which service, which audience to mint a token for, and which path — the three things
    that differ between operations' `purchase_orders`, projects' `tasks` and storefront's
    `enquiries` (`docs/insights/api.md` §6.1's own table)."""

    service_url: str
    audience: str
    path: str


async def fetch_page(
    settings: Settings,
    client: httpx.AsyncClient,
    endpoint: ExportEndpoint,
    *,
    organization_id: uuid.UUID,
    cursor: dict[str, str] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """One page, retried per ADR-0020 §3. Raises `EntityExportError` once every retry is
    exhausted — the backfill runner marks that run `failed` and lets it retry on its own hourly
    cadence, exactly as it would for any other source that is down."""
    url = f"{endpoint.service_url}{endpoint.path}"
    headers = {
        "X-Internal-Token": mint_service_token(settings, audience=endpoint.audience, caller=CALLER)
    }
    params: dict[str, Any] = {"organization_id": str(organization_id), "limit": limit}
    if cursor is not None:
        params["after_updated_at"] = cursor["after_updated_at"]
        params["after_id"] = cursor["after_id"]

    attempts = len(RETRY_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
            return list(body["rows"]), body.get("next")
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                await asyncio.sleep(RETRY_BACKOFFS_SECONDS[attempt])
    raise EntityExportError(f"{endpoint.path} export is unavailable: {last_error}")
