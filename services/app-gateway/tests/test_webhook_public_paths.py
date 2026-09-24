"""S07 end to end: the real policy and the real route tables (dev and production), unauthenticated.

`infrastructure/edge/routes.prod.json` routed `/api/v1/webhooks/razorpay` and `.../whatsapp`
through this gateway while the gateway's policy did not know either was public, so every provider
delivery met `401 Missing bearer token` and neither Razorpay mandates nor inbound WhatsApp ever
reached the service that verifies the signature. Nothing in the suite loaded the production table
against the real policy, which is why it went unnoticed; these tests do, and they check both
directions of the fix — the two deliveries get through, and nothing else does.

Requests are unauthenticated throughout and the gateway's real code runs (routing, the public
branch, the rate limit against the in-memory `FakeRedis`, the body cap, forwarding). Only the
upstream is a stub, so "reaches the forwarding step" is observed as "the stub was called".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.config import Settings, settings
from platform_core.app import create_app
from platform_core.gateway import RouteTable, build_gateway
from tests.conftest import FakeRedis

SERVICE_DIR = Path(__file__).resolve().parent.parent
DEV_ROUTES = SERVICE_DIR / "routes.json"
PROD_ROUTES = Path(__file__).resolve().parents[3] / "infrastructure/edge/routes.prod.json"

RAZORPAY = "/api/v1/webhooks/razorpay"
WHATSAPP = "/api/v1/webhooks/whatsapp"

SIGNED = b'{ "event" : "payment.captured",\n "payload":{} }'


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json={"ok": True})

    @property
    def paths(self) -> list[str]:
        return [call.url.path for call in self.calls]


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


@pytest.fixture(params=[DEV_ROUTES, PROD_ROUTES], ids=["dev-routes", "prod-routes"])
def routes_file(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


@pytest_asyncio.fixture
async def app(routes_file: Path, upstream: _Upstream, redis: FakeRedis) -> AsyncIterator[FastAPI]:
    """This gateway's own policy, over the route table under test, with only the upstream faked."""
    from app.main import policy

    cfg = settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    build_gateway(
        application, settings=cfg, routes=RouteTable.from_file(routes_file), policy=policy
    )
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    yield application
    await application.state.http.aclose()


async def call(
    app: FastAPI,
    method: str,
    raw_path: str,
    *,
    body: bytes = b"",
    query: bytes = b"",
    headers: dict[str, str] | None = None,
) -> int:
    """Drive `app` with `raw_path` as a decoded ASGI `scope["path"]` carries it.

    Not an `httpx` client: a client normalises `..` before sending, which would hide exactly what
    the traversal tests exist to exercise (see `tests/test_path_traversal_regression.py`).
    """
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body:
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": method,
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": query,
        "headers": raw_headers,
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


# ---------------------------------------------------------------------- the two deliveries


class TestProviderDeliveriesGetThrough:
    @pytest.mark.parametrize("path", [RAZORPAY, WHATSAPP])
    async def test_an_unauthenticated_post_reaches_the_forwarding_step(
        self, app: FastAPI, upstream: _Upstream, path: str
    ) -> None:
        """The whole of S07. With the path missing from the policy this was a 401 and the
        upstream stub was never called."""
        status = await call(app, "POST", path, body=SIGNED)

        assert status == 200
        assert upstream.paths == [path]
        assert upstream.calls[0].content == SIGNED, "the signed bytes are forwarded untouched"

    async def test_each_delivery_is_routed_to_the_service_that_verifies_it(
        self, app: FastAPI, upstream: _Upstream, routes_file: Path
    ) -> None:
        await call(app, "POST", RAZORPAY, body=SIGNED)
        await call(app, "POST", WHATSAPP, body=SIGNED)
        hosts = [call_.url.host for call_ in upstream.calls]
        ports = [call_.url.port for call_ in upstream.calls]
        if routes_file == PROD_ROUTES:
            assert hosts == ["payment", "notification"]
        else:
            assert (hosts, ports) == (["localhost", "localhost"], [8163, 8151])

    async def test_metas_handshake_get_is_public_on_the_whatsapp_path(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        query = b"hub.mode=subscribe&hub.verify_token=t&hub.challenge=42"
        status = await call(app, "GET", WHATSAPP, query=query)

        assert status == 200
        assert upstream.calls[0].url.params["hub.challenge"] == "42"

    async def test_razorpay_is_post_only(self, app: FastAPI, upstream: _Upstream) -> None:
        for method in ("GET", "PUT", "DELETE", "PATCH"):
            assert await call(app, method, RAZORPAY) == 401, method
        assert upstream.calls == []

    async def test_a_provider_does_not_spend_the_sign_in_allowance(
        self, app: FastAPI, upstream: _Upstream, redis: FakeRedis
    ) -> None:
        await call(app, "POST", RAZORPAY, body=SIGNED)
        # The per-address bucket and the path's own ceiling — and nothing in the anonymous ones.
        keys = sorted(redis.store)
        assert len(keys) == 2 and all(k.startswith(f"rl:hook:{RAZORPAY}:") for k in keys), keys
        assert not [k for k in keys if k.startswith("rl:anon")]


# ------------------------------------------------------------------ nothing else is let through


class TestNothingElseIsPublic:
    async def test_every_other_route_in_the_table_still_demands_a_token(
        self, app: FastAPI, upstream: _Upstream, routes_file: Path
    ) -> None:
        """For **every** prefix in the table, unauthenticated: 404 if blocked, forwarded only if
        the policy names it (exactly, for that method) as public, and 401 otherwise. A route the
        fix accidentally made public — a prefix rather than an exact path, a method too many —
        shows up here as a request that reached the stub."""
        from app.main import policy

        for route in RouteTable.from_file(routes_file):
            path = route.prefix
            if route.blocked:
                expected = 404
            elif path in policy.public_paths or path in policy.webhook_paths:
                expected = 200 if "GET" in policy.webhook_paths.get(path, {"GET"}) else 401
            elif any(path.startswith(p) for p in policy.public_path_prefixes):
                expected = 200
            else:
                expected = 401
            before = len(upstream.calls)
            assert await call(app, "GET", path) == expected, path
            assert (len(upstream.calls) - before == 1) == (expected == 200), path

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/employees",
            "/api/v1/customers",
            "/api/v1/billing/invoices",
            "/api/v1/webhooks",
            "/api/v1/webhooks/razorpay/extra",
            "/api/v1/webhooks/whatsapp/extra",
            "/api/v1/webhooks/whatsappx",
            "/api/v1/webhooks/Razorpay",
            "/api/v1/webhooks/stripe",
        ],
    )
    async def test_a_path_that_is_not_a_declared_webhook_is_refused(
        self, app: FastAPI, upstream: _Upstream, path: str
    ) -> None:
        status = await call(app, "POST", path, body=SIGNED)
        assert status in (401, 404), (path, status)
        assert upstream.calls == []


