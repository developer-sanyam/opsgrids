"""The egress guard — SSRF defence for every server-side fetch of an admin-supplied URL.

An organization admin who can type `discovery_url` into a form is choosing a URL for *this
process's* container to fetch. On the docker `back` network that reaches `http://account:8000`,
`redis:6379`, the cloud metadata address, and every other service that trusts requests
originating from inside the network. That is the whole vulnerability — nothing here is exotic —
and it applies to any URL a tenant supplies that a service later fetches on its own: OIDC
discovery, the JWKS and token endpoints a discovery document names, and whatever joins that list
later.

Every check below is here because skipping it is a known bypass:

* **Scheme.** `https` only. Plain `http` is refused unless a setting that is itself refused
  outside development says otherwise (`ServiceSettings.egress_allow_insecure_http`) — see its
  docstring for why that is enforced at config load, not here.
* **Userinfo.** `https://trusted.example@169.254.169.254/` names `169.254.169.254`, not
  `trusted.example` — the part before `@` is authentication material for the host after it, and
  reading it as identifying information is a documented URL-parsing bypass.
* **Bare IP hosts are refused by default.** A real identity provider is a hostname; a literal
  address bypasses DNS review entirely and buys the operator nothing.
* **Address class**, checked against the address actually resolved rather than the literal in
  the URL: loopback, RFC1918 and RFC6598 (carrier-grade NAT) private space, link-local (which is
  where `169.254.169.254` — most clouds' metadata endpoint — lives), unique-local and link-local
  IPv6, multicast, unspecified, and the IPv4-mapped IPv6 form of any of those
  (`::ffff:127.0.0.1`), which is a documented bypass of filters that only inspect the outer
  address family.
* **The connection is pinned to the address that was checked.** Checking a hostname is not
  checking what it resolves to, and DNS is not stable between the check and the connect that
  follows it a moment later — a name with a short TTL can answer a public address for the first
  and a private one for the second (DNS rebinding). So the host is resolved exactly once here,
  the result is validated, and the real HTTP connection is forced to that literal address —
  never handed the hostname again, which is the only way a second resolution could happen. The
  `Host` header and the TLS SNI still carry the real hostname, so virtual hosting and certificate
  validation both keep working; only the socket target is pinned.
* **No redirects.** A 3xx here is refused rather than followed, because following one hands
  whatever the destination is — credentials in query strings, form bodies — to a URL that never
  passed any of the checks above.
* **A response size cap and a short timeout**, because this fetch runs inside a request a person
  is waiting on, and an upstream that never stops sending must not be a way to hold the
  connection, or the process's memory, open indefinitely.

**Fails closed.** A host with no addresses, a host whose addresses are a mix of allowed and
disallowed, a redirect, a response that keeps growing — every ambiguity here is refused rather
than guessed at.

No network in tests: both DNS resolution and the transport underneath the pin are constructor
arguments, so a caller's test suite substitutes a stub resolver and an `httpx.MockTransport`
without either ever touching a socket, while the pinning logic itself — the part a mock transport
cannot exercise by construction — has its own tests in this library.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from platform_core.logging import get_logger

log = get_logger("platform_core.egress")

#: `100.64.0.0/10` — RFC 6598 carrier-grade NAT space. Not part of Python's `is_private`
#: (checked directly below, unlike the ranges that are).
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

#: `64:ff9b::/96` — RFC 6052 NAT64 well-known prefix; the low 32 bits are an IPv4 address.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

#: Resolves a hostname to the literal addresses it answers to. A `Callable` rather than a
#: `Protocol` so a test can pass a plain async function.
Resolver = Callable[[str], Awaitable[list[str]]]


class EgressBlockedError(Exception):
    """A URL, or the address it resolved to, failed the egress guard.

    One exception for every reason — bad scheme, userinfo, an unresolvable host, a disallowed
    address, a redirect, an oversized response. A caller two layers up (a Pydantic validator, an
    `IdpClient`) needs exactly one thing to catch and translate into its own vocabulary; the
    specific reason belongs in the log this module writes, not in a message that reaches an
    administrator who may be probing the platform's internal network rather than misconfiguring
    their own.
    """


@dataclass(frozen=True, slots=True)
class VettedTarget:
    """A URL that passed every check, with the one address the connection is pinned to."""

    url: str
    host: str
    ip: str


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`host` as an IP address, or `None` if it names one to be looked up instead.

    `urlsplit(...).hostname` already strips the brackets a literal IPv6 host wears in a URL
    (`[::1]` -> `::1`), so nothing here has to.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_disallowed(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why `addr` may not be connected to, or `None` if it may.

    The IPv4-mapped IPv6 form (`::ffff:127.0.0.1`) is unwrapped and re-checked as its mapped
    address first — checking the outer address alone is a documented bypass of exactly this
    kind of filter.
    """
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        inner = _is_disallowed(mapped)
        return f"ipv4-mapped {inner}" if inner else None
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        # Covers 169.254.0.0/16 — the block every major cloud publishes its instance-metadata
        # endpoint from (169.254.169.254) — and the IPv6 link-local range.
        return "link-local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_private:
        # RFC1918 (IPv4), unique-local (IPv6, fc00::/7), and the IANA reserved/documentation
        # ranges — all covered by the standard library for both address families.
        return "private"
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return "carrier-grade-nat"
    if isinstance(addr, ipaddress.IPv6Address):
        # IPv6 forms that carry an IPv4 address inside them. A resolver answering with one of
        # these for a private IPv4 target is the same bypass as the mapped form above.
        embedded: list[ipaddress.IPv4Address] = []
        if addr in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
        if addr.sixtofour is not None:
            embedded.append(addr.sixtofour)
        if addr.teredo is not None:
            embedded.extend(addr.teredo)
        for inner_addr in embedded:
            inner = _is_disallowed(inner_addr)
            if inner:
                return f"embedded {inner}"
    if not addr.is_global:
        # Anything else IANA does not route publicly (reserved 240.0.0.0/4, benchmarking,
        # documentation ranges, ...). The named checks above exist for the log message; this
        # is the net under them.
        return "non-global"
    return None


