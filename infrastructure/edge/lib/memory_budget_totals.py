"""Sums `memory-budget.json`'s `expected_rss_mb`, either for whichever containers a given
`COMPOSE_PROFILES` list turns on, or for an explicit set of container names.

Python, not bash, on purpose: this is a JSON lookup over `memory-budget.json`'s `containers`
mapping, not arithmetic — the arithmetic (the percent-of-MemTotal threshold check) lives in
`memory-budget.sh`'s `memory_budget_verdict`, testable on four plain numbers. Every box this
platform runs on already has python3 (every service here is Python, and the healthcheck every
API container runs is `python -S -c ...`), so `preflight.sh` shells out to this file's CLI
rather than hand-rolling a JSON parser in awk.

**Why two modes.** `COMPOSE_PROFILES`/`EDGE_PROFILES` says which services this box is
*configured* to run — but compose does not stop a container whose profile was just removed from
that list (H2, the memory-headroom slice's 2026-09-23 fix round: a profile disabled after a
deploy leaves its containers running as orphans until something explicitly removes them).
Pricing by *configured* profiles would then understate what is actually resident. `--containers`
prices an explicit, already-known list of container names instead — `preflight.sh` gets that
list from the Docker daemon itself (`docker ps` by compose project label), which is the ground
truth for what is actually running, configured or not.

Read by `preflight.sh` (through the CLI at the bottom) and by
`tests/infra/test_compose_memory_budget.py` directly — the same container -> profile membership
this file reads is what that test cross-checks against `compose.prod.yml`'s real services, so a
container added to one and not the other fails the lint test by name.
"""

from __future__ import annotations

import argparse
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


def expected_total_for_names(budget: dict[str, Any], container_names: set[str]) -> int:
    """Sum `expected_rss_mb` for an explicit set of container names — no profile involved.

    A name this budget does not price is skipped rather than raising: `docker ps` is asked about
    the *compose project*, not this file, so a container from a stray, unrelated project sharing
    the same host would otherwise crash a preflight check that exists to keep the box up, over
    something that is not even this platform's own container. `tests/infra/
    test_compose_memory_budget.py::test_every_compose_service_is_in_the_budget` is what actually
    guarantees every real container of *this* project has a price; this function does not need
    to repeat that guarantee to stay safe.
    """
    total = 0
    for name in container_names:
        if name not in budget["containers"]:
            continue
        if is_steady_state(budget, name):
            total += expected_rss_mb(budget, name)
    return total


def planned_entries(budget: dict[str, Any]) -> dict[str, Any]:
    """The `planned` mapping, minus its own `$comment` — every backlog placeholder this file
    prices ahead of any code existing for it. Empty if the budget file predates this key."""
    return {k: v for k, v in budget.get("planned", {}).items() if k != "$comment"}


def planned_total_mb(budget: dict[str, Any]) -> int:
    """Every `planned` entry's `kind`, summed — the *projected* addition to the budget once
    every backlog placeholder here becomes a real container. Never added to
    `expected_total_mb`'s own enforced total: a placeholder must never be able to fail a real
    deploy, only to be reported as headroom that is about to be spent."""
    total = 0
    for meta in planned_entries(budget).values():
        total += int(budget["kinds"][meta["kind"]]["expected_rss_mb"])
    return total


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sum memory-budget.json's expected_rss_mb, by profile or by container name."
    )
    parser.add_argument("budget_file", help="path to memory-budget.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--profiles",
        default=None,
        metavar="CSV",
        help="comma-separated COMPOSE_PROFILES/EDGE_PROFILES value; empty string means core only",
    )
    mode.add_argument(
        "--containers",
        default=None,
        metavar="CSV",
        help="comma-separated actual running container names (e.g. from `docker ps`)",
    )
    parser.add_argument(
        "--with-planned",
        action="store_true",
        help="add every backlog placeholder in the budget's planned key on top (projected, never"
        " enforced)",
    )
    return parser


def _main(argv: list[str]) -> int:
    """CLI. See `--help`. Prints the expected total (MB) to stdout and exits 0."""
    args = _build_parser().parse_args(argv[1:])
    try:
        budget = load_budget(Path(args.budget_file))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"memory_budget_totals: could not read {args.budget_file}: {exc}", file=sys.stderr)
        return 1
    if args.containers is not None:
        names = {c for c in args.containers.split(",") if c}
        total = expected_total_for_names(budget, names)
    else:
        enabled = {p for p in (args.profiles or "").split(",") if p}
        total = expected_total_mb(budget, enabled)
    if args.with_planned:
        total += planned_total_mb(budget)
    print(total)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
