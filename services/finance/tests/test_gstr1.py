"""GSTR-1 generation against real PostgreSQL — the assembly, and whether it foots.

A return that does not foot to the invoices it was made of is worse than no return: it is a
mismatch notice months later, reconciled by hand against a book nobody can reconstruct. So the
central assertion here is arithmetic, made three independent ways over the same book —

* every **section** sums to the lines of the documents in it;
* the **HSN summary**, assembled in parallel from the same lines, agrees with the sections;
* the **one-line totals**, summed straight off the lines, agree with both.

Three paths to one number. Building any of them from another would make them agree by
construction and hide exactly the disagreement the cross-foot exists to catch, which is why
`domain._hsn_summary` and `domain._totals_of` each start from the lines rather than from the
section entries.

The rest is the frozen snapshot: a *filed* return is what was reported, so an invoice corrected
afterwards has to show as a **difference** and must not be able to reach back into the row. The
last classes go to the database directly and try.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update

from app import domain, selectors, services
from app.models import GstReturn, GstReturnTransition, OutboxEvent
from app.schemas import (
    CustomerCreate,
    FileReturnIn,
    GenerateReturnIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import admin_ctx, ctx_for

pytestmark = pytest.mark.integration

RETURNS = "/api/v1/finance/gst/returns"
PERIOD = "2026-08"

#: The seller: Maharashtra (27). Checksum-valid, like everything else this service stores.
SELLER = "27AABCA9012D1Z5"
#: A Maharashtra buyer — same state as the seller, so CGST+SGST.
BUYER_SAME_STATE = "27AAECO9012H1ZG"
#: A Gujarat buyer — different state, so IGST.
BUYER_OTHER_STATE = "24AABCM1234F1Z3"


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx, gstin: str = SELLER):
    return await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Private Limited"),
    )


async def _customer(session, ctx, *, gstin=None, state="Maharashtra", name="Buyer", **flags):
    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name=name,
            contact_name="Contact",
            email="ap@example.in",
            gstin=gstin,
            city="Pune",
            state=state,
            **flags,
        ),
    )


def _payload(customer_id, number: str, **overrides) -> InvoiceCreate:
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 8, 12),
        "due_date": date(2026, 9, 11),
        "tax_rate_pct": 18,
        "lines": [
            InvoiceLineCreate(
                description="Hot-rolled coil",
                quantity=10,
                unit_price_minor=250_000,
                hsn_sac="72085110",
            )
        ],
    }
    payload.update(overrides)
    return InvoiceCreate(**payload)


def _line(**overrides) -> InvoiceLineCreate:
    fields = {
        "description": "Hot-rolled coil",
        "quantity": 1,
        "unit_price_minor": 100_000,
        "hsn_sac": "72085110",
    }
    fields.update(overrides)
    return InvoiceLineCreate(**fields)


def _doc(
    *,
    number="INV-1",
    document_type="invoice",
    supply_type="b2b",
    place_of_supply="27",
    buyer_gstin=BUYER_SAME_STATE,
    status="sent",
    lines=((100_000, Decimal(18), "72085110"),),
    intra=True,
    corrects_number=None,
    issued=date(2026, 8, 12),
) -> domain.ReturnDocument:
    """A pure `ReturnDocument` with its splits already computed, the way a frozen invoice carries
    them. The splits come from `domain.split_gst` so a hand-built fixture cannot disagree with
    the one calculation the platform has."""
    built = []
    taxable = 0
    tax = domain.ZERO_TAX
    for amount, rate, hsn in lines:
        split = domain.split_gst(amount, rate, intra_state=intra)
        taxable += amount
        tax = tax + split
        built.append(
            domain.ReturnLine(
                hsn_sac=hsn,
                description="line",
                quantity=Decimal(1),
                rate_pct=rate,
                taxable_minor=amount,
                cgst_minor=split.cgst_minor,
                sgst_minor=split.sgst_minor,
                igst_minor=split.igst_minor,
            )
        )
    return domain.ReturnDocument(
        id=uuid.uuid4(),
        number=number,
        document_type=document_type,
        issued_date=issued,
        supply_type=supply_type,
        place_of_supply=place_of_supply,
        reverse_charge=False,
        buyer_gstin=buyer_gstin,
        buyer_name="Buyer",
        taxable_minor=taxable,
        total_minor=taxable + tax.total_minor,
        status=status,
        corrects_number=corrects_number,
        lines=tuple(built),
    )


def _line_totals(documents) -> domain.ReturnTotals:
    """The book's own figures, summed from the lines with no reference to the assembly at all.

    Written out here rather than calling `domain._totals_of` on purpose: a test that checks the
    code against itself checks nothing.
    """
    taxable = cgst = sgst = igst = lines = 0
    for doc in documents:
        for line in doc.lines:
            taxable += line.taxable_minor
            cgst += line.cgst_minor
            sgst += line.sgst_minor
            igst += line.igst_minor
            lines += 1
    return domain.ReturnTotals(
        taxable_minor=taxable,
        cgst_minor=cgst,
        sgst_minor=sgst,
        igst_minor=igst,
        document_count=len(documents),
        line_count=lines,
    )


# --------------------------------------------------------------------- the assembly


class TestSectionsAreExhaustiveAndExclusive:
    """Every reported document lands in exactly one section. This is what makes footing provable:
    if a document could fall into two, figures would double; into none, they would vanish."""

    @pytest.mark.parametrize(
        "document,expected",
        [
            (_doc(), "b2b"),
            (_doc(supply_type="sez", buyer_gstin=BUYER_OTHER_STATE, intra=False), "b2b"),
            (_doc(supply_type="export", buyer_gstin=None, intra=False), "exp"),
            (
                _doc(supply_type="export", buyer_gstin=BUYER_OTHER_STATE, intra=False),
                "exp",
            ),
            (_doc(document_type="credit_note"), "cdnr"),
            (
                _doc(document_type="credit_note", supply_type="b2c", buyer_gstin=None),
                "cdnur",
            ),
            (_doc(supply_type="b2c", buyer_gstin=None), "b2cs"),
        ],
    )
    def test_each_document_has_exactly_one_section(self, document, expected) -> None:
        assert domain.gstr1_section_for(document) == expected

    def test_an_export_is_exp_even_when_the_buyer_is_registered(self) -> None:
        """An export carries no recipient GSTIN the return can file it against, so it is EXP
        whoever the buyer is — a registered exporter's document must not fall into B2B."""
        document = _doc(supply_type="export", buyer_gstin=BUYER_OTHER_STATE, intra=False)
        assert domain.gstr1_section_for(document) == "exp"

    def test_every_reported_document_reaches_a_section(self) -> None:
        """The exhaustiveness claim, asserted rather than assumed: sum the section counts and get
        the document count back."""
        documents = [
            _doc(number="INV-1"),
            _doc(number="INV-2", supply_type="b2c", buyer_gstin=None),
            _doc(
                number="INV-3",
                supply_type="b2c",
                buyer_gstin=None,
                place_of_supply="24",
                intra=False,
                lines=((300_000_00, Decimal(18), "72085110"),),
            ),
            _doc(number="CN-1", document_type="credit_note"),
            _doc(
                number="CN-2",
                document_type="credit_note",
                supply_type="b2c",
                buyer_gstin=None,
            ),
            _doc(number="EXP-1", supply_type="export", buyer_gstin=None, intra=False),
        ]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        placed = (
            len(ret.b2b)
            + len(ret.b2cl)
            + len(ret.cdnr)
            + len(ret.cdnur)
            + len(ret.exp)
            + sum(e.document_count for e in ret.b2cs)
        )
        assert placed == len(documents)


