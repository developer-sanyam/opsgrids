"""The admin-gateway hop signature, checked from auth's side (BLOCKER 2, and M1/m2/m3 of the
third-round security review).

`platform-core` proves the signature primitives and that only a signing gateway writes one; this
file proves what auth's operator routes do with it once `ADMIN_GATEWAY_HOP_KEY` is configured:

* no header, a wrong-method or wrong-path signature, a different key — 401 (m3);
* a header whose body or query differs from what was signed — 401, and a verbatim replay of a
  header already accepted — 401, so a capture is good once (m2);
* a Redis outage answers 503 instead of letting an unchecked replay through (m2, fail closed);
* production/staging/uat refuse to start without the key, and a key equal to
  `internal_service_token` is refused everywhere (M1(a)).

`POST /auth/operator/logout` is the probe route: it is idempotent, needs no database rows, and
answers 204 when the dependency lets it through — so the only thing a 401 can mean is the hop.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import SecretStr

import app.deps as deps_module
from app.config import Settings
from app.deps import require_admin_hop
from app.main import app
from platform_core import cache
from platform_core.internal_auth import canonical_query, sign_admin_hop

pytestmark = pytest.mark.integration

HOP_KEY = "test-admin-gateway-hop-key-of-at-least-32-bytes"
LOGOUT = "/api/v1/auth/operator/logout"
HEADER = "x-admin-gateway-hop"


@pytest.fixture
def hop_configured(monkeypatch: pytest.MonkeyPatch, cfg: Settings) -> None:
    keyed = cfg.model_copy(update={"admin_gateway_hop_key": SecretStr(HOP_KEY)})
    monkeypatch.setattr(deps_module, "settings", lambda: keyed)


def _signed(
    method: str = "POST", path: str = LOGOUT, *, query: str = "", body: bytes = b""
) -> dict[str, str]:
    # `X-Requested-With` alongside the hop signature: MINOR 1 of the 2026-09-24 SSO review put
    # the same CSRF header every tenant cookie-authenticated auth route requires onto the
    # operator refresh/logout routes too, so a real signed request — what this helper stands in
    # for — carries both.
    return {
        HEADER: sign_admin_hop(method, path, key=HOP_KEY, query=query, body=body),
        "X-Requested-With": "opsgrids",
    }


class TestWithTheKeyConfigured:
    async def test_a_missing_header_is_refused(self, client, hop_configured) -> None:
        response = await client.post(LOGOUT)
        assert response.status_code == 401

    async def test_a_gateway_signed_request_passes(self, client, hop_configured) -> None:
        response = await client.post(LOGOUT, headers=_signed())
        assert response.status_code == 204, response.text

    async def test_a_signature_for_a_different_method_is_refused(
        self, client, hop_configured
    ) -> None:
        response = await client.post(LOGOUT, headers=_signed(method="GET"))
        assert response.status_code == 401

    async def test_a_signature_for_a_different_path_is_refused(
        self, client, hop_configured
    ) -> None:
        response = await client.post(LOGOUT, headers=_signed(path="/api/v1/auth/operator/login"))
        assert response.status_code == 401

    async def test_a_signature_under_a_different_key_is_refused(
        self, client, hop_configured
    ) -> None:
        forged = {
            HEADER: sign_admin_hop("POST", LOGOUT, key="an-attackers-own-key-that-is-32-bytes!")
        }
        response = await client.post(LOGOUT, headers=forged)
        assert response.status_code == 401

    async def test_a_replayed_body_is_refused(self, client, hop_configured) -> None:
        """m2: signed for one body, presented with another."""
        headers = _signed(body=b'{"pre_auth_token": "a"}')
        response = await client.post(LOGOUT, headers=headers, content=b'{"pre_auth_token": "b"}')
        assert response.status_code == 401

    async def test_the_body_that_was_signed_passes(self, client, hop_configured) -> None:
        body = b'{"pre_auth_token": "a"}'
        response = await client.post(LOGOUT, headers=_signed(body=body), content=body)
        assert response.status_code == 204, response.text

    async def test_a_replayed_query_is_refused(self, client, hop_configured) -> None:
        """m2: signed for one query string, presented with another."""
        headers = _signed(query=canonical_query([("x", "1")]))
        response = await client.post(f"{LOGOUT}?x=2", headers=headers)
        assert response.status_code == 401

    async def test_the_query_that_was_signed_passes_in_any_order(
        self, client, hop_configured
    ) -> None:
        headers = _signed(query=canonical_query([("a", "1"), ("b", "2")]))
        response = await client.post(f"{LOGOUT}?b=2&a=1", headers=headers)
        assert response.status_code == 204, response.text

    async def test_a_nonce_cannot_be_used_twice(self, client, hop_configured) -> None:
        """m2: a byte-for-byte replay of a header auth already accepted."""
        headers = _signed()
        first = await client.post(LOGOUT, headers=headers)
        second = await client.post(LOGOUT, headers=headers)
        assert first.status_code == 204, first.text
        assert second.status_code == 401

    async def test_a_redis_outage_fails_closed(self, client, hop_configured) -> None:
        """m2: with the nonce store unreachable an unchecked replay must not be waved through
        — 503, not 204."""

        class _Down:
            async def set(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError("redis is down")

        cache._redis = _Down()  # type: ignore[assignment]
        response = await client.post(LOGOUT, headers=_signed())
        assert response.status_code == 503


class TestWithoutTheKey:
    async def test_the_defence_is_off_and_sign_in_still_works_in_development(self, client) -> None:
        """No key configured (only permitted outside production/staging/uat, below) — the
        dependency is a no-op, exactly as before, so a dev box that never provisioned one is
        not locked out. The CSRF header is unaffected by the hop key either way, and still
        required."""
        response = await client.post(LOGOUT, headers={"X-Requested-With": "opsgrids"})
        assert response.status_code == 204


class TestSettingsRefuseTheUnsafeShapes:
    """M1(a): the no-op above is only acceptable where nothing real depends on it."""

    def _settings(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "internal_service_token": SecretStr("x" * 40 + "-internal-token-value"),
            "pii_encryption_key": SecretStr("y" * 40 + "-pii-encryption-key"),
        }
        return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_a_real_deployment_without_the_key_refuses_to_start(self, environment: str) -> None:
        with pytest.raises(ValueError, match="admin_gateway_hop_key must be set"):
            self._settings(environment=environment, debug=False)

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_an_empty_key_from_compose_refuses_to_start_like_a_missing_one(
        self, environment: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`compose.prod.yml` passes `${ADMIN_GATEWAY_HOP_KEY:-}` — an empty string on a box
        without the key. That must read as *unset* (so auth refuses for the right reason), not
        as a zero-length key."""
        monkeypatch.setenv("ADMIN_GATEWAY_HOP_KEY", "")
        with pytest.raises(ValueError, match="admin_gateway_hop_key must be set"):
            self._settings(environment=environment, debug=False)

    def test_an_empty_key_in_development_is_unset_not_a_key_nothing_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADMIN_GATEWAY_HOP_KEY", "")
        assert self._settings(environment="local").admin_gateway_hop_key is None

    @pytest.mark.parametrize("environment", ["local", "test", "ci"])
    def test_development_without_the_key_starts(self, environment: str) -> None:
        assert self._settings(environment=environment).admin_gateway_hop_key is None

    def test_a_real_deployment_with_a_proper_key_starts(self) -> None:
        cfg = self._settings(
            environment="production",
            debug=False,
            admin_gateway_hop_key=SecretStr("z" * 40 + "-hop-key-value"),
        )
        assert cfg.admin_gateway_hop_key is not None

    @pytest.mark.parametrize("environment", ["local", "production"])
    def test_a_hop_key_equal_to_the_internal_token_is_refused_everywhere(
        self, environment: str
    ) -> None:
        shared = "s" * 40 + "-shared-secret-value"
        with pytest.raises(ValueError, match="must not equal internal_service_token"):
            self._settings(
                environment=environment,
                debug=False,
                internal_service_token=SecretStr(shared),
                admin_gateway_hop_key=SecretStr(shared),
            )


