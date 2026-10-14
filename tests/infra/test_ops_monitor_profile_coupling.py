"""m10 (round 3): the profile list `ops-monitor` is told it should be watching must be the same
list the deploy actually runs — not "usually the same".

`ops-monitor`'s `COMPOSE_PROFILES` environment value is what `OpsMonitorSettings` derives its
expected API containers and DLQ queues from (`config.expected_api_containers`,
`expected_dlq_queues`); `remote.sh` is what decides what `docker compose` runs. If those two ever
read different variables, a box could run one set of products and page about another — exactly
the silent divergence B1 (round 2) was fixing at the container level.

The memory-headroom slice has merged: `remote.sh` resolves `EDGE_PROFILES`, an explicit override,
and **exports it as `COMPOSE_PROFILES`** so that it beats the value in `.env` for every
`docker compose` call. The mechanism this file pins — "an exported `COMPOSE_PROFILES` wins over
`--env-file`, and `ops-monitor` renders whatever compose resolved" — is what that relies on. The
last test below asserts that `EDGE_PROFILES` ends in an exported `COMPOSE_PROFILES`, since
otherwise ops-monitor would watch the wrong box.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
COMPOSE_FILE = EDGE_DIR / "compose.prod.yml"
ENV_EXAMPLE = EDGE_DIR / ".env.example"
REMOTE_SH = EDGE_DIR / "remote.sh"

sys.path.insert(0, str(REPO_ROOT / "libs/platform-core/src"))
from platform_core.ops_monitor.config import (  # noqa: E402
    OpsMonitorSettings,
    expected_api_containers,
)

needs_docker_compose = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode
    != 0,
    reason="docker compose CLI not available (config rendering needs no daemon, only the CLI)",
)


def _render(compose_profiles: str | None) -> dict[str, Any]:
    """`docker compose config --format json` for this file with `.env.example` as the env
    file, and `COMPOSE_PROFILES` **exported in the process environment** when given — the same
    shape `remote.sh` uses to make a profile list beat the one in `.env`."""
    import os

    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
    if compose_profiles is not None:
        env["COMPOSE_PROFILES"] = compose_profiles
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(ENV_EXAMPLE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=EDGE_DIR,
    )
    rendered: dict[str, Any] = json.loads(result.stdout)
    return rendered


def test_ops_monitor_passes_the_bare_compose_profiles_variable_through() -> None:
    """Static half: not `${EDGE_PROFILES:-}`, not a literal, not a differently-named copy."""
    raw = yaml.safe_load(COMPOSE_FILE.read_text())
    value = raw["services"]["ops-monitor"]["environment"]["COMPOSE_PROFILES"]
    assert value == "${COMPOSE_PROFILES:-}", (
        f"ops-monitor's COMPOSE_PROFILES is {value!r}; it must be exactly the variable "
        "docker compose itself reads to decide what runs, or the monitor and the deploy can "
        "disagree about what should exist"
    )


@needs_docker_compose
def test_an_exported_value_beats_the_env_file_and_reaches_ops_monitor() -> None:
    """The mechanism remote.sh's profile resolution relies on: whatever it exports is what
    both compose (which services run) and ops-monitor (which services are expected) see."""
    rendered = _render("hrms,finance")
    assert rendered["services"]["ops-monitor"]["environment"]["COMPOSE_PROFILES"] == "hrms,finance"
    # `.env.example` itself lists every profile; the exported value narrowed it, and compose's
    # own service list narrowed with it — same source, same answer.
    assert "approval" in rendered["services"]
    assert "operations" not in rendered["services"]


@needs_docker_compose
def test_what_ops_monitor_expects_is_what_compose_runs_for_the_same_profiles() -> None:
    """For each profile combination, every API container ops-monitor would probe must be a
    service compose would actually start for that same `COMPOSE_PROFILES` — including the
    wildcard, which `OpsMonitorSettings` expands itself (m10) and docker compose expands
    natively."""
    # `hrms,*` and `*,hrms`: docker compose treats `*` as "every profile" wherever it sits in the
    # list (post-review fixup, finding 4), and so must the monitor's own expansion.
    for profiles in ("", "hrms,finance", "*", "hrms,*", "*,hrms"):
        rendered = _render(profiles)
        rendered_profiles = rendered["services"]["ops-monitor"]["environment"]["COMPOSE_PROFILES"]
        assert rendered_profiles == profiles
        settings = OpsMonitorSettings(compose_profiles=rendered_profiles)
        running = set(rendered["services"])
        expected = set(settings.ops_monitor_api_containers)
        assert expected <= running, (
            f"COMPOSE_PROFILES={profiles!r}: ops-monitor would probe {sorted(expected - running)} "
            "but compose does not run them for that profile list"
        )


@needs_docker_compose
def test_the_wildcard_makes_ops_monitor_expect_every_api_container_compose_runs() -> None:
    """The reverse direction, for the wildcard only: with everything enabled, no API
    container compose defines may be missing from the monitor's expectations — the failure
    this guards is the silent one (a box running every product, monitored as core-only)."""
    rendered = _render("*")
    expected = set(expected_api_containers(["*"]))
    for name, service in rendered["services"].items():
        if name in expected:
            continue
        # A container that serves HTTP on the shared port carries the shared `/health/ready`
        # healthcheck; workers, crons, relays and infrastructure do not.
        healthcheck = json.dumps(service.get("healthcheck", {}))
        assert "health/ready" not in healthcheck, (
            f"compose runs {name!r} with a /health/ready healthcheck under COMPOSE_PROFILES=* "
            "but ops-monitor's expected API containers do not include it"
        )


def test_remote_sh_has_no_second_profile_variable_that_ops_monitor_would_not_see() -> None:
    """The memory-headroom slice has merged, so `remote.sh` has `EDGE_PROFILES`. The resolved
    value must be **exported as `COMPOSE_PROFILES`** (compose interpolation, and so `ops-monitor`,
    reads nothing else), and no other `*_PROFILES` variable may be mentioned."""
    import re

    text = REMOTE_SH.read_text()
    profile_vars = set(re.findall(r"\b([A-Z][A-Z_]*_PROFILES)\b", text))
    assert "EDGE_PROFILES" in profile_vars
    assert profile_vars <= {"COMPOSE_PROFILES", "EDGE_PROFILES"}, (
        f"remote.sh reads unexpected profile variable(s) {sorted(profile_vars)} "
        "that ops-monitor does not"
    )
    assert "export COMPOSE_PROFILES=" in text, (
        "remote.sh resolves EDGE_PROFILES but never exports COMPOSE_PROFILES, so compose "
        "and ops-monitor would not see the resolved list"
    )
