"""Pure domain logic for the employee service — no I/O.

The one real algorithm here is the employment lifecycle state machine. It lives apart from
`services.py` because it is a rule, not a transaction: it decides whether a transition is
allowed, and nothing else. That makes it exhaustively testable without a database, and it
keeps the "which transitions are legal" question in a single place a reviewer can read at a
glance rather than scattered across the write path.
"""

from __future__ import annotations

import enum


class EmploymentStatus(enum.StrEnum):
    """The states an employment record moves through.

    `probation` is where a new hire starts, `exited` is terminal. `on_notice` is the
    serving-notice period between a resignation and the last working day.
    """

    PROBATION = "probation"
    ACTIVE = "active"
    ON_NOTICE = "on_notice"
    EXITED = "exited"


#: The only transitions the business allows. A probationer is confirmed into `active` or lets
#: their probation end in `exited`; an active employee resigns onto notice or exits directly;
#: someone on notice either withdraws it (`active`) or reaches their last day (`exited`).
#: `exited` is terminal — rehiring is a new record, not a resurrection, so history stays clean.
ALLOWED_TRANSITIONS: dict[EmploymentStatus, frozenset[EmploymentStatus]] = {
    EmploymentStatus.PROBATION: frozenset({EmploymentStatus.ACTIVE, EmploymentStatus.EXITED}),
    EmploymentStatus.ACTIVE: frozenset({EmploymentStatus.ON_NOTICE, EmploymentStatus.EXITED}),
    EmploymentStatus.ON_NOTICE: frozenset({EmploymentStatus.ACTIVE, EmploymentStatus.EXITED}),
    EmploymentStatus.EXITED: frozenset(),
}


def can_transition(current: EmploymentStatus, target: EmploymentStatus) -> bool:
    """Whether moving from `current` to `target` is a legal lifecycle step."""
    return target in ALLOWED_TRANSITIONS[current]


class IllegalTransition(ValueError):
    """A transition the lifecycle does not permit.

    Carries the offending pair so the service layer can turn it into a business-rule problem
    with a message that names both states.
    """

    def __init__(self, current: EmploymentStatus, target: EmploymentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"An employee cannot move from {current.value!r} to {target.value!r}")


def assert_can_transition(current: EmploymentStatus, target: EmploymentStatus) -> None:
    """Raise `IllegalTransition` unless the step is allowed. The single validation point."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
