"""The gateway: verify once, resolve tenancy, gate on entitlement, proxy.

Both gateways are this module plus a route file. `app-gateway` fronts product UIs;
`admin-gateway` fronts internal, operator and partner APIs. Splitting them keeps the
operator surface from ever being one routing mistake away from public, and lets each be
scaled and rate-limited on its own terms.

The order of checks is deliberate, cheapest and most-conclusive first:

    1. route exists            — a request to nowhere is not worth authenticating
    2. token verifies          — signature, issuer, audience, expiry
    3. permission version      — a revoked grant must not survive its token
    4. product entitlement     — 402, not 403: permissions are fine, the plan is not
    5. rate limit              — keyed on the caller now that we know who they are
    6. proxy

Rate limiting comes *after* authentication so it counts against a person rather than an
address, which is what makes a shared office NAT usable. The unauthenticated paths are
limited by IP instead — the *caller's* IP (`_resolve_client`: `X-Forwarded-For`, believed only
from a configured proxy and read from the right), with a ceiling over all callers combined, one for
the credential endpoints and one for everything else public. Provider webhooks
(`GatewayPolicy.webhook_paths`) take a third route: no token, but exact path and method, no
cookie or `Authorization` forwarded, an address-and-method-scoped arrival cap, and a lockout on
*rejected* deliveries an address inside `webhook_provider_cidrs` is exempt from. Every
unauthenticated request whose body the gateway buffers gets a deadline scaled to its declared
size (`_unauth_body_deadline`) and a slot in its own traffic class's pool (`_body_slot_pools`),
so a stranger cannot hold memory by sending part of a body and stopping, and a class saturated
this way cannot deny any other.

The proxy step caps the request body (`DEFAULT_MAX_BODY_BYTES`, or a route's own override)
and enforces it against the bytes actually read. The gateway buffers a body whole before
forwarding it, so without a cap a single large POST exhausts the process before the
downstream service — whose own limit protects only itself — is ever asked its opinion.

**The gateway owns no tables.** Everything it needs is in the token or in Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import posixpath
import re
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from starlette.background import BackgroundTask

from platform_core.auth import decode_token
from platform_core.cache import (
    cache_set,
    check_rate_limit,
    current_permission_version,
    enforce_rate_limit,
    record_rate_limit_hit,
    resolve_cached,
)
from platform_core.config import ServiceSettings
from platform_core.errors import (
    NotFoundProblem,
    PayloadTooLargeProblem,
    PaymentRequiredProblem,
    ProblemError,
    RequestTimeoutProblem,
    ServiceBusyProblem,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from platform_core.internal_auth import (
    ADMIN_HOP_HEADER,
    GATEWAY_AUDIENCE,
    canonical_query,
    mint_service_token,
    sign_admin_hop,
)
from platform_core.logging import get_logger

log = get_logger("gateway")

#: Never forwarded. `content-length` is on the list because the upstream request is
#: rebuilt and a stale length truncates the body.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
        "content-length",
        "host",
    }
)

#: Identity headers a client must never be able to set. They are stripped from every
#: inbound request before the gateway writes its own — otherwise a caller could simply
#: claim to be somebody else and the internal token would vouch for it.
#:
#: `ADMIN_HOP_HEADER` belongs here too, for the same reason: a caller presenting one it made
#: up itself must not be indistinguishable from the one admin-gateway signs and writes below
#: (BLOCKER 2 of the 2026-09-24 security review). Stripping it on *every* gateway — not only
#: app-gateway — is what makes "app-gateway strips it inbound" true without a second,
#: gateway-specific list to keep in sync with this one.
SPOOFABLE = frozenset(
    {
        "x-internal-token",
        "x-user-id",
        "x-org-id",
        "x-membership-id",
        "x-subject-ref",
        "x-roles",
        "x-perms",
        "x-entitlements",
        "x-locations",
        "x-amr",
        "x-permission-version",
        "x-session-id",
        "x-audience",
        "x-org-slug",
        ADMIN_HOP_HEADER,
    }
)

#: Request headers dropped from a forwarded provider webhook (`GatewayPolicy.webhook_paths`):
#: everything that only a browser session carries. `access-control-request-*` is matched by
#: prefix in `_is_browser_credential`.
_WEBHOOK_STRIPPED_REQUEST_HEADERS = frozenset({"cookie", "authorization", "origin", "referer"})

#: What `_normalize_path` collapses a run of slashes with. Anchors nowhere on its own — a
#: *leading* doubled slash is POSIX-reserved and `posixpath.normpath` deliberately leaves it
#: alone, so this collapse has to happen before that call, not instead of it.
_COLLAPSE_SLASHES = re.compile(r"/+")


#: Ceiling on a forwarded request body, in bytes, unless a route says otherwise.
#:
#: The gateway buffers a request body whole before forwarding it (see `_forward`), so an
#: uncapped POST is a one-request memory exhaustion of the whole process — the downstream
#: service's own limit protects that service, not the platform, because the body is already
#: in gateway memory by the time the service is asked.
#:
#: 2 MiB is roughly eight times the largest legitimate JSON body on the platform (the
#: 1000-event metering ingest batch) and bounds the worst case to a few hundred megabytes
#: at realistic concurrency. Anything that genuinely needs more — an upload endpoint — says
#: so per route in `routes.json` rather than raising the ceiling for every endpoint.
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024

#: The highest a per-route override may go. A route table is data and a typo in data must
#: not be able to hand back the unbounded buffer this cap exists to remove, so an override
#: past this refuses at boot rather than at request time.
MAX_ROUTE_BODY_BYTES = 32 * 1024 * 1024


#: What a trusted-proxy entry parses to.
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


#: The only address space a trusted proxy may live in: private (RFC 1918), loopback, link-local and
#: IPv6 unique-local. A reverse proxy sits on the machine's own networks, and the header it
#: writes is believed *because of who delivered it* — so a range containing public addresses is
#: not a proxy list, it is "any host on the internet may tell me who the caller is". A
#: `/0` is the obvious way to write that, but `0.0.0.0/1` with `128.0.0.0/1`, `2000::/3` and a
#: `8.0.0.0/8` are the same mistake, which is why this is an allow-list and not a check for `/0`.
#: (Kept as two tuples because `subnet_of` refuses to compare an IPv4 network with an IPv6 one.)
_TRUSTED_PROXY_ALLOWED_V4 = tuple(
    ipaddress.IPv4Network(text)
    for text in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16")
)
_TRUSTED_PROXY_ALLOWED_V6 = tuple(
    ipaddress.IPv6Network(text) for text in ("::1/128", "fe80::/10", "fc00::/7")
)


def _within_allowed_proxy_space(network: IPNetwork) -> bool:
    if isinstance(network, ipaddress.IPv4Network):
        return any(network.subnet_of(allowed) for allowed in _TRUSTED_PROXY_ALLOWED_V4)
    return any(network.subnet_of(allowed) for allowed in _TRUSTED_PROXY_ALLOWED_V6)


def parse_trusted_proxies(raw: Iterable[str]) -> tuple[IPNetwork, ...]:
    """Parse a list of addresses/CIDRs the gateway will believe `X-Forwarded-For` from.

    Refuses at boot — a deployment artefact, the same class as `_parse_max_body_bytes` — rather
    than at request time, in both directions: a typo'd entry that silently trusted nothing would
    leave every anonymous caller sharing one bucket again (the bug this list exists to fix), and
    an entry that reaches beyond the machine's own networks would let a host on the internet name
    its own address. Every entry must be a subnet of `_TRUSTED_PROXY_ALLOWED_V4`/`_V6`; the error
    names the entry so the line in `.env` can be found.
    """
    networks: list[IPNetwork] = []
    for item in raw:
        text = item.strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError(
                f"trusted proxy {text!r} is not an IP address or a CIDR range"
            ) from exc
        if not _within_allowed_proxy_space(network):
            raise ValueError(
                f"trusted proxy {text!r} is not inside a private, loopback, link-local or "
                "unique-local range (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, "
                "::1/128, 169.254.0.0/16, fe80::/10, fc00::/7); trusting an address a host on "
                "the internet can hold lets it choose the address it is rate limited under"
            )
        networks.append(network)
    return tuple(networks)


#: The narrowest a `WEBHOOK_PROVIDER_CIDRS` entry may be: a provider's published range is public
#: by definition, so — unlike `parse_trusted_proxies` — there is no allow-list of *where* it may
#: live. The risk here runs the other way: an entry that is too *wide* silently exempts a slice of
#: the internet from the rejection lockout. `/8` (IPv4) and `/24` (IPv6) are generous enough for
#: any real provider range (Razorpay's and Meta's published blocks are far narrower) while still
#: catching the paste-the-wrong-thing case — a `/0`, a typo'd prefix length, a bare `0.0.0.0`.
_PROVIDER_CIDR_MINIMUM_PREFIX_V4 = 8
_PROVIDER_CIDR_MINIMUM_PREFIX_V6 = 24


def parse_provider_cidrs(raw: Iterable[str]) -> tuple[IPNetwork, ...]:
    """Parse `WEBHOOK_PROVIDER_CIDRS`: public ranges a provider's own deliveries are known to
    originate from, exempted from the per-address *rejection* lockout (`GatewayPolicy.
    webhook_provider_cidrs` — the arrival limit still applies to them; see `build_gateway`).

    Empty by default, deliberately: guessing at Razorpay's or Meta's current published ranges and
    shipping them in this repository would be wrong the day either provider changes them, silently
    and with nothing here to notice. Filling it in from the providers' own current documentation is
    the deploying owner's job (RUNBOOK, "Provider webhooks").

    Refuses at boot, the same class of failure as `parse_trusted_proxies`, but checks the opposite
    thing: not *where* the range may live (any public range is expected here), only *how wide* an
    exemption a single entry may buy — a `/0`, or anything wider than `_PROVIDER_CIDR_MINIMUM_
    PREFIX_V4`/`_V6`, is refused with the entry named.
    """
    networks: list[IPNetwork] = []
    for item in raw:
        text = item.strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError(
                f"webhook provider CIDR {text!r} is not an IP address or a CIDR range"
            ) from exc
        minimum = (
            _PROVIDER_CIDR_MINIMUM_PREFIX_V4
            if isinstance(network, ipaddress.IPv4Network)
            else _PROVIDER_CIDR_MINIMUM_PREFIX_V6
        )
        if network.prefixlen < minimum:
            raise ValueError(
                f"webhook provider CIDR {text!r} is wider than the /{minimum} this deployment "
                "allows — a typo this wide would exempt a slice of the internet from the "
                "rejection lockout, not just one provider's range"
            )
        networks.append(network)
    return tuple(networks)


def _parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """One address, or `None` for anything that is not one. IPv4-mapped IPv6 (`::ffff:1.2.3.4`,
    what a dual-stack listener reports for an IPv4 peer) is unmapped, so the same host is never
    two different callers."""
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _limiter_key(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """What one caller is counted under.

    An IPv4 address is one caller. An IPv6 *address* is not: an ISP hands a subscriber a /64 (or
    wider), so a client that limits by the full address can rotate through 2^64 of them and never
    meet its own bucket. The /64 is the unit an IPv6 caller cannot cheaply multiply.
    """
    if isinstance(address, ipaddress.IPv6Address):
        return f"{ipaddress.ip_network(f'{address}/64', strict=False).network_address}/64"
    return str(address)


def _resolve_client(
    request: Request, trusted_proxies: tuple[IPNetwork, ...]
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | str | None:
    """Who called: an address, the peer's own text when that is not an address (an in-process
    test client reports "testclient"), or `None` when the request carries no peer at all.

    Behind a reverse proxy the socket peer is the *proxy* — Caddy's container address on the
    compose network — not the caller, and limiting on it collapses every anonymous caller in
    the world into one bucket: the per-address limit is then a global one, and it is the only
    brake there is on password and one-time-code guessing before a per-account lockout has
    anything to count.

    So `X-Forwarded-For` is believed, but **only when the peer that delivered the request is a
    proxy this deployment named** (`trusted_proxies`). From anyone else it is an attacker-chosen
    string and is ignored: a client that could set it could pick a fresh bucket per request.
    Once the peer is trusted the header is read **from the right**. Each proxy appends the
    address of whoever it received the request from, so the rightmost entries were written by our
    own infrastructure and everything to the left of the first address that is *not* ours is
    whatever the client claimed. The first untrusted address from the right is the caller.

    Every ambiguous case falls back to the peer, which is the conservative answer (shared, so
    stricter, never spoofable): no header, an entry that is not an address (a proxy that appends
    writes one; garbage on the right means something did not), or a chain made only of trusted
    proxies. The empty default trusts nobody, which is the right posture for development and for
    any deployment that has not said where its proxy is.

    **Trusted means the whole named range, not one proxy.** Any host that can connect from inside
    it may name a caller, so on the compose networks — where the default is the RFC 1918 ranges —
    that is every container on `front` and `back`. The range is narrowed to Caddy's own address in
    the runbook ("Narrowing the trusted proxy set"); the default is a deliberate trade against a
    network whose subnet is not fixed.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return None
    peer_address = _parse_ip(peer)
    if peer_address is None:
        return peer
    if not trusted_proxies or not any(peer_address in network for network in trusted_proxies):
        return peer_address

    entries = [
        entry.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for entry in header.split(",")
    ]
    for entry in reversed(entries):
        address = _parse_ip(entry)
        if address is None:
            return peer_address
        if not any(address in network for network in trusted_proxies):
            return address
    return peer_address


