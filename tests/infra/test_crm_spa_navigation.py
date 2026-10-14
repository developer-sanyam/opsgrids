"""The CRM rail leads only to screens that exist, and a dead address reaches the 404 page.

Four of the eleven rail entries led to a "coming soon" card for months, and each had a route a
person could bookmark: Reports, Integrations, Settings and Help. R1.2b removes them. Someone who
kept the bookmark must land on the app's own "That page doesn't exist" page, which is the route
table's last entry, and no rail entry may ever again point at a path the route table does not
declare. The route table is `main.tsx` and the rail is `ui/useNavGroups.ts`; a test cannot import
`main.tsx` (it mounts the app as a side effect), so both are read as source, the way
`test_engage_recipe_triggers.py` reads the Engage recipes.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "product-crm" / "src"
MAIN = SRC / "main.tsx"
NAV = SRC / "ui" / "useNavGroups.ts"

#: The routes that rendered a "coming soon" card and were removed together with their rail entries.
REMOVED = ("reports", "integrations", "settings", "help")

_ROUTE_PATH = re.compile(r'\bpath:\s*"([^"]+)"')
_NAV_TARGET = re.compile(r'\bto:\s*"([^"]+)"')


def _declared_paths() -> list[str]:
    return _ROUTE_PATH.findall(MAIN.read_text(encoding="utf-8"))


def _nav_targets() -> list[str]:
    return _NAV_TARGET.findall(NAV.read_text(encoding="utf-8"))


def test_the_scan_finds_what_it_is_meant_to_check() -> None:
    """A pattern that matched nothing would make every test below pass."""
    assert {"leads", "deals", "companies", "contacts", "activities", "team"} <= set(
        _declared_paths()
    )
    assert len(_nav_targets()) >= 7


def test_no_removed_placeholder_route_is_declared() -> None:
    back = sorted(set(_declared_paths()) & set(REMOVED))
    assert not back, f"placeholder routes are declared again: {back}"


def test_every_rail_entry_leads_to_a_declared_route() -> None:
    declared = set(_declared_paths())
    dead = [t for t in _nav_targets() if t != "/" and t.lstrip("/") not in declared]
    assert not dead, f"rail entries that lead nowhere: {dead}"


def test_the_last_route_is_the_not_found_page() -> None:
    source = MAIN.read_text(encoding="utf-8")
    routes = list(_ROUTE_PATH.finditer(source))
    assert routes and routes[-1].group(1) == "*", "the catch-all must be the last route"
    tail = source[routes[-1].end() :]
    assert "<NotFoundScreen" in tail or "<NotFoundPage" in tail, (
        "the catch-all must be the 404 page"
    )