class TestSectionsFootToTheLines:
    """The central property. Assembled figures are sums of the *stored* line splits, so they foot
    to the underlying invoice lines exactly — never approximately, and never after a correction
    step somebody has to remember to run."""

    def _book(self) -> list[domain.ReturnDocument]:
        return [
            _doc(number="INV-1", lines=((123_457, Decimal(18), "72085110"),)),
            _doc(
                number="INV-2",
                lines=(
                    (99_999, Decimal(12), "72085110"),
                    (1, Decimal("0.25"), "71131900"),
                    (7_777, Decimal(28), "87089900"),
                ),
            ),
            _doc(
                number="INV-3",
                buyer_gstin=BUYER_OTHER_STATE,
                place_of_supply="24",
                intra=False,
                lines=((555_555, Decimal(5), "72085110"),),
            ),
            _doc(
                number="INV-4",
                supply_type="b2c",
                buyer_gstin=None,
                lines=((13_333, Decimal(18), "72085110"),),
            ),
            _doc(
                number="CN-1",
                document_type="credit_note",
                corrects_number="INV-1",
                lines=((-123_457, Decimal(18), "72085110"),),
            ),
            _doc(
                number="EXP-1",
                supply_type="export",
                buyer_gstin=None,
                intra=False,
                lines=((2_500_000, Decimal(0), "72085110"),),
            ),
        ]

    def test_the_sections_sum_to_the_lines(self) -> None:
        documents = self._book()
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        expected = _line_totals(documents)

        taxable = cgst = sgst = igst = 0
        for name in ("b2b", "b2cl", "b2cs", "cdnr", "cdnur", "exp"):
            section = domain.section_totals(name, ret)
            taxable += section.taxable_minor
            cgst += section.cgst_minor
            sgst += section.sgst_minor
            igst += section.igst_minor

        assert (taxable, cgst, sgst, igst) == (
            expected.taxable_minor,
            expected.cgst_minor,
            expected.sgst_minor,
            expected.igst_minor,
        )

    def test_the_hsn_summary_agrees_with_the_sections(self) -> None:
        """Two independent declarations of one book. The portal cross-foots them; so does this."""
        ret = domain.assemble_gstr1(self._book(), period=PERIOD, gstin=SELLER)
        hsn = domain.section_totals("hsn", ret)
        assert (
            hsn.taxable_minor,
            hsn.cgst_minor,
            hsn.sgst_minor,
            hsn.igst_minor,
        ) == (
            ret.totals.taxable_minor,
            ret.totals.cgst_minor,
            ret.totals.sgst_minor,
            ret.totals.igst_minor,
        )

    def test_the_one_line_totals_agree_with_the_lines(self) -> None:
        documents = self._book()
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        assert ret.totals == _line_totals(documents)

    def test_the_declared_total_is_taxable_plus_tax(self) -> None:
        ret = domain.assemble_gstr1(self._book(), period=PERIOD, gstin=SELLER)
        assert ret.totals.total_minor == ret.totals.taxable_minor + ret.totals.tax_minor

    @pytest.mark.parametrize(
        "amount",
        [1, 7, 99, 101, 12_345, 999_999, 2_147_483_648, 9_999_999_999],
    )
    @pytest.mark.parametrize("rate", ["0.25", "3", "5", "12", "18", "28"])
    def test_every_amount_and_band_foots(self, amount: int, rate: str) -> None:
        """Property-style, across the odd-paisa cases the halving rule exists for.

        One document, one line, both split shapes. A return that foots for round numbers and not
        for 12,345 paise at 18 % is a return that foots in a demo and not in August.
        """
        for intra in (True, False):
            document = _doc(
                lines=((amount, Decimal(rate), "72085110"),),
                intra=intra,
                buyer_gstin=BUYER_SAME_STATE if intra else BUYER_OTHER_STATE,
                place_of_supply="27" if intra else "24",
            )
            ret = domain.assemble_gstr1([document], period=PERIOD, gstin=SELLER)
            b2b = domain.section_totals("b2b", ret)
            hsn = domain.section_totals("hsn", ret)
            expected_tax = domain.gst_amount_minor(amount, Decimal(rate))
            assert b2b.taxable_minor == amount
            assert b2b.tax_minor == expected_tax
            assert hsn.tax_minor == expected_tax
            assert ret.totals.tax_minor == expected_tax


