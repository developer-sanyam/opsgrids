"""E-invoicing end to end against real PostgreSQL — the state machine, not the network.

The portal is stubbed and nothing else is. These tests drive the real write path, the real
policies and the real append-only trigger, and they assert the things that would cost a customer
money if they were wrong: that the tax lands under the right head, that a document filed twice
keeps one IRN, that a refusal is recorded rather than swallowed, and that no row of the filing
ledger can ever be altered after it is written.

`einvoice_filings` is the evidence a business shows when the tax authority asks whether a document
was reported. Evidence the application can rewrite is not evidence, which is why the last class
here goes to the database directly and tries.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update

from app import domain, selectors, services
from app.irp.base import IrpAck, IrpInvoicePayload, IrpLine, IrpRejected, IrpUnavailable
from app.irp.stub import StubIrpClient, compute_irn
from app.models import EInvoiceFiling, OutboxEvent
from app.schemas import (
    CancelFilingIn,
    CustomerCreate,
    FileInvoiceIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
from platform_core.errors import BusinessRuleProblem
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

FILINGS = "/internal/v1/einvoice/filings"
CANCEL = "/internal/v1/einvoice/filings:cancel"

#: The seller: Maharashtra (27). Checksum-valid, like everything else this service stores.
SELLER = "27AABCA9012D1Z5"
#: A Maharashtra buyer — same state as the seller, so CGST+SGST.
BUYER_SAME_STATE = "27AAECO9012H1ZG"
#: A Gujarat buyer — different state, so IGST.
BUYER_OTHER_STATE = "24AABCM1234F1Z3"


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=gstin, legal_name="Atmora Industries Private Limited"),
    )


async def _customer(session, ctx, *, gstin: str | None, state: str = "Maharashtra", name="Buyer"):
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
        ),
    )


def _invoice_payload(customer_id, number: str, **overrides) -> InvoiceCreate:
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 7, 1),
        "due_date": date(2026, 7, 31),
        "tax_rate_pct": 18,
        "lines": [
            InvoiceLineCreate(
                description="Cotton yarn",
                quantity=100,
                unit_price_minor=25_000,
                hsn_sac="520512",
            )
        ],
    }
    payload.update(overrides)
    return InvoiceCreate(**payload)


# --------------------------------------------------------------------- registration


class TestGstRegistration:
    async def test_the_state_code_is_derived_from_the_gstin_not_entered_beside_it(
        self, session, org_id
    ) -> None:
        """The two can never disagree, because only one of them is an input."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="Atmora Industries Private Limited"),
            )
        assert registration.gstin == SELLER
        assert registration.state_code == "27"
        assert registration.is_primary is True

    async def test_an_invalid_gstin_is_refused_before_anything_is_written(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="not a valid GSTIN"):
                await services.set_gst_registration(
                    session,
                    ctx,
                    GstRegistrationUpsert(gstin="27AABCA9012D1Z9", legal_name="Wrong Checksum Ltd"),
                )
        async with session.begin():
            assert await selectors.get_gst_registration(session, org_id) is None

    async def test_setting_a_second_primary_demotes_the_first(self, session, org_id) -> None:
        """A partial unique index makes "at most one primary" a database guarantee, so the
        demotion is not a nicety — without it the second insert fails."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx, SELLER)
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(
                    gstin=BUYER_OTHER_STATE,  # a Gujarat branch registration
                    legal_name="Atmora Industries Private Limited",
                ),
            )
        async with session.begin():
            registrations = await selectors.all_gst_registrations(session, org_id)
            primary = [r for r in registrations if r.is_primary]
        assert len(registrations) == 2
        assert [r.gstin for r in primary] == [BUYER_OTHER_STATE]

    async def test_updating_a_registration_leaves_a_filed_invoice_naming_the_old_number(
        self, session, org_id
    ) -> None:
        """The reason `invoices.seller_gstin` is a snapshot and not a join. A document filed under
        one identity must keep naming it however the configuration changes afterwards."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx, SELLER)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-SNAP-1")
            )
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="Atmora Industries Pvt Ltd"),
            )
            refreshed = await selectors.get_invoice(session, org_id, invoice.id)
        assert refreshed is not None
        assert refreshed.seller_gstin == SELLER

    async def test_the_read_route_names_the_state(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        res = await client.get("/api/v1/finance/gst/registration")
        assert res.status_code == 200
        body = res.json()
        assert body["gstin"] == SELLER
        assert body["state_code"] == "27"
        assert body["state_name"] == "Maharashtra"

    async def test_an_unconfigured_organization_is_404_not_an_empty_row(self, client) -> None:
        res = await client.get("/api/v1/finance/gst/registration")
        assert res.status_code == 404

    async def test_the_write_route_refuses_a_bad_checksum(self, client) -> None:
        res = await client.put(
            "/api/v1/finance/gst/registration",
            json={"gstin": "27AABCA9012D1Z9", "legal_name": "Wrong Checksum Ltd"},
        )
        assert res.status_code == 422
        assert "GSTIN" in res.json()["detail"]


# --------------------------------------------------------------------- the split on a document


class TestInvoiceTaxFields:
    async def test_an_intra_state_supply_is_taxed_cgst_and_sgst(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-INTRA-1")
            )
        # 100 x ₹250 = ₹25,000 taxable; 18 % = ₹4,500, halved into ₹2,250 each.
        assert invoice.place_of_supply == "27"
        assert invoice.supply_type == "b2b"
        assert invoice.subtotal_minor == 25_00_000
        assert (invoice.cgst_minor, invoice.sgst_minor, invoice.igst_minor) == (
            2_25_000,
            2_25_000,
            0,
        )
        assert invoice.tax_minor == invoice.cgst_minor + invoice.sgst_minor
        assert invoice.total_minor == invoice.subtotal_minor + invoice.tax_minor

    async def test_an_inter_state_supply_is_taxed_igst(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_OTHER_STATE, state="Gujarat")
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-INTER-1")
            )
        assert invoice.place_of_supply == "24"
        assert (invoice.cgst_minor, invoice.sgst_minor, invoice.igst_minor) == (0, 0, 4_50_000)

    async def test_a_customer_without_a_gstin_is_b2c(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=None, state="Maharashtra")
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-B2C-1")
            )
        assert invoice.supply_type == "b2c"
        assert invoice.buyer_gstin is None
        # Still taxed — B2C is not tax-free, it is merely not e-invoiced.
        assert invoice.tax_minor == 4_50_000
        assert not domain.requires_einvoice(
            supply_type=invoice.supply_type, document_type=invoice.document_type
        )

    async def test_an_sez_supply_within_the_state_is_still_igst(self, session, org_id) -> None:
        """Statute beats geography (IGST Act s. 7(5)(b)), and getting it wrong is a refund claim
        rather than a rounding difference."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-SEZ-1", supply_type="sez")
            )
        assert invoice.place_of_supply == "27"  # same state as the seller
        assert (invoice.cgst_minor, invoice.sgst_minor, invoice.igst_minor) == (0, 0, 4_50_000)

    async def test_reverse_charge_states_the_rate_and_charges_nothing(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-RCM-1", reverse_charge=True)
            )
        assert invoice.reverse_charge is True
        assert invoice.tax_minor == 0
        assert invoice.total_minor == invoice.subtotal_minor
        assert float(invoice.lines[0].tax_rate_pct) == 18.0

    async def test_a_line_may_carry_its_own_rate(self, session, org_id) -> None:
        """The everyday case: freight at 5 % on a goods invoice at 18 %."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_OTHER_STATE, state="Gujarat")
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-MIXED-1",
                    lines=[
                        InvoiceLineCreate(
                            description="Goods",
                            quantity=1,
                            unit_price_minor=10_00_000,
                            hsn_sac="520512",
                        ),
                        InvoiceLineCreate(
                            description="Freight",
                            quantity=1,
                            unit_price_minor=1_00_000,
                            hsn_sac="996511",
                            tax_rate_pct=5,
                        ),
                    ],
                ),
            )
        assert [float(line.tax_rate_pct) for line in invoice.lines] == [18.0, 5.0]
        assert [line.igst_minor for line in invoice.lines] == [1_80_000, 5_000]
        assert invoice.igst_minor == 1_85_000
        assert invoice.tax_minor == sum(line.igst_minor for line in invoice.lines)

    async def test_a_rate_outside_the_bands_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            with pytest.raises(BusinessRuleProblem, match="not a GST rate band"):
                await services.create_invoice(
                    session, ctx, _invoice_payload(customer.id, "INV-BAD-RATE", tax_rate_pct=17)
                )

    async def test_a_malformed_hsn_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            with pytest.raises(BusinessRuleProblem, match="HSN/SAC"):
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer.id,
                        "INV-BAD-HSN",
                        lines=[
                            InvoiceLineCreate(
                                description="Goods",
                                quantity=1,
                                unit_price_minor=100,
                                hsn_sac="52A512",
                            )
                        ],
                    ),
                )

    async def test_a_customer_gstin_is_checksum_verified_on_write(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="GSTIN is not valid"):
                await _customer(session, ctx, gstin="24AABCM1234F1Z5")  # wrong check digit

    async def test_the_whole_seeded_book_foots_head_by_head(self, session, org_id, book) -> None:
        """Every document the demo seed raises, over four rate bands and both split shapes."""
        async with session.begin():
            invoices = await selectors.all_invoices(session, org_id)
        assert len(invoices) == 21
        for invoice in invoices:
            heads = invoice.cgst_minor + invoice.sgst_minor + invoice.igst_minor
            assert heads == invoice.tax_minor, invoice.number
            assert invoice.subtotal_minor + invoice.tax_minor == invoice.total_minor, invoice.number
            assert sum(line.amount_minor for line in invoice.lines) == invoice.subtotal_minor
            assert sum(line.cgst_minor for line in invoice.lines) == invoice.cgst_minor
            assert sum(line.sgst_minor for line in invoice.lines) == invoice.sgst_minor
            assert sum(line.igst_minor for line in invoice.lines) == invoice.igst_minor
            # Never both shapes on one document.
            assert not (invoice.igst_minor and (invoice.cgst_minor or invoice.sgst_minor))

    async def test_the_seeded_book_exercises_both_split_shapes(self, session, org_id, book) -> None:
        """A demo where every supply is inter-state would leave half the rules untested by eye."""
        async with session.begin():
            invoices = await selectors.all_invoices(session, org_id)
        assert any(inv.cgst_minor for inv in invoices)
        assert any(inv.igst_minor for inv in invoices)


