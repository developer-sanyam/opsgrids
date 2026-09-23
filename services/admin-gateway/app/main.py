"""The operator gateway — internal, staff and partner APIs.

The same machinery as the product gateway, with a different audience and a much narrower
route table. Two gateways rather than one because the alternative is a surface that grants
cross-customer access sitting one routing mistake away from the public internet.

Its public path list is deliberately tiny: staff sign in through the same auth-service as
everybody else, and nothing else here is reachable without a token.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.config import settings
from platform_core.app import create_app
from platform_core.gateway import (
    GatewayPolicy,
    PermissionResolver,
    RouteTable,
    build_gateway,
    http_client,
)

cfg = settings()

_routes_path = Path(cfg.routes_file)
if not _routes_path.is_absolute():
    _routes_path = Path(__file__).resolve().parent.parent / cfg.routes_file

routes = RouteTable.from_file(_routes_path)

policy = GatewayPolicy(
    audience=cfg.audience,
    # Only the sign-in flow and the health probes. Everything else needs a token.
    public_paths=frozenset(
        {
            "/health",
            "/health/ready",
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
            "/api/v1/auth/logout",
            "/api/v1/auth/mfa/verify",
            "/api/v1/auth/select-org",
            # The pre-auth operator sign-in and its rotation. Public because there is no
            # token yet; still authentication only, so the "public surface is only auth"
            # test stays green (all start with /api/v1/auth).
            "/api/v1/auth/operator/login",
            "/api/v1/auth/operator/refresh",
            "/api/v1/auth/operator/logout",
            # The operator MFA steps — mandatory for staff (ADR-0003 addendum), and every one
            # of them runs on a pre-auth token, not a bearer access token: there is no
            # principal yet for the gateway to check, exactly like the tenant `mfa/verify`
            # above. Omitting these left sign-in with no way to finish through the real
            # gateway at all — a caller could authenticate and never complete MFA, which is
            # BLOCKER 2 of the security review that added this.
            "/api/v1/auth/operator/mfa/enrol",
            "/api/v1/auth/operator/mfa/enrol/confirm",
            "/api/v1/auth/operator/mfa/verify",
            # The Razorpay payment webhook. Public because a provider carries no platform token —
            # it authenticates with the HMAC **signature** on its body, which payment-service
            # verifies against the shared webhook secret before recording or acting on anything
            # (ADR-0004 §6). It is deliberately NOT `staff_only`: a partner callback is not a
            # staff action. The gateway forwards the raw body and the signature header untouched.
            "/api/v1/webhooks/razorpay",
        }
    ),
    rate_limit=cfg.rate_limit_per_minute,
    anonymous_rate_limit=cfg.anonymous_rate_limit_per_minute,
    upstream_timeout=cfg.upstream_timeout_seconds,
)

resolver = PermissionResolver(cfg.account_service_url, cfg)


async def _startup(app: FastAPI) -> None:
    app.state.http = http_client(policy)


async def _shutdown(app: FastAPI) -> None:
    await app.state.http.aclose()
    await resolver.aclose()


app = create_app(
    cfg,
    title="Operator gateway",
    description=(
        "Internal, staff and partner APIs. Separate from the product gateway so a "
        "cross-customer surface is never one routing mistake away from being public."
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
