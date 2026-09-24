"""e-Way bills against real PostgreSQL — validity, cancellation, and the ledger underneath.

The portal is stubbed and nothing else is. What these tests are actually about is the one thing an
e-Way bill has that an IRN does not: it **expires**, and the moment it does is checked at a
roadside barrier by somebody holding a printout. So the deadline may not be a number only the
portal knows — it is arithmetic over the distance and the timestamp this platform stored, and
these tests recompute it from those two values and insist the stored answer matches.

The rest is the same shape `test_einvoice.py` has, because it is the same kind of fact: one row
per attempt, never mutated, and the last class goes to the database directly and tries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update

from app import domain, selectors, services
from app.eway.base import EwayAck, EwayPayload, EwayRejected, EwayUnavailable
from app.eway.stub import StubEwayClient, compute_eway_bill_no, compute_eway_bill_no_for_key
from app.models import EwayBill, OutboxEvent
from app.schemas import (
    CancelEwayIn,
    CustomerCreate,
    GenerateEwayIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

BILLS = "/internal/v1/eway/bills"
CANCEL = "/internal/v1/eway/bills:cancel"

SELLER = "27AABCA9012D1Z5"
BUYER_SAME_STATE = "27AAECO9012H1ZG"

#: One unit at 1,00,000 rupees, so the invoice total clears the 50,000-rupee consignment
#: threshold with room to spare. Under-threshold cases pass a smaller unit price explicitly.
OVER_THRESHOLD_UNIT_MINOR = 100_000_00


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Private Limited"),
    )


async def _customer(session, ctx, *, gstin=BUYER_SAME_STATE, state="Maharashtra"):
    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name="Consignee",
            contact_name="Contact",
            email="ap@example.in",
            gstin=gstin,
            city="Pune",
            state=state,
        ),
    )


def _invoice_payload(customer_id, number: str, *, unit_minor=OVER_THRESHOLD_UNIT_MINOR, **over):
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 8, 12),
        "due_date": date(2026, 9, 11),
        "tax_rate_pct": 18,
        "lines": [
            InvoiceLineCreate(
                description="Hot-rolled coil",
                quantity=1,
                unit_price_minor=unit_minor,
                hsn_sac="72085110",
            )
        ],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


def _generate_payload(org_id, invoice_id, **over) -> GenerateEwayIn:
    fields = {
        "organization_id": org_id,
        "invoice_id": invoice_id,
        "distance_km": 150,
        "transport_mode": "road",
        "vehicle_no": "MH12AB1234",
    }
    fields.update(over)
    return GenerateEwayIn(**fields)


async def _consignment(session, ctx, number="INV-EW-1", **over):
    await _register(session, ctx)
    customer = await _customer(session, ctx)
    return await services.create_invoice(
        session, ctx, _invoice_payload(customer.id, number, **over)
    )


def _stub_payload(**over) -> EwayPayload:
    fields = {
        "document_type": "INV",
        "document_number": "INV-EW-1",
        "document_date": date(2026, 8, 12),
        "seller_gstin": SELLER,
        "seller_state_code": "27",
        "buyer_gstin": BUYER_SAME_STATE,
        "buyer_name": "Consignee",
        "place_of_supply": "27",
        "transport_mode": "road",
        "distance_km": 150,
        "vehicle_no": "MH12AB1234",
        "transporter_id": None,
        "transport_doc_no": None,
        "transport_doc_date": None,
        "over_dimensional": False,
        "consignment_value_minor": 118_000_00,
        "taxable_minor": 100_000_00,
        "cgst_minor": 9_000_00,
        "sgst_minor": 9_000_00,
        "igst_minor": 0,
        "generated_at": datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
        "lines": [],
    }
    fields.update(over)
    payload = EwayPayload(**fields)  # type: ignore[arg-type]
    if not payload.lines:
        from app.eway.base import EwayLine

        payload = EwayPayload(
            **{
                **fields,
                "lines": [
                    EwayLine(
                        serial_no=1,
                        description="Hot-rolled coil",
                        hsn_sac="72085110",
                        quantity=Decimal(1),
                        taxable_minor=fields["taxable_minor"],  # type: ignore[arg-type]
                        rate_pct=Decimal(18),
                        cgst_minor=fields["cgst_minor"],  # type: ignore[arg-type]
                        sgst_minor=fields["sgst_minor"],  # type: ignore[arg-type]
                        igst_minor=fields["igst_minor"],  # type: ignore[arg-type]
                    )
                ],
            }  # type: ignore[arg-type]
        )
    return payload


# --------------------------------------------------------------------- the threshold


class TestTheConsignmentThreshold:
    """More than 50,000 rupees, strictly. A boundary implemented as `>=` raises a bill for every
    consignment that lands exactly on it, which the portal then refuses."""

    def _requires(self, value_minor: int, **over) -> bool:
        fields = {
            "consignment_value_minor": value_minor,
            "document_type": "invoice",
            "supply_type": "b2b",
            "status": "sent",
        }
        fields.update(over)
        return domain.requires_eway_bill(**fields)  # type: ignore[arg-type]

    def test_exactly_on_the_threshold_needs_no_bill(self) -> None:
        assert domain.EWAY_THRESHOLD_MINOR == 50_000_00
        assert self._requires(50_000_00) is False

    def test_one_paisa_over_the_threshold_needs_one(self) -> None:
        assert self._requires(50_000_01) is True

    def test_one_paisa_under_the_threshold_does_not(self) -> None:
        assert self._requires(49_999_99) is False

    def test_a_return_consignment_is_measured_on_its_absolute_value(self) -> None:
        """A credit note's negative total still describes goods physically moving — back to the
        seller — and a return lorry needs a bill exactly as the outward one did."""
        assert self._requires(-60_000_00, document_type="credit_note") is True

    @pytest.mark.parametrize("status", ["draft", "void"])
    def test_nothing_moves_under_a_document_nobody_issued(self, status: str) -> None:
        assert self._requires(60_000_00, status=status) is False

    def test_an_unknown_supply_type_is_refused_rather_than_assumed(self) -> None:
        assert self._requires(60_000_00, supply_type="barter") is False


# --------------------------------------------------------------------- validity


class TestValidity:
    """One day per 200 km **or part thereof**, and each day expires at midnight of the day
    following the date of generation — not at generation time plus N x 24 hours. A driver who
    reads it the second way is stopped at a checkpoint."""

    @pytest.mark.parametrize(
        "distance,days",
        [(1, 1), (5, 1), (199, 1), (200, 1), (201, 2), (400, 2), (401, 3), (4000, 20)],
    )
    def test_days_are_one_per_200km_or_part(self, distance: int, days: int) -> None:
        assert domain.eway_validity_days(distance) == days

    @pytest.mark.parametrize("distance,days", [(1, 1), (20, 1), (21, 2), (200, 10), (201, 11)])
    def test_over_dimensional_cargo_buys_a_tenth_of_the_distance(
        self, distance: int, days: int
    ) -> None:
        assert domain.eway_validity_days(distance, over_dimensional=True) == days

    @pytest.mark.parametrize("distance", [0, -1, 4001, 100_000])
    def test_a_distance_outside_the_band_is_refused(self, distance: int) -> None:
        with pytest.raises(ValueError, match="not a distance the portal accepts"):
            domain.eway_validity_days(distance)

    def test_a_day_expires_at_midnight_following_the_date_of_generation(self) -> None:
        raised = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)
        assert domain.eway_valid_until(raised, 150) == datetime(2026, 8, 13, 0, 0, tzinfo=UTC)

    def test_a_late_evening_bill_expires_the_same_night(self) -> None:
        """The case that surprises people: raised at 23:50, valid for ten minutes. This is the
        statute, and a screen that said "valid for a day" would be lying to a driver."""
        raised = datetime(2026, 8, 12, 23, 50, tzinfo=UTC)
        expires = domain.eway_valid_until(raised, 150)
        assert expires == datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        assert expires - raised == timedelta(minutes=10)

    def test_two_days_of_distance_reaches_the_second_midnight(self) -> None:
        raised = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)
        assert domain.eway_valid_until(raised, 350) == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)

    def test_expiry_is_inclusive_of_the_instant_it_ends(self) -> None:
        """At midnight the bill is already expired — the direction that fails safe."""
        until = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        assert domain.eway_expired(until, until - timedelta(seconds=1)) is False
        assert domain.eway_expired(until, until) is True
        assert domain.eway_expired(until, until + timedelta(seconds=1)) is True

    def test_expired_is_derived_and_never_written(self) -> None:
        """The table is append-only, so nothing can update a row when the clock passes it. The
        only honest place for "is this expired" is the read."""
        until = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        before = domain.eway_status_at(
            status="generated", valid_until=until, at=until - timedelta(hours=1)
        )
        after = domain.eway_status_at(status="generated", valid_until=until, at=until)
        assert (before, after) == ("generated", "expired")

    @pytest.mark.parametrize("status", ["pending", "failed", "cancelled"])
    def test_only_a_generated_bill_can_expire(self, status: str) -> None:
        """A cancelled bill does not later expire, and a refusal has no validity to run out."""
        until = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        assert (
            domain.eway_status_at(status=status, valid_until=until, at=until + timedelta(days=9))
            == status
        )


class TestTheCancellationWindow:
    def test_inside_twenty_four_hours_the_remedy_is_cancel(self) -> None:
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert (
            domain.eway_remedy_for(
                status="generated", generated_at=raised, at=raised + timedelta(hours=23)
            )
            == "cancel"
        )

    def test_the_window_closes_exactly_at_twenty_four_hours(self) -> None:
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert domain.eway_cancellation_window_open(raised, raised + timedelta(hours=24)) is True
        assert (
            domain.eway_cancellation_window_open(raised, raised + timedelta(hours=24, seconds=1))
            is False
        )

    def test_outside_the_window_there_is_no_remedy_at_all(self) -> None:
        """Unlike an IRN, there is no credit-note fallback: the movement happened, and it has to
        be reported as it happened. One answer, and here the answer is none."""
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert (
            domain.eway_remedy_for(
                status="generated", generated_at=raised, at=raised + timedelta(days=2)
            )
            is None
        )

    @pytest.mark.parametrize("status", ["pending", "cancelled", "expired", "cancelling"])
    def test_nothing_but_a_live_bill_has_a_remedy(self, status: str) -> None:
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert domain.eway_remedy_for(status=status, generated_at=raised, at=raised) is None

    def test_a_failed_generation_has_no_remedy(self) -> None:
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert domain.eway_remedy_for(status="failed", generated_at=None, at=raised) is None

    def test_a_failed_withdrawal_leaves_the_bill_standing_with_a_remedy(self) -> None:
        """A `failed` row carrying the bill's `valid_from` is a withdrawal that did not happen
        (D3): ask again inside the window; outside it only the portal knows, so a person looks."""
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        inside = raised + timedelta(hours=2)
        outside = raised + timedelta(hours=25)
        assert domain.eway_remedy_for(status="failed", generated_at=raised, at=inside) == "cancel"
        assert (
            domain.eway_remedy_for(status="failed", generated_at=raised, at=outside)
            == "contact_support"
        )

    def test_the_deadline_is_arithmetic_over_the_stored_timestamp(self) -> None:
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        assert domain.eway_cancellation_deadline(raised) == datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------- the stub portal


class TestTheStubPortal:
    def test_it_satisfies_the_protocol(self) -> None:
        from app.eway.base import EwayClient

        assert isinstance(StubEwayClient(), EwayClient)

    def test_the_same_attempt_always_yields_the_same_number(self) -> None:
        """Deterministic in its inputs, so the whole state machine is reproducible without a
        network."""
        args = {
            "seller_gstin": SELLER,
            "document_type": "INV",
            "document_number": "INV-EW-1",
            "raised_at": "2026-08-12T10:00:00+00:00",
        }
        assert compute_eway_bill_no(**args) == compute_eway_bill_no(**args)
        assert compute_eway_bill_no(**{**args, "document_number": "INV-EW-2"}) != (
            compute_eway_bill_no(**args)
        )

    def test_a_later_bill_on_the_same_document_gets_a_different_number(self) -> None:
        """The difference from an IRN, and a real one. A document carries several e-Way bills over
        its life — cancelled and re-raised, or expired in transit while the goods still have to
        arrive — so keying the number on the document alone would collide on the unique index the
        second time and strand a lorry because a hash said so."""
        args = {
            "seller_gstin": SELLER,
            "document_type": "INV",
            "document_number": "INV-EW-1",
            "raised_at": "2026-08-12T10:00:00+00:00",
        }
        later = {**args, "raised_at": "2026-08-15T10:00:00+00:00"}
        assert compute_eway_bill_no(**args) != compute_eway_bill_no(**later)

    def test_the_number_is_always_twelve_digits(self) -> None:
        """Never eleven with a leading zero that a downstream string comparison would drop."""
        for i in range(200):
            number = compute_eway_bill_no(
                seller_gstin=SELLER,
                document_type="INV",
                document_number=f"INV-{i}",
                raised_at="2026-08-12T10:00:00+00:00",
            )
            assert len(number) == 12
            assert number.isdigit()
            assert not number.startswith("0")

    def test_generating_needs_no_credentials_and_opens_no_socket(self) -> None:
        """The one seam. A test that would fail the moment somebody made the stub reach out."""
        import socket

        client = StubEwayClient()
        original = socket.socket

        def _forbidden(*args, **kwargs):
            raise AssertionError("the stub e-Way client must not open a socket")

        socket.socket = _forbidden  # type: ignore[assignment]
        try:
            ack = client.generate(_stub_payload())
            cancelled = client.cancel(
                eway_bill_no=ack.eway_bill_no,
                reason_code="1",
                remark="",
                at=datetime(2026, 8, 12, 11, 0, tzinfo=UTC),
            )
        finally:
            socket.socket = original  # type: ignore[assignment]
        assert isinstance(ack, EwayAck)
        assert cancelled.eway_bill_no == ack.eway_bill_no

    def test_the_ack_carries_no_deadline_of_its_own(self) -> None:
        """Deliberate: a deadline only the portal can produce is one no screen can explain and no
        test can check. The platform computes it from what it stored."""
        ack = StubEwayClient().generate(_stub_payload())
        assert not hasattr(ack, "valid_until")
        assert ack.valid_from == datetime(2026, 8, 12, 10, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        "override,code",
        [
            ({"seller_gstin": "27AABCA9012D1Z9"}, "102"),
            ({"buyer_gstin": "27AAECO9012H1ZZ"}, "103"),
            ({"place_of_supply": "ZZ"}, "108"),
            ({"transport_mode": "teleport"}, "122"),
            (
                {
                    "consignment_value_minor": 40_000_00,
                    "taxable_minor": 40_000_00,
                    "cgst_minor": 0,
                    "sgst_minor": 0,
                },
                "111",
            ),
            ({"distance_km": 0}, "112"),
            ({"distance_km": 5000}, "112"),
            ({"vehicle_no": None}, "113"),
            ({"transport_mode": "rail", "vehicle_no": None}, "114"),
        ],
    )
    def test_the_portal_refuses_what_the_real_one_refuses(self, override, code) -> None:
        with pytest.raises(EwayRejected) as raised:
            StubEwayClient().generate(_stub_payload(**override))
        assert raised.value.code == code

    def test_a_line_with_no_hsn_is_refused(self) -> None:
        """Not optional the way it is on an invoice draft: the classification is what a checkpoint
        reads."""
        from app.eway.base import EwayLine

        payload = _stub_payload(
            lines=[
                EwayLine(
                    serial_no=1,
                    description="Coil",
                    hsn_sac=None,
                    quantity=Decimal(1),
                    taxable_minor=100_000,
                    rate_pct=Decimal(18),
                    cgst_minor=9_000,
                    sgst_minor=9_000,
                    igst_minor=0,
                )
            ]
        )
        with pytest.raises(EwayRejected) as raised:
            StubEwayClient().generate(payload)
        assert raised.value.code == "116"

    def test_lines_that_do_not_foot_are_refused(self) -> None:
        with pytest.raises(EwayRejected) as raised:
            StubEwayClient().generate(_stub_payload(taxable_minor=99_999))
        assert raised.value.code == "119"

    def test_an_unknown_cancellation_reason_is_refused(self) -> None:
        with pytest.raises(EwayRejected):
            StubEwayClient().cancel(
                eway_bill_no="123456789012",
                reason_code="9",
                remark="",
                at=datetime(2026, 8, 12, tzinfo=UTC),
            )


# --------------------------------------------------------------------- the write path


class _Unavailable:
    """A portal that is down. `name` so the log and the events still say which one."""

    name = "stub-eway"

    def generate(self, payload):
        raise EwayUnavailable("connection reset by peer")

    def cancel(self, **kwargs):
        raise EwayUnavailable("connection reset by peer")


class TestGeneratingABill:
    async def test_a_bill_stores_the_distance_and_the_deadline_it_implies(
        self, session, org_id
    ) -> None:
        """The central claim: the deadline is recomputable from the two values on the row."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id, distance_km=350),
            )

        assert bill.status == "generated"
        assert bill.distance_km == 350
        assert bill.valid_from is not None
        assert bill.valid_until is not None
        # Recompute from the stored inputs — the whole point of storing them.
        assert bill.valid_until == domain.eway_valid_until(bill.valid_from, bill.distance_km)

    async def test_the_bill_number_is_the_portals_and_is_stored_verbatim(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx, "INV-EW-2")
        async with session.begin():
            bill = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id),
                at=raised,
            )
        # Numbered from the generation's idempotency key (D3): the portal's own answer to a
        # repeat of the same key is the same bill, so a resumed attempt records one bill, not two.
        key = domain.eway_idempotency_key(
            invoice_id=invoice.id,
            generation=1,
            transport_mode="road",
            vehicle_no="MH12AB1234",
            transporter_id=None,
            transport_doc_no=None,
            transport_doc_date=None,
            distance_km=150,
            over_dimensional=False,
        )
        assert bill.eway_bill_no == compute_eway_bill_no_for_key(key)

    async def test_over_dimensional_cargo_gets_a_tenth_of_the_validity(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id, distance_km=150, over_dimensional=True),
            )
        assert bill.valid_from is not None
        # 150 km at 20 km/day is eight days, not one.
        assert bill.valid_until == domain.eway_valid_until(
            bill.valid_from, 150, over_dimensional=True
        )
        assert domain.eway_validity_days(150, over_dimensional=True) == 8

    async def test_a_consignment_under_the_threshold_is_refused_before_the_portal(
        self, session, org_id
    ) -> None:
        """Nothing is attempted, so nothing is recorded — the 4xx cases are exactly the ones
        where no trip was made."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx, "INV-EW-3", unit_minor=1_000)
            invoice_id = invoice.id
        with pytest.raises(BusinessRuleProblem, match="needs no e-Way bill"):
            async with session.begin():
                await services.generate_eway_bill(
                    session, ctx, StubEwayClient(), _generate_payload(org_id, invoice_id)
                )
        async with session.begin():
            assert await selectors.eway_bills_for_invoice(session, org_id, invoice_id) == []

    async def test_an_impossible_distance_is_refused_before_the_portal(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="not a distance"):
            async with session.begin():
                await services.generate_eway_bill(
                    session,
                    ctx,
                    StubEwayClient(),
                    _generate_payload(org_id, invoice.id, distance_km=9_999),
                )

    async def test_an_unknown_invoice_is_not_found(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.generate_eway_bill(
                    session, ctx, StubEwayClient(), _generate_payload(org_id, uuid.uuid4())
                )

    async def test_a_second_bill_while_one_stands_is_refused(self, session, org_id) -> None:
        """Two live bills for one movement is two movements as far as a checkpoint is concerned."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )
        with pytest.raises(ConflictProblem, match="already has e-Way bill"):
            async with session.begin():
                await services.generate_eway_bill(
                    session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
                )

    async def test_a_new_bill_is_allowed_once_the_old_one_has_expired(
        self, session, org_id
    ) -> None:
        """An expired bill is spent, not standing — the goods still have to move."""
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            first = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id),
                at=raised,
            )
        async with session.begin():
            second = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id),
                at=raised + timedelta(days=5),
            )
        assert second.id != first.id
        assert second.status == "generated"

    async def test_a_portal_rejection_is_recorded_rather_than_raised(self, session, org_id) -> None:
        """An attempt nobody recorded is an attempt nobody can prove was made."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                # Rail with no transporter and no transport document: the portal's own refusal.
                _generate_payload(org_id, invoice.id, transport_mode="rail", vehicle_no=None),
            )
        assert bill.status == "failed"
        assert bill.error_code == "114"
        assert bill.eway_bill_no is None
        assert bill.valid_until is None

    async def test_an_outage_is_a_pending_attempt(self, session, org_id) -> None:
        """The document is fine, the portal is not — and the goods are already loaded, so
        somebody is going to try again."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session, ctx, _Unavailable(), _generate_payload(org_id, invoice.id)
            )
        assert bill.status == "pending"
        assert bill.error_code == domain.ERR_PORTAL_UNAVAILABLE

    async def test_a_pending_attempt_does_not_block_a_retry(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, _Unavailable(), _generate_payload(org_id, invoice.id)
            )
        async with session.begin():
            retried = await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )
        assert retried.status == "generated"

    async def test_generating_without_a_registration_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx, gstin=None)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-EW-NOREG")
            )
        with pytest.raises(BusinessRuleProblem, match="no GST registration"):
            async with session.begin():
                await services.generate_eway_bill(
                    session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
                )

    async def test_generating_announces_it_with_the_deadline(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_EWAY_GENERATED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["eway_bill_no"] == bill.eway_bill_no
        assert events[0].payload["valid_until"] == bill.valid_until.isoformat()

    async def test_a_refusal_says_whether_it_is_worth_retrying(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id, transport_mode="rail", vehicle_no=None),
            )
        async with session.begin():
            invoice2 = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(invoice.customer_id, "INV-EW-OUT"),
            )
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, _Unavailable(), _generate_payload(org_id, invoice2.id)
            )
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_EWAY_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        # Since C4 `failed` is terminal only: the refusal says so, and the outage — which is being
        # retried — is announced as `retry_scheduled` instead.
        retryable = {e.payload["error_code"]: e.payload["retryable"] for e in events}
        assert retryable == {"114": False}
        async with session.begin():
            scheduled = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_EWAY_RETRY_SCHEDULED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [e.payload["error_code"] for e in scheduled] == [domain.ERR_PORTAL_UNAVAILABLE]


