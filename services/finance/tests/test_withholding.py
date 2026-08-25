"""GST-TDS and income-tax TDS/TCS against real PostgreSQL — thresholds, versions, and footing.

Three heads, two statutes, and they behave differently in ways that get conflated:

* **GST s. 51** is a *cliff* on one document's taxable value. Below 2,50,000 rupees nothing is
  deducted; above it, 2 % of the **whole** value — not of the excess. It splits across tax heads
  exactly as the levy does, because it is GST.
* **s. 194Q** and **s. 206C(1H)** are *aggregates* over a financial year with one counterparty.
  The rate bites only on the part above 50,00,000 rupees, so the invoice that crosses the
  threshold is withheld on partially. They are mutually exclusive by statute, and the buyer's
  194Q liability wins.

Every boundary here is tested at the exact rupee, because a threshold implemented one comparison
out is invisible until somebody is under-deducted and penalised for it.

The other half is versioning. A rate change is a new row with a later `effective_from`, and every
figure already computed keeps pointing at the version that produced it — so the last class adds a
future rate and shows that recomputing an old invoice gives the old answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update

from app import domain, selectors, services
from app.models import InvoiceWithholding, OutboxEvent
from app.models import WithholdingRate as WithholdingRateRow
from app.schemas import (
    CustomerCreate,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/finance/gst/withholdings"
SELLER = "27AABCA9012D1Z5"
BUYER_SAME_STATE = "27AAECO9012H1ZG"
BUYER_OTHER_STATE = "24AABCM1234F1Z3"

#: 2,50,000 rupees and 50,00,000 rupees, in paise — the two statutory thresholds.
GST_TDS_THRESHOLD = 250_000_00
INCOME_TAX_THRESHOLD = 5_000_000_00


#: Any version a test publishes carries a date after this; everything the migration seeded is
#: before it. The two never overlap, so the cleanup below can tell them apart.
_SEED_CUTOFF = date(2026, 1, 1)


@pytest_asyncio.fixture(autouse=True)
async def restore_schedule(admin_session):
    """Take away any rate version a test published.

    `withholding_rates` is global vocabulary seeded by a migration, so the truncating `session`
    fixture deliberately leaves it alone — which means a test that publishes a new version would
    otherwise leave every later test resolving a rate it did not expect. The register rows
    referencing it go first (TRUNCATE, which the append-only trigger does not intercept and which
    the owning admin role may run), because the foreign key is doing its job and refusing to
    orphan them.
    """
    yield
    async with admin_session.begin():
        await admin_session.execute(text("TRUNCATE finance.invoice_withholdings CASCADE"))
        await admin_session.execute(
            delete(WithholdingRateRow).where(WithholdingRateRow.effective_from > _SEED_CUTOFF)
        )


def _rate(
    code: str = "gst_tds_51",
    *,
    rate_pct: str = "2.00",
    threshold_minor: int = GST_TDS_THRESHOLD,
    basis: str = "supply",
    effective_from: date = date(2018, 10, 1),
    effective_to: date | None = None,
) -> domain.WithholdingRate:
    return domain.WithholdingRate(
        id=uuid.uuid4(),
        code=code,
        rate_pct=Decimal(rate_pct),
        threshold_minor=threshold_minor,
        basis=basis,
        effective_from=effective_from,
        effective_to=effective_to,
    )


# --------------------------------------------------------------------- the pure maths


class TestTheGstTdsCliff:
    """s. 51 is a cliff, not an excess — the single most common way it is got wrong, because
    194Q sitting beside it works the opposite way."""

    def test_exactly_on_the_threshold_deducts_nothing(self) -> None:
        assert domain.supply_withholding_basis(GST_TDS_THRESHOLD, GST_TDS_THRESHOLD) == 0

    def test_one_paisa_over_the_threshold_deducts_on_the_whole_value(self) -> None:
        value = GST_TDS_THRESHOLD + 1
        assert domain.supply_withholding_basis(value, GST_TDS_THRESHOLD) == value

    def test_one_paisa_under_the_threshold_deducts_nothing(self) -> None:
        assert domain.supply_withholding_basis(GST_TDS_THRESHOLD - 1, GST_TDS_THRESHOLD) == 0

    def test_the_basis_is_never_the_excess(self) -> None:
        """A 3,00,000-rupee supply is deducted on 3,00,000, not on the 50,000 above the line."""
        assert domain.supply_withholding_basis(300_000_00, GST_TDS_THRESHOLD) == 300_000_00

    def test_two_per_cent_of_a_supply_just_over_the_line(self) -> None:
        rate = _rate()
        computed = domain.compute_gst_tds(
            taxable_minor=GST_TDS_THRESHOLD + 100, rate=rate, intra_state=True
        )
        assert computed is not None
        assert computed.basis_minor == GST_TDS_THRESHOLD + 100
        assert computed.amount_minor == 500_002  # 2 % of 2,50,001 rupees, in paise

    def test_below_the_threshold_there_is_no_row_at_all(self) -> None:
        """None rather than a zero row: a register full of zeroes is a register nobody reads."""
        assert (
            domain.compute_gst_tds(taxable_minor=GST_TDS_THRESHOLD, rate=_rate(), intra_state=True)
            is None
        )


class TestTheGstTdsSplit:
    """2 % is 1 % CGST + 1 % SGST intra-state and 2 % IGST inter-state, and the odd paisa lands
    where the levy's does — because it *is* the levy's rule, reused rather than re-implemented."""

    @pytest.mark.parametrize(
        "taxable",
        [250_001_00, 300_000_00, 333_333_33, 999_999_99, 1_000_000_01, 12_345_678_90],
    )
    def test_the_heads_sum_to_the_deduction_exactly(self, taxable: int) -> None:
        for intra in (True, False):
            computed = domain.compute_gst_tds(
                taxable_minor=taxable, rate=_rate(), intra_state=intra
            )
            assert computed is not None
            assert (
                computed.cgst_minor + computed.sgst_minor + computed.igst_minor
                == computed.amount_minor
            )

    def test_intra_state_is_cgst_plus_sgst_and_inter_state_is_igst(self) -> None:
        intra = domain.compute_gst_tds(taxable_minor=300_000_00, rate=_rate(), intra_state=True)
        inter = domain.compute_gst_tds(taxable_minor=300_000_00, rate=_rate(), intra_state=False)
        assert intra is not None and inter is not None
        assert (intra.cgst_minor, intra.sgst_minor, intra.igst_minor) == (
            300_000,
            300_000,
            0,
        )
        assert (inter.cgst_minor, inter.sgst_minor, inter.igst_minor) == (0, 0, 600_000)

    def test_the_odd_paisa_goes_where_the_levys_does(self) -> None:
        """`split_gst` is reused, so a deduction cannot round differently from the tax it sits
        beside. An odd total puts the extra paisa in SGST, in both directions of sign."""
        taxable = 250_000_50  # 2 % is 500,001 paise — odd
        computed = domain.compute_gst_tds(taxable_minor=taxable, rate=_rate(), intra_state=True)
        expected = domain.split_gst(taxable, Decimal("2.00"), intra_state=True)
        assert computed is not None
        assert (computed.cgst_minor, computed.sgst_minor) == (
            expected.cgst_minor,
            expected.sgst_minor,
        )
        assert computed.sgst_minor - computed.cgst_minor == 1

    @pytest.mark.parametrize(
        "split,expected",
        [
            (domain.TaxSplit(cgst_minor=900, sgst_minor=900), True),
            (domain.TaxSplit(igst_minor=1_800), False),
            # Zero-rated: nothing to read, so the statutory test decides.
            (domain.TaxSplit(), True),
        ],
    )
    def test_the_head_is_read_off_the_frozen_document(self, split, expected) -> None:
        """The deduction lands under the head the *invoice* was taxed under, not one re-derived
        from today's state codes — otherwise a seller re-registering in another state would make
        every historical deduction disagree with the tax it sits beside."""
        assert (
            domain.intra_state_from_split(
                split, supply_type="b2b", seller_state_code="27", place_of_supply="27"
            )
            is expected
        )

    def test_a_zero_rated_supply_falls_back_to_the_statutory_test(self) -> None:
        """Where there is no tax to read, the rule that decided the heads when the document was
        raised decides again — rather than a guess that makes every nil-rated supply intra-state."""
        assert (
            domain.intra_state_from_split(
                domain.TaxSplit(), supply_type="b2b", seller_state_code="27", place_of_supply="24"
            )
            is False
        )
        assert (
            domain.intra_state_from_split(
                domain.TaxSplit(), supply_type="sez", seller_state_code="27", place_of_supply="27"
            )
            is False
        )

    def test_income_tax_carries_no_heads(self) -> None:
        """Income tax is not GST, so the three columns stay zero rather than being given a
        meaning they do not have."""
        computed = domain.compute_income_tax_withholding(
            prior_minor=INCOME_TAX_THRESHOLD,
            current_minor=100_000_00,
            rate=_rate(
                "itax_194q",
                rate_pct="0.10",
                threshold_minor=INCOME_TAX_THRESHOLD,
                basis="aggregate",
            ),
        )
        assert computed is not None
        assert (computed.cgst_minor, computed.sgst_minor, computed.igst_minor) == (0, 0, 0)


class TestTheAggregateThreshold:
    """194Q and 206C(1H) bite on the *excess* over the year's running total — the opposite of the
    s. 51 cliff, and the invoice that straddles the threshold is the case to get right."""

    def _basis(self, prior: int, current: int) -> int:
        return domain.aggregate_withholding_basis(
            prior_minor=prior, current_minor=current, threshold_minor=INCOME_TAX_THRESHOLD
        )

    def test_wholly_below_the_threshold_withholds_nothing(self) -> None:
        assert self._basis(1_000_000_00, 1_000_000_00) == 0

    def test_landing_exactly_on_the_threshold_withholds_nothing(self) -> None:
        assert self._basis(4_000_000_00, 1_000_000_00) == 0

    def test_one_paisa_past_the_threshold_withholds_one_paisa(self) -> None:
        assert self._basis(4_000_000_00, 1_000_000_01) == 1

    def test_the_straddling_invoice_is_withheld_on_only_its_excess(self) -> None:
        """The case a naive implementation withholds the whole invoice on, over-deducting by
        lakhs on the document that happens to cross the line."""
        assert self._basis(4_500_000_00, 1_000_000_00) == 500_000_00

    def test_every_invoice_after_the_crossing_is_withheld_in_full(self) -> None:
        assert self._basis(6_000_000_00, 200_000_00) == 200_000_00

    def test_the_year_sums_to_the_whole_excess_however_it_is_split(self) -> None:
        """Property-style: the sum of per-invoice bases must equal the excess over the threshold,
        whatever sequence of invoices produced it."""
        for invoices in ([1_000_000_00] * 8, [500_000_00] * 16, [7_000_000_00, 1_000_000_00]):
            running = 0
            total_basis = 0
            for amount in invoices:
                total_basis += self._basis(running, amount)
                running += amount
            assert total_basis == max(0, running - INCOME_TAX_THRESHOLD)

    def test_a_credit_note_reduces_the_running_total_it_is_measured_against(self) -> None:
        """A return reduces the consideration, so the next invoice's excess shrinks with it."""
        assert self._basis(6_000_000_00, -1_000_000_00) == -1_000_000_00

    def test_nothing_to_withhold_produces_no_row(self) -> None:
        assert (
            domain.compute_income_tax_withholding(
                prior_minor=0,
                current_minor=100_000_00,
                rate=_rate("itax_194q", threshold_minor=INCOME_TAX_THRESHOLD, basis="aggregate"),
            )
            is None
        )