class TestTheB2cBoundary:
    """B2C-large is inter-state, unregistered and **more than** 2,50,000 rupees. Exactly on the
    threshold is B2C-small — a boundary implemented as `>=` misfiles every invoice that lands on
    it, which is invisible until a notice arrives."""

    def _b2c(self, total_minor: int, *, inter_state: bool) -> domain.ReturnDocument:
        # Work back from the wanted invoice value at 0 %, so the boundary is tested at the exact
        # rupee rather than at whatever a rate happens to round to.
        return _doc(
            supply_type="b2c",
            buyer_gstin=None,
            place_of_supply="24" if inter_state else "27",
            intra=not inter_state,
            lines=((total_minor, Decimal(0), "72085110"),),
        )

    def _section(self, document) -> str:
        # The seller sits in Maharashtra (27), which is what decides inter-state for a nil-rated
        # supply — there is no tax split to read the answer off at 0 %.
        return domain.gstr1_section_for(document, seller_state_code="27")

    def test_exactly_on_the_threshold_is_b2c_small(self) -> None:
        document = self._b2c(domain.B2C_LARGE_THRESHOLD_MINOR, inter_state=True)
        assert document.total_minor == 250_000_00
        assert self._section(document) == "b2cs"

    def test_one_paisa_over_the_threshold_is_b2c_large(self) -> None:
        document = self._b2c(domain.B2C_LARGE_THRESHOLD_MINOR + 1, inter_state=True)
        assert self._section(document) == "b2cl"

    def test_one_paisa_under_the_threshold_is_b2c_small(self) -> None:
        document = self._b2c(domain.B2C_LARGE_THRESHOLD_MINOR - 1, inter_state=True)
        assert self._section(document) == "b2cs"

    def test_an_intra_state_supply_is_never_b2c_large_however_big(self) -> None:
        """All three conditions are required. A 50-lakh counter sale inside the state is still
        B2CS: B2CL exists to report *inter-state* consumption, state by state."""
        document = self._b2c(50_000_000_00, inter_state=False)
        assert self._section(document) == "b2cs"

    def test_a_taxed_supply_reads_its_state_off_the_split_not_the_codes(self) -> None:
        """Where there *is* tax, the heads the document was actually taxed under are the fact the
        return declares — so a large IGST B2C supply is B2CL even if the state codes were later
        re-registered underneath it."""
        document = _doc(
            supply_type="b2c",
            buyer_gstin=None,
            place_of_supply="24",
            intra=False,
            lines=((300_000_00, Decimal(18), "72085110"),),
        )
        assert domain.gstr1_section_for(document, seller_state_code="27") == "b2cl"

    def test_b2c_small_aggregates_by_place_of_supply_and_rate(self) -> None:
        documents = [
            _doc(
                number=f"INV-{i}",
                supply_type="b2c",
                buyer_gstin=None,
                place_of_supply=pos,
                intra=pos == "27",
                lines=((10_000, Decimal(rate), "72085110"),),
            )
            for i, (pos, rate) in enumerate(
                [("27", 18), ("27", 18), ("27", 12), ("24", 18), ("24", 18), ("24", 18)]
            )
        ]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        keys = {(e.place_of_supply, e.rate_pct, e.inter_state, e.document_count) for e in ret.b2cs}
        assert keys == {
            ("24", Decimal(18), True, 3),
            ("27", Decimal(12), False, 1),
            ("27", Decimal(18), False, 2),
        }
        # Six documents, three aggregated rows, and the figures still foot.
        assert domain.section_totals("b2cs", ret).taxable_minor == 60_000