class TestCancelling:
    async def test_a_cancellation_inside_the_window_appends_a_row(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            generated = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id),
                at=raised,
            )
        async with session.begin():
            cancelled = await services.cancel_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                CancelEwayIn(
                    organization_id=org_id, invoice_id=invoice.id, reason_code="2", remark="off"
                ),
                at=raised + timedelta(hours=3),
            )
            history = await selectors.eway_bills_for_invoice(session, org_id, invoice.id)

        assert cancelled.id != generated.id
        assert cancelled.status == "cancelled"
        # The generation row is left exactly as it was, so "raised, then withdrawn" reads in order.
        visible = selectors.visible_attempts(history)
        assert [b.status for b in visible] == ["cancelled", "generated"]
        assert visible[1].eway_bill_no == generated.eway_bill_no
        # Underneath, each act was claimed before the portal was asked (D3): the answered claims
        # stay on the append-only ledger, ranked behind the outcome they share an instant with.
        assert [b.status for b in history] == ["cancelled", "cancelling", "generated", "submitting"]

    async def test_outside_the_window_it_is_refused_and_names_the_deadline(
        self, session, org_id
    ) -> None:
        """The window is arithmetic over the *stored* `valid_from`, so the deadline a screen shows
        and the deadline enforced here are the same number."""
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx, "INV-EW-LONG")
        async with session.begin():
            await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                # Ten days of validity, so the bill is still live when the cancel window shuts.
                _generate_payload(org_id, invoice.id, distance_km=1_800),
                at=raised,
            )
        with pytest.raises(BusinessRuleProblem, match="the window closed at"):
            async with session.begin():
                await services.cancel_eway_bill(
                    session,
                    ctx,
                    StubEwayClient(),
                    CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
                    at=raised + timedelta(hours=25),
                )

    async def test_an_expired_bill_cannot_be_cancelled(self, session, org_id) -> None:
        """Spent rather than withdrawable — the movement happened."""
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id),
                at=raised,
            )
        with pytest.raises(BusinessRuleProblem, match="no live e-Way bill"):
            async with session.begin():
                await services.cancel_eway_bill(
                    session,
                    ctx,
                    StubEwayClient(),
                    CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
                    at=raised + timedelta(days=4),
                )

    async def test_cancelling_when_there_is_nothing_to_cancel_is_refused(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="no live e-Way bill"):
            async with session.begin():
                await services.cancel_eway_bill(
                    session,
                    ctx,
                    StubEwayClient(),
                    CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="1"),
                )

    async def test_an_unknown_reason_code_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )
        with pytest.raises(BusinessRuleProblem, match="Cancellation reason"):
            async with session.begin():
                await services.cancel_eway_bill(
                    session,
                    ctx,
                    StubEwayClient(),
                    CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="9"),
                )

    async def test_a_cancelled_document_may_raise_a_fresh_bill(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id), at=raised
            )
        async with session.begin():
            await services.cancel_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="3"),
                at=raised + timedelta(hours=1),
            )
        async with session.begin():
            again = await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id, vehicle_no="MH12ZZ9999"),
                at=raised + timedelta(hours=2),
            )
        assert again.status == "generated"


