"""The attendance status machine and the check-out ordering rule, in isolation.

Pure functions, no database — this is where every legal and illegal status correction is pinned
down once, and where the check-out-after-check-in rule is proven, so the service-layer tests can
assume the machine is correct and only check that it is wired in.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.domain import (
    AttendanceStatus,
    CheckOutBeforeCheckIn,
    IllegalTransition,
    assert_can_transition,
    assert_checkout_after_checkin,
    can_transition,
    checkout_after_checkin,
)

S = AttendanceStatus

LEGAL = {
    (S.PRESENT, S.HALF_DAY),
    (S.PRESENT, S.ABSENT),
    (S.PRESENT, S.REGULARIZED),
    (S.ABSENT, S.PRESENT),
    (S.ABSENT, S.HALF_DAY),
    (S.ABSENT, S.REGULARIZED),
    (S.HALF_DAY, S.PRESENT),
    (S.HALF_DAY, S.ABSENT),
    (S.HALF_DAY, S.REGULARIZED),
}


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL, key=lambda p: (p[0], p[1])))
def test_the_allowed_corrections_are_allowed(current: S, target: S) -> None:
    assert can_transition(current, target)
    assert_can_transition(current, target)  # does not raise


@pytest.mark.parametrize(
    ("current", "target"),
    [pair for pair in itertools.product(S, S) if pair not in LEGAL],
)
def test_every_other_correction_is_refused(current: S, target: S) -> None:
    """Includes the no-op self-corrections and everything out of the terminal `regularized`."""
    assert not can_transition(current, target)
    with pytest.raises(IllegalTransition):
        assert_can_transition(current, target)


def test_regularized_is_terminal() -> None:
    for target in S:
        assert not can_transition(S.REGULARIZED, target)


def test_the_illegal_transition_error_names_both_states() -> None:
    with pytest.raises(IllegalTransition, match="'regularized'"):
        assert_can_transition(S.REGULARIZED, S.PRESENT)


class TestCheckOutOrdering:
    def test_a_later_check_out_is_allowed(self) -> None:
        check_in = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
        assert checkout_after_checkin(check_in, check_in + timedelta(hours=8))
        assert_checkout_after_checkin(check_in, check_in + timedelta(hours=8))  # no raise

    def test_a_check_out_at_or_before_the_check_in_is_refused(self) -> None:
        check_in = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
        assert not checkout_after_checkin(check_in, check_in)
        assert not checkout_after_checkin(check_in, check_in - timedelta(minutes=1))
        with pytest.raises(CheckOutBeforeCheckIn):
            assert_checkout_after_checkin(check_in, check_in - timedelta(minutes=1))


def test_the_work_day_rolls_over_at_local_midnight_not_utc_midnight() -> None:
    """00:30 IST on the 16th is 19:00 UTC on the 15th: it is already the 16th for the org."""
    from datetime import date

    from app.domain import work_date_in

    at = datetime(2026, 10, 15, 19, 0, tzinfo=UTC)
    assert work_date_in(at, "Asia/Kolkata") == date(2026, 10, 16)
    assert work_date_in(at, "UTC") == date(2026, 10, 15)
