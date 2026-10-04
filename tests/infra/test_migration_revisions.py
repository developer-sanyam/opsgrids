"""Pins `unknown_revisions` (infrastructure/edge/lib/migration-revisions.sh) — what lets
`remote.sh migrate` skip, loudly, a service whose database is ahead of the image being rolled back
to, instead of aborting the rollback on alembic's "Can't locate revision".

The most dangerous failure here is a *false* "unknown": a real migration file the matcher does not
recognise would make `migrate` silently skip a service that needs its migrations. So one test walks
every migration file in the repo and requires the matcher to know its revision.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "infrastructure/edge/lib/migration-revisions.sh"
REMOTE = REPO_ROOT / "infrastructure/edge/remote.sh"
REVISION_LINE = re.compile(r"""^revision(?:\s*:\s*str)?\s*=\s*["']([^"']+)["']""", re.MULTILINE)


def _run(function: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; {function} "$@"', "bash", *args],
        capture_output=True,
        text=True,
    )


def unknown(versions_dir: Path, *revs: str) -> list[str]:
    result = _run("unknown_revisions", str(versions_dir), *revs)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


@pytest.fixture
def versions(tmp_path: Path) -> Path:
    d = tmp_path / "versions"
    d.mkdir()
    (d / "aaa111_first.py").write_text(
        'revision: str = "aaa111"  # pragma: allowlist secret\ndown_revision = None\n'
    )
    (d / "bbb222_second.py").write_text("revision = 'bbb222'\ndown_revision = 'aaa111'\n")
    return d


def test_known_revisions_print_nothing(versions: Path) -> None:
    assert unknown(versions, "aaa111", "bbb222") == []


def test_a_revision_the_image_does_not_have_is_reported(versions: Path) -> None:
    assert unknown(versions, "aaa111", "b7d41e92c05a") == ["b7d41e92c05a"]


def test_down_revision_is_not_mistaken_for_a_revision(versions: Path) -> None:
    (versions / "ccc333_third.py").write_text('revision = "ccc333"\ndown_revision = "zzz999"\n')
    assert unknown(versions, "zzz999") == ["zzz999"]


def test_a_prefix_of_a_revision_is_not_a_match(versions: Path) -> None:
    assert unknown(versions, "aaa11") == ["aaa11"]


def test_blank_and_no_revisions_are_ignored(versions: Path) -> None:
    assert unknown(versions, "") == []
    assert unknown(versions) == []


def test_an_empty_versions_directory_knows_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert unknown(empty, "aaa111") == ["aaa111"]


def test_a_malformed_revision_is_reported_rather_than_used_in_a_pattern(versions: Path) -> None:
    assert unknown(versions, "a.*") == ["a.*"]


def test_every_real_migration_in_the_repo_is_recognised() -> None:
    checked = 0
    for versions_dir in sorted(REPO_ROOT.glob("services/*/migrations/versions")):
        for path in sorted(versions_dir.glob("*.py")):
            match = REVISION_LINE.search(path.read_text())
            assert match, f"{path} has no recognisable `revision = ...` line"
            assert unknown(versions_dir, match.group(1)) == [], path
            checked += 1
    assert checked > 50


def test_the_warning_says_skipped_and_never_downgrade() -> None:
    result = _run("migration_rollback_warning", "operations", "b7d41e92c05a")
    assert result.returncode == 0
    assert "operations: SKIPPED" in result.stdout
    assert "b7d41e92c05a" in result.stdout
    assert "NOT downgraded" in result.stdout


def test_remote_sh_migrate_uses_the_skip_and_still_parses() -> None:
    syntax = subprocess.run(["bash", "-n", str(REMOTE)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    text = REMOTE.read_text()
    start = text.index("migrate() {")
    body = text[start : text.index("\n}\n", start)]
    assert "unknown_revisions" in body and "migration_rollback_warning" in body
    # The skip comes before the upgrade it protects.
    assert body.index("unknown_revisions") < body.index("alembic upgrade head")