class TestCreditNotesReverseHeadForHead:
    """A credit note is the platform's correction mechanism and GST's. It must reverse the invoice
    it corrects **per head**, or the return declares a paisa of CGST nobody owes."""

    def test_a_credit_note_reverses_its_invoice_exactly(self) -> None:
        invoice = _doc(number="INV-9", lines=((123_457, Decimal(18), "72085110"),))
        credit = _doc(
            number="CN-9",
            document_type="credit_note",
            corrects_number="INV-9",
            lines=((-123_457, Decimal(18), "72085110"),),
        )
        ret = domain.assemble_gstr1([invoice, credit], period=PERIOD, gstin=SELLER)
        b2b = domain.section_totals("b2b", ret)
        cdnr = domain.section_totals("cdnr", ret)

        assert cdnr.taxable_minor == -b2b.taxable_minor
        assert cdnr.cgst_minor == -b2b.cgst_minor
        assert cdnr.sgst_minor == -b2b.sgst_minor
        assert cdnr.igst_minor == -b2b.igst_minor
        # And the period nets to nothing at all — the odd paisa included.
        assert ret.totals.taxable_minor == 0
        assert ret.totals.tax_minor == 0

    def test_cdnr_declares_the_number_it_corrects(self) -> None:
        """CDNR is filed against the original document, so the number travels with the note."""
        credit = _doc(number="CN-9", document_type="credit_note", corrects_number="INV-9")
        ret = domain.assemble_gstr1([credit], period=PERIOD, gstin=SELLER)
        assert ret.cdnr[0].corrects_number == "INV-9"

    def test_a_credit_note_to_an_unregistered_buyer_is_cdnur(self) -> None:
        credit = _doc(
            number="CN-U", document_type="credit_note", supply_type="b2c", buyer_gstin=None
        )
        ret = domain.assemble_gstr1([credit], period=PERIOD, gstin=SELLER)
        assert [e.number for e in ret.cdnur] == ["CN-U"]
        assert ret.cdnr == ()


class TestTheHsnSummary:
    def test_lines_group_by_code_and_rate(self) -> None:
        documents = [
            _doc(
                number="INV-1",
                lines=(
                    (10_000, Decimal(18), "72085110"),
                    (20_000, Decimal(18), "72085110"),
                    (30_000, Decimal(12), "72085110"),
                    (40_000, Decimal(18), "87089900"),
                ),
            )
        ]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        grouped = {(e.hsn_sac, e.rate_pct): e.taxable_minor for e in ret.hsn}
        assert grouped == {
            ("72085110", Decimal(12)): 30_000,
            ("72085110", Decimal(18)): 30_000,
            ("87089900", Decimal(18)): 40_000,
        }

    def test_a_line_with_no_hsn_is_shown_rather_than_dropped(self) -> None:
        """A missing classification is a gap the return must show. Dropping it would make the
        summary foot while the book did not, which is the worst of both."""
        documents = [_doc(lines=((10_000, Decimal(18), None),))]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        assert [e.hsn_sac for e in ret.hsn] == [None]
        assert domain.section_totals("hsn", ret).taxable_minor == 10_000

    def test_quantities_sum_within_a_group(self) -> None:
        documents = [
            _doc(lines=((10_000, Decimal(18), "72085110"), (20_000, Decimal(18), "72085110")))
        ]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        assert ret.hsn[0].quantity == Decimal(2)


