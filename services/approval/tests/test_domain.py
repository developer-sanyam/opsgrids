"""Pure routing logic (`app/domain.py`), slice 2's additions: the half-open delegation-activity
window (ADR-0006 §5) and the `inbox_reason` derivation (§6, screens §2.3). No database — these
are exactly the functions `domain.py` exists for, and the boundary conditions are cheapest to
pin here rather than through a full HTTP or service-layer round trip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app import domain

# No `integration` marker: these are plain functions over `domain.py`, no database needed.


def test_delegation_is_active_inside_its_half_open_window() -> None:
    now = datetime.now(UTC)
    assert domain.delegation_is_active(
        revoked_at=None,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        at=now,
    )


def test_delegation_is_active_at_the_exact_start_instant() -> None:
    now = datetime.now(UTC)
    assert domain.delegation_is_active(
        revoked_at=None, starts_at=now, ends_at=now + timedelta(hours=1), at=now
    )


def test_delegation_is_not_active_at_the_exact_end_instant() -> None:
    """Half-open `[starts_at, ends_at)` — a one-day delegation must not overlap another one
    starting the moment it ends (ADR-0006 §5, data model §1.5)."""
    now = datetime.now(UTC)
    assert not domain.delegation_is_active(
        revoked_at=None, starts_at=now - timedelta(hours=1), ends_at=now, at=now
    )


def test_delegation_is_not_active_before_it_starts() -> None:
    now = datetime.now(UTC)
    assert not domain.delegation_is_active(
        revoked_at=None,
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=2),
        at=now,
    )


def test_delegation_is_not_active_once_revoked_even_inside_the_window() -> None:
    now = datetime.now(UTC)
    assert not domain.delegation_is_active(
        revoked_at=now - timedelta(minutes=1),
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
        at=now,
    )


class TestDelegationStatus:
    """`domain.delegation_status` (slice 3, ADR-0006 §5, screens §4.E's row tags), which must
    agree with `delegation_is_active` at every boundary — it is derived from the same three
    instants."""

    def test_scheduled_before_it_starts(self) -> None:
        now = datetime.now(UTC)
        assert (
            domain.delegation_status(
                revoked_at=None,
                starts_at=now + timedelta(hours=1),
                ends_at=now + timedelta(hours=2),
                at=now,
            )
            == domain.DELEGATION_STATUS_SCHEDULED
        )

    def test_active_at_the_exact_start_instant(self) -> None:
        now = datetime.now(UTC)
        assert (
            domain.delegation_status(
                revoked_at=None, starts_at=now, ends_at=now + timedelta(hours=1), at=now
            )
            == domain.DELEGATION_STATUS_ACTIVE
        )

    def test_ended_at_the_exact_end_instant(self) -> None:
        """Half-open `[starts_at, ends_at)`, the same boundary `delegation_is_active` uses — a
        delegation reads `ended`, not `active`, at the instant it ends."""
        now = datetime.now(UTC)
        assert (
            domain.delegation_status(
                revoked_at=None, starts_at=now - timedelta(hours=1), ends_at=now, at=now
            )
            == domain.DELEGATION_STATUS_ENDED
        )

    def test_revoked_takes_priority_over_a_window_that_is_still_open(self) -> None:
        """A delegation revoked mid-window reads `revoked`, never `active` — eligibility ends
        immediately on revoke (ADR-0006 §5), and the tag must not keep claiming otherwise until
        the original window would have lapsed on its own."""
        now = datetime.now(UTC)
        assert (
            domain.delegation_status(
                revoked_at=now - timedelta(minutes=1),
                starts_at=now - timedelta(hours=1),
                ends_at=now + timedelta(hours=1),
                at=now,
            )
            == domain.DELEGATION_STATUS_REVOKED
        )

    def test_revoked_takes_priority_even_before_the_window_started(self) -> None:
        now = datetime.now(UTC)
        assert (
            domain.delegation_status(
                revoked_at=now,
                starts_at=now + timedelta(hours=1),
                ends_at=now + timedelta(hours=2),
                at=now,
            )
            == domain.DELEGATION_STATUS_REVOKED
        )


class TestInboxReasonFor:
    def test_the_direct_approver_sees_assigned(self) -> None:
        caller = uuid.uuid4()
        reason = domain.inbox_reason_for(
            caller_id=caller,
            approver_employee_id=caller,
            acting_for_employee_id=None,
            step_reason="assigned",
        )
        assert reason.reason == domain.INBOX_REASON_ASSIGNED
        assert reason.from_employee_id is None

    def test_the_delegate_sees_delegated_to_me_with_the_original_as_from(self) -> None:
        caller, original = uuid.uuid4(), uuid.uuid4()
        reason = domain.inbox_reason_for(
            caller_id=caller,
            approver_employee_id=caller,
            acting_for_employee_id=original,
            step_reason="delegated",
        )
        assert reason.reason == domain.INBOX_REASON_DELEGATED_TO_ME
        assert reason.from_employee_id == original

    def test_the_original_sees_delegated_away_with_the_delegate_as_from(self) -> None:
        original, delegate = uuid.uuid4(), uuid.uuid4()
        reason = domain.inbox_reason_for(
            caller_id=original,
            approver_employee_id=delegate,
            acting_for_employee_id=original,
            step_reason="delegated",
        )
        assert reason.reason == domain.INBOX_REASON_DELEGATED_AWAY
        assert reason.from_employee_id == delegate

    def test_an_unassigned_step_is_unassigned_for_anyone(self) -> None:
        reason = domain.inbox_reason_for(
            caller_id=uuid.uuid4(),
            approver_employee_id=None,
            acting_for_employee_id=None,
            step_reason="unassigned",
        )
        assert reason.reason == domain.INBOX_REASON_UNASSIGNED
        assert reason.from_employee_id is None

    def test_a_bystander_who_matches_neither_route_falls_back_to_unassigned(self) -> None:
        """Defensive only: `list_inbox`'s own query never returns a row for a caller who matches
        none of the routes, so this branch is unreachable in production but must still resolve
        to something rather than raising."""
        reason = domain.inbox_reason_for(
            caller_id=uuid.uuid4(),
            approver_employee_id=uuid.uuid4(),
            acting_for_employee_id=None,
            step_reason="assigned",
        )
        assert reason.reason == domain.INBOX_REASON_UNASSIGNED
