"""`publish-web.sh` must know the staff console (`APP=internal`) the same way it knows every
other named product app — a case in its own app table, pointing at the workspace package that
actually builds it, and named in the usage/error text so `make edge-web APP=bogus` still lists it.
"""

from __future__ import annotations

import re
from pathlib import Path

from _helpers import read_text

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "infrastructure/edge/publish-web.sh"


def test_internal_is_a_case_in_the_app_table() -> None:
    text = read_text(SCRIPT)
    match = re.search(r"\ninternal\)\n(.*?)\n\t;;\n", text, re.DOTALL)
    assert match, "publish-web.sh has no `internal)` case in its app table"
    block = match.group(1)
    assert "frontend/opsgrids-internal" in block, (
        "the `internal` case does not point at frontend/opsgrids-internal"
    )
    assert 'build_script="build"' in block
    assert 'entry="index.html"' in block


def test_internal_is_named_in_the_unknown_app_error() -> None:
    text = read_text(SCRIPT)
    assert "unknown APP" in text
    unknown_line = next(line for line in text.splitlines() if "unknown APP" in line)
    assert "internal" in unknown_line, (
        "the unknown-APP error text does not list 'internal' — a typo would print a list that "
        "lies about what this script accepts"
    )


def test_opsgrids_internal_workspace_package_actually_exists() -> None:
    """The other half of the same drift: a case naming a package directory that was renamed or
    never existed would only be discovered the first time someone tries to publish it."""
    package_dir = REPO_ROOT / "frontend/opsgrids-internal"
    assert package_dir.is_dir(), f"{package_dir} does not exist"
    assert (package_dir / "package.json").is_file()


def test_internal_case_sets_no_app_host() -> None:
    """`app_host` is what the SSO allow-list check (below) compares against
    `AUTH_REFRESH_CORS_ORIGINS`. The staff console must never grow one — it would make the
    script start treating `admin.opsgrids.com` as a tenant SPA host that has to appear in that
    app-gateway-only allow-list, which it must never do (see `test_internal_is_excluded_...`
    below)."""
    text = read_text(SCRIPT)
    match = re.search(r"\ninternal\)\n(.*?)\n\t;;\n", text, re.DOTALL)
    assert match, "publish-web.sh has no `internal)` case in its app table"
    assert "app_host=" not in match.group(1)


def test_internal_is_excluded_from_the_auth_origin_and_cors_allowlist_checks() -> None:
    """Single sign-on (ADR-0003 addendum) exempts `buyer` and `dashboard` from baking
    `VITE_AUTH_ORIGIN` and from the `AUTH_REFRESH_CORS_ORIGINS` allow-list refusal, because
    neither authenticates against the tenant refresh cookie the way every other product SPA
    does. The staff console (`APP=internal`, admin.opsgrids.com) belongs in that same exemption
    for its own reason: it authenticates operators same-origin against admin-gateway, never
    app-gateway's `/auth/refresh` — so it must never require `admin.opsgrids.com` in
    `AUTH_REFRESH_CORS_ORIGINS`, an allow-list that is app-gateway-only and tenant-only. This
    pins the one line in `publish-web.sh` that grants the exemption; forgetting `internal` there
    would make the script refuse to publish the console at all (or, worse, teach someone to
    "fix" that refusal by adding admin.opsgrids.com to a tenant CORS allow-list it must never be
    in)."""
    text = read_text(SCRIPT)
    pattern = r"^\tcase \"\$app\" in\n\tbuyer \| dashboard(?:\s*\|\s*internal)?\) ;;\n"
    match = re.search(pattern, text, re.MULTILINE)
    assert match, (
        "the buyer/dashboard exemption from the VITE_AUTH_ORIGIN / AUTH_REFRESH_CORS_ORIGINS "
        "check was not found in the expected shape"
    )
    assert "internal" in match.group(0), (
        "`internal` is not exempted alongside buyer/dashboard from the auth-origin/CORS-allowlist "
        "check — publishing the staff console would either fail outright or, if 'fixed' the wrong "
        "way, teach someone to add admin.opsgrids.com to AUTH_REFRESH_CORS_ORIGINS"
    )


def test_admin_host_is_never_in_the_auth_refresh_cors_allowlist() -> None:
    env_example = (REPO_ROOT / "infrastructure/edge/.env.example").read_text()
    match = re.search(r"^AUTH_REFRESH_CORS_ORIGINS=(\S+)$", env_example, re.MULTILINE)
    assert match, "AUTH_REFRESH_CORS_ORIGINS is missing from infrastructure/edge/.env.example"
    origins = {o.strip() for o in match.group(1).split(",") if o.strip()}
    assert "https://admin.opsgrids.com" not in origins, (
        "admin.opsgrids.com must never be in AUTH_REFRESH_CORS_ORIGINS: the staff console does "
        "not call app-gateway's /auth/refresh cross-origin, it authenticates same-origin against "
        "admin-gateway"
    )