class TestTheMutualExclusion:
    def test_a_194q_buyer_takes_precedence_over_206c(self) -> None:
        """By statute: where the buyer is liable to deduct under 194Q, the seller must **not**
        collect under 206C(1H). Charging both is the error a naive "apply every matching rule"
        produces."""
        assert domain.income_tax_head(buyer_deducts_194q=True) == "itax_194q"

    def test_otherwise_the_seller_collects(self) -> None:
        assert domain.income_tax_head(buyer_deducts_194q=False) == "itax_206c_1h"

    def test_the_direction_follows_the_head(self) -> None:
        assert domain.WITHHOLDING_DIRECTION["gst_tds_51"] == "deducted"
        assert domain.WITHHOLDING_DIRECTION["itax_194q"] == "deducted"
        assert domain.WITHHOLDING_DIRECTION["itax_206c_1h"] == "collected"


class TestRounding:
    @pytest.mark.parametrize(
        "basis,rate,expected",
        [
            (100, "2.00", 2),
            (1, "2.00", 0),  # 0.02 paise rounds to nothing
            (25, "2.00", 1),  # 0.5 paise rounds half-up, away from zero
            (-25, "2.00", -1),  # and symmetrically on a negative
            (100_000_00, "0.10", 10_000),
            (333_333_33, "0.10", 33_333),
        ],
    )
    def test_a_withholding_rounds_half_up_once(self, basis, rate, expected) -> None:
        assert domain.withholding_amount_minor(basis, Decimal(rate)) == expected

    def test_it_is_exact_across_a_range_of_amounts(self) -> None:
        """No float anywhere: 0.1 % of large receivables lands a paisa out often enough to
        matter."""
        for amount in (1, 7, 99, 12_345, 999_999, 2_147_483_648, 99_999_999_999):
            got = domain.withholding_amount_minor(amount, Decimal("0.10"))
            expected = int(
                (Decimal(amount) * Decimal("0.10") / Decimal(100)).to_integral_value(
                    rounding="ROUND_HALF_UP"
                )
            )
            assert got == expected


