"""The CGST/SGST/IGST split — property tests, because footing is the only thing that matters.

A GST split is unusual among calculations in that "close enough" has no meaning. The three heads
are declared separately in a GSTR-1 return and separately again in the e-invoice payload, and the
portal recomputes both. A single paisa of disagreement is not a rounding difference; it is a
mismatch a human reconciles by hand, and at volume that is somebody's week.

So these tests are written as invariants over wide ranges of inputs rather than as a handful of
worked examples: the split always sums to the tax, the tax is always the rate applied once, the
halves never differ by more than a paisa, and a credit note reverses an invoice head for head. The
worked examples are here too, but they are the illustration, not the guarantee.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import domain

#: Spans a paisa, a rupee, a lakh, a crore and a figure past the 32-bit boundary the money columns
#: are BigInteger for — plus the odd values where halving is not exact.
AMOUNTS = [
    0,
    1,
    3,
    7,
    99,
    100,
    101,
    333,
    1_00_000,
    1_23_456,
    9_99_999,
    47_700_000,
    56_286_000,
    2_147_483_647,
    9_999_999_999,
]

RATES = sorted(domain.GST_RATE_BANDS)


class TestTaxAmount:
    @pytest.mark.parametrize("amount", AMOUNTS)
    @pytest.mark.parametrize("rate", RATES)
    def test_the_tax_is_the_rate_applied_once_exactly(self, amount: int, rate: Decimal) -> None:
        """Computed in Decimal, so it matches the exact arithmetic rather than a float's."""
        exact = Decimal(amount) * rate / Decimal(100)
        assert domain.gst_amount_minor(amount, rate) == round(exact)

    def test_zero_rate_is_zero_tax(self) -> None:
        for amount in AMOUNTS:
            assert domain.gst_amount_minor(amount, Decimal("0")) == 0

    def test_rounding_is_half_away_from_zero_in_both_directions(self) -> None:
        """A credit note's negative tax must round symmetrically with the invoice's, or the pair
        does not net to zero."""
        # 10 paise at 5 % is exactly half a paisa.
        assert domain.gst_amount_minor(10, Decimal("5")) == 1
        assert domain.gst_amount_minor(-10, Decimal("5")) == -1

    def test_a_float_would_not_have_done(self) -> None:
        """The reason `Decimal` is load-bearing rather than fastidious: 0.1-style binary error at
        scale lands the tax a paisa out, and this is the arithmetic that must not."""
        amount, rate = 8_35_675, Decimal("18")
        assert domain.gst_amount_minor(amount, rate) == 1_50_422  # exact: 150421.5 -> half up
        assert int(amount * 18 / 100) == 1_50_421  # what float truncation would have given


class TestSplitFoots:
    @pytest.mark.parametrize("amount", AMOUNTS)
    @pytest.mark.parametrize("rate", RATES)
    @pytest.mark.parametrize("intra", [True, False])
    def test_the_components_sum_to_the_tax_exactly(
        self, amount: int, rate: Decimal, intra: bool
    ) -> None:
        """**The** invariant. Every amount, every band, both shapes."""
        split = domain.split_gst(amount, rate, intra_state=intra)
        assert split.total_minor == domain.gst_amount_minor(amount, rate)

    @pytest.mark.parametrize("amount", AMOUNTS)
    @pytest.mark.parametrize("rate", RATES)
    def test_intra_state_charges_cgst_and_sgst_and_no_igst(
        self, amount: int, rate: Decimal
    ) -> None:
        split = domain.split_gst(amount, rate, intra_state=True)
        assert split.igst_minor == 0
        assert abs(split.cgst_minor - split.sgst_minor) <= 1

    @pytest.mark.parametrize("amount", AMOUNTS)
    @pytest.mark.parametrize("rate", RATES)
    def test_inter_state_charges_igst_and_nothing_else(self, amount: int, rate: Decimal) -> None:
        split = domain.split_gst(amount, rate, intra_state=False)
        assert (split.cgst_minor, split.sgst_minor) == (0, 0)
        assert split.igst_minor == domain.gst_amount_minor(amount, rate)

    def test_the_odd_paisa_goes_to_sgst(self) -> None:
        """Not arbitrary — it has to go *somewhere* deterministically, or two runs of the same
        document disagree. 5 % of 30 paise is 1.5 -> 2 paise, which halves as 1 and 1; 5 % of
        10 paise is 1 paise, which cannot halve evenly."""
        split = domain.split_gst(10, Decimal("5"), intra_state=True)
        assert (split.cgst_minor, split.sgst_minor) == (0, 1)
        assert split.total_minor == 1

    def test_a_credit_note_reverses_an_invoice_head_for_head(self) -> None:
        """The platform corrects with a new negative document, so the two must net to nothing in
        every head — not merely in total."""
        for amount in AMOUNTS:
            for rate in RATES:
                for intra in (True, False):
                    up = domain.split_gst(amount, rate, intra_state=intra)
                    down = domain.split_gst(-amount, rate, intra_state=intra)
                    assert up.cgst_minor + down.cgst_minor == 0
                    assert up.sgst_minor + down.sgst_minor == 0
                    assert up.igst_minor + down.igst_minor == 0

    def test_splits_add_component_wise_so_a_document_foots_to_its_lines(self) -> None:
        """How an invoice's split is built: `sum(line splits)`. If addition were not
        component-wise the document could foot in total while disagreeing head by head, which is
        exactly the mismatch the portal rejects."""
        lines = [(1_23_456, Decimal("18")), (7_777, Decimal("5")), (999, Decimal("12"))]
        total = domain.ZERO_TAX
        for amount, rate in lines:
            total = total + domain.split_gst(amount, rate, intra_state=True)
        assert total.cgst_minor + total.sgst_minor == total.total_minor
        assert total.total_minor == sum(
            domain.gst_amount_minor(amount, rate) for amount, rate in lines
        )

    def test_rounding_per_line_is_not_rounding_the_document(self) -> None:
        """Why the split is computed per line and summed, rather than once over the subtotal.

        Three lines of 1 paise at 18 % each round to nothing; the same 3 paise taxed as one
        subtotal rounds to 1. Both are defensible, but only one of them agrees with what the
        portal recomputes from the lines it was sent — and that is the per-line figure.
        """
        per_line = sum(domain.gst_amount_minor(1, Decimal("18")) for _ in range(3))
        whole = domain.gst_amount_minor(3, Decimal("18"))
        assert per_line == 0
        assert whole == 1