def client_limiter_key(request: Request, trusted_proxies: tuple[IPNetwork, ...] = ()) -> str:
    """The address an unauthenticated request is rate limited under — see `_resolve_client`.
    An IPv6 caller is counted by its /64, everything else by its address."""
    resolved = _resolve_client(request, trusted_proxies)
    if resolved is None:
        return "anonymous"
    if isinstance(resolved, str):
        return resolved
    return _limiter_key(resolved)


def client_address(request: Request, trusted_proxies: tuple[IPNetwork, ...]) -> str | None:
    """The single address to forward as `X-Forwarded-For` — the same caller `client_limiter_key`
    counts, unbucketed. `None` when the request carries no peer at all."""
    resolved = _resolve_client(request, trusted_proxies)
    return None if resolved is None else str(resolved)


#: How long a *fresh* re-resolution (see `PermissionResolver.resolve`'s `fresh` branch) is
#: cached when it still finds no code for the product that triggered it.
#:
#: `OutboxRelay.max_idle_interval` (`platform_core.events`) defaults to 5.0s — the worst
#: case between a grant's outbox row being written and an idle relay noticing it, before
#: the account-service consumer that installs the grant has even run. A few seconds keeps
#: an owner whose grant is genuinely still in flight within about one extra relay cycle of
#: success once this key is re-checked, while an ordinary member who was simply never given
#: a role in this product pays for one resolver round trip every few seconds instead of
#: holding a stale-but-plausible-looking negative for the full 60s a normal cache entry
#: gets. Short enough to bound both the propagating owner's window and the unpermitted
#: member's retry load; long enough that a member hammering the endpoint does not turn into
#: a query per request.
GRANT_PROPAGATION_RETRY_TTL = 5


class TokenStaleProblem(ProblemError):
    """The token predates a change to the caller's grants.

    401 with a distinct type so the client refreshes rather than showing a login screen —
    the credential is fine, the snapshot inside it is not.
    """

    status_code = 401
    problem_type = "token-stale"
    title = "Token out of date"


@dataclass(frozen=True, slots=True)
class Route:
    prefix: str
    upstream: str
    #: Which product must be activated. `None` for platform routes every customer has.
    product: str | None = None
    #: Only reachable by OpsGrids staff. Used by the operator console.
    staff_only: bool = False
    #: Refused with the same 404 an unmatched path gets — never proxied, regardless of any
    #: bearer token. For a sub-path that a broader prefix on the *same* gateway would
    #: otherwise happily forward, and that this gateway specifically must not: app-gateway's
    #: `/api/v1/auth/operator` entry over the shorter `/api/v1/auth` one is the case this
    #: exists for (MAJOR 3 of the security review — operator sign-in was reachable from the
    #: public product gateway with any ordinary tenant bearer token, because nothing about
    #: `/api/v1/auth/operator/login` requires the caller's *own* token to be staff-shaped; it
    #: reads staff credentials from the request body). `RouteTable.resolve` picks the longest
    #: matching prefix, so a `blocked` entry here wins over the shorter one and is checked
    #: before the token is even decoded — the same "refuse before doing any other work" shape
    #: `_has_internal_segment` already uses for `/internal/` paths, just per-route instead of
    #: platform-wide, because *which* paths must be blocked is a per-gateway decision
    #: (admin-gateway wants exactly these reachable).
    blocked: bool = False
    #: Per-route request-body ceiling in bytes. `None` means the gateway's default. An
    #: upload route legitimately needs more than a JSON POST; nothing else should.
    max_body_bytes: int | None = None


