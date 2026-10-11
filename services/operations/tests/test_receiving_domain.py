"""Receiving rules as pure functions (inventory Phase 2): tolerance, exact unit conversion, the
requisition and purchase-order state maps. No database — these are the arithmetic and the
transition tables the posting service only applies."""

from __future__ import annotations

from fractions import Fraction
from itertools import pairwise
from typing import ClassVar

import pytest

from app import domain as po_domain
from app.inventory.quantity import QuantityError
from app.receiving import domain as d

q = d.quantity.parse_qty  # "82.5" -> 82_500


class TestTolerance:
    @pytest.mark.parametrize(
        ("text", "bp"),
        [("0", 0), ("5", 500), ("2.5", 250), ("0.25", 25), ("100", 10_000), ("12.05", 1_205)],
    )
    def test_percentages_parse_to_exact_basis_points(self, text: str, bp: int) -> None:
        assert d.parse_tolerance_pct(text) == bp
        assert d.parse_tolerance_pct(d.format_tolerance_pct(bp)) == bp

    @pytest.mark.parametrize("bad", ["", "-1", "100.01", "101", "1.234", "abc", "1e2", "5%"])
    def test_a_bad_percentage_is_refused_not_rounded(self, bad: str) -> None:
        with pytest.raises(ValueError):
            d.parse_tolerance_pct(bad)

    @pytest.mark.parametrize(
        ("bp", "text"), [(0, "0"), (500, "5"), (250, "2.5"), (25, "0.25"), (1_205, "12.05")]
    )
    def test_format_has_no_trailing_zeros(self, bp: int, text: str) -> None:
        assert d.format_tolerance_pct(bp) == text

    def test_the_limit_is_ordered_plus_tolerance_exactly(self) -> None:
        # 5 % of 82.5 m is 4.125 m: 86.625 m in all, no rounding anywhere.
        assert d.tolerance_limit_milli(q("82.5"), 500) == q("86.625")
        assert d.tolerance_limit_milli(q("100"), 0) == q("100")
        assert d.tolerance_limit_milli(q("100"), 10_000) == q("200")

    def test_the_limit_floors_so_a_tolerance_never_allows_a_milli_more_than_it_says(self) -> None:
        # 1 % of 0.333 is 0.00333 -> three milli-units short of a milli: floor to zero.
        assert d.tolerance_limit_milli(333, 100) == 333 + 3
        # 0.5 % of 0.199: 0.995 milli -> floors to 0, never rounds up to 1.
        assert d.tolerance_limit_milli(199, 50) == 199
        for ordered in (1, 7, 199, 82_500, 123_457):
            for bp in (1, 25, 250, 500, 9_999):
                limit = d.tolerance_limit_milli(ordered, bp)
                assert limit * d.BP_SCALE <= ordered * (d.BP_SCALE + bp)  # never over
                assert (limit + 1) * d.BP_SCALE > ordered * (d.BP_SCALE + bp)  # and not under


class TestOutcome:
    @pytest.mark.parametrize(
        ("ordered", "cumulative", "close_short", "outcome"),
        [
            (100_000, 100_000, False, "EXACT"),
            (100_000, 99_999, False, "PARTIAL"),
            (100_000, 40_000, True, "SHORT"),
            (100_000, 102_000, False, "OVER"),
            (100_000, 100_000, True, "EXACT"),
        ],
    )
    def test_classify(self, ordered: int, cumulative: int, close_short: bool, outcome: str) -> None:
        assert (
            d.classify_outcome(
                ordered_milli=ordered, cumulative_milli=cumulative, close_short=close_short
            )
            == outcome
        )

    def test_a_line_is_satisfied_when_received_or_closed_short(self) -> None:
        assert d.line_is_satisfied(ordered_milli=10, received_milli=10, short_closed=False)
        assert d.line_is_satisfied(ordered_milli=10, received_milli=11, short_closed=False)
        assert d.line_is_satisfied(ordered_milli=10, received_milli=3, short_closed=True)
        assert not d.line_is_satisfied(ordered_milli=10, received_milli=9, short_closed=False)


class TestPurchaseOrderStatus:
    def line(self, ordered: int, received: int, closed: bool = False) -> d.LineState:
        return d.LineState(ordered_milli=ordered, received_milli=received, short_closed=closed)

    def test_nothing_received_keeps_the_status(self) -> None:
        assert d.po_status_after("approved", [self.line(10, 0), self.line(5, 0)]) == "approved"

    def test_some_received_is_partial(self) -> None:
        assert d.po_status_after("approved", [self.line(10, 4), self.line(5, 0)]) == (
            "partially_received"
        )

    def test_every_line_satisfied_is_received(self) -> None:
        assert d.po_status_after("approved", [self.line(10, 10), self.line(5, 6)]) == "received"
        assert d.po_status_after(
            "partially_received", [self.line(10, 10), self.line(5, 2, True)]
        ) == ("received")

    def test_closing_a_line_short_with_another_open_is_partial(self) -> None:
        assert d.po_status_after("approved", [self.line(10, 0, True), self.line(5, 0)]) == (
            "partially_received"
        )

    def test_every_status_the_function_returns_is_a_legal_po_move(self) -> None:
        legal = po_domain.PO_TRANSITIONS
        assert "partially_received" in legal["approved"]
        assert "received" in legal["approved"] and "received" in legal["partially_received"]
        # a partial PO cannot be cancelled: stock already arrived against it
        assert "cancelled" not in legal["partially_received"]
        assert po_domain.can_transition_po("approved", "partially_received")
        assert not po_domain.can_transition_po("draft", "partially_received")


