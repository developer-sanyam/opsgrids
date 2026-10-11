"""`platform_core.gst` — the GST rules finance and billing share (ADR-0004 Q1).

Finance's own suites (`test_gstin.py`, `test_gst_split.py`) still exercise these through
`app.domain`; these pin the shared module directly, so a change here fails in the library that
owns it. Every GSTIN below is fake: a made-up PAN with its check digit computed, never a real one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from platform_core import gst


def _fake_gstin(state: str = "29") -> str:
    base = f"{state}ABCDE1234F1Z"
    return base + gst.gstin_check_digit(base)


class TestGstin:
    def test_a_fake_gstin_with_its_computed_check_digit_parses(self) -> None:
        parsed = gst.parse_gstin(_fake_gstin("27"))
        assert parsed.state_code == "27"
        assert parsed.pan == "ABCDE1234F"
        assert parsed.state_name == "Maharashtra"

    def test_input_is_normalised_before_it_is_checked(self) -> None:
        raw = _fake_gstin().lower()
        assert gst.parse_gstin(f" {raw[:5]}-{raw[5:]} ").value == raw.upper()

    def test_a_wrong_check_digit_is_refused_with_the_right_one_named(self) -> None:
        good = _fake_gstin()
        bad = good[:14] + ("0" if good[14] != "0" else "1")
        with pytest.raises(gst.GstinError, match="checksum"):
            gst.parse_gstin(bad)
        assert not gst.is_valid_gstin(bad)

    @pytest.mark.parametrize("raw", ["", "29ABCDE1234F1Z", "29ABCDE1234F1X5", "00ABCDE1234F1Z5"])
    def test_malformed_numbers_are_refused(self, raw: str) -> None:
        assert not gst.is_valid_gstin(raw)

    def test_state_names_resolve_and_unknown_ones_do_not_guess(self) -> None:
        assert gst.state_code_for_name("Tamil Nadu") == "33"
        assert gst.state_code_for_name("new delhi") == "07"
        assert gst.state_code_for_name("Atlantis") is None
        assert gst.state_code_for_name(None) is None


class TestFinancialYear:
    @pytest.mark.parametrize(
        ("on", "fy"),
        [
            (date(2026, 4, 1), "2026-27"),
            (date(2027, 3, 31), "2026-27"),
            (date(2000, 1, 1), "1999-00"),
        ],
    )
    def test_april_to_march(self, on: date, fy: str) -> None:
        assert gst.financial_year(on) == fy


class TestSplit:
    def test_tax_is_rounded_half_up_once(self) -> None:
        assert gst.gst_amount_minor(1_003, Decimal(18)) == 181  # 180.54
        assert gst.gst_amount_minor(-1_003, Decimal(18)) == -181

    def test_intra_state_odd_paisa_goes_to_sgst_and_the_heads_foot(self) -> None:
        split = gst.split_gst_total(181, intra_state=True)
        assert (split.cgst_minor, split.sgst_minor, split.igst_minor) == (90, 91, 0)
        assert gst.split_gst_total(-181, intra_state=True) == gst.TaxSplit(-90, -91, 0)

    def test_inter_state_is_all_igst(self) -> None:
        assert gst.split_gst_total(181, intra_state=False) == gst.TaxSplit(igst_minor=181)

    def test_split_gst_is_the_rate_then_the_total_split(self) -> None:
        for taxable in range(0, 2_000, 7):
            for rate in (Decimal(5), Decimal(12), Decimal(18), Decimal("0.25")):
                split = gst.split_gst(taxable, rate, intra_state=True)
                assert split.total_minor == gst.gst_amount_minor(taxable, rate)

    def test_sez_and_export_are_inter_state_even_in_the_sellers_state(self) -> None:
        for supply in ("sez", "export"):
            assert not gst.is_intra_state(
                supply_type=supply, seller_state_code="29", place_of_supply="29"
            )
        assert gst.is_intra_state(supply_type="b2b", seller_state_code="29", place_of_supply="29")
        assert not gst.is_intra_state(supply_type="b2b", seller_state_code="", place_of_supply="")
