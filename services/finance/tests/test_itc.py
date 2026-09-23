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
        list(itertools.product(AMOUNTS, repeat=6))[::41],  # a spread, not the full 5**6 grid
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
        list(itertools.product(AMOUNTS, repeat=6))[::41],
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
        list(itertools.product(AMOUNTS, repeat=6))[::41],
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
        list(itertools.product(AMOUNTS, repeat=6))[::41],
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
        list(itertools.product(AMOUNTS, repeat=6))[::41],
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

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(AMOUNTS, repeat=6))[::41],
    )
    def test_any_cgst_or_sgst_credit_applied_at_all_means_igst_credit_is_already_zero(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        """The stronger ordering property: it is not only crossing *into* IGST liability that
        must wait for IGST credit to be exhausted — CGST or SGST credit paying its *own* native
        liability must wait too, because Rule 88A requires IGST credit be completely spent before
        CGST or SGST credit is used **at all**, not merely before it reaches the other's liability
        or IGST's own."""
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        any_cgst_or_sgst_applied = any(line.head in ("cgst", "sgst") for line in result.lines)
        if any_cgst_or_sgst_applied:
            assert result.credit_remaining.igst_minor == 0


#: A small grid for the brute-force optimum check — large enough to include a value that does not
#: divide evenly into the others (13), and one specifically called out in review (130), small
#: enough that trying every split of the "flexible" IGST pool stays fast.
_BRUTE_FORCE_AMOUNTS = [0, 1, 5, 13, 130]


def _brute_force_minimum_cash(
    available: domain.TaxHeadAmounts, liability: domain.TaxHeadAmounts
) -> int:
    """The true minimum total cash payable under Rule 88A.

    Rule 88A itself fixes most of the allocation: IGST credit pays its own liability first (not a
    choice), and once CGST or SGST liability is reduced by whatever IGST credit reaches it, native
    CGST/SGST credit pays down what remains of its own liability and any leftover falls through to
    IGST liability — also not a choice, since CGST can reach only CGST-then-IGST and SGST only
    SGST-then-IGST. The **one** real choice is how IGST's leftover credit (what remains once its
    own liability is paid) is split between CGST and SGST liability, which is what this tries
    every way of, including a way that "wastes" more of the split than either liability needs —
    trying only up to each liability's own size would silently assume the optimal split never
    overshoots, which is exactly the kind of assumption a brute force exists to not make.

    A reference implementation independent of `utilise_itc`'s own code path, so a test comparing
    the two is a real check on optimality rather than the function agreeing with itself.
    """
    igst_own = min(available.igst_minor, liability.igst_minor)
    igst_liab_after_own = liability.igst_minor - igst_own
    igst_flex = available.igst_minor - igst_own

    best = None
    for to_cgst in range(0, igst_flex + 1):
        to_sgst = igst_flex - to_cgst
        cgst_liab_1 = max(0, liability.cgst_minor - to_cgst)
        sgst_liab_1 = max(0, liability.sgst_minor - to_sgst)

        cgst_own = min(available.cgst_minor, cgst_liab_1)
        cgst_liab_2 = cgst_liab_1 - cgst_own
        cgst_flex = available.cgst_minor - cgst_own

        sgst_own = min(available.sgst_minor, sgst_liab_1)
        sgst_liab_2 = sgst_liab_1 - sgst_own
        sgst_flex = available.sgst_minor - sgst_own

        igst_liab_2 = max(0, igst_liab_after_own - cgst_flex - sgst_flex)

        total_cash = igst_liab_2 + cgst_liab_2 + sgst_liab_2
        if best is None or total_cash < best:
            best = total_cash
    assert best is not None
    return best


#: A handful of representative GST scenarios (credit/liability across igst/cgst/sgst) crossed with
#: a small cess grid — cess is independent of the other three heads throughout, so this checks
#: that independence directly rather than re-running the full GST grid alongside it.
_GST_SCENARIOS = [
    (0, 0, 0, 0, 0, 0),
    (100, 0, 0, 0, 100, 100),  # the review counterexample
    (100, 100, 0, 100, 100, 100),
    (0, 50, 50, 100, 0, 0),
]


class TestUtilisationIsOptimal:
    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab",
        list(itertools.product(_BRUTE_FORCE_AMOUNTS, repeat=6))[::17],
    )
    def test_cash_payable_equals_the_brute_force_minimum(
        self, igst_credit, cgst_credit, sgst_credit, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        actual = (
            result.cash_payable.igst_minor
            + result.cash_payable.cgst_minor
            + result.cash_payable.sgst_minor
        )
        assert actual == _brute_force_minimum_cash(available, liability)

    def test_the_counterexample_from_review_pays_nothing_in_cash(self) -> None:
        """avail igst=100, cgst=100, sgst=0; liab cgst=100, sgst=100 — the greedy order used to
        leave 100 in cash with 100 of CGST credit stranded; paying SGST's shortfall from IGST and
        letting native CGST credit cover CGST leaves nothing in cash."""
        available = domain.TaxHeadAmounts(igst_minor=100, cgst_minor=100)
        liability = domain.TaxHeadAmounts(cgst_minor=100, sgst_minor=100)
        result = domain.utilise_itc(available, liability)
        assert result.cash_payable == domain.ZERO_ITC
        assert {(line.head, line.against, line.amount_minor) for line in result.lines} == {
            ("igst", "sgst", 100),
            ("cgst", "cgst", 100),
        }

    @pytest.mark.parametrize(
        "igst_credit,cgst_credit,sgst_credit,igst_liab,cgst_liab,sgst_liab", _GST_SCENARIOS
    )
    @pytest.mark.parametrize(
        "cess_credit,cess_liab", list(itertools.product(_BRUTE_FORCE_AMOUNTS, repeat=2))
    )
    def test_cess_never_changes_the_gst_optimum_or_vice_versa(
        self,
        igst_credit,
        cgst_credit,
        sgst_credit,
        igst_liab,
        cgst_liab,
        sgst_liab,
        cess_credit,
        cess_liab,
    ) -> None:
        """Cess is independent of the other three heads throughout, so adding it to the inputs
        must neither change the GST-side optimum nor let GST credit leak into cess or back."""
        available = domain.TaxHeadAmounts(
            igst_minor=igst_credit,
            cgst_minor=cgst_credit,
            sgst_minor=sgst_credit,
            cess_minor=cess_credit,
        )
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab, cess_minor=cess_liab
        )
        result = domain.utilise_itc(available, liability)
        gst_cash = (
            result.cash_payable.igst_minor
            + result.cash_payable.cgst_minor
            + result.cash_payable.sgst_minor
        )
        gst_only_available = domain.TaxHeadAmounts(
            igst_minor=igst_credit, cgst_minor=cgst_credit, sgst_minor=sgst_credit
        )
        gst_only_liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        assert gst_cash == _brute_force_minimum_cash(gst_only_available, gst_only_liability)
        assert result.cash_payable.cess_minor == max(0, cess_liab - cess_credit)
        assert not any(line.head == "cess" and line.against != "cess" for line in result.lines)
        assert not any(line.against == "cess" and line.head != "cess" for line in result.lines)