class TestRequisitionStateMachine:
    def test_the_happy_path(self) -> None:
        chain = ["DRAFT", "SUBMITTED", "APPROVED", "CONVERTED"]
        for current, new in pairwise(chain):
            assert d.can_transition_requisition(current, new)

    @pytest.mark.parametrize("terminal", ["REJECTED", "CONVERTED", "CANCELLED"])
    def test_terminal_states_go_nowhere(self, terminal: str) -> None:
        for other in d.REQUISITION_TRANSITIONS:
            assert not d.can_transition_requisition(terminal, other)

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            ("DRAFT", "APPROVED"),  # approval needs a submission first
            ("DRAFT", "CONVERTED"),
            ("SUBMITTED", "CONVERTED"),  # and a decision before conversion
            ("APPROVED", "REJECTED"),  # a decision is not undone
            ("APPROVED", "SUBMITTED"),
            ("SUBMITTED", "SUBMITTED"),  # a no-op "move" is a miss too
        ],
    )
    def test_skipping_or_undoing_a_step_is_refused(self, current: str, new: str) -> None:
        assert not d.can_transition_requisition(current, new)

    def test_only_unconverted_requisitions_can_be_cancelled(self) -> None:
        for current in ("DRAFT", "SUBMITTED", "APPROVED"):
            assert d.can_transition_requisition(current, "CANCELLED")


class TestUnitConversionIsExact:
    box: ClassVar[dict[str, tuple[int, int]]] = {"box": (12, 1)}

    def test_three_boxes_of_twelve_is_thirty_six_pieces(self) -> None:
        assert d.to_stock_milli(q("3"), "box", "pcs", self.box) == q("36")

    def test_a_ratio_is_reduced_to_lowest_terms(self) -> None:
        assert d.reduce_ratio(24, 2) == (12, 1)
        assert d.reduce_ratio(250, 1000) == (1, 4)
        with pytest.raises(QuantityError):
            d.reduce_ratio(0, 1)

    def test_a_fractional_ratio_converts_without_rounding(self) -> None:
        # a bundle of 2.5 m: 1 bundle = 5/2 m
        ratios = {"bundle": (5, 2)}
        assert d.to_stock_milli(q("4"), "bundle", "m", ratios) == q("10")
        assert d.to_stock_milli(q("3"), "bundle", "m", ratios) == q("7.5")

    def test_a_result_that_is_not_a_whole_milli_unit_is_refused(self) -> None:
        # 1 roll = 1/3 m -> one roll is 0.333.. m: not representable, never rounded silently
        with pytest.raises(QuantityError):
            d.to_stock_milli(q("1"), "spool", "m", {"spool": (1, 3)})

    def test_a_discrete_stock_unit_refuses_a_fractional_result(self) -> None:
        # 1 box = 12 pcs: a tenth of a box is 1.2 pieces
        with pytest.raises(QuantityError):
            d.to_stock_milli(q("0.1"), "box", "pcs", self.box)

    def test_fixed_units_of_one_dimension_convert_exactly(self) -> None:
        assert d.to_stock_milli(q("150"), "cm", "m", {}) == q("1.5")
        assert d.to_stock_milli(q("2"), "kg", "g", {}) == q("2000")

    def test_the_same_unit_is_the_identity_and_spellings_fold(self) -> None:
        assert d.to_stock_milli(q("82.5"), "metres", "m", {}) == q("82.5")
        assert d.to_stock_milli(q("5"), "PCS", "pcs", self.box) == q("5")

    def test_an_unknown_relation_is_refused_not_guessed(self) -> None:
        with pytest.raises(QuantityError):
            d.to_stock_milli(q("1"), "carton", "pcs", self.box)  # no carton ratio recorded
        with pytest.raises(QuantityError):
            d.to_stock_milli(q("1"), "kg", "m", {})  # different dimensions

    def test_stock_units_per_is_an_exact_fraction_or_none(self) -> None:
        assert d.stock_units_per("box", "pcs", self.box) == Fraction(12)
        assert d.stock_units_per("cm", "m", {}) == Fraction(1, 100)
        assert d.stock_units_per("carton", "pcs", {}) is None
        assert d.stock_units_per("pcs", "pcs", {}) == 1


class TestCost:
    def test_a_price_per_box_is_a_price_per_piece_rounded_half_even_once(self) -> None:
        # 100.00 per box of 12 -> 8.33333.. per piece -> 833 minor units
        assert d.unit_cost_per_stock_unit(10_000, "box", "pcs", {"box": (12, 1)}) == 833
        # 6.00 per box of 12 is exactly 0.50 per piece
        assert d.unit_cost_per_stock_unit(600, "box", "pcs", {"box": (12, 1)}) == 50
        # a half minor unit goes to the even neighbour: 25 / 2 = 12.5 -> 12, 27 / 2 = 13.5 -> 14
        assert d.unit_cost_per_stock_unit(25, "pair", "pcs", {"pair": (2, 1)}) == 12
        assert d.unit_cost_per_stock_unit(27, "pair", "pcs", {"pair": (2, 1)}) == 14

    def test_the_same_unit_or_an_unrelated_one_passes_the_price_through(self) -> None:
        assert d.unit_cost_per_stock_unit(123_45, "m", "m", {}) == 123_45
        assert d.unit_cost_per_stock_unit(123_45, "MT", "m", {}) == 123_45  # unregistered unit

    def test_a_weighted_cost_is_the_quantity_weighted_mean(self) -> None:
        assert d.weighted_unit_cost([(q("10"), 100), (q("30"), 200)]) == 175
        assert d.weighted_unit_cost([(q("1"), 5), (q("1"), 6)]) == 6  # 5.5 -> even
        with pytest.raises(QuantityError):
            d.weighted_unit_cost([])
