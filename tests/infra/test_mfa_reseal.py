"""R1(c) of the final security review of the operator-MFA slice: the deploy reseals every MFA
factor before the new auth container serves (`lib/mfa-reseal.sh`, called from `remote.sh migrate`
and again after `health`).

Without it, a box deployed from `main` — where every TOTP secret is raw plaintext — has every
tenant member with MFA locked out from the moment the new containers come up. The library is
sourced directly, with a fake `docker` first on `PATH` that records what it was asked to run and
answers the queries from a script: the real function text, not a copy of it.
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

#: `to_regclass` answers from FAKE_TABLE ("t"/"f"); the count queries answer from the scripted
#: FAKE_COUNTS sequence; FAKE_PSQL_FAIL makes every psql call fail with a message on stderr.
FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
*" psql "*)
	if [ "${FAKE_PSQL_FAIL:-0}" = 1 ]; then
		echo "psql: error: connection to server failed (fake)" >&2
		exit 2
	fi
	case "$*" in
	*to_regclass*) printf '%s\\n' "${FAKE_TABLE:-t}" ;;
	*)
		idx=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)
		IFS=, read -ra counts <<<"$FAKE_COUNTS"
		printf '%s\\n' "${counts[$idx]:-0}"
		echo $((idx + 1)) >"$FAKE_STATE"
		;;
	esac
	;;
*"mfa_maintenance reseal"*) exit "${FAKE_RESEAL_EXIT:-0}" ;;
esac
"""


def _run(
    tmp_path: Path,
    *,
    counts: str = "0",
    reseal_exit: int = 0,
    psql_fail: bool = False,
    table: str = "t",
    times: int = 1,
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
        f'source "{LIB}"\n' + "reseal_mfa_factors_if_pending\n" * times
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_STATE": str(tmp_path / "state"),
        "FAKE_COUNTS": counts,
        "FAKE_TABLE": table,
        "FAKE_RESEAL_EXIT": str(reseal_exit),
        "FAKE_PSQL_FAIL": "1" if psql_fail else "0",
    }
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
    )
    return result, log.read_text().splitlines() if log.exists() else []


def _runs(calls: list[str]) -> list[str]:
    return [c for c in calls if " run " in f" {c} "]


def _count_queries(calls: list[str]) -> list[str]:
    return [c for c in calls if "count(*)" in c]


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
    assert len(_count_queries(calls)) == 2, "a count before to decide, one after to prove it"


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


def test_a_table_that_really_is_absent_is_skipped(tmp_path: Path) -> None:
    """A brand-new box has no auth schema until migrate has run — and nothing to reseal."""
    result, calls = _run(tmp_path, table="f")
    assert result.returncode == 0, result.stderr
    assert "does not exist yet" in result.stdout
    assert _runs(calls) == []
    assert _count_queries(calls) == [], "no count is attempted against a table that is not there"


def test_a_real_psql_error_is_not_read_as_nothing_to_do(tmp_path: Path) -> None:
    """Item 3 of the second fixup: 'cannot count' used to skip. A postgres that is down, or a
    permissions error, must fail the deploy — and show psql's own message, not swallow it."""
    result, calls = _run(tmp_path, psql_fail=True)
    assert result.returncode != 0
    assert "refusing to continue" in result.stderr
    assert "connection to server failed (fake)" in result.stderr, "psql's stderr is not hidden"
    assert _runs(calls) == []


def test_running_it_again_after_a_reseal_is_a_no_op(tmp_path: Path) -> None:
    """Item 4: `deploy` calls it a second time after `health`. With nothing left to reseal the
    second call starts no container (idempotent)."""
    result, calls = _run(tmp_path, counts="2,0,0", times=2)
    assert result.returncode == 0, result.stderr
    assert len(_runs(calls)) == 1


def test_the_second_call_reseals_a_straggler_enrolled_through_the_old_container(
    tmp_path: Path,
) -> None:
    result, calls = _run(tmp_path, counts="0,1,0", times=2)
    assert result.returncode == 0, result.stderr
    assert len(_runs(calls)) == 1, "the first call skipped; the second found and resealed one"


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


def test_deploy_reseals_again_after_health_and_only_warns_if_that_fails() -> None:
    text = REMOTE_SH.read_text()
    deploy = text[text.index("deploy)") : text.index("previous-ref)")]
    health = deploy.index("\thealth\n")
    again = deploy.index("reseal_mfa_factors_if_pending")
    record = deploy.index("\trecord_ref\n")
    assert health < again < record
    line = next(ln for ln in deploy.splitlines() if "reseal_mfa_factors_if_pending" in ln)
    assert "||" in line, "a straggler sweep must not fail a deploy that is already up and healthy"


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


def test_the_old_image_claim_is_the_true_one() -> None:
    """Item 2: the old `_accept_second_factor` decodes `secret_enc` as UTF-8 before recovery
    codes, so for the seconds between the reseal and `up` — and after a rollback to a pre-sealing
    ref — a sealed envelope makes BOTH authenticator and recovery-code sign-in a 500. The
    library and the RUNBOOK must not claim a recovery code still works there."""
    for path in (LIB, RUNBOOK):
        text = path.read_text()
        assert "recovery code still works, since those are hashes" not in text, path
        assert "a recovery code still works." not in text, path


def test_the_runbook_warns_that_rolling_back_past_this_release_breaks_mfa() -> None:
    text = RUNBOOK.read_text()
    section = text[text.index("### Roll back a bad deploy") :]
    section = section[: section.index("\n### ", 5)]
    assert "no unseal tool" in section
    assert "roll forward" in section
