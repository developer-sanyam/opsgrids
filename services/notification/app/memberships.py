"""Verifying an assignment target before this service trusts it.

`POST /engage/conversations/{id}:assign` takes an `assignee_membership_id` typed or picked by the
caller. Nothing before this module checked it belonged to the caller's own organization at all —
an id from another tenant, or one that never existed, was written to `conversations` exactly as
though it were real, and the inbox would have shown a conversation "assigned" to nobody
reachable. `account`-service owns the roster; this asks it the one question that matters —
`GET /internal/v1/organizations/{org}/memberships/{id}` — before the write happens.

**Fails closed.** Unlike `app/contacts.py`'s attribution lookup, which degrades a *display* fact
(whose name to show) to "unknown" when account cannot be reached, this gates a *write*: assigning
a conversation to an id that turns out to belong to nobody, or to another organization, is not a
cosmetic gap the inbox shows plainly — it is a wrong assignment nobody would notice until the
person who was supposed to answer never does. So anything short of a confirmed, active membership
— including account-service being unreachable — refuses the assign with `422` rather than writing
it optimistically.
"""

from __future__ import annotations

from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("notification.memberships")

#: The audience the token is addressed to. A token minted for account cannot be replayed against
#: another service.
ACCOUNT_AUDIENCE: Final = "account"


async def membership_is_active(
    settings: Settings,
    organization_id: str,
    membership_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Does `membership_id` belong to `organization_id` and hold a membership someone could
    actually be working from right now? `False` for every failure mode alike — unreachable, a
    timeout, an unrecognisable body, a 4xx or 5xx — because a service that cannot confirm an
    assignment target must refuse it, not assume it is fine.
    """
    url = (
        f"{settings.account_internal_url}/internal/v1/organizations/{organization_id}"
        f"/memberships/{membership_id}"
    )
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=ACCOUNT_AUDIENCE, caller=settings.service_name
        )
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.account_timeout_seconds)
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("memberships.unreachable", reason=str(exc))
        return False
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        log.warning("memberships.refused", status=response.status_code)
        return False

    try:
        body = response.json()
    except ValueError:
        log.warning("memberships.unparseable_response")
        return False
    if not isinstance(body, dict):
        log.warning("memberships.unparseable_response")
        return False

    return bool(body.get("exists")) and bool(body.get("active"))
