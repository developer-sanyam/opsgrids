"""`app.domain.ORDER_TRANSITIONS` / `can_transition_order` — pure, no database (ADR-0025 §5:
"testable without a database"). Every edge in the ADR-0025 §1 table, and every non-edge answers
409's own predicate (`False` here — the route layer is what turns it into a 409, proven in
`test_orders_write.py`), the same convention `can_transition_po` already documents.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import domain

#: The exact content of ADR-0025 §1's transition table.
EDGES: list[tuple[str, str]] = [
    ("DRAFT", "PLACED"),
    ("DRAFT", "CANCELLED"),
    ("PLACED", "ACCEPTED"),
    ("PLACED", "REJECTED"),
    ("PLACED", "CANCELLED"),
    ("ACCEPTED", "IN_FULFILMENT"),
    ("ACCEPTED", "ON_HOLD"),
    ("ACCEPTED", "CANCELLED"),
    ("IN_FULFILMENT", "READY"),
    ("IN_FULFILMENT", "ON_HOLD"),
    ("IN_FULFILMENT", "CANCELLED"),
    ("ON_HOLD", "ACCEPTED"),
    ("ON_HOLD", "IN_FULFILMENT"),
    ("ON_HOLD", "READY"),
    ("ON_HOLD", "CANCELLED"),
    ("READY", "HANDED_OVER"),
    ("READY", "ON_HOLD"),
    ("READY", "CANCELLED"),
    ("HANDED_OVER", "DELIVERED"),
    ("DELIVERED", "COMPLETED"),
]

TERMINAL = ("COMPLETED", "CANCELLED", "REJECTED")


class TestEveryAdrEdgeIsReachable:
    @pytest.mark.parametrize(("current", "new"), EDGES)
    def test_edge(self, current: str, new: str) -> None:
        assert domain.can_transition_order(current, new) is True

    def test_every_status_is_covered_by_the_map(self) -> None:
        assert set(domain.ORDER_TRANSITIONS) == set(domain.ORDER_STATUSES)

    def test_no_edge_is_missing_from_the_fixture(self) -> None:
        """The fixture above and the map agree exactly — a change to one without the other is a
        red test, not a silent gap."""
        from_map = {(frm, to) for frm, tos in domain.ORDER_TRANSITIONS.items() for to in tos}
        assert from_map == set(EDGES)


class TestTerminalStatesHaveNoOutgoingEdge:
    @pytest.mark.parametrize("status", TERMINAL)
    def test_terminal(self, status: str) -> None:
        assert domain.ORDER_TRANSITIONS[status] == frozenset()
        assert status in domain.ORDER_TERMINAL_STATUSES


class TestNonEdgesAreRefused:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            ("CANCELLED", "ACCEPTED"),  # the exit criteria's own example
            ("CANCELLED", "PLACED"),
            ("REJECTED", "PLACED"),
            ("COMPLETED", "CANCELLED"),
            ("HANDED_OVER", "CANCELLED"),  # goods have left; cancellation is refused past here
            ("DELIVERED", "CANCELLED"),
            ("DRAFT", "ACCEPTED"),  # skips PLACED
            ("DRAFT", "ON_HOLD"),
            ("PLACED", "ON_HOLD"),  # only ACCEPTED+ can be held
            ("PLACED", "IN_FULFILMENT"),
            ("ACCEPTED", "READY"),  # skips IN_FULFILMENT
            ("ACCEPTED", "HANDED_OVER"),
        ],
    )
    def test_non_edge(self, current: str, new: str) -> None:
        assert domain.can_transition_order(current, new) is False

    @pytest.mark.parametrize("status", domain.ORDER_STATUSES)
    def test_a_no_op_move_to_the_same_status_is_always_refused(self, status: str) -> None:
        assert domain.can_transition_order(status, status) is False

    def test_an_unknown_current_status_has_no_edges(self) -> None:
        assert domain.can_transition_order("NOT_A_STATUS", "DRAFT") is False


class TestOrderReleaseTarget:
    def test_no_tasks_goes_to_accepted(self) -> None:
        assert (
            domain.order_release_target(has_open_tasks=False, all_tasks_picked=False) == "ACCEPTED"
        )

    def test_open_tasks_go_to_in_fulfilment(self) -> None:
        assert (
            domain.order_release_target(has_open_tasks=True, all_tasks_picked=False)
            == "IN_FULFILMENT"
        )

    def test_all_picked_goes_to_ready(self) -> None:
        assert domain.order_release_target(has_open_tasks=False, all_tasks_picked=True) == "READY"

    def test_open_tasks_win_over_all_picked(self) -> None:
        """Can't really happen together, but open beats picked if it ever does — a task still
        open is never safely called "ready"."""
        assert (
            domain.order_release_target(has_open_tasks=True, all_tasks_picked=True)
            == "IN_FULFILMENT"
        )


class TestOrderLineAmount:
    def test_exact_multiplication(self) -> None:
        # 2.5 pcs-equivalent milli * 500_00 minor / 1000 = 1250_00
        assert domain.order_line_amount_minor(2_500, 500_00) == 1_250_00

    def test_half_to_even_rounding(self) -> None:
        # 1.5 milli-scaled remainder rounds to even (banker's rounding), matching
        # `quantity.value_minor`'s own contract.
        assert domain.order_line_amount_minor(3, 500) == round(3 * 500 / 1000)

    def test_subtotal_sums_priced_lines_and_skips_unpriced(self) -> None:
        class Line:
            def __init__(self, total: int | None) -> None:
                self.line_total_minor = total

        lines = [Line(100), Line(None), Line(250)]
        assert domain.order_subtotal_minor(lines) == 350


class TestExpectedGstAmount:
    """`domain.expected_gst_amount_minor` — deliberately mirrors
    `services/finance/app/domain.py::gst_amount_minor` (same formula, same `Decimal`, same
    `ROUND_HALF_UP`), which is what lets `request_invoice_for_order`'s `expected_tax_minor` agree
    with finance's own independent recomputation (Fulfilment plan F4)."""

    def test_a_flat_percentage(self) -> None:
        assert domain.expected_gst_amount_minor(100_00, Decimal("18")) == 18_00

    def test_zero_rate_is_zero_tax(self) -> None:
        assert domain.expected_gst_amount_minor(999_99, Decimal("0")) == 0

    def test_rounds_half_up_not_half_to_even(self) -> None:
        # 0.125 * 100 = 12.5 -> 13 (half-up, away from zero), not 12 (banker's rounding, which
        # `order_line_amount_minor` uses for a *different* calculation — the two must not be
        # confused: this one mirrors finance's GST rule, not the stock-value rule).
        assert domain.expected_gst_amount_minor(1000, Decimal("1.25")) == 13

    def test_agrees_with_a_hand_computed_odd_rate(self) -> None:
        # 12.5% of 333_33 (33333) = 4166.625 -> 4167 (half-up).
        assert domain.expected_gst_amount_minor(333_33, Decimal("12.5")) == 4_167
