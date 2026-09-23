"""`_normalize_path`, and the public-prefix mechanism it makes safe.

`GatewayPolicy.public_path_prefixes` exists so a route with a variable segment — Engage's
per-recipient unsubscribe link is the first of these — can be public without being an exact
string in `public_paths`. That is only safe if the *decision* ("is this path public") and the
*action* ("what does the gateway actually forward") can never disagree, and they can only stay
in lockstep if both read the same normalised path — which is why `proxy` computes it once and
threads it through every check, including `_forward`, rather than trusting `request.url.path`
again downstream.

The raw-scope helper below exists because an ordinary `httpx.AsyncClient` normalises a URL
string — collapsing `..` and `//` — the moment it is asked to build a request. That is exactly
the attack this module defends against, so testing it through `client.get(...)` would prove
nothing: the client would have already fixed the very thing being exploited before the request
ever reached the ASGI app. Driving the app with a hand-built scope is what actually reproduces
what a raw HTTP request line can carry (proven here, not assumed) and what uvicorn hands
Starlette in production — percent-decoded, but not otherwise normalised.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from platform_core import cache
from platform_core.config import ServiceSettings
from platform_core.gateway import GatewayPolicy, Route, RouteTable, _normalize_path, build_gateway

pytestmark = pytest.mark.integration

UPSTREAM = "http://notification-upstream:9999"


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class _FakeRedis:
    """Enough Redis for the rate limiter — the only thing a public request touches."""

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


class _EchoUpstream:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "path": request.url.path,
                "headers": {k.lower(): v for k, v in request.headers.items()},
            }
        )
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def upstream() -> _EchoUpstream:
    return _EchoUpstream()


@pytest.fixture
def app(upstream: _EchoUpstream, redis: Any) -> FastAPI:
    from platform_core.app import create_app

    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    routes = RouteTable([Route("/api/v1/engage", UPSTREAM)])
    policy = GatewayPolicy(
        audience="app",
        public_paths=frozenset(),
        public_path_prefixes=frozenset({"/api/v1/engage/unsubscribe/"}),
        anonymous_rate_limit=3,
    )
    build_gateway(application, settings=cfg, routes=routes, policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


async def _raw_request(
    app: FastAPI, method: str, raw_path: str, *, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str]]:
    """Drive the ASGI app with `raw_path` exactly as a raw HTTP request line would carry it —
    no client-side URL normalisation in the way. See the module docstring."""
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": method,
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }
    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    status = start["status"]
    resp_headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return status, resp_headers


# --------------------------------------------------------------------- _normalize_path itself


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/v1/engage/unsubscribe/abc", "/api/v1/engage/unsubscribe/abc"),
        ("/api//v1/engage/unsubscribe/abc", "/api/v1/engage/unsubscribe/abc"),
        ("//api/v1/engage/unsubscribe/abc", "/api/v1/engage/unsubscribe/abc"),
        ("/api/v1/engage/unsubscribe/./abc", "/api/v1/engage/unsubscribe/abc"),
        ("/api/v1/engage/unsubscribe/abc/", "/api/v1/engage/unsubscribe/abc"),
        (
            "/api/v1/engage/unsubscribe/x/../../campaigns",
            "/api/v1/engage/campaigns",
        ),
        ("/../etc/passwd", "/etc/passwd"),
        ("/api/v1/../../../../etc/passwd", "/etc/passwd"),
        ("/api/v1/engage/unsubscribe/abc//../def", "/api/v1/engage/unsubscribe/def"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert _normalize_path(raw) == expected


def test_a_public_prefix_must_end_with_a_slash() -> None:
    with pytest.raises(ValueError, match="must end with"):
        GatewayPolicy(
            audience="app",
            public_paths=frozenset(),
            public_path_prefixes=frozenset({"/api/v1/engage/unsubscribe"}),
        )


# --------------------------------------------------------------------- the proxy end to end


async def test_a_request_under_the_public_prefix_reaches_upstream_anonymously(
    app: FastAPI, upstream: _EchoUpstream
) -> None:
    status, _headers = await _raw_request(app, "GET", "/api/v1/engage/unsubscribe/real-token-abc")
    assert status == 200
    assert upstream.calls[-1]["path"] == "/api/v1/engage/unsubscribe/real-token-abc"
    # No identity header — the public branch never asks for a token, so it never has one to
    # vouch for. Only the public branch's own headers travel.
    forwarded = upstream.calls[-1]["headers"]
    for spoofable in ("x-user-id", "x-org-id", "x-internal-token", "x-perms"):
        assert spoofable not in forwarded


async def test_repeated_public_requests_are_rate_limited_by_address(
    app: FastAPI, upstream: _EchoUpstream
) -> None:
    for _ in range(3):
        status, _ = await _raw_request(app, "GET", "/api/v1/engage/unsubscribe/tok")
        assert status == 200
    status, _ = await _raw_request(app, "GET", "/api/v1/engage/unsubscribe/tok")
    assert status == 429


async def test_a_traversal_escaping_the_public_prefix_is_refused_before_reaching_upstream(
    app: FastAPI, upstream: _EchoUpstream
) -> None:
    """The concrete exploit this whole mechanism exists to refuse: dressing up a fully
    authenticated, entitlement-gated Engage route (`.../campaigns`, permissioned campaign
    read/write) as a path *under* the public unsubscribe prefix. Normalised, it plainly is not
    — and it must be judged on that, not on the raw text it arrived as."""
    assert (
        _normalize_path("/api/v1/engage/unsubscribe/x/../../campaigns")
        == "/api/v1/engage/campaigns"
    )
    status, _headers = await _raw_request(
        app, "GET", "/api/v1/engage/unsubscribe/x/../../campaigns"
    )
    assert status == 401
    assert upstream.calls == []


async def test_a_traversal_that_stays_inside_the_prefix_is_still_public(
    app: FastAPI, upstream: _EchoUpstream
) -> None:
    """The mechanism narrows to exactly the prefix — it must not become stricter than intended
    and refuse a harmless `.` a real client happened to include."""
    status, _headers = await _raw_request(app, "GET", "/api/v1/engage/unsubscribe/./tok")
    assert status == 200
    assert upstream.calls[-1]["path"] == "/api/v1/engage/unsubscribe/tok"
