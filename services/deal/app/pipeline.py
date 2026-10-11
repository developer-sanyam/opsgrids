"""The stage catalogue of a deal pipeline: pure values and rules, no I/O, no session.

A deal's `stage` column holds a stage **key**. What a key means — its label, its position, the
tone it is drawn in, the probability a deal takes on entering it and, above all, its **outcome**
— comes from the organization's pipeline. An organization that has never configured one has
nothing stored and reads `DEFAULT_PIPELINE`, which is the six stages the service has always had
(the *virtual default*). `selectors.load_pipeline_spec` builds a `PipelineSpec` from whichever
applies, and every rule in `app.domain` and every projection in `app.selectors` takes one
instead of comparing a key with a literal.

Consumers test the **outcome** (`open`, `won`, `lost`), never the key. A key is an immutable
identifier that events, audit rows and tenant-authored automation rules carry; what it *means*
is its stage's outcome.

This module is the one place in the deal service the six legacy keys are spelled
(`tests/test_no_stage_literals.py` holds that line).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

Outcome = Literal["open", "won", "lost"]
Tone = Literal["neutral", "info", "success", "warning", "danger"]

OUTCOMES: Final[tuple[str, ...]] = ("open", "won", "lost")
TONES: Final[tuple[str, ...]] = ("neutral", "info", "success", "warning", "danger")

#: A stage key: an immutable slug that fits `deals.stage` (`VARCHAR(20)`). The same pattern is
#: the database CHECK `ck_pipeline_stages_key` and the wire type `schemas.StageKey`.
KEY_PATTERN: Final = r"^[a-z0-9_]{1,20}$"
_KEY = re.compile(KEY_PATTERN)

#: What a closing stage must carry as its default probability, and what a deal takes on closing:
#: a deal won is 100 percent, a deal lost is 0. An `open` stage has no pin.
CLOSING_PROBABILITY: Final[dict[str, int]] = {"won": 100, "lost": 0}

#: The pipeline an organization reads until its first stage-configuration write.
DEFAULT_PIPELINE_KEY: Final = "sales"
DEFAULT_PIPELINE_NAME: Final = "Sales pipeline"


class PipelineInvalid(ValueError):
    """A proposed set of stages breaks a rule the pipeline must hold.

    `code` is stable and machine-readable (a route maps it to a field error); `detail` is for a
    person and may name a key or a label but never a deal.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StageSpec:
    """One stage as the rules see it. `id` is None for a stage of the virtual default."""

    key: str
    label: str
    position: int
    outcome: Outcome
    default_probability: int
    tone: Tone
    archived: bool = False
    id: uuid.UUID | None = None


@dataclass(frozen=True)
class PipelineSpec:
    """An organization's stages. `stages` holds every stage, archived included; the methods
    below never assume it arrives ordered, so a loader's ordering cannot change an answer."""

    stages: tuple[StageSpec, ...]
    id: uuid.UUID | None = None
    key: str = DEFAULT_PIPELINE_KEY
    name: str = DEFAULT_PIPELINE_NAME
    #: False for the virtual default (nothing stored for the organization), True for rows read
    #: from `deal.pipeline_stages`.
    materialised: bool = False

    def get(self, key: str) -> StageSpec | None:
        """The stage with this key, archived or not; None when the pipeline has no such key."""
        for stage in self.stages:
            if stage.key == key:
                return stage
        return None

    def active(self) -> tuple[StageSpec, ...]:
        """The stages a deal may be moved into, in board order."""
        return tuple(
            sorted(
                (stage for stage in self.stages if not stage.archived),
                key=lambda stage: (stage.position, stage.key),
            )
        )

    def keys_of(self, outcome: Outcome) -> tuple[str, ...]:
        """Every key — archived included — whose stage has this outcome. A deal may keep
        sitting in an archived stage, so a filter by outcome has to know those too."""
        return tuple(stage.key for stage in self.stages if stage.outcome == outcome)

    def entry_stage(self) -> StageSpec | None:
        """Where a new deal opens: the first active open stage by position. None when the
        pipeline has no active open stage, which a valid pipeline never lacks."""
        return next((stage for stage in self.active() if stage.outcome == "open"), None)

    def outcome_of(self, key: str) -> Outcome:
        """The outcome of the stage a deal sits in. A key the pipeline does not contain reads
        `open`: that is what `is_open` has always said of a key it does not know, and it keeps
        one unresolvable row from changing a total."""
        stage = self.get(key)
        return stage.outcome if stage is not None else "open"

    def is_last_open(self, key: str) -> bool:
        """Whether this is the active open stage with the highest position — the stage a deal
        sits in just before it is decided."""
        open_stages = [stage for stage in self.active() if stage.outcome == "open"]
        return bool(open_stages) and open_stages[-1].key == key


