"""Proves the backlog memory-budget.json's `planned` key prices (ADR-0013's memory-headroom
addendum, 2026-09-23; corrected by the review's fix round, 2026-09-24 — B1(c)) actually project
against what production runs today, not a wrong, smaller stand-in for it.

**REALISTIC_PROFILES is every profile (B1, fix round 1): production runs all ten — every product
this platform catalogues `available`, on the one box's own explicit `COMPOSE_PROFILES` — not just
`inventory`.** The previous version of this test used `{"inventory"}`, on the mistaken premise
that Inventory was the only product sold; the coordinator's own review corrected that premise.
Since the corrected `kinds.background` price (memory-budget.json's own header) already puts the
real ten-profile set over the hard budget line before a single planned item is added,
`test_current_production_over_hard_budget_is_grandfathered_not_a_fresh_regression` and
`test_realistic_today_plus_every_planned_service_projection` below document that reality with
exact numbers instead of asserting a fit that does not exist — that is what B1(a)'s grandfather
clause (`grandfathered_verdict`, lib/memory-budget.sh) is *for*: an already-overcommitted box
whose next deploy does not make it worse still deploys, with the overcommit stated plainly.

`planned` prices backlog items with no code and no compose.prod.yml entry yet — see
`memory-budget.json`'s own `planned.$comment` for what a placeholder is and is not allowed to
do (never part of the enforced budget preflight.sh refuses a deploy over; only ever a projected
total reported alongside it) and for which two "incoming" items are deliberately absent from it
(account-worker's and subscription-worker's own merged sweeps — no new container, so nothing to
price).
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


def _budget() -> dict:
    return load_budget(BUDGET_FILE)


def _real_production_profiles(budget: dict) -> set[str]:
    """Every profile the box's own `COMPOSE_PROFILES` enables today (B1: all ten products this
    platform catalogues `available`) — `memory-budget.json`'s own `profiles` list names exactly
    this set, so there is nothing box-specific left to hardcode here."""
    return set(budget["profiles"])


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
    profiles = _real_production_profiles(budget)
    before = expected_total_mb(budget, profiles)
    # planned_total_mb reads the same budget; if expected_total_mb ever grew from summing
    # `planned` too, this would drift from `before` on the next call for no reason.
    assert expected_total_mb(budget, profiles) == before
    assert planned_total_mb(budget) > 0, "the planned list is empty — nothing to project"


def test_current_production_over_hard_budget_is_grandfathered_not_a_fresh_regression() -> None:
    """The honest state of the box today, pinned by number rather than left to drift: the real
    ten-profile production set already exceeds the hard budget line, before any planned service
    is added. This is not new information this test discovered — it is ADR-0013's memory-headroom
    addendum's own finding, re-measured — and it is exactly the situation B1(a)'s grandfather
    clause exists to let a box keep deploying through (not worse than last time) while it stays
    true. If this number ever drops back under the hard line, update it — and consider whether
    `.env.example`'s empty COMPOSE_PROFILES default and the grandfather clause are still needed."""
    budget = _budget()
    profiles = _real_production_profiles(budget)
    current = expected_total_mb(budget, profiles)
    host = budget["host"]
    percent = current * 100 // host["mem_total_mb_reference"]
    assert percent >= host["hard_budget_percent"], (
        f"the real production profile set ({','.join(sorted(profiles))}) now projects to "
        f"{current}M on a {host['mem_total_mb_reference']}M box ({percent}%) — no longer over "
        f"the hard budget on its own. Update this test's expectation, and reconsider whether "
        f"B1(a)'s grandfather clause and the empty .env.example default are still needed."
    )


def test_realistic_today_plus_every_planned_service_projection() -> None:
    """The number that documents the RAM decision (M8): today's real production footprint (every
    profile the box actually enables) plus every backlog placeholder in `planned` — CRM lead/
    customer, ops-monitor, and the rest of the roadmap. Pinned at a value **over** 100% on
    purpose: this is the number that tells the owner "more RAM (a VM resize) or de-listing a
    product" is a decision that has to be made before all of this backlog ships, not a test this
    slice's own consolidation work was expected to make pass. See ADR-0013's memory-headroom
    addendum for what was and was not done about it in this fix round."""
    budget = _budget()
    profiles = _real_production_profiles(budget)
    current = expected_total_mb(budget, profiles)
    planned = planned_total_mb(budget)
    projected = current + planned
    host = budget["host"]
    percent = projected * 100 // host["mem_total_mb_reference"]
    assert percent > host["hard_budget_percent"], (
        f"today's real production footprint ({current}M) plus every planned service ({planned}M) "
        f"projects to {projected}M on a {host['mem_total_mb_reference']}M box ({percent}%) — no "
        "longer over the hard budget. If consolidation work earned this back, update this test's "
        "expectation and revisit the RAM/de-listing decision this number used to document."
    )