# --------------------------------------------------------------------- filing


class TestFiling:
    async def test_a_filed_document_carries_the_government_irn(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-FILE-1")
            )

        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "filed"
        assert body["error_code"] is None
        assert body["ack_no"] and body["qr_payload"]

        # Recomputed here from the published formula rather than read back from the stub, so this
        # asserts the IRN is *right*, not merely that it is stable.
        expected = compute_irn(
            seller_gstin=SELLER,
            document_type="INV",
            document_number="INV-FILE-1",
            financial_year="2026-27",
        )
        assert body["irn"] == expected
        assert len(body["irn"]) == 64

    async def test_the_same_document_always_yields_the_same_irn(self) -> None:
        """Deterministic by statute: a document has one IRN, which is how the portal detects a
        duplicate and how a retry after a timeout is safe."""
        args = {
            "seller_gstin": SELLER,
            "document_type": "INV",
            "document_number": "INV-FILE-1",
            "financial_year": "2026-27",
        }
        assert compute_irn(**args) == compute_irn(**args)
        assert compute_irn(**{**args, "document_number": "INV-FILE-2"}) != compute_irn(**args)

    async def test_filing_needs_no_credentials_and_opens_no_socket(self) -> None:
        """The one seam. A test that would fail the moment somebody made the stub reach out."""
        import socket

        client = StubIrpClient()
        original = socket.socket

        def _forbidden(*args, **kwargs):
            raise AssertionError("the stub IRP client must not open a socket")

        socket.socket = _forbidden  # type: ignore[assignment]
        try:
            ack = client.register(_stub_payload())
        finally:
            socket.socket = original  # type: ignore[assignment]
        assert isinstance(ack, IrpAck)

    async def test_the_attempt_shows_on_the_invoice_read_model(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-FILE-2")
            )

        before = await client.get(f"/api/v1/finance/invoices/{invoice.id}")
        assert before.json()["einvoice"] == {
            "required": True,
            "status": None,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "error_code": None,
            "error": None,
            "attempted_at": None,
            "attempts": 0,
        }

        await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        after = (await client.get(f"/api/v1/finance/invoices/{invoice.id}")).json()["einvoice"]
        assert after["status"] == "filed"
        assert after["attempts"] == 1
        assert after["irn"]

    async def test_a_b2c_document_reports_no_obligation_and_cannot_be_filed(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=None)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-B2C-2")
            )
        read = await client.get(f"/api/v1/finance/invoices/{invoice.id}")
        assert read.json()["einvoice"]["required"] is False

        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        assert res.status_code == 422
        assert "does not require an e-invoice" in res.json()["detail"]

    async def test_filing_without_a_registration_is_refused_and_records_nothing(
        self, client, session, org_id
    ) -> None:
        """Nothing was attempted, so nothing is written: a ledger of attempts that never happened
        is worse than no ledger."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-NOREG-1")
            )
            # Read the id out before the refused call: the route's rollback expires every ORM
            # object on this shared session, so touching one afterwards would go back to the
            # database outside a transaction.
            invoice_id = invoice.id

        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice_id)},
        )
        assert res.status_code == 422
        assert "no GST registration configured" in res.json()["detail"]

        history = await client.get(f"/api/v1/finance/invoices/{invoice_id}/filings")
        assert history.json() == []

    async def test_filing_twice_is_a_conflict_naming_the_live_irn(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-DUP-1")
            )
        body = {"organization_id": str(org_id), "invoice_id": str(invoice.id)}
        first = await client.post(FILINGS, headers=service_token_headers(), json=body)
        assert first.status_code == 201
        second = await client.post(FILINGS, headers=service_token_headers(), json=body)
        assert second.status_code == 409
        assert first.json()["irn"] in second.json()["detail"]

    async def test_an_unknown_invoice_is_404(self, client, org_id) -> None:
        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(uuid.uuid4())},
        )
        assert res.status_code == 404

    async def test_a_credit_note_is_filed_as_a_credit_note(self, client, session, org_id) -> None:
        """GST's correction mechanism is a credit note, which is also this platform's. The two
        models line up with nothing left over — the document type is the only mapping."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CN-BASE")
            )
            credit = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "CN-CN-1",
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                    lines=[
                        InvoiceLineCreate(
                            description="Return",
                            quantity=-10,
                            unit_price_minor=25_000,
                            hsn_sac="520512",
                        )
                    ],
                ),
            )
        assert credit.total_minor < 0
        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(credit.id)},
        )
        assert res.status_code == 201
        assert res.json()["irn"] == compute_irn(
            seller_gstin=SELLER,
            document_type="CRN",
            document_number="CN-CN-1",
            financial_year="2026-27",
        )


