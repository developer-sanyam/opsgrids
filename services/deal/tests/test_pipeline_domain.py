"""The pipeline catalogue and the rules that read it — pure, no database.

`app.pipeline` is values and rules only, so every test here builds its stages in memory. The
default pipeline is held to the frozen legacy vocabulary in `tests/legacy_pipeline.py`; a custom
pipeline is built by hand to prove the rules do not depend on the six keys.
"""

from __future__ import annotations

import random
import uuid
from datetime import date

import pytest

from app import domain
from app.models import Deal
from app.pipeline import (
    CLOSING_PROBABILITY,
    DEFAULT_PIPELINE,
    PipelineInvalid,
    PipelineSpec,
    StageSpec,
    validate,
)
from tests.legacy_pipeline import (
    LEGACY_OPEN_STAGES,
    LEGACY_STAGE_DEFAULT_PROBABILITY,
    LEGACY_STAGE_LABELS,
    LEGACY_STAGE_ORDER,
    LEGACY_TERMINAL_STAGES,
    legacy_can_transition,
    legacy_is_open,
    legacy_open_count,
    legacy_open_deals,
    legacy_pipeline_value_minor,
    legacy_stage_funnel,
    legacy_weighted_pipeline_minor,
    legacy_win_rate_pct,
    legacy_won_value_minor,
)


def custom_stages() -> list[StageSpec]:
    """Three open stages and one each of won and lost, none of them a legacy key."""
    return [
        StageSpec("discovery", "Discovery", 0, "open", 10, "info"),
        StageSpec("scoping", "Scoping", 1, "open", 30, "info"),
        StageSpec("quote", "Quote", 2, "open", 60, "warning"),
        StageSpec("won", "Won", 3, "won", 100, "success"),
        StageSpec("lost", "Lost", 4, "lost", 0, "danger"),
    ]


def code_of(stages: list[StageSpec]) -> str:
    with pytest.raises(PipelineInvalid) as refused:
        validate(stages)
    return refused.value.code


class TestDefaultPipeline:
    def test_the_default_equals_the_frozen_legacy_vocabulary(self) -> None:
        stages = DEFAULT_PIPELINE.stages
        assert tuple(s.key for s in stages) == LEGACY_STAGE_ORDER
        assert {s.key: s.label for s in stages} == LEGACY_STAGE_LABELS
        assert {s.key: s.default_probability for s in stages} == LEGACY_STAGE_DEFAULT_PROBABILITY
        assert tuple(s.key for s in stages if s.outcome == "open") == LEGACY_OPEN_STAGES
        assert frozenset(s.key for s in stages if s.outcome != "open") == LEGACY_TERMINAL_STAGES

    def test_the_default_passes_validate(self) -> None:
        validate(DEFAULT_PIPELINE.stages)

    def test_the_default_is_virtual(self) -> None:
        assert DEFAULT_PIPELINE.materialised is False
        assert DEFAULT_PIPELINE.id is None
        assert (DEFAULT_PIPELINE.key, DEFAULT_PIPELINE.name) == ("sales", "Sales pipeline")
        assert all(s.id is None and not s.archived for s in DEFAULT_PIPELINE.stages)

    def test_the_entry_stage_is_the_first_stage(self) -> None:
        entry = DEFAULT_PIPELINE.entry_stage()
        assert entry is not None
        assert entry.key == LEGACY_STAGE_ORDER[0]
        assert entry.default_probability == 20

    def test_the_tones_and_probabilities_are_the_spas(self) -> None:
        """The SPA's own tables: `STAGE_TONE` (`StatusPill.tsx:60-67`) and the probabilities of
        `STAGE_DEFAULT_PROBABILITY` (`data/stages.ts:31-38`)."""
        by_key = {s.key: s for s in DEFAULT_PIPELINE.stages}
        assert [(k, s.tone, s.default_probability, s.outcome) for k, s in by_key.items()] == [
            ("lead", "info", 20, "open"),
            ("qualified", "info", 40, "open"),
            ("proposal", "info", 55, "open"),
            ("negotiation", "warning", 70, "open"),
            ("closed_won", "success", 100, "won"),
            ("closed_lost", "danger", 0, "lost"),
        ]
        assert [s.position for s in DEFAULT_PIPELINE.stages] == [0, 1, 2, 3, 4, 5]


