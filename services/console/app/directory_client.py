"""Composing an organization's detail from account-service's staff-only directory route.

`GET /admin/organizations/{id}` is console's one cross-service *read* that is not a delegated
write: it calls account-service's `GET /admin/directory/organizations/{id}` — a service-to-service
route, never `/internal/v1`, which authorizes no individual caller at all and would let any
operator-audience caller read every organization on the platform (`admin-gateway/routes.json`'s
own warning about that router).

The call **asserts** this request's already-verified operator identity — `sub`, `operator_roles`,
`amr` — in a short-lived service token addressed to account-service (`aud="account"`,
`caller="console"`), the same shape `platform_core.internal_auth` uses for every other
service-to-service call in the platform. It does not forward the operator's own bearer token
onward: that token has already done its one job (this request already passed console's own
`require_operator`), and there is no reason for a second service to become another place that
credential can leak. account-service re-checks the asserted role and second-factor claims
independently rather than trusting console's word for it, and writes its own audit row for the
read.
"""

from __future__ import annotations

import uuid

import httpx

from app.config import Settings
from platform_core.auth import Principal
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.directory_client")


class DirectoryLookupError(Exception):
    """account-service refused the lookup or was unreachable. Surfaced as a 503."""


async def get_organization(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    operator: Principal,
    client: httpx.AsyncClient,
) -> dict[str, object] | None:
    """The organization's directory record, or `None` when account-service has none.

    Raises `DirectoryLookupError` on anything else — a transport failure or an unexpected
    status — so the caller can tell "does not exist" apart from "could not find out".
    """
    url = f"{settings.account_public_url}/api/v1/admin/directory/organizations/{organization_id}"
    token = mint_service_token(
        settings,
        audience="account",
        caller="console",
        claims={
            "operator_sub": str(operator.sub),
            "operator_roles": list(operator.roles),
            "operator_amr": list(operator.amr),
        },
    )
    try:
        response = await client.get(url, headers={"X-Internal-Token": token})
    except httpx.HTTPError as exc:
        raise DirectoryLookupError(f"account-service was unreachable: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DirectoryLookupError(f"account-service refused the lookup: {response.status_code}")
    payload: dict[str, object] = response.json()
    return payload
