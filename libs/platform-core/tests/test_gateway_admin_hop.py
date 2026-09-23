"""BLOCKER 2 of the 2026-09-24 security review: admin-gateway signs every request it proxies
with a hop signature (`platform_core.internal_auth.sign_admin_hop`) under a key that is not
the general internal-service-token secret; any gateway that does not carry that key configured
(app-gateway) never writes one, and a client-supplied copy of the header is stripped before
either gateway forwards anything, so it can never survive to look like the real thing.

This is defence in depth on top of the route table's own `blocked` entry — see
`tests/infra/test_route_table_parity.py` for that half — for the case where the request
somehow reaches auth by a path other than through admin-gateway.
"""

from __future__ import annotations

import json
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
from platform_core.gateway import GatewayPolicy, Route, RouteTable, build_gateway
from platform_core.internal_auth import ADMIN_HOP_HEADER, verify_admin_hop

UPSTREAM = "http://upstream-service:9999"
OPERATOR_LOGIN = "/api/v1/auth/operator/login"
HOP_KEY = "admin-gateway-hop-key-of-32-bytes!!"


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
        self.calls: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "headers": dict(request.headers)})
        return httpx.Response(200, json={"ok": True})

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def upstream() -> _EchoUpstream:
    return _EchoUpstream()


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


def _build_app(upstream: _EchoUpstream, hop_key: str | None) -> FastAPI:
    cfg = _Settings(admin_gateway_hop_key=hop_key)
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    policy = GatewayPolicy(audience="operator", public_paths=frozenset({OPERATOR_LOGIN}))
    routes = RouteTable([Route("/api/v1/auth", UPSTREAM)])
    build_gateway(application, settings=cfg, routes=routes, policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest_asyncio.fixture
async def signing_client(upstream: _EchoUpstream, redis: Any) -> AsyncIterator[AsyncClient]:
    """Stands in for admin-gateway: configured with the hop key, so it signs."""
    app = _build_app(upstream, HOP_KEY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


@pytest_asyncio.fixture
async def non_signing_client(upstream: _EchoUpstream, redis: Any) -> AsyncIterator[AsyncClient]:
    """Stands in for app-gateway: no hop key configured, so it never signs."""
    app = _build_app(upstream, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


class TestTheSigningGateway:
    async def test_it_writes_a_hop_header_that_verifies_under_its_own_key(
        self, signing_client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await signing_client.post(OPERATOR_LOGIN, content=json.dumps({}))
        assert response.status_code == 200
        header = upstream.last["headers"].get(ADMIN_HOP_HEADER)
        assert header is not None
        assert verify_admin_hop(header, "POST", OPERATOR_LOGIN, key=HOP_KEY)

    async def test_a_client_supplied_hop_header_is_overwritten_not_forwarded(
        self, signing_client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """A caller cannot simply hand-write the header and have it pass through unchanged —
        the gateway's own signature always replaces whatever arrived on the inbound request."""
        response = await signing_client.post(
            OPERATOR_LOGIN,
            content=json.dumps({}),
            headers={ADMIN_HOP_HEADER: "forged.forged"},
        )
        assert response.status_code == 200
        header = upstream.last["headers"].get(ADMIN_HOP_HEADER)
        assert header != "forged.forged"
        assert verify_admin_hop(header, "POST", OPERATOR_LOGIN, key=HOP_KEY)


class TestTheNonSigningGateway:
    async def test_it_never_writes_a_hop_header(
        self, non_signing_client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await non_signing_client.post(OPERATOR_LOGIN, content=json.dumps({}))
        assert response.status_code == 200
        assert ADMIN_HOP_HEADER not in upstream.last["headers"]

    async def test_a_client_supplied_hop_header_is_stripped_not_forwarded(
        self, non_signing_client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """Without a key to sign with there is nothing to replace the client's value with —
        this asserts it is dropped outright rather than passed through untouched."""
        response = await non_signing_client.post(
            OPERATOR_LOGIN,
            content=json.dumps({}),
            headers={ADMIN_HOP_HEADER: "forged.forged"},
        )
        assert response.status_code == 200
        assert ADMIN_HOP_HEADER not in upstream.last["headers"]
