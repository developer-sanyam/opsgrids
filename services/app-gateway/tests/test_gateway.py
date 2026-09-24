"""What the gateway lets through, and what it refuses.

Most of these are negative. The gateway is the one component where a mistake is reachable
from the public internet, so "this request is rejected" is worth more here than anywhere
else in the platform.

The header-spoofing test is the single most important one: without stripping inbound
`X-*` identity headers, a caller could simply *claim* to be somebody else and the internal
token the gateway adds would vouch for it.
"""

from __future__ import annotations

from functools import partial

import pytest
from httpx import AsyncClient

from platform_core.cache import permission_version_key
from platform_core.gateway import PermissionResolver, Route, RouteTable
from tests.conftest import ORG, USER, EchoUpstream, FakeRedis

pytestmark = pytest.mark.integration

HRMS = "/api/v1/employees"
CRM = "/api/v1/customers"
CORE = "/api/v1/organization"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestRouting:
    def test_longest_prefix_wins(self) -> None:
        """A nested route may belong to a different service than its parent."""
        table = RouteTable(
            [
                Route("/api/v1/employees", "http://core-hr"),
                Route("/api/v1/employees/salary", "http://payroll"),
            ]
        )
        assert table.resolve("/api/v1/employees/123").upstream == "http://core-hr"  # type: ignore[union-attr]
        assert table.resolve("/api/v1/employees/salary/1").upstream == "http://payroll"  # type: ignore[union-attr]

    def test_the_route_table_is_data(self) -> None:
        """Loaded from a file, so a product launch never edits the core gateway."""
        from app.main import routes

        assert len(routes) > 20
        assert {"hrms", "crm", "pulseboard"} <= routes.products

    async def test_an_unknown_path_is_refused_before_authentication(
        self, client: AsyncClient
    ) -> None:
        # No token supplied at all — a request to nowhere is not worth authenticating.
        response = await client.get("/api/v1/nonsense")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")