def _parse_max_body_bytes(item: dict[str, object]) -> int | None:
    """Read and bound a route's `max_body_bytes`, refusing nonsense at boot.

    A bad value here is a deployment artefact, not a request, so the loud failure belongs
    at startup — a route table that silently parsed `"5mb"` as absent would hand back the
    unbounded buffer on exactly the route that asked to be different.
    """
    raw = item.get("max_body_bytes")
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise ValueError(f"route {item.get('prefix')!r}: max_body_bytes must be a positive integer")
    if raw > MAX_ROUTE_BODY_BYTES:
        raise ValueError(
            f"route {item.get('prefix')!r}: max_body_bytes {raw} exceeds the "
            f"{MAX_ROUTE_BODY_BYTES} byte ceiling the gateway will buffer"
        )
    return raw


class RouteTable:
    """Path prefix → upstream, resolved longest-prefix-first.

    Loaded from a file rather than compiled in. A hardcoded list means every product
    launch edits the core gateway, which is exactly the coupling the platform exists to
    avoid — see the "adding product N" checklist.
    """

    def __init__(self, routes: list[Route]) -> None:
        # Longest first, so `/api/v1/employees/salary` can belong to a different service
        # than `/api/v1/employees` without the shorter prefix swallowing it.
        self._routes = sorted(routes, key=lambda r: len(r.prefix), reverse=True)

    @classmethod
    def from_file(cls, path: str | Path) -> RouteTable:
        raw = json.loads(Path(path).read_text())
        return cls(
            [
                Route(
                    prefix=item["prefix"],
                    upstream=item["upstream"].rstrip("/"),
                    product=item.get("product"),
                    staff_only=bool(item.get("staff_only", False)),
                    blocked=bool(item.get("blocked", False)),
                    max_body_bytes=_parse_max_body_bytes(item),
                )
                for item in raw["routes"]
            ]
        )

    def resolve(self, path: str) -> Route | None:
        return next((r for r in self._routes if path.startswith(r.prefix)), None)

    def __len__(self) -> int:
        return len(self._routes)

    def __iter__(self) -> Iterator[Route]:
        return iter(self._routes)

    @property
    def products(self) -> set[str]:
        return {r.product for r in self._routes if r.product}


