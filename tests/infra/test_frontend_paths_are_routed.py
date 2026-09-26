"""Every API path a tenant-facing frontend calls has a route in the gateway table.

The Domains page in the dashboard called `GET /api/v1/domains`, which account-service serves, but
neither route table listed it: the gateway answered "No route for /api/v1/domains" and the page
showed "Couldn't load domains" in production. Nothing tested that a screen's API call and the
gateway's route table agree, so it shipped. This pins it: the first path segment of every literal
`apiRequest("/…")` call in the dashboard and the product apps must be a prefix of some route in
the production table.

The staff console (`opsgrids-internal`) is deliberately absent: it talks to the separate admin
gateway (`routes.admin.prod.json`), not this table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The production table only. The dev table has no billing routes at all (`/credit-balance`,
#: `/invoices`), which is a local-development gap and not what shipped broken.
TABLES = {"prod": REPO_ROOT / "infrastructure/edge/routes.prod.json"}
FRONTEND = REPO_ROOT / "frontend"
APPS = ["opsgrids-dashboard", *sorted(p.name for p in FRONTEND.glob("product-*") if p.is_dir())]

#: `apiRequest("/domains")`, `apiRequest<Foo[]>(`/members${qs}`)` — the leading literal path only.
CALL = re.compile(r"""apiRequest(?:<[^>()]*>)?\(\s*[`"'](/[A-Za-z0-9_\-]+)""")


def _prefixes(path: Path) -> list[str]:
    return [str(r["prefix"]) for r in json.loads(path.read_text())["routes"]]


def _called_segments() -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    for app in APPS:
        for file in (FRONTEND / app / "src").rglob("*.ts*"):
            if ".test." in file.name or "fixtures" in file.parts:
                continue
            for segment in CALL.findall(file.read_text()):
                seen.setdefault(segment, set()).add(app)
    return seen


def test_the_scan_finds_the_calls_it_is_meant_to_check() -> None:
    """A regex that silently matches nothing would make every other test here pass."""
    seen = _called_segments()
    assert len(seen) >= 20, sorted(seen)
    assert "/domains" in seen and "/members" in seen


def test_every_called_path_has_a_production_route() -> None:
    seen = _called_segments()
    missing: list[str] = []
    for name, table in TABLES.items():
        prefixes = _prefixes(table)
        for segment, apps in sorted(seen.items()):
            wanted = f"/api/v1{segment}"
            if not any(
                p == wanted or p.startswith(wanted + "/") or wanted.startswith(p + "/")
                for p in prefixes
            ):
                missing.append(f"{name}: {wanted} (called from {', '.join(sorted(apps))})")
    assert not missing, "no gateway route for: " + "; ".join(missing)