# --------------------------------------------------------------------- refusals


class TestPortalRefusals:
    async def test_a_missing_hsn_is_recorded_as_a_failed_attempt_not_an_error(
        self, client, session, org_id
    ) -> None:
        """A rejection is a *fact about the document*, so it goes in the ledger and comes back
        201. Turning it into a 4xx would lose the one thing a compliance audit needs: that the
        attempt was made and what the portal said."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-NOHSN-1",
                    lines=[
                        InvoiceLineCreate(
                            description="Goods", quantity=1, unit_price_minor=25_00_000
                        )
                    ],
                ),
            )
        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "2227"
        assert body["irn"] is None
        assert "HSN" in body["error"]

    async def test_a_rejection_can_be_retried_and_both_attempts_survive(
        self, client, session, org_id
    ) -> None:
        """The history is the point: a retry is a second row, and the first is still there."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-RETRY-1",
                    lines=[
                        InvoiceLineCreate(
                            description="Goods", quantity=1, unit_price_minor=25_00_000
                        )
                    ],
                ),
            )
        body = {"organization_id": str(org_id), "invoice_id": str(invoice.id)}
        await client.post(FILINGS, headers=service_token_headers(), json=body)
        await client.post(FILINGS, headers=service_token_headers(), json=body)

        history = await client.get(f"/api/v1/finance/invoices/{invoice.id}/filings")
        assert history.status_code == 200
        rows = history.json()
        assert len(rows) == 2
        assert {row["status"] for row in rows} == {"failed"}
        # Newest first.
        assert rows[0]["attempted_at"] >= rows[1]["attempted_at"]

    async def test_an_outage_is_recorded_as_pending_and_marked_retryable(
        self, client, session, org_id
    ) -> None:
        """The opposite of a rejection: the document is fine, the portal is not — so the event
        says `retryable`, and the identical document may be sent again."""
        from app.deps import get_irp_client
        from app.main import app

        class DownPortal:
            name = "down"

            def register(self, payload: IrpInvoicePayload) -> IrpAck:
                raise IrpUnavailable("the portal did not answer in time")

            def cancel(self, **kwargs):
                raise IrpUnavailable("the portal did not answer in time")

        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-DOWN-1")
            )

        app.dependency_overrides[get_irp_client] = DownPortal
        try:
            res = await client.post(
                FILINGS,
                headers=service_token_headers(),
                json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
            )
        finally:
            app.dependency_overrides.pop(get_irp_client, None)

        assert res.status_code == 201
        assert res.json()["status"] == "pending"
        assert res.json()["error_code"] == "unavailable"

    async def test_the_portal_refuses_tax_under_the_wrong_head(self) -> None:
        """The stub validates the way the portal does, so a mis-split document fails here rather
        than at a deadline."""
        payload = _stub_payload()
        wrong = dataclasses.replace(
            payload,
            cgst_minor=0,
            sgst_minor=0,
            igst_minor=payload.cgst_minor + payload.sgst_minor,
            lines=[
                dataclasses.replace(
                    line,
                    cgst_minor=0,
                    sgst_minor=0,
                    igst_minor=line.cgst_minor + line.sgst_minor,
                )
                for line in payload.lines
            ],
        )
        with pytest.raises(IrpRejected) as raised:
            StubIrpClient().register(wrong)
        assert raised.value.code == "2172"

    async def test_the_portal_refuses_a_document_that_does_not_foot(self) -> None:
        payload = _stub_payload()
        broken = dataclasses.replace(payload, total_minor=payload.total_minor + 1)
        with pytest.raises(IrpRejected) as raised:
            StubIrpClient().register(broken)
        assert raised.value.code == "2189"

    async def test_the_portal_refuses_a_b2b_document_with_no_recipient_gstin(self) -> None:
        payload = _stub_payload()
        anonymous = dataclasses.replace(payload, buyer_gstin=None)
        with pytest.raises(IrpRejected) as raised:
            StubIrpClient().register(anonymous)
        assert raised.value.code == "2233"

    async def test_the_portal_refuses_an_invalid_supplier_gstin(self) -> None:
        payload = _stub_payload()
        bad = dataclasses.replace(payload, seller_gstin="27AABCA9012D1Z9")
        with pytest.raises(IrpRejected) as raised:
            StubIrpClient().register(bad)
        assert raised.value.code == "3028"


