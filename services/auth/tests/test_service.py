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


class TestTheRefreshCookieOutlivesTheBrowser:
    """A session must survive the browser being closed and reopened (a production tester's
    report, 2026-10-15). The access token lives in memory and `sessionStorage`, both of which a
    new browser run loses; what restores the session is the boot-time silent refresh, and that
    only works if the refresh cookie itself was persisted. A cookie without `Max-Age`/`Expires`
    is a *session* cookie the browser discards on exit — these pin that it is not one, on
    sign-in and on every rotation, and that clearing it names the same scope it was set with
    (a deletion whose `Domain` differs from the set's is a different cookie, and leaves the
    real one behind)."""

    @staticmethod
    def _refresh_cookie(response) -> str:
        from app.config import settings

        name = settings().refresh_cookie_name
        matches = [c for c in response.headers.get_list("set-cookie") if c.startswith(name + "=")]
        assert len(matches) == 1, response.headers.get_list("set-cookie")
        return matches[0].lower()

    async def _login(self, client: AsyncClient):
        from tests.conftest import PASSWORD

        response = await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return response

    async def test_sign_in_sets_a_persistent_lax_host_only_cookie(
        self, client: AsyncClient, registered
    ) -> None:
        from app.config import settings

        cookie = self._refresh_cookie(await self._login(client))
        assert f"max-age={settings().refresh_token_ttl_seconds}" in cookie
        # Max-Age alone makes it persistent (RFC 6265 §5.3: it takes precedence over Expires,
        # and every supported browser honours it).
        assert "samesite=lax" in cookie
        assert "httponly" in cookie
        assert "path=/api/v1/auth" in cookie
        # Host-only by default (ADR-0003 addendum): never a `.opsgrids.com` cookie unless a
        # deployment deliberately configures one.
        assert "domain=" not in cookie

    async def test_a_rotation_sets_a_persistent_cookie_too(
        self, client: AsyncClient, registered
    ) -> None:
        from app.config import settings

        await self._login(client)
        rotated = await client.post(
            "/api/v1/auth/refresh", headers={"X-Requested-With": "opsgrids"}
        )
        assert rotated.status_code == 200, rotated.text
        cookie = self._refresh_cookie(rotated)
        assert f"max-age={settings().refresh_token_ttl_seconds}" in cookie
        assert "httponly" in cookie

    async def test_the_cookie_is_secure_outside_development(
        self, client: AsyncClient, registered, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings(), "environment", "production")
        assert "secure" in self._refresh_cookie(await self._login(client)).split("; ")

    async def test_logout_clears_the_cookie_with_the_scope_it_was_set_with(
        self, client: AsyncClient, registered, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings(), "refresh_cookie_domain", "app.example.test")
        set_cookie = self._refresh_cookie(await self._login(client))
        assert "domain=app.example.test" in set_cookie

        out = await client.post("/api/v1/auth/logout", headers={"X-Requested-With": "opsgrids"})
        assert out.status_code == 204, out.text
        cleared = self._refresh_cookie(out)
        assert "max-age=0" in cleared
        assert "path=/api/v1/auth" in cleared
        assert "domain=app.example.test" in cleared


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


VISITOR = "198.51.100.42"


def via_gateway(address: str, *, bound: str | None = None) -> dict[str, str]:
    """Headers as the gateway forwards an anonymous request: the visitor's address, under a hop
    token that names nobody and binds `bound` (the address itself unless told otherwise)."""
    from app.config import settings
    from platform_core.internal_auth import (
        GATEWAY_AUDIENCE,
        forwarded_headers_digest,
        mint_service_token,
    )

    fwd = forwarded_headers_digest({"x-client-ip": bound or address})
    token = mint_service_token(
        settings(), audience=GATEWAY_AUDIENCE, caller="gateway", claims={"fwd": fwd}
    )
    return {"X-Internal-Token": token, "X-Client-Ip": address}


class TestTheVisitorsAddress:
    """Behind the gateway, `request.client.host` is the gateway's container — the same address
    for every visitor — so a reset or sign-in record built from it says nothing about who asked.
    The address recorded is the one the gateway resolved and bound into its hop token."""

    async def _reset_ip(self, session) -> str | None:
        from sqlalchemy import select

        from app.models import PasswordResetToken

        row = (await session.execute(select(PasswordResetToken))).scalar_one()
        return None if row.requested_ip is None else str(row.requested_ip)

    async def test_a_reset_records_the_address_the_gateway_vouches_for(
        self, client: AsyncClient, session, registered
    ) -> None:
        response = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "sam@example.com"},
            headers=via_gateway(VISITOR),
        )
        assert response.status_code == 202, response.text
        assert await self._reset_ip(session) == VISITOR

    async def test_a_reset_ignores_an_address_nobody_vouches_for(
        self, client: AsyncClient, session, registered
    ) -> None:
        """A direct caller naming its own address gets the TCP peer recorded instead."""
        response = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "sam@example.com"},
            headers={"X-Client-Ip": VISITOR},
        )
        assert response.status_code == 202, response.text
        assert await self._reset_ip(session) == "127.0.0.1"

    async def test_a_reset_ignores_an_address_swapped_under_a_captured_hop(
        self, client: AsyncClient, session, registered
    ) -> None:
        response = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "sam@example.com"},
            headers=via_gateway(VISITOR, bound="203.0.113.1"),
        )
        assert response.status_code == 202, response.text
        assert await self._reset_ip(session) == "127.0.0.1"

    async def test_a_sign_in_records_the_visitors_address_on_the_session_and_audit_row(
        self, client: AsyncClient, session, registered
    ) -> None:
        from sqlalchemy import select

        from app.models import AuditLog, Session
        from tests.conftest import PASSWORD

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "sam@example.com", "password": PASSWORD},
            headers=via_gateway(VISITOR),
        )
        assert response.status_code == 200, response.text

        (signed_in,) = (await session.execute(select(Session))).scalars().all()
        assert str(signed_in.ip) == VISITOR
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "auth.password_verified")
            )
        ).scalar_one()
        assert str(audit.ip) == VISITOR
