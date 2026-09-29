"""`app.domain.COUNT_SESSION_TRANSITIONS`/`can_transition_count_session` (ADR-0026 §6) — pure,
no I/O, same shape `test_fulfilment_domain.py` uses for `can_transition_pick_task`.
"""

from __future__ import annotations

from app import domain


def test_active_moves_to_review_or_cancelled() -> None:
    assert domain.can_transition_count_session("ACTIVE", "REVIEW")
    assert domain.can_transition_count_session("ACTIVE", "CANCELLED")
    assert not domain.can_transition_count_session("ACTIVE", "CLOSED")
    assert not domain.can_transition_count_session("ACTIVE", "ACTIVE")


def test_review_moves_to_closed_or_cancelled() -> None:
    assert domain.can_transition_count_session("REVIEW", "CLOSED")
    assert domain.can_transition_count_session("REVIEW", "CANCELLED")
    assert not domain.can_transition_count_session("REVIEW", "ACTIVE")


def test_closed_and_cancelled_are_terminal() -> None:
    for status in ("CLOSED", "CANCELLED"):
        for target in ("ACTIVE", "REVIEW", "CLOSED", "CANCELLED"):
            assert not domain.can_transition_count_session(status, target)


def test_unknown_status_has_no_edges() -> None:
    assert not domain.can_transition_count_session("BOGUS", "ACTIVE")
