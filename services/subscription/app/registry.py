"""Asking product-service what a product key is — read only when a bundle is being sold.

A bundle subscription entitles the keys its pinned version names. Catalog checked those keys
when the version was authored, but a registry changes after authoring: a member's launch is
rolled back to `soon`, a product is folded into the platform. So the operator path that sells a
bundle asks again, at the moment of sale, and refuses what the registry cannot vouch for. It is
the same service-token internal read catalog makes (`GET /internal/v1/products`), and a failure
refuses the sale (503) rather than trusting an unchecked list — the pin lookup's discipline.

Only the bundle path calls this. A customer buying one product never touches product-service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from platform_core.config import ServiceSettings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("subscription.registry")


@dataclass(frozen=True, slots=True)
class RegisteredProduct:
    key: str
    status: str = "available"
    is_platform: bool = False
    is_bundle: bool = False


class ProductRegistry(Protocol):
    async def products(self) -> dict[str, RegisteredProduct]: ...


class HttpProductRegistry:
    """The real client, over product-service's service-token internal API."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    async def products(self) -> dict[str, RegisteredProduct]:
        headers = {
            "X-Internal-Token": mint_service_token(self._settings, audience="product"),
            "X-Calling-Service": self._settings.service_name,
        }
        correlation = correlation_id_ctx.get()
        if correlation:
            headers["X-Correlation-ID"] = correlation
        try:
            response = await self._client.get("/internal/v1/products", headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("product_registry.unavailable", error=str(exc)[:200])
            raise UpstreamUnavailableProblem(
                "product-service is unavailable; cannot check a bundle's members"
            ) from exc
        return {
            row["key"]: RegisteredProduct(
                key=row["key"],
                status=str(row.get("status", "soon")),
                is_platform=bool(row.get("is_platform")),
                is_bundle=bool(row.get("is_bundle")),
            )
            for row in response.json()["products"]
        }
