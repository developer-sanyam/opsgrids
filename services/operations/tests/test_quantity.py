"""Exact quantities (ADR-0014 §2) — pure, no database.

The contract: a quantity is an int of milli-units of the item's UOM; parsing a wire decimal is
exact or refused, never rounded; formatting round-trips; fixed conversions are exact rationals
and anything inexact or variable (a roll's length-to-weight) is refused rather than guessed.
"""

from __future__ import annotations

import pytest

from app.inventory.quantity import (
    QTY_SCALE,
    UNIT_NAMES,
    UNITS,
    QuantityError,
    Rounding,
    convert,
    convert_by_ratio,
    describe,
    format_qty,
    is_discrete,
    parse_qty,
    value_minor,
    whole_units,
)


class TestParseAndFormat:
    @pytest.mark.parametrize(
        ("wire", "uom", "milli"),
        [
            ("82.5", "m", 82_500),
            ("0.5", "m", 500),
            ("1.25", "m", 1_250),
            ("99999.99", "m", 99_999_990),
            ("14.8", "kg", 14_800),
            ("0.125", "kg", 125),
            ("1", "pcs", 1_000),
            ("100", "pcs", 100_000),
            ("0", "m", 0),
            ("0.001", "m", 1),
            (" 7.250 ", "kg", 7_250),
        ],
    )
    def test_decimal_strings_parse_exactly(self, wire: str, uom: str, milli: int) -> None:
        assert parse_qty(wire, uom) == milli

    @pytest.mark.parametrize(
        ("milli", "wire"),
        [
            (82_500, "82.5"),
            (500, "0.5"),
            (1_250, "1.25"),
            (99_999_990, "99999.99"),
            (14_800, "14.8"),
            (125, "0.125"),
            (1_000, "1"),
            (100_000, "100"),
            (0, "0"),
            (1, "0.001"),
            (-82_500, "-82.5"),
        ],
    )
    def test_formatting_is_exact_and_minimal(self, milli: int, wire: str) -> None:
        assert format_qty(milli) == wire

    @pytest.mark.parametrize("wire", ["82.5", "0.5", "1.25", "99999.99", "14.8", "0.125", "7"])
    def test_parse_format_round_trips(self, wire: str) -> None:
        assert format_qty(parse_qty(wire)) == wire

    def test_whole_ints_are_whole_units(self) -> None:
        assert parse_qty(40, "pcs") == 40 * QTY_SCALE

    def test_a_float_is_refused_not_rounded(self) -> None:
        with pytest.raises(QuantityError, match="never a float"):
            parse_qty(82.5)  # type: ignore[arg-type]

    def test_a_bool_is_not_a_quantity(self) -> None:
        with pytest.raises(QuantityError):
            parse_qty(True)  # type: ignore[arg-type]

    @pytest.mark.parametrize("wire", ["0.0001", "1.2345", "1e3", "1,000", "", "abc", "1.", ".5"])
    def test_anything_but_three_decimals_is_refused(self, wire: str) -> None:
        with pytest.raises(QuantityError):
            parse_qty(wire)

    def test_negative_needs_to_be_allowed(self) -> None:
        with pytest.raises(QuantityError, match="negative"):
            parse_qty("-3")
        assert parse_qty("-3", allow_negative=True) == -3_000

    @pytest.mark.parametrize("uom", ["pcs", "box", "carton", "bundle", "set", "roll", "Pieces"])
    def test_discrete_units_refuse_fractions(self, uom: str) -> None:
        assert is_discrete(uom)
        with pytest.raises(QuantityError, match="whole units"):
            parse_qty("1.5", uom)

    def test_an_unknown_uom_is_continuous(self) -> None:
        assert not is_discrete("MT")
        assert parse_qty("2.5", "MT") == 2_500

    def test_the_accumulation_that_breaks_floats_is_exact(self) -> None:
        # 0.1 + 0.2 != 0.3 in binary floating point; in milli-units it is plain integer addition.
        total = parse_qty("0.1") + parse_qty("0.2")
        assert total == parse_qty("0.3")
        assert format_qty(sum(parse_qty("0.1") for _ in range(10))) == "1"

    def test_describe_names_the_unit(self) -> None:
        assert describe(82_500, "m") == "82.5 m"

    @pytest.mark.parametrize(
        ("milli", "whole"), [(6_000, 6), (82_500, 82), (-82_500, -82), (999, 0), (0, 0)]
    )
    def test_whole_units_truncates_toward_zero(self, milli: int, whole: int) -> None:
        assert whole_units(milli) == whole


