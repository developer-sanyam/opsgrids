"""Trust `X-Forwarded-For`, but only from the one hop production actually has.

M2(b) of the 2026-09-24 SSO review: `bin/api` runs uvicorn with no `--forwarded-allow-ips`, so
`request.client.host` is always the TCP peer uvicorn sees — Caddy's own container address on the
compose network, for every request, since Caddy is the only thing in front of app-gateway
(ADR-0012: one edge VM). `enforce_rate_limit(f"anon:{client_ip}", ...)` on the login and
password-reset routes therefore keyed every visitor into the *same* bucket: one caller hammering
`/auth/login` could exhaust the limit for everyone else on the box, silently — no error, no log,
just a 429 for a stranger's failed attempts.

The fix is not "trust `X-Forwarded-For`" — that is the textbook mistake, since anything directly
reachable (or a peer inside a container network we do not otherwise control) could set it to
whatever gets it the widest bucket. It is "trust it only from the one peer that could actually
have put it there": Caddy's own private-network address. Caddy appends (never blindly forwards
verbatim) the connecting peer's own address to `X-Forwarded-For` on every request it proxies —
verified against `caddy:2.10.0`, the pinned image — so with exactly one hop between the browser
and this process, the *rightmost* entry in the header, as this process receives it, is always
the address Caddy itself observed for that connection, regardless of what a client prepended
ahead of it.
"""

from __future__ import annotations

import ipaddress
from typing import Any

_TRUSTED_PEER_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def is_trusted_proxy_peer(host: str) -> bool:
    """`True` for an RFC1918/loopback/ULA address — the private ranges a Docker compose network
    actually hands out. A peer outside them is untrusted by definition here: production has
    exactly one reverse proxy, and it is always on this network."""
    addr = _parse_ip(host)
    if addr is None:
        return False
    return any(addr in network for network in _TRUSTED_PEER_NETWORKS)


def real_client_host(*, peer_host: str | None, forwarded_for: str | None) -> str | None:
    """The address rate limiting and every other "which visitor is this" decision should use.

    `None` (from `peer_host`) only when the ASGI server genuinely reports no peer at all — a
    test transport, mostly. Falls back to `peer_host` whenever `forwarded_for` is absent, the
    peer is not trusted, or the header does not actually parse as an address list — a spoofed
    or malformed header from an untrusted or unparseable source is worth exactly as much as no
    header at all, never worth guessing at.
    """
    if peer_host is None or not is_trusted_proxy_peer(peer_host):
        return peer_host
    if not forwarded_for:
        return peer_host
    candidates = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if not candidates:
        return peer_host
    rightmost = candidates[-1]
    return rightmost if _parse_ip(rightmost) is not None else peer_host


class TrustForwardedForFromPrivatePeers:
    """ASGI middleware: rewrites `scope["client"]` to `real_client_host(...)`, so every
    downstream read of `request.client.host` — the anonymous rate limiter today, whatever reads
    it next — sees the real visitor rather than Caddy's own address, without any of them having
    to know why. A no-op wherever the immediate peer is not a trusted private address, which
    includes every test that talks to the app directly (`ASGITransport`, `httpx`'s default
    client address) and any request that somehow reached this process without going through
    Caddy at all.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer_host = client[0] if client else None
        forwarded_for: str | None = None
        for name, value in scope.get("headers") or ():
            if name == b"x-forwarded-for":
                forwarded_for = value.decode("latin-1")
                break

        resolved = real_client_host(peer_host=peer_host, forwarded_for=forwarded_for)
        if resolved != peer_host:
            port = client[1] if client else 0
            scope = {**scope, "client": (resolved, port)}

        await self.app(scope, receive, send)
