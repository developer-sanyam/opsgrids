"""R1: the anonymous rate limit counts the *caller*, not the reverse proxy in front of the gateway.

Behind Caddy the socket peer of every request app-gateway sees is Caddy's container address, so
keying the unauthenticated limiter on it made the "per-address" bucket one bucket for the whole
internet — and that limiter is the only brake on password and one-time-code guessing before a
per-account lockout has anything to count. The fix reads `X-Forwarded-For`, but only from a peer
inside a configured trusted-proxy list and from the right; these tests pin each half of that, and
that the anonymous ceiling added beside it trips and stays off the authenticated path.

The limiter runs for real against an in-memory counter (`cache._redis`), not a stub: what is under
test is *which key* a request is counted under, and a stub would not notice the answer being
wrong. Requests are driven through `ASGITransport(client=...)`, which is how a test chooses the
socket peer the gateway sees.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from platform_core import cache
from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import (
    GatewayPolicy,
    Route,
    RouteTable,
    build_gateway,
    client_limiter_key,
    parse_trusted_proxies,
)

LOGIN = "/api/v1/auth/login"
PRIVATE = "/api/v1/employees"
UPSTREAM = "http://upstream-service:9999"

#: Caddy's address on the compose network, and the range the deployment names as trusted.
CADDY = "172.18.0.5"
TRUSTED = parse_trusted_proxies(["172.16.0.0/12"])

#: Per-address limit small enough to trip in a test; the ceiling one larger than the number of
#: distinct callers a test uses so it only trips when a test means it to.
PER_ADDRESS = 3


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


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def redis() -> Iterator[_FakeRedis]:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


def _app(
    upstream: _Upstream,
    monkeypatch: pytest.MonkeyPatch,
    *,
    trusted: Any,
    global_limit: int = 0,
) -> FastAPI:
    from platform_core import gateway as gateway_module

    monkeypatch.setattr(
        gateway_module,
        "decode_token",
        lambda *a, **k: {
            "sub": "00000000-0000-0000-0000-000000000001",
            "act_org": "00000000-0000-0000-0000-000000000002",
            "pv": 0,
            "ent": [],
            "roles": [],
            "loc": [],
            "amr": ["pwd"],
        },
    )
    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    policy = GatewayPolicy(
        audience="app",
        public_paths=frozenset({LOGIN}),
        anonymous_rate_limit=PER_ADDRESS,
        anonymous_global_rate_limit=global_limit,
        trusted_proxies=trusted,
    )
    routes = RouteTable([Route("/api/v1/auth", UPSTREAM), Route("/api/v1/employees", UPSTREAM)])
    build_gateway(application, settings=cfg, routes=routes, policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


def _client(app: FastAPI, peer: str) -> AsyncClient:
    """A client whose *socket peer* is `peer` — what `request.client.host` reports."""
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 40000)), base_url="http://t")


def _request(peer: str | None, forwarded: list[str] | None = None) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded or []]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": LOGIN,
        "headers": headers,
        "client": (peer, 1234) if peer is not None else None,
    }
    return Request(scope)


# ------------------------------------------------------------------------- the resolver itself


class TestResolvingTheCaller:
    def test_a_trusted_proxy_is_believed_about_who_called(self) -> None:
        assert client_limiter_key(_request(CADDY, ["203.0.113.7"]), TRUSTED) == "203.0.113.7"

    def test_the_same_header_from_a_peer_that_is_not_a_trusted_proxy_is_ignored(self) -> None:
        """Anyone can send `X-Forwarded-For`. From a peer we did not name it is a string the
        attacker chose, and believing it would let a client pick a fresh bucket per request."""
        request = _request("198.51.100.9", ["203.0.113.7"])
        assert client_limiter_key(request, TRUSTED) == "198.51.100.9"

    def test_with_no_trusted_proxies_configured_nothing_is_ever_believed(self) -> None:
        """The default. Development and tests connect directly; a deployment that has not said
        where its proxy is must not start believing a header by accident."""
        assert client_limiter_key(_request(CADDY, ["203.0.113.7"]), ()) == CADDY

    def test_the_chain_is_read_from_the_right(self) -> None:
        """A client-supplied prefix cannot displace the address our own proxy appended. Here the
        client claims to be 1.1.1.1; the trusted proxy (10.0.0.9, the peer's peer) recorded the
        real 203.0.113.7 to its right, and that is the caller."""
        trusted = parse_trusted_proxies(["172.16.0.0/12", "10.0.0.0/8"])
        request = _request(CADDY, ["1.1.1.1, 203.0.113.7, 10.0.0.9"])
        assert client_limiter_key(request, trusted) == "203.0.113.7"

    def test_a_spoofed_leftmost_entry_never_wins(self) -> None:
        """The old, wrong reading ("the first hop is the client") is exactly what an attacker
        exploits. With one appended entry the spoofed prefix must lose to it."""
        request = _request(CADDY, ["6.6.6.6, 203.0.113.7"])
        assert client_limiter_key(request, TRUSTED) == "203.0.113.7"

    def test_repeated_header_lines_are_one_chain(self) -> None:
        request = _request(CADDY, ["6.6.6.6", "203.0.113.7"])
        assert client_limiter_key(request, TRUSTED) == "203.0.113.7"

    @pytest.mark.parametrize(
        "forwarded",
        [
            None,  # no header at all: a health probe from the proxy itself
            ["not-an-address"],
            ["203.0.113.7, garbage"],  # garbage where our proxy should have appended
            ["203.0.113.7:4444"],  # a port is not something Caddy writes; do not half-parse it
            [""],
            ["172.19.0.2, 172.20.0.3"],  # a chain of nothing but our own proxies
        ],
    )
    def test_anything_ambiguous_falls_back_to_the_peer(self, forwarded: list[str] | None) -> None:
        """The conservative bucket: shared, so stricter, and never chosen by the client."""
        assert client_limiter_key(_request(CADDY, forwarded), TRUSTED) == CADDY

    def test_an_ipv6_caller_is_counted_by_its_64(self) -> None:
        """An IPv6 subscriber holds a /64 (or more), so limiting on the full address lets one
        client rotate through 2^64 buckets and never meet its own."""
        one = client_limiter_key(_request(CADDY, ["2001:db8:1:2:aaaa::1"]), TRUSTED)
        other = client_limiter_key(_request(CADDY, ["2001:db8:1:2:bbbb::9"]), TRUSTED)
        elsewhere = client_limiter_key(_request(CADDY, ["2001:db8:1:3::1"]), TRUSTED)
        assert one == other == "2001:db8:1:2::/64"
        assert elsewhere != one

    def test_an_ipv4_mapped_address_is_the_ipv4_address(self) -> None:
        assert client_limiter_key(_request("::ffff:203.0.113.7"), ()) == "203.0.113.7"
        assert client_limiter_key(_request(CADDY, ["::ffff:203.0.113.7"]), TRUSTED) == "203.0.113.7"

    def test_a_peer_that_is_not_an_address_is_used_as_it_is(self) -> None:
        # An in-process client reports "testclient"; there is nothing to normalise or trust.
        assert client_limiter_key(_request("testclient", ["1.2.3.4"]), TRUSTED) == "testclient"

    def test_no_peer_at_all_is_one_anonymous_bucket(self) -> None:
        assert client_limiter_key(_request(None), TRUSTED) == "anonymous"


class TestTheTrustedProxyList:
    def test_addresses_and_ranges_parse(self) -> None:
        networks = parse_trusted_proxies(["172.16.0.0/12", " 10.1.2.3 ", "", "fd00::/8"])
        assert [str(n) for n in networks] == ["172.16.0.0/12", "10.1.2.3/32", "fd00::/8"]

    @pytest.mark.parametrize("entry", ["0.0.0.0/0", "::/0"])
    def test_a_range_that_matches_everything_is_refused(self, entry: str) -> None:
        """Trusting every peer is trusting no peer: any client could name its own address."""
        with pytest.raises(ValueError, match="matches every address"):
            parse_trusted_proxies([entry])

    @pytest.mark.parametrize("entry", ["caddy", "172.16.0.0/99", "*", "172.16.0.0/12;"])
    def test_a_malformed_entry_stops_boot_rather_than_silently_trusting_nothing(
        self, entry: str
    ) -> None:
        with pytest.raises(ValueError, match="not an IP address or a CIDR"):
            parse_trusted_proxies([entry])


# ------------------------------------------------------------------ through the real gateway


class TestSeparateBucketsBehindTheProxy:
    async def test_two_callers_behind_the_same_proxy_do_not_share_a_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug, as an outcome. Before the fix both callers arrive from `CADDY`, so the
        first caller's requests spend the second's allowance."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED)
        async with _client(app, CADDY) as client:
            noisy = {"X-Forwarded-For": "203.0.113.7"}
            quiet = {"X-Forwarded-For": "198.51.100.20"}

            for _ in range(PER_ADDRESS):
                assert (await client.post(LOGIN, headers=noisy)).status_code == 200
            over = await client.post(LOGIN, headers=noisy)
            assert over.status_code == 429
            assert over.headers["retry-after"] == "60"

            # A different caller behind the same proxy is untouched by the first one's spend.
            assert (await client.post(LOGIN, headers=quiet)).status_code == 200

        # `rl:anon:<address>:<window>` — each caller is counted under its own address, and
        # nothing is counted under Caddy's.
        counted = sorted(k.split(":")[2] for k in redis.store if k.startswith("rl:anon:"))
        assert counted == ["198.51.100.20", "203.0.113.7"], counted
        await app.state.http.aclose()

    async def test_without_the_trust_list_every_caller_behind_the_proxy_shares_one_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-fix behaviour, kept as the safe default and asserted so it is a decision and
        not an accident: with no list configured the proxy's own address is the caller, and
        one caller's spend is everyone's."""
        app = _app(upstream, monkeypatch, trusted=())
        async with _client(app, CADDY) as client:
            for i in range(PER_ADDRESS):
                headers = {"X-Forwarded-For": f"203.0.113.{i}"}
                assert (await client.post(LOGIN, headers=headers)).status_code == 200
            stranger = await client.post(LOGIN, headers={"X-Forwarded-For": "198.51.100.20"})
            assert stranger.status_code == 429
        await app.state.http.aclose()

    async def test_a_spoofed_header_from_an_untrusted_peer_cannot_pick_a_fresh_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An attacker who reaches the gateway directly (not through Caddy) rotates the header
        on every request. It must not buy a new allowance each time."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED)
        async with _client(app, "198.51.100.9") as client:
            statuses = [
                (await client.post(LOGIN, headers={"X-Forwarded-For": f"10.9.8.{i}"})).status_code
                for i in range(PER_ADDRESS + 2)
            ]
        assert statuses == [200] * PER_ADDRESS + [429, 429]
        # One bucket, under the peer's own address; none of the invented ones were ever counted.
        counted = [k.split(":")[2] for k in redis.store if k.startswith("rl:anon:")]
        assert counted == ["198.51.100.9"], counted
        await app.state.http.aclose()

    async def test_a_client_prefixed_header_through_the_proxy_cannot_dodge_its_own_bucket(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if a proxy in the chain *appends* rather than replaces, the client's leftmost
        entries lose to the one the proxy wrote."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED)
        async with _client(app, CADDY) as client:
            statuses = [
                (
                    await client.post(LOGIN, headers={"X-Forwarded-For": f"9.9.9.{i}, 203.0.113.7"})
                ).status_code
                for i in range(PER_ADDRESS + 1)
            ]
        assert statuses == [200] * PER_ADDRESS + [429]
        await app.state.http.aclose()


class TestTheAnonymousCeiling:
    async def test_the_shared_ceiling_trips_when_many_callers_are_each_under_their_own_limit(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crowd, each politely inside the per-address limit, guessing at the one surface
        anybody can reach. Five distinct callers, one request each, against a ceiling of four."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED, global_limit=4)
        async with _client(app, CADDY) as client:
            results = [
                await client.post(LOGIN, headers={"X-Forwarded-For": f"203.0.113.{i}"})
                for i in range(1, 6)
            ]
        assert [r.status_code for r in results] == [200, 200, 200, 200, 429]
        refused = results[-1]
        # Clear about what happened, and when to come back — and distinct from the per-address
        # refusal, so an operator reading a log can tell a flood from one noisy client.
        assert refused.headers["retry-after"] == "60"
        assert refused.headers["content-type"].startswith("application/problem+json")
        assert "unauthenticated requests are arriving" in refused.json()["detail"]
        assert len(upstream.calls) == 4, "a refused request must not reach the service"
        await app.state.http.aclose()

    async def test_one_address_cannot_spend_more_of_the_ceiling_than_its_own_allowance(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-address check runs first. A single address hammering the gateway is refused
        by its own bucket and stops counting against the shared one."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED, global_limit=PER_ADDRESS + 1)
        async with _client(app, CADDY) as client:
            noisy = {"X-Forwarded-For": "203.0.113.7"}
            for _ in range(PER_ADDRESS * 5):
                await client.post(LOGIN, headers=noisy)
            # Its flood spent PER_ADDRESS of the ceiling, not five times that, so one other
            # caller still gets through.
            other = await client.post(LOGIN, headers={"X-Forwarded-For": "198.51.100.20"})
        assert other.status_code == 200
        assert redis.store[next(k for k in redis.store if k.startswith("rl:anon-global:"))] == str(
            PER_ADDRESS + 1
        )
        await app.state.http.aclose()

    async def test_authenticated_traffic_is_untouched_by_an_exhausted_ceiling(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A flood of strangers must not log anybody out."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED, global_limit=1)
        async with _client(app, CADDY) as client:
            first = await client.post(LOGIN, headers={"X-Forwarded-For": "203.0.113.1"})
            second = await client.post(LOGIN, headers={"X-Forwarded-For": "203.0.113.2"})
            assert (first.status_code, second.status_code) == (200, 429)

            signed_in = await client.get(
                PRIVATE,
                headers={"Authorization": "Bearer stubbed", "X-Forwarded-For": "203.0.113.2"},
            )
        assert signed_in.status_code == 200
        assert any(k.startswith("rl:user:") for k in redis.store), "counted against the user"
        await app.state.http.aclose()

    async def test_a_ceiling_of_zero_means_none(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`admin-gateway` builds its policy without one, and must behave as it always has."""
        app = _app(upstream, monkeypatch, trusted=TRUSTED, global_limit=0)
        async with _client(app, CADDY) as client:
            for i in range(1, 11):
                response = await client.post(LOGIN, headers={"X-Forwarded-For": f"203.0.113.{i}"})
                assert response.status_code == 200
        assert not any(k.startswith("rl:anon-global") for k in redis.store)
        await app.state.http.aclose()
