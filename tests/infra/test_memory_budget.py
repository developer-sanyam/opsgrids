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


# -------------------------------------------------------- grandfathered_verdict (B1a)


def grandfathered_verdict(
    verdict: str, percent: int, expected_mb: int, last_ok_mb: str = ""
) -> str:
    script = f'source "{MEMORY_BUDGET_LIB}"; grandfathered_verdict "$1" "$2" "$3" "${{4:-}}"'
    args = ["bash", "-c", script, "_", verdict, str(percent), str(expected_mb), str(last_ok_mb)]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_ok_verdict_passes_through_unchanged() -> None:
    assert grandfathered_verdict("ok", 43, 1000, "2000") == "ok 43"


def test_warn_verdict_passes_through_unchanged() -> None:
    assert grandfathered_verdict("warn", 90, 1000, "2000") == "warn 90"


def test_a_fresh_box_with_no_last_ok_keeps_refusing() -> None:
    """No prior successful deploy recorded: today's behaviour, unchanged. A box's very first
    over-budget deploy must not be laundered into a warning just because nothing came before it."""
    assert grandfathered_verdict("refuse", 109, 4149) == "refuse 109"
    assert grandfathered_verdict("refuse", 109, 4149, "") == "refuse 109"


def test_a_deploy_no_worse_than_last_ok_is_downgraded_to_warn() -> None:
    # Exactly the same total as last time: 0% worse, well within the 1% ceiling.
    assert grandfathered_verdict("refuse", 109, 4149, "4149") == "warn 109"


def test_a_deploy_within_one_percent_worse_is_still_downgraded() -> None:
    # 4149 is within 1% of 4110 (ceiling: 4110 + 41 = 4151).
    assert grandfathered_verdict("refuse", 109, 4149, "4110") == "warn 109"


def test_exactly_at_the_one_percent_ceiling_is_downgraded() -> None:
    # last_ok=1000 -> ceiling 1010. expected_mb=1010 is "no worse", inclusive.
    assert grandfathered_verdict("refuse", 101, 1010, "1000") == "warn 101"


def test_a_deploy_that_makes_it_worse_still_refuses() -> None:
    # last_ok=1000 -> ceiling 1010. expected_mb=1011 is over it.
    assert grandfathered_verdict("refuse", 101, 1011, "1000") == "refuse 101"


def test_a_smaller_deploy_than_last_ok_is_downgraded() -> None:
    """This deploy actually improved things (still over the hard line, but less than before) —
    definitely not "worse than last time"."""
    assert grandfathered_verdict("refuse", 105, 4000, "4149") == "warn 105"
