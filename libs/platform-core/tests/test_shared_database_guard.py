"""`is_shared_dev_database` — what the destructive tests' "never the shared database" guards ask.

The database is *named* `opsgrids` in two places: the developer stack every `make infra` shares,
and the postgres service container each CI job starts for itself. Only the first is something to
protect, and the guards used to compare the name alone, so every one of them failed on every CI
run.
"""

from __future__ import annotations

import pytest

from platform_core.testing import is_shared_dev_database

SHARED = "postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids"
ISOLATED = "postgresql+asyncpg://opsgrids:opsgrids@localhost:5434/opsgrids_wt"


def test_the_developer_stack_database_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert is_shared_dev_database(SHARED)


def test_an_isolated_database_is_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert not is_shared_dev_database(ISOLATED)
    # A name that merely starts with it is a different database.
    assert not is_shared_dev_database(SHARED + "_mon")


def test_the_ci_job_container_is_not_shared_on_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert not is_shared_dev_database(SHARED)


@pytest.mark.parametrize("value", ["", "false", "1", "yes"])
def test_only_the_github_runner_marker_lifts_the_guard(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """`ENVIRONMENT=ci` is something a developer sets by hand to reproduce CI, so it does not
    count; only the exact marker GitHub's runners set does."""
    monkeypatch.setenv("GITHUB_ACTIONS", value)
    monkeypatch.setenv("ENVIRONMENT", "ci")
    assert is_shared_dev_database(SHARED)
