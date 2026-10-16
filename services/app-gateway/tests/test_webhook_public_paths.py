"""Meta's WhatsApp callback through the real policy and both real route tables, unauthenticated.

`infrastructure/edge/routes.prod.json` routed `/api/v1/webhooks/whatsapp` to notification while
the gateway's policy only knew a root-path `/webhooks/whatsapp` (which this proxy, serving only
`/api/...`, can never match). Every delivery and every subscription handshake met `401 Missing
bearer token`, so inbound WhatsApp could not work in production. Nothing loaded the production
table against the real policy, which is how it went unnoticed; these tests do, and they check both
directions of the fix: the handshake and the deliveries get through, and nothing else does.

Requests are unauthenticated throughout and the gateway's real code runs (routing, the public
branch, the rate limit against the in-memory `FakeRedis`, the body cap, forwarding). Only the
upstream is a stub, so "reaches the forwarding step" is observed as "the stub was called".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from platform_core.app import create_app
from platform_core.gateway import DEFAULT_MAX_BODY_BYTES, SPOOFABLE, RouteTable, build_gateway
from tests.conftest import EchoUpstream, FakeRedis

SERVICE_DIR = Path(__file__).resolve().parent.parent
DEV_ROUTES = SERVICE_DIR / "routes.json"
PROD_ROUTES = Path(__file__).resolve().parents[3] / "infrastructure/edge/routes.prod.json"

WHATSAPP = "/api/v1/webhooks/whatsapp"
RAZORPAY = "/api/v1/webhooks/razorpay"
RAZORPAY_COLLECTIONS = "/api/v1/webhooks/razorpay/collections"
#: The provider-neutral path of the same tenant webhook (ADR-0033; Cashfree merchants register it).
COLLECTIONS = "/api/v1/webhooks/collections"
#: Meta documents payloads up to 3 MB; the route says so in both tables.
WHATSAPP_BODY_CAP = 3 * 1024 * 1024

SIGNED = b'{ "object" : "whatsapp_business_account",\n "entry":[] }'
SIGNATURE = "sha256=" + "ab" * 32


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, text="1158201", headers={"content-type": "text/plain"})

    @property
    def paths(self) -> list[str]:
        return [call.url.path for call in self.calls]


@pytest.fixture
def probe_upstream() -> _Upstream:
    return _Upstream()


@pytest.fixture(params=[DEV_ROUTES, PROD_ROUTES], ids=["dev-routes", "prod-routes"])
def routes_file(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


@pytest_asyncio.fixture
async def app(
    routes_file: Path, probe_upstream: _Upstream, redis: FakeRedis
) -> AsyncIterator[FastAPI]:
    """This gateway's real policy, over the route table under test, with only the upstream faked.

    `trust_forwarded_for` as `app.main` sets it: behind Caddy the caller is in `X-Forwarded-For`.
    """
    from app.main import policy

    cfg = settings()
    application = create_app(
        cfg, title="probe-gateway", database=False, redis=False, trust_forwarded_for=True
    )
    build_gateway(
        application, settings=cfg, routes=RouteTable.from_file(routes_file), policy=policy
    )
    application.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(probe_upstream.handler)
    )
    yield application
    await application.state.http.aclose()


@pytest_asyncio.fixture
async def http(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


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


# ------------------------------------------------------------------ Meta gets through


class TestMetaGetsThrough:
    async def test_the_subscription_handshake_reaches_the_service_with_no_token(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        """Meta's GET. With the path missing from the policy this was `401 Missing bearer token`
        and the upstream stub was never called."""
        response = await http.get(
            WHATSAPP,
            params={"hub.mode": "subscribe", "hub.verify_token": "t", "hub.challenge": "1158201"},
        )

        assert response.status_code == 200
        assert response.text == "1158201", "the service's answer goes back to Meta unchanged"
        assert probe_upstream.paths == [WHATSAPP]
        assert probe_upstream.calls[0].url.params["hub.challenge"] == "1158201"
        assert probe_upstream.calls[0].url.params["hub.verify_token"] == "t"

    async def test_a_signed_delivery_reaches_the_service_byte_for_byte(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        """The signature is over these exact bytes, so nothing may re-encode them, and the header
        that carries it has to make the trip."""
        response = await http.post(
            WHATSAPP,
            content=SIGNED,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": SIGNATURE},
        )

        assert response.status_code == 200
        assert probe_upstream.paths == [WHATSAPP]
        forwarded = probe_upstream.calls[0]
        assert forwarded.content == SIGNED
        assert forwarded.headers["x-hub-signature-256"] == SIGNATURE
        assert forwarded.headers["content-type"] == "application/json"

    async def test_the_delivery_goes_to_notification(
        self, http: AsyncClient, probe_upstream: _Upstream, routes_file: Path
    ) -> None:
        await http.post(WHATSAPP, content=SIGNED)

        upstream = probe_upstream.calls[0].url
        if routes_file == PROD_ROUTES:
            assert (upstream.host, upstream.port) == ("notification", 8000)
        else:
            assert (upstream.host, upstream.port) == ("localhost", 8151)

    async def test_the_real_app_serves_it_too(
        self, client: AsyncClient, upstream: EchoUpstream
    ) -> None:
        """`app.main`'s own assembly (the policy it builds, its middleware), not a probe."""
        response = await client.get(WHATSAPP, params={"hub.mode": "subscribe"})

        assert response.status_code == 200
        assert upstream.last["path"] == WHATSAPP


