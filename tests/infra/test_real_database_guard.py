"""`_helpers.real_database_dsn` — the guard in front of every opt-in real-database test here.

Those tests drive tools that DELETE organisations and GPS captures, so a DSN that names the shared
dev database or a host that is not this machine must fail collection, not run.
"""

from __future__ import annotations

import pytest
from _helpers import real_database_dsn

VAR = "OPSGRIDS_TEST_GUARD_PROBE_URL"


@pytest.fixture(autouse=True)
def _not_on_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    # The shared-database exemption keys on GITHUB_ACTIONS; these tests must hold on a runner too.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv(VAR, raising=False)


def test_an_unset_variable_means_the_test_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    assert real_database_dsn(VAR) is None
    monkeypatch.setenv(VAR, "")
    assert real_database_dsn(VAR) is None


def test_a_throwaway_database_on_this_machine_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "postgresql+asyncpg://opsgrids_admin:pw@127.0.0.1:5434/opsgrids_tooling"
    monkeypatch.setenv(VAR, dsn)
    assert real_database_dsn(VAR) == dsn


def test_the_shared_dev_database_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR, "postgresql+asyncpg://opsgrids_admin:pw@localhost:5434/opsgrids")
    with pytest.raises(RuntimeError, match="shared dev database"):
        real_database_dsn(VAR)


@pytest.mark.parametrize("host", ["db.example.com", "postgres", "20.219.18.84", "10.0.0.5"])
def test_a_database_on_another_host_is_refused(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv(VAR, f"postgresql+asyncpg://opsgrids_admin:pw@{host}:5432/opsgrids_tooling")
    with pytest.raises(RuntimeError, match="not on this machine"):
        real_database_dsn(VAR)