class TestAuthentication:
    async def test_a_request_without_a_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(HRMS)
        assert response.status_code == 401

    async def test_an_expired_token_is_refused(self, client: AsyncClient, mint) -> None:
        response = await client.get(HRMS, headers=auth(mint(expired=True)))
        assert response.status_code == 401

    async def test_a_garbage_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(HRMS, headers=auth("not.a.token"))
        assert response.status_code == 401

    async def test_a_refresh_token_cannot_be_used_as_an_access_token(
        self, client: AsyncClient, mint
    ) -> None:
        """Token types are not interchangeable — a long-lived credential must not open
        the whole API."""
        response = await client.get(HRMS, headers=auth(mint(typ="refresh")))
        assert response.status_code == 401

    async def test_a_token_for_another_audience_is_refused(self, client: AsyncClient, mint) -> None:
        """An operator-console token must not work on the product gateway.

        This is what stops a stolen token from one surface reaching the other, even when
        the network path exists.
        """
        response = await client.get(HRMS, headers=auth(mint(audience="operator")))
        assert response.status_code == 401

    async def test_a_token_with_no_organization_is_refused(self, client: AsyncClient, mint) -> None:
        """There is no default organization. Guessing one is how tenants read each
        other's data."""
        response = await client.get(HRMS, headers=auth(mint(org=None)))
        assert response.status_code == 401

    async def test_a_valid_token_reaches_the_upstream(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        response = await client.get(HRMS, headers=auth(mint()))
        assert response.status_code == 200
        assert upstream.last["path"] == HRMS


class TestHeaderSpoofing:
    async def test_client_supplied_identity_headers_are_stripped(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """The most important test in this file.

        Without stripping, a caller sets `X-User-Id` to somebody else and the internal
        token the gateway adds makes the downstream service believe it.
        """
        response = await client.get(
            HRMS,
            headers={
                **auth(mint()),
                "X-User-Id": "00000000-0000-0000-0000-000000000001",
                "X-Org-Id": "00000000-0000-0000-0000-000000000002",
                "X-Perms": "hrms.*:ORG",
                "X-Entitlements": "hrms,crm,pulseboard",
                "X-Internal-Token": "guessed",
            },
        )
        assert response.status_code == 200

        assert upstream.header("x-user-id") == USER
        assert upstream.header("x-org-id") == ORG
        assert upstream.header("x-entitlements") == "hrms"
        assert upstream.header("x-internal-token") != "guessed"

    async def test_identity_is_forwarded_from_the_token(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        await client.get(HRMS, headers=auth(mint()))
        assert upstream.header("x-org-id") == ORG
        assert upstream.header("x-audience") == "app"
        assert upstream.header("x-membership-id")
        assert upstream.header("x-session-id")
        assert upstream.header("x-internal-token")

    async def test_the_hop_token_is_signed_expiring_and_bound_to_the_identity(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """The raw signing secret must never travel — it used to go out on every request,
        and one header capture forged any identity against any service forever. What goes
        out now is a short-lived token naming exactly the user and organization the other
        headers claim, so a capture cannot vouch for anybody else."""
        from app.config import settings
        from platform_core.internal_auth import verify_gateway_token

        cfg = settings()
        await client.get(HRMS, headers=auth(mint()))
        hop = upstream.header("x-internal-token")
        assert hop != cfg.internal_service_token.get_secret_value()

        claims = verify_gateway_token(cfg, hop)
        assert claims["uid"] == USER
        assert claims["org"] == ORG
        assert claims["iss"] == "gateway"
        assert claims["exp"] - claims["iat"] <= 300, "a hop credential must be short-lived"

    async def test_the_correlation_id_is_propagated(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """One request crossing five services has to be one searchable thing."""
        await client.get(HRMS, headers={**auth(mint()), "X-Correlation-ID": "trace-me"})
        assert upstream.header("x-correlation-id") == "trace-me"

    async def test_hop_by_hop_headers_are_not_forwarded(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        # Not `Connection`: httpx sets its own when it builds the upstream request, so
        # asserting on that would test the HTTP client rather than the gateway.
        await client.post(
            HRMS,
            json={"a": 1},
            headers={
                **auth(mint()),
                "Proxy-Authorization": "Basic Zm9vOmJhcg==",
                "TE": "trailers",
            },
        )
        assert "proxy-authorization" not in upstream.last["headers"]
        assert "te" not in upstream.last["headers"]


class TestEntitlement:
    async def test_a_product_the_organization_has_not_activated_is_refused(
        self, client: AsyncClient, mint
    ) -> None:
        """402, not 403.

        The caller's permissions are fine; the subscription is not. The distinction is
        what lets the client offer an upgrade instead of showing an error.
        """
        response = await client.get(CRM, headers=auth(mint(entitlements=["hrms"])))
        assert response.status_code == 402
        body = response.json()
        assert body["type"].endswith("/product-not-entitled")
        assert "crm" in body["detail"]

    async def test_an_activated_product_is_allowed(self, client: AsyncClient, mint) -> None:
        response = await client.get(CRM, headers=auth(mint(entitlements=["hrms", "crm"])))
        assert response.status_code == 200

    async def test_platform_routes_need_no_entitlement(self, client: AsyncClient, mint) -> None:
        """Every customer has an organization and a profile, whatever they bought."""
        response = await client.get(CORE, headers=auth(mint(entitlements=[])))
        assert response.status_code == 200


class TestPermissionVersion:
    async def test_a_token_predating_a_grant_change_is_refused(
        self, client: AsyncClient, mint, redis: FakeRedis
    ) -> None:
        """A revoked grant must not survive until the token expires.

        401 with a distinct type, so the client refreshes rather than showing a login
        screen — the credential is fine, the snapshot inside it is not.
        """
        redis.store[permission_version_key(ORG)] = "5"

        response = await client.get(HRMS, headers=auth(mint(permission_version=4)))
        assert response.status_code == 401
        assert response.json()["type"].endswith("/token-stale")

    async def test_a_current_token_passes(
        self, client: AsyncClient, mint, redis: FakeRedis
    ) -> None:
        redis.store[permission_version_key(ORG)] = "5"
        response = await client.get(HRMS, headers=auth(mint(permission_version=5)))
        assert response.status_code == 200

    async def test_an_unset_version_does_not_invalidate_everything(
        self, client: AsyncClient, mint
    ) -> None:
        """A rebuilt Redis must not sign every customer out.

        `None` means "nothing recorded", which is not the same as zero.
        """
        response = await client.get(HRMS, headers=auth(mint(permission_version=0)))
        assert response.status_code == 200


class TestRateLimiting:
    async def test_a_caller_over_the_limit_is_refused(
        self, client: AsyncClient, mint, redis: FakeRedis
    ) -> None:
        from app.config import settings

        token = mint()
        # Pre-spend the window rather than issuing hundreds of requests.
        for key in list(redis.store):
            redis.store.pop(key)
        response = await client.get(HRMS, headers=auth(token))
        assert response.status_code == 200

        bucket = next(k for k in redis.store if k.startswith("rl:user:"))
        redis.store[bucket] = str(settings().rate_limit_per_minute + 5)

        response = await client.get(HRMS, headers=auth(token))
        assert response.status_code == 429
        assert response.headers.get("retry-after")

    async def test_the_limiter_fails_closed(
        self, client: AsyncClient, mint, redis: FakeRedis
    ) -> None:
        """An outage must not become an amplification vector.

        Treating "cannot evaluate the limit" as "allowed" is how a Redis blip turns into
        an unbounded request flood against every downstream service.
        """
        redis.fail = True
        response = await client.get(HRMS, headers=auth(mint()))
        assert response.status_code == 503

    async def test_public_paths_are_limited_by_address(
        self, client: AsyncClient, redis: FakeRedis, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"email": "a@example.com", "password": "x"}
        )
        assert response.status_code == 200
        assert any(k.startswith("rl:anon:") for k in redis.store)


class TestPublicPaths:
    async def test_sign_in_is_reachable_without_a_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"email": "a@example.com", "password": "x"}
        )
        assert response.status_code == 200
        assert upstream.last["path"] == "/api/v1/auth/login"

    async def test_single_sign_on_is_reachable_without_a_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """Both halves, for the same reason `login` is public: they produce a token.

        The callback especially — an identity provider redirects a browser to it carrying
        nothing of ours but the `state` auth-service issued, so demanding a bearer token
        there would make the flow unfinishable.
        """
        started = await client.get("/api/v1/auth/sso/start", params={"email": "a@example.com"})
        assert started.status_code == 200
        assert upstream.last["path"] == "/api/v1/auth/sso/start"

        finished = await client.get(
            "/api/v1/auth/sso/callback", params={"code": "abc", "state": "xyz"}
        )
        assert finished.status_code == 200
        assert upstream.last["path"] == "/api/v1/auth/sso/callback"

    async def test_the_sso_configuration_routes_are_not_public(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """Deciding which external issuer may vouch for your colleagues is an ordinary
        permission-guarded tenant operation, and it sits under the same path prefix as two
        routes that are public — so it is worth pinning that the prefix does not leak."""
        assert (await client.get("/api/v1/auth/sso/config")).status_code == 401


class TestOperatorSignInIsBlockedHere:
    """MAJOR 3 of the security review: operator sign-in has no principal to check (staff
    credentials travel in the body), so it must not be reachable from the *public* product
    gateway at all — not even with an ordinary tenant's own valid bearer token. It belongs to
    admin-gateway alone (ADR-0003 §2)."""

    async def test_operator_login_is_refused_with_404_and_no_token_required_to_learn_that(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/auth/operator/login", json={"email": "staff@opsgrids.tech", "password": "x"}
        )
        assert response.status_code == 404

    async def test_a_valid_tenant_token_does_not_unlock_it_either(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """The headline finding: before this fix, an ordinary signed-in customer's own token
        was enough to reach every operator MFA endpoint through the public gateway."""
        for path, method, body in (
            ("/api/v1/auth/operator/login", "POST", {"email": "x", "password": "y"}),
            (
                "/api/v1/auth/operator/mfa/enrol",
                "POST",
                {"pre_auth_token": "x", "enrollment_code": "y"},
            ),
            ("/api/v1/auth/operator/mfa/verify", "POST", {"pre_auth_token": "x", "code": "123456"}),
            ("/api/v1/auth/operator/me", "GET", None),
        ):
            headers = {"Authorization": f"Bearer {mint()}"}
            response = await client.request(method, path, json=body, headers=headers)
            assert response.status_code == 404, path
        assert upstream.calls == [], "a blocked route must never reach the upstream"

    async def test_ordinary_auth_paths_under_the_same_prefix_are_unaffected(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """The block is scoped to `/api/v1/auth/operator`, not the whole `/api/v1/auth`
        prefix it sits under — sign-in for everybody else keeps working."""
        response = await client.post(
            "/api/v1/auth/login", json={"email": "a@example.com", "password": "x"}
        )
        assert response.status_code == 200
        assert upstream.last["path"] == "/api/v1/auth/login"

    async def test_the_organization_slug_is_taken_from_the_host(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """Only the gateway parses Host. A service trusting it would be trusting the
        client."""
        await client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "x"},
            headers={"Host": "acme.opsgrids.com"},
        )
        assert upstream.header("x-org-slug") == "acme"

    async def test_a_reserved_subdomain_is_not_treated_as_an_organization(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        await client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "x"},
            headers={"Host": "app.opsgrids.com"},
        )
        assert upstream.header("x-org-slug") is None


class TestTheStorefrontBuyerSurface:
    """The platform's first anonymous read of *tenant* data (ADR-0010).

    Every other public path exists to produce a token. This one is a stranger asking a merchant's
    store whether it delivers to their postal code, so the gateway is the component that decides
    whether the buyer surface works at all — and a green unit suite inside storefront-service
    proves nothing about that.
    """

    SERVICEABILITY = "/api/v1/storefront/public/serviceability"
    #: The bootstrap a buyer's browser calls first: hostname in, store slug out (ADR-0012 §2).
    #: It is on the same terms as the check above, which is the point — one mechanism, not two.
    STORE_CONTEXT = "/api/v1/storefront/public/store-context"

    async def test_the_store_context_bootstrap_is_reachable_with_no_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """**A stranger on a domain the platform does not own reaches this and nothing else.**
        Nothing on a custom domain is authenticated and that is enforced by physics — ADR-0001
        scopes the refresh cookie to `.opsgrids.com`, so it is simply never sent to
        `merchant.com` — which makes this list the whole of that host's reachable API."""
        response = await client.post(self.STORE_CONTEXT, json={"host": "merchant.com"})

        assert response.status_code == 200
        assert upstream.last["path"] == self.STORE_CONTEXT

    async def test_the_bootstrap_forwards_no_identity_either(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """It resolves its own tenant from the host, on a bypass reader, and binds it itself —
        so there must be nothing here for it to be tempted to trust."""
        await client.post(self.STORE_CONTEXT, json={"host": "merchant.com"})

        assert upstream.header("x-org-id") is None
        assert upstream.header("x-perms") is None
        assert upstream.header("x-internal-token") is None

    async def test_the_bootstrap_is_limited_by_address_too(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """Two anonymous calls per session rather than one (ADR-0012 §2). Fleet-wide that is
        nothing; **the number to watch is the per-IP limit under NAT** — an office or a mall's
        wifi is a single address — which is why the response is cached in `sessionStorage` for
        the tab rather than re-fetched per page view."""
        await client.post(self.STORE_CONTEXT, json={"host": "merchant.com"})
        assert any(k.startswith("rl:anon:") for k in redis.store)

    async def test_a_redis_outage_on_the_bootstrap_is_503_and_never_an_empty_store(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """**Unknown is not no**, applied to a whole shop. `enforce_rate_limit` fails closed, so
        a Redis outage takes the buyer surface down — down is a 503 the client renders as
        unavailable. What it must never be is a well-formed answer saying this address has no
        store, which is what a catch-and-answer anywhere on this path would produce."""
        redis.fail = True

        response = await client.post(self.STORE_CONTEXT, json={"host": "merchant.com"})

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "store" not in response.json()

    async def test_the_serviceability_check_is_reachable_with_no_token(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        response = await client.post(
            self.SERVICEABILITY, json={"store": "greenleaf", "postal_code": "400050"}
        )
        assert response.status_code == 200
        assert upstream.last["path"] == self.SERVICEABILITY

    async def test_it_forwards_no_organization_and_no_permissions(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """The public branch returns before identity is resolved, so the service gets nothing to
        trust — which is exactly why it resolves its own tenant from the slug in the body, on a
        bypass reader, and binds it itself."""
        await client.post(self.SERVICEABILITY, json={"store": "greenleaf", "postal_code": "400050"})
        assert upstream.header("x-org-id") is None
        assert upstream.header("x-perms") is None
        assert upstream.header("x-internal-token") is None

    async def test_it_is_limited_by_address_like_every_other_public_path(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """60 requests per minute per client IP. It throttles bulk enumeration of store slugs; it
        does not prevent it, and no scheme that keeps the data usable by strangers could."""
        await client.post(self.SERVICEABILITY, json={"store": "greenleaf", "postal_code": "400050"})
        assert any(k.startswith("rl:anon:") for k in redis.store)

    async def test_a_redis_outage_is_503_and_never_an_answer(
        self, client: AsyncClient, redis: FakeRedis
    ) -> None:
        """**Unknown is not no** (ADR-0010 §13). `enforce_rate_limit` fails closed, which is
        correct — an unverifiable limit on a public endpoint is an amplifier — and the
        consequence is that a Redis outage takes the buyer-facing check *down*. Down is a 503 the
        client renders as "couldn't check". What it must never be is a well-formed 200 saying the
        shop does not deliver there, which is what a catch-and-answer anywhere on this path would
        produce."""
        redis.fail = True

        response = await client.post(
            self.SERVICEABILITY, json={"store": "greenleaf", "postal_code": "400050"}
        )

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "serviceable" not in response.json()

    async def test_the_public_path_is_exact_and_not_a_prefix(self, client: AsyncClient) -> None:
        """`public_paths` is matched with `full_path in policy.public_paths`. A sibling under the
        same `/public/` segment is **not** public, which is the property the whole anonymous
        buyer API is designed around: variables travel in the query string or the body, never the
        path."""
        assert (await client.post("/api/v1/storefront/public/orders")).status_code == 401
        # The catalogue and the enquiry are public by exact path too: nothing beneath them, and
        # no near-miss beside them, comes along.
        assert (await client.post("/api/v1/storefront/public/catalogue/x")).status_code == 401
        assert (await client.post("/api/v1/storefront/public/enquiry/x")).status_code == 401
        assert (await client.post("/api/v1/storefront/public/enquiries")).status_code == 401
        assert (
            await client.post(f"{self.SERVICEABILITY}/x", json={"store": "g"})
        ).status_code == 401
        # The second entry buys nothing the first did not: still exact, still no path variable.
        # A hostname is a variable, which is why it travels in the body.
        assert (await client.post(f"{self.STORE_CONTEXT}/merchant.com")).status_code == 401
        assert (await client.post("/api/v1/storefront/public/store-contexts")).status_code == 401

    async def test_the_merchant_surface_behind_it_still_needs_a_token(
        self, client: AsyncClient
    ) -> None:
        assert (await client.get("/api/v1/storefront/delivery/zones")).status_code == 401
        assert (await client.get("/api/v1/storefront/store")).status_code == 401

    async def test_the_merchant_surface_needs_the_storefront_entitlement(
        self, client: AsyncClient, mint
    ) -> None:
        """402, not 403: the caller's permissions are fine, the subscription is not."""
        refused = await client.get(
            "/api/v1/storefront/delivery/zones", headers=auth(mint(entitlements=["hrms"]))
        )
        allowed = await client.get(
            "/api/v1/storefront/delivery/zones", headers=auth(mint(entitlements=["storefront"]))
        )
        assert refused.status_code == 402
        assert allowed.status_code == 200

    def test_the_public_route_entry_carries_no_product_key(self) -> None:
        """Two entries on one upstream, and the split is the point. Longest prefix wins, so the
        public entry resolves first and carries no `product` — making "the buyer surface is not
        entitlement-gated" a fact in the route table rather than a consequence of statement
        ordering inside the proxy. A product key there would be **inert**, because the public
        branch returns before the entitlement check, and an inert guard that looks like a guard
        is worse than none."""
        from app.main import routes

        public = routes.resolve("/api/v1/storefront/public/serviceability")
        merchant = routes.resolve("/api/v1/storefront/delivery/zones")
        assert public is not None and merchant is not None
        assert public.prefix == "/api/v1/storefront/public"
        assert public.product is None
        assert merchant.product == "storefront"
        assert public.upstream == merchant.upstream

    def test_the_gateways_own_policy_carries_the_path_not_the_shared_library(self) -> None:
        """Editing `DEFAULT_PUBLIC_PATHS` would put one product's buyer surface in the module
        **both** gateways read, and `admin-gateway` builds its own explicit set. A two-line
        change in this service is the right blast radius."""
        from app.main import STOREFRONT_PUBLIC_PATHS, policy
        from platform_core.gateway import DEFAULT_PUBLIC_PATHS

        assert {
            self.SERVICEABILITY,
            self.STORE_CONTEXT,
            "/api/v1/storefront/public/catalogue",
            "/api/v1/storefront/public/enquiry",
        } == STOREFRONT_PUBLIC_PATHS
        # The merchant's side of both stays behind a token: a stranger may send an enquiry and
        # may never list them, and may read a published catalogue and never edit one.
        assert "/api/v1/storefront/enquiries" not in policy.public_paths
        assert "/api/v1/storefront/products" not in policy.public_paths
        assert not (STOREFRONT_PUBLIC_PATHS & DEFAULT_PUBLIC_PATHS)
        assert policy.public_paths >= STOREFRONT_PUBLIC_PATHS
        # The certificate-issuance gate is reached by Caddy on the compose network and by
        # nothing else (ADR-0012 §5). Through the gateway it is a bearer-token route like any
        # other under /storefront — a stranger who could reach it could ask whether a domain
        # is connected to a store, which is the enumeration the public routes refuse to be.
        assert "/api/v1/storefront/internal/tls-authorize" not in policy.public_paths

    def test_signup_is_public_and_nothing_else_under_account_is(self) -> None:
        """ADR-0013 §3: a stranger may create an organisation and may not read one. The
        sibling routes under /account are ordinary tenant endpoints and stay behind a token."""
        from app.main import ACCOUNT_PUBLIC_PATHS, policy

        assert {"/api/v1/account/signup"} == ACCOUNT_PUBLIC_PATHS
        assert policy.public_paths >= ACCOUNT_PUBLIC_PATHS
        assert "/api/v1/account" not in policy.public_paths
        assert "/api/v1/organization" not in policy.public_paths

    def test_the_bootstrap_shares_the_public_route_entry(self) -> None:
        """One upstream, one entitlement-free prefix, two exact paths. The second public path
        needs no route-table change at all — which is what the prefix split in `routes.json` was
        for."""
        from app.main import routes

        entry = routes.resolve(self.STORE_CONTEXT)
        assert entry is not None
        assert entry.prefix == "/api/v1/storefront/public"
        assert entry.product is None


class TestUpstreamFailure:
    async def test_an_unreachable_service_becomes_a_problem_document(
        self, client: AsyncClient, mint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never leak a connection error to the caller."""
        import httpx

        from app.main import app

        async def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        slept: list[float] = []

        async def no_wait(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr("platform_core.gateway.asyncio.sleep", no_wait)
        await app.state.http.aclose()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))

        response = await client.get(HRMS, headers=auth(mint()))
        assert response.status_code == 503
        assert response.json()["type"].endswith("/upstream-unavailable")
        # It waited out the grace window first, and no longer than that.
        assert 15 < sum(slept) <= 20

    async def test_a_restarting_service_is_waited_for_not_refused(
        self, client: AsyncClient, mint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deploy recreates the container; a sign-in in that window must still land.

        A refused connection never reached the service, so the gateway retries it — for a
        POST too — and the caller sees the service's answer instead of a 503.
        """
        import httpx

        from app.main import app

        attempts = 0

        async def restarting(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(201, json={"ok": True})

        async def no_wait(seconds: float) -> None:
            return None

        monkeypatch.setattr("platform_core.gateway.asyncio.sleep", no_wait)
        await app.state.http.aclose()
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(restarting))

        response = await client.post(HRMS, headers=auth(mint()), json={"a": 1})
        assert response.status_code == 201
        assert attempts == 3

    async def test_an_upstream_error_status_is_passed_through(
        self, client: AsyncClient, mint, upstream: EchoUpstream
    ) -> None:
        """The gateway relays the service's answer rather than inventing one."""
        upstream.status = 422
        response = await client.get(HRMS, headers=auth(mint()))
        assert response.status_code == 422


class TestPermissionResolution:
    """The gateway asks account-service what the caller may do; the token does not say.

    These tests use the *real* resolver rather than the conftest stub, because the
    behaviour under test is the resolver's.
    """

    async def test_resolution_fails_closed(
        self, client: AsyncClient, mint, upstream: EchoUpstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If we cannot determine permissions, refuse — never forward without them.

        Forwarding would hand the caller whatever a downstream service does when `X-Perms`
        is absent, which is a decision no single service should be making on its own.
        """
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
        # Undo the conftest stub — these tests are about the real resolver.
        monkeypatch.setattr(resolver, "resolve", partial(PermissionResolver.resolve, resolver))

        response = await client.get(HRMS, headers=auth(mint()))
        assert response.status_code == 503
        assert not upstream.calls, "a request with unknown permissions must not be forwarded"

    async def test_grants_are_cached_against_the_permission_version(
        self, client: AsyncClient, mint, monkeypatch: pytest.MonkeyPatch, redis: FakeRedis
    ) -> None:
        """A grant change invalidates the cache by construction, not by waiting for a TTL."""
        import httpx

        from app.main import resolver

        loads: list[str] = []

        async def account(request: httpx.Request) -> httpx.Response:
            loads.append(request.url.path)
            return httpx.Response(200, json={"grants": ["hrms.profile.employee.read:TEAM"]})

        monkeypatch.setattr(
            resolver,
            "_client",
            httpx.AsyncClient(base_url="http://account", transport=httpx.MockTransport(account)),
        )
        # Undo the conftest stub — these tests are about the real resolver.
        monkeypatch.setattr(resolver, "resolve", partial(PermissionResolver.resolve, resolver))

        await client.get(HRMS, headers=auth(mint(permission_version=1)))
        await client.get(HRMS, headers=auth(mint(permission_version=1)))
        assert len(loads) == 1, "the second request should be served from cache"

        redis.store[permission_version_key(ORG)] = "2"
        await client.get(HRMS, headers=auth(mint(permission_version=2)))
        assert len(loads) == 2, "a version bump must miss the cache"


class TestNoDatabase:
    def test_the_gateway_opens_no_database_connection(self) -> None:
        """It owns no tables. Everything it needs is in the token or in Redis."""
        from pathlib import Path

        service_dir = Path(__file__).resolve().parent.parent
        assert not (service_dir / "migrations").exists()
        assert not (service_dir / "alembic.ini").exists()
        assert not (service_dir / "app" / "models.py").exists()