# --------------------------------------------------------------------- versioning


class TestRatesAreVersionedAndDoNotRestateHistory:
    """The reason the schedule is a table. A rate change is a new row with a later
    `effective_from`, resolution is against the **document's** date, and the resolved rate is
    snapshotted — so nothing that touches an old invoice can produce a new answer."""

    def _schedule(self) -> list[domain.WithholdingRate]:
        return [
            _rate(rate_pct="2.00", effective_from=date(2018, 10, 1)),
            _rate(rate_pct="5.00", effective_from=date(2026, 9, 1)),
        ]

    def test_the_document_date_picks_the_version_in_force_then(self) -> None:
        schedule = self._schedule()
        before = domain.resolve_withholding_rate(schedule, "gst_tds_51", date(2026, 8, 31))
        after = domain.resolve_withholding_rate(schedule, "gst_tds_51", date(2026, 9, 1))
        assert before is not None and after is not None
        assert before.rate_pct == Decimal("2.00")
        assert after.rate_pct == Decimal("5.00")

    def test_a_future_version_is_invisible_to_an_earlier_document(self) -> None:
        """Adding tomorrow's rate today changes nothing about yesterday — which is the whole
        claim, stated as a test."""
        old_only = [_rate(rate_pct="2.00", effective_from=date(2018, 10, 1))]
        with_future = self._schedule()
        august = date(2026, 8, 12)
        assert domain.resolve_withholding_rate(old_only, "gst_tds_51", august) is not None
        assert (
            domain.resolve_withholding_rate(old_only, "gst_tds_51", august).rate_pct  # type: ignore[union-attr]
            == domain.resolve_withholding_rate(with_future, "gst_tds_51", august).rate_pct  # type: ignore[union-attr]
        )

    def test_a_version_that_has_ended_stops_covering(self) -> None:
        """Half-open `[from, to)`, so a version ending the day another begins leaves neither a gap
        nor an overlap on the changeover date."""
        rate = _rate(effective_from=date(2018, 10, 1), effective_to=date(2026, 9, 1))
        assert rate.covers(date(2026, 8, 31)) is True
        assert rate.covers(date(2026, 9, 1)) is False

    def test_before_any_version_exists_there_is_no_rate(self) -> None:
        assert (
            domain.resolve_withholding_rate(self._schedule(), "gst_tds_51", date(2017, 1, 1))
            is None
        )

    def test_an_unknown_code_resolves_to_nothing(self) -> None:
        assert (
            domain.resolve_withholding_rate(self._schedule(), "itax_9999", date(2026, 8, 12))
            is None
        )

    def test_the_thresholds_are_versioned_alongside_the_rates(self) -> None:
        """A threshold change is as much a restatement risk as a rate change, so it rides on the
        same row rather than being a constant somewhere."""
        schedule = [
            _rate(threshold_minor=250_000_00, effective_from=date(2018, 10, 1)),
            _rate(threshold_minor=100_000_00, effective_from=date(2026, 9, 1)),
        ]
        before = domain.resolve_withholding_rate(schedule, "gst_tds_51", date(2026, 8, 12))
        after = domain.resolve_withholding_rate(schedule, "gst_tds_51", date(2026, 9, 12))
        assert before is not None and after is not None
        assert before.threshold_minor == 250_000_00
        assert after.threshold_minor == 100_000_00