class TestTraversalAroundTheWebhookPaths:
    """Decisions are taken on the *normalised* path, the same one that is forwarded, so a walk
    that starts at a public path is judged by where it ends."""

    @pytest.mark.parametrize(
        "payload",
        [
            "v1/webhooks/whatsapp/../../employees",
            "v1/webhooks/razorpay/../../employees",
            "v1/webhooks/whatsapp/../../customers",
            unquote("v1/webhooks/whatsapp/%2e%2e/%2e%2e/employees"),
            unquote("v1/webhooks/razorpay%2F..%2F..%2Femployees"),
            "v1//webhooks//whatsapp//..//..//employees",
            "v1/webhooks/whatsapp/./../../employees",
        ],
    )
    async def test_a_walk_off_a_webhook_onto_a_private_route_is_refused(
        self, app: FastAPI, upstream: _Upstream, payload: str
    ) -> None:
        """Public *as text*, private as a route. Deciding on the raw string and forwarding the
        normalised one is exactly how this would slip through."""
        for method in ("POST", "GET"):
            assert await call(app, method, f"/api/{payload}", body=SIGNED) == 401, payload
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            # One `..` from the whatsapp path lands on `/api/v1/webhooks/auth/login`: no route.
            "v1/webhooks/whatsapp/../auth/login",
            "v1/webhooks/razorpay/../auth/login",
            # Literal, not a `..`: what a doubly-encoded `%252e%252e` decodes to once.
            "v1/webhooks/whatsapp/%2e%2e/%2e%2e/employees",
            "v1/webhooks/razorpay/..;/employees",
            "v1/webhooks/whatsapp/..%00/employees",
        ],
    )
    async def test_a_segment_that_only_looks_like_a_dot_dot_is_not_public_either(
        self, app: FastAPI, upstream: _Upstream, payload: str
    ) -> None:
        status = await call(app, "POST", f"/api/{payload}", body=SIGNED)
        assert status in (401, 404), (payload, status)
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            "v1/webhooks/razorpay/../../auth/operator/login",
            "v1/webhooks/whatsapp/../../auth/operator/mfa/verify",
        ],
    )
    async def test_a_walk_onto_the_blocked_operator_surface_is_a_404(
        self, app: FastAPI, upstream: _Upstream, payload: str
    ) -> None:
        assert await call(app, "POST", f"/api/{payload}", body=SIGNED) == 404
        assert upstream.calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            "v1/webhooks/whatsapp/../../../internal/v1/memberships",
            "v1/webhooks/razorpay/../../internal/x",
        ],
    )
    async def test_a_walk_onto_an_internal_path_is_a_404(
        self, app: FastAPI, upstream: _Upstream, payload: str
    ) -> None:
        assert await call(app, "POST", f"/api/{payload}", body=SIGNED) == 404
        assert upstream.calls == []

    async def test_a_walk_onto_sign_in_is_sign_in_and_not_a_webhook(
        self, app: FastAPI, upstream: _Upstream, redis: FakeRedis
    ) -> None:
        """`/api/v1/webhooks/whatsapp/../../auth/login` resolves to `/api/v1/auth/login`, which is
        public *in its own right* — so it is let through as sign-in, forwarded as sign-in, and
        counted in the anonymous bucket. What it must never be is treated as the webhook: not
        the webhook's bucket, and never forwarded under the webhook's path."""
        status = await call(app, "POST", "/api/v1/webhooks/whatsapp/../../auth/login", body=b"{}")

        assert status == 200
        assert upstream.paths == ["/api/v1/auth/login"]
        assert [k for k in redis.store if k.startswith("rl:anon:")]
        assert not [k for k in redis.store if k.startswith("rl:hook:")]


