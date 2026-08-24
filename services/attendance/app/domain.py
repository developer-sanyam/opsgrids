"""Pure domain logic for the attendance service — no I/O.

Two rules live here, both pure so they are exhaustively testable without a database and read
in one place rather than scattered across the write path:

* the **attendance status machine** — which manual corrections are legal — mirroring the
  employee service's lifecycle machine; and
* the **check-out ordering** rule — a check-out may not precede its check-in.

Everything geographic is deliberately *not* here: the fence test is a PostGIS query in
`services.check_in`, not app-side haversine maths (CLAUDE.md, plan A.4).
"""

from __future__ import annotations

import enum
from datetime import datetime


class AttendanceStatus(enum.StrEnum):
    """The states a day's attendance record can hold.

    A punch opens the day as `present`. `absent` and `half_day` are the other observed states;
    `regularized` is the corrected end-state an authorised caller moves a record to, and — like
    the employee lifecycle's `exited` — it is terminal, so a correction of a correction is a
    fresh, audited act rather than a silent overwrite.
    """

    PRESENT = "present"
    HALF_DAY = "half_day"
    ABSENT = "absent"
    REGULARIZED = "regularized"


#: The only status corrections the business allows. A `present` day may be cut to `half_day` or
#: signed off as `regularized`; an `absent` day may be corrected to any observed state or
#: regularized; a `half_day` may be restored, marked absent, or regularized. `regularized` is
#: terminal — re-opening a signed-off day is a new record, not a resurrection, so history stays
#: clean. No state may transition to itself: a no-op correction is a mistake, not a change.
ALLOWED_TRANSITIONS: dict[AttendanceStatus, frozenset[AttendanceStatus]] = {
    AttendanceStatus.PRESENT: frozenset(
        {AttendanceStatus.HALF_DAY, AttendanceStatus.ABSENT, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.ABSENT: frozenset(
        {AttendanceStatus.PRESENT, AttendanceStatus.HALF_DAY, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.HALF_DAY: frozenset(
        {AttendanceStatus.PRESENT, AttendanceStatus.ABSENT, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.REGULARIZED: frozenset(),
}


def can_transition(current: AttendanceStatus, target: AttendanceStatus) -> bool:
    """Whether moving from `current` to `target` is a legal status correction."""
    return target in ALLOWED_TRANSITIONS[current]


class IllegalTransition(ValueError):
    """A status correction the machine does not permit.

    Carries the offending pair so the service layer can turn it into a business-rule problem
    with a message that names both states.
    """

    def __init__(self, current: AttendanceStatus, target: AttendanceStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"An attendance record cannot move from {current.value!r} to {target.value!r}"
        )


def assert_can_transition(current: AttendanceStatus, target: AttendanceStatus) -> None:
    """Raise `IllegalTransition` unless the correction is allowed. The single validation point."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


class CheckOutBeforeCheckIn(ValueError):
    """A check-out timestamped at or before its check-in — a day that ended before it began."""

    def __init__(self, check_in_at: datetime, check_out_at: datetime) -> None:
        self.check_in_at = check_in_at
        self.check_out_at = check_out_at
        super().__init__("A check-out cannot be at or before the check-in")


def checkout_after_checkin(check_in_at: datetime, check_out_at: datetime) -> bool:
    """Whether `check_out_at` is strictly later than `check_in_at`."""
    return check_out_at > check_in_at


def assert_checkout_after_checkin(check_in_at: datetime, check_out_at: datetime) -> None:
    """Raise `CheckOutBeforeCheckIn` unless the check-out strictly follows the check-in."""
    if not checkout_after_checkin(check_in_at, check_out_at):
        raise CheckOutBeforeCheckIn(check_in_at, check_out_at)
