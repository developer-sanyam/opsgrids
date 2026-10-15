"""The customer invoice as a complete CGST Rule 46 tax invoice.

What this module proves, in the order the rule lists it: the supplier's name, address and GSTIN
are frozen onto every document; the serial number fits sixteen characters and runs per financial
year; the recipient's name, address and state are frozen too; each line carries its HSN/SAC,
unit, quantity, discount and taxable value; the rate and the tax split follow the two states;
round-off is part of the total and foots; and the supplier settings say when an invoice may be
issued at all. The bank account a customer pays into is sealed at rest, masked everywhere but the
printable copy, and every opening of it is audited.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app import domain, selectors, services
from app.irp.stub import StubIrpClient
from app.models import AuditLog, Invoice, SupplierProfile
from app.schemas import (
    CustomerCreate,
    CustomerUpdate,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
    SupplierProfileUpsert,
)
from platform_core.errors import BusinessRuleProblem
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"  # Maharashtra; PAN AABCA9012D
SELLER_PAN = "AABCA9012D"


def gstin(state: str, pan: str = "AAACB1234C") -> str:
    """A GSTIN that passes its checksum, for the state given."""
    body = f"{state}{pan}1Z"
    return body + domain.gstin_check_digit(body)


async def _register(session, ctx, *, address: bool = True) -> None:
    extra = (
        {
            "address_line1": "Plot 14, MIDC Bhosari",
            "address_line2": "Near Telco Road",
            "city": "Pune",
            "pincode": "411026",
        }
        if address
        else {}
    )
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd", **extra),
    )


async def _customer(session, ctx, **overrides):
    payload = {
        "name": "Konkan Traders",
        "city": "Pune",
        "state": "Maharashtra",
        "address_line1": "12 FC Road",
        "pincode": "411004",
    }
    payload.update(overrides)
    return await services.create_customer(session, ctx, CustomerCreate(**payload))


def _invoice(customer_id, number: str, *, lines=None, **over) -> InvoiceCreate:
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 10, 10),
        "due_date": date(2026, 10, 25),
        "tax_rate_pct": 18,
        "lines": lines
        or [
            InvoiceLineCreate(
                description="Steel brackets",
                quantity=10,
                unit="nos",
                unit_price_minor=123_45,
                discount_minor=50_00,
                hsn_sac="7326",
            )
        ],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


# --------------------------------------------------------------------- pure rules


class TestDomain:
    def test_a_document_number_fits_rule_46(self) -> None:
        assert domain.document_number("INV", "2026-27", 42) == "INV-2627-00042"
        assert domain.document_number("CN", "2026-27", 1) == "CN-2627-00001"
        # Past 99 999 the sequence widens and still fits sixteen characters.
        assert len(domain.document_number("INV", "2026-27", 1_234_567)) <= 16

    def test_round_off_takes_the_total_to_whole_rupees_symmetrically(self) -> None:
        assert domain.round_off_minor(1_234_49, "INR") == -49
        assert domain.round_off_minor(1_234_50, "INR") == 50
        assert domain.round_off_minor(-1_234_50, "INR") == -50
        assert domain.round_off_minor(1_234_00, "INR") == 0
        assert domain.round_off_minor(1_234_49, "USD") == 0

    def test_a_discount_reduces_the_magnitude_on_either_sign(self) -> None:
        assert domain.line_taxable_minor(1000, 100) == 900
        assert domain.line_taxable_minor(-1000, 100) == -900
        with pytest.raises(ValueError):
            domain.line_taxable_minor(1000, 1001)
        with pytest.raises(ValueError):
            domain.line_taxable_minor(1000, -1)

    def test_units_are_the_portal_codes(self) -> None:
        assert domain.normalise_uqc("pcs") == "PCS"
        assert domain.normalise_uqc("") is None
        with pytest.raises(ValueError):
            domain.normalise_uqc("pieces")

    def test_the_forty_percent_slab_exists_only_from_the_2025_change(self) -> None:
        forty = Decimal("40")
        assert forty in domain.GST_RATE_BANDS
        assert not domain.band_in_force(forty, date(2025, 9, 21))
        assert domain.band_in_force(forty, date(2025, 9, 22))
        assert domain.band_in_force(Decimal("12"), date(2026, 1, 1))

    def test_supplier_readiness_names_what_is_missing(self) -> None:
        assert domain.supplier_missing(has_registration=False) == list(
            domain.SUPPLIER_REQUIRED_FIELDS
        )
        assert domain.supplier_missing(
            has_registration=True, legal_name="X", address_line1="1 Road", city="Pune"
        ) == ["pincode"]
        assert (
            domain.supplier_missing(
                has_registration=True,
                legal_name="X",
                address_line1="1 Road",
                city="Pune",
                pincode="411001",
            )
            == []
        )

    @pytest.mark.parametrize(
        "fn,good,bad",
        [
            (domain.normalise_pan, "aabca 9012d", "AABC9012D"),
            (domain.normalise_ifsc, "hdfc0001234", "HDFC1001234"),
            (domain.normalise_pincode, "411 026", "011026"),
            (domain.normalise_account_number, "1234-5678-9012", "12345"),
            (domain.normalise_upi, "opsgrids@hdfcbank", "opsgrids"),
        ],
    )
    def test_supplier_identifiers_are_shape_checked(self, fn, good, bad) -> None:
        assert fn(good)
        with pytest.raises(ValueError):
            fn(bad)


# --------------------------------------------------------------------- issuing


class TestIssuing:
    async def test_lines_carry_unit_discount_item_and_the_taxable_value(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        item = uuid.uuid4()
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice(
                    customer.id,
                    "INV-T-1",
                    lines=[
                        InvoiceLineCreate(
                            description="Steel brackets",
                            quantity=10,
                            unit="nos",
                            unit_price_minor=123_45,
                            discount_minor=50_00,
                            hsn_sac="7326",
                            item_id=item,
                            item_sku="ITM-000007",
                        )
                    ],
                ),
            )
        line = invoice.lines[0]
        assert line.unit == "NOS"
        assert line.discount_minor == 50_00
        # 10 x 123.45 = 1234.50, less 50.00 discount = 1184.50 taxable.
        assert line.amount_minor == 1184_50
        assert line.item_id == item and line.item_sku == "ITM-000007"
        # Intra-state (both Maharashtra): 18 % of 1184.50 = 213.21, CGST 106.60 + SGST 106.61.
        assert (line.cgst_minor, line.sgst_minor, line.igst_minor) == (106_60, 106_61, 0)
        assert invoice.subtotal_minor == 1184_50
        assert invoice.total_minor == invoice.subtotal_minor + invoice.tax_minor

    async def test_round_off_is_inside_the_total_and_foots(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-T-2", round_off=True)
            )
        # 1184.50 + 213.21 = 1397.71 -> 1398.00, round-off +0.29.
        assert invoice.round_off_minor == 29
        assert invoice.total_minor == 1398_00
        assert invoice.total_minor == invoice.subtotal_minor + invoice.tax_minor + 29

    async def test_an_inter_state_buyer_is_charged_igst(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(
                session, ctx, gstin=gstin("29"), state="", city="Bengaluru", state_code="29"
            )
            invoice = await services.create_invoice(session, ctx, _invoice(customer.id, "INV-T-3"))
        assert invoice.place_of_supply == "29"
        assert invoice.igst_minor == 213_21 and invoice.cgst_minor == invoice.sgst_minor == 0

    async def test_the_billing_state_code_decides_place_of_supply_for_b2c(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, state="", city="Panaji", state_code="30")
            invoice = await services.create_invoice(session, ctx, _invoice(customer.id, "INV-T-4"))
        assert invoice.supply_type == "b2c"
        assert invoice.place_of_supply == "30"
        assert invoice.igst_minor > 0
        assert customer.state == "Goa"  # filled from the code when left blank

    async def test_parties_are_snapshotted_at_issue(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, state_code="27")
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice(customer.id, "INV-T-5", ship_to_address="Godown 3, Chakan\nPune 410501"),
            )
        assert invoice.seller_legal_name == "OpsGrids Industries Pvt Ltd"
        assert invoice.seller_address == "Plot 14, MIDC Bhosari\nNear Telco Road\nPune 411026"
        assert invoice.buyer_name == "Konkan Traders"
        assert invoice.buyer_address == "12 FC Road\nPune 411004"
        assert invoice.buyer_state_code == "27"
        assert invoice.ship_to_address == "Godown 3, Chakan\nPune 410501"

        # Editing the customer afterwards does not rewrite what the document said.
        async with session.begin():
            fetched = await selectors.get_customer(session, org_id, customer.id)
            await services.update_customer(
                session, ctx, fetched, CustomerUpdate(address_line1="99 MG Road")
            )
            again = await selectors.get_invoice(session, org_id, invoice.id)
        assert again is not None and again.buyer_address == "12 FC Road\nPune 411004"

    async def test_forty_percent_before_the_change_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        customer_id = customer.id
        with pytest.raises(BusinessRuleProblem, match="only from 2025-09-22"):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice(
                        customer_id,
                        "INV-T-6",
                        tax_rate_pct=40,
                        issued_date=date(2025, 9, 1),
                        due_date=date(2025, 9, 30),
                    ),
                )
        async with session.begin():
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer_id, "INV-T-7", tax_rate_pct=40)
            )
        assert invoice.tax_rate_pct == Decimal("40")

    @pytest.mark.parametrize(
        "line,message",
        [
            (
                InvoiceLineCreate(
                    description="X", quantity=1, unit_price_minor=100, discount_minor=101
                ),
                "more than the line",
            ),
            (
                InvoiceLineCreate(description="X", quantity=1, unit_price_minor=100, unit="each"),
                "unit quantity code",
            ),
        ],
    )
    async def test_a_bad_line_is_refused_naming_it(self, session, org_id, line, message) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        with pytest.raises(BusinessRuleProblem, match=message):
            async with session.begin():
                await services.create_invoice(
                    session, ctx, _invoice(customer.id, "INV-T-8", lines=[line])
                )

    async def test_the_route_allocates_a_short_number_and_rounds_on_request(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        res = await client.post(
            "/api/v1/finance/invoices",
            json={
                "customer_id": str(customer.id),
                "issued_date": "2026-10-10",
                "due_date": "2026-10-25",
                "tax_rate_pct": 18,
                "round_off": True,
                "lines": [
                    {
                        "description": "Steel brackets",
                        "quantity": 10,
                        "unit": "NOS",
                        "unit_price_minor": 123_45,
                        "discount_minor": 50_00,
                        "hsn_sac": "7326",
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["number"] == "INV-2627-00001"
        assert len(body["number"]) <= 16
        assert body["round_off_minor"] == 29 and body["total_minor"] == 1398_00
        assert body["lines"][0]["unit"] == "NOS"
        assert body["lines"][0]["discount_minor"] == 50_00
        assert body["seller_address"].startswith("Plot 14")

    async def test_a_rounded_invoice_passes_the_portal_footing_check(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, gstin=gstin("27"), state_code="27")
            invoice = await services.create_invoice(
                session, ctx, _invoice(customer.id, "INV-T-9", round_off=True)
            )
            registration = await selectors.get_gst_registration(session, org_id)
        assert registration is not None
        payload = services.build_irp_payload(
            invoice, registration, buyer_legal_name="Konkan Traders", at=services._now()
        )
        assert payload.round_off_minor == invoice.round_off_minor != 0
        StubIrpClient().register(payload)  # raises IrpRejected if the totals did not foot


# --------------------------------------------------------------------- customers


class TestCustomerAddress:
    async def test_a_gstin_from_another_state_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="belongs to one"):
            async with session.begin():
                await _customer(session, ctx, gstin=gstin("29"), state_code="27")

    async def test_a_malformed_pincode_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="PIN code"):
            async with session.begin():
                await _customer(session, ctx, pincode="12345")

    async def test_an_unknown_state_code_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="not a GST state code"):
            async with session.begin():
                await _customer(session, ctx, state_code="00")


# --------------------------------------------------------------------- supplier settings


class TestSupplierSettings:
    async def test_readiness_follows_the_registration(self, client, session, org_id) -> None:
        res = await client.get("/api/v1/finance/settings/supplier")
        assert res.status_code == 200
        assert res.json()["ready"] is False
        assert res.json()["missing"] == list(domain.SUPPLIER_REQUIRED_FIELDS)

        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx, address=False)
        assert (await client.get("/api/v1/finance/settings/supplier")).json()["missing"] == [
            "address_line1",
            "city",
            "pincode",
        ]

        res = await client.put(
            "/api/v1/finance/gst/registration",
            json={
                "gstin": SELLER,
                "legal_name": "OpsGrids Industries Pvt Ltd",
                "address_line1": "Plot 14, MIDC Bhosari",
                "city": "Pune",
                "pincode": "411026",
            },
        )
        assert res.status_code == 200 and res.json()["pincode"] == "411026"
        body = (await client.get("/api/v1/finance/settings/supplier")).json()
        assert body["ready"] is True and body["missing"] == []

    async def test_a_registration_save_without_address_keeps_the_address(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        res = await client.put(
            "/api/v1/finance/gst/registration",
            json={"gstin": SELLER, "legal_name": "OpsGrids Industries Private Limited"},
        )
        assert res.status_code == 200
        assert res.json()["address_line1"] == "Plot 14, MIDC Bhosari"

    async def test_the_account_number_is_sealed_and_masked(
        self, client, session, admin_session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        res = await client.put(
            "/api/v1/finance/settings/supplier-profile",
            json={
                "pan": "aabca9012d",
                "bank_account_name": "OpsGrids Industries Pvt Ltd",
                "bank_account_number": "5010 0123 4567 89",
                "bank_ifsc": "hdfc0001234",
                "bank_name": "HDFC Bank",
                "bank_branch": "Bhosari",
                "upi_id": "opsgrids@hdfcbank",
            },
        )
        assert res.status_code == 200, res.text
        profile = res.json()["profile"]
        assert profile["pan"] == SELLER_PAN
        assert profile["bank"]["ifsc"] == "HDFC0001234"
        assert profile["bank"]["account_number"].endswith("6789")
        assert "50100123456789" not in res.text

        # At rest: ciphertext, never the number.
        async with admin_session.begin():
            raw = (
                await admin_session.execute(
                    text(
                        "SELECT bank_account_number_enc FROM finance.supplier_profiles "
                        "WHERE organization_id = :org"
                    ),
                    {"org": org_id},
                )
            ).scalar_one()
        assert b"50100123456789" not in bytes(raw)

        # A save that does not mention the number leaves it alone.
        res = await client.put(
            "/api/v1/finance/settings/supplier-profile", json={"bank_branch": "Pimpri"}
        )
        assert res.json()["profile"]["bank"]["account_number"].endswith("6789")

    async def test_a_pan_that_is_not_the_gstins_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        with pytest.raises(BusinessRuleProblem, match="does not match the GSTIN"):
            async with session.begin():
                await services.set_supplier_profile(
                    session, ctx, SupplierProfileUpsert(pan="ZZZZZ9999Z")
                )

    async def test_print_details_open_the_number_and_audit_it(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await services.set_supplier_profile(
                session,
                ctx,
                SupplierProfileUpsert(
                    bank_account_number="50100123456789", bank_ifsc="HDFC0001234"
                ),
            )
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(session, ctx, _invoice(customer.id, "INV-P-1"))

        res = await client.get(f"/api/v1/finance/invoices/{invoice.id}/print-details")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["bank"]["account_number"] == "50100123456789"
        assert body["gstin"] == SELLER and body["pincode"] == "411026"

        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "supplier_profile.bank_disclosed",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1 and audits[0].entity_id == invoice.id
        assert "50100123456789" not in str(audits[0].after)

    async def test_print_details_without_an_account_audit_nothing(
        self, client, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(session, ctx, _invoice(customer.id, "INV-P-2"))
        res = await client.get(f"/api/v1/finance/invoices/{invoice.id}/print-details")
        assert res.status_code == 200 and res.json()["bank"] is None
        async with session.begin():
            count = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "supplier_profile.bank_disclosed")
                )
            ).all()
        assert count == []


class TestIsolation:
    async def test_another_tenants_invoice_has_no_print_details(
        self, client, session, admin_session, org_id
    ) -> None:
        """An invoice id from another organization is a 404, and their sealed profile is not
        reachable through it."""
        other = uuid.uuid4()
        other_ctx = admin_ctx(other)
        async with admin_session.begin():
            await admin_session.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(other)}
            )
            await _register(admin_session, other_ctx)
            await services.set_supplier_profile(
                admin_session, other_ctx, SupplierProfileUpsert(bank_account_number="999988887777")
            )
            customer = await _customer(admin_session, other_ctx)
            theirs = await services.create_invoice(
                admin_session, other_ctx, _invoice(customer.id, "INV-X-1")
            )
        res = await client.get(f"/api/v1/finance/invoices/{theirs.id}/print-details")
        assert res.status_code == 404
        assert "999988887777" not in res.text

        # And this tenant's settings do not see their profile.
        body = (await client.get("/api/v1/finance/settings/supplier")).json()
        assert body["profile"]["bank"]["account_number"] == ""

        async with session.begin():
            mine = (
                await session.execute(
                    select(SupplierProfile).where(SupplierProfile.id.is_not(None))
                )
            ).all()
            visible = (await session.execute(select(Invoice).where(Invoice.id == theirs.id))).all()
        assert mine == [] and visible == []
