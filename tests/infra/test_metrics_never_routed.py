"""`/metrics` (every service's health router, and ops-monitor's own) must never be reachable
through a gateway or Caddy — it carries no auth of its own and is meant for something on `back`
to scrape, not for the internet.

No compose/Caddy network lint existed before this slice; this is that lint, added rather than
extended. Static, not a running stack: every gateway's route table and the edge Caddyfile are
plain files this test reads directly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUTE_FILES = [
    REPO_ROOT / "services/app-gateway/routes.json",
    REPO_ROOT / "services/admin-gateway/routes.json",
    REPO_ROOT / "infrastructure/edge/routes.prod.json",
]
CADDYFILE = REPO_ROOT / "infrastructure/edge/caddy/Caddyfile"


def _prefixes(route_file: Path) -> list[str]:
    raw = json.loads(route_file.read_text())
    return [route["prefix"] for route in raw["routes"]]


def test_no_gateway_route_table_names_a_metrics_prefix() -> None:
    for route_file in ROUTE_FILES:
        for prefix in _prefixes(route_file):
            assert not prefix.rstrip("/").endswith("/metrics"), (
                f"{route_file} routes {prefix!r} — /metrics must never be reachable through "
                "a gateway (it carries no auth of its own)"
            )


def test_no_gateway_route_table_has_a_catch_all_prefix() -> None:
    """A bare `/` (or empty) prefix would proxy `/metrics` right along with everything else —
    every prefix must be specific, not a catch-all that accidentally covers it."""
    for route_file in ROUTE_FILES:
        for prefix in _prefixes(route_file):
            assert prefix not in ("", "/"), f"{route_file} has a catch-all prefix {prefix!r}"


def test_the_edge_caddyfile_never_reverse_proxies_metrics() -> None:
    text = CADDYFILE.read_text()
    # Every path matcher (`@name { path ... }`) and every literal path segment on a
    # `handle`/`reverse_proxy` line — looked at together because Caddy expresses "which
    # requests reach this upstream" both ways in this file.
    assert "metrics" not in text.lower(), (
        "the Caddyfile mentions 'metrics' somewhere — /metrics must never be proxied to any "
        "upstream; it is reachable only on the compose `back` network"
    )


def test_the_edge_caddyfile_reverse_proxies_only_the_gateway_and_minio() -> None:
    """Pinning the known-good set so a new `reverse_proxy` line added later is reviewed for
    exactly this, rather than only caught if someone remembers to grep for 'metrics'."""
    text = CADDYFILE.read_text()
    upstreams = set(re.findall(r"reverse_proxy\s+([a-z0-9-]+):\d+", text))
    assert upstreams == {"app-gateway", "minio"}, (
        f"the Caddyfile now reverse_proxies to {upstreams}, not just {{app-gateway, minio}} — "
        "confirm the new upstream cannot serve /metrics before widening this set"
    )
