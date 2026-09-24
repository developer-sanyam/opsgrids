"""S07: provider webhooks (Razorpay, Meta's WhatsApp) through a gateway that demands a bearer token.

A webhook carries no bearer token — the provider signs the body and the owning service verifies the
signature over the raw bytes — so the gateway has to let it through *without* letting anything
else through with it. Every test below is one half of that sentence:

* what a webhook may do: reach the upstream, on its exact path and its own methods, body and
  signature header intact, counted in its own rate-limit buckets and capped in size;
* what it may not: pass a cookie, a credential or an `Origin` on, come back with a cookie or a CORS
  grant, or make any *other* path public — a sibling, a longer path, another method, a `..` walk.

Traversal payloads are driven with a hand-built ASGI scope, not an `httpx` client, for the reason
`test_gateway_path_normalization.py` gives: a client normalises a URL before it is ever sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, MutableMapping
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
        return httpx.Response(200, content=self.response_body, headers=self.response_headers)

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


def _build(upstream: _Upstream, **policy: Any) -> FastAPI:
    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    settings: dict[str, Any] = {
        "audience": "app",
        "public_paths": frozenset({"/api/v1/auth/login"}),
        "webhook_paths": WEBHOOKS,
        "webhook_rate_limit": 1000,
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


# ---------------------------------------------------------------------------- no cookie / CORS


class TestNoCookieOrCorsSurface:
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


# ---------------------------------------------------------------------------- rate limiting


class TestOwnBuckets:
    async def test_webhooks_are_counted_under_their_own_key_not_the_anonymous_one(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await client.post(WHATSAPP, content=SIGNED_BODY)
        keys = sorted(redis.store)
        assert len(keys) == 1 and keys[0].startswith(f"rl:hook:{WHATSAPP}:127.0.0.1:"), keys
        await app.state.http.aclose()

    async def test_a_sign_in_flood_cannot_starve_a_webhook_and_the_reverse(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, anonymous_rate_limit=2, webhook_rate_limit=2)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(2):
                assert (await client.post("/api/v1/auth/login", content=b"{}")).status_code == 200
            assert (await client.post("/api/v1/auth/login", content=b"{}")).status_code == 429

            # The anonymous bucket is spent; the webhook's is untouched.
            assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 200
            assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 200
            over = await client.post(RAZORPAY, content=SIGNED_BODY)
            assert over.status_code == 429
            assert over.headers["retry-after"] == "60"
        await app.state.http.aclose()

    async def test_each_webhook_path_has_its_own_ceiling(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(upstream, webhook_global_rate_limit=2)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 200
            assert (await client.post(RAZORPAY, content=SIGNED_BODY)).status_code == 200
            refused = await client.post(RAZORPAY, content=SIGNED_BODY)
            assert refused.status_code == 429
            assert "receiving too many requests" in refused.json()["detail"]
            # Razorpay's ceiling is Razorpay's: Meta's deliveries are not refused for it.
            assert (await client.post(WHATSAPP, content=SIGNED_BODY)).status_code == 200
        await app.state.http.aclose()

    async def test_providers_behind_the_proxy_are_counted_by_their_own_address(
        self, redis: _FakeRedis, upstream: _Upstream
    ) -> None:
        app = _build(
            upstream,
            webhook_rate_limit=1,
            trusted_proxies=parse_trusted_proxies(["172.16.0.0/12"]),
        )
        transport = ASGITransport(app=app, client=("172.18.0.5", 4000))
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            one = await client.post(RAZORPAY, content=b"{}", headers={"X-Forwarded-For": "1.1.1.1"})
            two = await client.post(RAZORPAY, content=b"{}", headers={"X-Forwarded-For": "2.2.2.2"})
            again = await client.post(
                RAZORPAY, content=b"{}", headers={"X-Forwarded-For": "1.1.1.1"}
            )
        assert (one.status_code, two.status_code, again.status_code) == (200, 200, 429)
        await app.state.http.aclose()

    async def test_the_limiter_fails_closed_for_a_webhook_too(
        self, upstream: _Upstream, redis: _FakeRedis
    ) -> None:
        """A provider retries a 503; an unverifiable limit on a public endpoint is not one to
        wave through."""

        async def down(key: str) -> int:
            raise ConnectionError("redis is down")

        redis.incr = down  # type: ignore[method-assign]
        app = _build(upstream)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(RAZORPAY, content=SIGNED_BODY)
        assert response.status_code == 503
        assert upstream.calls == []
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
        assert policy.webhook_global_rate_limit == 0
