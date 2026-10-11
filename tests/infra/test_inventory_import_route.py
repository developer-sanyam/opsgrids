"""The inventory bulk import is one JSON request of up to 5000 rows (ADR-0027), which can exceed the
gateway's 2 MiB default body cap. Both route tables carry a longer-prefix entry for exactly that
path with a larger cap, upstream and product identical to the operations route it sits in front of
-- so the import is not silently refused with a 413 on a big file, and nothing else in
`/api/v1/operations` gets the larger ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "dev": REPO_ROOT / "services/app-gateway/routes.json",
    "prod": REPO_ROOT / "infrastructure/edge/routes.prod.json",
}
IMPORT_PREFIX = "/api/v1/operations/inventory/data/import"
DEFAULT_BODY_CAP = 2 * 1024 * 1024
#: 5000 rows of 18 columns of ordinary text is well under 4 MiB; 8 MiB leaves headroom.
MIN_IMPORT_CAP = 4 * 1024 * 1024


def _routes(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text())["routes"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("table", sorted(TABLES))
def test_the_import_path_has_its_own_larger_body_cap(table: str) -> None:
    routes = _routes(TABLES[table])
    by_prefix = {str(r["prefix"]): r for r in routes}
    route = by_prefix.get(IMPORT_PREFIX)
    assert route is not None, f"{table}: no route for {IMPORT_PREFIX}"
    cap = route.get("max_body_bytes")
    assert isinstance(cap, int) and MIN_IMPORT_CAP <= cap <= 32 * 1024 * 1024
    parent = by_prefix["/api/v1/operations"]
    assert route["upstream"] == parent["upstream"]
    assert route["product"] == parent["product"] == "operations"
    assert not route.get("blocked")


@pytest.mark.parametrize("table", sorted(TABLES))
def test_the_rest_of_operations_keeps_the_default_cap(table: str) -> None:
    parent = next(r for r in _routes(TABLES[table]) if r["prefix"] == "/api/v1/operations")
    assert parent.get("max_body_bytes") in (None, DEFAULT_BODY_CAP)
