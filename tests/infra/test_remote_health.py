"""Pins `unhealthy_from_ps` (infrastructure/edge/lib/health-check.sh), the rule `remote.sh`'s
`health` step uses to decide whether a deploy may proceed.

Before this existed, `health` treated any non-"running" container as unhealthy, which meant a
one-shot bootstrap container (`minio-init`) that does its job and exits 0 made *every* deploy
with the `storefront` profile wait the full `HEALTH_TIMEOUT` and then fail — `record_ref` and the
apps job both skipped, on a deploy where nothing was actually wrong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEALTH_LIB = REPO_ROOT / "infrastructure/edge/lib/health-check.sh"


def unhealthy_from_ps(ps_lines: list[str]) -> list[str]:
    """Shells out to the real function rather than a reimplementation of it."""
    script = f'source "{HEALTH_LIB}"; unhealthy_from_ps'
    result = subprocess.run(
        ["bash", "-c", script],
        input="\n".join(ps_lines) + "\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _line(service: str, state: str, health: str, exit_code: str, labels: str) -> str:
    return f"{service}|{state}|{health}|{exit_code}|{labels}"


def test_running_and_healthy_is_ok() -> None:
    line = _line("app-gateway", "running", "healthy", "0", "com.docker.compose.service=app-gateway")
    assert unhealthy_from_ps([line]) == []


def test_running_with_no_healthcheck_is_ok() -> None:
    # compose prints "<nil>" for a container with no HEALTHCHECK, e.g. the background workers.
    line = _line("edge-relay", "running", "<nil>", "0", "com.docker.compose.service=edge-relay")
    assert unhealthy_from_ps([line]) == []


def test_exited_zero_labelled_oneshot_is_ok() -> None:
    """minio-init: does its job, exits 0, and is labelled opsgrids.oneshot=true."""
    line = _line(
        "minio-init",
        "exited",
        "",
        "0",
        "com.docker.compose.service=minio-init,opsgrids.oneshot=true",
    )
    assert unhealthy_from_ps([line]) == []


def test_exited_zero_without_the_label_fails() -> None:
    """A service that is supposed to keep running but exited 0 is still a failure — the label,
    not the exit code alone, is what grants the pass."""
    line = _line("document", "exited", "", "0", "com.docker.compose.service=document")
    assert unhealthy_from_ps([line]) == ["document(exited/)"]


def test_exited_nonzero_labelled_oneshot_still_fails() -> None:
    """The label only forgives a clean exit. minio-init failing its bucket bootstrap must not
    be waved through."""
    line = _line(
        "minio-init",
        "exited",
        "",
        "1",
        "com.docker.compose.service=minio-init,opsgrids.oneshot=true",
    )
    assert unhealthy_from_ps([line]) == ["minio-init(exited/)"]


def test_running_but_unhealthy_fails() -> None:
    line = _line("postgres", "running", "starting", "0", "com.docker.compose.service=postgres")
    assert unhealthy_from_ps([line]) == ["postgres(running/starting)"]


def test_label_matches_regardless_of_position_in_the_label_list() -> None:
    line = _line(
        "minio-init",
        "exited",
        "",
        "0",
        "a=1,opsgrids.oneshot=true,com.docker.compose.service=minio-init",
    )
    assert unhealthy_from_ps([line]) == []


def test_mixed_batch_reports_only_the_unhealthy_ones() -> None:
    lines = [
        _line("app-gateway", "running", "healthy", "0", "svc=app-gateway"),
        _line("minio-init", "exited", "", "0", "opsgrids.oneshot=true"),
        _line("postgres", "running", "starting", "0", "svc=postgres"),
    ]
    assert unhealthy_from_ps(lines) == ["postgres(running/starting)"]
