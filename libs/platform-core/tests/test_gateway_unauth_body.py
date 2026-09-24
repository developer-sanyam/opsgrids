"""The gateway buffers a request body whole, and a stranger chooses how long that takes.

Forty connections from one address, each sending 3 MiB minus a byte of a WhatsApp body and then
nothing, held 122 MiB of a 256 MiB container within half a second — and the webhook route's larger
cap made the pre-existing problem on the anonymous paths fifteen times worse. The fix is at the
gateway, not a global Caddy read timeout (which would break slow legitimate document uploads on
authenticated paths), and has three parts, each pinned here:

* a **deadline** for sending the body — 408 and everything buffered released — on every
  unauthenticated request the gateway buffers (webhooks and public paths), and only those;
* a **per-address** and a **process-wide** cap on how many such requests are in flight — 503 with
  `Retry-After: 5` past either, before a byte of the body is touched;
* both **released on every way out**: a finished response, a body over its cap, a timeout, a client
  that disconnects, an upstream that fails, a cancelled task.

Requests are driven with a hand-built ASGI scope because what matters is *when* `receive()` returns.
Peak buffered bytes is counted by the test: bytes handed to the gateway and not yet released with
their request, which is the most the gateway can be holding.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, MutableMapping
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect

from platform_core import cache
from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import GatewayPolicy, Route, RouteTable, build_gateway

UPSTREAM = "http://upstream-service:9999"
WHATSAPP = "/api/v1/webhooks/whatsapp"
LOGIN = "/api/v1/auth/login"
PRIVATE = "/api/v1/documents"

MIB = 1024 * 1024
WHATSAPP_CAP = 3 * MIB
#: What the probe sends before it stops: as much as the cap allows, less a byte.
ALMOST_CAP = WHATSAPP_CAP - 1


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
        self.calls = 0
        #: Set to hold every upstream call until a test lets it go.
        self.gate: asyncio.Event | None = None
        self.fail: Exception | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
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


def _build(upstream: _Upstream, monkeypatch: pytest.MonkeyPatch, **policy: Any) -> FastAPI:
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
    settings: dict[str, Any] = {
        "audience": "app",
        "public_paths": frozenset({LOGIN}),
        "webhook_paths": {WHATSAPP: frozenset({"GET", "POST"})},
        "anonymous_rate_limit": 100_000,
        "webhook_failure_limit": 100_000,
        **policy,
    }
    routes = RouteTable(
        [
            Route("/api/v1/auth", UPSTREAM),
            Route(PRIVATE, UPSTREAM, max_body_bytes=8 * MIB),
            Route(WHATSAPP, UPSTREAM, max_body_bytes=WHATSAPP_CAP),
        ]
    )
    build_gateway(application, settings=cfg, routes=routes, policy=GatewayPolicy(**settings))
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@dataclass
class _Held:
    """Bytes handed to the gateway and not yet released with their request."""

    live: int = 0
    peak: int = 0

    def take(self, size: int) -> None:
        self.live += size
        self.peak = max(self.peak, self.live)

    def release(self, size: int) -> None:
        self.live -= size


@dataclass
class _Answer:
    status: int
    headers: dict[str, str]
    body: bytes


def _scope(method: str, path: str, peer: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("test", 80),
        "client": (peer, 1),
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
    }


async def _drive(app: FastAPI, scope: dict[str, Any], receive: Callable[..., Any]) -> _Answer:
    messages: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return _Answer(int(start["status"]), headers, body)


async def _slow_post(
    app: FastAPI,
    held: _Held,
    *,
    path: str = WHATSAPP,
    peer: str = "198.51.100.7",
    size: int = ALMOST_CAP,
    then: str = "stall",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> _Answer:
    """A body that arrives in one big chunk and then stops (`then="stall"`), or whose client goes
    away (`then="disconnect"`) — the two shapes of a caller that will never finish sending."""
    stall = asyncio.Event()
    delivered = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            held.take(size)
            return {"type": "http.request", "body": bytes(size), "more_body": True}
        if then == "disconnect":
            return {"type": "http.disconnect"}
        await stall.wait()  # never set: the client has gone quiet
        return {"type": "http.disconnect"}

    scope = _scope("POST", path, peer, extra_headers or [])
    try:
        return await _drive(app, scope, receive)
    finally:
        if delivered:
            held.release(size)


async def _quick_post(
    app: FastAPI,
    *,
    path: str = WHATSAPP,
    peer: str = "198.51.100.7",
    body: bytes = b"{}",
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> _Answer:
    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return await _drive(app, _scope(method, path, peer, headers or []), receive)


async def _settle(seconds: float = 0.3) -> None:
    await asyncio.sleep(seconds)


async def _cancel(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------- the probe


class TestFortyHalfSentBodies:
    async def test_one_address_can_hold_only_its_share_of_memory(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reproduced attack: 40 tasks from one address, each sending 3 MiB - 1 and stalling.
        Before, all 40 were buffered at once (120 MiB); now four are, and the other 36 are told to
        come back in five seconds without a byte of their body being read."""
        app = _build(upstream, monkeypatch)
        held = _Held()
        tasks = [asyncio.create_task(_slow_post(app, held)) for _ in range(40)]
        await _settle(0.5)

        finished = [t.result() for t in tasks if t.done()]
        assert held.peak < 64 * MIB, f"peak buffered {held.peak / MIB:.0f} MiB"
        assert held.peak == 4 * ALMOST_CAP, "exactly the four slots one address may hold"
        assert len(finished) == 36
        assert {a.status for a in finished} == {503}
        assert {a.headers["retry-after"] for a in finished} == {"5"}
        assert app.state.body_slots.in_flight == 4
        await _cancel(tasks)
        assert app.state.body_slots.in_flight == 0, "a cancelled request gives its slot back"

    async def test_many_addresses_are_bounded_by_the_process_wide_cap(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One address is capped at four, so a real attacker uses many. The process-wide cap is
        what bounds *them*: 32 slots, whatever the number of addresses."""
        app = _build(upstream, monkeypatch)
        held = _Held()
        size = MIB
        tasks = [
            asyncio.create_task(_slow_post(app, held, peer=f"198.51.100.{i}", size=size))
            for i in range(1, 101)
        ]
        await _settle(0.5)

        refused = [t.result() for t in tasks if t.done()]
        assert held.peak == 32 * size
        assert len(refused) == 68 and {a.status for a in refused} == {503}
        await _cancel(tasks)
        assert app.state.body_slots.in_flight == 0


class TestPublicPathsAreGuardedToo:
    """The problem pre-dates the webhooks: a public path such as sign-in has always buffered a body
    for a stranger. The webhook route only made it worse, so the guard covers both."""

    async def test_forty_half_sent_sign_in_bodies_are_bounded_the_same_way(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch)
        held = _Held()
        size = 2 * MIB - 1  # just under the gateway's default cap
        tasks = [
            asyncio.create_task(_slow_post(app, held, path=LOGIN, size=size)) for _ in range(40)
        ]
        await _settle(0.5)

        assert held.peak == 4 * size and held.peak < 64 * MIB
        assert sum(1 for t in tasks if t.done() and t.result().status == 503) == 36
        await _cancel(tasks)
        assert app.state.body_slots.in_flight == 0

    async def test_a_public_path_and_a_webhook_draw_on_the_same_addresss_slots(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One address, one pool: it cannot hold four on each."""
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        stalled = asyncio.create_task(_slow_post(app, _Held(), path=LOGIN, size=1024))
        await _settle(0.1)
        assert app.state.body_slots.in_flight == 1

        assert (await _quick_post(app, path=LOGIN)).status == 503
        assert (await _quick_post(app, path=WHATSAPP)).status == 503
        await _cancel([stalled])
        assert (await _quick_post(app, path=LOGIN)).status == 200


class TestTheReadDeadline:
    async def test_a_body_that_stalls_is_abandoned_with_a_408_and_the_slot_is_released(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_read_timeout=0.2)
        held = _Held()
        tasks = [asyncio.create_task(_slow_post(app, held)) for _ in range(4)]
        await _settle(0.6)

        answers = [t.result() for t in tasks]
        assert {a.status for a in answers} == {408}
        assert b"request-timeout" in answers[0].body
        assert app.state.body_slots.in_flight == 0
        assert held.live == 0 and upstream.calls == 0, "nothing reached the service"
        # The address is free again: a genuine delivery from it goes through.
        assert (await _quick_post(app)).status == 200

    async def test_every_slot_is_free_again_after_the_deadline_for_a_full_attack(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_read_timeout=0.2)
        held = _Held()
        tasks = [
            asyncio.create_task(_slow_post(app, held, peer=f"198.51.100.{i}", size=MIB))
            for i in range(1, 41)
        ]
        await _settle(0.7)
        assert all(t.done() for t in tasks)
        statuses = [t.result().status for t in tasks]
        # Thirty-two got a slot and timed out; the other eight found none.
        assert statuses.count(408) == 32 and statuses.count(503) == 8
        assert app.state.body_slots.in_flight == 0

    async def test_a_public_path_has_the_same_deadline(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not only webhooks: the anonymous paths buffered bodies unguarded too."""
        app = _build(upstream, monkeypatch, unauth_body_read_timeout=0.2)
        answer = await _slow_post(app, _Held(), path=LOGIN, size=1024)
        assert answer.status == 408

    async def test_a_body_that_arrives_in_time_is_delivered(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_read_timeout=0.5)
        assert (await _quick_post(app, body=b"x" * 1000)).status == 200
        assert upstream.calls == 1

    async def test_an_authenticated_upload_has_no_deadline(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow document upload is legitimate, which is why this is not a global Caddy
        `read_body`: the slow body that gets a 408 unauthenticated goes through with a token."""
        app = _build(upstream, monkeypatch, unauth_body_read_timeout=0.1)
        received = 0

        async def receive() -> MutableMapping[str, Any]:
            nonlocal received
            received += 1
            if received == 1:
                return {"type": "http.request", "body": b"a" * 100, "more_body": True}
            await asyncio.sleep(0.3)  # three times the unauthenticated deadline
            return {"type": "http.request", "body": b"b" * 100, "more_body": False}

        scope = _scope("POST", PRIVATE, "198.51.100.7", [(b"authorization", b"Bearer stubbed")])
        answer = await _drive(app, scope, receive)
        assert answer.status == 200
        assert upstream.calls == 1


class TestSlotsAreReleasedOnEveryWayOut:
    """One address, one slot, so a leak of any kind shows up as the next request being refused."""

    async def test_after_a_normal_response(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        for _ in range(5):
            assert (await _quick_post(app)).status == 200
        assert app.state.body_slots.in_flight == 0

    async def test_after_a_body_over_its_cap(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        over = await _quick_post(app, body=b"x" * (WHATSAPP_CAP + 1))
        assert over.status == 413
        assert app.state.body_slots.in_flight == 0
        assert (await _quick_post(app)).status == 200

    async def test_after_a_declared_length_over_its_cap(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        declared = [(b"content-length", str(WHATSAPP_CAP + 1).encode())]
        assert (await _quick_post(app, headers=declared)).status == 413
        assert app.state.body_slots.in_flight == 0
        assert (await _quick_post(app)).status == 200

    async def test_after_a_read_timeout(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(
            upstream, monkeypatch, unauth_body_slots_per_address=1, unauth_body_read_timeout=0.1
        )
        assert (await _slow_post(app, _Held(), size=10)).status == 408
        assert app.state.body_slots.in_flight == 0
        assert (await _quick_post(app)).status == 200

    async def test_after_the_client_disconnects_mid_body(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        # What the server does about a dropped connection is not the point; the slot is.
        with pytest.raises(ClientDisconnect):
            await _slow_post(app, _Held(), then="disconnect", size=1000)
        assert app.state.body_slots.in_flight == 0, "the slot survived a dropped connection"
        assert (await _quick_post(app)).status == 200

    async def test_after_the_service_fails(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upstream.fail = httpx.ReadTimeout("slow")
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        assert (await _quick_post(app)).status == 503
        assert app.state.body_slots.in_flight == 0
        upstream.fail = None
        assert (await _quick_post(app)).status == 200

    async def test_after_the_request_is_cancelled(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        task = asyncio.create_task(_slow_post(app, _Held(), size=1000))
        await _settle(0.1)
        assert app.state.body_slots.in_flight == 1
        await _cancel([task])
        assert app.state.body_slots.in_flight == 0
        assert (await _quick_post(app)).status == 200


class TestASlotIsHeldWhileTheBodyIsInMemory:
    async def test_the_slot_is_held_through_the_upstream_call_not_just_the_read(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The body stays in gateway memory until the response is built, so a slow service must not
        let an address pile up buffered bodies behind it."""
        upstream.gate = asyncio.Event()
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        first = asyncio.create_task(_quick_post(app))
        await _settle(0.1)
        assert upstream.calls == 1 and app.state.body_slots.in_flight == 1

        second = await _quick_post(app)
        assert second.status == 503 and second.headers["retry-after"] == "5"
        assert upstream.calls == 1, "the refused request never reached the service"

        upstream.gate.set()
        assert (await first).status == 200
        assert app.state.body_slots.in_flight == 0


class TestGenuineTrafficStillGetsThrough:
    async def test_a_genuine_request_succeeds_while_all_but_one_slot_of_its_address_is_held(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch)  # four per address
        held = _Held()
        stalled = [asyncio.create_task(_slow_post(app, held)) for _ in range(3)]
        await _settle(0.1)
        assert app.state.body_slots.in_flight == 3

        assert (await _quick_post(app)).status == 200  # the fourth slot, same address
        assert (await _quick_post(app)).status == 200  # and it was given back
        await _cancel(stalled)

    async def test_a_different_address_is_unaffected_by_one_that_holds_all_of_its_own(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch)
        held = _Held()
        stalled = [asyncio.create_task(_slow_post(app, held, size=1024)) for _ in range(4)]
        await _settle(0.1)

        assert (await _quick_post(app)).status == 503  # the hostile address, refused
        assert (await _quick_post(app, peer="13.232.125.1")).status == 200  # the provider
        await _cancel(stalled)

    async def test_a_genuine_request_succeeds_while_all_but_one_global_slot_is_held(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build(upstream, monkeypatch)  # 32 in total
        held = _Held()
        stalled = [
            asyncio.create_task(_slow_post(app, held, peer=f"198.51.100.{i}", size=1024))
            for i in range(1, 32)
        ]
        await _settle(0.2)
        assert app.state.body_slots.in_flight == 31

        assert (await _quick_post(app, peer="13.232.125.1")).status == 200  # the 32nd slot
        # ... and with that one taken too, the next stranger is told to retry.
        one_more = asyncio.create_task(_slow_post(app, held, peer="203.0.113.1", size=1024))
        await _settle(0.1)
        assert app.state.body_slots.in_flight == 32
        refused = await _quick_post(app, peer="13.232.125.2")
        assert refused.status == 503 and refused.headers["retry-after"] == "5"
        await _cancel([*stalled, one_more])
        assert app.state.body_slots.in_flight == 0

    async def test_authenticated_requests_take_no_slot(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A signed-in user on an address whose slots a stranger has used up is not refused."""
        app = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        stalled = asyncio.create_task(_slow_post(app, _Held(), size=1024))
        await _settle(0.1)
        assert (await _quick_post(app)).status == 503

        signed_in = await _quick_post(
            app, path=PRIVATE, headers=[(b"authorization", b"Bearer stubbed")]
        )
        assert signed_in.status == 200
        await _cancel([stalled])

    async def test_the_shared_address_of_a_proxy_is_not_one_slot_pool_for_everyone(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Behind Caddy every request's socket peer is Caddy. Slots are counted per *caller*
        (the resolved address), or one hostile visitor would hold the pool for all of them."""
        from platform_core.gateway import parse_trusted_proxies

        trusted = parse_trusted_proxies(["172.16.0.0/12"])
        app = _build(
            upstream, monkeypatch, unauth_body_slots_per_address=1, trusted_proxies=trusted
        )
        caddy = "172.18.0.5"
        hostile = [(b"x-forwarded-for", b"203.0.113.9")]
        stalled = asyncio.create_task(
            _slow_post(app, _Held(), peer=caddy, size=1024, extra_headers=hostile)
        )
        await _settle(0.1)

        assert (await _quick_post(app, peer=caddy, headers=hostile)).status == 503
        visitor = [(b"x-forwarded-for", b"198.51.100.20")]
        assert (await _quick_post(app, peer=caddy, headers=visitor)).status == 200
        await _cancel([stalled])


class TestSlotsAreOnlyForTheUnauthenticated:
    async def test_the_defaults_are_the_documented_ones(self) -> None:
        policy = GatewayPolicy(audience="app", public_paths=frozenset())
        assert policy.unauth_body_read_timeout == 10.0
        assert policy.unauth_body_slots_per_address == 4
        assert policy.unauth_body_slots_global == 32

    async def test_a_gateway_keeps_its_own_slots(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        two = _build(upstream, monkeypatch, unauth_body_slots_per_address=1)
        stalled = asyncio.create_task(_slow_post(one, _Held(), size=1024))
        await _settle(0.1)
        assert one.state.body_slots.in_flight == 1 and two.state.body_slots.in_flight == 0
        assert (await _quick_post(two)).status == 200
        await _cancel([stalled])

    async def test_a_client_that_reads_as_an_asgi_transport_is_unaffected(
        self, redis: _FakeRedis, upstream: _Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordinary path, through httpx, for a body that is simply sent: nothing changes."""
        app = _build(upstream, monkeypatch)
        transport = ASGITransport(app=app, client=("198.51.100.7", 1))
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            responses = [await client.post(WHATSAPP, content=b"x" * 1000) for _ in range(20)]
        assert {r.status_code for r in responses} == {200}
        assert app.state.body_slots.in_flight == 0
