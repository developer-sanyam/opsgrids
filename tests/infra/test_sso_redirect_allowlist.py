"""ADR-0003 addendum: the dashboard's post-login `next` redirect only ever lands on a platform
app host, never a storefront host or a merchant's custom domain — that allow-list lives once, in
`@opsgrids/client` (`frontend/opsgrids-client/src/sso/platformHosts.ts`), and every product SPA
imports it rather than each carrying its own copy that could quietly drift from the next.

This file pins that list against two independent sources of truth so a host cannot be added to
one and forgotten in the others without a failing test naming which one moved:

  * `infrastructure/edge/Caddyfile` — the named `https://<host> { ... }` blocks are exactly the
    platform app hosts that exist; the storefront catch-all is deliberately unnamed.
  * `infrastructure/edge/.env.example`'s `TLS_PLATFORM_HOSTS` — the same set Caddy's on-demand
    TLS authorizer checks before it will even attempt a certificate.

`app.opsgrids.com` is real but is not itself a *product* SPA the way the others are — it is the
dashboard, the host the refresh cookie lives on and the `next` redirect's own target once
sign-in completes — so it is expected on every list here too, not an exception.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "infrastructure/edge/caddy/Caddyfile"
EDGE_ENV_EXAMPLE = REPO_ROOT / "infrastructure/edge/.env.example"
PLATFORM_HOSTS_TS = REPO_ROOT / "frontend/opsgrids-client/src/sso/platformHosts.ts"

#: Named platform sites in the Caddyfile that are not product SPAs the `next` redirect should
#: ever target: the edge asset host, and the one bare `https://{$EDGE_HOST}` site that serves
#: `/opsgrids-public/*` and nothing an app user ever signs into.
NON_APP_CADDY_HOSTS = frozenset({"{$EDGE_HOST}"})


def _caddy_named_hosts() -> set[str]:
    text = CADDYFILE.read_text()
    hosts = set(re.findall(r"^https://(\S+)\s*\{", text, flags=re.MULTILINE))
    # The anonymous storefront catch-all is `https:// { ... }` — a bare scheme, no host. The
    # regex above requires at least one non-space character after `https://`, so it is already
    # excluded; asserted here so a Caddyfile edit that changes that shape is caught loudly
    # rather than this test silently losing its own negative case.
    assert "" not in hosts
    return hosts - NON_APP_CADDY_HOSTS


def _tls_platform_hosts() -> set[str]:
    text = EDGE_ENV_EXAMPLE.read_text()
    match = re.search(r"^TLS_PLATFORM_HOSTS=(\S+)$", text, flags=re.MULTILINE)
    assert match, "TLS_PLATFORM_HOSTS is missing from infrastructure/edge/.env.example"
    hosts = {h.strip() for h in match.group(1).split(",") if h.strip()}
    # `edge.opsgrids.com` and `api.opsgrids.com` are platform-owned TLS names with no app behind
    # them yet (the edge asset host, and a reserved name) — real for TLS issuance, never a
    # redirect target because no app SPA is ever published there.
    return hosts - {"edge.opsgrids.com", "api.opsgrids.com"}


def _client_allowlist() -> list[str]:
    text = PLATFORM_HOSTS_TS.read_text()
    match = re.search(r"PLATFORM_APP_HOSTS.*?=\s*\[(.*?)\];", text, flags=re.DOTALL)
    assert match, "PLATFORM_APP_HOSTS array not found in platformHosts.ts"
    return re.findall(r'"([^"]+)"', match.group(1))


def _auth_refresh_cors_origins() -> set[str]:
    text = EDGE_ENV_EXAMPLE.read_text()
    match = re.search(r"^AUTH_REFRESH_CORS_ORIGINS=(\S+)$", text, flags=re.MULTILINE)
    assert match, "AUTH_REFRESH_CORS_ORIGINS is missing from infrastructure/edge/.env.example"
    return {origin.strip() for origin in match.group(1).split(",") if origin.strip()}


def test_the_allowlist_has_no_duplicates() -> None:
    hosts = _client_allowlist()
    assert len(hosts) == len(set(hosts))


def test_every_allowlisted_host_is_a_named_caddy_site() -> None:
    allowlisted = set(_client_allowlist())
    caddy_hosts = _caddy_named_hosts()
    missing = allowlisted - caddy_hosts
    assert not missing, f"in @opsgrids/client but not a named Caddy site: {sorted(missing)}"


def test_every_named_caddy_site_is_on_the_allowlist() -> None:
    """The other direction: a platform app host added to Caddy and forgotten in the client
    allow-list would silently break SSO's post-login redirect for that app, not fail loudly —
    this is what makes it fail loudly instead."""
    allowlisted = set(_client_allowlist())
    caddy_hosts = _caddy_named_hosts()
    missing = caddy_hosts - allowlisted
    assert not missing, f"a named Caddy site missing from @opsgrids/client: {sorted(missing)}"


def test_the_allowlist_matches_tls_platform_hosts_too() -> None:
    allowlisted = set(_client_allowlist())
    assert allowlisted == _tls_platform_hosts()


def test_no_storefront_or_wildcard_host_is_ever_allowlisted() -> None:
    allowlisted = _client_allowlist()
    for host in allowlisted:
        assert "stores.opsgrids.com" not in host
        assert "*" not in host


def test_auth_refresh_cors_origins_is_the_platform_allowlist_minus_the_dashboard() -> None:
    """MINOR 5 of the 2026-09-24 SSO review: `AUTH_REFRESH_CORS_ORIGINS` (app-gateway's narrow,
    credentialed CORS allowance for /auth/refresh and /auth/logout) must name exactly the
    product app hosts that call those two routes cross-origin — every platform app host except
    `app.opsgrids.com` itself, which already has its own same-origin allowance via
    `CORS_ORIGINS` and is never a *caller* of the cross-origin form. A host added to the
    platform allow-list and forgotten here would build and deploy that app fine — its `/login`
    redirect to the dashboard works, since that leg is same-site navigation, not a CORS-gated
    fetch — and only fail the very next silent refresh, as a 400 preflight the client can only
    report as its generic `"error"` outcome.
    """
    origins = _auth_refresh_cors_origins()
    hosts = {origin.removeprefix("https://") for origin in origins}
    expected = set(_client_allowlist()) - {"app.opsgrids.com"}
    assert hosts == expected

    # Every entry is a bare https:// origin — no path, no trailing slash, no port — matching
    # `platform_core.config`'s own validator for this field.
    for origin in origins:
        assert origin == f"https://{origin.removeprefix('https://')}"
        assert "/" not in origin.removeprefix("https://")
