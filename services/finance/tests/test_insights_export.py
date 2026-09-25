"""`GET /internal/v1/insights/export/{entity}` — insights' backfill read (ADR-0020 slice I2,
`docs/insights/api.md` §6.1).

Same shape as the rest of this service's internal-route tests: a real, minted service token
(`require_service_token` exercised for real), the shared tenant-bound session so a write in the
test is visible to the route's own transaction, and RLS proving isolation rather than the route's
own `organization_id` filter alone.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient

from app import services
from app.config import settings
from app.schemas import (
    CustomerCreate,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
    PaymentAllocationCreate,
    PaymentCreate,
    PurchaseDocumentCreate,
)
from platform_core.internal_auth import mint_service_token
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

EXPORT = "/internal/v1/insights/export"
SELLER = "27AABCA9012D1Z5"
SUPPLIER = "29AABCU9603R1ZJ"


def _insights_headers() -> dict[str, str]:
    """A real token minted for finance, from caller `insights` — what the backfill runner sends."""
    cfg = settings()
    token = mint_service_token(cfg, audience=cfg.service_name, caller="insights")
    return {"X-Internal-Token": token}


def _other_caller_headers() -> dict[str, str]:
    """A valid finance-audience token from any caller *other* than insights."""
    cfg = settings()
    token = mint_service_token(cfg, audience=cfg.service_name, caller="some-other-service")
    return {"X-Internal-Token": token}


async def _customer(session, ctx, **overrides):
    payload = {
        "name": "ACME Corp",
        "contact_name": "AP Desk",
        "email": "ap@example.com",
        "city": "Pune",
        "state": "Maharashtra",
    }
    payload.update(overrides)
    return await services.create_customer(session, ctx, CustomerCreate(**payload))


def _invoice_payload(customer_id: uuid.UUID, number: str, **over) -> InvoiceCreate:
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


class TestCallerIsChecked:
    async def test_a_token_from_any_other_caller_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        res = await client.get(
            f"{EXPORT}/invoices",
            params={"organization_id": str(org_id)},
            headers=_other_caller_headers(),
        )
        assert res.status_code == 401

    async def test_no_token_at_all_is_refused(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        res = await client.get(f"{EXPORT}/invoices", params={"organization_id": str(org_id)})
        assert res.status_code == 401

    async def test_an_unknown_entity_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        res = await client.get(
            f"{EXPORT}/not_a_real_entity",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        assert res.status_code == 422


class TestInvoicesExport:
    async def test_an_issued_invoice_is_exported_with_its_dates_and_totals(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )
            customer = await _customer(session, ctx)
        async with session.begin():
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-EXP-1")
            )

        res = await client.get(
            f"{EXPORT}/invoices",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        assert res.status_code == 200
        body = res.json()
        rows = {row["id"]: row for row in body["rows"]}
        row = rows[str(invoice.id)]
        assert row["number"] == "INV-EXP-1"
        assert row["issued_date"] == "2026-01-10"
        assert row["due_date"] == "2026-01-25"
        assert row["subtotal_minor"] == invoice.subtotal_minor
        assert row["tax_minor"] == invoice.tax_minor
        assert row["total_minor"] == invoice.total_minor
        assert row["customer_id"] == str(customer.id)
        assert row["paid_at"] is None
        assert body["next"] is None

    async def test_a_fully_paid_invoice_carries_the_settling_payments_created_at(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )
            customer = await _customer(session, ctx)
        async with session.begin():
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-EXP-2")
            )
        async with session.begin():
            payment = await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="UTR-EXP-1",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 20),
                    method="bank_transfer",
                    allocations=[
                        PaymentAllocationCreate(
                            invoice_id=invoice.id, amount_minor=invoice.total_minor
                        )
                    ],
                ),
            )

        res = await client.get(
            f"{EXPORT}/invoices",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        row = {r["id"]: r for r in res.json()["rows"]}[str(invoice.id)]
        assert row["status"] == "paid"
        assert row["paid_at"] is not None
        assert row["paid_at"].startswith(str(payment.created_at.date()))

    async def test_pagination_advances_by_updated_at_and_id(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )
            customer = await _customer(session, ctx)
        async with session.begin():
            await services.create_invoice(session, ctx, _invoice_payload(customer.id, "INV-P-1"))
            await services.create_invoice(session, ctx, _invoice_payload(customer.id, "INV-P-2"))

        first = await client.get(
            f"{EXPORT}/invoices",
            params={"organization_id": str(org_id), "limit": 1},
            headers=_insights_headers(),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["rows"]) == 1
        assert first_body["next"] is not None

        second = await client.get(
            f"{EXPORT}/invoices",
            params={
                "organization_id": str(org_id),
                "limit": 1,
                "after_updated_at": first_body["next"]["after_updated_at"],
                "after_id": first_body["next"]["after_id"],
            },
            headers=_insights_headers(),
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["rows"]) == 1
        assert second_body["rows"][0]["id"] != first_body["rows"][0]["id"]

    async def test_a_row_from_another_organization_is_invisible_under_rls(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        """The route binds to the organization the caller names; a second organization's own
        invoice, created directly against the same database, never appears — RLS as the net, not
        merely this route's own `organization_id` filter (CLAUDE.md)."""
        other_org = uuid.uuid4()
        ctx = admin_ctx(other_org)
        async with session.begin():
            from platform_core.db import set_tenant

            await set_tenant(session, other_org)
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="A Different Tenant Pvt Ltd"),
            )
            customer = await _customer(session, ctx)
            await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "OTHER-ORG-INV")
            )

        res = await client.get(
            f"{EXPORT}/invoices",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        assert res.status_code == 200
        numbers = {row["number"] for row in res.json()["rows"]}
        assert "OTHER-ORG-INV" not in numbers


class TestPaymentsExport:
    async def test_a_payment_is_exported_with_its_derived_currency(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )
            customer = await _customer(session, ctx)
        async with session.begin():
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-EXP-3", currency="USD")
            )
        async with session.begin():
            payment = await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="UTR-EXP-2",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 22),
                    method="upi",
                    allocations=[
                        PaymentAllocationCreate(
                            invoice_id=invoice.id, amount_minor=invoice.total_minor
                        )
                    ],
                ),
            )

        res = await client.get(
            f"{EXPORT}/payments",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        assert res.status_code == 200
        row = {r["id"]: r for r in res.json()["rows"]}[str(payment.id)]
        assert row["currency"] == "USD"
        assert row["amount_minor"] == payment.amount_minor
        assert row["received_date"] == "2026-01-22"
        assert row["method"] == "upi"


class TestPurchasesExport:
    async def test_a_purchase_document_is_exported_with_a_computed_tax_minor(
        self, client: AsyncClient, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )
        async with session.begin():
            purchase = await services.record_purchase_document(
                session,
                ctx,
                PurchaseDocumentCreate(
                    supplier_gstin=SUPPLIER,
                    number="BILL-EXP-1",
                    document_date=date(2026, 1, 5),
                    taxable_minor=10_000_00,
                    tax_rate_pct=18,
                    supplier_name="Acme Supplies",
                ),
            )

        res = await client.get(
            f"{EXPORT}/purchases",
            params={"organization_id": str(org_id)},
            headers=_insights_headers(),
        )
        assert res.status_code == 200
        row = {r["id"]: r for r in res.json()["rows"]}[str(purchase.id)]
        assert row["taxable_minor"] == purchase.taxable_minor
        assert row["tax_minor"] == purchase.cgst_minor + purchase.sgst_minor + purchase.igst_minor
        assert row["total_minor"] == purchase.total_minor
