"""Pins `profile_for`, `key_for_profile` and `activated_profiles`
(infrastructure/edge/lib/active-profiles.sh) — the catalogue product key <-> compose profile
mapping `preflight.sh`'s advisory checks are built on (memory-headroom slice, 2026-09-23 fix
round; H2/L9/L10 review findings). Runs the real functions as a subprocess, the same pattern
test_memory_budget.py uses for `memory_budget_verdict` — never a reimplementation, which would
only prove the copy agrees with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _helpers import memory_budget_json, sellable_profiles

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
ACTIVE_PROFILES_LIB = EDGE_DIR / "lib/active-profiles.sh"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"


def profile_for(product_key: str) -> str:
    script = f'source "{ACTIVE_PROFILES_LIB}"; profile_for "$1"'
    result = subprocess.run(
        ["bash", "-c", script, "_", product_key], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def key_for_profile(profile: str) -> str:
    script = f'source "{ACTIVE_PROFILES_LIB}"; key_for_profile "$1"'
    result = subprocess.run(["bash", "-c", script, "_", profile], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def resolve_declared_profiles(edge_profiles: str, compose_profiles: str) -> str:
    script = f'source "{ACTIVE_PROFILES_LIB}"; resolve_declared_profiles "$1" "$2"'
    result = subprocess.run(
        ["bash", "-c", script, "_", edge_profiles, compose_profiles],
        capture_output=True,
        text=True,
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

    def test_storefront_maps_to_itself(self) -> None:
        assert profile_for("storefront") == "storefront"

    def test_a_platform_product_has_no_profile(self) -> None:
        # notification is is_platform and every organization is entitled to it for free — it
        # is not behind a compose profile at all (ADR-0013 §5's core row).
        assert profile_for("notification") == ""

    def test_an_uncatalogued_key_has_no_profile(self) -> None:
        assert profile_for("not-a-real-product") == ""


class TestKeyForProfile:
    def test_inventory_maps_to_operations(self) -> None:
        assert key_for_profile("inventory") == "operations"

    def test_storefront_maps_to_itself(self) -> None:
        assert key_for_profile("storefront") == "storefront"

    def test_an_unknown_profile_has_no_key(self) -> None:
        assert key_for_profile("not-a-real-profile") == ""


class TestProfileForAndKeyForProfileCoverExactlyTheSameProfiles:
    """L9: a loop asserting `profile_for(key) == key` for a hardcoded list of names one already
    wrote by hand proves nothing but that the test's own list agrees with itself. This instead
    walks `memory-budget.json`'s own `profiles` list — the actual, current set of compose
    profiles this platform can enable — and proves the two mappings round-trip for every one of
    them, so the pair can never quietly drift apart the way two independently hand-maintained
    `case` statements eventually would."""

    def test_every_sellable_profile_round_trips_through_key_for_profile_and_back(self) -> None:
        for profile in sellable_profiles(memory_budget_json()):
            key = key_for_profile(profile)
            assert key, f"key_for_profile has no entry for {profile!r}"
            assert profile_for(key) == profile, (
                f"profile_for(key_for_profile({profile!r})) == {profile_for(key)!r}, expected "
                f"{profile!r} back — the two mappings have drifted apart"
            )

    def test_no_sellable_profile_is_missing_from_the_reverse_mapping(self) -> None:
        covered = {key_for_profile(p) for p in sellable_profiles(memory_budget_json())}
        assert "" not in covered, "at least one profile in memory-budget.json has no catalogue key"


class TestResolveDeclaredProfiles:
    """The one precedence rule preflight.sh and remote.sh both defer to (H2, the memory-headroom
    slice's 2026-09-23 fix round) — never a computed value, only ever a choice between the two
    explicit inputs an operator can set."""

    def test_edge_profiles_wins_when_both_are_set(self) -> None:
        assert resolve_declared_profiles("hrms", "inventory,storefront") == "hrms"

    def test_compose_profiles_is_used_when_edge_profiles_is_empty(self) -> None:
        assert resolve_declared_profiles("", "inventory,storefront") == "inventory,storefront"

    def test_both_empty_is_empty_not_an_error(self) -> None:
        assert resolve_declared_profiles("", "") == ""

    def test_edge_profiles_alone_is_used(self) -> None:
        assert resolve_declared_profiles("crm", "") == "crm"


class TestActivatedProfiles:
    def test_empty_input_is_empty_output(self) -> None:
        assert activated_profiles() == []

    def test_one_activation_yields_its_one_profile(self) -> None:
        assert activated_profiles("operations") == ["inventory"]

    def test_deduplicates_repeated_activations_of_the_same_profile(self) -> None:
        # 500 organizations activating operations must yield one profile, not five hundred
        # lines a caller would then have to deduplicate itself.
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


def expand_profile_wildcard(declared: str, every: str) -> str:
    script = f'source "{ACTIVE_PROFILES_LIB}"; expand_profile_wildcard "$1" "$2"'
    result = subprocess.run(
        ["bash", "-c", script, "_", declared, every], capture_output=True, text=True, cwd="/"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class TestExpandProfileWildcard:
    """`COMPOSE_PROFILES=*` is valid for docker compose and ops-monitor; preflight's advisories
    match profile names one at a time and a literal `*` matches none of them (round 3)."""

    def test_a_bare_star_becomes_every_profile(self) -> None:
        assert expand_profile_wildcard("*", "inventory,storefront,hrms") == (
            "inventory,storefront,hrms"
        )

    def test_a_list_without_a_star_is_untouched(self) -> None:
        assert expand_profile_wildcard("inventory,hrms", "inventory,storefront,hrms") == (
            "inventory,hrms"
        )

    def test_a_star_beside_named_profiles_keeps_them_first_and_deduplicates(self) -> None:
        assert expand_profile_wildcard("hrms,*", "inventory,storefront,hrms") == (
            "hrms,inventory,storefront"
        )

    def test_empty_stays_empty(self) -> None:
        assert expand_profile_wildcard("", "inventory") == ""

    def test_the_star_is_never_glob_expanded_into_filenames(self) -> None:
        """Run from a directory full of files: an unquoted `for x in $list` would turn `*` into
        their names. (cwd is the repository root here, which has plenty.)"""
        script = f'source "{ACTIVE_PROFILES_LIB}"; expand_profile_wildcard "*" "inventory"'
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
        )
        assert result.stdout.strip() == "inventory"
