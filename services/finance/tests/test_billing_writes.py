"""The gap this module closes: a real tenant can now add a customer, issue an invoice or credit
note, and record a payment — the three writes that had complete service functions and no route
(`finance.md`). These tests cover the rules layered on top of the pre-existing service functions:
the route contract cannot force a status, a credit note is checked against the invoice it
corrects, a payment allocation is checked against the invoice and customer it names, and a fully
allocated invoice's status moves to `paid` with no separate write.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app import selectors, services
from app.models import Invoice
from app.schemas import (
    CustomerCreate,
    CustomerUpdate,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
    PaymentAllocationCreate,
    PaymentCreate,
)
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _customer(session, ctx, *, name="ACME Corp", **overrides):
    payload = {
        "name": name,
        "contact_name": "AP Desk",
        "email": "ap@example.com",
        "city": "Pune",
        "state": "Maharashtra",
    }
    payload.update(overrides)
    return await services.create_customer(session, ctx, CustomerCreate(**payload))


def _invoice_payload(customer_id, number: str, **over) -> InvoiceCreate:
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 1, 10),
        "due_date": date(2026, 1, 25),
        "tax_rate_pct": 18,
        "lines": [InvoiceLineCreate(description="Widgets", quantity=10, unit_price_minor=100_00)],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


# --------------------------------------------------------------------- customers


class TestCustomerWrites:
    async def test_create_read_update(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx)
        assert customer.is_tds_deductor is False

        async with session.begin():
            fetched = await selectors.get_customer(session, org_id, customer.id)
            updated = await services.update_customer(
                session, ctx, fetched, CustomerUpdate(city="Mumbai")
            )
        assert updated.city == "Mumbai"
        assert updated.name == "ACME Corp"  # untouched fields survive a partial PATCH

    async def test_update_applies_only_what_was_sent(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx, email="old@example.com")
        async with session.begin():
            fetched = await selectors.get_customer(session, org_id, customer.id)
            await services.update_customer(session, ctx, fetched, CustomerUpdate())
        async with session.begin():
            refreshed = await selectors.get_customer(session, org_id, customer.id)
        assert refreshed.email == "old@example.com"

    async def test_create_refuses_both_withholding_flags(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await _customer(session, ctx, deducts_tds_194q=True, tcs_applicable=True)

    async def test_update_refuses_both_withholding_flags(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx, deducts_tds_194q=True)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                fetched = await selectors.get_customer(session, org_id, customer.id)
                await services.update_customer(
                    session, ctx, fetched, CustomerUpdate(tcs_applicable=True)
                )

    async def test_archive_soft_deletes_an_unreferenced_customer(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await _customer(session, ctx)
        async with session.begin():
            fetched = await selectors.get_customer(session, org_id, customer.id)
            await services.archive_customer(session, ctx, fetched)
        async with session.begin():
            assert await selectors.get_customer(session, org_id, customer.id) is None

    async def test_archive_is_refused_while_an_invoice_references_the_customer(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            await services.create_invoice(session, ctx, _invoice_payload(customer.id, "INV-ARCH-1"))
        with pytest.raises(ConflictProblem):
            async with session.begin():
                fetched = await selectors.get_customer(session, org_id, customer.id)
                await services.archive_customer(session, ctx, fetched)

    async def test_archive_is_refused_while_a_payment_references_the_customer(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-ARCH-2")
            )
            await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="PMT-ARCH-1",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 20),
                    method="upi",
                    allocations=[
                        PaymentAllocationCreate(
                            invoice_id=invoice.id, amount_minor=invoice.total_minor
                        )
                    ],
                ),
                today=date(2026, 1, 20),
            )
        with pytest.raises(ConflictProblem):
            async with session.begin():
                fetched = await selectors.get_customer(session, org_id, customer.id)
                await services.archive_customer(session, ctx, fetched)


class TestCustomerRoutes:
    async def test_create_update_archive_over_http(self, client) -> None:
        create = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "HTTP Customer",
                "contact_name": "A",
                "email": "a@example.com",
                "city": "Pune",
                "state": "Maharashtra",
            },
        )
        assert create.status_code == 201
        body = create.json()
        assert body["is_tds_deductor"] is False
        customer_id = body["id"]

        patch = await client.patch(
            f"/api/v1/finance/customers/{customer_id}", json={"city": "Nashik"}
        )
        assert patch.status_code == 200
        assert patch.json()["city"] == "Nashik"

        delete = await client.delete(f"/api/v1/finance/customers/{customer_id}")
        assert delete.status_code == 204

        missing = await client.get(f"/api/v1/finance/customers/{customer_id}")
        assert missing.status_code == 404

    async def test_create_rejects_unknown_fields(self, client) -> None:
        res = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "X",
                "contact_name": "",
                "email": "",
                "city": "",
                "state": "",
                "status": "vip",
            },
        )
        assert res.status_code == 422


# --------------------------------------------------------------------- invoices


class TestIssueInvoiceRoute:
    async def test_status_cannot_be_forced_through_the_route(self, client) -> None:
        """`InvoiceIssueIn` carries no `status` field — sending one is an unknown field, refused
        before the service ever sees the write, not silently ignored."""
        customer = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "Status Force Co",
                "contact_name": "",
                "email": "",
                "city": "",
                "state": "",
            },
        )
        payload = {
            "customer_id": customer.json()["id"],
            "issued_date": "2026-01-10",
            "due_date": "2026-01-25",
            "tax_rate_pct": 18,
            "lines": [{"description": "Widgets", "quantity": 1, "unit_price_minor": 1000}],
            "status": "paid",
        }
        res = await client.post("/api/v1/finance/invoices", json=payload)
        assert res.status_code == 422

    async def test_number_cannot_be_supplied_through_the_route(self, client) -> None:
        customer = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "Number Force Co",
                "contact_name": "",
                "email": "",
                "city": "",
                "state": "",
            },
        )
        payload = {
            "number": "INV-CALLER-0001",
            "customer_id": customer.json()["id"],
            "issued_date": "2026-01-10",
            "due_date": "2026-01-25",
            "tax_rate_pct": 18,
            "lines": [{"description": "Widgets", "quantity": 1, "unit_price_minor": 1000}],
        }
        res = await client.post("/api/v1/finance/invoices", json=payload)
        assert res.status_code == 422

    async def test_issuing_allocates_a_server_owned_number_and_computes_tax(self, client) -> None:
        reg = await client.put(
            "/api/v1/finance/gst/registration",
            json={"gstin": SELLER, "legal_name": "OpsGrids Industries Pvt Ltd"},
        )
        assert reg.status_code == 200
        customer = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "Numbering Co",
                "contact_name": "",
                "email": "",
                "city": "Pune",
                "state": "Maharashtra",
            },
        )
        customer_id = customer.json()["id"]
        payload = {
            "customer_id": customer_id,
            "issued_date": "2026-01-10",
            "due_date": "2026-01-25",
            "tax_rate_pct": 18,
            "lines": [{"description": "Widgets", "quantity": 10, "unit_price_minor": 100_00}],
        }
        first = await client.post("/api/v1/finance/invoices", json=payload)
        second = await client.post("/api/v1/finance/invoices", json=payload)
        assert first.status_code == 201
        assert second.status_code == 201
        first_body, second_body = first.json(), second.json()
        assert first_body["number"] != second_body["number"]
        assert first_body["number"].startswith("INV-")
        # Server-computed tax split, never client-supplied: Rs 1,000 (10 x Rs 100) @ 18%
        # intra-state — CGST + SGST, since both parties are in Maharashtra.
        assert first_body["subtotal_minor"] == 100_000
        assert first_body["tax_minor"] == 18_000
        assert first_body["total_minor"] == 118_000
        assert first_body["cgst_minor"] == 9_000
        assert first_body["sgst_minor"] == 9_000


class TestCreditNoteRules:
    async def test_a_credit_note_without_corrects_invoice_id_is_refused(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer.id,
                        "CN-NOCORRECT-1",
                        document_type="credit_note",
                        lines=[
                            InvoiceLineCreate(
                                description="Return", quantity=-1, unit_price_minor=100_00
                            )
                        ],
                    ),
                )

    async def test_a_credit_note_against_another_customers_invoice_is_refused(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer_a = await _customer(session, ctx, name="Customer A")
            customer_b = await _customer(session, ctx, name="Customer B")
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer_a.id, "INV-CN-CROSS-1")
            )
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer_b.id,
                        "CN-CROSS-1",
                        document_type="credit_note",
                        corrects_invoice_id=invoice.id,
                        lines=[
                            InvoiceLineCreate(
                                description="Return", quantity=-1, unit_price_minor=100_00
                            )
                        ],
                    ),
                )

    async def test_a_credit_note_cannot_exceed_what_remains_correctable(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CN-EXCEED-1")
            )
        # The invoice totals 1,18,000 (1,000 x 100 + 18%). A credit note for 11 units (worth more
        # than the whole invoice) must be refused.
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer.id,
                        "CN-EXCEED-1",
                        document_type="credit_note",
                        corrects_invoice_id=invoice.id,
                        lines=[
                            InvoiceLineCreate(
                                description="Return", quantity=-11, unit_price_minor=100_00
                            )
                        ],
                    ),
                )

    async def test_a_valid_credit_note_links_back_to_the_original(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-CN-OK-1")
            )
        async with session.begin():
            credit = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "CN-OK-1",
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                    lines=[
                        InvoiceLineCreate(
                            description="Return", quantity=-2, unit_price_minor=100_00
                        )
                    ],
                ),
            )
        assert credit.corrects_invoice_id == invoice.id
        assert credit.total_minor < 0

    async def test_due_date_before_issued_date_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer.id,
                        "INV-BADDATE-1",
                        issued_date=date(2026, 2, 1),
                        due_date=date(2026, 1, 1),
                    ),
                )

    async def test_a_zero_total_invoice_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer.id,
                        "INV-ZERO-1",
                        lines=[
                            InvoiceLineCreate(
                                description="Free sample", quantity=0, unit_price_minor=0
                            )
                        ],
                    ),
                )


# --------------------------------------------------------------------- payments


class TestPaymentRules:
    async def _open_invoice(
        self,
        session,
        ctx,
        customer_id,
        number,
        *,
        unit_minor=100_00,
        qty=10,
        today=date(2026, 1, 20),
    ):
        return await services.create_invoice(
            session,
            ctx,
            _invoice_payload(
                customer_id,
                number,
                lines=[
                    InvoiceLineCreate(
                        description="Widgets", quantity=qty, unit_price_minor=unit_minor
                    )
                ],
            ),
            today=today,
        )

    async def test_over_allocation_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await self._open_invoice(session, ctx, customer.id, "INV-PAY-OVER-1")
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference="PMT-OVER-1",
                        customer_id=customer.id,
                        received_date=date(2026, 1, 20),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(
                                invoice_id=invoice.id, amount_minor=invoice.total_minor + 1
                            )
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

    async def test_cross_customer_allocation_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer_a = await _customer(session, ctx, name="Payer A")
            customer_b = await _customer(session, ctx, name="Payer B")
            invoice = await self._open_invoice(session, ctx, customer_a.id, "INV-PAY-CROSS-1")
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference="PMT-CROSS-1",
                        customer_id=customer_b.id,
                        received_date=date(2026, 1, 20),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=1000)
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

    async def test_an_invoice_cannot_be_allocated_twice_in_one_payment(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await self._open_invoice(session, ctx, customer.id, "INV-PAY-DUP-1")
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference="PMT-DUP-1",
                        customer_id=customer.id,
                        received_date=date(2026, 1, 20),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=1000),
                            PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=1000),
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

    async def test_received_date_in_the_future_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await self._open_invoice(session, ctx, customer.id, "INV-PAY-FUTURE-1")
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference="PMT-FUTURE-1",
                        customer_id=customer.id,
                        received_date=date(2026, 1, 21),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=1000)
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

    async def test_a_settled_invoice_moves_to_paid_in_the_same_transaction(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await self._open_invoice(session, ctx, customer.id, "INV-PAY-FULL-1")
        async with session.begin():
            await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="PMT-FULL-1",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 20),
                    method="bank_transfer",
                    allocations=[
                        PaymentAllocationCreate(
                            invoice_id=invoice.id, amount_minor=invoice.total_minor
                        )
                    ],
                ),
                today=date(2026, 1, 20),
            )
        async with session.begin():
            refreshed = (
                await session.execute(select(Invoice).where(Invoice.id == invoice.id))
            ).scalar_one()
        assert refreshed.status == "paid"

    async def test_a_partial_payment_leaves_the_invoice_open(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await self._open_invoice(session, ctx, customer.id, "INV-PAY-PARTIAL-1")
        async with session.begin():
            await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="PMT-PARTIAL-1",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 20),
                    method="cash",
                    allocations=[PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=1000)],
                ),
                today=date(2026, 1, 20),
            )
        async with session.begin():
            refreshed = (
                await session.execute(select(Invoice).where(Invoice.id == invoice.id))
            ).scalar_one()
        assert refreshed.status == "sent"
        paid = await selectors.paid_by_invoice(session, org_id, [invoice.id])
        assert paid[invoice.id] == 1000


class TestPaymentRoute:
    async def test_record_payment_over_http(self, client) -> None:
        reg = await client.put(
            "/api/v1/finance/gst/registration",
            json={"gstin": SELLER, "legal_name": "OpsGrids Industries Pvt Ltd"},
        )
        assert reg.status_code == 200
        customer = await client.post(
            "/api/v1/finance/customers",
            json={
                "name": "HTTP Payer",
                "contact_name": "",
                "email": "",
                "city": "Pune",
                "state": "Maharashtra",
            },
        )
        customer_id = customer.json()["id"]
        invoice = await client.post(
            "/api/v1/finance/invoices",
            json={
                "customer_id": customer_id,
                "issued_date": "2026-01-10",
                "due_date": "2026-01-25",
                "tax_rate_pct": 18,
                "lines": [{"description": "Widgets", "quantity": 1, "unit_price_minor": 100_00}],
            },
        )
        invoice_body = invoice.json()
        payment = await client.post(
            "/api/v1/finance/payments",
            json={
                "reference": "PMT-HTTP-1",
                "customer_id": customer_id,
                "received_date": "2026-01-15",
                "method": "upi",
                "allocations": [
                    {
                        "invoice_id": invoice_body["id"],
                        "amount_minor": invoice_body["total_minor"],
                    }
                ],
            },
        )
        assert payment.status_code == 201
        assert payment.json()["amount_minor"] == invoice_body["total_minor"]

        settled = await client.get(f"/api/v1/finance/invoices/{invoice_body['id']}")
        assert settled.json()["status"] == "paid"
        assert settled.json()["amount_paid_minor"] == invoice_body["total_minor"]


# --------------------------------------------------------------------- overdue derivation


class TestOverdueDerivation:
    async def test_an_invoice_due_yesterday_is_overdue_today_with_no_write(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-OVERDUE-1",
                    issued_date=date(2026, 1, 1),
                    due_date=date(2026, 1, 10),
                ),
                today=date(2026, 1, 5),
            )
        assert invoice.status == "sent"  # not yet due when it was written

        async with session.begin():
            refreshed = await selectors.get_invoice(session, org_id, invoice.id)
            paid = await selectors.paid_by_invoice(session, org_id, [invoice.id])
            out_before = selectors.to_invoice_out(
                refreshed, paid.get(invoice.id, 0), today=date(2026, 1, 9)
            )
            out_after = selectors.to_invoice_out(
                refreshed, paid.get(invoice.id, 0), today=date(2026, 1, 11)
            )
        assert out_before.status == "sent"
        assert out_after.status == "overdue"

    async def test_the_status_query_filter_uses_the_same_derivation(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            await services.create_invoice(
                session,
                ctx,
                _invoice_payload(
                    customer.id,
                    "INV-OVERDUE-FILTER-1",
                    issued_date=date(2026, 1, 1),
                    due_date=date(2026, 1, 10),
                ),
                today=date(2026, 1, 5),
            )
        async with session.begin():
            stmt = selectors.invoices_stmt(ctx, status="overdue", today=date(2026, 1, 11))
            rows = (await session.execute(stmt)).scalars().all()
        assert any(r.number == "INV-OVERDUE-FILTER-1" for r in rows)


# --------------------------------------------------------------------- purchases


class TestPurchases:
    async def test_record_and_list_over_http(self, client) -> None:
        reg = await client.put(
            "/api/v1/finance/gst/registration",
            json={"gstin": SELLER, "legal_name": "OpsGrids Industries Pvt Ltd"},
        )
        assert reg.status_code == 200
        create = await client.post(
            "/api/v1/finance/purchases",
            json={
                "supplier_gstin": "24AABCM1234F1Z3",
                "supplier_name": "Component Supplier",
                "number": "SUP-INV-0001",
                "document_type": "invoice",
                "document_date": "2026-01-05",
                "taxable_minor": 10_000_00,
                "tax_rate_pct": 18,
            },
        )
        assert create.status_code == 201
        body = create.json()
        assert body["total_minor"] == 11_800_00

        listed = await client.get("/api/v1/finance/purchases?q=Component")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
