"""Pure domain logic for the attendance service — no I/O.

Two rules live here, both pure so they are exhaustively testable without a database and read
in one place rather than scattered across the write path:

* the **attendance status machine** — which manual corrections are legal — mirroring the
  employee service's lifecycle machine; and
* the **check-out ordering** rule — a check-out may not precede its check-in.

The geo-fence test is deliberately *not* here: it is a PostGIS query in `services.check_in`, not
app-side maths (CLAUDE.md, plan A.4). The anti-spoofing rules further down are a different
question — "could one person have made these two punches" — with no fence to ask the database
about, so they are plain functions over the numbers the punches carry.
"""

from __future__ import annotations

import enum
import uuid
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


# --------------------------------------------------------------------- the reporting line (TEAM)

#: `employee.employees.status`'s closed vocabulary, copied onto the TEAM projection.
TEAM_STATUSES: frozenset[str] = frozenset({"probation", "active", "on_notice", "exited"})

#: A person who has left is not a current report, so the TEAM read paths exclude this status.
STATUS_EXITED = "exited"


def normalise_manager(employee_id: uuid.UUID, manager_id: uuid.UUID | None) -> uuid.UUID | None:
    """The manager to store for `employee_id`: `manager_id`, unless it points back at the person.

    Employee-service refuses "reports to themselves" on write, so this never fires on a healthy
    stream. It exists because the TEAM projection sits under an authorization check: a self-loop
    would put a person in their own team, and the projection's own CHECK would turn a corrupt event
    into a dead-lettered message that is never applied. Treating it as "no manager" fails closed —
    the person is in nobody's team — and the caller logs it.
    """
    return None if manager_id == employee_id else manager_id
