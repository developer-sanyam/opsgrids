"""Sums `memory-budget.json`'s `expected_rss_mb` for whichever containers a given
`COMPOSE_PROFILES` list turns on.

Python, not bash, on purpose: this is a JSON lookup over `memory-budget.json`'s `containers`
mapping, not arithmetic — the arithmetic (the percent-of-MemTotal threshold check) lives in
`memory-budget.sh`'s `memory_budget_verdict`, testable on four plain numbers. Every box this
platform runs on already has python3 (every service here is Python, and the healthcheck every
API container runs is `python -S -c ...`), so `preflight.sh` shells out to this file's CLI
rather than hand-rolling a JSON parser in awk.

Read by `preflight.sh` (through the CLI at the bottom) and by
`tests/infra/test_compose_memory_budget.py` directly — the same container -> profile membership
this file reads is what that test cross-checks against `compose.prod.yml`'s real services, so a
container added to one and not the other fails the lint test by name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_budget(path: Path) -> dict[str, Any]:
    budget: dict[str, Any] = json.loads(path.read_text())
    return budget


def enabled_containers(budget: dict[str, Any], enabled_profiles: set[str]) -> list[str]:
    """Every container name that runs when `enabled_profiles` is the box's COMPOSE_PROFILES.

    A container with `profile: null` is core — always on, no profile required. One with a
    named profile runs only when that profile is in `enabled_profiles`.
    """
    names = []
    for name, meta in budget["containers"].items():
        profile = meta.get("profile")
        if profile is None or profile in enabled_profiles:
            names.append(name)
    return names


def _kind_meta(budget: dict[str, Any], container: str) -> dict[str, Any]:
    meta = budget["containers"][container]
    kind = meta["kind"]
    if kind == "datastore":
        return budget["datastores"][meta["datastore"]]  # type: ignore[no-any-return]
    return budget["kinds"][kind]  # type: ignore[no-any-return]


def expected_rss_mb(budget: dict[str, Any], container: str) -> int:
    return int(_kind_meta(budget, container)["expected_rss_mb"])


def is_steady_state(budget: dict[str, Any], container: str) -> bool:
    """False only for the one-shot bootstrap container (`minio-init`): it runs for a few
    seconds and exits, so it never contributes to a *steady-state* total."""
    return bool(_kind_meta(budget, container).get("steady_state", True))


def expected_total_mb(budget: dict[str, Any], enabled_profiles: set[str]) -> int:
    total = 0
    for name in enabled_containers(budget, enabled_profiles):
        if is_steady_state(budget, name):
            total += expected_rss_mb(budget, name)
    return total


def _main(argv: list[str]) -> int:
    """CLI: `memory_budget_totals.py <budget.json> <profiles-csv>` prints the expected total
    (MB) to stdout and exits 0. `<profiles-csv>` is exactly a box's `COMPOSE_PROFILES` value
    (comma-separated, may be empty for "core only")."""
    if len(argv) != 3:
        print("usage: memory_budget_totals.py <budget.json> <profiles-csv>", file=sys.stderr)
        return 2
    try:
        budget = load_budget(Path(argv[1]))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"memory_budget_totals: could not read {argv[1]}: {exc}", file=sys.stderr)
        return 1
    enabled = {p for p in argv[2].split(",") if p}
    print(expected_total_mb(budget, enabled))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