class TestConversion:
    def test_one_metre_is_a_thousand_millimetres(self) -> None:
        assert convert(parse_qty("1"), "m", "mm") == parse_qty("1000")

    def test_one_kilogram_is_a_thousand_grams(self) -> None:
        assert convert(parse_qty("1"), "kg", "g") == parse_qty("1000")

    def test_a_roll_length_converts_both_ways_exactly(self) -> None:
        in_mm = convert(parse_qty("82.5"), "m", "mm")
        assert format_qty(in_mm) == "82500"
        assert convert(in_mm, "mm", "m") == parse_qty("82.5")

    def test_grams_to_kilograms(self) -> None:
        assert format_qty(convert(parse_qty("125"), "g", "kg")) == "0.125"

    def test_inches_are_exact_rationals(self) -> None:
        assert format_qty(convert(parse_qty("58"), "in", "mm")) == "1473.2"

    def test_an_inexact_result_is_refused_without_a_rounding_mode(self) -> None:
        # 1 mm is 0.001 m, but 0.5 mm is 0.0005 m — not a whole milli-metre-unit.
        with pytest.raises(QuantityError, match="rounding mode"):
            convert(parse_qty("0.5"), "mm", "m")

    def test_rounding_is_only_ever_explicit(self) -> None:
        half_mm = parse_qty("0.5")
        assert convert(half_mm, "mm", "m", rounding=Rounding.HALF_EVEN) == 0
        assert convert(half_mm, "mm", "m", rounding=Rounding.UP) == 1
        assert convert(half_mm, "mm", "m", rounding=Rounding.DOWN) == 0
        assert convert(-half_mm, "mm", "m", rounding=Rounding.UP) == -1

    def test_length_never_converts_to_mass(self) -> None:
        # A roll's 82.5 m ↔ 14.8 kg is a measurement of that roll, not a universal factor.
        with pytest.raises(QuantityError, match="measured, not assumed"):
            convert(parse_qty("82.5"), "m", "kg")

    def test_container_units_have_no_fixed_size(self) -> None:
        with pytest.raises(QuantityError):
            convert(parse_qty("1"), "box", "pcs")

    def test_an_unknown_unit_has_no_conversion(self) -> None:
        with pytest.raises(QuantityError, match="no fixed conversion"):
            convert(parse_qty("1"), "bolt", "m")

    def test_dozen_to_pieces(self) -> None:
        assert convert(parse_qty("2"), "dozen", "pcs") == parse_qty("24")

    def test_82_5_metres_round_trips_through_centimetres(self) -> None:
        in_cm = convert(parse_qty("82.5"), "m", "cm")
        assert format_qty(in_cm) == "8250"
        assert convert(in_cm, "cm", "m") == parse_qty("82.5")

    def test_two_rolls_of_one_fabric_keep_their_own_weights(self) -> None:
        # Variable roll weights: each roll's (length, weight) pair is two measured quantities.
        # Nothing derives one from the other, so the two rolls stay independently exact.
        roll_a = (parse_qty("82.5", "m"), parse_qty("14.8", "kg"))
        roll_b = (parse_qty("75", "m"), parse_qty("13.1", "kg"))
        assert roll_a[0] + roll_b[0] == parse_qty("157.5")
        assert roll_a[1] + roll_b[1] == parse_qty("27.9")
        assert roll_a[1] * roll_b[0] != roll_b[1] * roll_a[0]  # different grams per metre


class TestValuation:
    def test_value_is_quantity_times_unit_cost_rounded_once(self) -> None:
        # 82.5 m at ₹123.45/m = ₹10,184.625 → half-to-even → ₹10,184.62
        assert value_minor(parse_qty("82.5"), 123_45) == 10_184_62

    def test_whole_units_value_is_exact(self) -> None:
        assert value_minor(parse_qty("6"), 2_18_000_00) == 13_08_000_00


class TestConvertByRatio:
    """Item-specific rational conversions (Phase 1 `uom_conversions`): "1 box = 12 pcs" is
    `numerator=12, denominator=1`, and — unlike the fixed registry — the same unit code can mean
    a different ratio for a different item."""

    def test_a_box_of_twelve_converts_to_pieces_and_back(self) -> None:
        one_box = parse_qty("1")
        as_pcs = convert_by_ratio(one_box, 12, 1, discrete=True)
        assert format_qty(as_pcs) == "12"
        assert convert_by_ratio(as_pcs, 12, 1, invert=True, discrete=True) == one_box

    def test_two_and_a_half_boxes_of_twelve(self) -> None:
        assert format_qty(convert_by_ratio(parse_qty("2.5"), 12, 1, discrete=True)) == "30"

    def test_a_non_integer_ratio_is_exact_via_the_fraction(self) -> None:
        # 1 roll = 82.5 m of an item stocked in metres: numerator=825, denominator=10.
        one_roll = parse_qty("1")
        assert format_qty(convert_by_ratio(one_roll, 825, 10)) == "82.5"

    def test_an_inexact_result_is_refused_without_a_rounding_mode(self) -> None:
        # 1 pcs is 1/12 of a box — 83.33 thousandths of a box, not a whole milli-box.
        with pytest.raises(QuantityError, match="rounding mode"):
            convert_by_ratio(parse_qty("1"), 12, 1, invert=True)
        assert convert_by_ratio(parse_qty("1"), 12, 1, invert=True, rounding=Rounding.DOWN) == 83

    def test_the_destination_discreteness_is_the_callers_to_name(self) -> None:
        # 6 pcs / 12-per-box divides exactly (500 milli-box = 0.5 box) but a box is discrete —
        # refused for not being a *whole* box, a different failure from an inexact division.
        with pytest.raises(QuantityError, match="whole units"):
            convert_by_ratio(parse_qty("6"), 12, 1, invert=True, discrete=True)

    @pytest.mark.parametrize(("numerator", "denominator"), [(0, 1), (1, 0), (-1, 1), (1, -1)])
    def test_a_non_positive_ratio_is_refused(self, numerator: int, denominator: int) -> None:
        with pytest.raises(QuantityError, match="positive numerator and denominator"):
            convert_by_ratio(parse_qty("1"), numerator, denominator)


class TestUnitNames:
    def test_every_fixed_unit_has_a_seed_display_name(self) -> None:
        assert set(UNIT_NAMES) == set(UNITS)
