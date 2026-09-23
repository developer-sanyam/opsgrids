"""Asking product-service what a product key is — the one cross-service read catalog makes.

A bundle's `bundled_products` names other products by key, and a subscription to the bundle
entitles exactly those keys (docs/commerce-packaging.md). A typo there is not cosmetic: it is a
customer who pays for three products and is entitled to two, found by them rather than by us. So
catalog checks the list against the product registry **when the version is authored** — while
it is still a draft and an operator is looking at it — rather than letting a published,
immutable version carry a mistake nobody can edit.

Product-service owns the registry (`product.products`), so this asks over its service-token
internal API rather than reading across the schema boundary (ADR-0000 §10). A failure is **not**
swallowed: authoring a bundle that could not be validated is refused with a 503, the same
discipline subscription's pin lookup applies. It is only ever called for a version that declares
a bundle, so every other plan authors exactly as it did.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.domain import RegisteredProduct
from platform_core.config import ServiceSettings
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("catalog.registry")


class ProductRegistry(Protocol):
    """Every registered product, keyed by product key."""

    async def products(self) -> dict[str, RegisteredProduct]: ...


class HttpProductRegistry:
    """The real client. Talks to product-service over its service-token internal API."""

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
                "product-service is unavailable; cannot validate a bundle's members"
            ) from exc
        return {
            row["key"]: RegisteredProduct(
                key=row["key"],
                is_platform=bool(row.get("is_platform")),
                is_bundle=bool(row.get("is_bundle")),
            )
            for row in response.json()["products"]
        }
