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
