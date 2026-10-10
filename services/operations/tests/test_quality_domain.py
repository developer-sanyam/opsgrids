"""The pure quality rules: the inspection state machine, judging a reading, release gating and the
accepted/rejected split. No database."""

from __future__ import annotations

import itertools

import pytest

from app.models import INSPECTION_STATUSES
from app.quality import domain
from app.quality.domain import (
    INSPECTION_TRANSITIONS,
    Judged,
    ParamSpec,
    can_release,
    can_transition_inspection,
    evaluate_param,
    split_quantity,
    suggested_outcome,
)

ALLOWED = {
    ("PENDING", "PASSED"),
    ("PENDING", "FAILED"),
    ("PENDING", "HOLD"),
    ("PENDING", "REWORK"),
    ("PENDING", "PARTIAL_PASS"),
    ("HOLD", "PASSED"),
    ("HOLD", "FAILED"),
    ("HOLD", "REWORK"),
    ("HOLD", "PARTIAL_PASS"),
    ("REWORK", "PENDING"),
    ("REWORK", "FAILED"),
}


def test_the_table_covers_exactly_the_model_statuses() -> None:
    assert set(INSPECTION_TRANSITIONS) == set(INSPECTION_STATUSES)
    assert all(t <= set(INSPECTION_STATUSES) for t in INSPECTION_TRANSITIONS.values())


@pytest.mark.parametrize(
    ("current", "new"), list(itertools.product(INSPECTION_STATUSES, INSPECTION_STATUSES))
)
def test_every_pair_is_allowed_or_refused_exactly_as_documented(current: str, new: str) -> None:
    assert can_transition_inspection(current, new) is ((current, new) in ALLOWED)


def test_final_states_have_no_exit_and_unknown_states_refuse() -> None:
    assert {"PASSED", "FAILED", "PARTIAL_PASS"} == domain.FINAL_STATUSES
    assert not can_transition_inspection("PASSED", "PENDING")
    assert not can_transition_inspection("NOPE", "PASSED")


class TestEvaluate:
    spec = ParamSpec("numeric", 170_000, 190_000)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(170_000, True), (190_000, True), (180_000, True), (169_999, False), (190_001, False)],
    )
    def test_bounds_are_inclusive(self, value: int, expected: bool) -> None:
        assert evaluate_param(self.spec, value_milli=value, passed=None) is expected

    def test_an_open_side_is_unbounded(self) -> None:
        only_max = ParamSpec("numeric", None, 3_500)
        assert evaluate_param(only_max, value_milli=-5_000, passed=None)
        assert not evaluate_param(only_max, value_milli=3_501, passed=None)

    def test_a_numeric_verdict_from_the_client_is_ignored(self) -> None:
        assert not evaluate_param(self.spec, value_milli=0, passed=True)

    def test_pass_fail_takes_the_verdict_and_numeric_needs_a_value(self) -> None:
        assert evaluate_param(ParamSpec("pass_fail"), value_milli=None, passed=True)
        assert not evaluate_param(ParamSpec("pass_fail"), value_milli=None, passed=False)
        with pytest.raises(ValueError):
            evaluate_param(ParamSpec("pass_fail"), value_milli=None, passed=None)
        with pytest.raises(ValueError):
            evaluate_param(self.spec, value_milli=None, passed=True)


class TestRelease:
    def test_a_failed_critical_parameter_blocks_release(self) -> None:
        assert not can_release([Judged(True, False), Judged(False, True)])
        assert can_release([Judged(True, True), Judged(False, False)])
        assert can_release([])

    def test_suggested_outcome(self) -> None:
        assert suggested_outcome([]) == "PENDING"
        assert suggested_outcome([Judged(True, True)]) == "PASSED"
        assert suggested_outcome([Judged(True, True), Judged(False, False)]) == "HOLD"
        assert suggested_outcome([Judged(False, True), Judged(False, False)]) == "FAILED"


class TestSplit:
    def test_nothing_or_all_accepted_is_a_pass(self) -> None:
        assert split_quantity(40_000, None) == (40_000, 0, "PASSED")
        assert split_quantity(40_000, 40_000) == (40_000, 0, "PASSED")

    def test_a_part_is_a_partial_pass(self) -> None:
        assert split_quantity(40_000, 25_500) == (25_500, 14_500, "PARTIAL_PASS")
        assert split_quantity(1, 0 + 1) == (1, 0, "PASSED")

    @pytest.mark.parametrize("accepted", [0, -1, 40_001])
    def test_impossible_splits_are_refused_not_clamped(self, accepted: int) -> None:
        with pytest.raises(ValueError):
            split_quantity(40_000, accepted)
