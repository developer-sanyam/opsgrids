"""Pure domain logic — the lifecycle state machine and the overview rollups. No database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import domain
from app.domain import IllegalTransition, TicketStatus


def _facts(**kw) -> domain.TicketFacts:
    base: dict = {
        "status": "open",
        "priority": "normal",
        "created_at": datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        "first_response_at": None,
        "sla_breached": False,
        "satisfaction": None,
    }
    base.update(kw)
    return domain.TicketFacts(**base)


class TestLifecycle:
    def test_new_can_open(self) -> None:
        assert domain.can_transition(TicketStatus.NEW, TicketStatus.OPEN)

    def test_solved_can_reopen(self) -> None:
        assert domain.can_transition(TicketStatus.SOLVED, TicketStatus.OPEN)

    def test_solved_cannot_go_pending(self) -> None:
        assert not domain.can_transition(TicketStatus.SOLVED, TicketStatus.PENDING)

    def test_assert_raises_on_illegal_step(self) -> None:
        with pytest.raises(IllegalTransition) as exc:
            domain.assert_can_transition(TicketStatus.SOLVED, TicketStatus.PENDING)
        assert exc.value.current is TicketStatus.SOLVED
        assert exc.value.target is TicketStatus.PENDING

    def test_is_active_is_everything_but_solved(self) -> None:
        assert domain.is_active("open")
        assert domain.is_active("on_hold")
        assert not domain.is_active("solved")


class TestRollups:
    def test_open_and_breaching_counts(self) -> None:
        tickets = [
            _facts(status="open", sla_breached=True),
            _facts(status="open", sla_breached=False),
            _facts(status="solved", sla_breached=True),  # solved, not counted as breaching
        ]
        assert domain.open_count(tickets) == 2
        assert domain.breaching_count(tickets) == 1

    def test_first_response_median(self) -> None:
        c = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
        tickets = [
            _facts(created_at=c, first_response_at=datetime(2026, 8, 24, 10, 10, tzinfo=UTC)),
            _facts(created_at=c, first_response_at=datetime(2026, 8, 24, 10, 30, tzinfo=UTC)),
            _facts(created_at=c, first_response_at=None),  # no reply — excluded
        ]
        assert domain.first_response_median_min(tickets) == 20

    def test_csat_none_until_rated_then_percentage(self) -> None:
        assert domain.csat_pct([_facts(satisfaction=None)]) is None
        rated = [
            _facts(status="solved", satisfaction="good"),
            _facts(status="solved", satisfaction="good"),
            _facts(status="solved", satisfaction="bad"),
        ]
        assert domain.csat_pct(rated) == 67

    def test_priority_breakdown_is_over_active_only_in_order(self) -> None:
        tickets = [
            _facts(status="open", priority="high"),
            _facts(status="open", priority="urgent"),
            _facts(status="solved", priority="low"),  # excluded — not active
        ]
        slices = domain.priority_breakdown(tickets)
        assert [s.key for s in slices] == ["urgent", "high"]
        assert all(s.count == 1 for s in slices)
        assert {s.pct for s in slices} == {50}

    def test_status_breakdown_counts_all(self) -> None:
        tickets = [_facts(status="open"), _facts(status="open"), _facts(status="solved")]
        slices = {s.key: s.count for s in domain.status_breakdown(tickets)}
        assert slices == {"open": 2, "solved": 1}

    def test_volume_series_has_seven_days_and_counts_opens(self) -> None:
        now = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
        tickets = [
            _facts(created_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC)),
            _facts(
                status="solved",
                created_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
                updated_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
            ),
        ]
        series = domain.volume_series(tickets, now)
        assert len(series) == 7
        assert series[-1].date == "2026-08-24"
        assert series[-1].opened == 1
        assert series[-1].solved == 1
