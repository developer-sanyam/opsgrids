"""Calling account-service's organization export (ADR-0020 §3, §6.2).

`GET /internal/v1/organizations/export` is account's own **new** cross-tenant read
(ADR-0002's enumerated bypass list gains this route in this slice — see
`services/account/app/api/internal.py`'s module docstring). It is deliberately narrower than the
existing `GET /internal/v1/organizations` console's backfill uses: no name, slug or domain
leaves account on this route, only `organization_id`, `timezone`, `currency`,
`fiscal_year_start_month`, `status` and `updated_at`, keyset-paginated on `(updated_at, id)`, for
trusted caller `insights` alone.

The backfill runner's `account.organizations` entity (`app/backfill.py`) is the one caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("insights.account_client")

ACCOUNT_AUDIENCE = "account"
CALLER = "insights"

#: `docs/insights/api.md` §6.2: 500 per page.
PAGE_SIZE = 500

#: ADR-0020 §3: "timeouts are 5 s to connect and 30 s to read, with 3 retries at 2/8/30 s."
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)


@dataclass(frozen=True, slots=True)
class OrganizationExportRow:
    organization_id: str
    timezone: str
    currency: str
    fiscal_year_start_month: int
    status: str
    updated_at: str


class AccountExportError(Exception):
    """account-service refused the export, or was unreachable after every retry."""


def _client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.backfill_connect_timeout_seconds,
            read=settings.backfill_read_timeout_seconds,
            write=settings.backfill_read_timeout_seconds,
            pool=settings.backfill_connect_timeout_seconds,
        )
    )


async def fetch_page(
    settings: Settings,
    *,
    client: httpx.AsyncClient,
    after_updated_at: str | None,
    after_id: str | None,
    limit: int = PAGE_SIZE,
) -> tuple[list[OrganizationExportRow], dict[str, str] | None]:
    """One page of the organization export, retried per ADR-0020 §3. Raises `AccountExportError`
    once every retry is exhausted — the caller (`app/backfill.py`) marks that run `failed` and
    lets it retry on the runner's own hourly cadence, exactly as it would for any other source
    that is down.
    """
    url = f"{settings.account_service_url}/internal/v1/organizations/export"
    headers = {
        "X-Internal-Token": mint_service_token(settings, audience=ACCOUNT_AUDIENCE, caller=CALLER)
    }
    params: dict[str, Any] = {"limit": limit}
    if after_updated_at is not None:
        params["after_updated_at"] = after_updated_at
    if after_id is not None:
        params["after_id"] = after_id

    attempts = len(RETRY_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
            rows = [OrganizationExportRow(**row) for row in body["rows"]]
            return rows, body.get("next")
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                await asyncio.sleep(RETRY_BACKOFFS_SECONDS[attempt])
    raise AccountExportError(f"account organization export is unavailable: {last_error}")


async def fetch_all(settings: Settings) -> list[OrganizationExportRow]:
    """Every organization, oldest-`updated_at`-first, across every keyset page. Used by the
    backfill runner's discovery step (`app/backfill.py`) and by `bin/backfill --verify`."""
    rows: list[OrganizationExportRow] = []
    async with _client(settings) as client:
        after_updated_at: str | None = None
        after_id: str | None = None
        while True:
            page, cursor = await fetch_page(
                settings, client=client, after_updated_at=after_updated_at, after_id=after_id
            )
            rows.extend(page)
            if not cursor:
                break
            after_updated_at = cursor["after_updated_at"]
            after_id = cursor["after_id"]
    return rows
