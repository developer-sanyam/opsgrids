"""`GatewayPolicy.public_prefix_rate_limits`: a public prefix with a rate-limit bucket of its own.

Every public path used to share one `anon:{address}` bucket at `anonymous_rate_limit` (60 a
minute). That is right for sign-in and wrong for Engage's open pixel and tracked links, which
mailbox providers' image proxies and corporate NATs fetch for many recipients from one address: the
sixty-first click in a minute answered 429 instead of redirecting. A prefix that opts in is counted
apart, at its own figure; every other public path, and every policy that sets nothing, is unchanged.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from platform_core import cache
from platform_core.config import ServiceSettings
from platform_core.gateway import GatewayPolicy, Route, RouteTable, build_gateway

pytestmark = pytest.mark.integration

UPSTREAM = "http://notification-upstream:9999"
TRACKING = "/api/v1/engage/t/"
UNSUBSCRIBE = "/api/v1/engage/unsubscribe/"
ANONYMOUS_LIMIT = 3
TRACKING_LIMIT = 10


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


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


def _app(policy: GatewayPolicy) -> FastAPI:
    from platform_core.app import create_app

    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    routes = RouteTable([Route("/api/v1/engage", UPSTREAM)])
    build_gateway(application, settings=cfg, routes=routes, policy=policy)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return application


def _policy(**overrides: Any) -> GatewayPolicy:
    fields: dict[str, Any] = {
        "audience": "app",
        "public_paths": frozenset(),
        "public_path_prefixes": frozenset({TRACKING, UNSUBSCRIBE}),
        "anonymous_rate_limit": ANONYMOUS_LIMIT,
    }
    return GatewayPolicy(**{**fields, **overrides})


@pytest.fixture
def default_app(redis: Any) -> FastAPI:
    return _app(_policy())


@pytest.fixture
def opted_in_app(redis: Any) -> FastAPI:
    return _app(_policy(public_prefix_rate_limits={TRACKING: TRACKING_LIMIT}))


async def _get(app: FastAPI, raw_path: str, *, client_host: str = "127.0.0.1") -> int:
    """Drive `app` with `raw_path` exactly as an ASGI server hands it over (see
    `test_gateway_path_normalization`): no client-side `..` collapsing in the way."""
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": (client_host, 12345),
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }
    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return int(next(m for m in messages if m["type"] == "http.response.start")["status"])


# ------------------------------------------------------------------------------ the policy


def test_the_default_changes_nothing() -> None:
    assert _policy().public_prefix_rate_limits == {}


def test_a_limit_for_a_prefix_that_is_not_public_is_refused() -> None:
    """Dead configuration that looks like a limit is worse than none."""
    with pytest.raises(ValueError, match="not a public path prefix"):
        _policy(public_prefix_rate_limits={"/api/v1/engage/other/": 100})


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "100"])
def test_a_limit_must_be_a_positive_integer(bad: Any) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        _policy(public_prefix_rate_limits={TRACKING: bad})


# ---------------------------------------------------------------------------- the behaviour


async def test_without_an_override_every_public_prefix_shares_the_anonymous_bucket(
    default_app: FastAPI, redis: Any
) -> None:
    for _ in range(ANONYMOUS_LIMIT):
        assert await _get(default_app, f"{TRACKING}o/token") == 200
    assert await _get(default_app, f"{TRACKING}o/token") == 429
    # The same address on the other public prefix is in the same, spent bucket.
    assert await _get(default_app, f"{UNSUBSCRIBE}token") == 429
    assert [k for k in redis.store if TRACKING in k] == []


async def test_an_opted_in_prefix_is_limited_at_its_own_figure(
    opted_in_app: FastAPI, redis: Any
) -> None:
    """This is the reported failure: the same address, more than `anonymous_rate_limit`
    requests in a minute, all answered (a click redirects instead of answering 429)."""
    for _ in range(TRACKING_LIMIT):
        assert await _get(opted_in_app, f"{TRACKING}c/token") == 200
    assert await _get(opted_in_app, f"{TRACKING}c/token") == 429
    assert [k for k in redis.store if k.startswith("rl:anon:127.0.0.1")] == []


async def test_the_opted_in_bucket_and_the_shared_bucket_do_not_spend_each_other(
    opted_in_app: FastAPI,
) -> None:
    for _ in range(TRACKING_LIMIT):
        assert await _get(opted_in_app, f"{TRACKING}o/token") == 200
    assert await _get(opted_in_app, f"{TRACKING}o/token") == 429
    # Tracking is spent; the unsubscribe prefix still has its whole shared allowance.
    for _ in range(ANONYMOUS_LIMIT):
        assert await _get(opted_in_app, f"{UNSUBSCRIBE}token") == 200
    assert await _get(opted_in_app, f"{UNSUBSCRIBE}token") == 429
    # ... and spending that did not touch the opted-in bucket, which is still spent, not reset.
    assert await _get(opted_in_app, f"{TRACKING}o/token") == 429


async def test_the_opted_in_bucket_is_per_address(opted_in_app: FastAPI) -> None:
    for _ in range(TRACKING_LIMIT):
        assert await _get(opted_in_app, f"{TRACKING}o/token", client_host="198.51.100.1") == 200
    assert await _get(opted_in_app, f"{TRACKING}o/token", client_host="198.51.100.1") == 429
    assert await _get(opted_in_app, f"{TRACKING}o/token", client_host="198.51.100.2") == 200


async def test_the_longest_matching_prefix_decides(redis: Any) -> None:
    clicks = f"{TRACKING}c/"
    app = _app(
        _policy(
            public_path_prefixes=frozenset({TRACKING, clicks, UNSUBSCRIBE}),
            public_prefix_rate_limits={TRACKING: 5, clicks: 2},
        )
    )
    assert [await _get(app, f"{clicks}x") for _ in range(3)] == [200, 200, 429]
    assert [await _get(app, f"{TRACKING}o/x") for _ in range(6)] == [200] * 5 + [429]


async def test_the_bucket_is_chosen_by_where_the_path_routes_not_by_how_it_starts(
    opted_in_app: FastAPI, redis: Any
) -> None:
    """`/t/x/../../campaigns` normalises to an authenticated route: it is not public at all, so it
    is a 401 and never counted in the generous bucket. The mirror image, a path that starts under
    unsubscribe but resolves under tracking, is counted as tracking."""
    assert await _get(opted_in_app, f"{TRACKING}x/../../campaigns") == 401
    assert redis.store == {}

    assert await _get(opted_in_app, f"{UNSUBSCRIBE}x/../../t/o/token") == 200
    assert [k for k in redis.store if k.startswith(f"rl:anon:{TRACKING}:")] != []