def check_url_shape(
    url: str, *, allow_insecure_http: bool = False, allow_ip_literal_host: bool = False
) -> str:
    """The checks that need no network: scheme, userinfo, and whether a host is even named.

    Returns the hostname, since every caller needs it next. Split out from `EgressGuard.vet`
    so a Pydantic validator can reject an obviously bad URL at 422 without an event loop or a
    resolver — a discovery URL of `ftp://...` or one carrying `user:pass@` is refused at the
    moment it is typed, not on the next sign-in attempt.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "http":
        if not allow_insecure_http:
            raise EgressBlockedError("only https URLs are permitted")
    elif scheme != "https":
        raise EgressBlockedError(f"unsupported URL scheme {parts.scheme!r}")

    if parts.username or parts.password:
        # `https://trusted.example@169.254.169.254/` — the host after `@` is what a socket
        # connects to; the part before it is authentication material nobody asked to send.
        raise EgressBlockedError("URLs carrying userinfo are not permitted")

    host = parts.hostname
    if not host:
        raise EgressBlockedError("URL names no host")

    if not allow_ip_literal_host and _parse_ip_literal(host) is not None:
        raise EgressBlockedError("bare IP addresses are not permitted as a host")

    return host


async def _system_resolve(host: str) -> list[str]:
    """The real resolver: whatever the operating system's DNS answers.

    `getaddrinfo` runs in the default executor — the stdlib call is synchronous — so this
    costs a network round trip and a thread, never blocking the event loop.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressBlockedError(f"could not resolve {host}") from exc
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


class _PinnedTransport(httpx.AsyncBaseTransport):
    """Forces a request to the one address `EgressGuard.vet` already validated.

    The request keeps the real hostname in its `Host` header and — via the `sni_hostname`
    extension `httpx`/`httpcore` define for exactly this — in the TLS handshake, so the
    certificate is checked against the name the operator configured. Only the socket's actual
    destination is rewritten, and it is rewritten to a literal address, which the connection
    layer below never has to (and therefore cannot) resolve again. That is what makes a second,
    different DNS answer between the check and this connect (rebinding) impossible: there is no
    second lookup to give a different answer.
    """

    def __init__(self, *, host: str, ip: str, inner: httpx.AsyncBaseTransport) -> None:
        self._host = host
        self._ip = ip
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.extensions = {**request.extensions, "sni_hostname": self._host}
        request.url = request.url.copy_with(host=self._ip)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _drain_capped(response: httpx.Response, limit: int) -> bytes:
    """The response body, or a refusal — never more than `limit` bytes read into memory."""
    size = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise EgressBlockedError(f"response exceeded the {limit}-byte cap")
        chunks.append(chunk)
    return b"".join(chunks)


