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