@dataclass(frozen=True, slots=True)
class GatewayPolicy:
    """What distinguishes one gateway from the other."""

    #: The `aud` value tokens must carry to be accepted here.
    audience: str
    #: Reachable without a token, by exact path.
    public_paths: frozenset[str]
    #: Reachable without a token, by prefix — the one shape `public_paths` cannot express (see
    #: its own module note in `app-gateway/app/main.py`): a path that carries a variable
    #: segment, such as a per-recipient token. Each entry **must** end in `/`, so a prefix names
    #: a real path boundary rather than a bare string match — `"/api/v1/engage/unsubscribe/"`
    #: matches `.../unsubscribe/abc` and not a hypothetical `.../unsubscribe-all`. Checked
    #: against the same normalised path routing itself uses (`_normalize_path`), never the raw
    #: one, so a `..`/`//`-laden path dressed up to look like a member of a public prefix is
    #: judged by where it actually resolves rather than by what it merely contains.
    public_path_prefixes: frozenset[str] = frozenset()
    #: Requests per minute, per caller.
    rate_limit: int = 300
    #: Unauthenticated callers are limited harder, and by address.
    anonymous_rate_limit: int = 60
    #: Refuse anyone who is not OpsGrids staff. The operator console sets this.
    staff_only: bool = False
    upstream_timeout: float = 30.0
    #: How long to keep retrying an upstream that refuses the connection before answering 503.
    #: A deploy recreates a container, and for those seconds its name resolves to nothing or
    #: its port refuses; without this every request in that window — a sign-in included —
    #: failed. A refused connection never reached the service, so retrying is safe for any
    #: method: the body is already buffered and nothing was executed.
    upstream_connect_grace: float = 20.0
    #: Request bodies larger than this are refused with 413 before being read into memory.
    #: A route may raise its own ceiling; see `Route.max_body_bytes`.
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    #: Sign every proxied request with the admin-gateway hop signature (`sign_admin_hop`).
    #: M1(b) of the third-round security review: this used to key off "`ADMIN_GATEWAY_HOP_KEY`
    #: is set in this process's settings", which made *any* gateway that happened to see the
    #: key — every service reads the same `.env` in development — a signer. Now a property of
    #: the gateway's own policy, set `True` in exactly one place (admin-gateway's `main.py`),
    #: so holding the key is necessary but no longer sufficient.
    sign_admin_hop: bool = False
    #: Provider webhooks: exact path -> the HTTP methods that are public on it.
    #:
    #: A separate list from `public_paths`, not a subset of it, because a webhook is a different
    #: kind of anonymous. It carries no bearer token — the provider signs the body and the
    #: owning service verifies that signature over the raw bytes — so it is not a person, has
    #: none of a browser's credentials (see `_WEBHOOK_STRIPPED_REQUEST_HEADERS`), is limited only
    #: by its own *rejected* deliveries (`webhook_failure_limit`) and is public **per method**:
    #: Meta's WhatsApp handshake is a GET and its deliveries are POSTs, Razorpay only POSTs, and
    #: a DELETE to either falls through to the bearer check like any other unlisted request.
    #: Exact-matched against the normalised path, so `/api/v1/webhooks/whatsapp/../employees`
    #: is judged as `/api/v1/employees`, which is not here.
    #:
    #: The fields from here down are keyword-only in practice and last on purpose: they were
    #: added after the ones above, and an existing positional construction must keep meaning what
    #: it meant.
    webhook_paths: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: A ceiling on the **credential** endpoints together (`credential_paths`), per minute, on top
    #: of the per-address limit (`anonymous_rate_limit`); `0` means none. The per-address limit
    #: stops one address; it does nothing about a thousand of them, each politely under its own
    #: limit, guessing passwords and one-time codes. Checked *after* the per-address one, so a
    #: single address hammering the gateway spends at most its own allowance of this and cannot
    #: exhaust it alone. Authenticated traffic never touches it.
    anonymous_global_rate_limit: int = 0
    #: The same, for every *other* unauthenticated path (`content`: a published catalogue, an
    #: unsubscribe link, the token refresh) — a separate bucket, and much larger, because
    #: nothing there can be guessed and a busy day of real buyers is legitimate traffic. One shared
    #: ceiling meant a crowd of storefront visitors could lock every merchant out of signing in,
    #: and a sign-in flood could take every catalogue offline. `0` means none.
    anonymous_content_global_rate_limit: int = 0
    #: The exact public paths that take a credential (sign-in, one-time codes, password reset,
    #: sign-up, accepting an invitation) and so count against `anonymous_global_rate_limit`;
    #: every other public path counts against `anonymous_content_global_rate_limit`. Declared by
    #: the gateway service, which also tests that every public path is classified one way or the
    #: other, so a new public path is a decision and not an accident.
    credential_paths: frozenset[str] = frozenset()
    #: Rejected webhook deliveries per (method, path, address) per minute. The bucket is *read*
    #: before a delivery is forwarded and *spent* only when the service answers 400, 401 or 403,
    #: or the gateway itself refuses the body with 408 or 413 (a bad signature, a wrong verify
    #: token, a body that never arrived, one that was too large). A delivery that succeeds, that
    #: fails on our side (5xx, a timeout), or that the service answers 422 (an authentic event it
    #: cannot yet attribute — a case the owning service now resolves as 200 on retry, not this
    #: gateway's business to punish) costs nothing. Keyed by **method as well as path**, so Meta's
    #: `GET` handshake failing does not spend the same bucket as its `POST` deliveries — a third
    #: party can point their own Meta app or Razorpay webhook at our URL and make either provider
    #: send a failing request from a shared egress address otherwise. 600 (ten times round one's
    #: figure) because that address space is genuinely shared: a provider's egress IP carries
    #: every merchant's traffic, not just ours, so the bar for "this address is hostile" has to be
    #: higher than "any other unauthenticated caller". `webhook_provider_cidrs` exempts a known
    #: provider range from this bucket entirely; `webhook_arrival_limit` bounds total traffic
    #: instead, on the same per-address terms, so nothing here reopens the shared-bucket
    #: starvation round one closed.
    webhook_failure_limit: int = 600
    #: Every request to a webhook path, per (method, path, address), regardless of outcome — the
    #: total-traffic cap the failure bucket does not provide, restored per-address only (never a
    #: ceiling shared across addresses, which is what let five hostile addresses starve every
    #: other one in round one). Generous: a provider's real burst is nothing like this.
    webhook_arrival_limit: int = 600
    #: Public ranges a provider's own deliveries are known to originate from
    #: (`parse_provider_cidrs`) — addresses inside are **never** subject to `webhook_failure_
    #: limit`'s lockout, because that address is shared with every other merchant using the same
    #: provider and a third party pointing their own webhook at our URL should not be able to
    #: burn it. `webhook_arrival_limit` still applies to them. Empty by default: nobody's ranges
    #: are guessed into this repository (RUNBOOK, "Provider webhooks").
    webhook_provider_cidrs: tuple[IPNetwork, ...] = ()
    #: Proxies whose `X-Forwarded-For` this gateway believes; see `_resolve_client`. Empty (the
    #: default) believes nobody, so the rate limit keys on the socket peer. When set, every
    #: request the gateway forwards carries exactly one `X-Forwarded-For`: the resolved caller.
    trusted_proxies: tuple[IPNetwork, ...] = ()
    #: The *ceiling* on how long an unauthenticated request may take to send its body — see
    #: `_unauth_body_deadline` for the actual, usually shorter, per-request figure. The gateway
    #: buffers a body whole, and a client that sends part of one and stops holds that memory for
    #: as long as the connection lives; without this a handful of connections held the container's
    #: memory limit. Authenticated requests are not subject to it — a slow document upload is
    #: legitimate.
    unauth_body_read_timeout: float = 10.0
    #: How many unauthenticated requests one address may have in flight *in one traffic class* at
    #: once, and how many the process will hold *for that class* in total, before answering 503
    #: with `Retry-After: 5`. A slot is held from the moment the request is accepted until its
    #: response has been sent, because the body stays in memory that long.
    #:
    #: **Per class, not one shared pool.** `build_gateway` opens one independent `_BodySlots` per
    #: traffic class — one per declared webhook path, one for the credential public paths, one for
    #: the content ones — each sized by these same two numbers. A single shared pool meant eight
    #: addresses stalling four bodies apiece (32, exactly the old global cap) denied Razorpay,
    #: WhatsApp, sign-in, session refresh and the storefront catalogue all at once, with no
    #: attacker required: the same 32 requests arrive for free during an ordinary auth redeploy,
    #: while `_send_riding_out_restarts` holds them open waiting for the container to come back.
    #: Splitting the pool cannot make one class's own saturation harmless to *itself* — that is
    #: what a bounded pool means — but it stops it from being harmless to every *other* class too.
    #: Buffered memory is therefore bounded per class at `slots * that class's largest body cap`,
    #: not by one number shared across everything unauthenticated.
    unauth_body_slots_per_address: int = 4
    unauth_body_slots_global: int = 32

    def __post_init__(self) -> None:
        # A prefix without a trailing slash is a bug waiting to widen: refused at construction
        # (a deployment-time failure, the same class as `_parse_max_body_bytes`'s) rather than
        # silently matching more than whoever wrote it meant.
        for prefix in self.public_path_prefixes:
            if not prefix.endswith("/"):
                raise ValueError(f"public path prefix {prefix!r} must end with '/'")
        # A webhook entry is matched against a normalised path, which never ends in `/` and
        # never carries a query, so an entry that does either could never match — and one with
        # no methods, or a lower-case one, would be public for nothing or fail to match GET.
        for path, methods in self.webhook_paths.items():
            if not path.startswith("/api/") or path.endswith("/") or "?" in path:
                raise ValueError(
                    f"webhook path {path!r} must be an exact /api/... path with no trailing "
                    "slash and no query"
                )
            if not methods or any(m != m.upper() or not m.isalpha() for m in methods):
                raise ValueError(f"webhook path {path!r} needs upper-case HTTP method names")
        # A limit of zero (or below) is not "off", it is "refuse everyone": the counter is
        # incremented first and `count > 0` is true for the first request. Where `0` does mean
        # off — the shared ceilings, documented above — it is allowed; everywhere else it is a
        # configuration mistake and stops boot rather than the first request.
        for name in (
            "rate_limit",
            "anonymous_rate_limit",
            "webhook_failure_limit",
            "webhook_arrival_limit",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1, got {getattr(self, name)}")
        for name in ("anonymous_global_rate_limit", "anonymous_content_global_rate_limit"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be 0 (off) or more, got {getattr(self, name)}")
        for name in ("unauth_body_slots_per_address", "unauth_body_slots_global"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1, got {getattr(self, name)}")
        if self.unauth_body_read_timeout <= 0:
            raise ValueError(
                f"unauth_body_read_timeout must be positive, got {self.unauth_body_read_timeout}"
            )
        not_public = sorted(self.credential_paths - self.public_paths)
        if not_public:
            raise ValueError(f"credential paths that are not public paths: {not_public}")


DEFAULT_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/health/ready",
        "/docs",
        "/openapi.json",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/select-org",
        "/api/v1/auth/mfa/verify",
        # Single sign-on. Public for the same reason `login` is — they are the routes that
        # *produce* a token, so requiring one is a circular definition. The callback is
        # public by necessity as well: an identity provider redirects a browser to it with
        # nothing of ours attached but the `state` this service issued, which is exactly what
        # it validates. The configuration routes beside them are **not** here, because they
        # are ordinary permission-guarded tenant endpoints.
        "/api/v1/auth/sso/start",
        "/api/v1/auth/sso/callback",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/password/reset",
        # Provider webhooks (Meta's WhatsApp, Razorpay) are deliberately **not** here: they are
        # `GatewayPolicy.webhook_paths`, declared by the gateway that fronts them. An entry
        # `/webhooks/whatsapp` used to live in this set and could never match — this proxy only
        # serves `/api/{path}`, so a path without the `/api/` prefix never reaches a public-path
        # check — which made the WhatsApp webhook look public while the real one, at
        # `/api/v1/webhooks/whatsapp`, was refused with a 401 for want of a bearer token.
        "/api/v1/invitations/accept",
    }
)


class PermissionResolver:
    """Turns a membership into its `code:SCOPE` grants, cached against the version.

    The cache key carries the permission version, so a grant change invalidates every
    entry for that organization **by construction** rather than by waiting for a TTL.
    """

    def __init__(
        self, account_url: str, settings: ServiceSettings, *, timeout: float = 5.0
    ) -> None:
        self._client = httpx.AsyncClient(base_url=account_url.rstrip("/"), timeout=timeout)
        self._settings = settings

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve(
        self,
        membership_id: str,
        org_id: str,
        version: int,
        *,
        fresh: bool = False,
        product: str | None = None,
    ) -> list[str]:
        """Resolve a membership's grants, normally through the cache.

        `fresh=True` bypasses the cache *read* — used only by the gateway's
        activation-race re-check (see the call site in `build_gateway`) — and always
        talks to account-service, the same endpoint an ordinary resolution would have hit
        on a miss. It cannot hand back anything the membership does not actually hold; it
        only refuses to trust a snapshot the caller has a specific, detectable reason to
        distrust. The fresh result is still written back to the same cache key so every
        following request benefits rather than repeating this round trip, with a TTL
        chosen by whether `product` (when given) is now present among the grants — see
        `GRANT_PROPAGATION_RETRY_TTL`.
        """

        async def load() -> list[str]:
            response = await self._client.get(
                f"/internal/v1/memberships/{membership_id}/grants",
                headers={
                    # Addressed to account-service and good for seconds, rather than the
                    # everlasting shared secret this replaced.
                    "X-Internal-Token": mint_service_token(self._settings, audience="account"),
                    "X-Calling-Service": "gateway",
                },
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return list(response.json().get("grants", []))

        key = f"authz:{org_id}:{membership_id}:{version}"
        try:
            if fresh:
                value = await load()
                landed = product is not None and any(g.startswith(f"{product}.") for g in value)
                # Once the grant has actually landed this fresh read *is* the truth, so it
                # earns the same 60s every other resolution gets. If it still hasn't landed,
                # caching that negative for 60s would recreate the exact bug this bypass
                # exists to fix — the next unlucky request would sit behind it for up to a
                # minute instead of seconds. See `GRANT_PROPAGATION_RETRY_TTL` for why a few
                # seconds is the right size for that half.
                await cache_set(
                    key, json.dumps(value), ttl=60 if landed else GRANT_PROPAGATION_RETRY_TTL
                )
                return value
            return list(await resolve_cached(key, load, ttl=60))
        except httpx.HTTPError as exc:
            # Failing open here would hand the caller whatever permissions the downstream
            # service defaults to. Refuse instead.
            raise UpstreamUnavailableProblem(
                "Cannot determine your permissions right now. Please retry shortly."
            ) from exc


#: What a service answers to a webhook it will not accept: 400 (bad or missing signature), 401
#: (the same, or no secret configured) and 403 (Meta's verify-token handshake with the wrong
#: token). These, and only these *service* answers, are spent from an address's bucket of
#: rejected deliveries — the gateway's own 408 (body never arrived) and 413 (body too large) are
#: spent too, but they never produce a `Response` to check here (see the `except` clause in
#: `build_gateway`'s webhook branch) — they raise before one exists. 2xx is a delivery that worked;
#: 5xx and timeouts are our failure, not the caller's, and must never lock a provider out.
#:
#: **422 is deliberately not here.** It used to be — a body an authentic provider sends but this
#: platform cannot yet attribute — and every provider retry of a real, signed event re-spent the
#: same bucket a third party's forgeries would. The owning service now answers those 200 instead
#: (so the provider stops retrying), but the exclusion holds regardless of that: a signature
#: passed, so the request is not what this lockout exists to stop.
_WEBHOOK_REJECTION_STATUSES = frozenset({400, 401, 403})


class _BodySlots:
    """How many unauthenticated requests are in flight at once — per address and in total.

    The gateway buffers a request body whole before forwarding it, and an unauthenticated caller
    chooses how long that takes: half a body sent and then silence holds that memory for as long
    as the connection stays open. Forty such connections from one address held 122 MiB of a 256
    MiB container in half a second, and a webhook path's larger body cap made it fifteen times
    worse. A request takes a slot when it is accepted and gives it back when its response is on
    its way — because the body stays in memory that long, not just while it is being read — and a
    request that finds none is answered `503` with `Retry-After: 5` before a byte of its body is
    touched. Providers retry a 503; the point is that memory is bounded by
    `slots * max_body_bytes` whatever any caller does.

    Single-threaded by construction: the check and the increment share one synchronous block, so
    no lock is needed, and the release is in a `finally`, so a client that disconnects, a body over
    its cap, a timeout and any other exception all give the slot back.
    """

    def __init__(self, per_address: int, total: int) -> None:
        self._per_address = per_address
        self._total_limit = total
        self._held: dict[str, int] = {}
        self._total = 0

    @property
    def in_flight(self) -> int:
        """Slots held right now, across every address."""
        return self._total

    @contextlib.asynccontextmanager
    async def hold(self, address: str) -> AsyncIterator[None]:
        if self._total >= self._total_limit or self._held.get(address, 0) >= self._per_address:
            log.warning(
                "gateway.body_slots_full",
                held_total=self._total,
                held_by_address=self._held.get(address, 0),
            )
            raise ServiceBusyProblem(
                "Too many requests are being processed right now. Please retry shortly.",
                headers={"Retry-After": "5"},
            )
        self._total += 1
        self._held[address] = self._held.get(address, 0) + 1
        try:
            yield
        finally:
            self._total -= 1
            remaining = self._held[address] - 1
            if remaining:
                self._held[address] = remaining
            else:
                del self._held[address]


def _body_slot_pools(policy: GatewayPolicy) -> dict[str, _BodySlots]:
    """One independent `_BodySlots` per traffic class, all sized by the same two policy numbers.

    The classes are the ones `build_gateway`'s proxy function itself sorts every unauthenticated
    request into: `"credential"`, `"content"`, and one `f"webhook:{path}"` per entry in
    `policy.webhook_paths`. Precomputed here, once, at startup — the set of webhook paths is
    static for the life of the process, so there is nothing to create lazily and no lock needed
    around a dict a concurrent request might otherwise be populating.
    """
    classes = {"credential", "content", *(f"webhook:{path}" for path in policy.webhook_paths)}
    return {
        name: _BodySlots(policy.unauth_body_slots_per_address, policy.unauth_body_slots_global)
        for name in classes
    }


def _in_any_network(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, networks: tuple[IPNetwork, ...]
) -> bool:
    return any(address in network for network in networks)


def build_gateway(
    app: FastAPI,
    *,
    settings: ServiceSettings,
    routes: RouteTable,
    policy: GatewayPolicy,
    resolver: PermissionResolver | None = None,
) -> None:
    """Mount the catch-all proxy on `app`."""

    # One independent pool per traffic class (see `_body_slot_pools`), so a stalled or saturated
    # class cannot deny another. Readable by the tests and by anything that wants to export it as
    # a gauge: how many unauthenticated requests are in flight right now, per class.
    body_slots = _body_slot_pools(policy)
    app.state.body_slots = body_slots

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def proxy(path: str, request: Request) -> Response:
        # Normalised once, here, and used for every decision this function makes — which route,
        # which permission gate, whether the path is public — and for what is actually
        # forwarded (`_forward` takes this, not `request.url.path`, for exactly this reason).
        # ASGI hands this handler `scope["path"]` percent-decoded but *not* otherwise
        # normalised: `..` segments, doubled slashes and the rest travel through exactly as the
        # client sent them (proven in this module's tests, not assumed). Deciding "is this
        # public" against the raw text and then forwarding a different, normalised path is
        # exactly how `/api/v1/engage/unsubscribe/x/../../senders` — publicly reachable by
        # prefix, but *routed* to `/api/v1/engage/senders` once httpx builds the outbound
        # request the same way `_normalize_path` does — would have bypassed the entitlement and
        # permission gates on `senders` entirely.
        full_path = _normalize_path(f"/api/{path}")

        if _has_internal_segment(full_path):
            # Defence in depth, not the only control: every service mounts its own internal
            # router at `/internal/v1` (never under `/api`), so no route table entry should
            # ever match one of these anyway. This exists for the request that reaches here
            # through a path a route table author did not anticipate — a typo'd prefix, a
            # `..` segment — rather than to be the thing that makes `/internal/` unreachable.
            # 404, the same answer a route that does not exist gives, so this refusal is not
            # itself a way to confirm an internal path exists.
            log.warning("gateway.internal_path_refused", path=full_path)
            raise NotFoundProblem(f"No route for {full_path}")

        route = routes.resolve(full_path)
        if route is None:
            raise NotFoundProblem(f"No route for {full_path}")
        if route.blocked:
            # Refused before the token is even decoded — a blocked route carries no bearer
            # token that would make it acceptable, so there is nothing to check. The same 404
            # an unmatched path gives, for the same reason `staff_only` answers 404: this
            # gateway is not confirming which paths it deliberately does not serve.
            raise NotFoundProblem(f"No route for {full_path}")

        # Start from the client's headers minus anything hop-by-hop or spoofable, then
        # write our own identity headers on top.
        #
        # Keys are lower-cased first. HTTP header names are case-insensitive but a dict is
        # not, so the client's `x-correlation-id` and our `X-Correlation-ID` survived as
        # two entries and the downstream service received "trace-me, trace-me". Every
        # header written below is therefore lower-case, so writing one always *replaces*
        # the caller's value rather than appending to it.
        headers = {
            k.lower(): v
            for k, v in request.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() not in SPOOFABLE
        }
        headers["x-request-id"] = request.state.request_id
        headers["x-correlation-id"] = request.state.correlation_id
        if policy.trusted_proxies:
            # One address, the one this gateway resolved and rate limits on. Without this the
            # service receives whatever chain arrived — and `platform_core.audit` reads the
            # *first* entry of it while the limiter reads the *last untrusted* one, so a caller
            # could be limited as one address and audited as another. Only a gateway that has been
            # told which proxy to believe does it: admin-gateway trusts none and keeps forwarding
            # the chain it was given, which is what its own audit rows read.
            resolved = client_address(request, policy.trusted_proxies)
            if resolved is None:
                headers.pop("x-forwarded-for", None)
            else:
                headers["x-forwarded-for"] = resolved

        webhook_methods = policy.webhook_paths.get(full_path)
        if webhook_methods is not None and request.method in webhook_methods:
            # A provider webhook: no bearer token, by design — the provider signs the body and
            # the owning service verifies that signature over the raw bytes we forward
            # untouched. Judged on the same normalised `full_path` as everything above, exactly
            # and per method (see `GatewayPolicy.webhook_paths`).
            #
            # Two independent limits, both per address and keyed by **method as well as path**
            # (`hook-fail`/`hook-arrival:<method>:<path>:<address>`) — method-keying is what stops
            # Meta's `GET` handshake failing from spending the same bucket as its `POST`
            # deliveries, which mattered once addresses could be shared: a third party can point
            # their own Meta app or Razorpay webhook at our URL and make either provider send a
            # failing request from an egress address our genuine merchants also deliver from.
            #
            # **`hook-fail`: only rejections, and only the ones this address is actually
            # responsible for.** Before the delivery is forwarded, the address's bucket of
            # *rejected* deliveries on this method and path is read (nobody has spent it yet);
            # after the service answers, one is added only for a rejection (`_WEBHOOK_REJECTION_
            # STATUSES`, plus the gateway's own 408/413 — the `except` below). A delivery that
            # succeeds, that fails on our side, or that the service answers 422 (an authentic
            # event it cannot yet attribute) costs nothing. Skipped entirely for an address inside
            # `webhook_provider_cidrs`: that range is shared with every other merchant on the same
            # provider, and one third party's forgeries must not be able to burn it.
            #
            # **`hook-arrival`: every request, no exemption, never shared across addresses.**
            # Round one removed a bucket exactly this shape because it was shared *across*
            # addresses — a handful of hostile ones could fill it and refuse a genuine one. This
            # one is per address only, so it cannot repeat that: it bounds one address's total
            # traffic on this method and path without ever being able to starve another.
            caller = client_limiter_key(request, policy.trusted_proxies)
            caller_address = _resolve_client(request, policy.trusted_proxies)
            exempt = isinstance(
                caller_address, ipaddress.IPv4Address | ipaddress.IPv6Address
            ) and _in_any_network(caller_address, policy.webhook_provider_cidrs)

            bucket_prefix = f"{request.method}:{full_path}:{caller}"
            rejected = f"hook-fail:{bucket_prefix}"
            await enforce_rate_limit(
                f"hook-arrival:{bucket_prefix}",
                limit=policy.webhook_arrival_limit,
                detail="Too many requests from this address. Retry shortly.",
            )
            if not exempt:
                await check_rate_limit(
                    rejected,
                    limit=policy.webhook_failure_limit,
                    detail="Too many rejected deliveries from this address. Retry shortly.",
                )
            # A provider is a server, not a browser. None of these mean anything to it and none
            # should reach the service that verifies its signature: a session cookie or bearer
            # token that rode along would be a credential handed to a route that ignores it, and
            # an `Origin` would invite the *service's* CORS middleware to answer as though this
            # were a page. (This gateway's own CORS middleware is not taken out of the path — it
            # is `create_app`'s, shared by every service — so a request with an allowed
            # first-party `Origin` is still answered with that origin's headers on the way out;
            # providers send none.) No `x-org-slug` either — the host says nothing about which
            # tenant a provider is calling for, and the body's own routing key does.
            for name in [n for n in headers if _is_browser_credential(n)]:
                del headers[name]
            # Its own pool (`_body_slot_pools`): a stalled body on this path cannot deny a
            # different webhook path, sign-in, session refresh or the storefront catalogue.
            pool = body_slots[f"webhook:{full_path}"]
            try:
                async with pool.hold(caller):
                    response = await _forward(
                        request,
                        route,
                        headers,
                        policy,
                        full_path,
                        settings,
                        webhook=True,
                        unauthenticated=True,
                    )
            except (PayloadTooLargeProblem, RequestTimeoutProblem):
                # The gateway's own refusal, before a `Response` exists to read a status from —
                # counted as a rejection like the service's 400/401/403, because it is one: a
                # body that never arrived or was declared too large is not a delivery this
                # address gets to keep retrying for free.
                if not exempt:
                    await record_rate_limit_hit(rejected)
                raise
            if not exempt and response.status_code in _WEBHOOK_REJECTION_STATUSES:
                await record_rate_limit_hit(rejected)
            return response

        if full_path in policy.public_paths or any(
            full_path.startswith(prefix) for prefix in policy.public_path_prefixes
        ):
            # Unauthenticated: limit by address, and pass the host through so login can
            # resolve the organization from the subdomain. No identity header is ever written
            # on this branch — those all live below, past the token this branch never asks
            # for — so a public route is forwarded exactly as anonymous as the caller was.
            #
            # The address is the *caller's* (`client_limiter_key`), not the socket peer's: behind
            # Caddy the peer is always Caddy, and one bucket for the whole internet is not a
            # per-address limit. Per address first, then the ceiling over all of them, so a
            # single address cannot spend more than its own allowance of the shared one. The
            # ceiling depends on what the path is: credential endpoints share one, everything
            # else another, so a crowd of storefront visitors cannot lock out sign-in and a
            # sign-in flood cannot take a catalogue offline.
            client_ip = client_limiter_key(request, policy.trusted_proxies)
            await enforce_rate_limit(f"anon:{client_ip}", limit=policy.anonymous_rate_limit)
            is_credential = full_path in policy.credential_paths
            if is_credential:
                ceiling, ceiling_limit = "anon-global", policy.anonymous_global_rate_limit
                ceiling_detail = (
                    "Too many sign-in and account requests are arriving right now. "
                    "Please retry shortly."
                )
            else:
                ceiling = "anon-content-global"
                ceiling_limit = policy.anonymous_content_global_rate_limit
                ceiling_detail = "Too many requests are arriving right now. Please retry shortly."
            if ceiling_limit:
                await enforce_rate_limit(ceiling, limit=ceiling_limit, detail=ceiling_detail)
            if (slug := _slug_from_host(request)) is not None:
                headers["x-org-slug"] = slug
            # Its own pool (`_body_slot_pools`): a stalled credential body cannot deny a webhook,
            # the storefront catalogue, or the other side of this same split.
            pool = body_slots["credential" if is_credential else "content"]
            async with pool.hold(client_ip):
                return await _forward(
                    request, route, headers, policy, full_path, settings, unauthenticated=True
                )

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise UnauthorizedProblem("Missing bearer token")

        claims = decode_token(
            settings,
            authorization.removeprefix("Bearer ").strip(),
            expected_type="access",
            audience=policy.audience,
        )

        org_id = str(claims.get("act_org") or "")
        if not org_id:
            # No default organization exists by design. A token without one is a bug, and
            # guessing is how tenants read each other's data.
            raise UnauthorizedProblem("Token carries no organization")

        # Either the whole gateway is staff-only, or this individual route is. The
        # per-route flag is the one that matters: `admin-gateway` carries internal
        # service-to-service routes alongside staff routes, and service calls hold no
        # staff claim — so a gateway-wide flag alone would have left every route in
        # `routes.json` marked `staff_only` unenforced.
        if (policy.staff_only or route.staff_only) and not claims.get("stf"):
            # Deliberately the same message a missing route would give: the existence of
            # the operator surface is not something to confirm.
            raise NotFoundProblem(f"No route for {full_path}")

        # A grant changed since this token was minted. The credential is fine; refresh it.
        current = await current_permission_version(org_id)
        if current is not None and int(claims.get("pv", 0)) < current:
            raise TokenStaleProblem("Your permissions have changed. Refreshing…")

        if route.product and route.product not in set(claims.get("ent", [])):
            # 402 rather than 403: the caller's permissions are fine, the subscription is
            # not, so the client offers an upgrade instead of an error.
            raise PaymentRequiredProblem(f"{route.product} is not active for this organization")

        await enforce_rate_limit(f"user:{claims['sub']}", limit=policy.rate_limit)

        headers["x-user-id"] = str(claims["sub"])
        headers["x-org-id"] = org_id
        headers["x-audience"] = policy.audience
        # The hop credential. Never the raw signing secret — that used to travel here on
        # every request, and one header capture was a forever-valid key that could forge any
        # identity against any service *and* mint `/internal/v1` tokens. What travels now is
        # derived, expiring, channel-audienced (so it opens no internal endpoint), and bound
        # to the one identity being forwarded: `_principal_from_gateway` refuses identity
        # headers the token does not vouch for.
        headers["x-internal-token"] = mint_service_token(
            settings,
            audience=GATEWAY_AUDIENCE,
            caller="gateway",
            claims={"uid": str(claims["sub"]), "org": org_id},
        )
        for claim, header in (
            ("mem", "x-membership-id"),
            ("sref", "x-subject-ref"),
            ("sid", "x-session-id"),
        ):
            if claims.get(claim):
                headers[header] = str(claims[claim])
        for claim, header in (
            ("roles", "x-roles"),
            ("ent", "x-entitlements"),
            ("loc", "x-locations"),
            ("amr", "x-amr"),
        ):
            headers[header] = ",".join(str(v) for v in claims.get(claim, []))
        headers["x-permission-version"] = str(claims.get("pv", 0))

        # Permissions are resolved here rather than carried in the token — see
        # `platform_core.auth` for why a fat token does not survive five products.
        if resolver is not None and claims.get("mem"):
            membership_id = str(claims["mem"])
            pv = int(claims.get("pv", 0))
            grants = await resolver.resolve(membership_id, org_id, pv)

            # The activation race: product-service commits an activation (and mints tokens
            # carrying it in `ent`) synchronously, but the owner's admin grant for that
            # product installs asynchronously — outbox -> relay -> account-service consumer,
            # on the order of seconds. A request that lands in that window has a token that
            # is neither stale (`pv` has not moved yet — installing the grant is what bumps
            # it) nor unentitled (`route.product` is already in `ent`, checked above), so
            # both earlier gates pass it through. But the grants just resolved may have been
            # read — or cached — from before the install landed: non-empty (the owner
            # already holds ORG_OWNER etc.) yet missing this product's codes specifically.
            # That combination — entitled per the token, absent per the resolved grants — is
            # the race's one detectable signature, and it is the only thing this branch
            # reacts to. (Checking `route.product in ent` again here would be redundant: the
            # 402 gate above already raised if it were not.)
            if route.product and not any(g.startswith(f"{route.product}.") for g in grants):
                # Re-read account-service directly, bypassing whatever is cached, and let
                # the fresh answer overwrite it (see `PermissionResolver.resolve`). This can
                # only ever narrow the gap between "the token says I'm entitled" and "the
                # grants agree" *toward the truth* — it is the exact same grants endpoint an
                # ordinary cache miss would have called, so it can return more than the
                # stale cache held only if the membership genuinely holds it now, and it can
                # never fabricate or widen a permission that account-service does not itself
                # report. An owner mid-propagation gets the grant that has already landed
                # server-side; a member who was simply never given a role in this product
                # gets the same absence as before, just cached briefly instead of for a full
                # minute.
                grants = await resolver.resolve(
                    membership_id, org_id, pv, fresh=True, product=route.product
                )

            headers["x-perms"] = ",".join(grants)

        return await _forward(request, route, headers, policy, full_path, settings)


def _normalize_path(raw: str) -> str:
    """Collapse repeated slashes and resolve `.`/`..` segments before this path is used for
    any routing decision — see `proxy`'s own comment at its call site for why.

    ASGI does not do this; Starlette matches `scope["path"]` byte-for-byte, so a raw
    `/api/v1/x/../y` reaches this handler exactly as written (proven in this module's tests
    with a hand-built ASGI scope — an `httpx` client normalises a URL string before it is ever
    sent, which would hide the very thing being defended against here). `posixpath.normpath`
    does the dot-segment and duplicate-slash collapsing; the explicit slash-collapse first is
    needed because `normpath` deliberately leaves a *leading* doubled slash alone (`//x` stays
    `//x` — POSIX reserves that form), which is not a distinction this router has any use for.
    `..` above the root clamps to the root rather than raising, matching what `httpx` itself
    does when `_forward` goes on to build the outbound request from this same string.
    """
    collapsed = _COLLAPSE_SLASHES.sub("/", raw)
    normalized = posixpath.normpath(collapsed)
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _has_internal_segment(path: str) -> bool:
    """True when `path`, normalised, contains a literal `internal` path segment.

    Every service mounts its internal, service-to-service router at `/internal/v1` — never
    under `/api`, which is all this gateway ever forwards — so an ordinary request has no
    business matching this at all; it exists for the request that was crafted to reach one
    anyway.

    Normalising first is what makes this hold under `..`: `/api/v1/x/../internal/y` is a
    literal string that does not contain `/internal/` as its own segment sequence in the
    naive sense, and `posixpath.normpath` collapsing it to `/api/internal/y` is what a
    same-check-without-normalising would miss. Percent-encoded forms (`%2Finternal%2F`) need
    no handling here: the ASGI server has already decoded them into literal `/` characters by
    the time `path` (captured from `{path:path}`) reaches this function, which is exactly why
    they show up as an ordinary `..`-free case above.
    """
    normalized = posixpath.normpath(path)
    return "internal" in normalized.split("/")


def _slug_from_host(request: Request) -> str | None:
    """`acme.opsgrids.com` → `acme`.

    Only the gateway parses the Host header. A service that trusted it directly would be
    trusting a client-supplied value.
    """
    host = request.headers.get("host", "").split(":")[0]
    parts = host.split(".")
    if len(parts) < 3 or parts[0] in ("www", "app", "api", "auth", "admin", "internal"):
        return None
    return parts[0]


def _format_bytes(limit: int) -> str:
    """`2097152` → `2 MiB`. This goes in the refusal, so the caller can act on it."""
    for unit, size in (("MiB", 1024 * 1024), ("KiB", 1024)):
        if limit >= size:
            value = limit / size
            return f"{value:.0f} {unit}" if value == int(value) else f"{value:.1f} {unit}"
    return f"{limit} bytes"


async def _read_capped_body(
    request: Request, limit: int, deadline_seconds: float | None = None
) -> bytes:
    """Buffer the request body, refusing anything past `limit` before it is all in memory.

    Checked twice, because `Content-Length` is a claim and not a fact:

    * against the declared length, which refuses an oversized body without reading a byte
      of it;
    * against the bytes actually read, which is what catches a header that lies and a
      chunked request that never sent one at all.

    The counter is the enforcement. The header is only an early exit.

    `deadline_seconds` bounds how long the *reading* may take: a caller who sends half a body
    and stops would otherwise hold what it sent for as long as its connection lives. On expiry
    the read is cancelled — what was buffered goes out of scope with it — and the answer is 408.
    `None` (an authenticated caller) is unbounded: a slow document upload is legitimate.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeProblem(
            f"Request body exceeds the {_format_bytes(limit)} limit for this endpoint"
        )

    async def buffer() -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                # Stop reading here. Draining the remainder would buffer exactly what was
                # refused, which is the thing this function exists to prevent.
                raise PayloadTooLargeProblem(
                    f"Request body exceeds the {_format_bytes(limit)} limit for this endpoint"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    if deadline_seconds is None:
        return await buffer()
    try:
        async with asyncio.timeout(deadline_seconds):
            return await buffer()
    except TimeoutError as exc:
        raise RequestTimeoutProblem(
            f"The request body was not received within {deadline_seconds:g} seconds"
        ) from exc


#: The throughput an unauthenticated body is held to, once it has declared how big it is — see
#: `_unauth_body_deadline`. Chosen well under anything a real client on an ordinary connection
#: sends at; a caller who cannot sustain even this is not sending a body, it is holding a slot.
_UNAUTH_BODY_MINIMUM_BYTES_PER_SECOND = 64 * 1024
#: The floor added to the throughput budget below, so a tiny declared body is not given an
#: unreasonably short deadline merely for being tiny — TCP and TLS setup alone can eat a
#: meaningful fraction of a second on a slow path.
_UNAUTH_BODY_DEADLINE_FLOOR_SECONDS = 2.0


def _unauth_body_deadline(policy: GatewayPolicy, request: Request) -> float:
    """How long this *particular* unauthenticated request gets to finish sending its body.

    Round one gave every unauthenticated body the same flat `unauth_body_read_timeout` (10s)
    regardless of how big it claimed to be. That is generous to exactly the shape of caller this
    guard exists to stop: a credential body is a few hundred bytes, so declaring one and then
    trickling it in at a few bytes a second held a slot for the full 10s on every attempt — and
    the per-address anonymous rate limit (`anonymous_rate_limit`, 60/min) does nothing to a caller
    who is not finishing enough requests to spend it.

    So the deadline scales with what the request itself claims to be: `_UNAUTH_BODY_DEADLINE_
    FLOOR_SECONDS` plus one second per `_UNAUTH_BODY_MINIMUM_BYTES_PER_SECOND` declared, capped at
    the flat ceiling (`unauth_body_read_timeout`) so a large, legitimate declared body — a
    WhatsApp delivery near its 3 MiB cap — is never held to *less* time than before. A request
    with no usable `Content-Length` (chunked, or none at all) gets the flat ceiling outright:
    there is nothing declared to compute a proportional budget from, and refusing to guess is
    the same choice `_read_capped_body`'s own size check already makes.

    The effect that matters: a credential body under a kilobyte now gets on the order of two
    seconds, not ten. Refreshing four held slots before each expires needs on the order of two
    requests a second sustained — well past what 60 requests a minute allows once the very first
    round of holds has been placed, so the per-address rate limit (checked *before* a slot is
    even requested) starts refusing new attempts long before the caller can keep the pool full.
    """
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        return policy.unauth_body_read_timeout
    proportional = _UNAUTH_BODY_DEADLINE_FLOOR_SECONDS + (
        int(declared) / _UNAUTH_BODY_MINIMUM_BYTES_PER_SECOND
    )
    return min(policy.unauth_body_read_timeout, proportional)


def _is_browser_credential(header: str) -> bool:
    """A request header that only a browser session has any use for."""
    name = header.lower()
    return name in _WEBHOOK_STRIPPED_REQUEST_HEADERS or name.startswith("access-control-")


def _is_cross_origin_response_header(header: str) -> bool:
    """A response header that would set a cookie or open the response to another origin."""
    name = header.lower()
    return name == "set-cookie" or name.startswith("access-control-")


async def _forward(
    request: Request,
    route: Route,
    headers: dict[str, str],
    policy: GatewayPolicy,
    full_path: str,
    settings: ServiceSettings,
    *,
    webhook: bool = False,
    unauthenticated: bool = False,
) -> Response:
    client: httpx.AsyncClient = request.app.state.http

    # The *normalised* path `proxy` already routed and authorized on — never `request.url.path`
    # again here. httpx's own URL parsing would resolve a raw `..`/`//` the same way on its way
    # to the wire regardless, but building the request from the same string the routing
    # decision was made against removes the "regardless" — the two can no longer read
    # differently even in principle.
    url = f"{route.upstream}{full_path}"

    # Before the body is buffered, and before the upstream is opened: an oversized request
    # must cost the gateway neither the memory nor a connection to a service that would
    # only refuse it later.
    limit = policy.max_body_bytes if route.max_body_bytes is None else route.max_body_bytes
    # An unauthenticated caller also has a deadline for sending it — see `_unauth_body_deadline`.
    read_timeout = _unauth_body_deadline(policy, request) if unauthenticated else None
    try:
        body = await _read_capped_body(request, limit, read_timeout)
    except RequestTimeoutProblem:
        log.warning(
            "gateway.body_read_timeout",
            path=request.url.path,
            method=request.method,
            timeout=read_timeout,
            declared=request.headers.get("content-length"),
            request_id=getattr(request.state, "request_id", None),
        )
        raise
    except PayloadTooLargeProblem:
        # The log carries the limit and the claim; the client is told the limit and
        # nothing about where the request was headed.
        log.warning(
            "gateway.body_too_large",
            path=request.url.path,
            method=request.method,
            limit=limit,
            declared=request.headers.get("content-length"),
            request_id=getattr(request.state, "request_id", None),
        )
        raise

    params = dict(request.query_params)
    if policy.sign_admin_hop and settings.admin_gateway_hop_key is not None:
        # Signed here, after the body is buffered, because the signature binds the method, the
        # normalised path, the canonical query and the body's digest (m2 of the third-round
        # review) — a captured header cannot be replayed with a different body or query. Only a
        # gateway whose *policy* says so signs: holding the key is not enough. Any copy of the
        # header a client attached to its inbound request was already stripped with the rest of
        # `SPOOFABLE`, so a non-signing gateway forwards none at all.
        headers[ADMIN_HOP_HEADER] = sign_admin_hop(
            request.method,
            full_path,
            key=settings.admin_gateway_hop_key.get_secret_value(),
            query=canonical_query(params.items()),
            body=body,
        )

    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        params=params,
        content=body,
    )
    try:
        upstream = await _send_riding_out_restarts(client, upstream_request, route, policy)
    except httpx.ConnectError as exc:
        log.error("gateway.upstream_unreachable", upstream=route.upstream, path=url)
        raise UpstreamUnavailableProblem("That service is temporarily unavailable") from exc
    except httpx.ReadTimeout as exc:
        log.error("gateway.upstream_timeout", upstream=route.upstream, path=url)
        raise UpstreamUnavailableProblem("That service took too long to respond") from exc

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    if webhook:
        # The other half of "no cookie from the service": whatever the owning service (or its own
        # CORS middleware) attached, a provider's reply carries neither a cookie nor an
        # allowance for a browser origin *from the service*. The gateway's own CORS middleware
        # runs outside this function and is untouched.
        response_headers = {
            k: v for k, v in response_headers.items() if not _is_cross_origin_response_header(k)
        }
    body = await upstream.aread()
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )


async def _send_riding_out_restarts(
    client: httpx.AsyncClient, upstream_request: httpx.Request, route: Route, policy: GatewayPolicy
) -> httpx.Response:
    """Send, retrying only a refused connection, with backoff, for `upstream_connect_grace`.

    Only `ConnectError` is retried: the request provably never reached the service. A read
    timeout or a reset mid-response may have executed the request, and is never retried.
    The budget counts the sleeps, not the wall clock, so the ceiling is the same under load.
    """
    delay, slept = 0.25, 0.0
    while True:
        try:
            return await client.send(upstream_request, stream=True)
        except httpx.ConnectError:
            if slept + delay > policy.upstream_connect_grace:
                raise
            log.warning("gateway.upstream_retry", upstream=route.upstream, waited=slept)
            await asyncio.sleep(delay)
            slept += delay
            delay = min(delay * 2, 2.0)


def http_client(policy: GatewayPolicy) -> httpx.AsyncClient:
    """One pooled client for the process. Building one per request is a socket leak."""
    return httpx.AsyncClient(
        timeout=policy.upstream_timeout,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_PUBLIC_PATHS",
    "GRANT_PROPAGATION_RETRY_TTL",
    "MAX_ROUTE_BODY_BYTES",
    "GatewayPolicy",
    "IPNetwork",
    "PermissionResolver",
    "Route",
    "RouteTable",
    "TokenStaleProblem",
    "build_gateway",
    "client_address",
    "client_limiter_key",
    "http_client",
    "parse_provider_cidrs",
    "parse_trusted_proxies",
]
