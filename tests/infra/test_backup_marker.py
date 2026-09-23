"""Pins `write_backup_marker` (infrastructure/edge/lib/backup-marker.sh), the function
`backup.sh` calls once, at the very end of a fully successful run, and the only writer of the
file `platform_core.ops_monitor.checks.check_backup_freshness` reads.

Sources the real function rather than a reimplementation of it — the same shape
`test_remote_health.py` uses for `unhealthy_from_ps`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_MARKER_LIB = REPO_ROOT / "infrastructure/edge/lib/backup-marker.sh"


def _write_backup_marker(state_dir: Path) -> subprocess.CompletedProcess[str]:
    script = f'source "{BACKUP_MARKER_LIB}"; write_backup_marker "$1"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(state_dir)],
        capture_output=True,
        text=True,
    )


def test_writes_a_marker_file_parseable_by_the_python_side(tmp_path: Path) -> None:
    result = _write_backup_marker(tmp_path)
    assert result.returncode == 0, result.stderr

    marker = tmp_path / "backup.last-ok"
    assert marker.exists()
    stamp = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
    # The same parsing `checks.check_backup_freshness` does — round-tripping here is what
    # proves the shell side and the Python side agree on a format, not just that each is
    # independently plausible.
    assert stamp.tzinfo is not None

    now = datetime.now(UTC)
    age_seconds = (now - stamp.astimezone(UTC)).total_seconds()
    assert 0 <= age_seconds < 10, "the marker must be a fresh 'now', not a fixed placeholder"


def test_creates_the_state_directory_if_it_does_not_exist(tmp_path: Path) -> None:
    state_dir = tmp_path / "does" / "not" / "exist" / "yet"
    result = _write_backup_marker(state_dir)
    assert result.returncode == 0, result.stderr
    assert (state_dir / "backup.last-ok").exists()


def test_overwrites_a_stale_marker_rather_than_appending(tmp_path: Path) -> None:
    marker = tmp_path / "backup.last-ok"
    marker.write_text("2020-01-01T00:00:00Z\n", encoding="utf-8")

    _write_backup_marker(tmp_path)

    lines = marker.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0] != "2020-01-01T00:00:00Z"
