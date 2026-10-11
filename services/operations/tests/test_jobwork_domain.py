"""Pure job-work rules (Inventory plan Phase 6): the state machine, the reconciliation identity,
the e-way threshold, overdue arithmetic and the financial-year series label."""

from __future__ import annotations

from datetime import date

import pytest

from app.jobwork import domain
from app.jobwork.domain import MaterialFigures, reconcile
from app.models import JOB_WORK_STATUSES


class TestTransitions:
    def test_every_status_is_in_the_table_and_every_target_is_a_status(self) -> None:
        assert set(domain.JOB_WORK_TRANSITIONS) == set(JOB_WORK_STATUSES)
        for targets in domain.JOB_WORK_TRANSITIONS.values():
            assert targets <= set(JOB_WORK_STATUSES)

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            ("DRAFT", "APPROVED"),
            ("DRAFT", "CANCELLED"),
            ("APPROVED", "ISSUED"),
            ("APPROVED", "CANCELLED"),
            ("ISSUED", "PARTIALLY_RECEIVED"),
            ("ISSUED", "RECEIVED"),
            ("PARTIALLY_RECEIVED", "RECEIVED"),
            ("RECEIVED", "CLOSED"),
        ],
    )
    def test_the_legal_edges(self, current: str, new: str) -> None:
        assert domain.can_transition(current, new)

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            ("DRAFT", "ISSUED"),
            ("DRAFT", "CLOSED"),
            ("ISSUED", "CANCELLED"),  # stock is at the vendor: only receiving can undo it
            ("PARTIALLY_RECEIVED", "CANCELLED"),
            ("ISSUED", "CLOSED"),  # closing needs RECEIVED
            ("RECEIVED", "ISSUED"),
            ("CLOSED", "DRAFT"),
            ("CANCELLED", "APPROVED"),
            ("PARTIALLY_RECEIVED", "PARTIALLY_RECEIVED"),  # a no-op "move" is not an edge
            ("NOPE", "DRAFT"),
        ],
    )
    def test_the_illegal_ones(self, current: str, new: str) -> None:
        assert not domain.can_transition(current, new)

    def test_terminal_states_have_no_exit(self) -> None:
        assert domain.JOB_WORK_TRANSITIONS["CLOSED"] == frozenset()
        assert domain.JOB_WORK_TRANSITIONS["CANCELLED"] == frozenset()


class TestReconcile:
    def test_a_settled_order_whose_ledger_agrees_balances(self) -> None:
        figures = [
            MaterialFigures("a", sent=82_500, received=75_625, rejected=2_375, shortage=4_500)
        ]
        result = reconcile(figures, {"a": 0})
        assert result.balanced
        assert result.total_outstanding == 0
        assert result.total_received + result.total_rejected + result.total_shortage == 82_500

    def test_outstanding_material_does_not_balance_even_when_the_ledger_agrees(self) -> None:
        figures = [MaterialFigures("a", sent=82_500, received=30_000, rejected=0, shortage=0)]
        result = reconcile(figures, {"a": 52_500})
        assert result.ledger_consistent
        assert not result.documents_settled
        assert not result.balanced
        assert result.total_outstanding == 52_500

    def test_a_ledger_that_disagrees_with_settled_documents_does_not_balance(self) -> None:
        figures = [MaterialFigures("a", sent=10_000, received=10_000, rejected=0, shortage=0)]
        result = reconcile(figures, {"a": 1})
        assert result.documents_settled
        assert not result.ledger_consistent
        assert not result.balanced
        assert result.drift == {"a": (0, 1)}

    def test_ledger_rows_for_an_item_the_order_never_listed_are_drift(self) -> None:
        result = reconcile([], {"ghost": 5_000})
        assert result.drift == {"ghost": (0, 5_000)}
        assert not result.balanced

    def test_two_materials_of_one_item_are_compared_together(self) -> None:
        figures = [
            MaterialFigures("a", sent=10_000, received=4_000, rejected=0, shortage=0),
            MaterialFigures("a", sent=5_000, received=0, rejected=0, shortage=0),
        ]
        assert reconcile(figures, {"a": 11_000}).ledger_consistent
        assert not reconcile(figures, {"a": 10_000}).ledger_consistent

    def test_identity_holds_for_awkward_milli_amounts(self) -> None:
        # 1 milli-unit of every kind: sent = received + rejected + shortage + outstanding.
        m = MaterialFigures("a", sent=3_001, received=1, rejected=1, shortage=1)
        assert m.sent == m.received + m.rejected + m.shortage + m.outstanding
        assert m.outstanding == 2_998


class TestDates:
    @pytest.mark.parametrize(
        ("day", "label"),
        [
            (date(2026, 3, 31), "25-26"),
            (date(2026, 4, 1), "26-27"),
            (date(2026, 12, 31), "26-27"),
            (date(2027, 1, 1), "26-27"),
            (date(2099, 4, 1), "99-00"),
        ],
    )
    def test_financial_year_label(self, day: date, label: str) -> None:
        assert domain.financial_year(day) == label

    def test_overdue_needs_stock_at_the_vendor_and_a_passed_due_date(self) -> None:
        today = date(2026, 10, 5)
        past, future = date(2026, 10, 1), date(2026, 10, 9)
        assert domain.is_overdue("ISSUED", past, today)
        assert domain.is_overdue("PARTIALLY_RECEIVED", past, today)
        assert not domain.is_overdue("ISSUED", today, today)  # due today is not overdue yet
        assert not domain.is_overdue("ISSUED", future, today)
        assert not domain.is_overdue("ISSUED", None, today)
        for status in ("DRAFT", "APPROVED", "RECEIVED", "CLOSED", "CANCELLED"):
            assert not domain.is_overdue(status, past, today)
        assert domain.days_overdue("ISSUED", past, today) == 4
        assert domain.days_overdue("CLOSED", past, today) == 0


class TestEway:
    def test_threshold_is_exclusive_and_inr_only(self) -> None:
        assert not domain.eway_required(50_000_00, "INR")
        assert domain.eway_required(50_000_01, "INR")
        assert not domain.eway_required(10_000_000_00, "USD")

    def test_inter_state_job_work_needs_a_bill_whatever_the_value(self) -> None:
        assert domain.eway_required(1, "INR", inter_state=True)
        assert not domain.eway_required(1, "INR", inter_state=False)
        assert not domain.eway_required(1, "USD", inter_state=True)


class TestIstDates:
    def test_the_financial_year_turns_over_at_ist_midnight(self) -> None:
        from datetime import UTC, datetime

        # 00:30 IST on 1 April is still 31 March in UTC.
        today = domain.ist_today(datetime(2027, 3, 31, 19, 0, tzinfo=UTC))
        assert today == date(2027, 4, 1)
        assert domain.financial_year(today) == "27-28"

    def test_received_on_bounds(self) -> None:
        today, issued = date(2026, 10, 11), date(2026, 10, 1)
        assert domain.received_on_problem(today, today, issued) is None
        assert domain.received_on_problem(date(2026, 10, 12), today, issued) is None  # tolerance
        assert domain.received_on_problem(issued, today, issued) is None
        assert domain.received_on_problem(date(2026, 10, 13), today, issued)
        assert domain.received_on_problem(date(2026, 9, 30), today, issued)
