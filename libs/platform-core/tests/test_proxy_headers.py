"""M2(b) of the 2026-09-24 SSO review: `request.client.host` behind Caddy is always Caddy's own
address unless something corrects it — the anonymous rate limiter used to key every visitor on
the box into the same bucket. `real_client_host`/`TrustForwardedForFromPrivatePeers` are the
correction, and only from a peer address a Docker compose network could actually hand Caddy.
"""

from __future__ import annotations

from typing import Any

from platform_core.proxy_headers import (
    TrustForwardedForFromPrivatePeers,
    is_trusted_proxy_peer,
    real_client_host,
)

CADDY = "172.20.0.5"
STRANGER = "203.0.113.7"


class TestIsTrustedProxyPeer:
    def test_docker_bridge_ranges_are_trusted(self) -> None:
        assert is_trusted_proxy_peer("172.20.0.5")
        assert is_trusted_proxy_peer("10.0.0.1")
        assert is_trusted_proxy_peer("192.168.1.1")

    def test_loopback_is_trusted(self) -> None:
        assert is_trusted_proxy_peer("127.0.0.1")
        assert is_trusted_proxy_peer("::1")

    def test_a_public_address_is_not_trusted(self) -> None:
        assert not is_trusted_proxy_peer(STRANGER)

    def test_garbage_is_not_trusted(self) -> None:
        assert not is_trusted_proxy_peer("not-an-address")
        assert not is_trusted_proxy_peer("")


class TestRealClientHost:
    def test_a_trusted_peer_with_one_hop_forwarded_for_is_believed(self) -> None:
        assert real_client_host(peer_host=CADDY, forwarded_for=STRANGER) == STRANGER

    def test_a_trusted_peer_takes_the_rightmost_entry_of_a_chain(self) -> None:
        """A client that prepends its own fake entries cannot change what Caddy itself
        appended — the rightmost entry, as this process receives it, is always Caddy's own
        observation of the connecting peer."""
        spoofed_chain = f"1.2.3.4, 5.6.7.8, {STRANGER}"
        assert real_client_host(peer_host=CADDY, forwarded_for=spoofed_chain) == STRANGER

    def test_an_untrusted_peer_is_believed_over_its_own_header(self) -> None:
        """The header on a direct, unproxied connection is whatever the caller wants it to be —
        ignored outright, in favour of the one fact that connection cannot fake: its own
        address."""
        assert real_client_host(peer_host=STRANGER, forwarded_for="1.2.3.4") == STRANGER

    def test_a_trusted_peer_with_no_header_falls_back_to_the_peer(self) -> None:
        assert real_client_host(peer_host=CADDY, forwarded_for=None) == CADDY
        assert real_client_host(peer_host=CADDY, forwarded_for="") == CADDY

    def test_a_trusted_peer_with_an_unparseable_rightmost_entry_falls_back_to_the_peer(
        self,
    ) -> None:
        assert real_client_host(peer_host=CADDY, forwarded_for="not-an-address") == CADDY

    def test_no_peer_at_all_is_left_alone(self) -> None:
        assert real_client_host(peer_host=None, forwarded_for=STRANGER) is None


async def _run(
    app_state: dict[str, Any], client: tuple[str, int] | None, headers: list[tuple[bytes, bytes]]
) -> None:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        app_state["client"] = scope.get("client")

    middleware = TrustForwardedForFromPrivatePeers(inner)
    scope = {"type": "http", "client": client, "headers": headers}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await middleware(scope, receive, send)


class TestTrustForwardedForFromPrivatePeersMiddleware:
    async def test_rewrites_client_for_a_trusted_peer(self) -> None:
        state: dict[str, Any] = {}
        await _run(state, (CADDY, 51234), [(b"x-forwarded-for", STRANGER.encode())])
        assert state["client"] == (STRANGER, 51234)

    async def test_leaves_client_alone_for_an_untrusted_peer_even_with_a_header(self) -> None:
        state: dict[str, Any] = {}
        await _run(state, (STRANGER, 51234), [(b"x-forwarded-for", b"1.2.3.4")])
        assert state["client"] == (STRANGER, 51234)

    async def test_two_different_forwarded_addresses_resolve_to_two_different_clients(
        self,
    ) -> None:
        """What actually fixes the shared rate-limit bucket: two visitors behind the same Caddy
        peer address end up with two different `scope["client"]`s, not one."""
        first: dict[str, Any] = {}
        second: dict[str, Any] = {}
        await _run(first, (CADDY, 1), [(b"x-forwarded-for", b"198.51.100.1")])
        await _run(second, (CADDY, 1), [(b"x-forwarded-for", b"198.51.100.2")])
        assert first["client"] != second["client"]

    async def test_a_non_http_scope_is_untouched(self) -> None:
        seen = {}

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            seen["scope"] = scope

        middleware = TrustForwardedForFromPrivatePeers(inner)
        scope = {"type": "lifespan"}
        await middleware(scope, lambda: None, lambda _m: None)
        assert seen["scope"] is scope
