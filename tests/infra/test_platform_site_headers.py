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
    return {name: body for name, body in _blocks().items() if "import platform-headers" in body}


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


# ---------------------------------------------------------------- Content-Security-Policy
#
# The snippet carries the directives every app site shares; each site passes the rest as the
# snippet's one argument. Why each third-party origin is there is written next to the policy in
# the Caddyfile; this table pins that each site allows exactly those and nothing more.
#: Only the storefront still allows Google Fonts; every other app bundles its fonts (@fontsource).
FONTS_CSS = "https://fonts.googleapis.com"
FONTS = "https://fonts.gstatic.com"
AUTH = "https://app.opsgrids.com"  # product apps refresh their session against the dashboard
RAZORPAY_SCRIPT = ["https://checkout.razorpay.com", "https://cdn.razorpay.com"]
RAZORPAY_API = ["https://api.razorpay.com", "https://api-dark.razorpay.com"]
#: Not its telemetry-beacon host: test_metrics_never_routed.py keeps the word out of the
#: Caddyfile, and a refused beacon costs Razorpay a data point, not the customer a checkout.
RAZORPAY_BEACON = ["https://lumberjack.razorpay.com", "https://lumberjack-cx.razorpay.com"]
CASHFREE_SCRIPT = ["https://sdk.cashfree.com"]
CASHFREE_PAGES = [
    "https://api.cashfree.com",
    "https://sandbox.cashfree.com",
    "https://payments.cashfree.com",
    "https://payments-test.cashfree.com",
]
GOOGLE_MAPS_IMG = [
    "https://*.googleapis.com",
    "https://*.gstatic.com",
    "https://*.google.com",
    "https://*.ggpht.com",
    "https://*.googleusercontent.com",
]

PLAIN_PRODUCT = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "font-src": ["'self'"],
    "connect-src": ["'self'", AUTH],
}
PLAIN_HOSTS = (
    "worker",
    "people",
    "revenue",
    "pulse",
    "projects",
    "support",
    "marketing",
    "finance",
    "it",
)

EXPECTED_CSP: dict[str, dict[str, list[str]]] = {
    **{f"https://{host}.opsgrids.com": PLAIN_PRODUCT for host in PLAIN_HOSTS},
    # Razorpay and Cashfree checkout (billing): their scripts inject <style> and style attributes.
    "https://app.opsgrids.com": {
        "default-src": ["'self'"],
        "script-src": ["'self'", *RAZORPAY_SCRIPT, *CASHFREE_SCRIPT],
        "style-src": ["'self'", "'unsafe-inline'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "data:", "https://cdn.razorpay.com"],
        "connect-src": ["'self'", *RAZORPAY_API, *RAZORPAY_BEACON, *CASHFREE_PAGES],
        "frame-src": [*RAZORPAY_API, *CASHFREE_SCRIPT, *CASHFREE_PAGES],
    },
    # The same two checkouts (order payments), the Google warehouse map, and the 3D floor view's
    # text renderer (troika: a blob: worker that importScripts blob: modules and fetches its glyph
    # data from cdn.jsdelivr.net).
    "https://ops.opsgrids.com": {
        "default-src": ["'self'"],
        "script-src": [
            "'self'",
            "blob:",
            "https://maps.googleapis.com",
            "https://maps.gstatic.com",
            *RAZORPAY_SCRIPT,
            *CASHFREE_SCRIPT,
        ],
        "style-src": ["'self'", "'unsafe-inline'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "data:", *GOOGLE_MAPS_IMG, "https://cdn.razorpay.com"],
        "connect-src": [
            "'self'",
            AUTH,
            "https://*.googleapis.com",
            "https://*.google.com",
            "https://*.gstatic.com",
            "https://cdn.jsdelivr.net",
            *RAZORPAY_API,
            *RAZORPAY_BEACON,
            *CASHFREE_PAGES,
        ],
        "frame-src": ["https://*.google.com", *RAZORPAY_API, *CASHFREE_SCRIPT, *CASHFREE_PAGES],
        "worker-src": ["blob:"],
    },
    # Meta's SDK for WhatsApp Embedded Signup; the sandboxed srcdoc email previews inherit this
    # policy, so their inline styles and (opt-in) remote https images need it too.
    "https://engage.opsgrids.com": {
        "default-src": ["'self'"],
        "script-src": ["'self'", "https://connect.facebook.net"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "https:"],
        "connect-src": ["'self'", AUTH, "https://graph.facebook.com", "https://www.facebook.com"],
        "frame-src": [
            "https://www.facebook.com",
            "https://web.facebook.com",
            "https://staticxx.facebook.com",
        ],
    },
    # The store editor previews merchant content: pasted https images, upload previews (blob:),
    # generated placeholder art (data:) and any https video embed address.
    "https://store.opsgrids.com": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'", FONTS_CSS],
        "font-src": ["'self'", FONTS],
        "img-src": ["'self'", "data:", "blob:", "https:"],
        "connect-src": ["'self'", AUTH],
        "frame-src": ["https:"],
    },
}
SHARED = {"object-src": ["'none'"], "base-uri": ["'self'"], "frame-ancestors": ["'none'"]}


def _parse(policy: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in policy.split(";"):
        name, *sources = part.split()
        assert name not in directives, f"directive {name} appears twice in {policy!r}"
        directives[name] = sources
    return directives


def _site_csp(site: str) -> dict[str, list[str]]:
    """The policy a site actually sends: the snippet's template with the site's argument in it."""
    template = re.search(r'Content-Security-Policy "([^"]*)"', _blocks()["(platform-headers)"])
    assert template, "the platform-headers snippet sets no Content-Security-Policy"
    arg = re.search(r'import platform-headers "([^"]*)"\n', _blocks()[site])
    assert arg, f"{site} passes no policy to platform-headers"
    return _parse(template.group(1).replace("{args[0]}", arg.group(1)))


def test_every_platform_site_has_an_expected_policy() -> None:
    assert set(_platform_sites()) == set(EXPECTED_CSP)


@pytest.mark.parametrize("site", sorted(EXPECTED_CSP))
def test_each_site_allows_exactly_the_origins_its_app_loads(site: str) -> None:
    assert _site_csp(site) == {**EXPECTED_CSP[site], **SHARED}


@pytest.mark.parametrize("site", sorted(EXPECTED_CSP))
def test_no_site_allows_script_injection(site: str) -> None:
    csp = _site_csp(site)
    scripts = csp["script-src"]
    assert "'unsafe-inline'" not in scripts
    assert not any("unsafe-eval" in s for sources in csp.values() for s in sources)
    assert not any(s in ("*", "https:", "http:", "data:") or "*" in s for s in scripts)
    assert csp["object-src"] == ["'none'"]
    assert csp["base-uri"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"]


def test_unsafe_inline_style_only_where_a_third_party_script_needs_it() -> None:
    inline = {site for site in EXPECTED_CSP if "'unsafe-inline'" in _site_csp(site)["style-src"]}
    assert inline == {
        "https://app.opsgrids.com",
        "https://ops.opsgrids.com",
        "https://engage.opsgrids.com",
    }


def test_merchant_domains_get_no_platform_policy() -> None:
    assert "Content-Security-Policy" not in _blocks()["https://"]