#: The six stages the service has always had, as the virtual default. Tones are the CRM
#: SPA's (`StatusPill.tsx`), probabilities its board's (`data/stages.ts`).
DEFAULT_PIPELINE: Final = PipelineSpec(
    stages=(
        StageSpec("lead", "Lead", 0, "open", 20, "info"),
        StageSpec("qualified", "Qualified", 1, "open", 40, "info"),
        StageSpec("proposal", "Proposal", 2, "open", 55, "info"),
        StageSpec("negotiation", "Negotiation", 3, "open", 70, "warning"),
        StageSpec("closed_won", "Closed Won", 4, "won", 100, "success"),
        StageSpec("closed_lost", "Closed Lost", 5, "lost", 0, "danger"),
    )
)


def validate(stages: Sequence[StageSpec]) -> None:
    """Raise `PipelineInvalid` unless these stages form a pipeline the service can run.

    The row-local rules mirror the database CHECKs (so a refusal here is a field error rather
    than a constraint failure); the set-level rules — at least one active stage of each
    outcome, closed stages last, dense positions — exist nowhere else, because a cross-row
    trigger would be racy. A stage-configuration write builds the *proposed* set, calls this,
    and only then writes (R2.5).
    """
    for stage in stages:
        if not _KEY.fullmatch(stage.key):
            raise PipelineInvalid("key_invalid", f"{stage.key!r} is not a valid stage key")
        if stage.outcome not in OUTCOMES:
            raise PipelineInvalid("outcome_invalid", f"{stage.key!r} has outcome {stage.outcome!r}")
        if stage.tone not in TONES:
            raise PipelineInvalid("tone_invalid", f"{stage.key!r} has tone {stage.tone!r}")
        if not 0 <= stage.default_probability <= 100:
            raise PipelineInvalid(
                "probability_out_of_range", f"{stage.key!r} has probability outside 0-100"
            )
        pin = CLOSING_PROBABILITY.get(stage.outcome)
        if pin is not None and stage.default_probability != pin:
            raise PipelineInvalid(
                "probability_pinned", f"{stage.key!r} is {stage.outcome} and must carry {pin}"
            )

    seen: set[str] = set()
    for stage in stages:
        if stage.key in seen:
            raise PipelineInvalid("duplicate_key", f"the key {stage.key!r} appears twice")
        seen.add(stage.key)

    active = sorted(
        (stage for stage in stages if not stage.archived),
        key=lambda stage: (stage.position, stage.key),
    )
    labels: set[str] = set()
    for stage in active:
        folded = stage.label.casefold()
        if folded in labels:
            raise PipelineInvalid(
                "duplicate_label", f"two active stages are labelled {stage.label!r}"
            )
        labels.add(folded)

    for outcome in OUTCOMES:
        if not any(stage.outcome == outcome for stage in active):
            raise PipelineInvalid(f"no_active_{outcome}_stage", f"no active {outcome} stage")

    if [stage.position for stage in active] != list(range(len(active))):
        raise PipelineInvalid(
            "positions_not_dense", "active stage positions must be exactly 0 to n-1"
        )
    last_open = max(stage.position for stage in active if stage.outcome == "open")
    if any(stage.outcome != "open" and stage.position < last_open for stage in active):
        raise PipelineInvalid("closed_stage_before_open", "won and lost stages must come last")
