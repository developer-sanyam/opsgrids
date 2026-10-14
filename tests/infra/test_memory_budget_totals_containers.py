"""`expected_total_for_names` and the `--containers` CLI mode (memory_budget_totals.py) — H2's
fix, the memory-headroom slice's 2026-09-23 fix round: pricing by the box's actual *running*
containers (`docker ps`, ground truth) rather than by its *configured* `COMPOSE_PROFILES`
(compose does not stop a container whose profile was just disabled — an orphan keeps costing
memory preflight would otherwise stop counting).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _helpers import edge_budget

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"
LIB_DIR = EDGE_DIR / "lib"
TOTALS_SCRIPT = LIB_DIR / "memory_budget_totals.py"

sys.path.insert(0, str(LIB_DIR))
from memory_budget_totals import (  # noqa: E402
    expected_total_for_names,
    post_deploy_container_names,
    unpriced_container_names,
)


def test_prices_an_explicit_container_list_regardless_of_profile() -> None:
    budget = edge_budget()
    # auth and billing are core (profile: null) and plain `kind: api` (unlike account, which is
    # `api_with_worker` since the worker-folding follow-up folded its own worker in); operations
    # is behind `inventory` and still plain `api` too (it folds only a sweep, on
    # RUN_SWEEPS_IN_API, not a worker). Naming all three here must price all three, with no
    # COMPOSE_PROFILES involved at all.
    total = expected_total_for_names(budget, {"auth", "billing", "operations"})
    api_mb = budget["kinds"]["api"]["expected_rss_mb"]
    assert total == api_mb * 3


def test_a_container_the_budget_does_not_price_is_priced_as_background_not_skipped() -> None:
    """M7 (review round 2): the caller has already filtered `docker ps` to this compose
    project's own label, so an unpriced name here is not a stray foreign container — it is one
    of ours `memory-budget.json` has fallen behind on. Priced conservatively rather than as
    zero, so a real gap does not silently understate the box."""
    budget = edge_budget()
    total = expected_total_for_names(budget, {"auth", "some-unpriced-container"})
    assert (
        total
        == budget["kinds"]["api"]["expected_rss_mb"]
        + budget["kinds"]["background"]["expected_rss_mb"]
    )


def test_unpriced_container_names_names_the_gap() -> None:
    budget = edge_budget()
    assert unpriced_container_names(budget, {"auth", "some-unpriced-container"}) == {
        "some-unpriced-container"
    }


def test_unpriced_container_names_is_empty_when_everything_is_priced() -> None:
    budget = edge_budget()
    assert unpriced_container_names(budget, {"auth", "account"}) == set()


def test_empty_container_set_prices_zero() -> None:
    budget = edge_budget()
    assert expected_total_for_names(budget, set()) == 0


def test_orphaned_container_from_a_disabled_profile_still_counts() -> None:
    """The exact scenario H2 exists for: a profile taken out of COMPOSE_PROFILES whose
    container compose never actually stopped. Pricing by the running-container list (this
    function) must still count it; pricing by the (now-reduced) profile list would not."""
    budget = edge_budget()
    running_now = {"auth", "account", "employee"}  # hrms never actually disabled by anything
    still_running_after_profile_removed = running_now  # docker did not stop them
    assert expected_total_for_names(
        budget, still_running_after_profile_removed
    ) == expected_total_for_names(budget, running_now)


class TestPostDeployContainerNames:
    """B1 (review round 2): the post-deploy price is the union of (running intersected with
    all_services) and declared_services — not just what is running today, and not just what is
    declared."""

    def test_a_newly_declared_profile_is_priced_even_though_nothing_is_running_yet(self) -> None:
        names = post_deploy_container_names(
            running={"auth", "account"},
            all_services={"auth", "account", "operations"},
            declared_services={"auth", "account", "operations"},
        )
        assert names == {"auth", "account", "operations"}

    def test_a_running_orphan_still_defined_in_the_new_compose_file_survives(self) -> None:
        """A container from a profile that was disabled but never actually stopped (H2) — the
        new compose file still knows the service, so it still counts."""
        names = post_deploy_container_names(
            running={"auth", "account", "employee"},
            all_services={"auth", "account", "employee", "operations"},
            declared_services={"auth", "account"},  # hrms no longer declared
        )
        assert "employee" in names

    def test_a_running_container_whose_service_was_deleted_entirely_does_not_survive(
        self,
    ) -> None:
        """Unlike a profile toggle, a real service deletion IS torn down by `--remove-orphans`
        — pricing it after the deploy would overstate what is left."""
        names = post_deploy_container_names(
            running={"auth", "account", "some-removed-service"},
            all_services={"auth", "account"},  # the new compose file no longer defines it
            declared_services={"auth", "account"},
        )
        assert "some-removed-service" not in names

    def test_prices_correctly_through_expected_total_for_names(self) -> None:
        # `billing`, not `account`: plain `kind: api`, unlike `account`, which is
        # `api_with_worker` since the worker-folding follow-up folded its own worker in.
        budget = edge_budget()
        names = post_deploy_container_names(
            running={"auth", "billing"},
            all_services={"auth", "billing", "operations"},
            declared_services={"auth", "billing", "operations"},
        )
        assert (
            expected_total_for_names(budget, names) == budget["kinds"]["api"]["expected_rss_mb"] * 3
        )


def test_cli_post_deploy_mode_matches_the_python_function() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(TOTALS_SCRIPT),
            str(BUDGET_FILE),
            "--post-deploy",
            "--running",
            "auth,account",
            "--all-services",
            "auth,account,operations",
            "--declared-services",
            "auth,account,operations",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    budget = edge_budget()
    expected = expected_total_for_names(budget, {"auth", "account", "operations"})
    assert int(result.stdout.strip()) == expected


def test_cli_post_deploy_requires_all_three_lists() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(TOTALS_SCRIPT),
            str(BUDGET_FILE),
            "--post-deploy",
            "--running",
            "auth",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_cli_containers_mode_matches_the_python_function() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(TOTALS_SCRIPT),
            str(BUDGET_FILE),
            "--containers",
            "auth,account,operations",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    budget = edge_budget()
    expected = expected_total_for_names(budget, {"auth", "account", "operations"})
    assert int(result.stdout.strip()) == expected


def test_cli_refuses_both_profiles_and_containers_at_once() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(TOTALS_SCRIPT),
            str(BUDGET_FILE),
            "--profiles",
            "inventory",
            "--containers",
            "auth",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_cli_profiles_mode_still_works_with_the_new_flag_form() -> None:
    result = subprocess.run(
        [sys.executable, str(TOTALS_SCRIPT), str(BUDGET_FILE), "--profiles", "inventory"],
        capture_output=True,
        text=True,
        check=True,
    )
    budget = edge_budget()
    from memory_budget_totals import expected_total_mb

    assert int(result.stdout.strip()) == expected_total_mb(budget, {"inventory"})
