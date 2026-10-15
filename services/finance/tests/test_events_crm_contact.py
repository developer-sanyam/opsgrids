"""`invoice.issued` and `payment.received` carry the customer's CRM contact (SP4 EV33), so an
Engage automation can address the person; null when the finance customer has no CRM link."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import PaymentAllocationCreate, PaymentCreate
from tests.helpers import admin_ctx
from tests.test_billing_writes import _customer, _invoice_payload, _register

pytestmark = pytest.mark.integration


async def _events(session, event_type: str) -> list[dict]:
    rows = await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    return [r.payload for r in rows.scalars()]


@pytest.mark.parametrize("linked", [True, False])
async def test_invoice_and_payment_events_carry_crm_contact_id(session, org_id, linked) -> None:
    ctx = admin_ctx(org_id)
    contact = uuid.uuid4()
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
        if linked:
            customer.crm_contact_id = contact
        invoice = await services.create_invoice(
            session, ctx, _invoice_payload(customer.id, "INV-EV-1")
        )
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                customer_id=customer.id,
                received_date=date(2026, 1, 20),
                method="cash",
                allocations=[
                    PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=invoice.total_minor)
                ],
            ),
            today=date(2026, 1, 20),
        )
    expected = str(contact) if linked else None
    async with session.begin():
        (issued,) = await _events(session, "finance.billing.invoice.issued")
        (received,) = await _events(session, "finance.billing.payment.received")
    assert issued["crm_contact_id"] == expected
    assert received["crm_contact_id"] == expected


async def _issue(session, ctx, number: str):
    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
        invoice = await services.create_invoice(session, ctx, _invoice_payload(customer.id, number))
    return customer, invoice


async def _check(session, ctx, invoice, today=date(2026, 1, 26)) -> list[dict]:
    async with session.begin():
        staged = await services.check_invoice_overdue(session, ctx, invoice.id, today=today)
        events = await _events(session, "finance.billing.invoice.overdue")
    return events if staged else []


async def test_issuing_schedules_one_overdue_check_the_day_after_due(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    _, invoice = await _issue(session, ctx, "INV-OD-1")
    async with session.begin():
        rows = await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "platform.schedule.requested")
        )
        (payload,) = [
            r.payload
            for r in rows.scalars()
            if r.payload["target"]["event_type"] == "finance.billing.invoice.overdue_check"
        ]
    assert payload["idempotency_key"] == f"invoice:{invoice.id}:overdue"
    assert payload["due_at"].startswith("2026-01-26T00:00:00")


async def test_unpaid_invoice_gets_exactly_one_overdue_event(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    _, invoice = await _issue(session, ctx, "INV-OD-2")
    (event,) = await _check(session, ctx, invoice)
    assert event["amount_due_minor"] == invoice.total_minor
    assert event["days_overdue"] == 1
    assert event["crm_contact_id"] is None
    assert "email" not in event and "name" not in event


async def test_paid_invoice_gets_no_overdue_event(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    customer, invoice = await _issue(session, ctx, "INV-OD-3")
    async with session.begin():
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                customer_id=customer.id,
                received_date=date(2026, 1, 20),
                method="cash",
                allocations=[
                    PaymentAllocationCreate(invoice_id=invoice.id, amount_minor=invoice.total_minor)
                ],
            ),
            today=date(2026, 1, 20),
        )
    assert await _check(session, ctx, invoice) == []


async def test_not_yet_due_gets_no_overdue_event(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    _, invoice = await _issue(session, ctx, "INV-OD-4")
    assert await _check(session, ctx, invoice, today=date(2026, 1, 25)) == []


async def test_void_invoice_gets_no_overdue_event(session, org_id) -> None:
    ctx = admin_ctx(org_id)
    _, invoice = await _issue(session, ctx, "INV-OD-5")
    async with session.begin():
        invoice.status = "void"
    assert await _check(session, ctx, invoice) == []
