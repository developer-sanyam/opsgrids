"""The activation-race regression: a token that is not stale can still be refused.

The sequence this file reproduces (see the comment at the resolver-call site in
`gateway.py` for the full account):

1. product-service commits an activation synchronously. A token minted right after carries
   the product in `ent` and the org's current permission version `pv = N`.
2. The owner's admin grant for that product installs ASYNCHRONOUSLY — outbox -> relay ->
   account-service consumer — which is what bumps the version to `N + 1`.
3. A request in between has a token that is neither stale (`pv == N == current`) nor
   unentitled (the product is in `ent`), so both of the gateway's earlier checks pass it.
4. `PermissionResolver.resolve` may then read — or serve from cache — the grants from
   *before* the install landed: non-empty (the owner already holds their other grants) but
   missing this product's codes specifically, and cacheable for up to 60s.

The fix's job is narrow: detect exactly that signature (entitled per the token, absent per
the resolved grants) and re-read account-service once, bypassing the cache, before deciding
what to forward — never fabricating a grant, and caching the fresh answer briefly instead of
for a full minute when it is still absent.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from platform_core import cache
from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import (
    GRANT_PROPAGATION_RETRY_TTL,
    GatewayPolicy,
    PermissionResolver,
    Route,
    RouteTable,
    build_gateway,
)

ORG = str(uuid.uuid4())
USER = str(uuid.uuid4())
MEMBERSHIP = str(uuid.uuid4())

UPSTREAM = "http://upstream-service:9999"
#: A product-gated route — this is the one the race lands on.
ENGAGE = "/api/v1/engage/campaigns"
#: A platform route every customer has, whatever they bought. No `product`, so the race's
#: detection can never fire on it.
PLATFORM = "/api/v1/organization"

#: What the owner already held before the product's grant landed — non-empty, which is
#: exactly why "don't cache an empty grants list" was the wrong fix.
OWNER_GRANTS_BEFORE_INSTALL = ["core.org.settings.write:ORG"]
#: What account-service reports once the consumer has installed the product's grant.
ENGAGE_GRANT = "engage.campaign.manage:ORG"


def _routes() -> RouteTable:
    return RouteTable(
        [
            Route("/api/v1/engage", UPSTREAM, product="engage"),
            Route("/api/v1/organization", UPSTREAM),
        ]
    )


def authz_key(version: int) -> str:
    """The exact cache key `PermissionResolver.resolve` reads and writes."""
    return f"authz:{ORG}:{MEMBERSHIP}:{version}"


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer stubbed"}


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class FakeRedis:
    """Enough Redis for the gateway, plus the one extra thing these tests need: what TTL a
    `set` was actually given, so the short-vs-normal TTL policy is checkable directly rather
    than by waiting real seconds for one to expire."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)

    async def aclose(self) -> None:
        return None


class FakeAccountService:
    """Stands in for `/internal/v1/memberships/{id}/grants` — the source of truth.

    Counts calls so a test can assert the fresh re-read fired exactly when — and only
    when — the race's signature was present, never on an ordinary request.
    """

    def __init__(self, grants: list[str]) -> None:
        self.grants = grants
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"grants": self.grants})


