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
limited by IP instead. Provider webhooks (`GatewayPolicy.webhook_paths`) take a third route:
no token, exact path *and* method, their own rate-limit buckets, and no cookie or CORS surface.

The proxy step caps the request body (`DEFAULT_MAX_BODY_BYTES`, or a route's own override)
and enforces it against the bytes actually read. The gateway buffers a body whole before
forwarding it, so without a cap a single large POST exhausts the process before the
downstream service — whose own limit protects only itself — is ever asked its opinion.

**The gateway owns no tables.** Everything it needs is in the token or in Redis.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from starlette.background import BackgroundTask

from platform_core.auth import decode_token
from platform_core.cache import (
    cache_set,
    current_permission_version,
    enforce_rate_limit,
    resolve_cached,
)
from platform_core.config import ServiceSettings
from platform_core.errors import (
    NotFoundProblem,
    PayloadTooLargeProblem,
    PaymentRequiredProblem,
    ProblemError,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from platform_core.internal_auth import (
    ADMIN_HOP_HEADER,
    FORWARDED_HEADERS,
    GATEWAY_AUDIENCE,
    canonical_query,
    forwarded_headers_digest,
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
#:
#: Every header the hop token binds (`FORWARDED_HEADERS`) is here by construction, so the two
#: lists cannot drift apart.
SPOOFABLE = frozenset({"x-internal-token", ADMIN_HOP_HEADER, *FORWARDED_HEADERS})

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
    #: kind of anonymous. It carries no bearer token — the provider signs the body and the owning
    #: service verifies that signature over the raw bytes — so it is not a person and has none of
    #: a browser's credentials (see `_WEBHOOK_STRIPPED_REQUEST_HEADERS`); it is counted in its own
    #: buckets (`webhook_rate_limit`, `webhook_global_rate_limit`) so a sign-in flood cannot
    #: starve a provider and the other way round; and it is public **per method**: Meta's
    #: subscription handshake is a GET and its deliveries are POSTs, and a PUT or DELETE to the
    #: same path falls through to the bearer check like any other unlisted request.
    #: Exact-matched against the normalised path, so `/api/v1/webhooks/whatsapp/../employees` is
    #: judged as `/api/v1/employees`, which is not here.
    #:
    #: Last on purpose: added after the fields above, so an existing positional construction
    #: keeps meaning what it meant.
    webhook_paths: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Per-address requests per minute on one webhook path. Generous: a provider delivers from a
    #: small set of addresses and legitimately bursts, and it retries a 429, so a limit set too
    #: low turns into redelivery rather than loss.
    webhook_rate_limit: int = 600
    #: All addresses together, per webhook path, per minute; `0` means none.
    webhook_global_rate_limit: int = 0
    #: Public prefix (a member of `public_path_prefixes`) -> per-address requests per minute in a
    #: bucket of its own, instead of `anonymous_rate_limit` in the bucket every other public path
    #: shares. For a public route that is fetched by machines and by many people behind one
    #: address — Engage's open pixel and tracked links are opened by mailbox providers' image
    #: proxies and from corporate NATs, where sixty a minute turns a click into a 429 instead of a
    #: redirect. A prefix that is not listed here keeps the shared anonymous bucket, so an empty
    #: mapping (the default) changes nothing. Last on purpose, like `webhook_paths`.
    public_prefix_rate_limits: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A prefix without a trailing slash is a bug waiting to widen: refused at construction
        # (a deployment-time failure, the same class as `_parse_max_body_bytes`'s) rather than
        # silently matching more than whoever wrote it meant.
        for prefix in self.public_path_prefixes:
            if not prefix.endswith("/"):
                raise ValueError(f"public path prefix {prefix!r} must end with '/'")
        # An override for a prefix that is not public would be dead configuration that looks like
        # a limit; a non-positive figure would refuse every request or (0) read as "no limit".
        for prefix, per_minute in self.public_prefix_rate_limits.items():
            if prefix not in self.public_path_prefixes:
                raise ValueError(f"rate limit for {prefix!r}, which is not a public path prefix")
            if isinstance(per_minute, bool) or not isinstance(per_minute, int) or per_minute < 1:
                raise ValueError(f"rate limit for {prefix!r} must be a positive integer")
        # A webhook entry is matched against a normalised path, which never ends in `/` and never
        # carries a query, so an entry that does either could never match — and one with no
        # methods, or a lower-case one, would be public for nothing or fail to match GET.
        for path, methods in self.webhook_paths.items():
            if not path.startswith("/api/") or path.endswith("/") or "?" in path:
                raise ValueError(
                    f"webhook path {path!r} must be an exact /api/... path with no trailing "
                    "slash and no query"
                )
            # A bare string is iterable, and `"POST"` would then pass as four one-letter "methods"
            # (and `request.method in "POST"` is a substring test). Looked at as an `object`: the
            # annotation rules it out, which is exactly why an untyped caller needs the check.
            declared: object = methods
            if (
                isinstance(declared, str)
                or not methods
                or any(m != m.upper() or not m.isalpha() for m in methods)
            ):
                raise ValueError(f"webhook path {path!r} needs upper-case HTTP method names")


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
        # Self-serve Google/Microsoft sign-in — the platform-wide sibling of the SSO pair
        # above, public for the identical reason: `/start` is what *produces* the redirect a
        # person with no token yet has to follow, and `/callback` is where the provider sends
        # a bearer-less browser back with nothing of ours but the `state` this service issued.
        # Exact paths, not a prefix: a prefix would silently make any future route nested
        # under `/api/v1/auth/oauth/` public too, which is not what this list is for.
        "/api/v1/auth/oauth/google/start",
        "/api/v1/auth/oauth/google/callback",
        "/api/v1/auth/oauth/microsoft/start",
        "/api/v1/auth/oauth/microsoft/callback",
        "/api/v1/auth/password/forgot",
        "/api/v1/auth/password/reset",
        # Provider webhooks (Meta's WhatsApp) are deliberately **not** here: they are
        # `GatewayPolicy.webhook_paths`, declared by the gateway that fronts them. A
        # `/webhooks/whatsapp` entry used to live in this set and could never match — this proxy
        # only serves `/api/{path}`, so a path without the `/api/` prefix never reaches a
        # public-path check — which made the webhook look public while the real one, at
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


def build_gateway(
    app: FastAPI,
    *,
    settings: ServiceSettings,
    routes: RouteTable,
    policy: GatewayPolicy,
    resolver: PermissionResolver | None = None,
) -> None:
    """Mount the catch-all proxy on `app`."""

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

        webhook_methods = policy.webhook_paths.get(full_path)
        if webhook_methods is not None and request.method in webhook_methods:
            # A provider webhook: no bearer token, by design — the provider signs the body and the
            # owning service verifies that signature over the raw bytes, which are forwarded
            # untouched. Judged on the same normalised `full_path` as everything above, exactly
            # and per method (see `GatewayPolicy.webhook_paths`), and counted in its own buckets:
            # one per address, then one for the path as a whole. The per-address one runs first, so
            # a single address spends at most its own allowance of the shared one; and neither a
            # sign-in flood nor a webhook flood can spend the other's allowance.
            client_ip = request.client.host if request.client else "anonymous"
            await enforce_rate_limit(
                f"hook:{full_path}:{client_ip}", limit=policy.webhook_rate_limit
            )
            if policy.webhook_global_rate_limit:
                await enforce_rate_limit(
                    f"hook:{full_path}", limit=policy.webhook_global_rate_limit
                )
            # A provider is a server, not a browser. None of these mean anything to it and none
            # should reach the service that verifies its signature: a session cookie or bearer
            # token that rode along would be a credential handed to a route that ignores it, and
            # an `Origin` would invite the upstream's CORS middleware to answer as though this
            # were a page. No `x-org-slug` either — the host says nothing about which tenant a
            # provider is calling for; the body's own routing key does.
            for name in [n for n in headers if _is_browser_credential(n)]:
                del headers[name]
            return await _forward(
                request, route, headers, policy, full_path, settings, webhook=True
            )

        if full_path in policy.public_paths or any(
            full_path.startswith(prefix) for prefix in policy.public_path_prefixes
        ):
            # Unauthenticated: limit by address, and pass the host through so login can
            # resolve the organization from the subdomain. No identity header is ever written
            # on this branch — those all live below, past the token this branch never asks
            # for — so a public route is forwarded exactly as anonymous as the caller was.
            client_ip = request.client.host if request.client else "anonymous"
            bucket, limit = f"anon:{client_ip}", policy.anonymous_rate_limit
            # A prefix that opted in (`GatewayPolicy.public_prefix_rate_limits`) is counted apart
            # from the shared bucket, so it can neither be starved by a sign-in flood nor spend
            # the sign-in allowance. The longest matching prefix wins.
            opted_in = max(
                (p for p in policy.public_prefix_rate_limits if full_path.startswith(p)),
                key=len,
                default=None,
            )
            if opted_in is not None:
                bucket = f"anon:{opted_in}:{client_ip}"
                limit = policy.public_prefix_rate_limits[opted_in]
            await enforce_rate_limit(bucket, limit=limit)
            if (slug := _slug_from_host(request)) is not None:
                headers["x-org-slug"] = slug
            # A hop that names nobody: it binds only the visitor's address and the slug, so a
            # service recording where an anonymous request came from (a password reset) can
            # believe it.
            _sign_forwarded(headers, request, settings)
            return await _forward(request, route, headers, policy, full_path, settings)

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

        # Last, once every forwarded header is written — the token binds their digest.
        _sign_forwarded(headers, request, settings, uid=str(claims["sub"]), org=org_id)
        return await _forward(request, route, headers, policy, full_path, settings)


def _sign_forwarded(
    headers: dict[str, str], request: Request, settings: ServiceSettings, **identity: str
) -> None:
    """Write the visitor's address and the hop credential.

    Never the raw signing secret — that used to travel here on every request, and one header
    capture was a forever-valid key that could forge any identity against any service *and*
    mint `/internal/v1` tokens. What travels now is derived, expiring, channel-audienced (so it
    opens no internal endpoint), and bound to what is being forwarded: the `uid`/`org` it names
    and a digest of every `FORWARDED_HEADERS` value (`fwd`), so `_principal_from_gateway`
    refuses a capture replayed with an escalated `X-Perms` or `X-Audience`.
    """
    if request.client is not None:
        # Already the real visitor: `TrustForwardedForFromPrivatePeers` rewrote the peer.
        headers["x-client-ip"] = request.client.host
    headers["x-internal-token"] = mint_service_token(
        settings,
        audience=GATEWAY_AUDIENCE,
        caller="gateway",
        claims={**identity, "fwd": forwarded_headers_digest(headers)},
    )


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


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """Buffer the request body, refusing anything past `limit` before it is all in memory.

    Checked twice, because `Content-Length` is a claim and not a fact:

    * against the declared length, which refuses an oversized body without reading a byte
      of it;
    * against the bytes actually read, which is what catches a header that lies and a
      chunked request that never sent one at all.

    The counter is the enforcement. The header is only an early exit.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeProblem(
            f"Request body exceeds the {_format_bytes(limit)} limit for this endpoint"
        )

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
    try:
        body = await _read_capped_body(request, limit)
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
    except httpx.RemoteProtocolError as exc:
        # The service dropped the connection without answering: ours to retry, not a 500.
        log.error("gateway.upstream_disconnected", upstream=route.upstream, path=url)
        raise UpstreamUnavailableProblem("That service is temporarily unavailable") from exc

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    if webhook:
        # The other half of "no cookie/CORS surface": whatever the owning service (or its own CORS
        # middleware) attached, a provider's reply carries neither a cookie nor an allowance for
        # a browser origin.
        response_headers = {
            k: v for k, v in response_headers.items() if not _is_cross_origin_response_header(k)
        }
    # `items()` comma-joins repeated headers, which turns two cookies into one malformed one; each
    # Set-Cookie must reach the browser as its own header line.
    # (A webhook reply already lost its Set-Cookie above, so the pop finds none and adds none.)
    cookies = (
        upstream.headers.get_list("set-cookie") if response_headers.pop("set-cookie", None) else []
    )
    body = await upstream.aread()
    response = Response(
        content=body,
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )
    response.raw_headers.extend((b"set-cookie", c.encode("latin-1")) for c in cookies)
    return response


async def _send_riding_out_restarts(
    client: httpx.AsyncClient, upstream_request: httpx.Request, route: Route, policy: GatewayPolicy
) -> httpx.Response:
    """Send, retrying only a refused connection, with backoff, for `upstream_connect_grace`.

    `ConnectError` is retried: the request provably never reached the service. A read timeout
    or a reset mid-response may have executed the request, and is never retried -- with one
    exception: an idempotent read (`GET`, `HEAD`, `OPTIONS`) that met `RemoteProtocolError` is
    sent once more. That is a pooled keep-alive connection the upstream closed as it was reused
    (the 2026-10-15 transient 500s on first load); repeating a read is harmless, repeating a
    write never is. The budget counts the sleeps, not the wall clock, so the ceiling is the same
    under load.
    """
    delay, slept = 0.25, 0.0
    resent_read = False
    while True:
        try:
            return await client.send(upstream_request, stream=True)
        except httpx.RemoteProtocolError:
            if resent_read or upstream_request.method not in _IDEMPOTENT_READS:
                raise
            resent_read = True
            log.warning("gateway.upstream_dropped_connection", upstream=route.upstream)
        except httpx.ConnectError:
            if slept + delay > policy.upstream_connect_grace:
                raise
            log.warning("gateway.upstream_retry", upstream=route.upstream, waited=slept)
            await asyncio.sleep(delay)
            slept += delay
            delay = min(delay * 2, 2.0)


#: The methods `_send_riding_out_restarts` may send twice after a dropped connection.
_IDEMPOTENT_READS = frozenset({"GET", "HEAD", "OPTIONS"})

#: How long an idle pooled connection to a service is kept. Below uvicorn's default
#: `--timeout-keep-alive` of 5 s on purpose: with both at 5 s the gateway could pick up a
#: connection in the instant the service was closing it, and the request failed with
#: `RemoteProtocolError` -- a 500 on the first request after any pause.
UPSTREAM_KEEPALIVE_EXPIRY_SECONDS = 2.0


def http_client(policy: GatewayPolicy) -> httpx.AsyncClient:
    """One pooled client for the process. Building one per request is a socket leak."""
    return httpx.AsyncClient(
        timeout=policy.upstream_timeout,
        limits=httpx.Limits(
            max_connections=200,
            max_keepalive_connections=50,
            keepalive_expiry=UPSTREAM_KEEPALIVE_EXPIRY_SECONDS,
        ),
        follow_redirects=False,
    )


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_PUBLIC_PATHS",
    "GRANT_PROPAGATION_RETRY_TTL",
    "MAX_ROUTE_BODY_BYTES",
    "GatewayPolicy",
    "PermissionResolver",
    "Route",
    "RouteTable",
    "TokenStaleProblem",
    "build_gateway",
    "http_client",
]
