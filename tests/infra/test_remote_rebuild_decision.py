"""Pins `needs_full_rebuild` (infrastructure/edge/lib/rebuild-decision.sh), the rule
`remote.sh`'s `build()` uses to decide "rebuild every image" vs "just the services that
changed". Getting this rule wrong means production silently runs stale images — see the
comment beside the function itself — and until this file existed nothing tested it at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _helpers import baseline_for
from conftest import REBUILD_LIB, GitRepo

BASELINE_FILES = {
    "services/checkout/app.py": "v1\n",
    "libs/platform-core/src/core.py": "v1\n",
    "libs/contracts/schema.json": "v1\n",
    "pyproject.toml": "v1\n",
    "uv.lock": "v1\n",
    ".dockerignore": "v1\n",
    "README.md": "v1\n",
}


def needs_full_rebuild(repo: str, prev: str, nxt: str) -> bool:
    """Shells out to the real function rather than a reimplementation of it."""
    script = f'source "{REBUILD_LIB}"; needs_full_rebuild "$1" "$2" "$3"'
    result = subprocess.run(
        ["bash", "-c", script, "_", repo, prev, nxt],
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), (
        f"needs_full_rebuild exited {result.returncode}, expected 0 or 1: {result.stderr}"
    )
    return result.returncode == 0


_baseline = baseline_for(BASELINE_FILES)


def test_change_under_platform_core_src_is_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(
        base, {"libs/platform-core/src/core.py": "v2\n"}, "touch platform-core"
    )
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is True


def test_change_to_uv_lock_is_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(base, {"uv.lock": "v2\n"}, "bump a dependency")
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is True


def test_change_to_pyproject_toml_is_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(base, {"pyproject.toml": "v2\n"}, "edit pyproject")
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is True


def test_change_to_dockerignore_is_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(base, {".dockerignore": "v2\n"}, "edit dockerignore")
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is True


def test_change_under_libs_contracts_only_is_not_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(
        base, {"libs/contracts/schema.json": "v2\n"}, "regenerate an OpenAPI file"
    )
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is False


def test_change_under_one_service_only_is_not_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    nxt = git_repo.commit_from(base, {"services/checkout/app.py": "v2\n"}, "edit checkout")
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is False


def test_empty_prev_is_full_rebuild(git_repo: GitRepo) -> None:
    """No usable last-good-ref — a first deploy, or a ref this clone no longer knows."""
    base = _baseline(git_repo)
    assert needs_full_rebuild(str(git_repo.path), "", base) is True


def test_head_to_head_is_not_full_rebuild(git_repo: GitRepo) -> None:
    base = _baseline(git_repo)
    assert needs_full_rebuild(str(git_repo.path), base, base) is False


def test_the_r2_4_diff_forces_a_full_rebuild(git_repo: GitRepo) -> None:
    """CRM R2.4 changes `PROFILE_DLQ_QUEUES` in platform-core's ops-monitor config: every image
    rebuilds and recreates on that deploy, which its deploy notes say."""
    path = "libs/platform-core/src/platform_core/ops_monitor/config.py"
    base = git_repo.initial_commit({**BASELINE_FILES, path: "v1\n"}, "baseline")
    nxt = git_repo.commit_from(base, {path: "v2\n"}, "watch deal.events")
    assert needs_full_rebuild(str(git_repo.path), base, nxt) is True


# ------------------------------------------------------------------ build_each


def build_each(tmp_path: Path, *args: str, fail_on: str = "") -> tuple[int, list[str], str]:
    """Runs the real `build_each` with a stand-in build command that logs each call and fails
    for the service named `fail_on`. "FAKE" in `args` stands for that command. Returns
    (exit code, calls, stderr)."""
    log = tmp_path / "calls.log"
    fake = tmp_path / "fake-compose"
    fake.write_text('#!/usr/bin/env bash\necho "$*" >> "$CALLS"\n[ "${!#}" != "$FAIL_ON" ]\n')
    fake.chmod(0o755)
    script = f'source "{REBUILD_LIB}"; build_each "$@"'
    argv = [str(fake) if a == "FAKE" else a for a in args]
    result = subprocess.run(
        ["bash", "-c", script, "_", *argv],
        capture_output=True,
        text=True,
        env={"CALLS": str(log), "FAIL_ON": fail_on, "PATH": "/usr/bin:/bin"},
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result.returncode, calls, result.stderr


def test_build_each_builds_one_service_per_call_in_order(tmp_path: Path) -> None:
    code, calls, _ = build_each(tmp_path, "FAKE", "build", "--pull", "--", "auth", "lead", "deal")
    assert code == 0
    assert calls == ["build --pull auth", "build --pull lead", "build --pull deal"]


def test_build_each_stops_at_the_first_failure_and_names_it(tmp_path: Path) -> None:
    code, calls, err = build_each(
        tmp_path, "FAKE", "build", "--", "auth", "lead", "deal", fail_on="lead"
    )
    assert code == 1
    assert calls == ["build auth", "build lead"]
    assert "lead failed to build" in err


def test_build_each_without_a_separator_is_a_usage_error(tmp_path: Path) -> None:
    code, calls, _ = build_each(tmp_path, "FAKE", "build", "auth")
    assert code == 2
    assert calls == []


def test_build_each_without_a_command_is_a_usage_error(tmp_path: Path) -> None:
    code, calls, _ = build_each(tmp_path, "--", "auth")
    assert code == 2
    assert calls == []


def test_build_each_with_no_services_builds_nothing(tmp_path: Path) -> None:
    code, calls, _ = build_each(tmp_path, "FAKE", "build", "--")
    assert code == 0
    assert calls == []
