"""The input-tax-credit ledger's arithmetic, and GSTR-3B's assembly — pure functions.

Two things must be true of the statutory utilisation order (`domain.utilise_itc`) for every input,
not merely the worked examples: nothing it produces is ever negative, and CGST credit never offsets
SGST liability or vice versa — the two are separate governments' money and the statute keeps them
apart even when both are owed to the same taxpayer. So the property tests here run over a grid of
credit and liability combinations rather than a handful of hand-picked ones, the same shape
`test_gst_split.py` takes with the tax split.

`assemble_gstr3b` is cross-footed against `assemble_gstr1` the same way GSTR-1's own HSN summary
is cross-footed against its sections: built independently from the same raw documents, so a test
comparing the two is a real check rather than a tautology.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app import domain

# --------------------------------------------------------------------- the utilisation order

#: A grid spanning zero, a value below the other side, an exact match and a value above it — the
#: four shapes a credit-versus-liability comparison can take.
AMOUNTS = [0, 100, 500, 1_000, 12_345]


class TestUtilisationNeverGoesNegative:
    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::7],  # a spread, not the full 5**6 grid
    )
    def test_every_output_is_non_negative(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        for head in domain.ITC_TAX_HEAD_ORDER:
            assert result.credit_remaining.for_head(head) >= 0
            assert result.cash_payable.for_head(head) >= 0
        for line in result.lines:
            assert line.amount_minor > 0

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::7],
    )
    def test_cgst_and_sgst_credit_never_cross(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        """CGST credit may fall through to IGST liability, and SGST credit may too — but neither
        ever reaches the other's liability. Two separate governments' money."""
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        for line in result.lines:
            assert not (line.head == "cgst" and line.against == "sgst")
            assert not (line.head == "sgst" and line.against == "cgst")

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::7],
    )
    def test_every_head_foots_credit_applied_plus_remaining_equals_available(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        for head in domain.ITC_TAX_HEAD_ORDER:
            applied_from = sum(entry.amount_minor for entry in result.lines if entry.head == head)
            assert applied_from + result.credit_remaining.for_head(head) == available.for_head(head)

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::7],
    )
    def test_every_head_foots_liability_met_plus_cash_payable_equals_liability(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        """The liability is either fully met by credit or the remainder is payable in cash —
        never both partially unaccounted for."""
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        for head in domain.ITC_TAX_HEAD_ORDER:
            applied_against = sum(
                entry.amount_minor for entry in result.lines if entry.against == head
            )
            assert applied_against + result.cash_payable.for_head(head) == liability.for_head(head)

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::7],
    )
    def test_igst_credit_is_exhausted_before_cgst_or_sgst_credit_reaches_igst_liability(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        crosses_into_igst = any(
            line.against == "igst" and line.head in ("cgst", "sgst") for line in result.lines
        )
        if crosses_into_igst:
            assert result.credit_remaining.igst_minor == 0


class TestUtilisationWorkedExamples:
    """The illustration, not the guarantee — the property tests above are that."""

    def test_igst_credit_covers_its_own_liability_then_spills_to_cgst_then_sgst(self) -> None:
        available = domain.TaxHeadAmounts(igst_minor=1_000)
        liability = domain.TaxHeadAmounts(igst_minor=200, cgst_minor=300, sgst_minor=300)
        result = domain.utilise_itc(available, liability)
        assert {(entry.head, entry.against, entry.amount_minor) for entry in result.lines} == {
            ("igst", "igst", 200),
            ("igst", "cgst", 300),
            ("igst", "sgst", 300),
        }
        assert result.credit_remaining == domain.TaxHeadAmounts(igst_minor=200)
        assert result.cash_payable == domain.ZERO_ITC

    def test_cgst_credit_never_touches_sgst_liability(self) -> None:
        available = domain.TaxHeadAmounts(cgst_minor=1_000)
        liability = domain.TaxHeadAmounts(sgst_minor=500)
        result = domain.utilise_itc(available, liability)
        assert result.lines == ()
        assert result.credit_remaining == domain.TaxHeadAmounts(cgst_minor=1_000)
        assert result.cash_payable == domain.TaxHeadAmounts(sgst_minor=500)

    def test_sgst_credit_never_touches_cgst_liability(self) -> None:
        available = domain.TaxHeadAmounts(sgst_minor=1_000)
        liability = domain.TaxHeadAmounts(cgst_minor=500)
        result = domain.utilise_itc(available, liability)
        assert result.lines == ()
        assert result.cash_payable == domain.TaxHeadAmounts(cgst_minor=500)

    def test_cess_never_touches_gst_and_gst_never_touches_cess(self) -> None:
        available = domain.TaxHeadAmounts(igst_minor=1_000, cess_minor=50)
        liability = domain.TaxHeadAmounts(igst_minor=200, cess_minor=200)
        result = domain.utilise_itc(available, liability)
        assert ("cess", "cess", 50) in {
            (entry.head, entry.against, entry.amount_minor) for entry in result.lines
        }
        assert result.cash_payable.cess_minor == 150
        assert result.credit_remaining.igst_minor == 800

    def test_a_shortfall_is_payable_in_cash(self) -> None:
        available = domain.TaxHeadAmounts()
        liability = domain.TaxHeadAmounts(igst_minor=100, cgst_minor=50, sgst_minor=50)
        result = domain.utilise_itc(available, liability)
        assert result.lines == ()
        assert result.cash_payable == liability


# --------------------------------------------------------------------- blocked credit


class TestEligibility:
    @pytest.mark.parametrize("category", sorted(domain.ITC_EXPENSE_CATEGORIES))
    def test_general_is_the_only_eligible_category(self, category: str) -> None:
        assert domain.is_itc_eligible(category) == (category not in domain.BLOCKED_ITC_CATEGORIES)

    def test_general_is_always_eligible(self) -> None:
        assert domain.is_itc_eligible("general") is True

    @pytest.mark.parametrize("category", sorted(domain.BLOCKED_ITC_CATEGORIES))
    def test_a_blocked_category_accrues_nothing_across_every_head(self, category: str) -> None:
        document = domain.TaxHeadAmounts(igst_minor=500, cgst_minor=250, sgst_minor=250)
        assert domain.itc_eligible_amounts(document, category=category) == domain.ZERO_ITC

    def test_an_eligible_category_accrues_the_document_in_full(self) -> None:
        document = domain.TaxHeadAmounts(igst_minor=500, cgst_minor=250, sgst_minor=250)
        assert domain.itc_eligible_amounts(document, category="general") == document


class TestPostingKindForTransition:
    @pytest.mark.parametrize(
        "from_status,to_status",
        [(f, t) for f in domain.IMS_STATUSES for t in domain.IMS_STATUSES],
    )
    def test_only_a_move_across_the_accepted_boundary_posts_anything(
        self, from_status: str, to_status: str
    ) -> None:
        kind = domain.itc_posting_kind_for_transition(from_status=from_status, to_status=to_status)
        if to_status == "accepted" and from_status != "accepted":
            assert kind == "accrual"
        elif from_status == "accepted" and to_status != "accepted":
            assert kind == "reversal"
        else:
            assert kind is None

    def test_accepted_to_accepted_posts_nothing(self) -> None:
        assert (
            domain.itc_posting_kind_for_transition(from_status="accepted", to_status="accepted")
            is None
        )


# --------------------------------------------------------------------- GSTR-3B assembly


def _line(taxable: int, rate: Decimal, *, intra: bool) -> domain.ReturnLine:
    split = domain.split_gst(taxable, rate, intra_state=intra)
    return domain.ReturnLine(
        hsn_sac="72085110",
        description="line",
        quantity=Decimal(1),
        rate_pct=rate,
        taxable_minor=taxable,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
    )


def _doc(
    *,
    number: str,
    supply_type: str,
    taxable: int,
    rate: Decimal = Decimal(18),
    intra: bool = True,
    buyer_gstin: str | None = "27AAECO9012H1ZG",
    place_of_supply: str = "27",
) -> domain.ReturnDocument:
    lines = (_line(taxable, rate, intra=intra),)
    tax = domain.ZERO_TAX
    for line in lines:
        tax = tax + line.split
    return domain.ReturnDocument(
        id=uuid.uuid4(),
        number=number,
        document_type="invoice",
        issued_date=date(2026, 8, 12),
        supply_type=supply_type,
        place_of_supply=place_of_supply,
        reverse_charge=False,
        buyer_gstin=buyer_gstin,
        buyer_name="Buyer",
        taxable_minor=taxable,
        total_minor=taxable + tax.total_minor,
        status="sent",
        corrects_number=None,
        lines=lines,
    )


PERIOD = "2026-08"
GSTIN = "27AABCA9012D1Z5"


class TestGstr3bReconcilesWithGstr1:
    def test_a_book_of_domestic_and_export_documents_reconciles(self) -> None:
        documents = [
            _doc(number="INV-1", supply_type="b2b", taxable=1_00_000, intra=True),
            _doc(
                number="INV-2",
                supply_type="b2b",
                taxable=2_00_000,
                intra=False,
                place_of_supply="24",
            ),
            _doc(number="INV-3", supply_type="export", taxable=5_00_000, buyer_gstin=None),
            _doc(number="INV-4", supply_type="b2c", taxable=1_000, buyer_gstin=None),
        ]
        gstr1 = domain.assemble_gstr1(documents, period=PERIOD, gstin=GSTIN)
        gstr3b = domain.assemble_gstr3b(
            documents,
            period=PERIOD,
            gstin=GSTIN,
            itc_brought_forward=domain.ZERO_ITC,
            itc_accrued=domain.ZERO_ITC,
            itc_reversed=domain.ZERO_ITC,
            itc_blocked=domain.ZERO_ITC,
        )
        assert domain.gstr3b_reconciles_with_gstr1(gstr3b, gstr1.totals)
        assert gstr3b.document_count == gstr1.totals.document_count

    def test_export_lands_in_zero_rated_and_domestic_in_taxable_supplies(self) -> None:
        documents = [
            _doc(number="INV-1", supply_type="b2b", taxable=1_00_000, intra=True),
            _doc(number="INV-2", supply_type="export", taxable=5_00_000, buyer_gstin=None),
        ]
        gstr3b = domain.assemble_gstr3b(
            documents,
            period=PERIOD,
            gstin=GSTIN,
            itc_brought_forward=domain.ZERO_ITC,
            itc_accrued=domain.ZERO_ITC,
            itc_reversed=domain.ZERO_ITC,
            itc_blocked=domain.ZERO_ITC,
        )
        assert gstr3b.outward.outward_taxable_minor == 1_00_000
        assert gstr3b.outward.zero_rated_taxable_minor == 5_00_000
        assert gstr3b.outward.nil_rated_taxable_minor == 0
        assert gstr3b.outward.reverse_charge_inward_taxable_minor == 0
        assert gstr3b.outward.non_gst_taxable_minor == 0

    def test_a_malformed_period_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a return period"):
            domain.assemble_gstr3b(
                [],
                period="not-a-period",
                gstin=GSTIN,
                itc_brought_forward=domain.ZERO_ITC,
                itc_accrued=domain.ZERO_ITC,
                itc_reversed=domain.ZERO_ITC,
                itc_blocked=domain.ZERO_ITC,
            )


class TestGstr3bPayment:
    def test_liability_is_netted_against_brought_forward_and_this_periods_credit(self) -> None:
        documents = [_doc(number="INV-1", supply_type="b2b", taxable=1_00_000, intra=True)]
        # 18% on 1,00,000 intra-state = 9,000 CGST + 9,000 SGST.
        gstr3b = domain.assemble_gstr3b(
            documents,
            period=PERIOD,
            gstin=GSTIN,
            itc_brought_forward=domain.TaxHeadAmounts(igst_minor=20_000),
            itc_accrued=domain.TaxHeadAmounts(cgst_minor=1_000, sgst_minor=1_000),
            itc_reversed=domain.ZERO_ITC,
            itc_blocked=domain.ZERO_ITC,
        )
        assert gstr3b.payment.liability == domain.TaxHeadAmounts(cgst_minor=9_000, sgst_minor=9_000)
        # IGST brought forward covers both CGST and SGST liability in full — none payable in cash.
        assert gstr3b.payment.cash_payable == domain.ZERO_ITC
        heads = {(entry.head, entry.against) for entry in gstr3b.payment.utilisation.lines}
        assert ("igst", "cgst") in heads
        assert ("igst", "sgst") in heads


# --------------------------------------------------------------------- TaxHeadAmounts arithmetic


class TestTaxHeadAmounts:
    def test_addition_is_componentwise(self) -> None:
        a = domain.TaxHeadAmounts(igst_minor=1, cgst_minor=2, sgst_minor=3, cess_minor=4)
        b = domain.TaxHeadAmounts(igst_minor=10, cgst_minor=20, sgst_minor=30, cess_minor=40)
        assert a + b == domain.TaxHeadAmounts(
            igst_minor=11, cgst_minor=22, sgst_minor=33, cess_minor=44
        )

    def test_subtraction_is_componentwise(self) -> None:
        a = domain.TaxHeadAmounts(igst_minor=10, cgst_minor=20, sgst_minor=30, cess_minor=40)
        b = domain.TaxHeadAmounts(igst_minor=1, cgst_minor=2, sgst_minor=3, cess_minor=4)
        assert a - b == domain.TaxHeadAmounts(
            igst_minor=9, cgst_minor=18, sgst_minor=27, cess_minor=36
        )

    def test_total_minor_sums_every_head(self) -> None:
        a = domain.TaxHeadAmounts(igst_minor=1, cgst_minor=2, sgst_minor=3, cess_minor=4)
        assert a.total_minor == 10

    def test_for_head_reads_the_named_column(self) -> None:
        a = domain.TaxHeadAmounts(igst_minor=1, cgst_minor=2, sgst_minor=3, cess_minor=4)
        for head, expected in zip(domain.ITC_TAX_HEAD_ORDER, (1, 2, 3, 4), strict=True):
            assert a.for_head(head) == expected