class TestUtilisationWithNegativeInputs:
    """A heavy-credit-note period can report negative liability at a head; a ledger whose
    reversals have legitimately outrun its accruals (a credit note's own negative accrual, see
    `services.record_ims_decision`) can report negative available credit. Neither is spendable."""

    @pytest.mark.parametrize(
        "igst_liab,cgst_liab,sgst_liab",
        list(itertools.product([-500, -1, 0, 1, 500], repeat=3)),
    )
    def test_negative_liability_is_clamped_and_reported_as_excess_never_as_cash_or_credit(
        self, igst_liab, cgst_liab, sgst_liab
    ) -> None:
        available = domain.TaxHeadAmounts()
        liability = domain.TaxHeadAmounts(
            igst_minor=igst_liab, cgst_minor=cgst_liab, sgst_minor=sgst_liab
        )
        result = domain.utilise_itc(available, liability)
        assert result.cash_payable.igst_minor == max(0, igst_liab)
        assert result.cash_payable.cgst_minor == max(0, cgst_liab)
        assert result.cash_payable.sgst_minor == max(0, sgst_liab)
        assert result.excess_liability.igst_minor == max(0, -igst_liab)
        assert result.excess_liability.cgst_minor == max(0, -cgst_liab)
        assert result.excess_liability.sgst_minor == max(0, -sgst_liab)
        assert result.credit_remaining == domain.ZERO_ITC
        assert result.lines == ()

    def test_negative_available_credit_spends_nothing(self) -> None:
        available = domain.TaxHeadAmounts(igst_minor=-1_000)
        liability = domain.TaxHeadAmounts(igst_minor=500)
        result = domain.utilise_itc(available, liability)
        assert result.lines == ()
        assert result.cash_payable == domain.TaxHeadAmounts(igst_minor=500)
        assert result.credit_remaining == domain.ZERO_ITC

    def test_the_counterexample_from_review_produces_no_utilisation_row(self) -> None:
        """avail igst=100, liab cgst=-50, sgst=-50 used to leave igst credit at 200 (having
        *subtracted* a negative liability instead of ignoring it) and post a fabricated +100
        utilisation row. Both liabilities are clamped to zero; nothing is applied; the excess is
        reported, not spent."""
        available = domain.TaxHeadAmounts(igst_minor=100)
        liability = domain.TaxHeadAmounts(cgst_minor=-50, sgst_minor=-50)
        result = domain.utilise_itc(available, liability)
        assert result.lines == ()
        assert result.credit_remaining == domain.TaxHeadAmounts(igst_minor=100)
        assert result.cash_payable == domain.ZERO_ITC
        assert result.excess_liability == domain.TaxHeadAmounts(cgst_minor=50, sgst_minor=50)


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


