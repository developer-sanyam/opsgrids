"""The stage vocabulary and rollups exactly as they were before stages became data.

Verbatim copies of `app.domain` and `app.services` as of the commit before R2.3 (the constants
at `domain.py:20-39`, the seven rollups and the transition rule at `domain.py:42-117`, the
default probabilities at `services.py:51-58`). They are frozen here on purpose: the parity tests
(`test_pipeline_domain.py`) hold the new, pipeline-driven code to these answers over the whole
input grid, so "behaves as it always did for the legacy six" is a checked claim and not a
reading of the diff.

Nothing in `app/` imports this module, and nothing here may be edited to make a test pass: if
the new code and these disagree, the new code is wrong (the one deliberate exception is the
closing-probability pin on a stage move, which is a service rule and is not modelled here).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models import Deal

LEGACY_STAGE_ORDER: tuple[str, ...] = (
    "lead",
    "qualified",
    "proposal",
    "negotiation",
    "closed_won",
    "closed_lost",
)
LEGACY_STAGE_LABELS: dict[str, str] = {
    "lead": "Lead",
    "qualified": "Qualified",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed_won": "Closed Won",
    "closed_lost": "Closed Lost",
}
LEGACY_OPEN_STAGES: tuple[str, ...] = ("lead", "qualified", "proposal", "negotiation")
LEGACY_TERMINAL_STAGES: frozenset[str] = frozenset({"closed_won", "closed_lost"})
LEGACY_STAGE_DEFAULT_PROBABILITY: dict[str, int] = {
    "lead": 20,
    "qualified": 40,
    "proposal": 55,
    "negotiation": 70,
    "closed_won": 100,
    "closed_lost": 0,
}


def legacy_is_open(stage: str) -> bool:
    return stage not in LEGACY_TERMINAL_STAGES


def legacy_can_transition(current: str, new: str) -> bool:
    if current not in LEGACY_STAGE_ORDER or new not in LEGACY_STAGE_ORDER:
        return False
    return current not in LEGACY_TERMINAL_STAGES and new != current


def legacy_open_deals(deals: Sequence[Deal]) -> list[Deal]:
    return [d for d in deals if legacy_is_open(d.stage)]


def legacy_pipeline_value_minor(deals: Sequence[Deal]) -> int:
    return sum(d.value_minor for d in legacy_open_deals(deals))


def legacy_weighted_pipeline_minor(deals: Sequence[Deal]) -> int:
    return sum(round(d.value_minor * d.probability / 100) for d in legacy_open_deals(deals))


def legacy_open_count(deals: Sequence[Deal]) -> int:
    return len(legacy_open_deals(deals))


def legacy_win_rate_pct(deals: Sequence[Deal]) -> int:
    won = sum(1 for d in deals if d.stage == "closed_won")
    lost = sum(1 for d in deals if d.stage == "closed_lost")
    decided = won + lost
    return 0 if decided == 0 else round(won / decided * 100)


def legacy_won_value_minor(deals: Sequence[Deal]) -> int:
    return sum(d.value_minor for d in deals if d.stage == "closed_won")


def legacy_stage_funnel(deals: Sequence[Deal]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    values: list[int] = []
    for stage in LEGACY_OPEN_STAGES:
        in_stage = [d for d in deals if d.stage == stage]
        value = sum(d.value_minor for d in in_stage)
        values.append(value)
        rows.append(
            {
                "stage": stage,
                "label": LEGACY_STAGE_LABELS[stage],
                "count": len(in_stage),
                "value_minor": value,
                "pct": 0,
            }
        )
    top = max(*values, 1)
    for row, value in zip(rows, values, strict=True):
        row["pct"] = round(value / top * 100)
    return rows
