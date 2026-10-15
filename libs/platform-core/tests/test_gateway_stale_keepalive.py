"""A pooled upstream connection the service has just closed must not become a 500.

2026-10-15 customer retest: right after sign-in on a phone, `GET /customers/owners` and
`GET /notifications/me` each answered a 500 once, then worked. Both are plain reads in different
services; what they share is the gateway's pooled httpx client. Every service runs uvicorn with its
default 5-second keep-alive, and httpx's default keep-alive expiry is also 5 seconds, so after a
pause (the sign-in screen) the gateway could reuse a connection at the moment uvicorn closed it.
httpx raises `RemoteProtocolError` ("Server disconnected without sending a response"), which
nothing caught. These pin the fix: the pool gives connections up well before the upstream does,
an idempotent request that met a dropped connection is sent once more, and anything that still
fails is a 503 to retry, never a 500.
"""

from __future__ import annotations

import httpx
import pytest

from platform_core.gateway import GatewayPolicy, Route, _send_riding_out_restarts, http_client

ROUTE = Route(prefix="/api/v1/customers", upstream="http://customer:8000")
POLICY = GatewayPolicy(audience="app", public_paths=frozenset())


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _dropping_first(calls: list[str]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"ok": True})

    return handler


async def test_a_read_that_met_a_dropped_keepalive_connection_is_sent_again() -> None:
    calls: list[str] = []
    async with _client(_dropping_first(calls)) as client:
        request = client.build_request("GET", "http://customer:8000/api/v1/customers/owners")
        response = await _send_riding_out_restarts(client, request, ROUTE, POLICY)
    assert response.status_code == 200
    assert calls == ["GET", "GET"]


async def test_a_write_is_never_sent_twice() -> None:
    calls: list[str] = []
    async with _client(_dropping_first(calls)) as client:
        request = client.build_request("POST", "http://customer:8000/api/v1/customers", json={})
        with pytest.raises(httpx.RemoteProtocolError):
            await _send_riding_out_restarts(client, request, ROUTE, POLICY)
    assert calls == ["POST"]


async def test_a_read_is_retried_once_only() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    async with _client(handler) as client:
        request = client.build_request("GET", "http://customer:8000/api/v1/customers/owners")
        with pytest.raises(httpx.RemoteProtocolError):
            await _send_riding_out_restarts(client, request, ROUTE, POLICY)
    assert calls == ["GET", "GET"]


def test_the_pool_lets_a_connection_go_before_the_upstream_closes_it() -> None:
    client = http_client(POLICY)
    pool = client._transport._pool  # type: ignore[attr-defined]
    # uvicorn's default `--timeout-keep-alive` is 5 s; the pool must expire idle connections first.
    assert pool._keepalive_expiry is not None
    assert pool._keepalive_expiry < 5.0
