"""The product gateway — the single public entry point for every product UI.

Responsibilities, and deliberately nothing more:

* terminate the public API surface and route to the service that owns the path
* verify the token once, and forward identity as signed internal headers
* refuse a product the organization has not activated
* resolve permissions, and rate limit
* propagate the correlation id so one request is traceable across services

It contains no business logic and owns no tables. Fine-grained permission and scope
filtering stay in the services, where the data is — the gateway knows *who* is calling,
not which rows they may see.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from platform_core.app import create_app
from platform_core.gateway import (
    DEFAULT_PUBLIC_PATHS,
    GatewayPolicy,
    PermissionResolver,
    RouteTable,
    build_gateway,
    http_client,
)

cfg = settings()

#: The storefront's buyer surface (ADR-0010 §4, ADR-0012 §2). Lives **here, in the gateway
#: service**, and is
#: unioned into the shared default below rather than being added to `DEFAULT_PUBLIC_PATHS` in
#: `platform_core`: editing the library's frozenset would put one product's buyer surface into
#: the one module both gateways read, and `admin-gateway` — which builds its own explicit set —
#: would then be carrying a product concern it has no use for. A two-line change in the gateway
#: *service* is the right blast radius.
#:
#: **Exact match, and that is a constraint on the whole future buyer API rather than a quirk of
#: this route.** `build_gateway` tests `full_path in policy.public_paths`, and `full_path` is
#: `f"/api/{path}"` from the `{path:path}` catch-all — so it excludes the query string, a public
#: *prefix* is not configurable, and a public path with a path parameter is not expressible at
#: all. Every anonymous storefront endpoint must therefore carry its variables in the query
#: string or the request body; the store slug travels in the body here for exactly that reason.
#:
#: Being on this list means: no token required, limited by client IP rather than by caller, and
#: **no entitlement check** — the public branch of the proxy returns before that gate. It does
#: not mean the path is unauthenticated *by accident*; adding one is a deliberate decision about
#: a route that has no principal to authorize.
#:
#: **Two entries, and the second is what a buyer's browser asks first.** `store-context` turns
#: the hostname in the address bar into the store's slug, so a merchant on a domain they own
#: gets served without the bundle carrying baked-in configuration (ADR-0012 §2). It is on the
#: same terms as the first: an exact path, no path variable, the variable in the body, and the
#: anonymous per-IP limit in front of it. Nothing on a custom domain is authenticated — the
#: refresh cookie is scoped to `.opsgrids.com` and is simply never sent there — so this list is
#: the whole of what a stranger on `merchant.com` may reach.
STOREFRONT_PUBLIC_PATHS = frozenset(
    {
        "/api/v1/storefront/public/serviceability",
        "/api/v1/storefront/public/store-context",
    }
)

# Resolved relative to the service directory so `make run` and the container image agree.
_routes_path = Path(cfg.routes_file)
if not _routes_path.is_absolute():
    _routes_path = Path(__file__).resolve().parent.parent / cfg.routes_file

routes = RouteTable.from_file(_routes_path)

policy = GatewayPolicy(
    audience=cfg.audience,
    public_paths=DEFAULT_PUBLIC_PATHS | STOREFRONT_PUBLIC_PATHS,
    rate_limit=cfg.rate_limit_per_minute,
    anonymous_rate_limit=cfg.anonymous_rate_limit_per_minute,
    upstream_timeout=cfg.upstream_timeout_seconds,
)

resolver = PermissionResolver(cfg.account_service_url, cfg)


async def _startup(app: FastAPI) -> None:
    # One pooled client for the process. Building one per request leaks sockets under any
    # real load.
    app.state.http = http_client(policy)


async def _shutdown(app: FastAPI) -> None:
    await app.state.http.aclose()
    await resolver.aclose()


app = create_app(
    cfg,
    title="Product gateway",
    description=(
        "The public entry point for OpsGrids's product interfaces. Verifies once, resolves "
        "the organization, and refuses anything the customer has not activated."
    ),
    on_startup=_startup,
    on_shutdown=_shutdown,
    # The gateway owns no tables, so it opens no engine — an earlier version did,
    # contradicting this service's own configuration comment and giving readiness a
    # dependency it never uses. Redis it genuinely needs: the rate limiter and the
    # permission-version check both read it, and both fail closed.
    database=False,
    redis=True,
)

build_gateway(app, settings=cfg, routes=routes, policy=policy, resolver=resolver)
