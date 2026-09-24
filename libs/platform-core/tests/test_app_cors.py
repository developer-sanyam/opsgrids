"""CORS for the two cross-origin auth routes (ADR-0003 addendum).

The refresh cookie is host-only on `app.opsgrids.com`; every other product SPA calls
`/api/v1/auth/refresh` and `/api/v1/auth/logout` cross-origin to reach it, and each of those
origins needs a *credentialed* CORS allowance to do so. The point under test is that this
allowance stays narrow: an origin on `auth_refresh_cors_origins` may call exactly those two
routes with credentials, and gets nothing from `cors_origins` widening — every other route on
the same gateway answers a cross-origin call from it exactly as it would with no configuration
at all.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from platform_core.app import create_app
from platform_core.config import ServiceSettings


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


def _app(**kwargs: object) -> FastAPI:
    return create_app(_Settings(**kwargs), title="probe", database=False)  # type: ignore[arg-type]


async def _get(app: FastAPI, path: str, origin: str) -> tuple[int, str | None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, headers={"Origin": origin})
    return response.status_code, response.headers.get("access-control-allow-origin")


class TestNoScopedOrigins:
    async def test_falls_back_to_the_ordinary_single_cors_policy(self) -> None:
        app = _app(cors_origins="https://app.opsgrids.com")
        _, allow = await _get(app, "/api/v1/auth/refresh", "https://app.opsgrids.com")
        assert allow == "https://app.opsgrids.com"
        _, allow_elsewhere = await _get(app, "/api/v1/auth/refresh", "https://people.opsgrids.com")
        assert allow_elsewhere is None


class TestScopedAuthOrigins:
    def _app(self) -> FastAPI:
        return _app(
            cors_origins="https://app.opsgrids.com",
            auth_refresh_cors_origins="https://people.opsgrids.com,https://revenue.opsgrids.com",
        )

    async def test_a_listed_product_origin_is_allowed_on_refresh(self) -> None:
        app = self._app()
        _, allow = await _get(app, "/api/v1/auth/refresh", "https://people.opsgrids.com")
        assert allow == "https://people.opsgrids.com"

    async def test_a_listed_product_origin_is_allowed_on_logout(self) -> None:
        app = self._app()
        _, allow = await _get(app, "/api/v1/auth/logout", "https://revenue.opsgrids.com")
        assert allow == "https://revenue.opsgrids.com"

    async def test_an_unlisted_origin_gets_nothing_even_on_the_scoped_routes(self) -> None:
        app = self._app()
        _, allow = await _get(
            app, "/api/v1/auth/refresh", "https://evil-merchant.stores.opsgrids.com"
        )
        assert allow is None

    async def test_a_listed_product_origin_gets_nothing_on_an_ordinary_route(self) -> None:
        """The whole point: the allowance is per-route, not per-origin. A product origin that
        may call refresh/logout must not be able to call anything else on this gateway."""
        app = self._app()
        _, allow = await _get(app, "/health", "https://people.opsgrids.com")
        assert allow is None

    async def test_the_dashboard_origin_still_works_everywhere_via_the_default_policy(
        self,
    ) -> None:
        app = self._app()
        _, allow = await _get(app, "/health", "https://app.opsgrids.com")
        assert allow == "https://app.opsgrids.com"
