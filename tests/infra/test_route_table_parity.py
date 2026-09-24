"""BLOCKER 2 of the 2026-09-24 security review: the dev route table (`services/app-gateway/
routes.json`) and the production one (`infrastructure/edge/routes.prod.json`) are two separate
files by design (different upstream hosts — see the `$comment` in each), which means nothing
stops them from drifting. A `blocked` entry that exists only in dev is the specific way that
drift is dangerous: `/api/v1/auth/operator` was refused locally and openly reachable in prod,
because only the dev table had ever been told to refuse it.

These tests pin the one property that matters: every `blocked` prefix in the dev table has an
identical entry (same prefix, same `blocked` value) in the prod table. They do not require the
tables to be otherwise identical — upstream hosts differ on purpose, and prod may legitimately
lack a route dev has no compose profile for.

The second half of the file (S07) holds the same two tables against the app-gateway's *policy*:
every provider webhook the production table routes must be public in it, and every public path it
declares must be routed in both tables.
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ROUTES = REPO_ROOT / "services/app-gateway/routes.json"
PROD_ROUTES = REPO_ROOT / "infrastructure/edge/routes.prod.json"


def _routes(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    routes = data["routes"]
    assert isinstance(routes, list)
    return routes


def _blocked_prefixes(routes: list[dict[str, object]]) -> dict[str, bool]:
    return {
        str(route["prefix"]): bool(route.get("blocked", False))
        for route in routes
        if route.get("blocked")
    }


def test_the_dev_table_actually_blocks_the_operator_prefix() -> None:
    """Fails without BLOCKER 2's fix on the dev side — proves the fixture itself is not
    vacuous before asserting anything about parity."""
    assert _blocked_prefixes(_routes(DEV_ROUTES)).get("/api/v1/auth/operator") is True


def test_every_blocked_dev_prefix_is_blocked_in_prod_too() -> None:
    """The regression this whole file exists to catch: `/api/v1/auth/operator` (or any future
    `blocked` entry) present in dev only is a route that is openly reachable in production."""
    dev_blocked = _blocked_prefixes(_routes(DEV_ROUTES))
    prod_blocked = _blocked_prefixes(_routes(PROD_ROUTES))
    missing = {
        prefix: value for prefix, value in dev_blocked.items() if prod_blocked.get(prefix) != value
    }
    assert missing == {}, f"blocked in dev but not identically blocked in prod: {missing}"


def test_the_prod_operator_route_points_at_the_in_cluster_auth_host() -> None:
    """Parity in `blocked` is not enough on its own if the entry silently rots to point
    somewhere else — pin the upstream too, since that is what a hand-edit is most likely to
    get wrong."""
    prod_routes = _routes(PROD_ROUTES)
    matches = [r for r in prod_routes if r["prefix"] == "/api/v1/auth/operator"]
    assert len(matches) == 1
    assert matches[0]["upstream"] == "http://auth:8000"
    assert matches[0]["blocked"] is True


# ------------------------------------------------------------------------------------------
# S07: the provider webhooks. `routes.prod.json` routed `/api/v1/webhooks/razorpay` and
# `/api/v1/webhooks/whatsapp` through app-gateway while app-gateway's policy did not know either
# was public — so every delivery met `401 Missing bearer token` and neither reached the service
# that verifies its signature. Nothing compared the *policy* with the *production table*, which is
# how it shipped. These tests read the real policy (in a subprocess inside the service's own
# directory: `import app` is ambiguous across services, so it cannot share this process) and hold
# it against both tables.
# ------------------------------------------------------------------------------------------

WEBHOOK_PREFIX = "/api/v1/webhooks/"

#: Paths the gateway process itself serves (health, docs), not routes to a service: public, but
#: not something either route table is expected to name.
_SERVED_BY_THE_GATEWAY = {"/health", "/health/ready", "/docs", "/openapi.json"}

_POLICY_SCRIPT = """
import json
from app.main import policy
print(json.dumps({
    "public_paths": sorted(policy.public_paths),
    "public_path_prefixes": sorted(policy.public_path_prefixes),
    "webhook_paths": {path: sorted(methods) for path, methods in policy.webhook_paths.items()},
}))
"""


@functools.lru_cache(maxsize=1)
def _app_gateway_policy() -> dict[str, Any]:
    """This service's own `policy`, exactly as `app.main` builds it."""
    result = subprocess.run(
        [sys.executable, "-c", _POLICY_SCRIPT],
        cwd=REPO_ROOT / "services/app-gateway",
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


def _webhook_routes(path: Path) -> dict[str, dict[str, object]]:
    return {
        str(route["prefix"]): route
        for route in _routes(path)
        if str(route["prefix"]).startswith(WEBHOOK_PREFIX)
    }


def test_the_production_table_actually_carries_the_two_provider_webhooks() -> None:
    """Keeps the parity tests below from passing over an empty set."""
    assert set(_webhook_routes(PROD_ROUTES)) == {
        "/api/v1/webhooks/razorpay",
        "/api/v1/webhooks/whatsapp",
    }


def test_every_webhook_route_in_prod_is_public_in_the_app_gateway_policy() -> None:
    """The S07 regression, as a build failure: a `/api/v1/webhooks/*` route served by the
    production gateway that the policy does not declare public is a route that answers every
    provider with a 401."""
    declared = set(_app_gateway_policy()["webhook_paths"])
    missing = set(_webhook_routes(PROD_ROUTES)) - declared
    assert missing == set(), f"routed in prod but not in WEBHOOK_PUBLIC_PATHS: {sorted(missing)}"


def test_every_public_webhook_path_is_routed_in_both_tables() -> None:
    """The other direction: a path the policy makes public with no route behind it is a public
    404 today and a public hole the day someone adds a broader prefix."""
    declared = set(_app_gateway_policy()["webhook_paths"])
    for name, table in (("dev", DEV_ROUTES), ("prod", PROD_ROUTES)):
        routed = set(_webhook_routes(table))
        assert declared <= routed, (
            f"public in the policy, not routed in {name}: {declared - routed}"
        )


def test_every_webhook_route_in_prod_exists_in_dev() -> None:
    prod, dev = _webhook_routes(PROD_ROUTES), _webhook_routes(DEV_ROUTES)
    assert set(prod) <= set(dev), f"in prod only: {sorted(set(prod) - set(dev))}"


def test_a_webhook_route_carries_the_same_body_cap_in_both_tables() -> None:
    """The cap is the only thing between an unauthenticated POST and a buffered body, so the
    value the box enforces must be the value the tests exercised."""
    prod, dev = _webhook_routes(PROD_ROUTES), _webhook_routes(DEV_ROUTES)
    for prefix, route in prod.items():
        assert route.get("max_body_bytes") == dev[prefix].get("max_body_bytes"), prefix


def test_no_webhook_route_falls_back_to_the_default_body_cap() -> None:
    """Unauthenticated, so sized to the real payload: Razorpay's are a few KiB, Meta documents up
    to 3 MB. A route with no `max_body_bytes` silently gets the gateway's 2 MiB default."""
    routes = _webhook_routes(PROD_ROUTES)
    for prefix, route in routes.items():
        cap = route.get("max_body_bytes")
        assert isinstance(cap, int), f"{prefix} has no body cap of its own"
        assert cap <= 4 * 1024 * 1024, f"{prefix} allows {cap} bytes"
    razorpay_cap = routes["/api/v1/webhooks/razorpay"]["max_body_bytes"]
    assert isinstance(razorpay_cap, int) and razorpay_cap <= 1024 * 1024


def test_every_public_path_the_policy_declares_resolves_to_a_route_in_both_tables() -> None:
    """A path that is public in the policy and unrouted in a table is dead there (a 404 that
    looks like it should work), and is exactly the dev/prod drift S07 was. Public prefixes are
    checked by their own text, which is itself a path the table must route."""
    from platform_core.gateway import RouteTable

    policy = _app_gateway_policy()
    public = (
        (set(policy["public_paths"]) - _SERVED_BY_THE_GATEWAY)
        | set(policy["webhook_paths"])
        | set(policy["public_path_prefixes"])
    )
    assert public, "the policy reported no public paths at all"
    for name, table in (("dev", DEV_ROUTES), ("prod", PROD_ROUTES)):
        routes = RouteTable.from_file(table)
        unrouted = sorted(p for p in public if (r := routes.resolve(p)) is None or r.blocked)
        assert unrouted == [], (
            f"public in app-gateway but not routed in the {name} table: {unrouted}"
        )
