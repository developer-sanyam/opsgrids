"""m10 (round 2) / m5 (round 3): a behavioural regression test, against the REAL route tables
(this service's own dev `routes.json`, and the production `routes.prod.json`), that no
dot-segment or percent-encoded traversal payload ever reaches a service's bare `/status`,
`/metrics` or `/health` — the three endpoints every service exposes with no auth of its own,
precisely because they are meant to be reachable only on the compose `back` network, never
through this gateway (`tests/infra/test_metrics_never_routed.py` is the static half of this
same guarantee).

Driven with a hand-built ASGI scope, not an `httpx` client (see
`libs/platform-core/tests/test_gateway_path_normalization.py`'s own module docstring for why: a
real client normalises a URL string before ever sending it, which would silently fix the exact
thing this test exists to exercise). Percent-encoded payloads are decoded here, once, before
being placed in the scope — that is what a real ASGI server does before an application ever
sees `scope["path"]` (confirmed by `platform_core.gateway._has_internal_segment`'s own
docstring), so this is testing the same boundary `_normalize_path` actually operates at, not an
easier one.

**Round 3's fix over round 2's version.** The original version of this file used
`public_paths=frozenset()` with no prefixes, so every payload hit `authorization.startswith
("Bearer ")` and failed there — before the request ever reached `RouteTable.resolve` or
`_forward`, since `proxy()` resolves the route *before* branching on public-vs-authenticated
(see `gateway.py`). A payload that never reaches routing or forwarding cannot prove either is
safe; it only proves the auth gate runs first. Setting `public_path_prefixes=frozenset({"/api/"})`
here means a request whose **normalised** `full_path` starts with `/api/` (that is what
`gateway.py` matches the prefix against — `full_path.startswith(prefix)`, after `_normalize_path`,
never the raw string) skips the bearer-token check and goes straight on to the rate limit and
`_forward`, so that any payload that does resolve to a real route reaches forwarding instead of
being stopped at auth. Under the real normaliser none of them resolves (see below), so what these
tests actually pin is the 404 — and, under the identity-normaliser mutation, the divergence that
proves the pin is real. `enforce_rate_limit` is stubbed to a no-op
because it talks to Redis, which this test has none of; stubbing it, not disabling the public
branch, is what keeps this test about routing and forwarding rather than about rate limiting.

**Why `test_mutating_the_normalizer_proves_this_file_has_teeth` exists.** Every payload here is
built as `v1/auth/<segments>` on purpose: `/api/v1/auth` is a real prefix in both route tables,
so the *raw*, unnormalised string `/api/v1/auth/../../status` itself starts with it —
`RouteTable.resolve` is a plain `path.startswith(prefix)` (`gateway.py`). With the real
`_normalize_path`, the collapsed path is `/api/status`, which starts with no route in either
table, so `resolve()` returns `None` and the request 404s before forwarding. Replace
`_normalize_path` with the identity function and that same raw string now resolves against the
`/api/v1/auth` route and is forwarded — a directly observable divergence (404 vs. forwarded)
that has nothing to do with what `httpx` itself does to the URL on the wire. Without a test
that demonstrates this divergence, every assertion in this file could pass merely because none
of these payloads happens to survive to `_forward` at all, regardless of whether
`_normalize_path` does its job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableMapping
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
#: percent-decoding (see the module docstring). The last two are round 3's addition: traversal
#: one level deeper than the shallower entries above them, above the point `/api/v1/auth`
#: itself sits, to make sure a defender who only clamped exactly one level up is still caught.
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
    "v1/auth/../../../status",
    "v1/auth/../../../metrics",
]

#: The subset of the payloads above whose *raw, unnormalised* form still starts with a real
#: route prefix (`/api/v1/auth`) — the only ones for which mutating `_normalize_path` to the
#: identity function can possibly change the outcome (see the module docstring). The others
#: (`v1/../status`, `../status`, ...) never touch `/api/v1/auth` at all, raw or normalised, so
#: they would 404 either way and prove nothing about the mutation.
_PREFIX_SENSITIVE_PAYLOADS = [p for p in TRAVERSAL_PAYLOADS if p.startswith("v1/auth/")]


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
    # `proxy()` resolves the route before it looks at auth, so this prefix changes nothing for a
    # payload that 404s. What it does is make a payload that *does* resolve (the identity-
    # normaliser mutation below) run on to the rate limit and `_forward` rather than stop at
    # the bearer-token check — see the module docstring. A frozenset, as `GatewayPolicy` types it.
    policy = GatewayPolicy(
        audience="app", public_paths=frozenset(), public_path_prefixes=frozenset({"/api/"})
    )
    build_gateway(app, settings=cfg, routes=routes, policy=policy)
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return app


async def _raw_get(app: FastAPI, raw_path: str) -> int:
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
    return int(start["status"])


@pytest.fixture(autouse=True)
def _no_redis_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`enforce_rate_limit` (`platform_core/cache.py`) talks to Redis, which this test has
    none of — stubbed to a no-op rather than leaving `public_path_prefixes` empty (which would
    just recreate round 2's bug of never reaching routing at all, see the module docstring)."""

    async def _allow(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("platform_core.gateway.enforce_rate_limit", _allow)


@pytest.fixture(params=[DEV_ROUTES, PROD_ROUTES], ids=["dev-routes", "prod-routes"])
def routes_file(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


@pytest.fixture
async def upstream() -> AsyncIterator[_CapturingUpstream]:
    yield _CapturingUpstream()


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
async def test_traversal_payload_never_reaches_a_bare_status_metrics_or_health(
    routes_file: Path, payload: str, upstream: _CapturingUpstream
) -> None:
    app = _build_app(routes_file, upstream)
    try:
        status = await _raw_get(app, f"/api/{payload}")
    finally:
        await app.state.http.aclose()

    # Unconditional, not gated behind `if status == 200`: every one of these payloads
    # normalises to something outside any real `/api/v1/...` prefix, so none of them should
    # ever reach `_forward` at all — asserted directly, not inferred from the status code.
    for forwarded_path in upstream.calls:
        assert forwarded_path not in ("/status", "/metrics", "/health"), (
            f"payload {payload!r} against {routes_file.name} reached upstream at "
            f"{forwarded_path!r} — a bare, unauthenticated service endpoint"
        )
    assert upstream.calls == [], (
        f"payload {payload!r} against {routes_file.name} reached upstream at all: "
        f"{upstream.calls!r} — every one of these payloads should 404 on route resolution"
    )
    assert status == 404, f"expected a refusal (404) for {payload!r}, got {status}"


@pytest.mark.parametrize("payload", _PREFIX_SENSITIVE_PAYLOADS)
async def test_mutating_the_normalizer_proves_this_file_has_teeth(
    routes_file: Path, payload: str, upstream: _CapturingUpstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-check (round 3): with `_normalize_path` replaced by the identity function, the raw,
    un-collapsed payload still starts with the real `/api/v1/auth` prefix (`RouteTable.resolve`
    is a plain `str.startswith`), so it now resolves and is forwarded instead of 404ing — proof
    that the 404s asserted above depend on `_normalize_path` actually doing its job, not on
    these payloads being unroutable no matter what."""
    monkeypatch.setattr("platform_core.gateway._normalize_path", lambda raw: raw)
    app = _build_app(routes_file, upstream)
    try:
        status = await _raw_get(app, f"/api/{payload}")
    finally:
        await app.state.http.aclose()

    assert status != 404, (
        f"payload {payload!r} against {routes_file.name} still 404'd with an identity "
        "normaliser — this test is not actually sensitive to _normalize_path being broken"
    )
    assert upstream.calls, (
        f"payload {payload!r} against {routes_file.name} never reached upstream even with a "
        "broken normaliser — the divergence this test exists to prove did not happen"
    )
