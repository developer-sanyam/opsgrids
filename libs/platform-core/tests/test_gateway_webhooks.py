"""`GatewayPolicy.webhook_paths`: a provider webhook is public by exact path *and* method, and
nothing else.

Meta cannot hold one of our bearer tokens, so its callback has to be reachable without one — but
"public" here is much narrower than `public_paths`. These tests pin each way it must be narrower:

* exact path, judged on the normalised path (a sub-path, a sibling, a `..` traversal is not it);
* only the listed methods — anything else falls through to the bearer check;
* its own rate-limit buckets, so it neither starves nor is starved by sign-in traffic;
* no browser credential in (`Cookie`, `Authorization`, `Origin`, ...) and no cookie or CORS header
  back out, and no identity header — spoofed or derived — on the forwarded request.

The real policy and the real route tables are exercised in `services/app-gateway/tests`; this file
is the generic mechanism, over a synthetic path.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from platform_core import cache
from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import (
    DEFAULT_PUBLIC_PATHS,
    GatewayPolicy,
    Route,
    RouteTable,
    build_gateway,
)
from platform_core.internal_auth import ADMIN_HOP_HEADER

HOOK = "/api/v1/webhooks/hook"
LOGIN = "/api/v1/auth/login"
UPSTREAM = "http://upstream-service:9999"

#: Deliberately tiny, so a limit is crossed in a handful of calls.
PER_ADDRESS = 3
GLOBAL = 5


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self.store.get(key)


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.response_headers: dict[str, str] = {}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json={"ok": True}, headers=self.response_headers)


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


@pytest.fixture
def redis() -> Iterator[_FakeRedis]:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


def _app(upstream: _Upstream, *, global_limit: int = GLOBAL) -> FastAPI:
    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    policy = GatewayPolicy(
        audience="app",
        public_paths=frozenset({LOGIN}),
        webhook_paths={HOOK: frozenset({"GET", "POST"})},
        webhook_rate_limit=PER_ADDRESS,
        webhook_global_rate_limit=global_limit,
        anonymous_rate_limit=PER_ADDRESS,
    )
    routes = RouteTable([Route("/api/v1/auth", UPSTREAM), Route("/api/v1/webhooks/hook", UPSTREAM)])
    build_gateway(application, settings=cfg, routes=routes, policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest.fixture
def app(upstream: _Upstream, redis: _FakeRedis) -> FastAPI:
    return _app(upstream)


async def call(
    app: FastAPI,
    method: str,
    raw_path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("203.0.113.7", 4433),
) -> tuple[int, dict[str, str]]:
    """Drive `app` with `raw_path` as a decoded ASGI `scope["path"]` carries it.

    Not an `httpx` client: a client normalises `..` before sending, which would hide exactly what
    the traversal tests exist to exercise.
    """
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body:
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": method,
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "scheme": "http",
        "server": ("test", 80),
        "client": client,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }
    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    response_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return int(start["status"]), response_headers


class TestPolicyConstruction:
    @pytest.mark.parametrize(
        "path",
        [
            "/webhooks/hook",  # not under /api/, so the proxy can never see it
            "/api/v1/webhooks/hook/",  # a normalised path never ends in a slash
            "/api/v1/webhooks/hook?x=1",  # nor carries a query
        ],
    )
    def test_a_path_that_could_never_match_is_refused_at_construction(self, path: str) -> None:
        with pytest.raises(ValueError, match="exact /api/"):
            GatewayPolicy(
                audience="app", public_paths=frozenset(), webhook_paths={path: frozenset({"GET"})}
            )

    @pytest.mark.parametrize(
        "methods", [frozenset(), frozenset({"get"}), frozenset({"G-T"}), "GET"]
    )
    def test_a_method_list_that_is_empty_or_malformed_is_refused(self, methods: Any) -> None:
        with pytest.raises(ValueError, match="upper-case HTTP method"):
            GatewayPolicy(audience="app", public_paths=frozenset(), webhook_paths={HOOK: methods})

    def test_nothing_is_a_webhook_unless_a_gateway_declares_it(self) -> None:
        assert GatewayPolicy(audience="app", public_paths=frozenset()).webhook_paths == {}

    def test_the_shared_default_carries_no_provider_webhook(self) -> None:
        """A `/webhooks/whatsapp` entry sat here for a long time and could never match — this
        proxy only serves `/api/...` — which made a webhook that answered 401 look public."""
        assert not [p for p in DEFAULT_PUBLIC_PATHS if "webhooks" in p]


class TestPublicByPathAndMethod:
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_a_listed_method_reaches_the_upstream_with_no_token(
        self, app: FastAPI, upstream: _Upstream, method: str
    ) -> None:
        status, _ = await call(app, method, HOOK, body=b"" if method == "GET" else b"{}")

        assert status == 200
        assert [c.url.path for c in upstream.calls] == [HOOK]

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def test_any_other_method_falls_through_to_the_bearer_check(
        self, app: FastAPI, upstream: _Upstream, method: str
    ) -> None:
        status, _ = await call(app, method, HOOK)

        assert status == 401
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "path",
        [
            f"{HOOK}/extra",  # a sub-path is not the path
            f"{HOOK}x",  # a sibling that merely starts with it
            "/api/v1/webhooks/other",  # another webhook
            "/api/v1/webhooks",
            "/api/v1/webhooks/HOOK",  # case matters: no route at all
            f"{HOOK}/../employees",  # judged as /api/v1/webhooks/employees
            f"{HOOK}/../other",
            "/api/v1/webhooks/x/../hookx",
        ],
    )
    async def test_only_the_exact_normalised_path_is_public(
        self, app: FastAPI, upstream: _Upstream, path: str
    ) -> None:
        status, _ = await call(app, "POST", path, body=b"{}")

        assert status in (401, 404), path
        assert upstream.calls == [], path

    async def test_a_trailing_slash_is_the_same_path_and_is_forwarded_without_it(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        """Judged and forwarded as one normalised string, like every other public path."""
        status, _ = await call(app, "POST", f"{HOOK}/", body=b"{}")

        assert status == 200
        assert [c.url.path for c in upstream.calls] == [HOOK]

    async def test_a_traversal_that_lands_on_the_path_is_the_path(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        """Normalisation cuts both ways: what is forwarded is what was judged."""
        status, _ = await call(app, "POST", "/api/v1/auth/../webhooks/hook", body=b"{}")

        assert status == 200
        assert [c.url.path for c in upstream.calls] == [HOOK]


class TestNoCredentialSurface:
    async def test_identity_headers_are_never_forwarded(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        await call(
            app,
            "POST",
            HOOK,
            body=b"{}",
            headers={
                "X-User-Id": "00000000-0000-0000-0000-000000000001",
                "X-Org-Id": "00000000-0000-0000-0000-000000000002",
                "X-Perms": "core.*:ORG",
                "X-Roles": "ORG_OWNER",
                "X-Internal-Token": "guessed",
                "X-Audience": "app",
                ADMIN_HOP_HEADER: "guessed",
            },
        )

        forwarded = upstream.calls[0].headers
        for name in (
            "x-user-id",
            "x-org-id",
            "x-perms",
            "x-roles",
            "x-internal-token",
            "x-audience",
            ADMIN_HOP_HEADER,
        ):
            assert name not in forwarded, name

    async def test_browser_credentials_are_dropped_and_the_signature_travels(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        await call(
            app,
            "POST",
            HOOK,
            body=b'{"a" : 1}',
            headers={
                "Cookie": "refresh=secret",
                "Authorization": "Bearer would-be-a-token",
                "Origin": "https://evil.example",
                "Referer": "https://evil.example/x",
                "Access-Control-Request-Method": "POST",
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=abc",
                "Host": "acme.opsgrids.com",
            },
        )

        forwarded = upstream.calls[0].headers
        for name in (
            "cookie",
            "authorization",
            "origin",
            "referer",
            "access-control-request-method",
        ):
            assert name not in forwarded, name
        assert forwarded["x-hub-signature-256"] == "sha256=abc"
        assert forwarded["content-type"] == "application/json"
        # The tenant hint a public path gets from the Host is not offered to a provider.
        assert "x-org-slug" not in forwarded
        assert upstream.calls[0].content == b'{"a" : 1}'

    async def test_the_reply_carries_no_cookie_and_no_cors_allowance(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        upstream.response_headers = {
            "Set-Cookie": "session=abc",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Credentials": "true",
            "X-Upstream-Thing": "kept",
        }

        status, headers = await call(app, "POST", HOOK, body=b"{}")

        assert status == 200
        assert "set-cookie" not in headers
        assert not [h for h in headers if h.startswith("access-control-")]
        assert headers["x-upstream-thing"] == "kept"

    async def test_an_ordinary_public_path_keeps_its_behaviour(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        """The stripping is the webhook branch's, not a change to every public path: sign-in
        still gets the org slug from the Host and can still set the refresh cookie."""
        upstream.response_headers = {"Set-Cookie": "refresh=abc"}

        _, headers = await call(app, "POST", LOGIN, body=b"{}", headers={"Host": "a.b.c"})

        assert upstream.calls[0].headers["x-org-slug"] == "a"
        assert "set-cookie" in headers


class TestOwnRateLimitBuckets:
    async def test_a_webhook_is_counted_in_its_own_buckets_not_the_anonymous_one(
        self, app: FastAPI, redis: _FakeRedis
    ) -> None:
        await call(app, "POST", HOOK, body=b"{}")

        keys = sorted(redis.store)
        assert len(keys) == 2 and all(k.startswith(f"rl:hook:{HOOK}") for k in keys), keys
        assert not [k for k in keys if k.startswith("rl:anon")]

    async def test_one_address_is_refused_past_its_own_allowance(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        statuses = [(await call(app, "POST", HOOK, body=b"{}"))[0] for _ in range(PER_ADDRESS + 1)]

        assert statuses == [200] * PER_ADDRESS + [429]
        assert len(upstream.calls) == PER_ADDRESS

    async def test_a_refused_address_does_not_spend_the_shared_ceiling(
        self, app: FastAPI, redis: _FakeRedis
    ) -> None:
        """Per address first, then the ceiling: one address hammering the path exhausts its own
        allowance and no more of the shared one, so it cannot starve the provider's other
        addresses."""
        for _ in range(PER_ADDRESS + 10):
            await call(app, "POST", HOOK, body=b"{}")

        (shared,) = [v for k, v in redis.store.items() if "203.0.113.7" not in k]
        assert int(shared) == PER_ADDRESS

    async def test_the_shared_ceiling_refuses_when_many_addresses_together_spend_it(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        statuses = [
            (await call(app, "POST", HOOK, body=b"{}", client=(f"198.51.100.{n}", 1)))[0]
            for n in range(GLOBAL + 1)
        ]

        assert statuses == [200] * GLOBAL + [429]

    async def test_zero_disables_the_shared_ceiling(
        self, upstream: _Upstream, redis: _FakeRedis
    ) -> None:
        app = _app(upstream, global_limit=0)

        await call(app, "POST", HOOK, body=b"{}")

        (only,) = redis.store
        assert "203.0.113.7" in only, "just the per-address bucket"

    async def test_a_sign_in_flood_does_not_starve_the_provider(self, app: FastAPI) -> None:
        statuses = [(await call(app, "POST", LOGIN, body=b"{}"))[0] for _ in range(PER_ADDRESS + 1)]
        assert statuses[-1] == 429, "the anonymous allowance is spent"

        assert (await call(app, "POST", HOOK, body=b"{}"))[0] == 200

    async def test_a_webhook_flood_does_not_starve_sign_in(self, app: FastAPI) -> None:
        statuses = [(await call(app, "POST", HOOK, body=b"{}"))[0] for _ in range(PER_ADDRESS + 1)]
        assert statuses[-1] == 429, "the webhook allowance is spent"

        assert (await call(app, "POST", LOGIN, body=b"{}"))[0] == 200

    async def test_the_limiter_fails_closed_for_a_webhook_too(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        cache._redis = None
        status, _ = await call(app, "POST", HOOK, body=b"{}")

        assert status == 503
        assert upstream.calls == []
