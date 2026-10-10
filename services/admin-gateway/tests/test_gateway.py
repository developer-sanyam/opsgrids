"""What the operator gateway lets through, and what it refuses.

This surface can act across customers, so the tests that matter most are the ones proving
it *cannot* be reached from the product surface. Two properties carry that weight:

1. A product token is not an operator token — different audience, refused outright.
2. A non-staff caller gets **404**, not 403, on staff routes. A 403 confirms the route
   exists; 404 tells a prober nothing.

Everything the two gateways share is tested once, in `app-gateway`. What is repeated here
is only what differs.
"""

from __future__ import annotations

from functools import partial

import pytest
from httpx import AsyncClient

from platform_core.gateway import PermissionResolver
from tests.conftest import ORG, USER, EchoUpstream, FakeRedis

pytestmark = pytest.mark.integration

STAFF_ROUTE = "/api/v1/admin/organizations"
# Not `staff_only` in routes.json, and not a public path — an authenticated, unflagged route.
NON_STAFF_ROUTE = "/api/v1/auth/me"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestSurfaceSeparation:
    async def test_a_product_token_cannot_reach_the_operator_surface(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """The whole reason there are two gateways rather than one.

        A token stolen from a customer's browser must be inert here even though the
        network path between the two exists.
        """
        response = await client.get(STAFF_ROUTE, headers=auth(mint(audience="app", staff=True)))
        assert response.status_code == 401
        assert not upstream.calls

    async def test_an_operator_token_is_accepted(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        response = await client.get(STAFF_ROUTE, headers=auth(mint(staff=True)))
        assert response.status_code == 200
        assert upstream.last["path"] == STAFF_ROUTE
        assert upstream.header("x-audience") == "operator"


class TestStaffOnly:
    async def test_a_non_staff_caller_gets_404_not_403(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """404 is deliberate.

        403 would confirm that `/api/v1/admin/organizations` is a real endpoint. Somebody
        holding a valid operator-audience token but no staff flag learns nothing here.
        """
        response = await client.get(STAFF_ROUTE, headers=auth(mint(staff=False)))
        assert response.status_code == 404
        assert not upstream.calls

    async def test_the_refusal_is_indistinguishable_from_an_unknown_path(
        self, client: AsyncClient, mint
    ) -> None:
        real = await client.get(STAFF_ROUTE, headers=auth(mint(staff=False)))
        fake = await client.get("/api/v1/admin/nonexistent", headers=auth(mint(staff=False)))

        assert real.status_code == fake.status_code == 404
        assert real.json()["type"] == fake.json()["type"]
        # The detail must not differ either — a probe that diffs two bodies would
        # otherwise learn exactly which paths exist.
        assert real.json()["title"] == fake.json()["title"]

    async def test_a_route_without_the_flag_does_not_require_staff(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """`staff_only` is per route, not gateway-wide.

        This is the half that a gateway-wide flag would have broken: an operator-audience
        caller who is not staff must still reach the routes that are not marked, or signing
        in through this gateway would require already being signed in.
        """
        response = await client.get(NON_STAFF_ROUTE, headers=auth(mint(staff=False)))
        assert response.status_code == 200


class TestCatalogAuthoringSurface:
    """The commerce catalog's operator surface, `/api/v1/admin/catalog`, is staff-gated here.

    The generic `TestStaffOnly` cases already prove the mechanism on another route; this pins it
    to the catalog prefix specifically, so a later edit that dropped its `staff_only` flag would
    fail a test that names it.
    """

    CATALOG_ROUTE = "/api/v1/admin/catalog/plans"

    async def test_a_staff_token_reaches_catalog_as_operator(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        response = await client.post(self.CATALOG_ROUTE, headers=auth(mint(staff=True)), json={})
        assert response.status_code == 200
        assert upstream.last["path"] == self.CATALOG_ROUTE
        assert upstream.header("x-audience") == "operator"

    async def test_a_non_staff_operator_token_gets_404(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        response = await client.post(self.CATALOG_ROUTE, headers=auth(mint(staff=False)), json={})
        assert response.status_code == 404
        assert not upstream.calls

    async def test_a_product_audience_token_is_refused(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            self.CATALOG_ROUTE, headers=auth(mint(audience="app", staff=True)), json={}
        )
        assert response.status_code == 401
        assert not upstream.calls


class TestEntitlement:
    async def test_operator_routes_need_no_product_entitlement(
        self, client: AsyncClient, mint
    ) -> None:
        """Staff act on organizations that have bought nothing at all.

        Gating this surface on entitlements would make an unsubscribed org unmanageable —
        exactly when support most needs to reach it.
        """
        response = await client.get(STAFF_ROUTE, headers=auth(mint(staff=True, entitlements=[])))
        assert response.status_code == 200


class TestPublicPaths:
    async def test_sign_in_is_reachable_without_a_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"email": "staff@opsgrids.tech", "password": "x"}
        )
        assert response.status_code == 200

    async def test_the_public_surface_is_only_authentication_and_signed_webhooks(self) -> None:
        """Public here is authentication — plus provider webhooks, which authenticate by signature.

        A provider callback carries no platform token, so it cannot go through the bearer path; it
        is authenticated instead by the HMAC signature payment-service verifies (ADR-0004 §6). That
        is the one deliberate non-auth exception, and it is a partner *webhook*, never a
        cross-customer read — so it is enumerated here rather than left open-ended."""
        from app.main import policy

        allowed_prefixes = ("/api/v1/auth", "/health", "/api/v1/webhooks/")
        non_auth = {p for p in policy.public_paths if not p.startswith(allowed_prefixes)}
        assert non_auth == set(), f"unexpected public operator paths: {non_auth}"

    async def test_no_staff_route_is_public(self) -> None:
        from app.main import policy, routes

        for route in routes:
            if route.staff_only:
                assert route.prefix not in policy.public_paths


class TestIdentityForwarding:
    async def test_the_staff_flag_reaches_the_upstream(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """`admin-service` still makes its own decisions; the gateway only vouches for
        who is asking."""
        await client.get(STAFF_ROUTE, headers=auth(mint(staff=True)))
        assert upstream.header("x-user-id") == USER
        assert upstream.header("x-org-id") == ORG
        assert upstream.header("x-internal-token")

    async def test_client_supplied_staff_claims_are_stripped(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """A header saying "I am staff" must never survive. Only the token decides."""
        response = await client.get(
            STAFF_ROUTE,
            headers={
                **auth(mint(staff=False)),
                "X-Staff": "true",
                "X-User-Id": "00000000-0000-0000-0000-000000000001",
            },
        )
        assert response.status_code == 404
        assert not upstream.calls


class TestRateLimiting:
    async def test_the_operator_limit_is_tighter_than_the_product_limit(self) -> None:
        """A spike on this surface is a signal, not traffic."""
        from app.config import settings

        assert settings().rate_limit_per_minute <= 120

    async def test_the_limiter_fails_closed(
        self, client: AsyncClient, mint, redis: FakeRedis
    ) -> None:
        redis.fail = True
        response = await client.get(STAFF_ROUTE, headers=auth(mint(staff=True)))
        assert response.status_code == 503

    async def test_two_visitors_behind_the_same_proxy_get_two_different_buckets(
        self, client: AsyncClient, redis: FakeRedis, upstream: EchoUpstream
    ) -> None:
        """Without this, every anonymous caller behind Caddy shared one 30/min bucket, so
        anyone could lock all staff out of operator sign-in by sending 31 requests."""
        for addr in ("198.51.100.1", "198.51.100.2"):
            await client.post(
                "/api/v1/auth/operator/login",
                json={"email": "a@example.com", "password": "x"},
                headers={"X-Forwarded-For": addr},
            )
        buckets = {k for k in redis.store if k.startswith("rl:anon:")}
        assert any("198.51.100.1" in b for b in buckets)
        assert any("198.51.100.2" in b for b in buckets)


class TestPermissionResolution:
    async def test_resolution_fails_closed(
        self,
        client: AsyncClient,
        mint,
        upstream: EchoUpstream,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even for staff. Especially for staff — this surface crosses customers."""
        import httpx

        from app.main import resolver

        async def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("account-service is down")

        monkeypatch.setattr(
            resolver,
            "_client",
            httpx.AsyncClient(
                base_url="http://account", transport=httpx.MockTransport(unreachable)
            ),
        )
        monkeypatch.setattr(resolver, "resolve", partial(PermissionResolver.resolve, resolver))

        response = await client.get(STAFF_ROUTE, headers=auth(mint(staff=True)))
        assert response.status_code == 503
        assert not upstream.calls


class TestNoServiceToServiceRoutes:
    """The internal API must not be publishable through a gateway.

    It reads across every tenant and carries **no per-user authorization** — the shared
    service token is the only guard. Routing it here would let any operator-audience
    caller, staff or not, read every user and organization on the platform.

    An earlier route table did exactly that. It was inert only because the prefixes did not
    match what account-service mounts, which is luck rather than design, so this asserts the
    property instead of trusting the typo.
    """

    def test_no_route_reaches_the_internal_api(self) -> None:
        from app.main import routes

        offenders = [r.prefix for r in routes if "internal" in r.prefix.lower()]
        assert offenders == [], (
            f"the operator gateway must not route to the internal API: {offenders}. "
            "Service-to-service calls go direct to the service port."
        )

    async def test_an_internal_path_is_not_routable(self, client: AsyncClient, mint) -> None:
        """Belt and braces: the path a client would guess resolves to nothing."""
        for path in (
            "/api/v1/internal/accounts/users",
            "/api/v1/internal/v1/users/by-email",
        ):
            response = await client.get(path, headers=auth(mint(staff=True)))
            assert response.status_code == 404, path


class TestOperatorMfaFlowIsReachable:
    """BLOCKER 2 of the security review: the three operator MFA steps are pre-auth (there is
    no bearer token yet to check), and were missing from `public_paths` — every one of them
    was refused with 401 before ever reaching auth-service, so an operator's sign-in could
    never actually finish through the real gateway. Public here is exactly as narrow as the
    module docstring's rule: authentication only, nothing that reads or writes anything else.
    """

    async def test_enrolment_start_is_reachable_without_a_bearer_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/operator/mfa/enrol",
            json={"pre_auth_token": "x", "enrollment_code": "y"},
        )
        assert response.status_code == 200, response.text
        assert upstream.last["path"] == "/api/v1/auth/operator/mfa/enrol"

    async def test_enrolment_confirm_is_reachable_without_a_bearer_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/operator/mfa/enrol/confirm",
            json={"pre_auth_token": "x", "factor_id": str(USER), "code": "123456"},
        )
        assert response.status_code == 200, response.text
        assert upstream.last["path"] == "/api/v1/auth/operator/mfa/enrol/confirm"

    async def test_mfa_verify_is_reachable_without_a_bearer_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/operator/mfa/verify",
            json={"pre_auth_token": "x", "code": "123456"},
        )
        assert response.status_code == 200, response.text
        assert upstream.last["path"] == "/api/v1/auth/operator/mfa/verify"

    async def test_the_full_shape_login_then_mfa_then_me_is_all_reachable(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """One request per leg of the flow the review asked to see proven end to end at the
        gateway: sign-in, both MFA steps, and the authenticated `/me` that follows — none of
        them refused before ever reaching auth-service."""
        login = await client.post(
            "/api/v1/auth/operator/login",
            json={"email": "staff@opsgrids.tech", "password": "x"},
        )
        assert login.status_code == 200

        enrol = await client.post(
            "/api/v1/auth/operator/mfa/enrol",
            json={"pre_auth_token": "x", "enrollment_code": "y"},
        )
        assert enrol.status_code == 200

        confirm = await client.post(
            "/api/v1/auth/operator/mfa/enrol/confirm",
            json={"pre_auth_token": "x", "factor_id": str(USER), "code": "123456"},
        )
        assert confirm.status_code == 200

        verify = await client.post(
            "/api/v1/auth/operator/mfa/verify",
            json={"pre_auth_token": "x", "code": "123456"},
        )
        assert verify.status_code == 200

        # `/me` is on the same `/api/v1/auth` prefix and is not gateway-`staff_only` — the
        # in-service `require_operator` is what gates it — so a completed operator token
        # reaches it like any other authenticated call.
        me = await client.get("/api/v1/auth/operator/me", headers=auth(mint(staff=True)))
        assert me.status_code == 200
        assert upstream.header("x-user-id") == USER


class TestNoDatabase:
    def test_the_gateway_opens_no_database_connection(self) -> None:
        from pathlib import Path

        service_dir = Path(__file__).resolve().parent.parent
        assert not (service_dir / "migrations").exists()
        assert not (service_dir / "alembic.ini").exists()
        assert not (service_dir / "app" / "models.py").exists()
