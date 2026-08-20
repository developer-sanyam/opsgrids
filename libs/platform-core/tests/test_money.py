"""Money arithmetic and allocation.

The invariant these tests exist to defend: **parts sum exactly to the whole**. An invoice
whose lines do not add up to its total is discovered by a customer, during a dispute, and
by then it has been wrong for months. Property tests are used rather than examples because
the failure cases are the awkward remainders, not the round numbers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from platform_core.money import CurrencyMismatch, Money, exponent, total


class TestConstruction:
    def test_minor_units_are_integers(self) -> None:
        with pytest.raises(TypeError):
            Money(10.5, "INR")  # type: ignore[arg-type]

    def test_bool_is_not_an_int_here(self) -> None:
        # bool subclasses int; a True amount is a bug, not a rupee.
        with pytest.raises(TypeError):
            Money(True, "INR")

    def test_currency_is_normalised(self) -> None:
        assert Money(1, "inr").currency == "INR"

    def test_currency_must_be_iso(self) -> None:
        with pytest.raises(ValueError, match="ISO-4217"):
            Money(1, "RUPEE")

    @pytest.mark.parametrize(
        ("currency", "expected"),
        [("INR", 2), ("USD", 2), ("JPY", 0), ("KWD", 3)],
    )
    def test_minor_unit_exponent_varies_by_currency(self, currency: str, expected: int) -> None:
        # Hardcoding two decimals is wrong for a third of the world.
        assert exponent(currency) == expected

    def test_from_decimal_respects_the_exponent(self) -> None:
        assert Money.from_decimal("100.50", "INR").minor == 10_050
        assert Money.from_decimal("100", "JPY").minor == 100
        assert Money.from_decimal("1.234", "KWD").minor == 1_234

    def test_from_decimal_rounds_half_up(self) -> None:
        assert Money.from_decimal("0.005", "INR").minor == 1


class TestArithmetic:
    def test_currencies_do_not_mix(self) -> None:
        with pytest.raises(CurrencyMismatch):
            Money(100, "INR") + Money(100, "USD")

    def test_multiplication_is_by_whole_counts(self) -> None:
        assert (Money(100, "INR") * 3).minor == 300
        with pytest.raises(TypeError):
            Money(100, "INR") * 1.5  # type: ignore[operator]

    def test_total_of_nothing_still_has_a_currency(self) -> None:
        assert total([], "INR") == Money(0, "INR")

    def test_percent_bps(self) -> None:
        # 18% GST on 100.00
        assert Money(10_000, "INR").percent_bps(1_800) == Money(1_800, "INR")


class TestAllocation:
    def test_the_classic_remainder(self) -> None:
        # 100 across three ways is 34/33/33 — never 33/33/33, which loses a unit.
        parts = Money(100, "INR").split(3)
        assert [p.minor for p in parts] == [34, 33, 33]
        assert sum(p.minor for p in parts) == 100

    def test_weighted_allocation_follows_the_weights(self) -> None:
        parts = Money(1000, "INR").allocate([1, 4])
        assert [p.minor for p in parts] == [200, 800]

    def test_a_credit_allocates_as_the_mirror_of_a_charge(self) -> None:
        charge = Money(100, "INR").split(3)
        credit = Money(-100, "INR").split(3)
        assert [-p.minor for p in credit] == [p.minor for p in charge]

    def test_zero_weights_are_refused(self) -> None:
        with pytest.raises(ValueError, match="all be zero"):
            Money(100, "INR").allocate([0, 0])

    def test_a_line_with_no_weight_gets_nothing(self) -> None:
        parts = Money(100, "INR").allocate([1, 0])
        assert [p.minor for p in parts] == [100, 0]

    @given(
        amount=st.integers(min_value=-10_000_000, max_value=10_000_000),
        weights=st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=12),
    )
    def test_allocation_always_sums_to_the_whole(self, amount: int, weights: list[int]) -> None:
        if sum(weights) == 0:
            return
        parts = Money(amount, "INR").allocate(weights)
        assert sum(p.minor for p in parts) == amount

    @given(
        amount=st.integers(min_value=0, max_value=10_000_000),
        parts=st.integers(min_value=1, max_value=50),
    )
    def test_even_split_is_within_one_unit(self, amount: int, parts: int) -> None:
        shares = [p.minor for p in Money(amount, "INR").split(parts)]
        assert max(shares) - min(shares) <= 1
        assert sum(shares) == amount


class TestRates:
    def test_sub_minor_rates_round_once_at_the_line(self) -> None:
        # ₹0.0005 per API call — a twentieth of a paisa. 10,000 calls is ₹5.00.
        line = Money.rate(unit_price_micros=500, quantity=10_000, currency="INR")
        assert line == Money(500, "INR")

    def test_a_single_call_at_a_sub_paisa_rate_rounds_to_zero(self) -> None:
        assert Money.rate(unit_price_micros=500, quantity=1, currency="INR").minor == 0

    def test_zero_decimal_currency(self) -> None:
        # 1_000_000 micros = 1 yen, and a yen has no minor subdivision.
        assert Money.rate(unit_price_micros=1_000_000, quantity=3, currency="JPY").minor == 3

    def test_fractional_quantity(self) -> None:
        line = Money.rate(unit_price_micros=1_000_000, quantity=Decimal("2.5"), currency="INR")
        assert line == Money(250, "INR")


class TestDisplay:
    def test_str_uses_the_currency_exponent(self) -> None:
        assert str(Money(10_050, "INR")) == "100.50 INR"
        assert str(Money(100, "JPY")) == "100 JPY"
        assert str(Money(1_234, "KWD")) == "1.234 KWD"
