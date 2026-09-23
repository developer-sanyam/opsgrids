"""Proves the backlog memory-budget.json's `planned` key prices (ADR-0013's memory-headroom
addendum, 2026-09-23) actually fits this box once added on top of what it realistically runs
today — not the "every product at once" worst case `test_every_sellable_profile_at_once_is_
refused_not_silently_allowed` exists to catch, but the box's own slice-plan reality (ADR-0013
§5: Inventory is the only product sold so far).

`planned` prices backlog items with no code and no compose.prod.yml entry yet — see
`memory-budget.json`'s own `planned.$comment` for what a placeholder is and is not allowed to
do (never part of the enforced budget preflight.sh refuses a deploy over; only ever a projected
total reported alongside it).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"
LIB_DIR = EDGE_DIR / "lib"

sys.path.insert(0, str(LIB_DIR))
from memory_budget_totals import (  # noqa: E402
    expected_total_mb,
    load_budget,
    planned_entries,
    planned_total_mb,
)

# The box's own realistic deployment today (ADR-0013 §5's own slice plan): core plus Inventory,
# the only product this platform has ever actually sold. Not `.env.example`'s COMPOSE_PROFILES
# (deliberately empty since this slice — see test_compose_memory_budget.py's `_enabled_profiles`
# docstring) and not `memory-budget.json`'s full `profiles` list (the worst case, and refused).
REALISTIC_PROFILES = {"inventory"}


def _budget() -> dict:
    return load_budget(BUDGET_FILE)


def test_planned_entries_all_name_a_priced_kind() -> None:
    """Every placeholder must resolve to a real `kinds` entry — a `planned` item with a typo'd
    or missing `kind` would silently price at 0 MB, which is worse than not tracking it at all."""
    budget = _budget()
    kinds = set(budget["kinds"])
    for name, meta in planned_entries(budget).items():
        assert meta.get("kind") in kinds, (
            f"planned.{name} names kind {meta.get('kind')!r}, which is not in memory-budget.json's "
            "`kinds` — add it there first."
        )


def test_planned_is_not_double_counted_into_the_enforced_budget() -> None:
    """`expected_total_mb` (what preflight.sh actually refuses a deploy over) must never move
    just because a `planned` entry was added — a placeholder for code that does not exist yet
    must not be able to fail a real deploy."""
    budget = _budget()
    before = expected_total_mb(budget, REALISTIC_PROFILES)
    # planned_total_mb reads the same budget; if expected_total_mb ever grew from summing
    # `planned` too, this would drift from `before` on the next call for no reason.
    assert expected_total_mb(budget, REALISTIC_PROFILES) == before
    assert planned_total_mb(budget) > 0, "the planned list is empty — nothing to project"


def test_realistic_today_plus_every_planned_service_fits_under_the_soft_budget() -> None:
    """The headroom target this slice exists to hit: today's realistic deployment (Inventory,
    the one product actually sold) plus every backlog placeholder in `planned` — CRM lead/
    customer, ops-monitor, and the rest of the roadmap — must land under the soft budget line
    (>=15% margin under the box's reference RAM), so the platform can grow into all of it
    without a resize (ADR-0013's memory-budget erratum: the owner will not resize the box)."""
    budget = _budget()
    current = expected_total_mb(budget, REALISTIC_PROFILES)
    planned = planned_total_mb(budget)
    projected = current + planned
    host = budget["host"]
    percent = projected * 100 // host["mem_total_mb_reference"]
    assert percent < host["soft_budget_percent"], (
        f"today's realistic footprint ({current}M, COMPOSE_PROFILES=inventory) plus every "
        f"planned service ({planned}M) projects to {projected}M on a "
        f"{host['mem_total_mb_reference']}M box ({percent}%) — at or over the "
        f"{host['soft_budget_percent']}% soft line, i.e. under 15% margin. A new planned entry "
        "or a corrected kind pushed this over; trim the backlog list, or consolidate further, "
        "before adding more to it."
    )
