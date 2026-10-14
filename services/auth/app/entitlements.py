"""Which products an organization may use, asked of product-service.

The same shape as `directory.py`, and for the same reason: auth-service owns credentials and
nothing else. What a customer has bought belongs to product-service, so this asks over the
internal API rather than reading across a schema boundary — the rule that keeps a later
database-per-service split a configuration change.

The answer becomes the `ent` claim in every token minted, and one hop later it is the
difference between a 200 and a 402 at the gateway.

**Failure is quiet on purpose, and this is the interesting decision.** If product-service
cannot be reached, sign-in continues with an empty entitlement list rather than failing. A
customer who cannot log in at all is worse than one who logs in and finds a product
temporarily refusing — the first is a total outage, the second degrades to exactly the state
they would see if they had bought nothing, recovers by itself, and leaves them able to reach
their account. The alternative — refusing to issue any token when a *downstream* service is
unwell — makes product-service a single point of failure for the whole platform.

It is logged at error level, because "everyone silently lost their products" must not be
something you discover from a support ticket.
"""

from __future__ import annotations

import uuid
from typing import Protocol
from urllib.parse import quote

import httpx

from platform_core.config import ServiceSettings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("auth.entitlements")


class Entitlements(Protocol):
    """What token issuing needs to know about what a customer has bought."""

    async def for_organization(self, organization_id: uuid.UUID) -> list[str]: ...


class HttpEntitlements:
    """The real client. Talks to product-service over the internal API."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    async def for_organization(self, organization_id: uuid.UUID) -> list[str]:
        headers = {
            # Minted per call, addressed to product-service, good for seconds.
            "X-Internal-Token": mint_service_token(self._settings, audience="product"),
            "X-Calling-Service": "auth",
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation

        try:
            response = await self._client.get(
                f"/internal/v1/organizations/{quote(str(organization_id), safe='')}/entitlements",
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # See the module docstring: degrade rather than refuse the login.
            log.error(
                "entitlements.unavailable",
                organization_id=str(organization_id),
                error=str(exc)[:200],
            )
            return []

        return list(response.json().get("entitlements", []))
