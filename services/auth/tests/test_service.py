"""The service boots, answers, and publishes its verification keys.

Unlike the generated smoke test, these go through the `client` fixture — auth-service's
startup loads a signing key, and a test that depends on `make devkeys` having been run is
a test that fails on a fresh clone.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_reports_this_service(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "auth"}


async def test_info_is_mounted_under_the_versioned_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/info")
    assert response.status_code == 200
    assert response.json()["schema_name"] == "auth"


async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    # Every failure in the platform has the same shape, including 404s.
    response = await client.get("/api/v1/auth/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in response.json()


class TestJwks:
    async def test_the_public_keys_are_served_at_the_standard_path(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/.well-known/jwks.json")
        assert response.status_code == 200

        keys = response.json()["keys"]
        assert len(keys) >= 1
        key = keys[0]
        assert key["kty"] == "OKP"
        assert key["crv"] == "Ed25519"
        assert key["alg"] == "EdDSA"
        assert key["use"] == "sig"
        assert key["kid"]

    async def test_the_private_half_is_never_published(self, client: AsyncClient) -> None:
        """The whole security model rests on this.

        `d` is the private scalar in an OKP JWK. If it ever appeared here, every service
        that fetches this document could mint tokens for anyone.
        """
        response = await client.get("/.well-known/jwks.json")
        assert "d" not in response.json()["keys"][0]
        assert '"d"' not in response.text

    async def test_a_token_verifies_against_the_published_key(
        self, client: AsyncClient, cfg, registered
    ) -> None:
        """End to end: what auth signs, a product service verifies with only the JWKS."""
        import jwt
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from tests.conftest import PASSWORD

        login = await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        access = login.json()["access_token"]

        published = (await client.get("/.well-known/jwks.json")).json()["keys"][0]
        public = jwt.PyJWK.from_dict(published).key
        assert isinstance(public, Ed25519PublicKey)

        claims = jwt.decode(
            access, public, algorithms=["EdDSA"], issuer=cfg.jwt_issuer, audience="app"
        )
        assert claims["typ"] == "access"
        assert claims["act_org"]
        assert claims["sid"]
        # No permission list travels in the token — the gateway resolves them per request.
        assert "perms" not in claims

    async def test_the_refresh_token_is_a_cookie_not_a_response_field(
        self, client: AsyncClient, registered
    ) -> None:
        """A refresh token in a response body ends up in `localStorage`, and from there
        in an XSS payload's exfiltration."""
        from app.config import settings
        from tests.conftest import PASSWORD

        response = await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        assert "refresh_token" not in response.json()

        set_cookie = response.headers.get("set-cookie", "").lower()
        assert settings().refresh_cookie_name in set_cookie
        assert "httponly" in set_cookie
        assert "path=/api/v1/auth" in set_cookie


class TestCsrfOnCookieAuthenticatedRoutes:
    """`/auth/refresh` and `/auth/logout` are the two routes a browser will act on using only
    the refresh cookie — no bearer token, no header CORS would ever have forced a preflight
    for on a *same*-site request. A bare cross-site `<form>` POST from anywhere under
    `*.opsgrids.com` — a merchant's storefront included — carries the cookie under
    `SameSite=Lax` but can never add `X-Requested-With`, so the header is what tells a forged
    submission from the shared client apart.
    """

    async def _login(self, client: AsyncClient) -> None:
        from tests.conftest import PASSWORD

        response = await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text

    async def test_refresh_without_the_header_is_refused(
        self, client: AsyncClient, registered
    ) -> None:
        await self._login(client)

        forged = await client.post("/api/v1/auth/refresh")
        assert forged.status_code == 403, forged.text
        assert forged.headers["content-type"].startswith("application/problem+json")

        # Nothing was spent: the legitimate client can still refresh normally afterward.
        genuine = await client.post(
            "/api/v1/auth/refresh", headers={"X-Requested-With": "opsgrids"}
        )
        assert genuine.status_code == 200, genuine.text

    async def test_logout_without_the_header_is_refused(
        self, client: AsyncClient, registered
    ) -> None:
        await self._login(client)

        forged = await client.post("/api/v1/auth/logout")
        assert forged.status_code == 403, forged.text

        # The session survived the forged attempt.
        genuine = await client.post(
            "/api/v1/auth/refresh", headers={"X-Requested-With": "opsgrids"}
        )
        assert genuine.status_code == 200, genuine.text

    async def test_the_header_alone_is_enough_to_log_out(
        self, client: AsyncClient, registered
    ) -> None:
        await self._login(client)

        response = await client.post(
            "/api/v1/auth/logout", headers={"X-Requested-With": "opsgrids"}
        )
        assert response.status_code == 204, response.text
