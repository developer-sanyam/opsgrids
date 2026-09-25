"""Composing an organization's detail from account-service's staff-only directory route.

`GET /admin/organizations/{id}` is console's one cross-service *read* that is not a delegated
write: it calls account-service's `GET /admin/directory/organizations/{id}` — a service-to-service
route, never `/internal/v1`, which authorizes no individual caller at all and would let any
operator-audience caller read every organization on the platform (`admin-gateway/routes.json`'s
own warning about that router).

MINOR 7 of the 2026-09-24 security review: this call used to **assert** the operator's identity
(`sub`, `operator_roles`, `amr`) inside a service token addressed to account-service, rather than
forward anything account-service could verify independently — which meant any caller holding the
shared internal-token secret could mint a service token claiming to be any operator at all, for
any role. It now forwards the operator's own bearer access token verbatim in `X-Operator-Token`;
account-service verifies it itself, the same way any operator-audience route verifies one (JWKS,
`aud="operator"`, `"otp" in amr`), and uses the token's own `sub` for its audit row rather than
trusting console's word for who is calling.

The second half (m6 of the third-round review): the call **also** carries a plain service token
(`X-Internal-Token`, `aud="account"`, `iss="console"`) that asserts nothing about the operator —
only *who is calling*. account-service requires both, so an operator access token presented
straight to account, bypassing console, is refused.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.directory_client")


class DirectoryLookupError(Exception):
    """account-service refused the lookup or was unreachable. Surfaced as a 503."""


async def get_organization(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    operator_token: str,
    client: httpx.AsyncClient,
) -> dict[str, object] | None:
    """The organization's directory record, or `None` when account-service has none.

    `operator_token` is this request's own raw bearer access token (no `Bearer ` prefix) —
    the same one console's own `require_operator` already verified, forwarded so
    account-service can verify it too rather than trusting an assertion about it.

    Raises `DirectoryLookupError` on anything else — a transport failure or an unexpected
    status — so the caller can tell "does not exist" apart from "could not find out".
    """
    url = f"{settings.account_public_url}/api/v1/admin/directory/organizations/{organization_id}"
    try:
        response = await client.get(
            url,
            headers={
                # Who is calling: console, addressed to account (m6).
                "X-Internal-Token": mint_service_token(
                    settings, audience="account", caller="console"
                ),
                # On whose behalf: the operator's own token, for account to verify itself.
                "X-Operator-Token": operator_token,
            },
        )
    except httpx.HTTPError as exc:
        raise DirectoryLookupError(f"account-service was unreachable: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DirectoryLookupError(f"account-service refused the lookup: {response.status_code}")
    payload: dict[str, object] = response.json()
    return payload


async def list_members(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    operator_token: str,
    client: httpx.AsyncClient,
    page: int,
    size: int,
) -> dict[str, Any] | None:
    """One page of the organization's members, or `None` when account-service has no such
    organization. Same two-credential shape as `get_organization` — the same route family,
    `app/api/admin.py`'s `list_members_for_operator`, just the paginated sibling."""
    url = (
        f"{settings.account_public_url}/api/v1/admin/directory/organizations/"
        f"{organization_id}/members"
    )
    try:
        response = await client.get(
            url,
            params={"page": page, "size": size},
            headers={
                "X-Internal-Token": mint_service_token(
                    settings, audience="account", caller="console"
                ),
                "X-Operator-Token": operator_token,
            },
        )
    except httpx.HTTPError as exc:
        raise DirectoryLookupError(f"account-service was unreachable: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DirectoryLookupError(
            f"account-service refused the members lookup: {response.status_code}"
        )
    payload: dict[str, Any] = response.json()
    return payload