class TestPipelineSpec:
    def test_get_finds_an_archived_stage_too(self) -> None:
        archived = StageSpec("old", "Old", 9, "open", 10, "neutral", archived=True)
        spec = PipelineSpec(stages=(*custom_stages(), archived))
        assert spec.get("old") == archived
        assert spec.get("nope") is None

    def test_active_is_in_position_order_and_leaves_archived_stages_out(self) -> None:
        stages = custom_stages()
        stages[1] = StageSpec("scoping", "Scoping", 1, "open", 30, "info", archived=True)
        spec = PipelineSpec(stages=tuple(stages))
        assert [s.key for s in spec.active()] == ["discovery", "quote", "won", "lost"]

    def test_active_does_not_depend_on_the_order_the_stages_arrive_in(self) -> None:
        shuffled = tuple(reversed(custom_stages()))
        spec = PipelineSpec(stages=shuffled)
        assert [s.key for s in spec.active()] == ["discovery", "scoping", "quote", "won", "lost"]
        entry = spec.entry_stage()
        assert entry is not None
        assert entry.key == "discovery"

    def test_the_entry_stage_skips_an_archived_first_stage(self) -> None:
        stages = custom_stages()
        stages[0] = StageSpec("discovery", "Discovery", 0, "open", 10, "info", archived=True)
        entry = PipelineSpec(stages=tuple(stages)).entry_stage()
        assert entry is not None
        assert entry.key == "scoping"

    def test_there_is_no_entry_stage_when_every_open_stage_is_archived(self) -> None:
        stages = [
            StageSpec(s.key, s.label, s.position, s.outcome, s.default_probability, s.tone, True)
            if s.outcome == "open"
            else s
            for s in custom_stages()
        ]
        assert PipelineSpec(stages=tuple(stages)).entry_stage() is None

    def test_outcome_of_reads_the_stage_and_an_unknown_key_reads_open(self) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        assert spec.outcome_of("quote") == "open"
        assert spec.outcome_of("won") == "won"
        assert spec.outcome_of("lost") == "lost"
        assert spec.outcome_of("galaxy_brain") == "open"

    def test_is_last_open_is_the_highest_active_open_position(self) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        assert [s.key for s in spec.stages if spec.is_last_open(s.key)] == ["quote"]
        assert not spec.is_last_open("galaxy_brain")

    def test_an_archived_open_stage_is_never_the_last_open_stage(self) -> None:
        stages = [*custom_stages(), StageSpec("late", "Late", 9, "open", 80, "info", archived=True)]
        spec = PipelineSpec(stages=tuple(stages))
        assert spec.is_last_open("quote")
        assert not spec.is_last_open("late")

    def test_keys_of_includes_archived_stages(self) -> None:
        stages = [*custom_stages(), StageSpec("old_won", "Old won", 9, "won", 100, "success", True)]
        spec = PipelineSpec(stages=tuple(stages))
        assert spec.keys_of("won") == ("won", "old_won")
        assert spec.keys_of("lost") == ("lost",)
        assert spec.keys_of("open") == ("discovery", "scoping", "quote")


