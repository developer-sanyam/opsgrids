"""R1(c) of the final security review of the operator-MFA slice: the deploy reseals every MFA
factor before the new auth container serves (`lib/mfa-reseal.sh`, called from `remote.sh migrate`).

Without it, a box deployed from `main` — where every TOTP secret is raw plaintext — has every
tenant member with MFA locked out from the moment the new containers come up. The library is
sourced directly, with a fake `docker` first on `PATH` that records what it was asked to run and
answers the count query from a scripted sequence: the real function text, not a copy of it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
LIB = EDGE_DIR / "lib/mfa-reseal.sh"
REMOTE_SH = EDGE_DIR / "remote.sh"
RUNBOOK = EDGE_DIR / "RUNBOOK.md"

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
*" psql "*)
	[ "${FAKE_PSQL_FAIL:-0}" = 1 ] && exit 2
	idx=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)
	IFS=, read -ra counts <<<"$FAKE_COUNTS"
	printf '%s\\n' "${counts[$idx]:-0}"
	echo $((idx + 1)) >"$FAKE_STATE"
	;;
*"mfa_maintenance reseal"*) exit "${FAKE_RESEAL_EXIT:-0}" ;;
esac
"""


def _run(
    tmp_path: Path, *, counts: str, reseal_exit: int = 0, psql_fail: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    script = (
        "set -euo pipefail\n"
        'log() { printf "\\n=== %s\\n" "$*"; }\n'
        "COMPOSE=(docker compose)\n"
        f'source "{LIB}"\n'
        "reseal_mfa_factors_if_pending\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_STATE": str(tmp_path / "state"),
        "FAKE_COUNTS": counts,
        "FAKE_RESEAL_EXIT": str(reseal_exit),
        "FAKE_PSQL_FAIL": "1" if psql_fail else "0",
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
    )
    return result, log.read_text().splitlines() if log.exists() else []


def _runs(calls: list[str]) -> list[str]:
    return [c for c in calls if " run " in f" {c} "]


def test_nothing_to_reseal_starts_no_container(tmp_path: Path) -> None:
    """Every deploy after the first: the count is 0, so no one-off container is started."""
    result, calls = _run(tmp_path, counts="0")
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stdout
    assert _runs(calls) == []


def test_plaintext_factors_are_resealed_from_a_one_off_container_with_the_flag(
    tmp_path: Path,
) -> None:
    result, calls = _run(tmp_path, counts="3,0")
    assert result.returncode == 0, result.stderr
    (run,) = _runs(calls)
    assert "-e MFA_ACCEPT_LEGACY_PLAINTEXT=true" in run
    assert run.endswith("auth python -m app.mfa_maintenance reseal")
    assert "--rm" in run, "a one-off container, never the long-running auth service"
    assert "0 remain" in result.stdout


def test_the_flag_is_only_ever_passed_to_the_one_off_run(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, counts="2,0")
    assert result.returncode == 0, result.stderr
    with_flag = [c for c in calls if "MFA_ACCEPT_LEGACY_PLAINTEXT" in c]
    assert len(with_flag) == 1 and " run " in f" {with_flag[0]} "


def test_it_counts_before_and_after(tmp_path: Path) -> None:
    _result, calls = _run(tmp_path, counts="4,0")
    psql = [c for c in calls if " psql " in f" {c} "]
    assert len(psql) == 2, "a count before to decide, and a count after to prove it reached 0"


def test_a_failed_reseal_aborts_the_deploy(tmp_path: Path) -> None:
    result, _calls = _run(tmp_path, counts="3,3", reseal_exit=1)
    assert result.returncode != 0
    assert "refusing to serve auth" in result.stderr


def test_a_reseal_that_leaves_plaintext_behind_aborts_the_deploy(tmp_path: Path) -> None:
    """The script itself exits 0 here (as far as the caller can tell); the post-count is the
    independent proof, and it is what stops the new auth serving rows it refuses."""
    result, _calls = _run(tmp_path, counts="3,2")
    assert result.returncode != 0
    assert "left 2 plaintext factor(s)" in result.stderr


def test_an_uncountable_database_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A brand-new box has no auth schema until migrate has run — and nothing to reseal."""
    result, calls = _run(tmp_path, counts="0", psql_fail=True)
    assert result.returncode == 0, result.stderr
    assert "cannot count" in result.stdout
    assert _runs(calls) == []


def _function_text(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start : source.index("\n}\n", start) + 3]


def test_remote_sh_sources_the_library_and_calls_it_inside_migrate_before_up() -> None:
    text = REMOTE_SH.read_text()
    assert "source lib/mfa-reseal.sh" in text
    migrate = _function_text(text, "migrate")
    assert migrate.rstrip().endswith("reseal_mfa_factors_if_pending\n}"), (
        "the reseal is the last thing migrate does — after every schema is current"
    )
    deploy = text[text.index("deploy)") :]
    assert deploy.index("\tmigrate\n") < deploy.index("\tup\n"), "reseal happens before `up`"


def test_the_count_sql_in_the_runbook_is_the_one_the_deploy_runs() -> None:
    sql = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; printf %s "$MFA_PLAINTEXT_COUNT_SQL"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert re.search(r"encode\(secret_enc,'escape'\)", sql)
    assert sql in RUNBOOK.read_text(), "operators are told to run this exact statement by hand"


def test_the_live_auth_container_can_never_be_given_the_plaintext_flag() -> None:
    """The RUNBOOK states the flag exists only as a `-e` on the one-off reseal run. Nothing in the
    compose file, the shared service environment or the edge `.env.example` may name it, so no
    deploy can hand it to the long-running container."""
    for name in ("compose.prod.yml", ".env.example"):
        assert "MFA_ACCEPT_LEGACY_PLAINTEXT" not in (EDGE_DIR / name).read_text(), name
