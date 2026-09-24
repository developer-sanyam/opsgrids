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

import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

sys.path.insert(0, str(REPO_ROOT / "libs/platform-core/src"))
from platform_core.ops_monitor.config import (  # noqa: E402
    CORE_DLQ_QUEUES,
    PROFILE_DLQ_QUEUES,
)


def _worker_services() -> dict[str, str | None]:
    """`{schema: profile}` for every service whose `command` is exactly `['./bin/worker']` —
    `profile` is `None` for a core service (no `profiles:` key at all, the same "always on"
    convention every other core container in `compose.prod.yml` uses)."""
    raw: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text())
    workers: dict[str, str | None] = {}
    for name, spec in raw["services"].items():
        if spec.get("command") != ["./bin/worker"]:
            continue
        profiles = spec.get("profiles")
        if profiles is not None:
            assert len(profiles) == 1, (
                f"{name}: this test assumes one profile per worker service, got {profiles!r}"
            )
        schema = name.removesuffix("-worker")
        workers[schema] = profiles[0] if profiles else None
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