class TestValidate:
    def test_a_custom_pipeline_is_valid(self) -> None:
        validate(custom_stages())

    def test_several_won_and_lost_stages_are_allowed(self) -> None:
        stages = [
            *custom_stages()[:3],
            StageSpec("won", "Won", 3, "won", 100, "success"),
            StageSpec("won_late", "Won late", 4, "won", 100, "success"),
            StageSpec("lost", "Lost", 5, "lost", 0, "danger"),
        ]
        validate(stages)

    @pytest.mark.parametrize("outcome", ["open", "won", "lost"])
    def test_a_pipeline_needs_an_active_stage_of_every_outcome(self, outcome: str) -> None:
        kept = [s for s in custom_stages() if s.outcome != outcome]
        renumbered = [
            StageSpec(s.key, s.label, i, s.outcome, s.default_probability, s.tone)
            for i, s in enumerate(kept)
        ]
        assert code_of(renumbered) == f"no_active_{outcome}_stage"

    def test_an_archived_stage_does_not_count_as_the_active_one(self) -> None:
        stages = custom_stages()
        stages[4] = StageSpec("lost", "Lost", 4, "lost", 0, "danger", archived=True)
        assert code_of(stages) == "no_active_lost_stage"

    def test_a_closed_stage_ordered_before_an_open_one(self) -> None:
        stages = [
            StageSpec("discovery", "Discovery", 0, "open", 10, "info"),
            StageSpec("won", "Won", 1, "won", 100, "success"),
            StageSpec("quote", "Quote", 2, "open", 60, "warning"),
            StageSpec("lost", "Lost", 3, "lost", 0, "danger"),
        ]
        assert code_of(stages) == "closed_stage_before_open"

    def test_duplicate_keys(self) -> None:
        stages = [*custom_stages(), StageSpec("quote", "Quote again", 5, "open", 60, "info", True)]
        assert code_of(stages) == "duplicate_key"

    def test_duplicate_active_labels_compare_case_insensitively(self) -> None:
        stages = custom_stages()
        stages[1] = StageSpec("scoping", "DISCOVERY", 1, "open", 30, "info")
        assert code_of(stages) == "duplicate_label"

    def test_an_archived_stage_may_repeat_an_active_label(self) -> None:
        stages = [*custom_stages(), StageSpec("old", "discovery", 9, "open", 10, "info", True)]
        validate(stages)

    @pytest.mark.parametrize(
        ("outcome", "probability"), [("won", 99), ("won", 0), ("lost", 1), ("lost", 100)]
    )
    def test_a_won_stage_must_carry_100_and_a_lost_stage_0(
        self, outcome: str, probability: int
    ) -> None:
        stages = custom_stages()
        stages[3 if outcome == "won" else 4] = StageSpec(
            "won" if outcome == "won" else "lost",
            "Closed",
            3 if outcome == "won" else 4,
            outcome,
            probability,
            "success",
        )
        assert code_of(stages) == "probability_pinned"

    def test_the_pins_are_the_ones_a_move_applies(self) -> None:
        assert CLOSING_PROBABILITY == {"won": 100, "lost": 0}

    def test_positions_must_be_dense_among_active_stages(self) -> None:
        stages = custom_stages()
        stages[2] = StageSpec("quote", "Quote", 7, "open", 60, "warning")
        assert code_of(stages) == "positions_not_dense"

    def test_two_active_stages_may_not_share_a_position(self) -> None:
        stages = custom_stages()
        stages[1] = StageSpec("scoping", "Scoping", 0, "open", 30, "info")
        assert code_of(stages) == "positions_not_dense"

    def test_an_archived_stage_keeps_whatever_position_it_had(self) -> None:
        validate([*custom_stages(), StageSpec("old", "Old", 40, "open", 10, "info", True)])

    @pytest.mark.parametrize("key", ["", "Has Caps", "with-dash", "a" * 21, "naïve"])
    def test_a_key_outside_the_pattern(self, key: str) -> None:
        stages = custom_stages()
        stages[1] = StageSpec(key, "Scoping", 1, "open", 30, "info")
        assert code_of(stages) == "key_invalid"

    def test_an_unknown_outcome_tone_or_probability_is_refused(self) -> None:
        bad_outcome = custom_stages()
        bad_outcome[0] = StageSpec("discovery", "Discovery", 0, "pending", 10, "info")
        assert code_of(bad_outcome) == "outcome_invalid"
        bad_tone = custom_stages()
        bad_tone[0] = StageSpec("discovery", "Discovery", 0, "open", 10, "purple")
        assert code_of(bad_tone) == "tone_invalid"
        bad_probability = custom_stages()
        bad_probability[0] = StageSpec("discovery", "Discovery", 0, "open", 101, "info")
        assert code_of(bad_probability) == "probability_out_of_range"

    def test_the_refusal_carries_a_code_and_a_detail(self) -> None:
        with pytest.raises(PipelineInvalid) as refused:
            validate([])
        assert refused.value.code == "no_active_open_stage"
        assert "no active open stage" in refused.value.detail
        assert str(refused.value).startswith("no_active_open_stage: ")


