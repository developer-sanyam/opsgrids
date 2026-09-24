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

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

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


async def _preflight(
    app: FastAPI, path: str, origin: str, *, request_headers: str = "x-requested-with"
) -> tuple[int, dict[str, str]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": request_headers,
            },
        )
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}


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


class TestScopedPreflight:
    """Adopted from the security review's CORS probe (fix round 1) — a preflight this time,
    the request a real cross-origin `fetch` with a JSON body and a custom header actually
    triggers, rather than the simple GET `TestScopedAuthOrigins` above uses."""

    def _app(self) -> FastAPI:
        return _app(
            cors_origins="https://app.opsgrids.com",
            auth_refresh_cors_origins="https://people.opsgrids.com,https://store.opsgrids.com",
        )

    async def test_a_listed_origin_preflights_refresh_successfully(self) -> None:
        app = self._app()
        status, headers = await _preflight(
            app, "/api/v1/auth/refresh", "https://people.opsgrids.com"
        )
        assert status == 200
        assert headers["access-control-allow-origin"] == "https://people.opsgrids.com"
        assert headers["access-control-allow-credentials"] == "true"
        assert "origin" in headers.get("vary", "").lower()

    async def test_a_storefront_origin_is_refused_at_the_preflight(self) -> None:
        app = self._app()
        status, headers = await _preflight(
            app, "/api/v1/auth/refresh", "https://evil.stores.opsgrids.com"
        )
        assert status == 400
        assert "access-control-allow-origin" not in headers

    async def test_authorization_is_not_in_the_scoped_allow_headers(self) -> None:
        """What `authApi.logout()` must never send cross-origin: if it did, this is the
        preflight that would refuse it outright, before the request ever reached the route —
        B2 of the 2026-09-24 review is `logout()` sending `auth: false` so this never happens."""
        app = self._app()
        status, _headers = await _preflight(
            app,
            "/api/v1/auth/logout",
            "https://people.opsgrids.com",
            request_headers="authorization,x-requested-with",
        )
        assert status == 400

    async def test_path_variations_never_get_the_scoped_policy(self) -> None:
        """The scoped paths are matched exactly against the normalised ASGI path — no prefix,
        no case-fold, no dot-segment resolution performed by this layer itself (uvicorn/Starlette
        own whatever normalisation happens before the app ever sees the path)."""
        app = self._app()
        for path in (
            "/api/v1/auth/refresh/",
            "/api/v1/auth/refresh/extra",
            "/API/v1/auth/refresh",
        ):
            _, headers = await _preflight(app, path, "https://people.opsgrids.com")
            assert "access-control-allow-origin" not in headers, path

    async def test_a_wildcard_auth_refresh_cors_origins_is_refused_at_config_time(self) -> None:
        """M2 mutation-kill: `auth_refresh_cors_origins` cannot even be constructed with `*` —
        `platform_core.config`'s validator refuses it before `_PathScopedCorsMiddleware` is ever
        built, closing the misconfiguration this class used to be able to demonstrate at
        runtime (Starlette's own `CORSMiddleware` echoes back any origin, with credentials, once
        a wildcard is configured)."""
        with pytest.raises(ValidationError, match="must not contain"):
            _Settings(cors_origins="https://app.opsgrids.com", auth_refresh_cors_origins="*")
