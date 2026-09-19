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
limited by IP instead.

**The gateway owns no tables.** Everything it needs is in the token or in Redis.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from starlette.background import BackgroundTask

from platform_core.auth import decode_token
from platform_core.cache import (
    current_permission_version,
    enforce_rate_limit,
    resolve_cached,
)
from platform_core.config import ServiceSettings
from platform_core.errors import (
    NotFoundProblem,
    PaymentRequiredProblem,
    ProblemError,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from platform_core.internal_auth import GATEWAY_AUDIENCE, mint_service_token
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
    }
)


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
    #: Reachable without a token.
    public_paths: frozenset[str]
    #: Requests per minute, per caller.
    rate_limit: int = 300
    #: Unauthenticated callers are limited harder, and by address.
    anonymous_rate_limit: int = 60
    #: Refuse anyone who is not OpsGrids staff. The operator console sets this.
    staff_only: bool = False
    upstream_timeout: float = 30.0


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
        # Meta's WhatsApp webhooks. Public by necessity — the provider cannot hold one of our
        # tokens — and authenticated instead by an HMAC-SHA256 signature over the raw body,
        # verified in notification-service. The GET is Meta's subscription handshake.
        "/webhooks/whatsapp",
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

    async def resolve(self, membership_id: str, org_id: str, version: int) -> list[str]:
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
        full_path = f"/api/{path}"

        route = routes.resolve(full_path)
        if route is None:
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

        if full_path in policy.public_paths:
            # Unauthenticated: limit by address, and pass the host through so login can
            # resolve the organization from the subdomain.
            client_ip = request.client.host if request.client else "anonymous"
            await enforce_rate_limit(f"anon:{client_ip}", limit=policy.anonymous_rate_limit)
            if (slug := _slug_from_host(request)) is not None:
                headers["x-org-slug"] = slug
            return await _forward(request, route, headers, policy)

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
            grants = await resolver.resolve(str(claims["mem"]), org_id, int(claims.get("pv", 0)))
            headers["x-perms"] = ",".join(grants)

        return await _forward(request, route, headers, policy)


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


async def _forward(
    request: Request, route: Route, headers: dict[str, str], policy: GatewayPolicy
) -> Response:
    client: httpx.AsyncClient = request.app.state.http
    url = f"{route.upstream}{request.url.path}"

    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        params=dict(request.query_params),
        content=await request.body(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.ConnectError as exc:
        log.error("gateway.upstream_unreachable", upstream=route.upstream, path=url)
        raise UpstreamUnavailableProblem("That service is temporarily unavailable") from exc
    except httpx.ReadTimeout as exc:
        log.error("gateway.upstream_timeout", upstream=route.upstream, path=url)
        raise UpstreamUnavailableProblem("That service took too long to respond") from exc

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    body = await upstream.aread()
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )


def http_client(policy: GatewayPolicy) -> httpx.AsyncClient:
    """One pooled client for the process. Building one per request is a socket leak."""
    return httpx.AsyncClient(
        timeout=policy.upstream_timeout,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )


__all__ = [
    "DEFAULT_PUBLIC_PATHS",
    "GatewayPolicy",
    "PermissionResolver",
    "Route",
    "RouteTable",
    "TokenStaleProblem",
    "build_gateway",
    "http_client",
]