# --------------------------------------------------------------------- end to end


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=gstin, legal_name="Atmora Industries Private Limited"),
    )


async def _customer(session, ctx, *, name="Scrap Buyer", gstin=BUYER_SAME_STATE, **flags):
    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name=name,
            contact_name="Contact",
            email="ap@example.in",
            gstin=gstin,
            city="Pune",
            state="Maharashtra" if gstin == BUYER_SAME_STATE else "Gujarat",
            **flags,
        ),
    )


def _invoice(customer_id, number: str, *, unit_minor: int, issued=date(2026, 8, 12), **over):
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": issued,
        "due_date": date(2026, 9, 11),
        "tax_rate_pct": 18,
        "lines": [
            InvoiceLineCreate(
                description="Metal scrap",
                quantity=1,
                unit_price_minor=unit_minor,
                # 7204 is ferrous waste and scrap — the vertical s. 51 was notified for.
                hsn_sac="72044900",
            )
        ],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


class TestAssessmentOnFinalize:
    async def test_a_notified_deductor_gets_a_gst_tds_row(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-1", unit_minor=300_000_00)
            )
        async with session.begin():
            rows = await selectors.withholdings_for_invoice(session, org_id, invoice.id)

        assert len(rows) == 1
        row = rows[0]
        assert row.code == "gst_tds_51"
        assert row.direction == "deducted"
        assert row.basis == "supply"
        assert row.basis_minor == 300_000_00
        assert row.amount_minor == 600_000  # 2 % of 3,00,000 rupees
        assert row.cgst_minor + row.sgst_minor + row.igst_minor == row.amount_minor
        assert row.period == "2026-08"
        assert row.resolved_on == date(2026, 8, 12)

    async def test_the_rate_and_threshold_are_snapshotted_beside_the_version(
        self, session, org_id
    ) -> None:
        """Storing the answer *and* naming the version is what makes "why is this figure what it
        is" answerable years later without depending on the schedule still saying the same."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-2", unit_minor=300_000_00)
            )
        async with session.begin():
            row = (await selectors.withholdings_for_invoice(session, org_id, invoice.id))[0]
            version = (
                await session.execute(
                    select(WithholdingRateRow).where(
                        WithholdingRateRow.id == row.withholding_rate_id
                    )
                )
            ).scalar_one()

        assert row.rate_pct == Decimal("2.00")
        assert row.threshold_minor == GST_TDS_THRESHOLD
        assert version.code == "gst_tds_51"

    async def test_a_supply_under_the_threshold_records_nothing(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-3", unit_minor=200_000_00)
            )
        async with session.begin():
            assert await selectors.withholdings_for_invoice(session, org_id, invoice.id) == []

    async def test_an_inter_state_supply_deducts_under_igst(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_OTHER_STATE, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-4", unit_minor=300_000_00)
            )
        async with session.begin():
            row = (await selectors.withholdings_for_invoice(session, org_id, invoice.id))[0]
        assert (row.cgst_minor, row.sgst_minor, row.igst_minor) == (0, 0, 600_000)

    async def test_a_customer_with_no_flags_gets_nothing_invented_for_them(
        self, session, org_id
    ) -> None:
        """The default that keeps the whole feature opt-in. An organization that has told the
        platform nothing about its counterparties has no figures made up for it."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-5", unit_minor=9_000_000_00)
            )
        async with session.begin():
            assert await selectors.withholdings_for_invoice(session, org_id, invoice.id) == []

    async def test_a_credit_note_is_not_withheld_on(self, session, org_id) -> None:
        """A withholding is a deduction from a payment, and a credit note is not a payment; the
        correction to an over-deduction is made by the deductor against the original."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-6", unit_minor=300_000_00)
            )
            credit = await services.create_invoice(
                session,
                ctx,
                _invoice(
                    customer.id,
                    "CN-TDS-6",
                    unit_minor=-300_000_00,
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                ),
            )
        async with session.begin():
            assert await selectors.withholdings_for_invoice(session, org_id, credit.id) == []

    @pytest.mark.parametrize("status", ["draft", "void"])
    async def test_an_unissued_document_is_not_withheld_on(self, session, org_id, status) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice(customer.id, f"INV-TDS-{status}", unit_minor=300_000_00, status=status),
            )
        async with session.begin():
            assert await selectors.withholdings_for_invoice(session, org_id, invoice.id) == []

    async def test_assessing_announces_it(self, session, org_id) -> None:
        """The money named here never arrives: a collections consumer expecting the gross would
        chase a receivable that was settled net."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-TDS-7", unit_minor=300_000_00)
            )
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_WITHHOLDING_RECORDED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["code"] == "gst_tds_51"
        assert events[0].payload["amount_minor"] == 600_000


