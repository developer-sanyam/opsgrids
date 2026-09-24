"""B1 (round 2): `platform_core.ops_monitor.config`'s `CORE_API_CONTAINERS`/
`PROFILE_API_CONTAINERS` (and the DLQ-queue counterparts) are a static copy of
`infrastructure/edge/memory-budget.json`'s own container -> kind -> profile mapping, kept as a
second, static copy on purpose (ops-monitor must know what should exist even if that JSON file
is unreadable — see `config.py`'s own docstring). This is the gate that keeps the two in sync: a
container added to `compose.prod.yml`/`memory-budget.json` and not mirrored here fails by name.

DLQ queues are not in `memory-budget.json` at all (it prices memory, not event-consumption), so
`PROFILE_DLQ_QUEUES`/`CORE_DLQ_QUEUES` are checked only for "every queue names a container that
this same file says is a background/worker container" — a weaker, but still real, consistency
check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
MEMORY_BUDGET = EDGE_DIR / "memory-budget.json"

sys.path.insert(0, str(REPO_ROOT / "libs/platform-core/src"))
from platform_core.ops_monitor.config import (  # noqa: E402
    CORE_API_CONTAINERS,
    CORE_DLQ_QUEUES,
    PROFILE_API_CONTAINERS,
    PROFILE_DLQ_QUEUES,
)


def _budget() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(MEMORY_BUDGET.read_text())
    return result


#: `api_with_worker` (the worker-folding follow-up to the memory-headroom slice, 2026-09-24) is
#: still an ordinary API container as far as ops-monitor's readiness check is concerned — the
#: kind split is a *pricing* distinction (it costs a little more, folding a former `*-worker`
#: container's loops in), not a container-role one. Both kinds belong in the same bucket here.
_API_KINDS = ("api", "api_with_worker")


def _api_containers_by_profile() -> dict[str | None, list[str]]:
    budget = _budget()
    by_profile: dict[str | None, list[str]] = {}
    for name, meta in budget["containers"].items():
        if meta.get("kind") not in _API_KINDS:
            continue
        by_profile.setdefault(meta.get("profile"), []).append(name)
    return by_profile


def test_core_api_containers_matches_the_profile_null_api_containers() -> None:
    by_profile = _api_containers_by_profile()
    assert set(CORE_API_CONTAINERS) == set(by_profile.get(None, [])), (
        "config.CORE_API_CONTAINERS has drifted from memory-budget.json's own profile:null, "
        "kind:api containers — update both together"
    )


def test_every_profile_in_profile_api_containers_matches_memory_budget() -> None:
    by_profile = _api_containers_by_profile()
    for profile, containers in PROFILE_API_CONTAINERS.items():
        assert set(containers) == set(by_profile.get(profile, [])), (
            f"config.PROFILE_API_CONTAINERS[{profile!r}] = {containers} does not match "
            f"memory-budget.json's kind:api containers for that profile: "
            f"{by_profile.get(profile, [])}"
        )


def test_no_profile_is_missing_from_profile_api_containers() -> None:
    by_profile = _api_containers_by_profile()
    known_profiles = set(PROFILE_API_CONTAINERS) | {None}
    missing = set(by_profile) - known_profiles
    assert not missing, (
        f"memory-budget.json has kind:api containers under profile(s) {missing} that "
        "config.PROFILE_API_CONTAINERS does not know about"
    )


def test_every_dlq_queue_names_a_real_background_container() -> None:
    """Not a byte-for-byte mirror (memory-budget.json has no notion of queue names) — just:
    every queue in PROFILE_DLQ_QUEUES/CORE_DLQ_QUEUES belongs to a profile that memory-budget.json
    actually lists a background OR api_with_worker container for, so a queue cannot survive a
    profile's consumer being removed from compose.prod.yml without this failing. `api_with_worker`
    counts too, since the worker-folding follow-up to the memory-headroom slice (2026-09-24) moved
    several of these queues' consumers into their own API container by default
    (RUN_CONSUMERS_IN_API) — the queue itself, and the DLQ depth this checks, does not care which
    container binds it, only that something does; `split-workers` (the `*-worker` containers'
    rollback profile) is deliberately not in this set, since a queue must be covered by the
    *default* configuration, not only by the rollback."""
    budget = _budget()
    background_profiles = {
        meta.get("profile")
        for meta in budget["containers"].values()
        if meta.get("kind") in ("background", "api_with_worker")
    }
    for profile in PROFILE_DLQ_QUEUES:
        assert profile in background_profiles, (
            f"config.PROFILE_DLQ_QUEUES names profile {profile!r}, but memory-budget.json has "
            "no background container under that profile"
        )
    if CORE_DLQ_QUEUES:
        assert None in background_profiles, (
            "config.CORE_DLQ_QUEUES is non-empty but memory-budget.json has no profile:null "
            "background container to consume from"
        )
