"""S07: provider webhooks (Razorpay, Meta's WhatsApp) through a gateway that demands a bearer token.

A webhook carries no bearer token — the provider signs the body and the owning service verifies the
signature over the raw bytes — so the gateway has to let it through *without* letting anything
else through with it. Every test below is one half of that sentence:

* what a webhook may do: reach the upstream, on its exact path and its own methods, body and
  signature header intact, limited only by its own address's *rejected* deliveries and capped in
  size;
* what it may not: pass a cookie, a credential or an `Origin` on, come back with a cookie or a CORS
  grant from the service, or make any *other* path public — a sibling, a longer path, another
  method, a `..` walk;
* what nothing a stranger does may do: use up a bucket that a genuine delivery from a different
  address needs.

Traversal payloads are driven with a hand-built ASGI scope, not an `httpx` client, for the reason
`test_gateway_path_normalization.py` gives: a client normalises a URL before it is ever sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, MutableMapping
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
    GatewayPolicy,
    Route,
    RouteTable,
    build_gateway,
    parse_provider_cidrs,
    parse_trusted_proxies,
)

UPSTREAM = "http://upstream-service:9999"
RAZORPAY = "/api/v1/webhooks/razorpay"
WHATSAPP = "/api/v1/webhooks/whatsapp"
EMPLOYEES = "/api/v1/employees"

#: Small, so the cap is cheap to cross; the behaviour is the same at 256 KiB.
CAP = 512

WEBHOOKS = {RAZORPAY: frozenset({"POST"}), WHATSAPP: frozenset({"GET", "POST"})}

#: A body a provider signed. The gateway must hand the service *these bytes*, whitespace and key
#: order included — re-serialising it is what makes every genuine signature fail.
SIGNED_BODY = b'{ "entity":"event",  "event":"payment.captured",\n "payload": {"a":1} }'


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _Upstream:
    """Records what the service would have been sent, and answers with whatever a test asks."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response_headers: dict[str, str] = {}
        self.response_body = b'{"ok":true}'
        #: The status the service answers with, decided per request — a signature check that
        #: accepts what is signed and refuses what is not.
        self.answer: Callable[[httpx.Request], int] = lambda request: 200

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "path": request.url.path,
                "query": dict(request.url.params),
                "method": request.method,
                "body": request.content,
                "headers": {k.lower(): v for k, v in request.headers.items()},
            }
        )
        return httpx.Response(
            self.answer(request), content=self.response_body, headers=self.response_headers
        )

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def redis() -> Iterator[_FakeRedis]:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


def _build(upstream: _Upstream, *, cors_origins: list[str] | None = None, **policy: Any) -> FastAPI:
    cfg = _Settings(cors_origins=cors_origins) if cors_origins else _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    settings: dict[str, Any] = {
        "audience": "app",
        "public_paths": frozenset({"/api/v1/auth/login"}),
        "webhook_paths": WEBHOOKS,
        "anonymous_rate_limit": 1000,
    }
    settings.update(policy)
    routes = RouteTable(
        [
            Route("/api/v1/auth", UPSTREAM),
            Route(EMPLOYEES, UPSTREAM),
            # The shape both real webhook routes take: a body cap of their own.
            Route(RAZORPAY, UPSTREAM, max_body_bytes=CAP),
            Route(WHATSAPP, UPSTREAM, max_body_bytes=CAP),
        ]
    )
    build_gateway(application, settings=cfg, routes=routes, policy=GatewayPolicy(**settings))
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest_asyncio.fixture
async def client(redis: _FakeRedis, upstream: _Upstream) -> AsyncIterator[AsyncClient]:
    app = _build(upstream)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


async def _raw(app: FastAPI, method: str, raw_path: str, *, body: bytes = b"") -> int:
    """Drive `app` with `raw_path` exactly as a decoded `scope["path"]` carries it."""
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": method,
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": b"",
        "headers": [(b"content-length", str(len(body)).encode())] if body else [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }
    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return int(next(m for m in messages if m["type"] == "http.response.start")["status"])


async def _once(body: bytes) -> MutableMapping[str, Any]:
    return {"type": "http.request", "body": body, "more_body": False}


async def _drive_with_receive(
    app: FastAPI, path: str, receive: Callable[[], Any], *, declared: int
) -> int:
    """Like `_raw`, but the caller supplies its own `receive()` — for a body that never finishes
    arriving, where a fixed `body=` argument cannot express the shape."""
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-length", str(declared).encode())],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }
    messages: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return int(next(m for m in messages if m["type"] == "http.response.start")["status"])


# ------------------------------------------------------------------ what a webhook may do