# --------------------------------------------------------------- and nothing else does


class TestNothingElseIsPublic:
    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def test_any_other_method_on_the_path_is_refused(
        self, app: FastAPI, probe_upstream: _Upstream, method: str
    ) -> None:
        assert await call(app, method, WHATSAPP, body=SIGNED) == 401
        assert probe_upstream.calls == []

    async def test_razorpays_webhook_is_still_refused(
        self, app: FastAPI, probe_upstream: _Upstream, routes_file: Path
    ) -> None:
        """Routed in production, deliberately not public: the owner decides that separately."""
        expected = 401 if routes_file == PROD_ROUTES else 404
        assert await call(app, "POST", RAZORPAY, body=SIGNED) == expected
        assert probe_upstream.calls == []

    @pytest.mark.parametrize(
        "path",
        [
            f"{WHATSAPP}/extra",
            f"{WHATSAPP}x",
            f"{WHATSAPP}/../razorpay",
            f"{WHATSAPP}/../../employees",
            "/api/v1/webhooks",
            "/api/v1/webhooks/other",
        ],
    )
    @pytest.mark.parametrize("method", ["GET", "POST"])
    async def test_every_other_path_under_webhooks_still_needs_a_token(
        self, app: FastAPI, probe_upstream: _Upstream, path: str, method: str
    ) -> None:
        """Exact path, judged after normalisation: a sub-path, a sibling and a `..` walk are not
        the callback. 401 where a route matches, 404 where none does; never forwarded."""
        assert await call(app, method, path, body=b"") in (401, 404), path
        assert probe_upstream.calls == [], path

    async def test_a_traversal_that_lands_on_the_path_is_judged_as_the_path(
        self, app: FastAPI, probe_upstream: _Upstream
    ) -> None:
        """What is forwarded is what was judged: `/x/../webhooks/whatsapp` *is* the callback."""
        assert await call(app, "POST", "/api/v1/auth/../webhooks/whatsapp", body=SIGNED) == 200
        assert probe_upstream.paths == [WHATSAPP]

    async def test_across_the_whole_table_only_the_callback_is_reachable_without_a_token(
        self, app: FastAPI, probe_upstream: _Upstream, routes_file: Path
    ) -> None:
        """For **every** prefix in the table, every method, unauthenticated: the only requests
        that reach the upstream are the callback's GET and POST. A route this change accidentally
        made public (a prefix instead of an exact path, a method too many) shows up here as a
        request the stub saw."""
        reached: set[tuple[str, str]] = set()
        for route in RouteTable.from_file(routes_file):
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                before = len(probe_upstream.calls)
                await call(app, method, route.prefix)
                if len(probe_upstream.calls) != before:
                    reached.add((method, route.prefix))
        # Pinned, not derived from the policy: every public webhook, by exact path and method.
        # Meta's WhatsApp callback; Facebook Login's deauthorize / data-deletion callbacks; and a
        # tenant's own payment-provider webhook (tenant collections, ADR-0024 §8) at its original
        # Razorpay path and its provider-neutral one (ADR-0033) — never the platform's own
        # `/api/v1/webhooks/razorpay`, which stays routed-but-not-public.
        assert reached == {
            ("GET", WHATSAPP),
            ("POST", WHATSAPP),
            ("POST", f"{WHATSAPP}/deauthorize"),
            ("POST", f"{WHATSAPP}/data-deletion"),
            ("GET", f"{WHATSAPP}/data-deletion-status"),
            ("POST", RAZORPAY_COLLECTIONS),
            ("POST", COLLECTIONS),
            # A customer's Shiprocket account pushing tracking to operations.
            ("POST", "/api/v1/webhooks/operations/parcel-track"),
            # ... and Delhivery pushing scans.
            ("POST", "/api/v1/webhooks/operations/delhivery"),
        }
        assert ("POST", RAZORPAY) not in reached