class TestSplitPositiveNegative:
    def test_splits_headwise_and_the_parts_sum_back_to_the_input(self) -> None:
        amounts = domain.TaxHeadAmounts(
            igst_minor=500, cgst_minor=-300, sgst_minor=0, cess_minor=-1
        )
        positive, negative = domain.split_positive_negative(amounts)
        assert positive == domain.TaxHeadAmounts(igst_minor=500)
        assert negative == domain.TaxHeadAmounts(cgst_minor=-300, cess_minor=-1)
        assert positive + negative == amounts

    def test_a_zero_delta_splits_into_two_zeros(self) -> None:
        positive, negative = domain.split_positive_negative(domain.ZERO_ITC)
        assert positive == domain.ZERO_ITC
        assert negative == domain.ZERO_ITC


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
    document_type: str = "invoice",
    reverse_charge: bool = False,
    corrects_number: str | None = None,
) -> domain.ReturnDocument:
    lines = (_line(taxable, rate, intra=intra),)
    tax = domain.ZERO_TAX
    for line in lines:
        tax = tax + line.split
    return domain.ReturnDocument(
        id=uuid.uuid4(),
        number=number,
        document_type=document_type,
        issued_date=date(2026, 8, 12),
        supply_type=supply_type,
        place_of_supply=place_of_supply,
        reverse_charge=reverse_charge,
        buyer_gstin=buyer_gstin,
        buyer_name="Buyer",
        taxable_minor=taxable,
        total_minor=taxable + tax.total_minor,
        status="sent",
        corrects_number=corrects_number,
        lines=lines,
    )


PERIOD = "2026-08"
GSTIN = "27AABCA9012D1Z5"


class TestGstr3bReconcilesWithGstr1:
    def test_a_book_of_domestic_and_export_documents_reconciles(self) -> None:
        """A real cross-check, not a tautology built from itself: a credit note (negative,
        against an earlier invoice), a reverse-charge supply (excluded from `output_tax` but not
        from what GSTR-1 declares), an export (zero-rated) and aggregated B2C-small supplies,
        none landing in the same Table 3.1 row, still reconcile to the paisa."""
        documents = [
            _doc(number="INV-1", supply_type="b2b", taxable=1_00_000, intra=True),
            _doc(
                number="CN-1",
                supply_type="b2b",
                taxable=-20_000,
                intra=True,
                document_type="credit_note",
                corrects_number="INV-1",
            ),
            _doc(
                number="INV-2",
                supply_type="b2b",
                taxable=2_00_000,
                intra=False,
                place_of_supply="24",
            ),
            _doc(number="INV-3", supply_type="export", taxable=5_00_000, buyer_gstin=None),
            _doc(number="INV-4", supply_type="b2c", taxable=1_000, buyer_gstin=None),
            _doc(
                number="INV-5",
                supply_type="b2b",
                taxable=3_00_000,
                intra=True,
                reverse_charge=True,
            ),
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
        # Not vacuous: the reverse-charge document really is excluded from the liability GSTR-3B
        # would collect cash against, even though it counts toward the reconciled declaration.
        assert gstr3b.outward.output_tax != (
            gstr3b.outward.taxable_supplies
            + gstr3b.outward.zero_rated
            + gstr3b.outward.outward_reverse_charge
        )
        assert gstr3b.outward.outward_reverse_charge_taxable_minor == 3_00_000

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