class TestAWebhookIsDelivered:
    @pytest.mark.parametrize("path", [RAZORPAY, WHATSAPP])
    async def test_an_unauthenticated_post_reaches_the_service(
        self, client: AsyncClient, upstream: _Upstream, path: str
    ) -> None:
        """The regression itself: without this path being public the gateway answered
        `401 Missing bearer token` and the service never saw the delivery."""
        response = await client.post(path, content=SIGNED_BODY)

        assert response.status_code == 200
        assert upstream.last["path"] == path
        assert upstream.last["method"] == "POST"

    async def test_the_signed_bytes_and_the_signature_header_arrive_untouched(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        signature = "sha256=deadbeef"
        await client.post(
            WHATSAPP,
            content=SIGNED_BODY,
            headers={"X-Hub-Signature-256": signature, "X-Razorpay-Signature": "abc123"},
        )
        assert upstream.last["body"] == SIGNED_BODY
        assert upstream.last["headers"]["x-hub-signature-256"] == signature
        assert upstream.last["headers"]["x-razorpay-signature"] == "abc123"

    async def test_metas_subscription_handshake_is_a_get_and_is_answered(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        upstream.response_body = b"1158201444"
        response = await client.get(
            WHATSAPP,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "t",
                "hub.challenge": "1158201444",
            },
        )
        assert response.status_code == 200
        assert response.content == b"1158201444"
        assert upstream.last["query"] == {
            "hub.mode": "subscribe",
            "hub.verify_token": "t",
            "hub.challenge": "1158201444",
        }

    async def test_no_identity_is_ever_written_for_a_provider(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        """Forwarded exactly as anonymous as it arrived, whatever the caller claimed to be."""
        await client.post(
            RAZORPAY,
            content=SIGNED_BODY,
            headers={"X-User-Id": "someone", "X-Org-Id": "some-org", "X-Internal-Token": "forged"},
        )
        forwarded = upstream.last["headers"]
        for name in ("x-user-id", "x-org-id", "x-internal-token", "x-roles", "x-perms"):
            assert name not in forwarded, name


# ------------------------------------------------------------- what a webhook may not do


class TestNothingElseBecomesPublic:
    async def test_any_other_api_path_still_needs_a_bearer_token(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        for method, path in (("GET", EMPLOYEES), ("POST", EMPLOYEES), ("POST", "/api/v1/auth")):
            response = await client.request(method, path, content=b"{}")
            assert response.status_code == 401, (method, path)
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "path",
        [
            RAZORPAY + "/extra",  # longer than the exact path
            RAZORPAY + "-live",  # shares the prefix as text, not as a path segment
            "/api/v1/webhooks/razorpa",
            "/api/v1/webhooks/Razorpay",  # case is not folded
            "/api/v1/webhooks/whatsapp/v2",
            "/api/v1/webhooks",
        ],
    )
    async def test_a_sibling_or_longer_path_is_not_a_webhook(
        self, client: AsyncClient, upstream: _Upstream, path: str
    ) -> None:
        response = await client.post(path, content=SIGNED_BODY)
        assert response.status_code in (401, 404), (path, response.status_code)
        assert upstream.calls == []

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            (RAZORPAY, "GET"),  # Razorpay only ever POSTs
            (RAZORPAY, "PUT"),
            (RAZORPAY, "DELETE"),
            (RAZORPAY, "PATCH"),
            (WHATSAPP, "PUT"),
            (WHATSAPP, "DELETE"),
            (WHATSAPP, "PATCH"),
            (WHATSAPP, "HEAD"),
        ],
    )
    async def test_only_the_methods_a_provider_uses_are_public(
        self, client: AsyncClient, upstream: _Upstream, path: str, method: str
    ) -> None:
        """Public per method: the other verbs fall through to the bearer check like any request."""
        response = await client.request(method, path, content=b"{}")
        assert response.status_code == 401, (path, method)
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            "v1/webhooks/razorpay/../employees",  # normalises to /api/v1/webhooks/employees
            "v1/webhooks/razorpay/../../employees",  # normalises to /api/v1/employees
            "v1/webhooks/whatsapp/../../employees",
            "v1/webhooks/whatsapp/../../auth/operator/login",
            "v1/webhooks/whatsapp/./../../employees",
            "v1//webhooks//whatsapp//..//..//employees",
            "v1/webhooks/whatsapp/%2e%2e/%2e%2e/employees",  # a literal segment, not a `..`
            "v1/webhooks/razorpay/..;/employees",
            "v1/webhooks/whatsapp/../../../../status",
        ],
    )
    async def test_a_dot_dot_walk_around_a_webhook_path_is_judged_by_where_it_lands(
        self, redis: _FakeRedis, upstream: _Upstream, payload: str
    ) -> None:
        """`/api/v1/webhooks/whatsapp/../../employees` starts with a public path as *text* and
        routes to `/api/v1/employees`, which is not one. Deciding on the raw string and
        forwarding the normalised one is the bypass; the decision is taken on the normalised
        path, so this is refused (or 404s) and the service is never called."""
        app = _build(upstream)
        try:
            status = await _raw(app, "POST", f"/api/{payload}", body=SIGNED_BODY)
        finally:
            await app.state.http.aclose()

        assert status in (401, 404), (payload, status)
        assert upstream.calls == [], (payload, upstream.calls)

    async def test_a_walk_that_lands_on_a_webhook_is_the_webhook_and_nothing_more(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """The other direction: `/api/v1/x/../webhooks/razorpay` *is* the Razorpay path once
        resolved, and is forwarded as that — the normalised path, never the dressed-up one."""
        app = _build(upstream)
        try:
            status = await _raw(app, "POST", "/api/v1/auth/../webhooks/razorpay", body=SIGNED_BODY)
        finally:
            await app.state.http.aclose()
        assert status == 200
        assert upstream.last["path"] == RAZORPAY


# ------------------------------------------------------------------- cookies, credentials, CORS


class TestCookiesCredentialsAndCors:
    """Exactly what the gateway does here, and what it does not. The service never sees a cookie,
    an `Authorization` header or an `Origin`, and nothing the service sets comes back as a cookie
    or a CORS grant. What it does **not** do is take the *gateway's own* CORS middleware out of
    the path (`create_app` puts it in front of every route of every service): a request that
    carries an allowed first-party `Origin` still gets that origin's headers, and a preflight from
    one is still answered by the middleware. Providers send no `Origin`, so in practice nothing
    changes for them; the last two tests state the residual instead of claiming it away."""

    async def test_a_browsers_credentials_do_not_reach_the_service(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        await client.post(
            WHATSAPP,
            content=SIGNED_BODY,
            headers={
                "Cookie": "refresh=abc",
                "Authorization": "Bearer some-token",
                "Origin": "https://app.opsgrids.com",
                "Referer": "https://app.opsgrids.com/x",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
                "X-Hub-Signature-256": "sha256=ok",
            },
        )
        forwarded = upstream.last["headers"]
        for name in (
            "cookie",
            "authorization",
            "origin",
            "referer",
            "access-control-request-method",
            "access-control-request-headers",
        ):
            assert name not in forwarded, name
        assert forwarded["x-hub-signature-256"] == "sha256=ok"

    async def test_the_tenant_host_is_not_turned_into_an_organisation_hint(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        """Public sign-in reads the subdomain to find the organisation. A provider's call is not
        for whichever tenant's hostname it happened to be sent to."""
        await client.post(RAZORPAY, content=SIGNED_BODY, headers={"Host": "acme.opsgrids.com"})
        assert "x-org-slug" not in upstream.last["headers"]

    async def test_a_cookie_or_cors_grant_from_the_service_never_reaches_the_provider(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        upstream.response_headers = {
            "Set-Cookie": "session=1; Path=/",
            "Access-Control-Allow-Origin": "https://evil.example",
            "Access-Control-Allow-Credentials": "true",
            "X-Request-Note": "kept",
        }
        response = await client.post(RAZORPAY, content=SIGNED_BODY)

        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["x-request-note"] == "kept", "only the two surfaces are removed"

    async def test_an_ordinary_public_route_still_forwards_the_cookie(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        """The stripping is the webhook branch's, not a global change: sign-in and refresh
        depend on the refresh cookie reaching auth."""
        await client.post("/api/v1/auth/login", content=b"{}", headers={"Cookie": "refresh=abc"})
        assert upstream.last["headers"]["cookie"] == "refresh=abc"

    async def test_the_gateways_own_cors_middleware_still_answers_an_allowed_first_party_origin(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """The residual, stated as behaviour (probe P7). `CORSMiddleware` wraps the proxy, so a
        webhook request that carries an allowed first-party `Origin` gets that origin's grant on
        the way out — while the service behind it still never saw the `Origin` — and an origin
        that is not on the list gets nothing."""
        app = _build(upstream, cors_origins=["https://app.opsgrids.com"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            allowed = await client.post(
                RAZORPAY, content=SIGNED_BODY, headers={"Origin": "https://app.opsgrids.com"}
            )
            other = await client.post(
                RAZORPAY, content=SIGNED_BODY, headers={"Origin": "https://evil.example"}
            )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://app.opsgrids.com"
        assert "origin" not in upstream.calls[0]["headers"]
        assert "access-control-allow-origin" not in other.headers
        await app.state.http.aclose()

    async def test_a_preflight_from_an_allowed_origin_is_answered_by_the_middleware_alone(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, cors_origins=["https://app.opsgrids.com"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            preflight = await client.options(
                RAZORPAY,
                headers={
                    "Origin": "https://app.opsgrids.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://app.opsgrids.com"
        assert upstream.calls == [], "a preflight is never forwarded"
        await app.state.http.aclose()


# ---------------------------------------------------------------------------- rate limiting


#: Two addresses behind the edge, and the one Caddy connects from. Behind the proxy the gateway
#: rate limits on what `X-Forwarded-For` says (from the trusted peer), so tests name callers by it.
CADDY = "172.18.0.5"
TRUSTED = parse_trusted_proxies(["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"])
GENUINE = "13.232.125.1"
FAILURE_LIMIT = 5
#: Comfortably above anything a single test drives through one address, so the arrival cap
#: (MAJOR A.4 / B.1's own limit) never interferes with a test aimed at the *failure* bucket.
ARRIVAL_HEADROOM = 100_000


def _signed_only(request: httpx.Request) -> int:
    """A service that accepts what carries the right signature and answers 400 to the rest."""
    return 200 if request.headers.get("x-razorpay-signature") == "good" else 400


def _via_caddy(app: FastAPI, *, peer: str = CADDY) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 4000)), base_url="http://t")


def _fail_keys(redis: _FakeRedis) -> list[str]:
    return sorted(k for k in redis.store if k.startswith("rl:hook-fail"))


def _arrival_keys(redis: _FakeRedis) -> list[str]:
    return sorted(k for k in redis.store if k.startswith("rl:hook-arrival"))


class TestOnlyRejectionsAreCounted:
    """A webhook has two limits, both per address: rejected deliveries (`hook-fail`) and total
    arrivals (`hook-arrival`, MAJOR A.4/B.1) — never a bucket shared across addresses, so nothing
    a stranger can fill blocks a genuine delivery from somewhere else. Before the failure bucket
    existed, a shared ceiling over all addresses meant five hosts posting bad signatures could 429
    every real Razorpay and Meta delivery for most of each minute, and a provider disables a
    webhook that fails for a day. `webhook_arrival_limit` is set high in most tests here so it
    never interferes with what is under test; `TestTheArrivalLimit` below is where it is."""

    async def test_attackers_cannot_starve_a_genuine_delivery(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """Probe P2. Five hostile addresses each post far more bad-signature bodies than their
        limit; a genuine, signed delivery from a clean address afterwards is delivered."""
        upstream.answer = _signed_only
        app = _build(
            upstream,
            webhook_failure_limit=FAILURE_LIMIT,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            for attacker in range(1, 6):
                for _ in range(FAILURE_LIMIT * 4):
                    await client.post(
                        RAZORPAY,
                        content=b"{}",
                        headers={"X-Forwarded-For": f"203.0.113.{attacker}"},
                    )
            genuine = await client.post(
                RAZORPAY,
                content=SIGNED_BODY,
                headers={"X-Forwarded-For": GENUINE, "X-Razorpay-Signature": "good"},
            )
        assert genuine.status_code == 200
        assert upstream.calls[-1]["headers"]["x-razorpay-signature"] == "good"
        await app.state.http.aclose()

    async def test_two_hundred_failures_from_other_addresses_do_not_touch_a_clean_one(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        upstream.answer = _signed_only
        app = _build(
            upstream,
            webhook_failure_limit=60,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            for i in range(200):
                await client.post(
                    WHATSAPP, content=b"{}", headers={"X-Forwarded-For": f"198.51.100.{i % 20}"}
                )
            genuine = await client.post(
                WHATSAPP,
                content=SIGNED_BODY,
                headers={"X-Forwarded-For": GENUINE, "X-Razorpay-Signature": "good"},
            )
        assert genuine.status_code == 200
        await app.state.http.aclose()

    async def test_an_address_at_its_failure_limit_is_refused_and_a_different_one_is_not(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """g2, reshaped. The bucket is read before anything else happens to the request."""
        upstream.answer = _signed_only
        app = _build(
            upstream,
            webhook_failure_limit=FAILURE_LIMIT,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            bad = {"X-Forwarded-For": "203.0.113.9"}
            statuses = [
                (await client.post(RAZORPAY, content=b"{}", headers=bad)).status_code
                for _ in range(FAILURE_LIMIT)
            ]
            assert statuses == [400] * FAILURE_LIMIT
            calls_before = len(upstream.calls)
            refused = await client.post(RAZORPAY, content=b"{}", headers=bad)
            # Refused up front, with a Retry-After, and never forwarded.
            assert refused.status_code == 429
            assert refused.headers["retry-after"] == "60"
            assert "rejected deliveries" in refused.json()["detail"]
            assert len(upstream.calls) == calls_before
            # Even a *signed* delivery from the same address is refused now: the check is first.
            still = await client.post(
                RAZORPAY, content=SIGNED_BODY, headers={**bad, "X-Razorpay-Signature": "good"}
            )
            assert still.status_code == 429

            other = await client.post(
                RAZORPAY,
                content=SIGNED_BODY,
                headers={"X-Forwarded-For": GENUINE, "X-Razorpay-Signature": "good"},
            )
        assert other.status_code == 200
        await app.state.http.aclose()

    async def test_a_successful_delivery_never_spends_the_failure_bucket(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        upstream.answer = _signed_only
        app = _build(upstream, webhook_failure_limit=2, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(50):
                ok = await client.post(
                    RAZORPAY, content=SIGNED_BODY, headers={"X-Razorpay-Signature": "good"}
                )
                assert ok.status_code == 200
        assert _fail_keys(redis) == [], "2xx must not create, let alone increment, the bucket"
        await app.state.http.aclose()

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_a_failure_on_our_side_never_spends_the_failure_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, status: int
    ) -> None:
        """The service being down is not the provider's fault and must not lock it out — that
        would turn an outage of ours into a webhook the provider disables."""
        upstream.answer = lambda request: status
        app = _build(upstream, webhook_failure_limit=2, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(10):
                assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == status
        assert _fail_keys(redis) == []
        await app.state.http.aclose()

    async def test_a_service_that_cannot_be_reached_never_spends_the_failure_bucket(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        async def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        app = _build(upstream, webhook_failure_limit=1, webhook_arrival_limit=ARRIVAL_HEADROOM)
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(5):
                assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 503
        assert _fail_keys(redis) == []
        await app.state.http.aclose()

    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_each_rejection_status_spends_one(
        self, redis: _FakeRedis, upstream: _Upstream, status: int
    ) -> None:
        upstream.answer = lambda request: status
        app = _build(upstream, webhook_failure_limit=3, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            results = [(await client.post(RAZORPAY, content=b"{}")).status_code for _ in range(4)]
        assert results == [status, status, status, 429]
        keys = _fail_keys(redis)
        assert len(keys) == 1 and keys[0].startswith(f"rl:hook-fail:POST:{RAZORPAY}:127.0.0.1:")
        assert redis.store[keys[0]] == "3", "the refused fourth was never forwarded or counted"
        await app.state.http.aclose()

    async def test_422_never_spends_the_failure_bucket_however_many_times_it_repeats(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """MAJOR B.2: an authentic-but-unattributable event answered 422 used to count like a
        forged one — and a provider retries an event it got no 2xx for, so every retry of one
        genuine, signed event re-spent the same bucket a third party's forgeries would. Sending
        many more than the (small, test-configured) failure limit must never trip it."""
        upstream.answer = lambda request: 422
        app = _build(upstream, webhook_failure_limit=3, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            results = [(await client.post(RAZORPAY, content=b"{}")).status_code for _ in range(10)]
        assert results == [422] * 10
        assert _fail_keys(redis) == []
        await app.state.http.aclose()

    @pytest.mark.parametrize("status", [200, 201, 202, 204, 301, 404, 409, 429, 500, 503])
    async def test_no_other_status_spends_the_failure_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, status: int
    ) -> None:
        upstream.answer = lambda request: status
        app = _build(upstream, webhook_failure_limit=1, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(3):
                assert (await client.post(RAZORPAY, content=b"{}")).status_code == status
        assert _fail_keys(redis) == []
        await app.state.http.aclose()

    async def test_a_wrong_verify_token_on_the_handshake_counts_as_a_failure(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """Meta's subscription handshake is a GET with the token in the query, answered 403 by
        notification when it is wrong — that is somebody probing, and is spent like a bad
        signature. The right token (200) costs nothing."""
        upstream.answer = lambda request: (
            200 if request.url.params.get("hub.verify_token") == "right" else 403
        )
        app = _build(upstream, webhook_failure_limit=2, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(10):
                good = await client.get(
                    WHATSAPP, params={"hub.verify_token": "right", "hub.challenge": "1"}
                )
                assert good.status_code == 200
            assert _fail_keys(redis) == []
            wrong = {"hub.verify_token": "wrong", "hub.challenge": "1"}
            results = [(await client.get(WHATSAPP, params=wrong)).status_code for _ in range(3)]
        assert results == [403, 403, 429]
        await app.state.http.aclose()

    async def test_buckets_are_per_path_and_per_address(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """Razorpay's rejections are not Meta's, and one address's are not another's."""
        upstream.answer = lambda request: 400
        app = _build(
            upstream,
            webhook_failure_limit=1,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            one = {"X-Forwarded-For": "1.1.1.1"}
            assert (await client.post(RAZORPAY, content=b"{}", headers=one)).status_code == 400
            assert (await client.post(RAZORPAY, content=b"{}", headers=one)).status_code == 429
            # Same address, other path; other address, same path.
            assert (await client.post(WHATSAPP, content=b"{}", headers=one)).status_code == 400
            two = {"X-Forwarded-For": "2.2.2.2"}
            assert (await client.post(RAZORPAY, content=b"{}", headers=two)).status_code == 400
        await app.state.http.aclose()

    async def test_the_failure_bucket_is_not_the_anonymous_one_and_a_sign_in_flood_is_harmless(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(
            upstream,
            anonymous_rate_limit=2,
            webhook_failure_limit=2,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(2):
                assert (await client.post("/api/v1/auth/login", content=b"{}")).status_code == 200
            assert (await client.post("/api/v1/auth/login", content=b"{}")).status_code == 429
            # The anonymous bucket is spent; a webhook from the same address is untouched.
            assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 200
        await app.state.http.aclose()

    async def test_no_failure_ceiling_is_shared_across_addresses(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """The structural guarantee. Whatever the volume of *successful* traffic from however many
        addresses, no failure counter exists for it to fill — each address's own `hook-arrival`
        counter exists (that bucket counts every arrival, by design) but stays small and never a
        shared one."""
        app = _build(
            upstream,
            webhook_failure_limit=1,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            for i in range(300):
                headers = {"X-Forwarded-For": f"198.51.100.{i % 250}"}
                assert (
                    await client.post(WHATSAPP, content=b"{}", headers=headers)
                ).status_code == 200
        assert _fail_keys(redis) == []
        assert not [k for k in redis.store if k.startswith("rl:anon")]
        arrivals = _arrival_keys(redis)
        assert len(arrivals) == 250, "one counter per address, never one shared across them"
        assert max(int(redis.store[k]) for k in arrivals) <= 2

    async def test_providers_behind_the_proxy_are_counted_by_their_own_address(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        upstream.answer = lambda request: 400
        app = _build(
            upstream,
            webhook_failure_limit=1,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            for _ in range(2):
                await client.post(RAZORPAY, content=b"{}", headers={"X-Forwarded-For": "1.1.1.1"})
        keys = _fail_keys(redis)
        assert len(keys) == 1 and f"POST:{RAZORPAY}:1.1.1.1:" in keys[0], keys
        await app.state.http.aclose()

    async def test_the_check_fails_closed_when_redis_is_down(
        self, upstream: _Upstream, redis: _FakeRedis
    ) -> None:
        """A provider retries a 503; an unverifiable limit on a public endpoint is not one to
        wave through."""

        async def down(key: str) -> str | None:
            raise ConnectionError("redis is down")

        redis.get = down  # type: ignore[method-assign]
        app = _build(upstream)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(RAZORPAY, content=SIGNED_BODY)
        assert response.status_code == 503
        assert upstream.calls == []
        await app.state.http.aclose()

    async def test_recording_a_failure_never_turns_the_response_into_an_error(
        self, upstream: _Upstream, redis: _FakeRedis
    ) -> None:
        """The service has already answered; a counter that cannot be written loosens one window
        and is logged, it does not replace a handled rejection with a 503. The arrival counter's
        own `incr` (checked *before* forwarding, and correctly fails closed) has to keep working
        for this request to reach the service at all — only the *second* `incr`, the failure
        bucket's post-response record, is the one made to fail here."""
        upstream.answer = lambda request: 400
        original_incr = redis.incr
        calls = 0

        async def flaky_after_the_first(key: str) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return await original_incr(key)
            raise ConnectionError("redis is down")

        redis.incr = flaky_after_the_first  # type: ignore[method-assign]
        app = _build(upstream)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(RAZORPAY, content=b"{}")
        assert response.status_code == 400
        await app.state.http.aclose()

    async def test_a_delivery_walked_in_through_doubled_slashes_is_the_webhook_and_is_counted(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """g1. `//` collapses before the decision, so the service is asked for the real path and
        the rejection lands in that path's bucket rather than being judged on the raw text."""
        upstream.answer = lambda request: 400
        app = _build(upstream)
        try:
            status = await _raw(app, "POST", "/api/v1//webhooks//razorpay", body=SIGNED_BODY)
        finally:
            await app.state.http.aclose()
        assert status == 400
        assert [c["path"] for c in upstream.calls] == [RAZORPAY]
        assert len(_fail_keys(redis)) == 1 and RAZORPAY in _fail_keys(redis)[0]


class TestGatewaySideRefusalsCountToo:
    """MAJOR A.4: a body that never arrives (408) or that declares itself too large (413) used to
    cost the attacker nothing at all — the gateway raised before a `Response` with a status code
    existed to check, so neither joined the failure bucket. Both do now."""

    async def test_a_declared_length_over_the_cap_counts_as_a_rejection(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, webhook_failure_limit=2, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(2):
                over = await client.post(WHATSAPP, content=b"x" * (CAP + 1))
                assert over.status_code == 413
            refused = await client.post(WHATSAPP, content=SIGNED_BODY)
        assert refused.status_code == 429
        assert upstream.calls == [], "every one of these was refused before reaching the service"
        await app.state.http.aclose()

    async def test_a_body_that_never_arrives_counts_as_a_rejection(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(
            upstream,
            webhook_failure_limit=2,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            unauth_body_read_timeout=0.05,
        )
        stall = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await stall.wait()
            return {"type": "http.disconnect"}

        for _ in range(2):
            timed_out = await _drive_with_receive(app, WHATSAPP, receive, declared=CAP)
            assert timed_out == 408
        refused = await _drive_with_receive(
            app, WHATSAPP, lambda: _once(SIGNED_BODY), declared=len(SIGNED_BODY)
        )
        assert refused == 429
        assert upstream.calls == []
        await app.state.http.aclose()


class TestTheArrivalLimit:
    """MAJOR A.4/B.1: a per-address cap on *every* request to one method and path, restored
    without the shared-across-addresses shape round one removed. Never exempted, even for an
    address inside `webhook_provider_cidrs` (MAJOR B.4) — only the failure lockout is."""

    async def test_every_request_counts_regardless_of_outcome(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, webhook_arrival_limit=3, webhook_failure_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            statuses = [
                (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code for _ in range(4)
            ]
        assert statuses == [200, 200, 200, 429]
        await app.state.http.aclose()

    async def test_it_is_per_address_and_never_shared(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(
            upstream,
            webhook_arrival_limit=2,
            webhook_failure_limit=ARRIVAL_HEADROOM,
            trusted_proxies=TRUSTED,
        )
        async with _via_caddy(app) as client:
            one = {"X-Forwarded-For": "1.1.1.1"}
            two = {"X-Forwarded-For": "2.2.2.2"}
            for _ in range(2):
                assert (
                    await client.post(RAZORPAY, content=SIGNED_BODY, headers=one)
                ).status_code == 200
            assert (
                await client.post(RAZORPAY, content=SIGNED_BODY, headers=one)
            ).status_code == 429
            # A different address's allowance is untouched.
            assert (
                await client.post(RAZORPAY, content=SIGNED_BODY, headers=two)
            ).status_code == 200
        await app.state.http.aclose()

    async def test_it_is_per_method_so_a_get_flood_does_not_spend_a_posts_allowance(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, webhook_arrival_limit=2, webhook_failure_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(5):
                await client.get(WHATSAPP, params={"hub.verify_token": "x", "hub.challenge": "1"})
            post = await client.post(WHATSAPP, content=SIGNED_BODY)
        assert post.status_code == 200
        await app.state.http.aclose()

    async def test_the_arrival_limit_still_applies_inside_a_provider_cidr(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        cidrs = parse_provider_cidrs(["203.0.113.0/24"])
        app = _build(
            upstream,
            webhook_arrival_limit=2,
            webhook_failure_limit=ARRIVAL_HEADROOM,
            webhook_provider_cidrs=cidrs,
            trusted_proxies=TRUSTED,
        )
        headers = {"X-Forwarded-For": "203.0.113.99"}
        async with _via_caddy(app) as client:
            for _ in range(2):
                assert (
                    await client.post(RAZORPAY, content=SIGNED_BODY, headers=headers)
                ).status_code == 200
            refused = await client.post(RAZORPAY, content=SIGNED_BODY, headers=headers)
        assert refused.status_code == 429
        await app.state.http.aclose()


class TestWebhookProviderCidrs:
    """MAJOR B.4: an address inside a declared provider range is never locked out by the failure
    bucket, because that range is shared with every other merchant on the same provider and a
    third party pointing their own webhook at our URL should not be able to burn it."""

    async def test_an_address_inside_the_range_is_never_locked_out(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        upstream.answer = lambda request: 400
        cidrs = parse_provider_cidrs(["203.0.113.0/24"])
        app = _build(
            upstream,
            webhook_failure_limit=3,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            webhook_provider_cidrs=cidrs,
            trusted_proxies=TRUSTED,
        )
        headers = {"X-Forwarded-For": "203.0.113.50"}
        async with _via_caddy(app) as client:
            statuses = [
                (await client.post(RAZORPAY, content=b"{}", headers=headers)).status_code
                for _ in range(10)
            ]
        assert statuses == [400] * 10, "far more than the limit, never once refused"
        assert _fail_keys(redis) == [], "exempt: the bucket is never even read for this address"
        await app.state.http.aclose()

    async def test_an_address_outside_the_range_is_still_locked_out(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        upstream.answer = lambda request: 400
        cidrs = parse_provider_cidrs(["203.0.113.0/24"])
        app = _build(
            upstream,
            webhook_failure_limit=3,
            webhook_arrival_limit=ARRIVAL_HEADROOM,
            webhook_provider_cidrs=cidrs,
            trusted_proxies=TRUSTED,
        )
        headers = {"X-Forwarded-For": "198.51.100.7"}
        async with _via_caddy(app) as client:
            statuses = [
                (await client.post(RAZORPAY, content=b"{}", headers=headers)).status_code
                for _ in range(4)
            ]
        assert statuses == [400, 400, 400, 429]
        await app.state.http.aclose()

    async def test_400s_n3_then_upstream_fixed_the_next_genuine_post_is_200(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """Below the limit, a run of failures never blocks the request that follows once the
        upstream is fixed — a transient misconfiguration is not a permanent lockout."""
        upstream.answer = lambda request: 400
        app = _build(upstream, webhook_failure_limit=5, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(3):
                assert (await client.post(RAZORPAY, content=b"{}")).status_code == 400
            upstream.answer = lambda request: 200
            genuine = await client.post(RAZORPAY, content=SIGNED_BODY)
        assert genuine.status_code == 200
        await app.state.http.aclose()

    async def test_403_handshake_gets_n4_never_touch_a_posts_bucket_then_genuine_post_is_200(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """Method-keying (MAJOR B.1): the handshake's own `GET` bucket locking itself out past the
        failure limit is the mechanism working as designed, on its own method — the point is that
        none of it ever touches the `POST` bucket for the same address, so a genuine delivery
        still succeeds."""
        upstream.answer = lambda request: 403 if request.method == "GET" else 200
        app = _build(upstream, webhook_failure_limit=3, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            statuses = [
                (await client.get(WHATSAPP, params={"hub.verify_token": "wrong"})).status_code
                for _ in range(5)
            ]
            assert statuses == [403, 403, 403, 429, 429]
            genuine = await client.post(WHATSAPP, content=SIGNED_BODY)
        assert genuine.status_code == 200
        await app.state.http.aclose()

    async def test_422s_n5_never_lock_out_however_many_then_genuine_post_is_200(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """422 is excluded entirely (MAJOR B.2): even well past the failure limit, none of them
        count, so the genuine delivery that follows is never refused."""
        upstream.answer = lambda request: 422
        app = _build(upstream, webhook_failure_limit=3, webhook_arrival_limit=ARRIVAL_HEADROOM)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(10):
                assert (await client.post(RAZORPAY, content=b"{}")).status_code == 422
            upstream.answer = lambda request: 200
            genuine = await client.post(RAZORPAY, content=SIGNED_BODY)
        assert genuine.status_code == 200
        await app.state.http.aclose()


# ------------------------------------------------------------------------------ body size


class TestTheBodyCap:
    async def test_a_body_at_the_cap_is_delivered(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        response = await client.post(RAZORPAY, content=b"x" * CAP)
        assert response.status_code == 200
        assert len(upstream.last["body"]) == CAP

    async def test_a_declared_length_over_the_cap_is_refused_before_anything_is_read(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        response = await client.post(WHATSAPP, content=b"x" * (CAP + 1))
        assert response.status_code == 413
        assert response.json()["type"].endswith("/payload-too-large")
        assert upstream.calls == []

    async def test_the_bytes_decide_when_the_length_is_missing(
        self, client: AsyncClient, upstream: _Upstream
    ) -> None:
        """A chunked delivery declares no length at all. The counter is the enforcement; the
        header is only an early exit."""

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(CAP // 64 + 2):
                yield b"y" * 64

        response = await client.post(RAZORPAY, content=chunks())
        assert response.status_code == 413
        assert upstream.calls == []

    async def test_the_bytes_decide_when_the_length_lies(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        """`Content-Length` says the body is small; the body is not."""
        app = _build(upstream)
        body = b"z" * (CAP * 4)
        scope: MutableMapping[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": RAZORPAY,
            "raw_path": RAZORPAY.encode(),
            "query_string": b"",
            "headers": [(b"content-length", b"10")],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
        }
        messages: list[MutableMapping[str, Any]] = []
        sent = False

        async def receive() -> MutableMapping[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            messages.append(message)

        try:
            await app(scope, receive, send)
        finally:
            await app.state.http.aclose()
        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 413
        assert upstream.calls == []


# ------------------------------------------------------------------------ policy validation


class TestPolicyValidation:
    @pytest.mark.parametrize(
        "webhook_paths",
        [
            {"/webhooks/whatsapp": frozenset({"POST"})},  # not under /api: can never match
            {"/api/v1/webhooks/whatsapp/": frozenset({"POST"})},  # a normalised path has no tail /
            {"/api/v1/webhooks/whatsapp?x=1": frozenset({"POST"})},  # nor a query
            {WHATSAPP: frozenset()},  # public for no method at all
            {WHATSAPP: frozenset({"post"})},  # would never equal request.method
        ],
    )
    def test_an_entry_that_could_never_match_or_would_match_wrongly_refuses_at_construction(
        self, webhook_paths: dict[str, frozenset[str]]
    ) -> None:
        with pytest.raises(ValueError, match="webhook path"):
            GatewayPolicy(audience="app", public_paths=frozenset(), webhook_paths=webhook_paths)

    def test_a_policy_with_no_webhooks_is_the_old_policy(self) -> None:
        """`admin-gateway` builds its policy without any of the new fields."""
        policy = GatewayPolicy(audience="operator", public_paths=frozenset())
        assert dict(policy.webhook_paths) == {}
        assert policy.trusted_proxies == ()
        assert policy.anonymous_global_rate_limit == 0
        assert policy.anonymous_content_global_rate_limit == 0
        assert policy.credential_paths == frozenset()
        assert policy.webhook_provider_cidrs == ()

    def test_the_shipped_webhook_defaults(self) -> None:
        """MAJOR B.3: raised from round one's 60 — that address space is genuinely shared, so the
        bar for "this address is hostile" has to be higher than for any other unauthenticated
        caller."""
        policy = GatewayPolicy(audience="app", public_paths=frozenset())
        assert policy.webhook_failure_limit == 600
        assert policy.webhook_arrival_limit == 600

    @pytest.mark.parametrize(
        "kw",
        [
            {"webhook_failure_limit": 0},
            {"webhook_failure_limit": -1},
            {"webhook_arrival_limit": 0},
            {"webhook_arrival_limit": -1},
            {"anonymous_global_rate_limit": -1},
            {"anonymous_content_global_rate_limit": -1},
            {"anonymous_rate_limit": 0},
            {"rate_limit": 0},
            {"unauth_body_slots_per_address": 0},
            {"unauth_body_slots_global": 0},
            {"unauth_body_read_timeout": 0},
            {"unauth_body_read_timeout": -3.0},
        ],
    )
    def test_a_limit_that_refuses_everything_is_refused_at_construction(
        self, kw: dict[str, Any]
    ) -> None:
        """Probe P6. `0` on a per-address limit is not "off" — the counter is incremented first and
        one is over zero — so it would answer 429 to the very first request. It stops boot."""
        with pytest.raises(ValueError):
            GatewayPolicy(audience="app", public_paths=frozenset(), **kw)

    @pytest.mark.parametrize(
        "kw", [{"anonymous_global_rate_limit": 0}, {"anonymous_content_global_rate_limit": 0}]
    )
    def test_zero_is_off_only_where_it_is_documented_as_off(self, kw: dict[str, Any]) -> None:
        policy = GatewayPolicy(audience="app", public_paths=frozenset(), **kw)
        assert policy.anonymous_global_rate_limit == 0

    def test_a_credential_path_that_is_not_public_is_a_typo_and_is_refused(self) -> None:
        with pytest.raises(ValueError, match="credential paths"):
            GatewayPolicy(
                audience="app",
                public_paths=frozenset({"/api/v1/auth/login"}),
                credential_paths=frozenset({"/api/v1/auth/logn"}),
            )


class TestParseProviderCidrs:
    """MAJOR B.4: `WEBHOOK_PROVIDER_CIDRS` runs in the opposite direction from `parse_trusted_
    proxies` — these are expected to be *public* ranges, so the guard is not "must be private", it
    is "must not be implausibly wide"."""

    def test_addresses_and_narrow_ranges_parse(self) -> None:
        parsed = parse_provider_cidrs(["203.0.113.0/24", " 198.51.100.7 ", "", "2001:db8::/32"])
        assert [str(n) for n in parsed] == ["203.0.113.0/24", "198.51.100.7/32", "2001:db8::/32"]

    def test_empty_is_the_default_and_exempts_nobody(self) -> None:
        assert parse_provider_cidrs([]) == ()

    @pytest.mark.parametrize(
        "entry",
        [
            "0.0.0.0/0",
            "0.0.0.0/7",  # one bit narrower than the IPv4 floor
            "10.0.0.0/7",
            "::/0",
            "2000::/23",  # one bit narrower than the IPv6 floor
        ],
    )
    def test_a_range_wider_than_the_sanity_floor_is_refused(self, entry: str) -> None:
        with pytest.raises(ValueError, match="wider than the"):
            parse_provider_cidrs([entry])

    @pytest.mark.parametrize("entry", ["10.0.0.0/8", "203.0.113.0/24", "2000::/24", "::1/128"])
    def test_exactly_the_floor_or_narrower_is_accepted(self, entry: str) -> None:
        assert parse_provider_cidrs([entry])

    def test_a_malformed_entry_stops_boot(self) -> None:
        with pytest.raises(ValueError, match="not an IP address or a CIDR range"):
            parse_provider_cidrs(["not-an-address"])

    def test_the_refusal_names_the_entry(self) -> None:
        with pytest.raises(ValueError, match=r"8\.0\.0\.0/4"):
            parse_provider_cidrs(["8.0.0.0/4"])
