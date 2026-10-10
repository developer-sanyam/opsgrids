"""Every platform app site sends the baseline security headers.

Only admin.opsgrids.com set any; app.opsgrids.com — which holds the refresh cookie and the member
and role screens — could be framed by any page (clickjacking) and had no HSTS. Merchant custom
domains (the catch-all `https://` site) are deliberately left out: HSTS on a domain we do not own
outlives our tenancy of it, and a merchant may legitimately embed their own store.
"""

from __future__ import annotations

import re
from pathlib import Path

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