class EgressGuard:
    """Vets an admin-supplied URL, then fetches it — the one door this kind of fetch may use.

    `resolver` and `inner_transport` are the two seams a test needs and production never
    supplies: the default resolver asks the real operating system, and the default transport
    is a real `httpx.AsyncHTTPTransport`. A test passes a stub resolver to choose what address
    a hostname "resolves" to without a network, and an `httpx.MockTransport` to answer the
    eventual request without one either — the pinning wrapper still runs in front of it, so a
    mocked test still proves the request that was sent named the vetted address and the real
    hostname's `Host` header, not the other way round.
    """

    def __init__(
        self,
        *,
        allow_insecure_http: bool = False,
        allow_ip_literal_host: bool = False,
        resolver: Resolver | None = None,
        inner_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 8.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._allow_insecure_http = allow_insecure_http
        self._allow_ip_literal_host = allow_ip_literal_host
        self._resolver = resolver or _system_resolve
        self._inner_transport = inner_transport
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

    async def vet(self, url: str) -> VettedTarget:
        """Every check that decides whether `url` may be fetched at all.

        Raises `EgressBlockedError` and performs **no I/O beyond the DNS lookup itself** — in
        particular, a URL refused here never opens a connection to anything it named.
        """
        host = check_url_shape(
            url,
            allow_insecure_http=self._allow_insecure_http,
            allow_ip_literal_host=self._allow_ip_literal_host,
        )

        literal = _parse_ip_literal(host)
        if literal is not None:
            candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [literal]
        else:
            try:
                resolved = await self._resolver(host)
            except EgressBlockedError:
                raise
            except Exception as exc:
                raise EgressBlockedError(f"could not resolve {host}") from exc
            if not resolved:
                raise EgressBlockedError(f"{host} resolved to no addresses")
            try:
                candidates = [ipaddress.ip_address(a) for a in resolved]
            except ValueError as exc:
                raise EgressBlockedError(f"{host} resolved to an unusable address") from exc

        # Every resolved address is checked, not just the one that will be used: a name that
        # answers with a mix of a public and a non-routable address is refused outright rather
        # than trusted to only ever route the public one at connect time.
        for addr in candidates:
            reason = _is_disallowed(addr)
            if reason is not None:
                log.warning("egress.blocked", host=host, address=str(addr), reason=reason)
                raise EgressBlockedError(f"{host} resolves to a disallowed address")

        return VettedTarget(url=url, host=host, ip=str(candidates[0]))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Vet `url`, then send exactly one request to it. No redirects, ever.

        Returns a fully-materialized `httpx.Response` — the connection is closed before this
        returns, so the size cap has already been enforced and `.json()` is always safe to
        call on the result.
        """
        target = await self.vet(url)
        inner = self._inner_transport or httpx.AsyncHTTPTransport()
        transport = _PinnedTransport(host=target.host, ip=target.ip, inner=inner)
        async with httpx.AsyncClient(
            transport=transport, timeout=self._timeout, follow_redirects=False
        ) as client:
            upstream = await client.send(
                client.build_request(method, target.url, headers=headers, data=data),
                stream=True,
            )
            try:
                if 300 <= upstream.status_code < 400:
                    raise EgressBlockedError("the response was a redirect, which is refused")
                body = await _drain_capped(upstream, self._max_response_bytes)
            finally:
                await upstream.aclose()
        return httpx.Response(
            upstream.status_code,
            headers=upstream.headers,
            content=body,
            request=upstream.request,
        )


__all__ = [
    "EgressBlockedError",
    "EgressGuard",
    "Resolver",
    "VettedTarget",
    "check_url_shape",
]