# --------------------------------------------------------------------- the read model


class TestTheReadModel:
    async def test_an_unmet_obligation_is_visible_rather_than_absent(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)

        res = await client.get(f"/api/v1/finance/invoices/{invoice.id}/eway")
        assert res.status_code == 200
        assert res.json() == {
            "required": True,
            "status": None,
            "eway_bill_no": None,
            "valid_from": None,
            "valid_until": None,
            "terminal": False,
            "cancellable_until": None,
            "remedy": None,
            "attempts": 0,
            # The C4 retry fields: nothing attempted, so nothing to explain and nothing to retry.
            "remedy_detail": None,
            "error_code": None,
            "error": None,
            "attempt": None,
            "max_attempts": domain.EWAY_MAX_ATTEMPTS,
            "retry_at": None,
            "can_retry": False,
        }

    async def test_a_small_consignment_reports_no_obligation(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx, "INV-EW-SMALL", unit_minor=1_000)
        res = await client.get(f"/api/v1/finance/invoices/{invoice.id}/eway")
        assert res.json()["required"] is False

    async def test_a_live_bill_offers_cancel_until_the_window_shuts(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx, "INV-EW-READ")
        async with session.begin():
            await services.generate_eway_bill(
                session,
                ctx,
                StubEwayClient(),
                _generate_payload(org_id, invoice.id, distance_km=1_800),
                at=raised,
            )
        async with session.begin():
            bills = await selectors.eway_bills_for_invoice(session, org_id, invoice.id)

        inside = selectors.to_eway_bill_out(bills[0], at=raised + timedelta(hours=2))
        outside = selectors.to_eway_bill_out(bills[0], at=raised + timedelta(hours=30))
        assert inside.remedy == "cancel"
        assert inside.effective_status == "generated"
        assert outside.remedy is None
        assert outside.effective_status == "generated"

    async def test_the_read_reports_expired_without_the_row_changing(self, session, org_id) -> None:
        """The append-only table cannot be updated when the clock moves, so `expired` has to be a
        read. Same row, two moments, two answers."""
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id), at=raised
            )
        async with session.begin():
            bills = await selectors.eway_bills_for_invoice(session, org_id, invoice.id)

        assert bills[0].status == "generated"
        assert selectors.to_eway_bill_out(bills[0], at=raised).effective_status == "generated"
        assert (
            selectors.to_eway_bill_out(bills[0], at=raised + timedelta(days=3)).effective_status
            == "expired"
        )

    async def test_the_state_calls_an_expired_bill_terminal(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        raised = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id), at=raised
            )
        async with session.begin():
            latest, counts = await selectors.latest_eway_bills(session, org_id, [invoice.id])
            state = selectors.to_eway_state(
                invoice, latest[invoice.id], counts[invoice.id], at=raised + timedelta(days=3)
            )
        assert state.status == "expired"
        assert state.terminal is True
        assert state.remedy is None
        assert state.attempts == 1