# --------------------------------------------------------------------- cancellation


class TestCancellation:
    async def test_a_cancellation_is_a_new_row_leaving_the_registration_visible(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CANCEL-1")
            )
        filed = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        assert filed.status_code == 201

        cancelled = await client.post(
            CANCEL,
            headers=service_token_headers(),
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "reason_code": "2",
                "remark": "Data entry mistake",
            },
        )
        assert cancelled.status_code == 201
        assert cancelled.json()["status"] == "cancelled"

        history = (await client.get(f"/api/v1/finance/invoices/{invoice.id}/filings")).json()
        assert [row["status"] for row in history] == ["cancelled", "filed"]
        # The registration row still carries the IRN it was issued: the history reads in order.
        assert history[1]["irn"] == filed.json()["irn"]

    async def test_an_unknown_reason_code_is_refused(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CANCEL-2")
            )
        await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        res = await client.post(
            CANCEL,
            headers=service_token_headers(),
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "reason_code": "9",
            },
        )
        assert res.status_code == 422

    async def test_an_unfiled_document_has_no_irn_to_cancel(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CANCEL-3")
            )
        res = await client.post(
            CANCEL,
            headers=service_token_headers(),
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice.id),
                "reason_code": "3",
            },
        )
        assert res.status_code == 422
        assert "no registered IRN" in res.json()["detail"]

    async def test_past_the_window_the_correction_is_a_credit_note(self, session, org_id) -> None:
        """Enforced against this platform's own ledger rather than by asking the portal — which
        is what lets a screen offer the right one of the two choices."""
        ctx = admin_ctx(org_id)
        filed_at = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CANCEL-4")
            )
            await services.file_invoice(
                session,
                ctx,
                StubIrpClient(),
                FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id),
                at=filed_at,
            )
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="more than 24 hours ago"):
                await services.cancel_filing(
                    session,
                    ctx,
                    StubIrpClient(),
                    CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
                    at=filed_at + timedelta(hours=25),
                )