# ------------------------------------------------------------------ parity with the legacy six


def deal(stage: str, value: int = 100, probability: int = 50) -> Deal:
    return Deal(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name=f"deal-{uuid.uuid4().hex[:6]}",
        company_id=uuid.uuid4(),
        value_minor=value,
        currency="INR",
        stage=stage,
        owner_id=uuid.uuid4(),
        probability=probability,
        close_date=date(2026, 8, 24),
        source="",
    )


def materialised_default() -> PipelineSpec:
    """The default pipeline as `load_pipeline_spec` returns it for an organization that has the
    six rows stored: the same stages, with ids, read from the database."""
    return PipelineSpec(
        stages=tuple(
            StageSpec(
                s.key,
                s.label,
                s.position,
                s.outcome,
                s.default_probability,
                s.tone,
                id=uuid.uuid4(),
            )
            for s in DEFAULT_PIPELINE.stages
        ),
        id=uuid.uuid4(),
        materialised=True,
    )


def seeded_book(size: int = 300) -> list[Deal]:
    rng = random.Random(20261007)  # noqa: S311 - deterministic test data, not a security use
    keys = [*LEGACY_STAGE_ORDER, "galaxy_brain"]  # one key no pipeline contains
    return [
        deal(rng.choice(keys), rng.randrange(0, 5_000_000), rng.randrange(0, 101))
        for _ in range(size)
    ]


class TestAliases:
    def test_the_domain_aliases_are_derived_from_the_default_pipeline(self) -> None:
        assert domain.STAGE_ORDER == LEGACY_STAGE_ORDER
        assert domain.STAGE_LABELS == LEGACY_STAGE_LABELS
        assert domain.OPEN_STAGES == LEGACY_OPEN_STAGES
        assert domain.TERMINAL_STAGES == LEGACY_TERMINAL_STAGES


class TestTransitions:
    @pytest.mark.parametrize("spec", [DEFAULT_PIPELINE, materialised_default()])
    def test_the_6x6_grid_equals_the_legacy_can_transition(self, spec: PipelineSpec) -> None:
        keys = [*LEGACY_STAGE_ORDER, "galaxy_brain", "", "Lead"]  # three keys nobody has
        for current in keys:
            for new in keys:
                expected = legacy_can_transition(current, new)
                assert domain.can_transition(current, new, spec) is expected, (current, new)
                assert domain.can_transition(current, new) is expected, (current, new)

    def test_is_open_equals_the_legacy_rule_for_every_key(self) -> None:
        for key in [*LEGACY_STAGE_ORDER, "galaxy_brain"]:
            assert domain.is_open(key) is legacy_is_open(key)

    def test_a_custom_pipeline_moves_between_its_own_open_stages_and_closes_either_way(
        self,
    ) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        assert domain.can_transition("discovery", "quote", spec)
        assert domain.can_transition("quote", "discovery", spec)
        assert domain.can_transition("scoping", "won", spec)
        assert domain.can_transition("scoping", "lost", spec)
        assert not domain.can_transition("quote", "quote", spec)
        # The legacy keys mean nothing here.
        assert not domain.can_transition("lead", "qualified", spec)

    def test_an_archived_target_is_refused(self) -> None:
        stages = custom_stages()
        stages[1] = StageSpec("scoping", "Scoping", 1, "open", 30, "info", archived=True)
        spec = PipelineSpec(stages=tuple(stages))
        assert not domain.can_transition("discovery", "scoping", spec)
        assert domain.can_transition("discovery", "quote", spec)

    def test_a_closed_deal_moves_nowhere_in_a_custom_pipeline(self) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        for closed in ("won", "lost"):
            for target in ("discovery", "quote", "won", "lost"):
                assert not domain.can_transition(closed, target, spec), (closed, target)

    def test_a_deal_in_an_archived_won_stage_still_moves_nowhere(self) -> None:
        stages = [*custom_stages(), StageSpec("old_won", "Old won", 9, "won", 100, "success", True)]
        spec = PipelineSpec(stages=tuple(stages))
        assert not domain.can_transition("old_won", "discovery", spec)

    def test_an_unknown_current_stage_is_not_a_transition(self) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        assert not domain.can_transition("galaxy_brain", "discovery", spec)
        assert not domain.can_transition("discovery", "galaxy_brain", spec)