class TestTheTenantCollectionsWebhook:
    """A merchant's payment-provider webhook (ADR-0024 §8, ADR-0033), at both its paths."""

    @pytest.mark.parametrize("path", [COLLECTIONS, RAZORPAY_COLLECTIONS])
    async def test_a_signed_delivery_reaches_payment_with_its_headers_byte_for_byte(
        self, http: AsyncClient, probe_upstream: _Upstream, routes_file: Path, path: str
    ) -> None:
        body = b'{"type":"PAYMENT_SUCCESS_WEBHOOK","data":{}}'
        headers = {
            "Content-Type": "application/json",
            "x-webhook-signature": "c2lnbmF0dXJl",
            "x-webhook-timestamp": "1759745115000",
            "x-idempotency-header": "dlv-1",
            "X-Razorpay-Signature": "ab" * 32,
        }
        response = await http.post(f"{path}?account=acc-1", content=body, headers=headers)

        assert response.status_code == 200
        forwarded = probe_upstream.calls[0]
        assert forwarded.url.path == path and forwarded.url.params["account"] == "acc-1"
        assert forwarded.content == body
        for name, value in headers.items():
            assert forwarded.headers[name.lower()] == value
        upstream = forwarded.url
        if routes_file == PROD_ROUTES:
            assert (upstream.host, upstream.port) == ("payment", 8000)
        else:
            assert (upstream.host, upstream.port) == ("localhost", 8163)

    @pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
    async def test_only_post_is_public(
        self, app: FastAPI, probe_upstream: _Upstream, method: str
    ) -> None:
        assert await call(app, method, COLLECTIONS, body=b"{}") == 401
        assert probe_upstream.calls == []

    def test_both_tables_cap_the_body_at_256_kib(self, routes_file: Path) -> None:
        route = RouteTable.from_file(routes_file).resolve(COLLECTIONS)
        assert route is not None and route.prefix == COLLECTIONS
        assert route.max_body_bytes == 256 * 1024

    async def test_a_body_over_the_cap_is_refused_before_payment_is_opened(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        response = await http.post(f"{COLLECTIONS}?account=a", content=b"x" * (256 * 1024 + 1))
        assert response.status_code == 413
        assert probe_upstream.calls == []


class TestNoCredentialSurface:
    async def test_every_spoofable_identity_header_is_stripped(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        """A caller cannot use the one public write path to claim an identity: every header the
        gateway reserves for itself is dropped, and none is written in its place."""
        await http.post(WHATSAPP, content=SIGNED, headers=dict.fromkeys(SPOOFABLE, "spoofed"))

        forwarded = probe_upstream.calls[0].headers
        assert not [name for name in SPOOFABLE if name in forwarded]
        assert "x-org-slug" not in forwarded

    async def test_a_bearer_token_is_neither_needed_nor_forwarded(
        self, http: AsyncClient, probe_upstream: _Upstream, mint: Any
    ) -> None:
        """A genuine tenant token on this path does not turn it into an authenticated call: no
        identity is derived from it and it is not passed on to the service."""
        response = await http.post(
            WHATSAPP, content=SIGNED, headers={"Authorization": f"Bearer {mint()}"}
        )

        assert response.status_code == 200
        forwarded = probe_upstream.calls[0].headers
        assert "authorization" not in forwarded
        assert "x-user-id" not in forwarded and "x-org-id" not in forwarded
        assert "x-internal-token" not in forwarded

    async def test_cookies_and_origin_are_not_forwarded(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        await http.post(
            WHATSAPP,
            content=SIGNED,
            headers={
                "Cookie": "refresh=secret",
                "Origin": "https://app.opsgrids.com",
                "Referer": "https://app.opsgrids.com/x",
                "Host": "acme.opsgrids.com",
            },
        )

        forwarded = probe_upstream.calls[0].headers
        assert not {"cookie", "origin", "referer", "x-org-slug"} & set(forwarded)


# ---------------------------------------------------------------------- the body cap


class TestBodyCap:
    def test_both_tables_carry_the_cap_on_the_route(self, routes_file: Path) -> None:
        route = RouteTable.from_file(routes_file).resolve(WHATSAPP)

        assert route is not None and route.prefix == WHATSAPP
        assert route.max_body_bytes == WHATSAPP_BODY_CAP

    async def test_a_body_between_the_default_and_the_route_cap_is_delivered(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        """Without the route's own cap a 2.5 MiB Meta batch would be a 413 at the gateway, which
        Meta answers by redelivering the same body until it gives up."""
        body = b"x" * (DEFAULT_MAX_BODY_BYTES + 512 * 1024)

        response = await http.post(WHATSAPP, content=body)

        assert response.status_code == 200
        assert probe_upstream.calls[0].content == body

    async def test_a_body_at_the_cap_is_delivered(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        response = await http.post(WHATSAPP, content=b"x" * WHATSAPP_BODY_CAP)

        assert response.status_code == 200
        assert len(probe_upstream.calls[0].content) == WHATSAPP_BODY_CAP

    async def test_a_body_one_byte_over_the_cap_is_refused_before_the_upstream_is_opened(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        response = await http.post(WHATSAPP, content=b"x" * (WHATSAPP_BODY_CAP + 1))

        assert response.status_code == 413
        assert probe_upstream.calls == []

    async def test_an_undeclared_oversized_body_is_counted_not_trusted(
        self, http: AsyncClient, probe_upstream: _Upstream
    ) -> None:
        """No `Content-Length` (chunked): the byte counter is the enforcement."""

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"y" * (1024 * 1024)

        response = await http.post(WHATSAPP, content=chunks())

        assert response.status_code == 413
        assert probe_upstream.calls == []


# ---------------------------------------------------------------- its own rate limit


class TestOwnRateLimit:
    def test_the_defaults_are_generous_enough_for_a_provider_burst(self) -> None:
        cfg = settings()
        assert cfg.webhook_rate_limit_per_minute == 600
        assert cfg.webhook_global_rate_limit_per_minute == 3000
        assert cfg.webhook_rate_limit_per_minute > 5 * cfg.anonymous_rate_limit_per_minute

    async def test_it_is_counted_apart_from_the_anonymous_bucket(
        self, http: AsyncClient, redis: FakeRedis
    ) -> None:
        await http.post(WHATSAPP, content=SIGNED)

        keys = sorted(redis.store)
        assert len(keys) == 2 and all(k.startswith(f"rl:hook:{WHATSAPP}") for k in keys), keys

    async def test_sign_in_traffic_does_not_spend_it_and_it_does_not_spend_sign_in(
        self, http: AsyncClient, redis: FakeRedis, probe_upstream: _Upstream
    ) -> None:
        await http.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "x"})
        await http.post(WHATSAPP, content=SIGNED)

        anon = [k for k in redis.store if k.startswith("rl:anon:")]
        hook = [k for k in redis.store if k.startswith("rl:hook:")]
        assert len(anon) == 1 and len(hook) == 2
        assert all(redis.store[k] == "1" for k in anon + hook)

    async def test_the_caller_is_counted_not_the_proxy(
        self, http: AsyncClient, redis: FakeRedis
    ) -> None:
        """Behind Caddy the peer is always Caddy: two callers are two buckets."""
        for caller in ("198.51.100.1", "198.51.100.2"):
            await http.post(WHATSAPP, content=SIGNED, headers={"X-Forwarded-For": caller})

        per_address = [k for k in redis.store if k.startswith(f"rl:hook:{WHATSAPP}:198.51.100.")]
        assert len(per_address) == 2

    async def test_an_address_over_its_own_allowance_is_refused_with_a_retry_after(
        self, http: AsyncClient, redis: FakeRedis, probe_upstream: _Upstream
    ) -> None:
        await http.post(WHATSAPP, content=SIGNED)
        bucket = next(k for k in redis.store if k.startswith(f"rl:hook:{WHATSAPP}:"))
        redis.store[bucket] = str(settings().webhook_rate_limit_per_minute)
        probe_upstream.calls.clear()

        response = await http.post(WHATSAPP, content=SIGNED)

        assert response.status_code == 429
        assert response.headers["retry-after"]
        assert probe_upstream.calls == []

    async def test_the_shared_ceiling_refuses_when_it_is_spent(
        self, http: AsyncClient, redis: FakeRedis, probe_upstream: _Upstream
    ) -> None:
        await http.post(WHATSAPP, content=SIGNED)
        shared = next(k for k in redis.store if "127.0.0.1" not in k)
        redis.store[shared] = str(settings().webhook_global_rate_limit_per_minute)
        probe_upstream.calls.clear()

        response = await http.post(WHATSAPP, content=SIGNED)

        assert response.status_code == 429
        assert probe_upstream.calls == []

    async def test_the_limiter_fails_closed(
        self, http: AsyncClient, redis: FakeRedis, probe_upstream: _Upstream
    ) -> None:
        redis.fail = True

        response = await http.post(WHATSAPP, content=SIGNED)

        assert response.status_code == 503
        assert probe_upstream.calls == []
