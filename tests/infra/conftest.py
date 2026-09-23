"""Shared fixtures for testing the edge deploy tooling's path-diff decisions.

Everything here builds a throwaway git repository in a temp dir and drives the real scripts
under `infrastructure/edge/` against it — never a reimplementation of their logic. The point
of these tests is to pin the pathspec `remote.sh` and `ci-plan.sh` use to decide what a deploy
touches; asserting against a copy of the rule would only prove the copy agrees with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REBUILD_LIB = REPO_ROOT / "infrastructure/edge/lib/rebuild-decision.sh"
CI_PLAN = REPO_ROOT / "infrastructure/edge/ci-plan.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


class GitRepo:
    """A throwaway git repository plus the two helpers every test needs.

    `commit_from(base_sha, files, message)` checks out `base_sha` (detached), writes `files`
    on top of it, and commits — so every scenario's diff is exactly the files it names,
    against the same baseline, regardless of what other scenarios have already committed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def initial_commit(self, files: dict[str, str], message: str) -> str:
        """The very first commit on the repo's still-unborn branch. No base to check out."""
        _write(self.path, files)
        return _commit(self.path, message)

    def commit_from(self, base_sha: str, files: dict[str, str], message: str) -> str:
        _git(self.path, "checkout", "-q", base_sha)
        _write(self.path, files)
        return _commit(self.path, message)


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-q", "-b", "main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test")
    # Never sign or gpg-prompt in a scratch repo nobody will ever push.
    _git(repo_dir, "config", "commit.gpgsign", "false")
    return GitRepo(repo_dir)