class TestRollups:
    @pytest.mark.parametrize("spec", [DEFAULT_PIPELINE, materialised_default()])
    def test_every_rollup_equals_the_legacy_function_over_a_seeded_book(
        self, spec: PipelineSpec
    ) -> None:
        book = seeded_book()
        assert [d.id for d in domain.open_deals(book, spec)] == [
            d.id for d in legacy_open_deals(book)
        ]
        assert domain.pipeline_value_minor(book, spec) == legacy_pipeline_value_minor(book)
        assert domain.weighted_pipeline_minor(book, spec) == legacy_weighted_pipeline_minor(book)
        assert domain.open_count(book, spec) == legacy_open_count(book)
        assert domain.win_rate_pct(book, spec) == legacy_win_rate_pct(book)
        assert domain.won_value_minor(book, spec) == legacy_won_value_minor(book)
        assert domain.stage_funnel(book, spec) == legacy_stage_funnel(book)

    def test_a_call_without_a_spec_reads_the_default_pipeline(self) -> None:
        book = seeded_book(60)
        assert domain.pipeline_value_minor(book) == domain.pipeline_value_minor(
            book, DEFAULT_PIPELINE
        )
        assert domain.stage_funnel(book) == domain.stage_funnel(book, DEFAULT_PIPELINE)

    def test_win_rate_counts_every_won_and_lost_outcome_stage(self) -> None:
        stages = [
            *custom_stages()[:3],
            StageSpec("won", "Won", 3, "won", 100, "success"),
            StageSpec("won_late", "Won late", 4, "won", 100, "success"),
            StageSpec("lost", "Lost", 5, "lost", 0, "danger"),
        ]
        spec = PipelineSpec(stages=tuple(stages))
        book = [
            deal("won", 100),
            deal("won_late", 300),
            deal("lost", 50),
            deal("quote", 700),  # open: counts for neither the rate nor the won value
        ]
        assert domain.win_rate_pct(book, spec) == 67  # 2 of 3 decided
        assert domain.won_value_minor(book, spec) == 400
        assert domain.open_count(book, spec) == 1
        assert domain.pipeline_value_minor(book, spec) == 700

    def test_the_funnel_lists_active_open_stages_in_position_order(self) -> None:
        stages = custom_stages()
        stages[1] = StageSpec("scoping", "Scoping", 1, "open", 30, "info", archived=True)
        spec = PipelineSpec(stages=tuple(stages))
        book = [deal("discovery", 100), deal("quote", 400), deal("scoping", 999), deal("won", 5)]
        funnel = domain.stage_funnel(book, spec)
        assert [row["stage"] for row in funnel] == ["discovery", "quote"]
        assert [row["label"] for row in funnel] == ["Discovery", "Quote"]
        assert [row["pct"] for row in funnel] == [25, 100]
        # The deal sitting in the archived stage is still open: it is in the totals, not a column.
        assert domain.open_count(book, spec) == 3
        assert domain.pipeline_value_minor(book, spec) == 1499

    def test_a_deal_in_an_unknown_stage_counts_as_open_and_is_not_in_the_funnel(self) -> None:
        spec = PipelineSpec(stages=tuple(custom_stages()))
        book = [deal("discovery", 100), deal("galaxy_brain", 900)]
        assert domain.open_count(book, spec) == 2
        assert domain.pipeline_value_minor(book, spec) == 1000
        assert [row["count"] for row in domain.stage_funnel(book, spec)] == [1, 0, 0]

    def test_a_pipeline_with_one_open_stage_has_a_funnel(self) -> None:
        spec = PipelineSpec(
            stages=(
                StageSpec("only", "Only", 0, "open", 50, "info"),
                StageSpec("won", "Won", 1, "won", 100, "success"),
                StageSpec("lost", "Lost", 2, "lost", 0, "danger"),
            )
        )
        assert [row["pct"] for row in domain.stage_funnel([deal("only", 10)], spec)] == [100]
        assert [row["pct"] for row in domain.stage_funnel([], spec)] == [0]
