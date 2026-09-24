"""admin-gateway's production route table (fix round 1, security review minor): only
`/api/v1/auth/operator` is routed to auth, never the wider `/api/v1/auth` prefix the dev table
(`services/admin-gateway/routes.json`) still carries — every staff auth route already lives under
`/operator`, and the wider prefix would proxy a tenant sign-in route
(`/api/v1/auth/login`, `/refresh`, `/mfa/verify`, `/select-org`) to a surface that must never
answer a tenant token.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_ROUTES = REPO_ROOT / "infrastructure/edge/routes.admin.prod.json"


def _routes() -> list[dict[str, object]]:
    data = json.loads(ADMIN_ROUTES.read_text())
    routes = data["routes"]
    assert isinstance(routes, list)
    return routes


def test_only_the_operator_auth_prefix_is_routed_to_auth() -> None:
    auth_prefixes = {str(r["prefix"]) for r in _routes() if r["upstream"] == "http://auth:8000"}
    assert auth_prefixes == {"/api/v1/auth/operator"}, (
        f"admin-gateway routes {auth_prefixes} to auth — only /api/v1/auth/operator should be "
        "reachable here; the wider /api/v1/auth prefix would proxy tenant sign-in routes to the "
        "operator surface"
    )


def test_no_route_is_bare_tenant_auth() -> None:
    prefixes = {str(r["prefix"]) for r in _routes()}
    for tenant_only in ("/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/mfa/verify"):
        assert tenant_only not in prefixes


def test_nothing_here_is_tenant_facing() -> None:
    """Every route is either the operator auth prefix or explicitly `staff_only`."""
    for route in _routes():
        prefix = str(route["prefix"])
        if prefix == "/api/v1/auth/operator":
            continue
        assert route.get("staff_only") is True, f"{prefix} is not staff_only and not operator auth"