class TestTheDocumentSeries:
    def test_the_range_covers_every_number_issued(self) -> None:
        documents = [_doc(number=f"INV-{i:03d}") for i in (1, 5, 9)]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        series = {e.document_type: e for e in ret.docs}
        assert series["invoice"].from_number == "INV-001"
        assert series["invoice"].to_number == "INV-009"
        assert series["invoice"].total_count == 3

    def test_a_void_is_counted_as_cancelled_and_declared_nowhere_else(self) -> None:
        """The one section a void belongs in. Its number was consumed — a gap in a series is how
        an unreported invoice announces itself — but its figures are not a supply."""
        documents = [
            _doc(number="INV-1"),
            _doc(number="INV-2", status="void"),
        ]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        series = {e.document_type: e for e in ret.docs}
        assert series["invoice"].total_count == 2
        assert series["invoice"].cancelled_count == 1
        assert series["invoice"].net_count == 1
        # Its figures are absent everywhere else.
        assert [e.number for e in ret.b2b] == ["INV-1"]
        assert ret.totals.document_count == 1

    def test_invoices_and_credit_notes_declare_separate_series(self) -> None:
        documents = [_doc(number="INV-1"), _doc(number="CN-1", document_type="credit_note")]
        ret = domain.assemble_gstr1(documents, period=PERIOD, gstin=SELLER)
        assert {e.document_type for e in ret.docs} == {"invoice", "credit_note"}


class TestPeriodParsing:
    def test_a_malformed_period_is_refused_before_anything_is_assembled(self) -> None:
        with pytest.raises(ValueError, match="return period"):
            domain.assemble_gstr1([], period="August", gstin=SELLER)

    def test_a_month_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            domain.assemble_gstr1([], period="2026-13", gstin=SELLER)


# --------------------------------------------------------------------- end to end


async def _book(session, ctx) -> dict[str, object]:
    """A real August book through the real write path: B2B both split shapes, a B2C-small, a
    B2C-large, a credit note and an export."""
    await _register(session, ctx)
    same = await _customer(session, ctx, gstin=BUYER_SAME_STATE, name="Maharashtra Buyer")
    other = await _customer(
        session, ctx, gstin=BUYER_OTHER_STATE, state="Gujarat", name="Gujarat Buyer"
    )
    retail = await _customer(session, ctx, gstin=None, name="Counter Sale")

    inv1 = await services.create_invoice(session, ctx, _payload(same.id, "INV-A-1"))
    inv2 = await services.create_invoice(
        session,
        ctx,
        _payload(other.id, "INV-A-2", lines=[_line(unit_price_minor=333_333)]),
    )
    retail_small = await services.create_invoice(
        session,
        ctx,
        _payload(retail.id, "INV-A-3", lines=[_line(unit_price_minor=10_000)]),
    )
    credit = await services.create_invoice(
        session,
        ctx,
        _payload(
            same.id,
            "CN-A-1",
            document_type="credit_note",
            corrects_invoice_id=inv1.id,
            lines=[_line(quantity=-1, unit_price_minor=100_000)],
        ),
    )
    return {
        "invoices": [inv1, inv2, retail_small, credit],
        "customers": {"same": same, "other": other, "retail": retail},
    }