# ---------------------------------------------------------------------------- body size


class TestTheRealBodyCaps:
    """Both routes carry a cap of their own in *both* tables, sized to the real payload, and the
    gateway enforces it before anything is buffered or forwarded."""

    async def test_razorpay_is_capped_at_256_kib(self, app: FastAPI, upstream: _Upstream) -> None:
        cap = 256 * 1024
        assert await call(app, "POST", RAZORPAY, body=b"x" * cap) == 200
        assert await call(app, "POST", RAZORPAY, body=b"x" * (cap + 1)) == 413
        assert len(upstream.calls) == 1, "the oversized body must never reach the service"

    async def test_whatsapp_is_capped_at_the_3_mib_meta_documents(
        self, app: FastAPI, upstream: _Upstream
    ) -> None:
        cap = 3 * 1024 * 1024
        assert await call(app, "POST", WHATSAPP, body=b"x" * cap) == 200
        assert await call(app, "POST", WHATSAPP, body=b"x" * (cap + 1)) == 413
        assert len(upstream.calls) == 1


# ------------------------------------------------------------------- how it is configured


class TestThePolicyThisGatewayBuilds:
    def test_the_webhook_paths_are_exact_and_carry_only_the_methods_a_provider_uses(self) -> None:
        from app.main import WEBHOOK_PUBLIC_PATHS, policy

        assert dict(WEBHOOK_PUBLIC_PATHS) == {
            RAZORPAY: frozenset({"POST"}),
            WHATSAPP: frozenset({"GET", "POST"}),
        }
        assert policy.webhook_paths is WEBHOOK_PUBLIC_PATHS

    def test_they_are_not_in_public_paths_because_that_would_share_the_anonymous_bucket(
        self,
    ) -> None:
        from app.main import policy

        assert not (set(policy.webhook_paths) & policy.public_paths)

    def test_the_anonymous_surface_has_a_shared_ceiling_above_one_callers_allowance(self) -> None:
        from app.main import policy

        assert policy.anonymous_global_rate_limit > policy.anonymous_rate_limit > 0
        assert policy.webhook_global_rate_limit > policy.webhook_rate_limit > 0

    def test_no_proxy_is_trusted_unless_the_deployment_names_one(self) -> None:
        """The gateway as imported here has no `TRUSTED_PROXY_CIDRS`, so it believes no header:
        development and tests connect directly, and the safe default must be the default."""
        from app.main import policy

        assert policy.trusted_proxies == ()


_LIMIT_SETTINGS = {
    "ANONYMOUS_RATE_LIMIT_PER_MINUTE": "anonymous_rate_limit_per_minute",
    "ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE": "anonymous_global_rate_limit_per_minute",
    "WEBHOOK_RATE_LIMIT_PER_MINUTE": "webhook_rate_limit_per_minute",
    "WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE": "webhook_global_rate_limit_per_minute",
}


class TestTheLimitsCanBeTunedFromTheEnvironment:
    def test_compose_passes_them_empty_and_empty_means_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`compose.prod.yml` hands the container `KEY=` for every knob the box's `.env` does not
        set. That must read as "not configured", not as a zero limit that refuses everything (or
        a parse error that stops the gateway booting)."""
        defaults = {name: getattr(Settings(), name) for name in _LIMIT_SETTINGS.values()}
        for key in _LIMIT_SETTINGS:
            monkeypatch.setenv(key, "")
        assert {name: getattr(Settings(), name) for name in _LIMIT_SETTINGS.values()} == defaults
        assert defaults["anonymous_global_rate_limit_per_minute"] > 0

    def test_a_value_in_the_environment_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for i, key in enumerate(_LIMIT_SETTINGS, start=1):
            monkeypatch.setenv(key, str(i * 111))
        cfg = Settings()
        assert [getattr(cfg, name) for name in _LIMIT_SETTINGS.values()] == [111, 222, 333, 444]


class TestTrustedProxySettings:
    def test_a_comma_separated_list_is_read_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "172.16.0.0/12, 10.0.0.0/8 ,fd00::/8")
        assert Settings().trusted_proxy_cidrs == ["172.16.0.0/12", "10.0.0.0/8", "fd00::/8"]

    def test_unset_and_empty_both_mean_trust_nobody(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
        assert Settings().trusted_proxy_cidrs == []
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "")
        assert Settings().trusted_proxy_cidrs == []

    @pytest.mark.parametrize("value", ["0.0.0.0/0", "172.16.0.0/12,::/0", "caddy", "172.16.0.0/99"])
    def test_a_catch_all_or_malformed_entry_stops_the_gateway_booting(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", value)
        with pytest.raises(ValueError, match="trusted proxy"):
            Settings()
