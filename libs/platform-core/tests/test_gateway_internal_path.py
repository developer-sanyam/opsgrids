"""The gateway refuses any request whose normalised path names an `/internal/` segment.

Every service mounts its service-to-service router at `/internal/v1` — never under `/api`,
which is the only prefix this gateway ever forwards — so no legitimate request should ever
match this. This is defence in depth for the request that was crafted to reach one anyway: a
route-table typo, or a path built to walk out of a public prefix and into an internal one.

Three shapes of the same attempt are covered, because each defeats a different naive check:

* the literal segment, unencoded;
* the percent-encoded form (`%2Finternal%2F`) — decoded by the ASGI server before this ever
  runs, exactly as it would be in production (`httpx.URL.path` decodes it the same way
  `uvicorn` does building `scope["path"]`, which is what makes this test representative
  rather than a fact about the test harness);
* a `..` segment that only resolves into `/internal/...` once normalised.

`storefront`'s own `/api/v1/storefront/internal/tls-authorize` is not exercised here: Caddy
calls it directly on `storefront:8000` and 404s it from the internet before app-gateway is ever
in the picture (see `infrastructure/edge/caddy/Caddyfile`), so this gateway-level refusal is an
additional layer under a route that never reaches it, not a replacement for one that does.
"""

from __future__ import annotations

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
    GatewayPolicy,
    Route,
    RouteTable,
    _has_internal_segment,
    build_gateway,
)

PUBLIC = "/api/v1/auth/login"
PRIVATE = "/api/v1/employees"
UPSTREAM = "http://upstream-service:9999"


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


class _EchoUpstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})


def _routes() -> RouteTable:
    return RouteTable(
        [
            Route("/api/v1/auth", UPSTREAM),
            Route("/api/v1/employees", UPSTREAM),
        ]
    )


@pytest.fixture
def upstream() -> _EchoUpstream:
    return _EchoUpstream()


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def app(upstream: _EchoUpstream, redis: Any, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from platform_core import gateway as gateway_module

    def _claims(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "sub": "00000000-0000-0000-0000-000000000001",
            "act_org": "00000000-0000-0000-0000-000000000002",
            "pv": 0,
            "ent": [],
            "roles": [],
            "loc": [],
            "amr": ["pwd"],
        }

    monkeypatch.setattr(gateway_module, "decode_token", _claims)

    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    policy = GatewayPolicy(audience="app", public_paths=frozenset({PUBLIC}))
    build_gateway(application, settings=cfg, routes=_routes(), policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer stubbed"}


class TestTheHelperNormalisesFirst:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/employees/internal/secret",
            "/api/v1/internal/secret",
            "/api/internal",
            "/api/v1/employees/x/../internal/secret",
            "/api/v1/employees/./internal/y",
        ],
    )
    def test_a_literal_or_dot_segment_internal_path_is_flagged(self, path: str) -> None:
        assert _has_internal_segment(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/employees",
            "/api/v1/employees/international",
            "/api/v1/internal-tools/secret",
            "/api/v1/employees/x/../employees/y",
        ],
    )
    def test_a_lookalike_or_ordinary_path_is_not_flagged(self, path: str) -> None:
        assert _has_internal_segment(path) is False


class TestOverTheWire:
    async def test_a_literal_internal_segment_is_refused_as_404(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await client.get(f"{PRIVATE}/internal/secret", headers=auth())

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert not upstream.calls

    async def test_an_unauthenticated_internal_segment_is_refused_before_anything_else(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """No bearer token, no route — the refusal happens before authentication is even
        asked for, exactly like a route that does not exist."""
        response = await client.get(f"{PRIVATE}/internal/secret")

        assert response.status_code == 404
        assert not upstream.calls

    async def test_a_percent_encoded_internal_segment_is_refused(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """`%2Finternal%2F` is decoded by the ASGI server before this ever runs — this proves
        that decoding, not a special case for the encoded form."""
        response = await client.get("/api/v1/employees%2Finternal%2Fsecret", headers=auth())

        assert response.status_code == 404
        assert not upstream.calls

    async def test_a_dot_segment_walk_into_internal_is_refused(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await client.get("/api/v1/employees/x/../../internal/secret", headers=auth())

        assert response.status_code == 404
        assert not upstream.calls

    async def test_an_ordinary_request_is_unaffected(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await client.get(PRIVATE, headers=auth())

        assert response.status_code == 200
        assert upstream.calls == [PRIVATE]

    async def test_a_path_that_merely_contains_the_word_is_unaffected(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """`international` is not `internal` — a segment match, not a substring match."""
        response = await client.get(f"{PRIVATE}/international", headers=auth())

        assert response.status_code == 200
        assert upstream.calls
