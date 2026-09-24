"""m6 (round 3): every `./bin/worker` service in `compose.prod.yml` must have its own
`{schema}.events` DLQ queue known to `config.py`'s `CORE_DLQ_QUEUES`/`PROFILE_DLQ_QUEUES` — the
same drift `test_ops_monitor_profile_containers.py` already guards against on the API side,
derived here from the compose file's own worker services rather than hand-copied, so a worker
added to `compose.prod.yml` without updating `config.py` fails this test by name (and a queue
left behind in `config.py` after its worker is removed fails it too — see
`test_no_dlq_queue_in_config_belongs_to_a_worker_that_no_longer_exists`).

`notification-worker`'s own second queue (`notification.dispatch`, its dispatcher consumer
alongside the ordinary events one — see `compose.prod.yml`'s own comment above that service) is
not derivable from the service name at all; it is asserted separately, by name, rather than this
file inventing a second naming convention just for one service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"
MEMORY_BUDGET = REPO_ROOT / "infrastructure/edge/memory-budget.json"

sys.path.insert(0, str(REPO_ROOT / "libs/platform-core/src"))
from platform_core.ops_monitor.config import (  # noqa: E402
    CORE_DLQ_QUEUES,
    PROFILE_DLQ_QUEUES,
)


def _budget() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(MEMORY_BUDGET.read_text())
    return result


def _worker_services() -> dict[str, str | None]:
    """`{schema: profile}` for every service whose `command` is exactly `['./bin/worker']` —
    `profile` is `None` for a core service (no `profiles:` key at all, the same "always on"
    convention every other core container in `compose.prod.yml` uses).

    A worker behind `split-workers` (the worker-folding follow-up to the memory-headroom slice,
    2026-09-24) is the *rollback* for a schema whose loops now run inside its own API container
    by default (`RUN_CONSUMERS_IN_API`) — the queue's real, default-configuration consumer is
    that API container, not this one, so the profile that actually justifies the queue in
    `config.py` is the API container's own profile (`memory-budget.json`'s `containers[schema]`),
    looked up here rather than read off this container's own `profiles:` key.

    `scheduler` carries two profiles (`['finance', 'split-workers']`, the same round): it is not
    itself folded (it has no API container to fold into — see its own comment in
    `compose.prod.yml`) but a *different* worker, `finance-worker`, depends on it existing, which
    means it must also come up whenever `split-workers` is the only active profile. Its own,
    non-`split-workers` profile is still `finance` — this test picks the first profile that is
    not `split-workers` for exactly that reason, rather than assuming exactly one profile."""
    raw: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text())
    budget = _budget()
    workers: dict[str, str | None] = {}
    for name, spec in raw["services"].items():
        if spec.get("command") != ["./bin/worker"]:
            continue
        profiles = spec.get("profiles")
        schema = name.removesuffix("-worker")
        if not profiles:
            workers[schema] = None
            continue
        real_profiles = [p for p in profiles if p != "split-workers"]
        assert len(real_profiles) <= 1, (
            f"{name}: this test assumes at most one non-split-workers profile, got {profiles!r}"
        )
        if real_profiles:
            workers[schema] = real_profiles[0]
        else:
            # Every listed profile is `split-workers` — this container's own profile is nothing
            # but the rollback, so the schema's real, default-configuration profile is its API
            # container's own (memory-budget.json's `containers[schema]`).
            workers[schema] = budget["containers"][schema]["profile"]
    return workers


def test_every_bin_worker_service_has_its_own_events_queue_known_to_config() -> None:
    workers = _worker_services()
    assert workers, "found no './bin/worker' services at all -- this test would pass vacuously"
    for schema, profile in workers.items():
        expected = f"{schema}.events"
        known = CORE_DLQ_QUEUES if profile is None else PROFILE_DLQ_QUEUES.get(profile, ())
        assert expected in known, (
            f"compose.prod.yml runs a './bin/worker' service for {schema!r} (profile "
            f"{profile!r}) but config.py's DLQ queue tables do not list {expected!r} — a "
            "consumer with no monitored DLQ silently drops event-processing failures"
        )


def test_notification_dispatch_is_the_one_hand_named_exception() -> None:
    """`notification-worker` runs two consumers on one `./bin/worker` process — `events` (the
    ordinary, derivable queue, covered above) and `dispatch` (its own, which no naming
    convention here would ever produce from the service name alone)."""
    assert "notification.dispatch" in CORE_DLQ_QUEUES


def test_no_dlq_queue_in_config_belongs_to_a_worker_that_no_longer_exists() -> None:
    """The reverse direction: a queue left behind in `config.py` for a schema that
    `compose.prod.yml` no longer runs a `./bin/worker` service for is exactly the drift this
    file exists to catch too, not just a missing addition."""
    workers = _worker_services()

    core_events_queues = {
        f"{schema}.events" for schema, profile in workers.items() if profile is None
    }
    for queue in CORE_DLQ_QUEUES:
        if queue == "notification.dispatch":
            continue
        assert queue in core_events_queues, (
            f"config.CORE_DLQ_QUEUES names {queue!r} but no core './bin/worker' service in "
            "compose.prod.yml produces it any more"
        )

    for profile, queues in PROFILE_DLQ_QUEUES.items():
        profile_events_queues = {
            f"{schema}.events" for schema, p in workers.items() if p == profile
        }
        for queue in queues:
            assert queue in profile_events_queues, (
                f"config.PROFILE_DLQ_QUEUES[{profile!r}] names {queue!r} but no "
                f"'./bin/worker' service under that profile in compose.prod.yml produces it "
                "any more"
            )
