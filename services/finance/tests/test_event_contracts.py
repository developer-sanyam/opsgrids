"""Every finance event insights binds validated against its committed schema (ADR-0020 slice I2,
`docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: drive the real write path
inside a real transaction, read the staged `outbox` row back, and validate the payload against
the schema committed under `libs/contracts/events/`. Once every assertion here passes, the
schema's `$comment` — "ahead of the producer" — is stale and is removed in the same commit.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import (
    CustomerCreate,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
    PaymentAllocationCreate,
    PaymentCreate,
    PurchaseDocumentCreate,
)
from platform_core.errors import BusinessRuleProblem
from platform_core.events import validate_event_schema
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `finance`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

SELLER = "27AABCA9012D1Z5"
SUPPLIER = "29AABCU9603R1ZJ"


async def _register(session, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _customer(session, ctx, *, name: str = "ACME Corp", **overrides: Any):
    payload = {
        "name": name,
        "contact_name": "AP Desk",
        "email": "ap@example.com",
        "city": "Pune",
        "state": "Maharashtra",
    }
    payload.update(overrides)
    return await services.create_customer(session, ctx, CustomerCreate(**payload))


def _invoice_payload(customer_id: uuid.UUID, number: str, **over: Any) -> InvoiceCreate:
    payload: dict[str, Any] = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 1, 10),
        "due_date": date(2026, 1, 25),
        "tax_rate_pct": 18,
        "lines": [InvoiceLineCreate(description="Widgets", quantity=10, unit_price_minor=100_00)],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


async def _last_payload(session, event_type: str) -> dict[str, Any]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.created_at.desc())
    )
    row = (await session.execute(stmt)).scalars().first()
    assert row is not None, f"no {event_type} event was staged"
    return dict(row.payload)


async def _validate(session, event_type: str, *, version: int = 1) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, version, payload, contracts_root=CONTRACTS_ROOT)


async def test_invoice_issued_matches_its_schema(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
    async with session.begin():
        await services.create_invoice(session, ctx, _invoice_payload(customer.id, "INV-1"))
    await _validate(session, services.EVT_INVOICE_ISSUED)


async def test_credit_note_issued_matches_its_schema(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
    async with session.begin():
        invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-2")
        )
    async with session.begin():
        await services.create_invoice(
            session,
            ctx,
            _invoice_payload(
                customer.id,
                "CN-1",
                document_type="credit_note",
                corrects_invoice_id=invoice.id,
                lines=[
                    InvoiceLineCreate(description="Return", quantity=-1, unit_price_minor=100_00)
                ],
            ),
        )
    await _validate(session, services.EVT_INVOICE_ISSUED)


async def test_invoice_paid_and_payment_received_match_their_schemas(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
    async with session.begin():
        invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-3")
        )
    async with session.begin():
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                reference="UTR-1",
                customer_id=customer.id,
                received_date=date(2026, 1, 15),
                method="bank_transfer",
                allocations=[
                    PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=invoice.total_minor)
                ],
            ),
        )
    await _validate(session, services.EVT_PAYMENT_RECEIVED)
    await _validate(session, services.EVT_INVOICE_PAID)


async def test_payment_received_matches_its_schema_on_a_partial_allocation(session, org_id) -> None:
    """A payment that does not fully settle its invoice never emits `invoice.paid`, but
    `payment.received` still carries the derived currency and date (events.md §2)."""
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
    async with session.begin():
        invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-4")
        )
    async with session.begin():
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                reference="UTR-2",
                customer_id=customer.id,
                received_date=date(2026, 1, 16),
                method="upi",
                allocations=[
                    PaymentAllocationCreate(
                        invoice_id=invoice.id, amount_minor=invoice.total_minor // 2
                    )
                ],
            ),
        )
    await _validate(session, services.EVT_PAYMENT_RECEIVED)


async def test_payment_across_mixed_currency_invoices_is_refused(session, org_id) -> None:
    """`payments` carries no currency column of its own: a payment is refused rather than
    picking one invoice's currency and silently misreporting the other (events.md §5 finding 1)."""
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
    async with session.begin():
        inr_invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-5", currency="INR")
        )
        usd_invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-6", currency="USD")
        )
    with pytest.raises(BusinessRuleProblem):
        async with session.begin():
            await services.record_payment(
                session,
                ctx,
                PaymentCreate(
                    reference="UTR-3",
                    customer_id=customer.id,
                    received_date=date(2026, 1, 17),
                    method="cash",
                    allocations=[
                        PaymentAllocationCreate(
                            invoice_id=inr_invoice.id, amount_minor=inr_invoice.total_minor
                        ),
                        PaymentAllocationCreate(
                            invoice_id=usd_invoice.id, amount_minor=usd_invoice.total_minor
                        ),
                    ],
                ),
            )


async def test_purchase_recorded_matches_its_schema(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    async with session.begin():
        await _register(session, ctx)
    async with session.begin():
        await services.record_purchase_document(
            session,
            ctx,
            PurchaseDocumentCreate(
                supplier_gstin=SUPPLIER,
                number="BILL-1",
                document_date=date(2026, 1, 12),
                taxable_minor=50_000_00,
                tax_rate_pct=18,
                supplier_name="Acme Supplies",
            ),
        )
    await _validate(session, services.EVT_PURCHASE_RECORDED)
