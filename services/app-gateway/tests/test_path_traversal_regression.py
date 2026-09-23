"""m10 (round 2): a behavioural regression test, against the REAL route tables (this service's
own dev `routes.json`, and the production `routes.prod.json`), that no dot-segment or
percent-encoded traversal payload ever reaches a service's bare `/status`, `/metrics` or
`/health` — the three endpoints every service exposes with no auth of its own, precisely
because they are meant to be reachable only on the compose `back` network, never through this
gateway (`tests/infra/test_metrics_never_routed.py` is the static half of this same guarantee).

Driven with a hand-built ASGI scope, not an `httpx` client (see
`libs/platform-core/tests/test_gateway_path_normalization.py`'s own module docstring for why: a
real client normalises a URL string before ever sending it, which would silently fix the exact
thing this test exists to exercise). Percent-encoded payloads are decoded here, once, before
being placed in the scope — that is what a real ASGI server does before an application ever
sees `scope["path"]` (confirmed by `platform_core.gateway._has_internal_segment`'s own
docstring), so this is testing the same boundary `_normalize_path` actually operates at, not an
easier one.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI

from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import GatewayPolicy, RouteTable, build_gateway

SERVICE_DIR = Path(__file__).resolve().parent.parent
DEV_ROUTES = SERVICE_DIR / "routes.json"
PROD_ROUTES = Path(__file__).resolve().parents[3] / "infrastructure/edge/routes.prod.json"

#: What a client dressing a traversal payload up as an ordinary API call might send — decoded
#: here to mirror what scope["path"] actually contains once a real ASGI server has done its own
#: percent-decoding (see the module docstring).
TRAVERSAL_PAYLOADS = [
    "v1/auth/../../status",
    "v1/auth/../../metrics",
    "v1/auth/../../health",
    "v1/../status",
    "v1/../metrics",
    "v1/../health",
    unquote("v1%2Fauth%2F..%2F..%2Fstatus"),
    unquote("v1%2Fauth%2F..%2F..%2Fmetrics"),
    "v1//../../status",
    "v1/x/../y/../../../status",
    "../status",
    "../metrics",
    "../health",
]


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class _CapturingUpstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})


def _build_app(routes_file: Path, upstream: _CapturingUpstream) -> FastAPI:
    cfg = _Settings()
    app = create_app(cfg, title="probe-gateway", database=False, redis=False)
    routes = RouteTable.from_file(routes_file)
    policy = GatewayPolicy(audience="app", public_paths=frozenset())
    build_gateway(app, settings=cfg, routes=routes, policy=policy)
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return app


async def _raw_get(app: FastAPI, raw_path: str) -> tuple[int, list[str]]:
    """Drive `app` with `raw_path` exactly as a decoded `scope["path"]` would carry it — see
    the module docstring for why this, and not an `httpx` client, is what actually exercises
    the defence."""
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
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
    return start["status"], []


@pytest.fixture(params=[DEV_ROUTES, PROD_ROUTES], ids=["dev-routes", "prod-routes"])
def routes_file(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
async def test_traversal_payload_never_reaches_a_bare_status_metrics_or_health(
    routes_file: Path, payload: str
) -> None:
    upstream = _CapturingUpstream()
    app = _build_app(routes_file, upstream)
    try:
        status, _ = await _raw_get(app, f"/api/{payload}")
    finally:
        await app.state.http.aclose()

    # Either the gateway refused it outright (no route matches a bare /status, /metrics or
    # /health — every route prefix in both tables starts with /api/v1/<something>), or — the
    # stronger, belt-and-braces assertion — if it somehow forwarded anything at all, the
    # forwarded path was never exactly one of the three bare endpoints.
    if status == 200:
        assert upstream.calls, "reported success but never actually called upstream"
        for forwarded_path in upstream.calls:
            assert forwarded_path not in ("/status", "/metrics", "/health"), (
                f"payload {payload!r} against {routes_file.name} reached upstream at "
                f"{forwarded_path!r} — a bare, unauthenticated service endpoint"
            )
    else:
        assert status == 404, f"expected a refusal (404) for {payload!r}, got {status}"
        assert upstream.calls == [], f"payload {payload!r} reached upstream despite a {status}"
