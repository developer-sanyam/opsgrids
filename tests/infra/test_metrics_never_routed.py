"""`/metrics` (every service's health router, and ops-monitor's own) must never be reachable
through a gateway or Caddy — it carries no auth of its own and is meant for something on `back`
to scrape, not for the internet. Same rule for ops-monitor's `/status` and its port (9110):
nothing outside `back` (and, for the certificate probe, `front`) should ever be able to name it.

Static, not a running stack: every gateway's route table and the edge Caddyfile are plain files
this test reads directly. `reverse_proxy`/`to` tokens are parsed properly (round 2, m10) — the
plain `host:port` form, the `scheme://host:port` form, and Caddy's `{$VAR}`/`{placeholder}`
forms — rather than a regex that only matched the one shape the file happened to use when the
lint was written. `handle_path` and `uri ... strip_prefix` are banned outright: both let an
upstream receive a path that is not what the route's own prefix matcher looked at, which is
exactly the kind of gap a path-traversal payload lives in.
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

#: `reverse_proxy <upstream>` or `to <upstream>` (the block form: `reverse_proxy up {` then
#: `to <upstream>` on its own line) — `<upstream>` may be a bare `host:port`, a
#: `scheme://host:port`, or a Caddy placeholder (`{$ENV_VAR}`, `{http.request...}`). Captures
#: the whole token; `_upstream_host` below picks the host back out of whichever form it is.
_UPSTREAM_TOKEN = re.compile(
    r"^\s*(?:reverse_proxy|to)\s+"
    r"((?:https?://)?(?:\{[^}]+\}|[a-zA-Z0-9_.-]+)(?::\d+)?)",
    re.MULTILINE,
)
_FORBIDDEN_DIRECTIVES = ("handle_path", "strip_prefix")
_FORBIDDEN_NAMES = ("ops-monitor", "9110", "/status")


def _prefixes(route_file: Path) -> list[str]:
    raw = json.loads(route_file.read_text())
    return [route["prefix"] for route in raw["routes"]]


def _upstream_host(token: str) -> str | None:
    """The bare hostname out of any of the three forms `_UPSTREAM_TOKEN` matches — `None` for
    a placeholder, which names no literal host to check at all (and is exactly why a
    placeholder upstream would be worth a human's attention if one is ever added here: this
    lint cannot see through it, so a reviewer has to)."""
    if token.startswith("{"):
        return None
    without_scheme = re.sub(r"^https?://", "", token)
    return without_scheme.split(":", 1)[0]


def _caddyfile_upstreams(text: str) -> set[str]:
    hosts: set[str] = set()
    for match in _UPSTREAM_TOKEN.finditer(text):
        host = _upstream_host(match.group(1))
        if host is not None:
            hosts.add(host)
    return hosts


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


def test_no_route_table_names_ops_monitor_its_port_or_its_status_endpoint() -> None:
    """B1/C10's own boundary: ops-monitor's `/metrics`, `/status` and port 9110 are reachable
    only on `back` (and `front`, for the certificate probe) — never through either gateway."""
    for route_file in ROUTE_FILES:
        text = route_file.read_text()
        for forbidden in _FORBIDDEN_NAMES:
            assert forbidden not in text, f"{route_file} names {forbidden!r} — it must not"


def test_the_edge_caddyfile_never_reverse_proxies_metrics() -> None:
    text = CADDYFILE.read_text()
    # Every path matcher (`@name { path ... }`) and every literal path segment on a
    # `handle`/`reverse_proxy` line — looked at together because Caddy expresses "which
    # requests reach this upstream" both ways in this file.
    assert "metrics" not in text.lower(), (
        "the Caddyfile mentions 'metrics' somewhere — /metrics must never be proxied to any "
        "upstream; it is reachable only on the compose `back` network"
    )


def test_the_caddyfile_never_names_ops_monitor_its_port_or_status() -> None:
    text = CADDYFILE.read_text()
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in text, f"the Caddyfile names {forbidden!r} — it must not"


def test_the_caddyfile_never_uses_handle_path_or_strip_prefix() -> None:
    """`handle_path`/`uri ... strip_prefix` both let an upstream see a path that is not what
    the route's own matcher looked at — exactly the shape a path-traversal payload needs: a
    prefix check that passes on the outer path while a rewritten, stripped path reaches the
    upstream. This box's Caddyfile has never needed either; this pins that."""
    text = CADDYFILE.read_text()
    for directive in _FORBIDDEN_DIRECTIVES:
        assert directive not in text, (
            f"the Caddyfile uses {directive!r} — banned (m10): it can let an upstream see a "
            "path different from the one the route's own prefix matcher checked"
        )


def test_the_edge_caddyfile_reverse_proxies_only_the_gateway_and_minio() -> None:
    """Pinning the known-good set so a new `reverse_proxy`/`to` line added later — in any of
    the three forms this lint now parses — is reviewed for exactly this, rather than only
    caught if someone remembers to grep for 'metrics'."""
    text = CADDYFILE.read_text()
    upstreams = _caddyfile_upstreams(text)
    assert upstreams == {"app-gateway", "minio"}, (
        f"the Caddyfile now reverse_proxies to {upstreams}, not just {{app-gateway, minio}} — "
        "confirm the new upstream cannot serve /metrics (or ops-monitor's /status) before "
        "widening this set"
    )


def test_upstream_token_parser_handles_all_three_forms() -> None:
    """Regression pin for the parser itself (m10) — the old regex only matched the bare
    `host:port` form and would have silently let a `scheme://` or placeholder upstream past
    every check above with no warning at all."""
    sample = """
    reverse_proxy plain-host:8000
    reverse_proxy https://scheme-host:8000
    reverse_proxy {$UPSTREAM_ENV}
    handle {
        to placeholder-in-block:8000
    }
    """
    hosts = _caddyfile_upstreams(sample)
    assert hosts == {"plain-host", "scheme-host", "placeholder-in-block"}