# --------------------------------------------------------------------- events


class TestOutbox:
    async def test_a_successful_filing_stages_the_event_an_agent_consumes(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-EVT-1")
            )
        res = await client.post(
            FILINGS,
            headers=service_token_headers(),
            json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
        )
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_FILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["irn"] == res.json()["irn"]
        assert payload["number"] == "INV-EVT-1"
        assert payload["portal"] == "stub-irp"

    async def test_the_issued_event_says_whether_the_document_needs_filing(
        self, session, org_id
    ) -> None:
        """So an auto-filing agent can decide from the event alone, without reading back."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            b2b = await _customer(session, ctx, gstin=BUYER_SAME_STATE, name="Registered Buyer")
            b2c = await _customer(session, ctx, gstin=None, name="Walk-in Buyer")
            await services.create_invoice(session, ctx, _invoice_payload(b2b.id, "INV-EVT-B2B"))
            await services.create_invoice(session, ctx, _invoice_payload(b2c.id, "INV-EVT-B2C"))
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_INVOICE_ISSUED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        by_number = {row.payload["number"]: row.payload for row in rows}
        assert by_number["INV-EVT-B2B"]["requires_einvoice"] is True
        assert by_number["INV-EVT-B2B"]["place_of_supply"] == "27"
        assert by_number["INV-EVT-B2C"]["requires_einvoice"] is False


# --------------------------------------------------------------------- append-only, at the DB


class TestFilingsAreAppendOnlyAtTheDatabase:
    """The guarantee, checked where it is made rather than where it is convenient.

    A `REVOKE UPDATE, DELETE` would not have held: `apply_schema_grants` re-grants DML on every
    table at the end of every migration run. The trigger is independent of privileges, and these
    tests run as the same NOBYPASSRLS app role the service uses — so what they prove is what
    production has.
    """

    async def _filed(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=BUYER_SAME_STATE)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-APPEND-1")
            )
            filing = await services.file_invoice(
                session,
                ctx,
                StubIrpClient(),
                FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id),
            )
            return filing.id

    async def test_an_update_is_refused_by_the_database(self, session, org_id) -> None:
        filing_id = await self._filed(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(EInvoiceFiling)
                    .where(EInvoiceFiling.id == filing_id)
                    .values(status="cancelled")
                )

    async def test_a_delete_is_refused_by_the_database(self, session, org_id) -> None:
        filing_id = await self._filed(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(delete(EInvoiceFiling).where(EInvoiceFiling.id == filing_id))

    async def test_a_soft_delete_is_refused_too(self, session, org_id) -> None:
        """`deleted_at` is on the row because every tenant table carries it. Setting it is still
        an UPDATE, so the trigger refuses that route to erasure as well."""
        filing_id = await self._filed(session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(EInvoiceFiling)
                    .where(EInvoiceFiling.id == filing_id)
                    .values(deleted_at=datetime.now(UTC))
                )

    async def test_the_trigger_is_installed_on_the_table(self, session) -> None:
        """A guard against the guard: if the migration stopped creating the trigger, the three
        tests above would pass for the wrong reason only if something else refused the write —
        this asserts the actual mechanism is present."""
        async with session.begin():
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'finance' AND c.relname = 'einvoice_filings' "
                            "AND NOT t.tgisinternal"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_einvoice_filings_append_only" in found

    async def test_the_gst_registration_is_deliberately_not_append_only(
        self, session, org_id
    ) -> None:
        """Configuration, not evidence. A trade name changes; what must not change is the GSTIN a
        filed invoice was filed under, and that is a snapshot on the invoice."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="Atmora Industries Private Limited"),
            )
            registration_id = registration.id
        async with session.begin():
            updated = await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(
                    gstin=SELLER, legal_name="Atmora Industries Pvt Ltd", trade_name="Atmora"
                ),
            )
        assert updated.id == registration_id
        assert updated.legal_name == "Atmora Industries Pvt Ltd"


# --------------------------------------------------------------------- a stub payload


def _stub_payload() -> IrpInvoicePayload:
    """A well-formed intra-state document, for the pure portal-validation tests."""
    taxable = 25_00_000
    split = domain.split_gst(taxable, Decimal("18"), intra_state=True)
    return IrpInvoicePayload(
        document_type="INV",
        document_number="INV-STUB-1",
        document_date=date(2026, 7, 1),
        supply_type="b2b",
        reverse_charge=False,
        seller_gstin=SELLER,
        seller_legal_name="Atmora Industries Private Limited",
        seller_state_code="27",
        buyer_gstin=BUYER_SAME_STATE,
        buyer_legal_name="Orbit Logistics",
        place_of_supply="27",
        taxable_minor=taxable,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
        total_minor=taxable + split.total_minor,
        financial_year="2026-27",
        generated_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        lines=[
            IrpLine(
                serial_no=1,
                description="Cotton yarn",
                hsn_sac="520512",
                quantity=Decimal("100"),
                unit_price_minor=25_000,
                taxable_minor=taxable,
                rate_pct=Decimal("18"),
                cgst_minor=split.cgst_minor,
                sgst_minor=split.sgst_minor,
                igst_minor=split.igst_minor,
            )
        ],
    )