class TestTheAggregateHeadsEndToEnd:
    async def test_206c_bites_only_once_the_year_crosses_the_threshold(
        self, session, org_id
    ) -> None:
        """Four invoices of 20 lakh: nothing, nothing, the excess, then the whole of the last."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, tcs_applicable=True)
            customer_id = customer.id

        collected = []
        for i in range(4):
            async with session.begin():
                invoice = await services.create_invoice(
                    session,
                    ctx,
                    # 20,00,000 rupees plus 18 % is 23,60,000 — the aggregate is measured on the
                    # consideration, which is the invoice total.
                    _invoice(customer_id, f"INV-TCS-{i}", unit_minor=2_000_000_00),
                )
            async with session.begin():
                rows = await selectors.withholdings_for_invoice(session, org_id, invoice.id)
                collected.append(rows[0].basis_minor if rows else 0)

        running = 0
        expected = []
        for _ in range(4):
            invoice_total = 2_000_000_00 + 360_000_00
            expected.append(
                domain.aggregate_withholding_basis(
                    prior_minor=running,
                    current_minor=invoice_total,
                    threshold_minor=INCOME_TAX_THRESHOLD,
                )
            )
            running += invoice_total
        assert collected == expected
        # And the year's bases sum to the whole excess, exactly.
        assert sum(collected) == max(0, running - INCOME_TAX_THRESHOLD)

    async def test_a_194q_buyer_is_deducted_from_and_never_also_collected_from(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, deducts_tds_194q=True, tcs_applicable=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-194Q-1", unit_minor=9_000_000_00)
            )
        async with session.begin():
            rows = await selectors.withholdings_for_invoice(session, org_id, invoice.id)

        assert [r.code for r in rows] == ["itax_194q"]
        assert rows[0].direction == "deducted"

    async def test_a_prior_year_does_not_count_towards_this_ones_threshold(
        self, session, org_id
    ) -> None:
        """The window is India's financial year, April to April — so a March invoice and an April
        one are measured separately."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, tcs_applicable=True)
            customer_id = customer.id
        async with session.begin():
            await services.create_invoice(
                session,
                ctx,
                _invoice(
                    customer_id, "INV-FY-1", unit_minor=9_000_000_00, issued=date(2026, 3, 20)
                ),
            )
        async with session.begin():
            april = await services.create_invoice(
                session,
                ctx,
                _invoice(customer_id, "INV-FY-2", unit_minor=1_000_000_00, issued=date(2026, 4, 2)),
            )
        async with session.begin():
            rows = await selectors.withholdings_for_invoice(session, org_id, april.id)
        # The new year starts at zero, and 11,80,000 rupees is nowhere near 50 lakh.
        assert rows == []

    async def test_both_heads_can_apply_to_one_document(self, session, org_id) -> None:
        """GST s. 51 and 194Q are different statutes and both can bite. Two rows, one per head."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True, deducts_tds_194q=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-BOTH-1", unit_minor=9_000_000_00)
            )
        async with session.begin():
            rows = await selectors.withholdings_for_invoice(session, org_id, invoice.id)
        assert sorted(r.code for r in rows) == ["gst_tds_51", "itax_194q"]


class TestARateChangeDoesNotRestateHistory:
    async def test_a_later_version_leaves_an_earlier_invoice_alone(
        self, session, admin_session, org_id
    ) -> None:
        """The end-to-end version of the claim: publish a new rate, recompute, get the old answer.

        The new version is inserted through the admin session because `withholding_rates` is
        global vocabulary a migration seeds — this is the shape a rate change actually arrives in.
        """
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            august = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-VER-1", unit_minor=300_000_00)
            )
            customer_id = customer.id
        async with session.begin():
            before = (await selectors.withholdings_for_invoice(session, org_id, august.id))[0]
            original_amount = before.amount_minor
            original_rate = before.rate_pct

        # The rate doubles from 1 September.
        async with admin_session.begin():
            admin_session.add(
                WithholdingRateRow(
                    code="gst_tds_51",
                    name="GST TDS - section 51 (revised)",
                    rate_pct=Decimal("4.00"),
                    threshold_minor=GST_TDS_THRESHOLD,
                    basis="supply",
                    jurisdiction="IN",
                    effective_from=date(2026, 9, 1),
                )
            )

        async with session.begin():
            schedule = await selectors.withholding_schedule(session)
            resolved_august = domain.resolve_withholding_rate(
                schedule, "gst_tds_51", date(2026, 8, 12)
            )
            resolved_september = domain.resolve_withholding_rate(
                schedule, "gst_tds_51", date(2026, 9, 12)
            )

        assert resolved_august is not None and resolved_september is not None
        assert resolved_august.rate_pct == Decimal("2.00")
        assert resolved_september.rate_pct == Decimal("4.00")

        # The August invoice's stored figure is untouched, and re-deriving it from the schedule
        # reproduces it rather than restating it.
        async with session.begin():
            after = (await selectors.withholdings_for_invoice(session, org_id, august.id))[0]
        assert after.amount_minor == original_amount
        assert after.rate_pct == original_rate

        # A September document picks the new rate, which is the other half of "versioned".
        async with session.begin():
            september = await services.create_invoice(
                session,
                ctx,
                _invoice(
                    customer_id,
                    "INV-VER-2",
                    unit_minor=300_000_00,
                    issued=date(2026, 9, 12),
                    due_date=date(2026, 10, 12),
                ),
            )
        async with session.begin():
            row = (await selectors.withholdings_for_invoice(session, org_id, september.id))[0]
        assert row.rate_pct == Decimal("4.00")
        assert row.amount_minor == 1_200_000  # 4 % of 3,00,000 rupees


# --------------------------------------------------------------------- reporting


class TestTheGstr7StyleSummary:
    async def _book(self, session, ctx, org_id) -> None:
        async with session.begin():
            await _register(session, ctx)
            first = await _customer(
                session, ctx, name="Municipal Corporation", is_tds_deductor=True
            )
            second = await _customer(
                session,
                ctx,
                name="State Transport Undertaking",
                gstin=BUYER_OTHER_STATE,
                is_tds_deductor=True,
            )
            for i in range(2):
                await services.create_invoice(
                    session, ctx, _invoice(first.id, f"INV-SUM-A{i}", unit_minor=300_000_00)
                )
            await services.create_invoice(
                session, ctx, _invoice(second.id, "INV-SUM-B0", unit_minor=400_000_00)
            )

    async def test_the_summary_groups_per_deductor_and_foots(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        await self._book(session, ctx, org_id)
        async with session.begin():
            summary = await selectors.withholding_summary(session, ctx, period="2026-08")

        assert summary.period == "2026-08"
        assert len(summary.rows) == 2
        by_gstin = {r.counterparty_gstin: r for r in summary.rows}
        assert by_gstin[BUYER_SAME_STATE].document_count == 2
        assert by_gstin[BUYER_SAME_STATE].basis_minor == 600_000_00
        assert by_gstin[BUYER_SAME_STATE].amount_minor == 1_200_000
        assert by_gstin[BUYER_OTHER_STATE].igst_minor == 800_000

        # The totals sum the rows exactly — a summary that does not foot to its own rows is
        # worse than no summary.
        assert summary.total_basis_minor == sum(r.basis_minor for r in summary.rows)
        assert summary.total_amount_minor == sum(r.amount_minor for r in summary.rows)
        assert summary.total_cgst_minor == sum(r.cgst_minor for r in summary.rows)
        assert summary.total_sgst_minor == sum(r.sgst_minor for r in summary.rows)
        assert summary.total_igst_minor == sum(r.igst_minor for r in summary.rows)
        assert summary.document_count == 3

    async def test_the_heads_sum_to_the_declared_amount(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        await self._book(session, ctx, org_id)
        async with session.begin():
            summary = await selectors.withholding_summary(session, ctx, period="2026-08")
        assert (
            summary.total_cgst_minor + summary.total_sgst_minor + summary.total_igst_minor
            == summary.total_amount_minor
        )

    async def test_another_period_is_empty(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        await self._book(session, ctx, org_id)
        async with session.begin():
            summary = await selectors.withholding_summary(session, ctx, period="2026-07")
        assert summary.rows == []
        assert summary.total_amount_minor == 0

    async def test_filtering_by_code_narrows_the_summary(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        await self._book(session, ctx, org_id)
        async with session.begin():
            summary = await selectors.withholding_summary(
                session, ctx, period="2026-08", code="itax_194q"
            )
        assert summary.rows == []
        assert summary.direction == "deducted"


class TestTheEndpoints:
    async def test_the_register_and_the_summary_read_back(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-HTTP-1", unit_minor=300_000_00)
            )

        listed = await client.get(REGISTER)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        item = listed.json()["items"][0]
        assert item["withholding"]["code"] == "gst_tds_51"
        assert item["invoice_number"] == "INV-HTTP-1"

        filtered = await client.get(REGISTER, params={"code": "itax_194q"})
        assert filtered.json()["total"] == 0

        summary = await client.get(f"{REGISTER}/summary", params={"period": "2026-08"})
        assert summary.status_code == 200
        assert summary.json()["total_amount_minor"] == 600_000

    async def test_the_summary_needs_a_period(self, client) -> None:
        """A withholding report with no period would be "everything ever", which is not a return
        anybody files."""
        res = await client.get(f"{REGISTER}/summary")
        assert res.status_code == 422

    async def test_the_schedule_is_published_beside_the_register(self, client) -> None:
        """A figure is only explicable next to the version of the rate that produced it."""
        res = await client.get("/api/v1/finance/gst/withholding-rates")
        assert res.status_code == 200
        codes = {row["code"] for row in res.json()}
        assert codes == {"gst_tds_51", "itax_194q", "itax_206c_1h"}

    async def test_the_seeded_schedule_carries_the_statutory_figures(self, client) -> None:
        res = await client.get("/api/v1/finance/gst/withholding-rates")
        by_code = {row["code"]: row for row in res.json()}
        assert by_code["gst_tds_51"]["rate_pct"] == 2.0
        assert by_code["gst_tds_51"]["threshold_minor"] == GST_TDS_THRESHOLD
        assert by_code["gst_tds_51"]["basis"] == "supply"
        assert by_code["itax_194q"]["rate_pct"] == 0.1
        assert by_code["itax_194q"]["threshold_minor"] == INCOME_TAX_THRESHOLD
        assert by_code["itax_206c_1h"]["basis"] == "aggregate"

    async def test_the_register_publishes_no_write_verb(self) -> None:
        """The register is computed when a document is finalized. There is no route that lets a
        person type a deduction in — a figure somebody could type is a figure nobody can trust."""
        from app.main import app

        paths = app.openapi()["paths"]
        verbs = {
            verb
            for path, ops in paths.items()
            if "withholding" in path
            for verb in ops
            if verb != "parameters"
        }
        assert verbs == {"get"}


# --------------------------------------------------------------------- the guarantees


class TestTheRegisterIsAppendOnlyAtTheDatabase:
    """Evidence about money somebody else already paid to a tax authority on this organization's
    behalf. Evidence the application can rewrite is not evidence."""

    async def _row_id(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-AO-1", unit_minor=300_000_00)
            )
        async with session.begin():
            return (await selectors.withholdings_for_invoice(session, org_id, invoice.id))[0].id

    async def test_an_update_is_refused(self, session, org_id) -> None:
        row_id = await self._row_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(InvoiceWithholding)
                    .where(InvoiceWithholding.id == row_id)
                    .values(amount_minor=1)
                )

    async def test_a_delete_is_refused(self, session, org_id) -> None:
        row_id = await self._row_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    delete(InvoiceWithholding).where(InvoiceWithholding.id == row_id)
                )

    async def test_a_soft_delete_is_refused_too(self, session, org_id) -> None:
        row_id = await self._row_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(InvoiceWithholding)
                    .where(InvoiceWithholding.id == row_id)
                    .values(deleted_at=datetime.now(UTC))
                )

    async def test_the_trigger_is_installed_on_the_table(self, session) -> None:
        async with session.begin():
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t"
                            " JOIN pg_class c ON c.oid = t.tgrelid"
                            " JOIN pg_namespace n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = 'finance'"
                            "   AND c.relname = 'invoice_withholdings'"
                            "   AND NOT t.tgisinternal"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_invoice_withholdings_append_only" in found

    async def test_the_rate_schedule_is_unpolicied_platform_vocabulary(self, session) -> None:
        """`withholding_rates` carries no `organization_id` and is left out of RLS alongside
        `tax_rates` and `processed_events`: a rate under GST s. 51 is the law of one country, not
        one tenant's data, and a policy over a statute would be meaningless."""
        async with session.begin():
            policied = (
                await session.execute(
                    text(
                        "SELECT relrowsecurity FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = 'finance' AND c.relname = 'withholding_rates'"
                    )
                )
            ).scalar_one()
            columns = (
                (
                    await session.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns"
                            " WHERE table_schema = 'finance'"
                            "   AND table_name = 'withholding_rates'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert policied is False
        assert "organization_id" not in columns


class TestCrossTenantIsolation:
    async def test_another_organizations_register_is_invisible(
        self, session, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            other_ctx = admin_ctx(other_org)
            await _register(admin_session, other_ctx)
            customer = await _customer(admin_session, other_ctx, is_tds_deductor=True)
            invoice = await services.create_invoice(
                admin_session,
                other_ctx,
                _invoice(customer.id, "INV-OTHER-1", unit_minor=300_000_00),
            )
            their_invoice_id = invoice.id

        ctx = admin_ctx(org_id)
        async with session.begin():
            rows = await selectors.withholdings_for_invoice(session, org_id, their_invoice_id)
            summary = await selectors.withholding_summary(session, ctx, period="2026-08")
        assert rows == []
        assert summary.rows == []
        assert summary.total_amount_minor == 0

    async def test_the_year_to_date_turnover_never_crosses_a_tenant(
        self, session, admin_session, org_id
    ) -> None:
        """The aggregate threshold is measured by a query, so a leak here would be a wrong
        *figure* as well as a disclosure — another tenant's sales pushing this one over 50 lakh."""
        other_org = uuid.uuid4()
        shared_customer_id = uuid.uuid4()
        async with admin_session.begin():
            other_ctx = admin_ctx(other_org)
            await _register(admin_session, other_ctx)
            customer = await _customer(admin_session, other_ctx)
            await services.create_invoice(
                admin_session,
                other_ctx,
                _invoice(customer.id, "INV-BIG-1", unit_minor=9_000_000_00),
            )
            shared_customer_id = customer.id

        async with session.begin():
            turnover = await selectors.financial_year_turnover(
                session, org_id, customer_id=shared_customer_id, on=date(2026, 8, 12)
            )
        assert turnover == 0
