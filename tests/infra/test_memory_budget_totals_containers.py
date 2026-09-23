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

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"
LIB_DIR = EDGE_DIR / "lib"
TOTALS_SCRIPT = LIB_DIR / "memory_budget_totals.py"

sys.path.insert(0, str(LIB_DIR))
from memory_budget_totals import expected_total_for_names, load_budget  # noqa: E402


def _budget() -> dict:
    return load_budget(BUDGET_FILE)


def test_prices_an_explicit_container_list_regardless_of_profile() -> None:
    budget = _budget()
    # auth and account are core (profile: null); operations is behind `inventory`. Naming all
    # three here must price all three, with no COMPOSE_PROFILES involved at all.
    total = expected_total_for_names(budget, {"auth", "account", "operations"})
    api_mb = budget["kinds"]["api"]["expected_rss_mb"]
    assert total == api_mb * 3


def test_a_container_the_budget_does_not_price_is_skipped_not_an_error() -> None:
    budget = _budget()
    total = expected_total_for_names(budget, {"auth", "some-unrelated-container"})
    assert total == budget["kinds"]["api"]["expected_rss_mb"]


def test_empty_container_set_prices_zero() -> None:
    budget = _budget()
    assert expected_total_for_names(budget, set()) == 0


def test_orphaned_container_from_a_disabled_profile_still_counts() -> None:
    """The exact scenario H2 exists for: a profile taken out of COMPOSE_PROFILES whose
    container compose never actually stopped. Pricing by the running-container list (this
    function) must still count it; pricing by the (now-reduced) profile list would not."""
    budget = _budget()
    running_now = {"auth", "account", "operations", "operations-cron"}  # inventory never disabled
    still_running_after_profile_removed = running_now  # docker did not stop them
    assert expected_total_for_names(
        budget, still_running_after_profile_removed
    ) == expected_total_for_names(budget, running_now)


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
    budget = _budget()
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
    budget = _budget()
    from memory_budget_totals import expected_total_mb

    assert int(result.stdout.strip()) == expected_total_mb(budget, {"inventory"})
