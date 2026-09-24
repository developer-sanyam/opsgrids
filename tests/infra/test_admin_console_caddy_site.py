"""admin.opsgrids.com's Caddy site block (security review, fix round 1): CSP, HSTS and the two
generic security headers, and the inline-script extraction that makes the CSP actually work
rather than silently breaking the SPA's theme detection and its Google-Fonts stylesheet.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "infrastructure/edge/caddy/Caddyfile"
INDEX_HTML = REPO_ROOT / "frontend/opsgrids-internal/index.html"
THEME_INIT = REPO_ROOT / "frontend/opsgrids-internal/public/theme-init.js"


def _admin_site_block() -> str:
    text = CADDYFILE.read_text()
    match = re.search(r"^https://admin\.opsgrids\.com \{\n(.*?)^\}\n", text, re.DOTALL | re.M)
    assert match, "no https://admin.opsgrids.com site block found in the Caddyfile"
    return match.group(1)


def _header_value(block: str, header: str) -> str:
    match = re.search(rf'header {re.escape(header)} "([^"]*)"', block)
    assert match, f"{header} is not set in the admin.opsgrids.com site block"
    return match.group(1)


def test_the_csp_allows_self_and_only_the_named_google_fonts_hosts() -> None:
    csp = _header_value(_admin_site_block(), "Content-Security-Policy")
    assert "default-src 'self'" in csp
    assert "style-src 'self' https://fonts.googleapis.com" in csp
    assert "font-src 'self' https://fonts.gstatic.com" in csp
    assert "frame-ancestors 'none'" in csp
    # No blanket inline-script or inline-style allowance anywhere in the policy — the whole
    # point of extracting the theme-init snippet into its own file.
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_strict_transport_security_is_set() -> None:
    hsts = _header_value(_admin_site_block(), "Strict-Transport-Security")
    assert "max-age=31536000" in hsts


def test_the_generic_security_headers_are_set() -> None:
    block = _admin_site_block()
    assert _header_value(block, "X-Frame-Options") == "DENY"
    assert _header_value(block, "X-Content-Type-Options") == "nosniff"


def test_index_html_has_no_inline_script_body() -> None:
    """A `<script>` with a body (not just a `src=` attribute) cannot run under a CSP with no
    `'unsafe-inline'` in `script-src` — this pins the extraction so it cannot silently regress
    back to an inline snippet that would 404 the theme on first paint instead of erroring loudly."""
    html = INDEX_HTML.read_text()
    for match in re.finditer(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, re.DOTALL):
        if "src=" in match.group("attrs"):
            continue
        assert match.group("body").strip() == "", (
            "index.html has an inline <script> body, which the admin console's CSP "
            "(script-src has no 'unsafe-inline') will refuse to execute"
        )


def test_theme_init_is_its_own_file_and_referenced_by_index_html() -> None:
    assert THEME_INIT.is_file()
    assert '<script src="/theme-init.js"></script>' in INDEX_HTML.read_text()