class TestTheEndpoints:
    async def test_the_internal_route_raises_and_cancels(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)

        raised = await client.post(
            BILLS,
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "distance_km": 150,
                "vehicle_no": "MH12AB1234",
            },
            headers=service_token_headers(),
        )
        assert raised.status_code == 201
        body = raised.json()
        assert body["status"] == "generated"
        assert body["effective_status"] in ("generated", "expired")
        assert body["valid_until"] is not None

        cancelled = await client.post(
            CANCEL,
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "reason_code": "2",
                "remark": "order pulled",
            },
            headers=service_token_headers(),
        )
        assert cancelled.status_code == 201
        assert cancelled.json()["status"] == "cancelled"

    async def test_a_portal_rejection_is_a_201_carrying_the_failure(
        self, client, session, org_id
    ) -> None:
        """The attempt happened and is now part of the document's history; the caller's job is to
        read the code, not to treat a recorded fact as a transport failure."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        res = await client.post(
            BILLS,
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "distance_km": 150,
                "transport_mode": "rail",
            },
            headers=service_token_headers(),
        )
        assert res.status_code == 201
        assert res.json()["status"] == "failed"
        assert res.json()["error_code"] == "114"

    async def test_the_internal_route_needs_a_service_token(self, client, session, org_id) -> None:
        res = await client.post(
            BILLS,
            json={
                "organization_id": str(org_id),
                "invoice_id": str(uuid.uuid4()),
                "distance_km": 10,
            },
        )
        assert res.status_code in (401, 403)

    async def test_the_history_and_the_list_read_back(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )

        history = await client.get(f"/api/v1/finance/invoices/{invoice.id}/eway-bills")
        assert history.status_code == 200
        assert len(history.json()) == 1

        listed = await client.get("/api/v1/finance/eway-bills")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["invoice_number"] == "INV-EW-1"
        assert listed.json()["items"][0]["customer_name"] == "Consignee"

        filtered = await client.get("/api/v1/finance/eway-bills", params={"status": "failed"})
        assert filtered.json()["total"] == 0

    async def test_an_invoice_from_another_organization_is_not_found(self, client) -> None:
        res = await client.get(f"/api/v1/finance/invoices/{uuid.uuid4()}/eway-bills")
        assert res.status_code == 404


# --------------------------------------------------------------------- the guarantees


class TestBillsAreAppendOnlyAtTheDatabase:
    """The guarantee, checked where it is made. These run as the same NOBYPASSRLS app role the
    service uses, so what they prove is what production has."""

    async def _bill_id(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_payload(org_id, invoice.id)
            )
            return bill.id

    async def test_an_update_is_refused(self, session, org_id) -> None:
        bill_id = await self._bill_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(EwayBill).where(EwayBill.id == bill_id).values(status="cancelled")
                )

    async def test_extending_the_validity_is_refused(self, session, org_id) -> None:
        """The row an operator would most want to edit, and the one that must not be editable:
        moving `valid_until` forward is forging a licence for goods on a road."""
        bill_id = await self._bill_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(EwayBill)
                    .where(EwayBill.id == bill_id)
                    .values(valid_until=datetime(2027, 1, 1, tzinfo=UTC))
                )

    async def test_a_delete_is_refused(self, session, org_id) -> None:
        bill_id = await self._bill_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(delete(EwayBill).where(EwayBill.id == bill_id))

    async def test_a_soft_delete_is_refused_too(self, session, org_id) -> None:
        bill_id = await self._bill_id(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(EwayBill)
                    .where(EwayBill.id == bill_id)
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
                            " WHERE n.nspname = 'finance' AND c.relname = 'eway_bills'"
                            "   AND NOT t.tgisinternal"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_eway_bills_append_only" in found


class TestCrossTenantIsolation:
    async def test_another_organizations_bill_is_invisible(
        self, session, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            other_ctx = admin_ctx(other_org)
            invoice = await _consignment(admin_session, other_ctx, "INV-EW-OTHER")
            bill = await services.generate_eway_bill(
                admin_session,
                other_ctx,
                StubEwayClient(),
                _generate_payload(other_org, invoice.id),
            )
            their_invoice_id = invoice.id
            their_bill_id = bill.id

        async with session.begin():
            history = await selectors.eway_bills_for_invoice(session, org_id, their_invoice_id)
            found = (
                (await session.execute(select(EwayBill).where(EwayBill.id == their_bill_id)))
                .scalars()
                .all()
            )
        assert history == []
        assert found == []

    async def test_the_unbound_session_can_write_nothing(
        self, unbound_app_session, session, org_id
    ) -> None:
        """The RLS net. Without the per-transaction GUC, `current_org()` is NULL and the forced
        policy's WITH CHECK refuses the insert outright."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            invoice = await _consignment(session, ctx)

        with pytest.raises(Exception):  # noqa: B017 - any refusal is the assertion
            async with unbound_app_session.begin():
                unbound_app_session.add(
                    EwayBill(
                        organization_id=org_id,
                        invoice_id=invoice.id,
                        status="generated",
                        transport_mode="road",
                        distance_km=10,
                        over_dimensional=False,
                        consignment_value_minor=100_000,
                        attempted_at=datetime.now(UTC),
                    )
                )
                await unbound_app_session.flush()
