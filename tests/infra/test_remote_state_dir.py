"""m8 (round 3): `remote.sh`'s `ensure_ops_monitor_state_dir` and `lib/numeric-id.sh`.

The function asks the `product` image for its uid and gid and hands them to a `chown -R` that
runs as root on the production box. Before this fix it asked only for the uid, used it as both
halves of `uid:gid`, interpolated it into a `sh -c` string unchecked, and chowned only the top
directory. These tests pin the four things that changed:

* both ids are validated as digits before anything runs as root;
* the gid is asked for (`id -g`), not assumed equal to the uid;
* the ownership change is recursive (`chown -R`);
* it is passed as argv (`--entrypoint chown`), so no `sh -c` string exists to inject into.

`remote.sh` itself cannot be sourced (it `cd`s and executes at the top level), so the function is
sliced out of it verbatim and run in a throwaway shell with a fake `docker` first on `PATH` that
records what it was asked to run. That is the real function text, not a copy of it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
REMOTE_SH = EDGE_DIR / "remote.sh"
NUMERIC_ID_LIB = EDGE_DIR / "lib/numeric-id.sh"


def _require_numeric_id(label: str, value: str) -> subprocess.CompletedProcess[str]:
    script = f'source "{NUMERIC_ID_LIB}"; require_numeric_id "$@"'
    return subprocess.run(
        ["bash", "-c", script, "bash", label, value], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1000", "1000"), ("0", "0"), ("65534", "65534"), ("1000\r", "1000"), ("  1001 ", "1001")],
)
def test_a_plain_number_is_accepted_and_normalised(raw: str, expected: str) -> None:
    result = _require_numeric_id("uid", raw)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected}\n"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "-1",
        "1000; rm -rf /",
        "1000 1001",
        "$(id)",
        "1000\nWARNING: something",
        "0x10",
        "1e3",
    ],
)
def test_anything_that_is_not_only_digits_is_refused_and_prints_nothing(raw: str) -> None:
    result = _require_numeric_id("uid", raw)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "uid is not a number" in result.stderr


def _function_text() -> str:
    text = REMOTE_SH.read_text()
    start = text.index("ensure_ops_monitor_state_dir() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run_ensure(
    tmp_path: Path, *, uid: str, gid: str
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *"--entrypoint id product -u") printf "%s\\n" "$FAKE_UID" ;;\n'
        '  *"--entrypoint id product -g") printf "%s\\n" "$FAKE_GID" ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    state_root = tmp_path / "state"
    script = (
        "set -euo pipefail\n"
        f'source "{NUMERIC_ID_LIB}"\n'
        "COMPOSE=(docker compose)\n"
        f"{_function_text()}\n"
        "ensure_ops_monitor_state_dir\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_UID": uid,
        "FAKE_GID": gid,
        "EDGE_STATE_DIR": str(state_root),
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls, state_root / "ops-monitor"


def test_it_asks_for_the_gid_separately_and_chowns_recursively_as_argv(tmp_path: Path) -> None:
    result, calls, state_dir = _run_ensure(tmp_path, uid="1001", gid="1002")

    assert result.returncode == 0, result.stderr
    assert any(call.endswith("--entrypoint id product -u") for call in calls)
    assert any(call.endswith("--entrypoint id product -g") for call in calls), (
        "the group must be asked for, not assumed equal to the user"
    )
    chown = [c for c in calls if c.startswith("run --rm --user 0")]
    assert len(chown) == 1
    assert "--entrypoint chown" in chown[0]
    assert "-R 1001:1002 /s" in chown[0], "must be recursive and use the real uid:gid pair"
    assert "sh -c" not in chown[0], "no shell string to interpolate into"
    assert state_dir.is_dir()


@pytest.mark.parametrize(
    ("uid", "gid"),
    [
        ("1001; touch /pwned", "1001"),
        ("1001", "1001; touch /pwned"),
        ("$(id)", "1001"),
        ("1001", ""),
        ("", "1001"),
        ("1001", "WARNING: banner"),
    ],
)
def test_a_non_numeric_id_aborts_before_anything_runs_as_root(
    tmp_path: Path, uid: str, gid: str
) -> None:
    result, calls, state_dir = _run_ensure(tmp_path, uid=uid, gid=gid)

    assert result.returncode != 0
    assert "is not a number" in result.stderr
    assert not any(c.startswith("run --rm --user 0") for c in calls), (
        "a root-owned chown must never be issued with an unvalidated id"
    )
    assert not state_dir.exists(), "validation must happen before the directory is touched"
