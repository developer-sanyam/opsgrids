"""Pins `memory_budget_verdict` (infrastructure/edge/lib/memory-budget.sh) — the threshold
arithmetic `preflight.sh` uses to decide whether the box's enabled `COMPOSE_PROFILES` fit its
own MemTotal (ADR-0013's memory-budget erratum, 2026-09-23). Runs the real function as a
subprocess, the same pattern test_remote_health.py uses for `unhealthy_from_ps` — never a
reimplementation of the arithmetic, which would only prove the copy agrees with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_BUDGET_LIB = REPO_ROOT / "infrastructure/edge/lib/memory-budget.sh"


def memory_budget_verdict(
    mem_total_kb: int, expected_mb: int, soft_percent: int = 80, hard_percent: int = 100
) -> str:
    script = f'source "{MEMORY_BUDGET_LIB}"; memory_budget_verdict "$1" "$2" "$3" "$4"'
    args = [
        "bash",
        "-c",
        script,
        "_",
        str(mem_total_kb),
        str(expected_mb),
        str(soft_percent),
        str(hard_percent),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_well_under_soft_is_ok() -> None:
    # ~7.6 GB box, 3378 MB expected (the reference box's full-profile total, computed by
    # test_compose_memory_budget.py) — comfortably under the 80% line.
    assert memory_budget_verdict(8_000_000, 3378) == "ok 43"


def test_the_reference_box_at_full_profile_list_warns() -> None:
    # 1000 MB expected of a 1000 MB box's worth of headroom below hard, above soft.
    assert memory_budget_verdict(1_024_000, 900) == "warn 90"


def test_a_smaller_box_refuses() -> None:
    assert memory_budget_verdict(3_000_000, 3378) == "refuse 115"


def test_exactly_at_the_soft_threshold_warns_not_ok() -> None:
    # 1000 MB box, 800 MB expected: exactly 80%. The line itself is "no longer compliant".
    assert memory_budget_verdict(1_024_000, 800, 80, 100) == "warn 80"


def test_just_under_the_soft_threshold_is_ok() -> None:
    assert memory_budget_verdict(1_024_000, 799, 80, 100) == "ok 79"


def test_exactly_at_the_hard_threshold_refuses() -> None:
    assert memory_budget_verdict(1_024_000, 1000, 80, 100) == "refuse 100"


def test_zero_mem_total_refuses_rather_than_dividing_by_zero() -> None:
    assert memory_budget_verdict(0, 100) == "refuse 0"


def test_missing_mem_total_refuses() -> None:
    assert memory_budget_verdict("", 100) == "refuse 0"  # type: ignore[arg-type]


def test_custom_thresholds_are_respected() -> None:
    # A stricter box: 50% soft, 70% hard.
    assert memory_budget_verdict(1_024_000, 400, 50, 70) == "ok 40"
    assert memory_budget_verdict(1_024_000, 600, 50, 70) == "warn 60"
    assert memory_budget_verdict(1_024_000, 800, 50, 70) == "refuse 80"


# -------------------------------------------------------- apply_accepted_ceiling (B2/H3/H4)


def apply_accepted_ceiling(
    verdict: str, percent: int, expected_mb: int, accepted_mb: str = "", cap_mb: str = ""
) -> str:
    script = (
        f'source "{MEMORY_BUDGET_LIB}"; apply_accepted_ceiling "$1" "$2" "$3" "${{4:-}}" "${{5:-}}"'
    )
    args = [
        "bash",
        "-c",
        script,
        "_",
        verdict,
        str(percent),
        str(expected_mb),
        str(accepted_mb),
        str(cap_mb),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_ok_verdict_passes_through_unchanged() -> None:
    assert apply_accepted_ceiling("ok", 43, 1000, "2000") == "ok 43"


def test_warn_verdict_passes_through_unchanged() -> None:
    assert apply_accepted_ceiling("warn", 90, 1000, "2000") == "warn 90"


def test_no_accepted_ceiling_set_keeps_refusing() -> None:
    """No EDGE_BUDGET_ACCEPTED_MB in .env: no implicit acceptance. Silence means the deploy
    stops — the owner has to actually set the ceiling, not have one assumed for them."""
    assert apply_accepted_ceiling("refuse", 109, 4149) == "refuse 109"
    assert apply_accepted_ceiling("refuse", 109, 4149, "") == "refuse 109"


def test_a_deploy_at_or_under_the_accepted_ceiling_is_downgraded_to_warn() -> None:
    assert apply_accepted_ceiling("refuse", 109, 4149, "4200") == "warn 109"


def test_exactly_at_the_accepted_ceiling_is_downgraded() -> None:
    assert apply_accepted_ceiling("refuse", 109, 4149, "4149") == "warn 109"


def test_a_deploy_over_the_accepted_ceiling_still_refuses() -> None:
    """The owner accepted 4149M; this deploy wants 4150M — one more than what was actually
    agreed to, so the hard line still catches it even with a ceiling configured."""
    assert apply_accepted_ceiling("refuse", 109, 4150, "4149") == "refuse 109"


def test_the_ceiling_is_static_not_derived_from_expected_mb() -> None:
    """Unlike the removed last-ok ratchet, this ceiling never moves on its own — a smaller
    deploy than before is judged against the same owner-set number, not a new, lower bar."""
    assert apply_accepted_ceiling("refuse", 90, 3500, "4149") == "warn 90"


# ------------------------------------------ the ceiling's upper bound (round 3, M5)


def test_a_total_over_the_cap_refuses_even_with_a_ceiling_that_covers_it() -> None:
    """The cap is absolute: past a quarter more than the box has, no `.env` line can sign for it."""
    assert apply_accepted_ceiling("refuse", 130, 5000, "6000", "4827") == "refuse 130"


def test_a_total_at_the_cap_is_still_covered_by_a_ceiling() -> None:
    assert apply_accepted_ceiling("refuse", 125, 4827, "4827", "4827") == "warn 125"


def test_no_cap_given_behaves_as_before() -> None:
    assert apply_accepted_ceiling("refuse", 130, 5000, "6000") == "warn 130"


def test_a_non_numeric_cap_is_not_a_reason_to_error_out_of_the_gate() -> None:
    assert apply_accepted_ceiling("refuse", 109, 4149, "4200", "lots") == "refuse 109"


def describe_budget_usage(expected_mb: int, mem_total_mb: int) -> str:
    script = f'source "{MEMORY_BUDGET_LIB}"; describe_budget_usage "$1" "$2"'
    result = subprocess.run(
        ["bash", "-c", script, "_", str(expected_mb), str(mem_total_mb)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_usage_under_memtotal_is_percent_to_one_decimal() -> None:
    assert describe_budget_usage(3400, 3862) == "3400M of 3862M MemTotal (88.0%)"


def test_usage_over_memtotal_says_how_many_megabytes_over() -> None:
    """3894 MB on a 3862 MB box floors to "100%" — reads as fine. It is 32 MB over."""
    assert describe_budget_usage(3894, 3862) == (
        "3894M against 3862M MemTotal: 32M over MemTotal (100.8%)"
    )


def test_usage_exactly_at_memtotal_is_not_called_an_overcommit() -> None:
    assert describe_budget_usage(3862, 3862) == "3862M of 3862M MemTotal (100.0%)"