class TestIntraOrInterState:
    def test_the_same_state_is_intra_state(self) -> None:
        assert domain.is_intra_state(
            supply_type="b2b", seller_state_code="27", place_of_supply="27"
        )

    def test_a_different_state_is_inter_state(self) -> None:
        assert not domain.is_intra_state(
            supply_type="b2b", seller_state_code="27", place_of_supply="24"
        )

    @pytest.mark.parametrize("supply_type", ["export", "sez"])
    def test_exports_and_sez_are_inter_state_even_within_one_state(self, supply_type: str) -> None:
        """The case a naive `seller == buyer` comparison gets wrong, and gets wrong expensively:
        a supply to an SEZ unit down the road is IGST by statute (IGST Act s. 7(5)(b))."""
        assert not domain.is_intra_state(
            supply_type=supply_type, seller_state_code="27", place_of_supply="27"
        )

    def test_b2c_within_the_state_is_still_intra_state(self) -> None:
        assert domain.is_intra_state(
            supply_type="b2c", seller_state_code="27", place_of_supply="27"
        )

    def test_no_seller_state_is_never_intra_state(self) -> None:
        """An organization that has not configured its registration cannot be said to be supplying
        within its own state, because there is no own state. Inter-state is the safe reading here
        only because nothing may be filed at all without a registration."""
        assert not domain.is_intra_state(
            supply_type="b2b", seller_state_code="", place_of_supply="27"
        )


class TestEinvoiceApplicability:
    @pytest.mark.parametrize("supply_type", ["b2b", "export", "sez"])
    @pytest.mark.parametrize("document_type", ["invoice", "credit_note"])
    def test_reportable_supplies_need_an_irn(self, supply_type: str, document_type: str) -> None:
        assert domain.requires_einvoice(supply_type=supply_type, document_type=document_type)

    def test_b2c_does_not(self) -> None:
        """E-invoicing covers B2B, exports and SEZ. A B2C invoice carries a dynamic QR obligation
        instead, which is a different mechanism and not this slice."""
        assert not domain.requires_einvoice(supply_type="b2c", document_type="invoice")

    def test_an_unknown_document_type_does_not(self) -> None:
        assert not domain.requires_einvoice(supply_type="b2b", document_type="quotation")


class TestCancellationWindow:
    def test_within_twenty_four_hours_is_open(self) -> None:
        from datetime import UTC, datetime, timedelta

        acked = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
        assert domain.cancellation_window_open(acked, acked + timedelta(hours=23, minutes=59))

    def test_past_twenty_four_hours_is_closed(self) -> None:
        from datetime import UTC, datetime, timedelta

        acked = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
        assert not domain.cancellation_window_open(acked, acked + timedelta(hours=24, seconds=1))