class TestEveryOperatorRouteIsGuarded:
    """Item 6 of the final review: the guard is attached route by route, so a new operator route
    added without it would be reachable by anything that gets past the route table. Introspect
    the real app rather than trusting a list."""

    def _operator_routes(self) -> list[tuple[str, set[str], APIRoute]]:
        """(client path, methods, route) — walked through FastAPI's own route-context iterator,
        because included routers are wrappers carrying a prefix and are not flattened into
        `app.routes` (a plain `app.routes` scan silently finds nothing; `test_tenancy.py`'s
        route audit walks it the same way)."""
        found: list[tuple[str, set[str], APIRoute]] = []
        for context in iter_route_contexts(app.routes):
            route = context.original_route
            if not isinstance(route, APIRoute):
                continue
            path = context.path or route.path
            if path.startswith("/api/v1/auth/operator"):
                found.append((path, set(context.methods or route.methods), route))
        return found

    def test_the_scan_finds_the_operator_surface(self) -> None:
        paths = {path for path, _methods, _route in self._operator_routes()}
        assert {
            "/api/v1/auth/operator/login",
            "/api/v1/auth/operator/mfa/verify",
            "/api/v1/auth/operator/mfa/enrol",
            "/api/v1/auth/operator/mfa/enrol/confirm",
            "/api/v1/auth/operator/refresh",
            "/api/v1/auth/operator/logout",
            "/api/v1/auth/operator/me",
            "/api/v1/auth/operator/enrollment-codes",
            "/api/v1/auth/operator/staff/{staff_id}/mfa:reset",
        } <= paths, "guards the guard: an empty scan would pass every assertion below"

    def test_every_operator_route_depends_on_require_admin_hop(self) -> None:
        unguarded = [
            f"{sorted(methods)} {path}"
            for path, methods, route in self._operator_routes()
            if not any(dep.call is require_admin_hop for dep in route.dependant.dependencies)
        ]
        assert unguarded == []