class EchoUpstream:
    """Records the headers the gateway forwarded — `x-perms` above all else here."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"ok": True})

    def header(self, name: str) -> str | None:
        return self.calls[-1].get(name.lower()) if self.calls else None


@pytest.fixture
def redis() -> Any:
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def account() -> FakeAccountService:
    # No grant for the product by default — each test sets what it needs.
    return FakeAccountService(list(OWNER_GRANTS_BEFORE_INSTALL))


@pytest.fixture
def upstream() -> EchoUpstream:
    return EchoUpstream()


@pytest.fixture
def claims_box() -> dict[str, Any]:
    """Mutable claims the patched `decode_token` reads per request.

    Standing in for a verified access token — as `test_gateway_body_limit.py` does — keeps
    these tests about the resolver's race, not about minting and verifying a real, signed
    one. Tests mutate this before calling the client.
    """
    return {
        "sub": USER,
        "act_org": ORG,
        "mem": MEMBERSHIP,
        "pv": 0,
        "ent": [],
        "roles": [],
        "loc": [],
        "amr": ["pwd"],
    }


@pytest.fixture
def app(
    redis: Any,
    account: FakeAccountService,
    upstream: EchoUpstream,
    claims_box: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    from platform_core import gateway as gateway_module

    def _claims(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(claims_box)

    monkeypatch.setattr(gateway_module, "decode_token", _claims)

    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)

    resolver = PermissionResolver("http://account-service", cfg)
    monkeypatch.setattr(
        resolver,
        "_client",
        httpx.AsyncClient(
            base_url="http://account-service", transport=httpx.MockTransport(account.handler)
        ),
    )

    policy = GatewayPolicy(audience="app", public_paths=frozenset())
    build_gateway(application, settings=cfg, routes=_routes(), policy=policy, resolver=resolver)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


class TestTheActivationRaceRegression:
    """Reproduces purchase -> immediate use, exactly as the E2E suite did."""

    async def test_the_first_request_after_the_grant_lands_gets_it_not_the_stale_cache(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        redis: FakeRedis,
        upstream: EchoUpstream,
        claims_box: dict[str, Any],
    ) -> None:
        pv = 7
        # The cache holds exactly what `resolve_cached` would have written moments before
        # the consumer installed the grant: non-empty, no `engage.*` — the owner's other
        # grants, from before the install landed.
        redis.store[authz_key(pv)] = json.dumps(OWNER_GRANTS_BEFORE_INSTALL)
        # The grant has now landed at the source of truth. This is what the old code path
        # never looked at again inside the 60s window.
        account.grants = [*OWNER_GRANTS_BEFORE_INSTALL, ENGAGE_GRANT]

        # A token minted right after activation: not stale (no `pv:{org}` bump has happened
        # yet — nothing in this test ever writes one), and entitled (`engage` is in `ent`).
        claims_box.update(pv=pv, ent=["engage"])

        response = await client.get(ENGAGE, headers=auth())

        assert response.status_code == 200
        forwarded = (upstream.header("x-perms") or "").split(",")
        assert ENGAGE_GRANT in forwarded, (
            "the first request after the grant lands must get it, not the snapshot cached "
            "before it landed"
        )


class TestTheFreshReadTTLPolicy:
    async def test_a_fresh_read_that_still_lacks_the_product_is_cached_briefly_not_for_a_minute(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        redis: FakeRedis,
        claims_box: dict[str, Any],
    ) -> None:
        pv = 3
        redis.store[authz_key(pv)] = json.dumps(OWNER_GRANTS_BEFORE_INSTALL)
        # Nothing has landed — a genuinely unpermitted member, indistinguishable from the
        # gateway's point of view from an owner still mid-propagation.
        account.grants = list(OWNER_GRANTS_BEFORE_INSTALL)
        claims_box.update(pv=pv, ent=["engage"])

        response = await client.get(ENGAGE, headers=auth())

        assert response.status_code == 200
        assert redis.ttls[authz_key(pv)] == GRANT_PROPAGATION_RETRY_TTL
        assert redis.ttls[authz_key(pv)] != 60

    async def test_a_fresh_read_that_now_has_the_product_is_cached_for_the_normal_ttl(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        redis: FakeRedis,
        claims_box: dict[str, Any],
    ) -> None:
        pv = 4
        redis.store[authz_key(pv)] = json.dumps(OWNER_GRANTS_BEFORE_INSTALL)
        account.grants = [*OWNER_GRANTS_BEFORE_INSTALL, ENGAGE_GRANT]
        claims_box.update(pv=pv, ent=["engage"])

        response = await client.get(ENGAGE, headers=auth())

        assert response.status_code == 200
        assert redis.ttls[authz_key(pv)] == 60, (
            "the grant has landed; this reading is now the truth and earns the normal ttl"
        )


class TestNoSpuriousFreshReads:
    async def test_a_route_with_no_product_never_triggers_a_fresh_read(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        claims_box: dict[str, Any],
    ) -> None:
        claims_box.update(pv=1, ent=[])
        account.grants = list(OWNER_GRANTS_BEFORE_INSTALL)

        response = await client.get(PLATFORM, headers=auth())

        assert response.status_code == 200
        assert account.calls == 1, "exactly the one ordinary resolution — no product to race on"

    async def test_a_caller_not_entitled_to_the_product_gets_402_and_the_resolver_is_never_touched(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        claims_box: dict[str, Any],
    ) -> None:
        """402 is raised by the entitlement gate, which runs before permission resolution
        at all — so the resolver, fresh or cached, is never even reached."""
        claims_box.update(pv=1, ent=[])  # engage was never activated for this org

        response = await client.get(ENGAGE, headers=auth())

        assert response.status_code == 402
        assert account.calls == 0


class TestAuthorizationDoesNotWeaken:
    async def test_a_genuine_absence_at_the_source_of_truth_stays_absent_after_the_fresh_read(
        self,
        client: AsyncClient,
        account: FakeAccountService,
        redis: FakeRedis,
        upstream: EchoUpstream,
        claims_box: dict[str, Any],
    ) -> None:
        """The property a reviewer checks first: re-reading can only ever confirm the
        truth, never invent a grant the membership does not hold. A member who was simply
        never given a role in this product gets refused — by the *service*, on the 403 the
        missing `engage.*` code produces — not waved through by the gateway."""
        pv = 9
        redis.store[authz_key(pv)] = json.dumps(OWNER_GRANTS_BEFORE_INSTALL)
        account.grants = list(OWNER_GRANTS_BEFORE_INSTALL)  # never granted, fresh or not
        claims_box.update(pv=pv, ent=["engage"])

        response = await client.get(ENGAGE, headers=auth())

        # The gateway forwards the request either way — 402/401 are its own gates, and both
        # already passed. What must not happen is `engage.*` appearing in what it forwards.
        assert response.status_code == 200
        forwarded = (upstream.header("x-perms") or "").split(",")
        assert not any(code.startswith("engage.") for code in forwarded)
