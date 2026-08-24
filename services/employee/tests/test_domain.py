"""The employment lifecycle state machine, in isolation and exhaustively.

Pure functions, no database — this is where every legal and illegal transition is pinned down
once, so the service-layer tests can assume the machine is correct and only check that it is
wired in.
"""

from __future__ import annotations

import itertools

import pytest

from app.domain import (
    EmploymentStatus,
    IllegalTransition,
    assert_can_transition,
    can_transition,
)

S = EmploymentStatus

LEGAL = {
    (S.PROBATION, S.ACTIVE),
    (S.PROBATION, S.EXITED),
    (S.ACTIVE, S.ON_NOTICE),
    (S.ACTIVE, S.EXITED),
    (S.ON_NOTICE, S.ACTIVE),
    (S.ON_NOTICE, S.EXITED),
}


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL, key=lambda p: (p[0], p[1])))
def test_the_allowed_transitions_are_allowed(current: S, target: S) -> None:
    assert can_transition(current, target)
    assert_can_transition(current, target)  # does not raise


@pytest.mark.parametrize(
    ("current", "target"),
    [pair for pair in itertools.product(S, S) if pair not in LEGAL],
)
def test_every_other_transition_is_refused(current: S, target: S) -> None:
    """Includes the no-op self-transitions and everything out of the terminal `exited` state."""
    assert not can_transition(current, target)
    with pytest.raises(IllegalTransition):
        assert_can_transition(current, target)


def test_exited_is_terminal() -> None:
    for target in S:
        assert not can_transition(S.EXITED, target)


def test_the_illegal_transition_error_names_both_states() -> None:
    with pytest.raises(IllegalTransition, match="'exited'"):
        assert_can_transition(S.EXITED, S.ACTIVE)
