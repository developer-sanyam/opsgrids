"""Calling catalog for the pricing screen's two reads and its one write.

Console never authors a plan version itself (ADR-0004 §2 is catalog's alone to enforce). The
write here calls catalog's narrow `POST /admin/internal/catalog/.../bespoke-price`
(`catalog/app/api/console_internal.py`), which takes two credentials the same way
`directory_client.get_organization` does for account: a service token addressed to catalog by
name (who is calling) and the operator's own bearer access token, forwarded verbatim (on whose
behalf, and with what role) — catalog verifies both itself, exactly as `require_operator` verifies
any other operator-audience call, rather than trusting console's word for either.

The two reads (`public_flat_plans`, `plan_version`) reuse catalog's existing, already-open
internal endpoints and need only the service token.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.catalog_client")

CATALOG_AUDIENCE = "catalog"


class CatalogError(Exception):
    """catalog refused the call, or was unreachable.

    `status_code` mirrors catalog's own answer when it gave one (404 unknown product, 409 no
    public flat plan to clone, 422 a validation catalog itself rejected) — the caller maps those
    onto the same problem; anything else (no status, a 5xx, a transport failure) is a 503.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(
            settings, audience=CATALOG_AUDIENCE, caller="console"
        )
    }


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else response.text[:300]


async def get_public_flat_plans(
    settings: Settings, *, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Every product's public flat plan — the pricing screen's baseline column."""
    url = f"{settings.catalog_internal_url}/internal/v1/public-flat-plans"
    try:
        response = await client.get(url, headers=_headers(settings))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CatalogError(f"catalog is unavailable: {exc}") from exc
    plans: list[dict[str, Any]] = response.json()["plans"]
    return plans


async def get_plan_version(
    settings: Settings, plan_version_id: uuid.UUID, *, client: httpx.AsyncClient
) -> dict[str, Any] | None:
    """One plan version, unfiltered — used to price an organization's current pin. `None` for a
    version catalog no longer knows (should not happen for a live pin; treated as "cannot show a
    current price" rather than failing the whole pricing screen).

    Deliberately typed `uuid.UUID`, not `str` (MAJOR-1, fix round 1): a caller reading an id out
    of another service's JSON response must parse it first, so an interpolated path segment is
    always a real UUID's own `str()` and never an arbitrary string from a downstream body.
    """
    url = f"{settings.catalog_internal_url}/internal/v1/plan-versions/{plan_version_id}"
    try:
        response = await client.get(url, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise CatalogError(f"catalog is unavailable: {exc}") from exc
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CatalogError(f"catalog refused the read: {response.status_code}") from exc
    result: dict[str, Any] = response.json()
    return result


async def ensure_bespoke_price(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    amount_minor: int,
    currency: str,
    billing_interval: str,
    operator_token: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Clone the product's public flat plan into a published, audience-of-one price for
    `organization_id` — idempotent by content on catalog's side (a repeat with the same
    organization/product/price reuses the version it already made)."""
    # MAJOR-1 (fix round 1): `product_key` is validated at the boundary (SetPricingIn's own
    # pattern), but this is the one place it becomes a URL path segment two hops downstream —
    # percent-encoded here too, on the assumption that a boundary validator can be loosened or
    # bypassed (a direct call to this function from a future caller, say) without this line
    # noticing. `organization_id` needs no equivalent: it is a `uuid.UUID` object, and `str()`
    # of one is always exactly 36 hex-and-hyphen characters.
    url = (
        f"{settings.catalog_internal_url}/admin/internal/catalog/organizations/"
        f"{organization_id}/products/{quote(product_key, safe='')}/bespoke-price"
    )
    headers = _headers(settings)
    headers["Authorization"] = f"Bearer {operator_token}"
    body = {
        "amount_minor": amount_minor,
        "currency": currency,
        "billing_interval": billing_interval,
    }
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CatalogError(f"catalog is unavailable: {exc}") from exc
    if response.status_code == 401:
        # Console's own service token was refused — an internal credential problem, never the
        # caller's fault, so it surfaces as a 503 like any other upstream failure (no
        # status_code set here, which is CatalogError's "unclassified" bucket the caller maps
        # to 503) rather than a 401 that would misdirect the operator console.
        log.error(
            "pricing.catalog_call_refused",
            organization_id=str(organization_id),
            product_key=product_key,
            status_code=response.status_code,
        )
        raise CatalogError("catalog refused console's own service credentials")
    if response.status_code >= 400:
        # MAJOR-2 (fix round 1): a 403 here is catalog's own `require_operator` role check
        # genuinely refusing the operator's role — a real, caller-facing authorization
        # decision, not an internal credential problem (that is the 401 case above). It must
        # reach the caller as the 403 it is, not be folded into the generic 503 "catalog is
        # unavailable" bucket the way it used to be.
        raise CatalogError(_detail(response), status_code=response.status_code)
    result: dict[str, Any] = response.json()
    return result
