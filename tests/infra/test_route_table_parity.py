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
"""

from __future__ import annotations

import json
from pathlib import Path

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
