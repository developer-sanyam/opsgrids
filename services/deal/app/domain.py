"""Pure rollups and vocabulary for the CRM pipeline. No I/O, no session — only model
instances in, plain values out, so this is trivially testable and mirrors the SPA's
`data/derive.ts` and `data/stages.ts` line for line: the numbers agree whichever side
computes them.

Also the home of the stage-transition rule: which moves through the pipeline are legal is
an algorithm, not a persistence detail, so it lives here and `services.move_stage` is its
enforcement point.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.models import Activity, Deal

# ------------------------------------------------------------------ vocabulary (labels match SPA)

#: Every stage in board order — the two terminal stages last (`data/stages.ts`).
STAGE_ORDER: tuple[str, ...] = (
    "lead",
    "qualified",
    "proposal",
    "negotiation",
    "closed_won",
    "closed_lost",
)
STAGE_LABELS: dict[str, str] = {
    "lead": "Lead",
    "qualified": "Qualified",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed_won": "Closed Won",
    "closed_lost": "Closed Lost",
}
#: The four open (in-flight) stages, in order — the working columns of the board.
OPEN_STAGES: tuple[str, ...] = ("lead", "qualified", "proposal", "negotiation")
TERMINAL_STAGES: frozenset[str] = frozenset({"closed_won", "closed_lost"})


def is_open(stage: str) -> bool:
    return stage not in TERMINAL_STAGES


def can_transition(current: str, new: str) -> bool:
    """Whether a deal may move `current` → `new`.

    Open deals move freely between the open stages (forward on progress, backward on a
    re-qualification) and may close either way. A closed deal is a record: it moves
    nowhere — reopening one is a new deal, so won/lost reporting stays append-only in
    spirit. A no-op "move" to the same stage is not a transition.
    """
    if current not in STAGE_ORDER or new not in STAGE_ORDER:
        return False
    return current not in TERMINAL_STAGES and new != current


# ------------------------------------------------------------------ rollups (mirror derive.ts)


def open_deals(deals: Sequence[Deal]) -> list[Deal]:
    """Deals still in flight (not closed-won or closed-lost)."""
    return [d for d in deals if is_open(d.stage)]


def pipeline_value_minor(deals: Sequence[Deal]) -> int:
    """Total open pipeline value, in minor units."""
    return sum(d.value_minor for d in open_deals(deals))


def weighted_pipeline_minor(deals: Sequence[Deal]) -> int:
    """Probability-weighted open pipeline (the forecast), in minor units."""
    return sum(round(d.value_minor * d.probability / 100) for d in open_deals(deals))


def open_count(deals: Sequence[Deal]) -> int:
    return len(open_deals(deals))


def win_rate_pct(deals: Sequence[Deal]) -> int:
    """Win rate over *decided* deals: closed-won / (closed-won + closed-lost), 0-100.
    Deals still open don't count — a full pipeline shouldn't drag the rate toward zero."""
    won = sum(1 for d in deals if d.stage == "closed_won")
    lost = sum(1 for d in deals if d.stage == "closed_lost")
    decided = won + lost
    return 0 if decided == 0 else round(won / decided * 100)


def won_value_minor(deals: Sequence[Deal]) -> int:
    """Total value won across all closed-won deals, in minor units."""
    return sum(d.value_minor for d in deals if d.stage == "closed_won")


def stage_funnel(deals: Sequence[Deal]) -> list[dict[str, object]]:
    """The by-stage funnel over the four open stages: count and summed value per stage,
    with `pct` scaled to the richest stage so the bars read as a funnel — the same
    arithmetic as the SPA's `stageFunnel`."""
    rows: list[dict[str, object]] = []
    values: list[int] = []
    for stage in OPEN_STAGES:
        in_stage = [d for d in deals if d.stage == stage]
        value = sum(d.value_minor for d in in_stage)
        values.append(value)
        rows.append(
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "count": len(in_stage),
                "value_minor": value,
                "pct": 0,
            }
        )
    top = max(*values, 1)
    for row, value in zip(rows, values, strict=True):
        row["pct"] = round(value / top * 100)
    return rows


def activities_this_week(activities: Sequence[Activity], *, now: datetime | None = None) -> int:
    """Touches logged in the trailing seven days. A rolling window rather than a calendar
    week so the KPI does not reset to zero every Monday morning."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    return sum(1 for a in activities if cutoff <= _aware(a.occurred_at) <= now)


def _aware(dt: datetime) -> datetime:
    """Treat a naive timestamp as UTC so comparisons never raise."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
