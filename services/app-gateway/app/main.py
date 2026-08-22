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

# Resolved relative to the service directory so `make run` and the container image agree.
_routes_path = Path(cfg.routes_file)
if not _routes_path.is_absolute():
    _routes_path = Path(__file__).resolve().parent.parent / cfg.routes_file

routes = RouteTable.from_file(_routes_path)

policy = GatewayPolicy(
    audience=cfg.audience,
    public_paths=DEFAULT_PUBLIC_PATHS,
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
        "The public entry point for Atmora's product interfaces. Verifies once, resolves "
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
