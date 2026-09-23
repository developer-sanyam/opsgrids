"""Lints `compose.prod.yml` against `memory-budget.json` — the gate ADR-0013's memory-budget
erratum (2026-09-23) exists to be. A future slice that adds a container to one and not the
other fails here, by name, in CI's static job (`make test-infra`), before it ever reaches the
box's own `preflight.sh`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
COMPOSE_FILE = EDGE_DIR / "compose.prod.yml"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"
ENV_EXAMPLE = EDGE_DIR / ".env.example"
LIB_DIR = EDGE_DIR / "lib"
MEMORY_BUDGET_SH = LIB_DIR / "memory-budget.sh"

sys.path.insert(0, str(LIB_DIR))
from memory_budget_totals import expected_total_mb, load_budget  # noqa: E402


def _verdict(
    mem_total_kb: int, expected_mb: int, soft_percent: int, hard_percent: int
) -> tuple[str, int]:
    script = f'source "{MEMORY_BUDGET_SH}"; memory_budget_verdict "$1" "$2" "$3" "$4"'
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
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    verdict, percent = result.stdout.split()
    return verdict, int(percent)


def _compose_services() -> dict[str, dict]:
    raw = yaml.safe_load(COMPOSE_FILE.read_text())
    return raw["services"]


def _budget() -> dict:
    return load_budget(BUDGET_FILE)


def _enabled_profiles() -> set[str]:
    """`.env.example`'s COMPOSE_PROFILES. Deliberately empty since the memory-headroom slice
    (2026-09-23): re-measuring `kinds.background` (see memory-budget.json's own header) showed
    every profile on at once no longer fits this box's hard budget line at all, so the example
    stopped modelling that as a safe default. Empty means core only; a real box's list is written
    by hand (review round 1, H2/M4 — nothing computes it from the catalogue)."""
    for line in ENV_EXAMPLE.read_text().splitlines():
        if line.startswith("COMPOSE_PROFILES="):
            return {p for p in line.split("=", 1)[1].split(",") if p}
    raise AssertionError("COMPOSE_PROFILES not found in infrastructure/edge/.env.example")


def _every_sellable_profile() -> set[str]:
    """`memory-budget.json`'s own `profiles` list — every product this platform can sell, at
    once. No longer what `.env.example` defaults to (see `_enabled_profiles`'s docstring), but
    still the worst case an operator could configure by hand, and the scenario the hard budget
    line exists to catch."""
    return set(_budget()["profiles"])


def test_every_compose_service_has_a_mem_limit() -> None:
    services = _compose_services()
    missing = sorted(name for name, cfg in services.items() if "mem_limit" not in cfg)
    assert not missing, (
        "these services in compose.prod.yml have no mem_limit, so an unbounded process — not "
        f"this file — is what the OOM killer picks a victim from: {missing}"
    )


def test_every_compose_service_is_in_the_budget() -> None:
    services = set(_compose_services())
    budgeted = set(_budget()["containers"])
    missing = sorted(services - budgeted)
    assert not missing, (
        "added to compose.prod.yml with no entry in memory-budget.json — this container's "
        f"memory cost is unaccounted for: {missing}. Add each to memory-budget.json's "
        '"containers" (ADR-0013\'s memory-budget erratum, 2026-09-23, is the gate this closes).'
    )


def test_every_budget_entry_is_a_real_compose_service() -> None:
    """The other direction of drift: a container memory-budget.json still prices that
    compose.prod.yml no longer runs — stale, and it would understate a future refactor's
    actual savings."""
    services = set(_compose_services())
    budgeted = set(_budget()["containers"])
    stale = sorted(budgeted - services)
    assert not stale, (
        f"memory-budget.json names containers compose.prod.yml no longer defines: {stale}. "
        "Remove them so the budget reflects what actually deploys."
    )


def test_every_enabled_profile_container_exists_in_compose() -> None:
    """A container the budget says runs for an enabled profile that compose does not define
    would make preflight's sum count memory nothing actually allocates."""
    budget = _budget()
    services = set(_compose_services())
    enabled = _enabled_profiles()
    for name, meta in budget["containers"].items():
        profile = meta.get("profile")
        if profile is None or profile in enabled:
            assert name in services, (
                f"{name} is budgeted against an enabled profile ({profile!r}) but "
                "compose.prod.yml defines no such service"
            )


def test_datastores_carry_both_mem_limit_and_mem_reservation() -> None:
    """postgres, rabbitmq, redis and caddy are the four processes big enough, and important
    enough, that ADR-0013's memory-budget erratum gave them an explicit mem_reservation too —
    so host-wide memory pressure reclaims from the squeezable api/background containers before
    it touches a datastore."""
    services = _compose_services()
    for name in ("postgres", "rabbitmq", "redis", "caddy"):
        cfg = services[name]
        assert "mem_limit" in cfg, f"{name} has no mem_limit"
        assert "mem_reservation" in cfg, f"{name} has no mem_reservation"


def test_env_example_profile_list_fits_the_documented_box() -> None:
    """.env.example's COMPOSE_PROFILES must not cross the hard budget line for the box
    memory-budget.json documents (ADR-0013's erratum, 2026-09-23): if it does, a fresh box
    following .env.example word for word cannot come up without heavy swapping — caught here,
    not on a 3am page. Trivial since the memory-headroom slice (2026-09-23): the example now
    ships empty (see `_enabled_profiles`'s docstring) precisely because the alternative —
    defaulting to every profile — no longer passes this same assertion (see the test directly
    below, which proves that on purpose)."""
    budget = _budget()
    enabled = _enabled_profiles()
    total = expected_total_mb(budget, enabled)
    host = budget["host"]
    mem_total_kb = host["mem_total_mb_reference"] * 1024
    verdict, percent = _verdict(
        mem_total_kb, total, host["soft_budget_percent"], host["hard_budget_percent"]
    )
    assert verdict != "refuse", (
        f"COMPOSE_PROFILES={','.join(sorted(enabled))} expects {total}M steady-state on a "
        f"{host['mem_total_mb_reference']}M box ({percent}%) — over the hard budget "
        f"({host['hard_budget_percent']}%). Trim a profile or consolidate a service."
    )


def test_every_sellable_profile_at_once_is_refused_not_silently_allowed() -> None:
    """The scenario `.env.example` used to default to — every product this platform can sell,
    on one box, at once — is exactly what `preflight.sh`'s hard budget line exists to catch.

    Re-measuring `kinds.background` (memory-budget.json's own header, the memory-headroom
    slice, 2026-09-23) showed this combination costs far more than the previous, never-actually-
    measured 35-50 MB assumption priced it at — it does not fit this box at all any more, not
    even before a single planned service is added. That is a real finding about a real box, not
    a bug in this test: the assertion below is that `preflight.sh` would correctly REFUSE this
    deploy rather than let it through to swap or OOM in production. If a future consolidation
    slice brings this back under the hard line, update this assertion (and reconsider whether
    `.env.example` should default to a wider list again) at the same time — a passing test here
    would then be silently promising a safety net that no longer does anything."""
    budget = _budget()
    enabled = _every_sellable_profile()
    total = expected_total_mb(budget, enabled)
    host = budget["host"]
    mem_total_kb = host["mem_total_mb_reference"] * 1024
    verdict, percent = _verdict(
        mem_total_kb, total, host["soft_budget_percent"], host["hard_budget_percent"]
    )
    assert verdict == "refuse", (
        f"every sellable profile ({','.join(sorted(enabled))}) now expects {total}M steady-state "
        f"on a {host['mem_total_mb_reference']}M box ({percent}%) — no longer over the hard "
        "budget. If a consolidation slice earned this back, update this test's expectation."
    )


def test_realistic_default_profile_list_fits_comfortably() -> None:
    """What the box actually runs today (ADR-0013 §5's own slice plan: Inventory is the only
    product sold so far) must fit with real margin — not just under the hard line, under the
    soft one too. This is the scenario `EDGE_PROFILES`/COMPOSE_PROFILES auto-off actually
    targets: profiles grow one at a time, as products are sold, not all at once."""
    budget = _budget()
    enabled = {"inventory"}
    total = expected_total_mb(budget, enabled)
    host = budget["host"]
    mem_total_kb = host["mem_total_mb_reference"] * 1024
    verdict, percent = _verdict(
        mem_total_kb, total, host["soft_budget_percent"], host["hard_budget_percent"]
    )
    assert verdict == "ok", (
        f"COMPOSE_PROFILES=inventory expects {total}M steady-state on a "
        f"{host['mem_total_mb_reference']}M box ({percent}%) — no longer comfortably under the "
        f"soft budget ({host['soft_budget_percent']}%)."
    )
