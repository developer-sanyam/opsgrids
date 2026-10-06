"""Resolving the pin from catalog — the one cross-service read this service makes.

A subscribe or a change-plan must pin an **immutable** catalog plan version (ADR-0000 §9), and
it must not take the customer's word for which one: the id has to exist, be published, and be
visible to this organization (public, or a bespoke version negotiated for exactly this org,
ADR-0004 §2). Catalog owns that truth, so this asks catalog over its service-token internal API
rather than reaching across the schema boundary — the same discipline auth-service's entitlement
read follows, and the rule that keeps a later database-per-service split a configuration change.

The internal endpoint returns the version *unfiltered* — including `restricted_to_org_id` — and
this service applies the published-and-visible policy itself (in `services.py`). Pushing the
audience decision to the caller keeps the catalog endpoint a plain read and puts the commercial
rule where the subscription lives.

Unlike auth's entitlement read, a failure here is **not** swallowed: pinning to a version that
could not be validated would be a subscription to a price nobody agreed to. A pin that cannot be
resolved refuses the subscribe.

The second read, `offerable_versions`, is catalog's own answer to "which versions may this
organization be moved onto now" (active plans, published, public or negotiated for it, superseded
versions dropped). The customer's change-plan enforces it, not only shows it (ADR-0033): a picker
loaded before a newer version was published, or a direct call naming any published id, would
otherwise pin a price the catalogue has moved past. It carries amounts; what a tenant may see of
them is decided here, in `app/plan_options.py`, never passed through.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import httpx

from app.schemas import CatalogOfferableVersion, CatalogPlanVersion
from platform_core.config import ServiceSettings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("subscription.catalog")

#: The 503 code a customer surface shows when catalog could not answer: nothing was changed.
CODE_CATALOG_UNAVAILABLE = "catalog_unavailable"


class PlanCatalog(Protocol):
    """What pinning a subscription needs to know about a catalog plan version."""

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogPlanVersion | None: ...

    async def offerable_versions(
        self, organization_id: uuid.UUID, product_key: str
    ) -> list[CatalogOfferableVersion]: ...


def catalog_unavailable(detail: str) -> UpstreamUnavailableProblem:
    return UpstreamUnavailableProblem(
        detail, errors=[{"code": CODE_CATALOG_UNAVAILABLE, "message": detail}]
    )


class HttpPlanCatalog:
    """The real client. Talks to catalog over its service-token internal API."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            # Minted per call, addressed to catalog by name, good for seconds.
            "X-Internal-Token": mint_service_token(self._settings, audience="catalog"),
            "X-Calling-Service": self._settings.service_name,
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation
        return headers

    async def offerable_versions(
        self, organization_id: uuid.UUID, product_key: str
    ) -> list[CatalogOfferableVersion]:
        """Catalog's offerable set for one organization and product. Any failure — transport, a
        refused token, a 5xx — is a 503 `catalog_unavailable`: the caller cannot tell what is on
        offer, so it refuses rather than guesses (a change-plan then changes nothing)."""
        try:
            response = await self._client.get(
                f"/internal/v1/organizations/{organization_id}/offerable-versions",
                params={"product_key": product_key},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            log.error("catalog.offerable_unavailable", error=str(exc)[:200])
            raise catalog_unavailable(
                "catalog is unavailable; cannot check which plans are on offer"
            ) from exc
        if response.status_code != 200:
            log.error("catalog.offerable_refused", status=response.status_code)
            raise catalog_unavailable("catalog did not answer which plans are on offer")
        body = response.json()
        return [CatalogOfferableVersion.model_validate(v) for v in body.get("versions", [])]

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogPlanVersion | None:
        headers = self._headers()

        try:
            response = await self._client.get(
                f"/internal/v1/plan-versions/{plan_version_id}", headers=headers
            )
        except httpx.HTTPError as exc:
            log.error(
                "catalog.unavailable", plan_version_id=str(plan_version_id), error=str(exc)[:200]
            )
            raise UpstreamUnavailableProblem(
                "catalog is unavailable; cannot pin a plan version"
            ) from exc

        if response.status_code == 404:
            # Catalog is reachable and says the version does not exist — a real, actionable "no",
            # distinct from catalog being down (which raised above).
            return None
        response.raise_for_status()
        return CatalogPlanVersion.model_validate(response.json())
