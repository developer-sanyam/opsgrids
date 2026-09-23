"""Composing an organization's detail from account-service's staff-only directory route.

`GET /admin/organizations/{id}` is console's one cross-service *read* that is not a delegated
write: it calls account-service's `GET /admin/directory/organizations/{id}` — a `staff_only`
route behind the operator gateway, guarded by the same `require_operator` every staff route
uses — and never `/internal/v1`, which authorizes no individual caller at all and would let any
operator-audience caller read every organization on the platform (`admin-gateway/routes.json`'s
own warning about that router).

The call forwards the **incoming request's own bearer token**, not a service token minted here.
The gateway does not strip `Authorization` on its way through (only the identity headers a
client could spoof), so the token this handler received is still on the request; forwarding it
means account-service's own `require_operator` re-checks the exact audience and role this
request already passed, rather than console vouching for the caller with a credential of its
own. That is the load-bearing difference from `delegation.py`'s overrides, which *do* mint a
service token — those are platform-to-platform writes with no single human caller to forward;
this is one operator's own read, forwarded as theirs.
"""

from __future__ import annotations

import uuid

import httpx

from app.config import Settings
from platform_core.logging import get_logger

log = get_logger("console.directory_client")


class DirectoryLookupError(Exception):
    """account-service refused the lookup or was unreachable. Surfaced as a 503."""


async def get_organization(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    authorization: str,
    client: httpx.AsyncClient,
) -> dict[str, object] | None:
    """The organization's directory record, or `None` when account-service has none.

    Raises `DirectoryLookupError` on anything else — a transport failure or an unexpected
    status — so the caller can tell "does not exist" apart from "could not find out".
    """
    url = f"{settings.account_public_url}/api/v1/admin/directory/organizations/{organization_id}"
    try:
        response = await client.get(url, headers={"Authorization": authorization})
    except httpx.HTTPError as exc:
        raise DirectoryLookupError(f"account-service was unreachable: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DirectoryLookupError(f"account-service refused the lookup: {response.status_code}")
    payload: dict[str, object] = response.json()
    return payload
