"""Proves the backlog memory-budget.json's `planned` key prices (ADR-0013's memory-headroom
addendum, 2026-09-23; corrected by the review's fix rounds, 2026-09-24 — B1(c), then round 2's
B2) actually project against what production runs today, not a wrong, smaller stand-in for it.

**REALISTIC_PROFILES is every profile (B1, fix round 1): production runs all ten — every product
this platform catalogues `available`, on the one box's own explicit `COMPOSE_PROFILES` — not just
`inventory`.** The previous version of this test used `{"inventory"}`, on the mistaken premise
that Inventory was the only product sold; the coordinator's own review corrected that premise.
Since the corrected `kinds.background` price (memory-budget.json's own header) already puts the
real ten-profile set at the hard budget line before a single planned item is added,
`test_current_production_is_at_the_hard_line_so_the_owner_decision_is_required` and
`test_realistic_today_plus_every_planned_service_projection` below document that reality with
exact numbers instead of asserting a fit that does not exist. What preflight does about an
overcommitted box is the owner's explicit `EDGE_BUDGET_ACCEPTED_MB` ceiling (round 2, B2) — the
"grandfather" clause round 1 shipped was removed because it let the last deploy to succeed set the
bar instead of a person.

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


def test_current_production_is_at_the_hard_line_so_the_owner_decision_is_required() -> None:
    """The honest state of the box, pinned by number rather than left to drift: the real
    ten-profile production set is at or over the hard budget line before any planned service is
    added. This is not new information this test discovered — it is ADR-0013's memory-headroom
    addendum's own finding, re-measured — and it is why the RUNBOOK asks the owner to decide
    (resize, de-list, or set `EDGE_BUDGET_ACCEPTED_MB`) *before* the first deploy. If this number
    ever drops clearly under the hard line, update it — and consider whether the owner-decision
    section of the RUNBOOK is still needed."""
    budget = _budget()
    profiles = _real_production_profiles(budget)
    current = expected_total_mb(budget, profiles)
    host = budget["host"]
    percent = current * 100 // host["mem_total_mb_reference"]
    assert percent >= host["hard_budget_percent"], (
        f"the real production profile set ({','.join(sorted(profiles))}) now projects to "
        f"{current}M on a {host['mem_total_mb_reference']}M box ({percent}%) — no longer at the "
        f"hard budget on its own. Update this test's expectation, and reconsider whether the "
        f"RUNBOOK's owner-decision section (EDGE_BUDGET_ACCEPTED_MB) is still needed."
    )


def test_runbook_ceiling_example_is_the_number_preflight_would_print() -> None:
    """The RUNBOOK tells the owner to set `EDGE_BUDGET_ACCEPTED_MB` to "the number preflight's own
    projection printed" and gives the ten-profile figure as the worked example. If a budget entry
    moves and that example does not, an owner copying it would set a ceiling one deploy away from
    refusing (too low) or quietly accepting more overcommit than was decided (too high)."""
    budget = _budget()
    total = expected_total_mb(budget, set(budget["profiles"]))
    runbook = (EDGE_DIR / "RUNBOOK.md").read_text()
    assert f"EDGE_BUDGET_ACCEPTED_MB={total}" in runbook, (
        f"the ten-profile set prices at {total}M but RUNBOOK.md's worked example does not say "
        f"EDGE_BUDGET_ACCEPTED_MB={total} — update the example (and the ADR-0013 round-2 table)."
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


def test_planned_and_containers_never_overlap() -> None:
    """A `planned` placeholder is deleted the day its container lands in `containers` (round 3).
    Left behind, it prices the same container twice in every projection — which is exactly what
    happened to `ops-monitor`, `lead-api` (compose name: `lead`) and `customer-worker` when their
    slices merged: the projected total counted each of them once as real and once as planned.

    Names differ between the two lists (`lead-api` vs `lead`), so this checks each planned
    entry's own name, its declared eventual `container`, and the name with an `-api` suffix
    stripped — the convention every compose service name here follows."""
    budget = _budget()
    containers = set(budget["containers"])
    for name, meta in planned_entries(budget).items():
        candidates = {name, meta.get("container", name), name.removesuffix("-api")}
        clash = candidates & containers
        assert not clash, (
            f"planned.{name} is already a real container ({sorted(clash)}) in `containers` — "
            "delete the placeholder; it is being priced twice."
        )


def test_planned_api_entries_name_the_container_they_will_become() -> None:
    """The overlap test above is only as good as the names it can compare. An `-api` planned entry
    without a `container` field is guessing at the compose name; declare it."""
    budget = _budget()
    for name, meta in planned_entries(budget).items():
        if name.endswith("-api"):
            assert "container" in meta, f"planned.{name} does not say what compose will call it"


def test_the_accepted_ceiling_cap_is_above_the_hard_line_and_documented() -> None:
    """`host.max_accepted_percent` is what preflight bounds `EDGE_BUDGET_ACCEPTED_MB` with. A cap at
    or below the hard line would make the ceiling meaningless (nothing could ever be accepted)."""
    host = _budget()["host"]
    assert host["max_accepted_percent"] > host["hard_budget_percent"]
    assert host["max_accepted_percent"] == 125
