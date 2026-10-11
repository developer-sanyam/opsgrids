"""`remote.sh up` recreates the gateways before any service.

A service on a newer platform-core refuses a hop token an older gateway minted (its `fwd` digest
binds headers the old gateway does not sign), so while the two mix every authenticated request
is a 401 and every signed-in user is bounced to the login screen. The reverse mix, a new gateway
in front of an old service, is harmless: the old service ignores the extra claim. So the
gateways go first, on their own and without their dependencies, and only then everything else.

`up` is sliced out of `remote.sh` verbatim (the script cannot be sourced) and run against a fake
`docker` that records what it was asked to do.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REMOTE_SH = Path(__file__).resolve().parents[2] / "infrastructure/edge/remote.sh"


def _up_text() -> str:
    text = REMOTE_SH.read_text()
    start = text.index("\nup() {") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _run_up(tmp_path: Path, active_services: list[str]) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\n'
        'case "$*" in *"config --services") printf "%s\\n" $FAKE_SERVICES ;; esac\n'
    )
    fake.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        "COMPOSE=(docker compose)\n"
        "log() { :; }\nensure_ops_monitor_state_dir() { :; }\nup_parallel_limit() { echo 4; }\n"
        f"{_up_text()}\n"
        "up\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_SERVICES": " ".join(active_services),
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr
    return [c for c in log.read_text().splitlines() if " up " in f" {c} "]


def test_the_gateways_are_recreated_alone_before_everything_else(tmp_path: Path) -> None:
    ups = _run_up(tmp_path, ["auth", "app-gateway", "billing", "admin-gateway"])

    assert len(ups) == 2, ups
    assert ups[0] == "compose up -d --no-deps app-gateway admin-gateway"
    assert ups[1] == "compose up -d --remove-orphans"


def test_a_gateway_whose_profile_is_off_is_not_started_by_naming_it(tmp_path: Path) -> None:
    ups = _run_up(tmp_path, ["auth", "app-gateway", "billing"])

    assert ups[0] == "compose up -d --no-deps app-gateway"
