"""Every platform app site sends the baseline security headers.

Only admin.opsgrids.com set any; app.opsgrids.com — which holds the refresh cookie and the member
and role screens — could be framed by any page (clickjacking) and had no HSTS. Merchant custom
domains (the catch-all `https://` site) are deliberately left out: HSTS on a domain we do not own
outlives our tenancy of it, and a merchant may legitimately embed their own store.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "infrastructure/edge/caddy/Caddyfile"


def _blocks() -> dict[str, str]:
    text = CADDYFILE.read_text()
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^(\S+) \{\n(.*?)^\}\n", text, re.DOTALL | re.M)
    }


def test_the_snippet_sets_every_baseline_header() -> None:
    snippet = _blocks()["(platform-headers)"]
    assert 'Strict-Transport-Security "max-age=31536000"' in snippet
    assert 'X-Content-Type-Options "nosniff"' in snippet
    assert 'X-Frame-Options "DENY"' in snippet
    assert "frame-ancestors 'none'" in snippet
    assert 'Referrer-Policy "strict-origin-when-cross-origin"' in snippet
    # HSTS must never reach domains we do not own.
    assert "includeSubDomains" not in snippet


def test_every_platform_app_site_imports_it() -> None:
    blocks = _blocks()
    app_sites = [
        name
        for name in blocks
        if re.fullmatch(r"https://[a-z]+\.opsgrids\.com", name)
        and name != "https://admin.opsgrids.com"
    ]
    assert "https://app.opsgrids.com" in app_sites
    missing = [name for name in app_sites if "import platform-headers" not in blocks[name]]
    assert not missing, f"sites without the security headers: {missing}"


def test_merchant_domains_do_not_get_them() -> None:
    assert "import platform-headers" not in _blocks()["https://"]


# ---------------------------------------------------------------- the inline theme script
#
# Every platform site's web root (`/srv/web/<root>/current`) → the SPA publish-web.sh builds into
# it, and the localStorage key that SPA's pre-paint theme script reads.
FRONTEND = CADDYFILE.parents[3] / "frontend"
SPAS = {
    "dashboard": ("opsgrids-dashboard", "opsgrids.dashboard.theme"),
    "operations": ("product-operations", "opsgrids.operations.theme"),
    "worker": ("worker-app", "opsgrids.worker.theme"),
    "hrms": ("product-hrms", "opsgrids.hrms.theme"),
    "crm": ("product-crm", "opsgrids.crm.theme"),
    "pulseboard": ("product-pulseboard", "opsgrids.pulseboard.theme"),
    "projects": ("product-projects", "opsgrids.projects.theme"),
    "support": ("product-support", "opsgrids.support.theme"),
    "marketing": ("product-marketing", "opsgrids.marketing.theme"),
    "finance": ("product-finance", "opsgrids.finance.theme"),
    "itsm": ("product-itsm", "opsgrids.itsm.theme"),
    "storefront": ("product-storefront", "opsgrids.storefront.theme"),
    "engage": ("product-engage", "opsgrids.engage.theme"),
}


def _platform_sites() -> dict[str, str]:
    return {
        name: body
        for name, body in _blocks().items()
        if "import platform-headers" in body
    }


def test_every_platform_site_root_is_a_known_spa() -> None:
    roots = {
        re.search(r"root \* /srv/web/([a-z]+)/current", body).group(1)  # type: ignore[union-attr]
        for body in _platform_sites().values()
    }
    assert roots == set(SPAS)


@pytest.mark.parametrize("root", sorted(SPAS))
def test_index_html_has_no_inline_script_body(root: str) -> None:
    """A `<script>` with a body cannot run under a script-src without 'unsafe-inline'."""
    html = (FRONTEND / SPAS[root][0] / "index.html").read_text()
    for match in re.finditer(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, re.DOTALL):
        if "src=" in match.group("attrs"):
            continue
        assert match.group("body").strip() == "", f"{SPAS[root][0]}/index.html has an inline script"


@pytest.mark.parametrize("root", sorted(SPAS))
def test_theme_init_is_a_same_origin_file_reading_the_apps_own_key(root: str) -> None:
    app, key = SPAS[root]
    assert '<script src="/theme-init.js"></script>' in (FRONTEND / app / "index.html").read_text()
    assert f'"{key}"' in (FRONTEND / app / "public/theme-init.js").read_text()
