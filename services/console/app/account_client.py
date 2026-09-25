"""Calling account-service's bulk internal read — the backfill's org-identity discovery.

Distinct from `directory_client.py`: that module is the staff-only, operator-token-and-service-
token composite `GET /admin/organizations/{id}` uses, one organization at a time, on behalf of a
signed-in operator. This one is `app/backfill.py`'s discovery step, which runs with no operator at
all — a one-off script — so it calls account-service's `/internal/v1/organizations`
(`account/app/api/internal.py`), service-token-only, and pages through every organization on the
platform.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.account_client")

ACCOUNT_AUDIENCE = "account"

#: A page at a time, capped at what account-service's own route allows
#: (`MAX_ORGANIZATIONS_PAGE_SIZE`). The platform has a few dozen organizations today; this is
#: sized for years of growth, not today's count.
PAGE_SIZE = 200

#: How long a best-effort enrichment call (`member_counts`) may take before the list it decorates
#: gives up on it and renders without.
ENRICHMENT_TIMEOUT_SECONDS = 3.0


class AccountLookupError(Exception):
    """account-service refused the bulk read, or was unreachable. Surfaced as a 503 by the
    backfill's own caller (or simply logged and left for the next run — see `app/backfill.py`)."""


async def list_organizations(
    settings: Settings, *, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Every live organization's identity, oldest-id-first, across every keyset page.

    Raises `AccountLookupError` on the first page that fails — a partial backfill from a half
    -read organization list would silently skip whatever came after the failure, which is worse
    than refusing the whole run and letting the operator retry.
    """
    url = f"{settings.account_public_url}/internal/v1/organizations"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=ACCOUNT_AUDIENCE, caller="console"
        )
    }
    organizations: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AccountLookupError(f"account-service is unavailable: {exc}") from exc
        page = response.json()
        organizations.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return organizations


async def member_counts(settings: Settings, *, client: httpx.AsyncClient) -> dict[str, int]:
    """Every organization's member count, keyed by organization id as a string — the accounts
    list's Users column (`app/api/routes.py::list_organizations`). Merged in live on every call
    rather than folded and stored: no `core.*` event tells console when a membership is added or
    removed, so a stored count would only ever be as fresh as the last backfill run.

    Best-effort from the caller's point of view: raises `AccountLookupError` like every other
    call here, but `list_organizations` treats that as "no counts this time" rather than failing
    the whole list — a list that already renders MRR, lifecycle and everything else should not
    go blank because one enrichment call timed out.
    """
    url = f"{settings.account_public_url}/internal/v1/organizations/member-counts"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=ACCOUNT_AUDIENCE, caller="console"
        )
    }
    try:
        # Shorter than the shared client's default: this is enrichment on a list that already
        # renders without it, so a hung account-service must cost the operator seconds, not a
        # 10-second wait for a column that was going to be `null` anyway.
        response = await client.get(url, headers=headers, timeout=ENRICHMENT_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AccountLookupError(f"account-service is unavailable: {exc}") from exc
    body = response.json()
    return {str(row["organization_id"]): int(row["count"]) for row in body["counts"]}