class TestGeneratingAReturn:
    async def test_the_snapshot_foots_to_the_invoice_lines_in_the_database(
        self, session, org_id
    ) -> None:
        """The end-to-end footing claim: real invoices, real splits, real snapshot.

        The expected figures come from summing `invoice_lines` directly, so this compares the
        return against the *table* rather than against another view of the same computation.
        """
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))

        async with session.begin():
            rows = (
                await session.execute(
                    text(
                        "SELECT coalesce(sum(l.amount_minor), 0), coalesce(sum(l.cgst_minor), 0),"
                        " coalesce(sum(l.sgst_minor), 0), coalesce(sum(l.igst_minor), 0)"
                        " FROM finance.invoice_lines l"
                        " JOIN finance.invoices i ON i.id = l.invoice_id"
                        " WHERE l.organization_id = :org AND i.status <> 'void'"
                        "   AND i.issued_date >= :start AND i.issued_date < :end"
                    ),
                    {
                        "org": org_id,
                        "start": date(2026, 8, 1),
                        "end": date(2026, 9, 1),
                    },
                )
            ).one()

        assert (
            ret.total_taxable_minor,
            ret.total_cgst_minor,
            ret.total_sgst_minor,
            ret.total_igst_minor,
        ) == tuple(int(v) for v in rows)

    async def test_the_stored_sections_foot_to_the_stored_totals(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            detail = await selectors.return_detail(session, org_id, ret)

        sections = detail.sections
        assert sections is not None
        taxable = sum(
            item.taxable_minor
            for group in (sections.b2b, sections.b2cl, sections.cdnr, sections.cdnur, sections.exp)
            for entry in group
            for item in entry.items
        ) + sum(e.taxable_minor for e in sections.b2cs)
        assert taxable == ret.total_taxable_minor
        assert sum(e.taxable_minor for e in sections.hsn) == ret.total_taxable_minor

    async def test_the_first_generation_appends_a_transition(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            trail = await selectors.return_transitions(session, org_id, ret.id)

        assert [(t.from_status, t.to_status) for t in trail] == [("draft", "generated")]
        assert trail[0].total_taxable_minor == ret.total_taxable_minor

    async def test_regenerating_replaces_the_snapshot_and_appends_a_second_transition(
        self, session, org_id
    ) -> None:
        """Before filing, the honest answer is what the book says now — so a regeneration is
        allowed, and it leaves a trail saying it happened."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            built = await _book(session, ctx)
        async with session.begin():
            first = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            before = first.total_taxable_minor

        async with session.begin():
            customers = built["customers"]  # type: ignore[index]
            await services.create_invoice(
                session,
                ctx,
                _payload(customers["same"].id, "INV-A-9", lines=[_line(unit_price_minor=50_000)]),
            )
        async with session.begin():
            second = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            trail = await selectors.return_transitions(session, org_id, second.id)

        assert second.id == first.id
        assert second.total_taxable_minor == before + 50_000
        assert [t.to_status for t in trail] == ["generated", "generated"]

    async def test_a_period_with_no_documents_generates_an_empty_return(
        self, session, org_id
    ) -> None:
        """Empty and unassembled are different answers, and the difference matters: "August is
        done, there was nothing" is not "August was never fetched"."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period="2026-03"))
        assert ret.status == "generated"
        assert ret.document_count == 0
        assert ret.sections is not None

    async def test_generating_without_a_registration_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="no GST registration"):
            async with session.begin():
                await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))

    async def test_an_unknown_return_type_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="Unknown return type"):
            async with session.begin():
                await services.generate_return(
                    session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr9")
                )

    async def test_a_malformed_period_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="return period"):
            async with session.begin():
                await services.generate_return(session, ctx, GenerateReturnIn(period="2026-8"))

    async def test_generating_announces_it(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_RETURN_GENERATED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["period"] == PERIOD
        assert events[0].payload["total_taxable_minor"] == ret.total_taxable_minor


class TestFilingAReturn:
    async def _generated(self, session, ctx) -> GstReturn:
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            return await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))

    async def test_filing_records_the_arn_and_appends_a_transition(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        ret = await self._generated(session, ctx)
        async with session.begin():
            filed = await services.file_return(
                session, ctx, ret.id, FileReturnIn(arn="AA270826000001B")
            )
            trail = await selectors.return_transitions(session, org_id, ret.id)

        assert filed.status == "filed"
        assert filed.arn == "AA270826000001B"
        assert filed.filed_at is not None
        assert [t.to_status for t in trail] == ["generated", "filed"]
        # The evidence row is self-contained: it carries the ARN and the totals as they stood.
        assert trail[-1].arn == "AA270826000001B"
        assert trail[-1].total_taxable_minor == ret.total_taxable_minor

    async def test_a_draft_cannot_be_filed(self, session, org_id) -> None:
        """Filing a period with no snapshot would be claiming something nobody assembled."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await _register(session, ctx)
            draft = GstReturn(
                organization_id=org_id,
                gst_registration_id=registration.id,
                gstin=registration.gstin,
                state_code=registration.state_code,
                period=PERIOD,
                return_type="gstr1",
                status="draft",
            )
            session.add(draft)
            await session.flush()
            draft_id = draft.id

        with pytest.raises(BusinessRuleProblem, match="generate it first"):
            async with session.begin():
                await services.file_return(session, ctx, draft_id, FileReturnIn(arn="AA1"))

    async def test_filing_twice_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        ret = await self._generated(session, ctx)
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA1"))
        with pytest.raises(ConflictProblem, match="already filed"):
            async with session.begin():
                await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA2"))

    async def test_an_empty_arn_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        ret = await self._generated(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="acknowledgement number"):
            async with session.begin():
                await services.file_return(session, ctx, ret.id, FileReturnIn(arn="   "))

    async def test_a_filed_period_cannot_be_regenerated(self, session, org_id) -> None:
        """The whole reason the snapshot is frozen. Regenerating over a filing would erase the
        only record of what the filing actually said."""
        ctx = admin_ctx(org_id)
        ret = await self._generated(session, ctx)
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA1"))
        with pytest.raises(ConflictProblem, match="read the difference instead"):
            async with session.begin():
                await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))


class TestTheDiff:
    async def test_an_unchanged_book_reports_no_difference(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
        async with session.begin():
            diff = await services.diff_return(session, ctx, ret.id)

        assert diff.changed is False
        assert diff.delta.taxable_minor == 0
        assert diff.delta.document_count == 0
        assert all(section.changed is False for section in diff.sections)

    async def test_a_later_credit_note_shows_as_a_difference(self, session, org_id) -> None:
        """The case the frozen snapshot exists for: August was filed, a correction arrives, and
        the answer is a *difference* — the filed return still says what it said."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            built = await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            snapshot_taxable = ret.total_taxable_minor
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA1"))

        invoices = built["invoices"]  # type: ignore[index]
        customers = built["customers"]  # type: ignore[index]
        async with session.begin():
            await services.create_invoice(
                session,
                ctx,
                _payload(
                    customers["same"].id,
                    "CN-A-2",
                    document_type="credit_note",
                    corrects_invoice_id=invoices[0].id,
                    issued_date=date(2026, 8, 28),
                    lines=[_line(quantity=-1, unit_price_minor=40_000)],
                ),
            )
        async with session.begin():
            diff = await services.diff_return(session, ctx, ret.id)
            refreshed = await selectors.get_return(session, org_id, ret.id)

        assert diff.changed is True
        assert diff.delta.taxable_minor == -40_000
        assert diff.delta.document_count == 1
        cdnr = next(s for s in diff.sections if s.section == "cdnr")
        assert cdnr.changed is True
        assert cdnr.delta.taxable_minor == -40_000
        # And the filed return is untouched — that is the point.
        assert refreshed is not None
        assert refreshed.total_taxable_minor == snapshot_taxable
        assert refreshed.status == "filed"

    async def test_a_draft_has_nothing_to_diff_against(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await _register(session, ctx)
            draft = GstReturn(
                organization_id=org_id,
                gst_registration_id=registration.id,
                gstin=registration.gstin,
                state_code=registration.state_code,
                period=PERIOD,
                return_type="gstr1",
                status="draft",
            )
            session.add(draft)
            await session.flush()
            draft_id = draft.id
        with pytest.raises(BusinessRuleProblem, match="no snapshot"):
            async with session.begin():
                await services.diff_return(session, ctx, draft_id)


# --------------------------------------------------------------------- the HTTP surface


class TestTheEndpoints:
    async def test_generate_fetch_and_diff(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)

        created = await client.post(RETURNS, json={"period": PERIOD})
        assert created.status_code == 201
        return_id = created.json()["id"]
        assert created.json()["status"] == "generated"
        assert created.json()["gstin"] == SELLER

        fetched = await client.get(f"{RETURNS}/{return_id}")
        assert fetched.status_code == 200
        body = fetched.json()
        assert set(body["sections"]) == {
            "b2b",
            "b2cl",
            "b2cs",
            "cdnr",
            "cdnur",
            "exp",
            "hsn",
            "docs",
        }
        assert [t["to_status"] for t in body["transitions"]] == ["generated"]

        diff = await client.get(f"{RETURNS}/{return_id}/diff")
        assert diff.status_code == 200
        assert diff.json()["changed"] is False

    async def test_filing_over_http(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        created = await client.post(RETURNS, json={"period": PERIOD})
        return_id = created.json()["id"]

        filed = await client.post(f"{RETURNS}/{return_id}/filing", json={"arn": "AA270826000001B"})
        assert filed.status_code == 201
        assert filed.json()["status"] == "filed"
        assert filed.json()["arn"] == "AA270826000001B"

    async def test_the_list_is_filterable(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        await client.post(RETURNS, json={"period": PERIOD})

        listed = await client.get(RETURNS, params={"period": PERIOD})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        empty = await client.get(RETURNS, params={"period": "2026-07"})
        assert empty.json()["total"] == 0

        by_status = await client.get(RETURNS, params={"status": "filed"})
        assert by_status.json()["total"] == 0

    async def test_a_return_from_another_organization_is_not_found(
        self, client, session, org_id
    ) -> None:
        """404 rather than 403: confirming an id exists in *some* organization is itself a
        disclosure."""
        res = await client.get(f"{RETURNS}/{uuid.uuid4()}")
        assert res.status_code == 404

    async def test_the_return_surface_publishes_no_edit_verb(self) -> None:
        """A return is generated, filed and read. There is no PUT, PATCH or DELETE anywhere on it
        — the only way a period changes is a new document, and then a diff."""
        from app.main import app

        paths = app.openapi()["paths"]
        verbs = {
            verb
            for path, ops in paths.items()
            if "/finance/gst/returns" in path
            for verb in ops
            if verb != "parameters"
        }
        assert verbs == {"get", "post"}


# --------------------------------------------------------------------- the guarantees


class TestTheSnapshotIsFrozenAtTheDatabase:
    """The guarantee, checked where it is made.

    A convention the service happens to follow is not what an auditor is asking about. These run
    as the same NOBYPASSRLS app role the service uses, so what they prove is what production has.

    The boundary is **filed**, not generated: before it goes to the portal nothing has been
    reported to anybody, and re-assembling a period after a late invoice is the point of looking
    at a return before sending it. The last two tests pin both halves of that.
    """

    async def _filed_id(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA1"))
            return ret.id

    async def test_rewriting_the_totals_is_refused(self, session, org_id) -> None:
        return_id = await self._filed_id(session, org_id)
        with pytest.raises(Exception, match="frozen once filed"):
            async with session.begin():
                await session.execute(
                    update(GstReturn).where(GstReturn.id == return_id).values(total_taxable_minor=1)
                )

    async def test_rewriting_the_sections_is_refused(self, session, org_id) -> None:
        return_id = await self._filed_id(session, org_id)
        with pytest.raises(Exception, match="frozen once filed"):
            async with session.begin():
                await session.execute(
                    update(GstReturn).where(GstReturn.id == return_id).values(sections=None)
                )

    async def test_moving_the_period_is_refused(self, session, org_id) -> None:
        """Re-labelling August as September would move a whole snapshot into a period it was not
        assembled from."""
        return_id = await self._filed_id(session, org_id)
        with pytest.raises(Exception, match="frozen once filed"):
            async with session.begin():
                await session.execute(
                    update(GstReturn).where(GstReturn.id == return_id).values(period="2026-09")
                )

    async def test_a_delete_is_refused(self, session, org_id) -> None:
        return_id = await self._filed_id(session, org_id)
        with pytest.raises(Exception, match="not deletable"):
            async with session.begin():
                await session.execute(delete(GstReturn).where(GstReturn.id == return_id))

    async def test_a_soft_delete_is_refused_too(self, session, org_id) -> None:
        """`deleted_at` is on the row because every tenant table carries it. Setting it is still
        an UPDATE, so the trigger refuses that route to erasure as well."""
        from datetime import UTC, datetime

        return_id = await self._filed_id(session, org_id)
        with pytest.raises(Exception, match="frozen once filed"):
            async with session.begin():
                await session.execute(
                    update(GstReturn)
                    .where(GstReturn.id == return_id)
                    .values(deleted_at=datetime.now(UTC))
                )

    async def test_the_status_may_still_move_forward(self, session, org_id) -> None:
        """The freeze protects the snapshot, not the lifecycle. A return is a record that gets
        filed; if `status` were frozen too, nothing could ever be filed at all."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
        async with session.begin():
            filed = await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA1"))
        assert filed.status == "filed"

    async def test_a_generated_return_may_still_be_re_assembled(self, session, org_id) -> None:
        """The other half of the boundary. Freezing at `generated` would mean the first person to
        click generate on the 1st had permanently fixed a period nobody had filed."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            first = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            first_id = first.id
        async with session.begin():
            await session.execute(
                update(GstReturn).where(GstReturn.id == first_id).values(total_taxable_minor=1)
            )
        async with session.begin():
            row = await selectors.get_return(session, org_id, first_id)
        assert row is not None
        assert row.total_taxable_minor == 1

    async def test_the_triggers_are_installed(self, session) -> None:
        """A guard against the guard: the tests above would pass for the wrong reason if
        something *else* refused the writes."""
        async with session.begin():
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t"
                            " JOIN pg_class c ON c.oid = t.tgrelid"
                            " JOIN pg_namespace n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = 'finance' AND NOT t.tgisinternal"
                            "   AND c.relname IN ('gst_returns', 'gst_return_transitions')"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_gst_returns_snapshot_frozen" in found
        assert "trg_gst_return_transitions_append_only" in found


class TestTheTrailIsAppendOnlyAtTheDatabase:
    async def _transition_id(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
        async with session.begin():
            ret = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))
            trail = await selectors.return_transitions(session, org_id, ret.id)
            return trail[0].id

    async def test_an_update_is_refused(self, session, org_id) -> None:
        transition_id = await self._transition_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(GstReturnTransition)
                    .where(GstReturnTransition.id == transition_id)
                    .values(to_status="filed")
                )

    async def test_a_delete_is_refused(self, session, org_id) -> None:
        transition_id = await self._transition_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    delete(GstReturnTransition).where(GstReturnTransition.id == transition_id)
                )


class TestCrossTenantIsolation:
    async def test_a_return_is_invisible_to_another_organization(
        self, session, admin_session, org_id
    ) -> None:
        """The other tenant's return is created through the admin (BYPASSRLS) session, then read
        for through the app role bound to *this* org. Nothing comes back."""
        other_org = uuid.uuid4()
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _book(session, ctx)
            mine = await services.generate_return(session, ctx, GenerateReturnIn(period=PERIOD))

        async with admin_session.begin():
            other_ctx = admin_ctx(other_org)
            await _register(admin_session, other_ctx)
            theirs = await services.generate_return(
                admin_session, other_ctx, GenerateReturnIn(period=PERIOD)
            )
            theirs_id = theirs.id

        async with session.begin():
            visible = await selectors.get_return(session, org_id, theirs_id)
            listed = (
                (
                    await session.execute(
                        selectors.returns_stmt(ctx_for(org_id, "finance.compliance.returns.read"))
                    )
                )
                .scalars()
                .all()
            )
        assert visible is None
        assert [r.id for r in listed] == [mine.id]

    async def test_the_period_read_never_crosses_a_tenant(
        self, session, admin_session, org_id
    ) -> None:
        """The assembly reads invoices directly, so it is the read that must be scoped — a return
        that swept in another organization's documents would be a disclosure *and* a wrong
        return."""
        other_org = uuid.uuid4()
        ctx = admin_ctx(org_id)
        async with admin_session.begin():
            await _book(admin_session, admin_ctx(other_org))

        async with session.begin():
            await _register(session, ctx)
            documents = await selectors.return_documents(session, org_id, PERIOD)
        assert documents == []
