"""Pure rollups and vocabulary for the CRM pipeline. No I/O, no session — only model
instances in, plain values out, so this is trivially testable and mirrors the SPA's
`data/derive.ts` and `data/stages.ts` line for line: the numbers agree whichever side
computes them.

Also the home of the stage-transition rule: which moves through the pipeline are legal is
an algorithm, not a persistence detail, so it lives here and `services.move_stage` is its
enforcement point.

**Nothing here compares a stage key with a literal.** Every rule takes the organization's
`PipelineSpec` (`app.pipeline`) and asks it for a stage's *outcome*: `open`, `won` or `lost`.
The `spec` argument defaults to the virtual default pipeline — the six stages the service
has always had — so a caller that has no spec to hand, and every test written before stages
were data, gets exactly the answers it always got (`tests/test_pipeline_domain.py` holds that
parity over the whole input grid).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.models import Activity, Deal
from app.pipeline import DEFAULT_PIPELINE, PipelineSpec

# ------------------------------------------------------------------ vocabulary (labels match SPA)

#: The default pipeline's vocabulary, derived rather than restated so there is one place the
#: six stages are written (`app.pipeline`). Kept under their old names because callers and
#: tests import them; they describe the *default* pipeline, never an organization's own.
#: Every stage in board order — the two closing stages last (`data/stages.ts`).
STAGE_ORDER: tuple[str, ...] = tuple(stage.key for stage in DEFAULT_PIPELINE.active())
STAGE_LABELS: dict[str, str] = {stage.key: stage.label for stage in DEFAULT_PIPELINE.stages}
#: The open (in-flight) stages, in order — the working columns of the board.
OPEN_STAGES: tuple[str, ...] = tuple(
    stage.key for stage in DEFAULT_PIPELINE.active() if stage.outcome == "open"
)
TERMINAL_STAGES: frozenset[str] = frozenset(
    stage.key for stage in DEFAULT_PIPELINE.stages if stage.outcome != "open"
)


def is_open(stage: str, spec: PipelineSpec = DEFAULT_PIPELINE) -> bool:
    """Whether a deal in this stage is still in play. A key the pipeline does not contain
    reads open — the same answer `is_open` has always given a key it did not know."""
    return spec.outcome_of(stage) == "open"


def can_transition(current: str, new: str, spec: PipelineSpec = DEFAULT_PIPELINE) -> bool:
    """Whether a deal may move `current` → `new`.

    Open deals move freely between the open stages (forward on progress, backward on a
    re-qualification) and may close either way. A closed deal is a record: it moves
    nowhere — reopening one is a new deal, so won/lost reporting stays append-only in
    spirit. A no-op "move" to the same stage is not a transition, and neither is a move
    into a stage the pipeline does not have or has archived.
    """
    source = spec.get(current)
    target = spec.get(new)
    if source is None or target is None or target.archived:
        return False
    return source.outcome == "open" and new != current


# ------------------------------------------------------------------ rollups (mirror derive.ts)


def open_deals(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> list[Deal]:
    """Deals still in flight (their stage's outcome is `open`)."""
    return [d for d in deals if is_open(d.stage, spec)]


def pipeline_value_minor(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> int:
    """Total open pipeline value, in minor units."""
    return sum(d.value_minor for d in open_deals(deals, spec))


def weighted_pipeline_minor(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> int:
    """Probability-weighted open pipeline (the forecast), in minor units."""
    return sum(round(d.value_minor * d.probability / 100) for d in open_deals(deals, spec))


def open_count(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> int:
    return len(open_deals(deals, spec))


def win_rate_pct(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> int:
    """Win rate over *decided* deals: won / (won + lost), 0-100, where a deal is won or lost
    by its stage's outcome. Deals still open don't count — a full pipeline shouldn't drag the
    rate toward zero."""
    won = sum(1 for d in deals if spec.outcome_of(d.stage) == "won")
    lost = sum(1 for d in deals if spec.outcome_of(d.stage) == "lost")
    decided = won + lost
    return 0 if decided == 0 else round(won / decided * 100)


def won_value_minor(deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE) -> int:
    """Total value won across every deal in a `won` stage, in minor units."""
    return sum(d.value_minor for d in deals if spec.outcome_of(d.stage) == "won")


def stage_funnel(
    deals: Sequence[Deal], spec: PipelineSpec = DEFAULT_PIPELINE
) -> list[dict[str, object]]:
    """The by-stage funnel over the active open stages, in position order: count and summed
    value per stage, with `pct` scaled to the richest stage so the bars read as a funnel —
    the same arithmetic as the SPA's `stageFunnel`. Archived stages are not columns, and a
    deal sitting in a stage the pipeline does not contain is in no row."""
    rows: list[dict[str, object]] = []
    values: list[int] = []
    for stage in spec.active():
        if stage.outcome != "open":
            continue
        in_stage = [d for d in deals if d.stage == stage.key]
        value = sum(d.value_minor for d in in_stage)
        values.append(value)
        rows.append(
            {
                "stage": stage.key,
                "label": stage.label,
                "count": len(in_stage),
                "value_minor": value,
                "pct": 0,
            }
        )
    top = max((*values, 1))
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


# ------------------------------------------------------------------ conversion ids (copy of lead's)

#: lead's `_CONVERSION_NAMESPACE`, frozen: changing it would re-derive every id ever handed out.
CONVERSION_NAMESPACE = uuid.UUID("6f2e6f3a-6d1e-4b6a-9c8e-2f7a5b1d4c90")


def conversion_ids(
    lead_id: uuid.UUID, generation: int = 0
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The `(company_id, contact_id, deal_id)` a lead converts into at `generation`.

    A verbatim copy of lead's `app.domain.conversion_ids` (`import app` is ambiguous across
    services, and platform-core is not the home of a CRM algorithm). The three copies are held
    equal by `libs/contracts/vectors/conversion_ids.v1.json`, which each service's tests assert.
    The aborted-conversion handler re-derives an attempt's ids with this and never acts on the
    ids an event names. Generation `0` has no suffix; each later generation appends `:{generation}`.
    """
    suffix = "" if generation == 0 else f":{generation}"
    return (
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:company{suffix}"),
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:contact{suffix}"),
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:deal{suffix}"),
    )
