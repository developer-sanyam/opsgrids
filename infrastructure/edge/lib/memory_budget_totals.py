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


def unpriced_container_names(budget: dict[str, Any], container_names: set[str]) -> set[str]:
    """Which of `container_names` have no entry in `memory-budget.json`'s own `containers`
    mapping — the exact gap `test_every_compose_service_is_in_the_budget` exists to close before
    a deploy ever gets here, surfaced again at deploy time in case that gate was ever bypassed
    (a hotfix on the box itself, a compose file edited by hand). Never raises: a caller decides
    what to do about an unpriced name (M7 — price it conservatively and warn, rather than either
    silently skip it or crash a check that exists to keep the box up)."""
    return {name for name in container_names if name not in budget["containers"]}


def expected_total_for_names(budget: dict[str, Any], container_names: set[str]) -> int:
    """Sum `expected_rss_mb` for an explicit set of container names — no profile involved.

    A name this budget does not price is **not** skipped (M7, review round 2 — reversing the
    original version of this function): the caller (`docker ps`, filtered by this compose
    project's own label) has already scoped `container_names` to containers that are definitely
    ours, so an unpriced one is not a stray container from an unrelated project sharing the
    host — it is one of ours that `memory-budget.json` has fallen behind on. Pricing it at zero
    would understate the box by exactly the amount that gap costs; pricing it at `kinds.
    background`'s rate (a real, measured process cost, used as this file's fallback for "some
    kind of process, unknown which") keeps the total honest, while `unpriced_container_names` is
    what a caller uses to also warn about the gap by name, so it gets fixed rather than quietly
    tolerated forever.
    """
    total = 0
    background_mb = int(budget["kinds"]["background"]["expected_rss_mb"])
    for name in container_names:
        if name not in budget["containers"]:
            total += background_mb
            continue
        if is_steady_state(budget, name):
            total += expected_rss_mb(budget, name)
    return total


def post_deploy_container_names(
    *, running: set[str], all_services: set[str], declared_services: set[str]
) -> set[str]:
    """The set of containers that will actually be resident once this deploy finishes (B1,
    review round 2): whatever is running today that the *new* compose file still defines, union
    whatever the box's declared `COMPOSE_PROFILES`/`EDGE_PROFILES` will bring up.

    Neither half alone is honest. `running` on its own prices the *past* — a deploy that adds a
    profile has not started that profile's containers yet, so they would not be counted, and a
    deploy refusing itself over its own arithmetic could never happen because the arithmetic
    never sees what it is about to add. `declared_services` on its own drops orphans: a
    container still running from a profile a previous, incomplete change removed from
    `COMPOSE_PROFILES` (H2 — compose does not stop those on its own) would go uncounted the
    moment the declared list no longer mentions it, understating the box exactly the way pricing
    by configured profiles alone always did.

    `running ∩ all_services` is the "survives the deploy" half: a container running today that
    the new compose file has removed as a *service* entirely (a real rename or deletion, not a
    profile toggle) is excluded, because compose's own `--remove-orphans` on `up` actually does
    tear those down — unlike the profile-filtered case this function exists to catch.
    """
    return (running & all_services) | declared_services


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
    mode.add_argument(
        "--post-deploy",
        action="store_true",
        help="union mode (B1): needs --running, --all-services and --declared-services together",
    )
    parser.add_argument(
        "--running",
        default=None,
        metavar="CSV",
        help="--post-deploy: comma-separated container names actually running now (`docker ps`)",
    )
    parser.add_argument(
        "--all-services",
        default=None,
        metavar="CSV",
        help="--post-deploy: every service the new compose file defines, any profile "
        "(`docker compose config --services` with every profile enabled)",
    )
    parser.add_argument(
        "--declared-services",
        default=None,
        metavar="CSV",
        help="--post-deploy: services the new compose file configures for the box's declared "
        "profiles (`docker compose config --services`)",
    )
    parser.add_argument(
        "--with-planned",
        action="store_true",
        help="add every backlog placeholder in the budget's planned key on top (projected, never"
        " enforced)",
    )
    return parser


def _csv_set(value: str | None) -> set[str]:
    return {v for v in (value or "").split(",") if v}


def _main(argv: list[str]) -> int:
    """CLI. See `--help`. Prints the expected total (MB) to stdout and exits 0."""
    args = _build_parser().parse_args(argv[1:])
    try:
        budget = load_budget(Path(args.budget_file))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"memory_budget_totals: could not read {args.budget_file}: {exc}", file=sys.stderr)
        return 1
    if args.post_deploy:
        if args.running is None or args.all_services is None or args.declared_services is None:
            print(
                "memory_budget_totals: --post-deploy needs --running, --all-services and "
                "--declared-services",
                file=sys.stderr,
            )
            return 2
        names = post_deploy_container_names(
            running=_csv_set(args.running),
            all_services=_csv_set(args.all_services),
            declared_services=_csv_set(args.declared_services),
        )
        total = expected_total_for_names(budget, names)
    elif args.containers is not None:
        total = expected_total_for_names(budget, _csv_set(args.containers))
    else:
        total = expected_total_mb(budget, _csv_set(args.profiles))
    if args.with_planned:
        total += planned_total_mb(budget)
    print(total)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
