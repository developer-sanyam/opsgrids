"""Pins `profile_for` and `activated_profiles` (infrastructure/edge/lib/active-profiles.sh) —
the zero-activation profile auto-off ADR-0013's memory-budget erratum (2026-09-23) named as the
one lever that costs nothing to ship. Runs the real functions as a subprocess, the same pattern
test_memory_budget.py uses for `memory_budget_verdict` — never a reimplementation, which would
only prove the copy agrees with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PROFILES_LIB = REPO_ROOT / "infrastructure/edge/lib/active-profiles.sh"


def profile_for(product_key: str) -> str:
    script = f'source "{ACTIVE_PROFILES_LIB}"; profile_for "$1"'
    result = subprocess.run(
        ["bash", "-c", script, "_", product_key], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def activated_profiles(*product_keys: str) -> list[str]:
    script = f'source "{ACTIVE_PROFILES_LIB}"; activated_profiles "$@"'
    result = subprocess.run(
        ["bash", "-c", script, "_", *product_keys], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


class TestProfileFor:
    def test_operations_maps_to_inventory(self) -> None:
        assert profile_for("operations") == "inventory"

    def test_a_profile_named_after_its_own_key(self) -> None:
        for key in (
            "hrms",
            "crm",
            "pulseboard",
            "projects",
            "support",
            "marketing",
            "finance",
            "itsm",
        ):
            assert profile_for(key) == key

    def test_storefront_maps_to_itself(self) -> None:
        assert profile_for("storefront") == "storefront"

    def test_a_platform_product_has_no_profile(self) -> None:
        # notification is is_platform and every organization is entitled to it for free — it
        # is not behind a compose profile at all (ADR-0013 §5's core row).
        assert profile_for("notification") == ""

    def test_an_uncatalogued_key_has_no_profile(self) -> None:
        assert profile_for("not-a-real-product") == ""


class TestActivatedProfiles:
    def test_empty_input_is_empty_output(self) -> None:
        assert activated_profiles() == []

    def test_one_activation_yields_its_one_profile(self) -> None:
        assert activated_profiles("operations") == ["inventory"]

    def test_deduplicates_repeated_activations_of_the_same_profile(self) -> None:
        # 500 organizations activating operations must yield one profile, not five hundred
        # lines remote.sh would then have to deduplicate itself.
        keys = ["operations"] * 5
        assert activated_profiles(*keys) == ["inventory"]

    def test_skips_keys_with_no_compose_profile(self) -> None:
        assert activated_profiles("notification", "operations") == ["inventory"]

    def test_preserves_first_appearance_order(self) -> None:
        assert activated_profiles("crm", "operations", "hrms") == ["crm", "inventory", "hrms"]

    def test_multiple_distinct_activated_products(self) -> None:
        assert sorted(activated_profiles("operations", "crm", "hrms", "crm")) == [
            "crm",
            "hrms",
            "inventory",
        ]
