"""Row-level security isolation for I2's three finance fact tables (ADR-0002,
`docs/insights/data-model.md` §1): another organization's rows are invisible under a bound
tenant, even with the application-level filter removed — RLS as the net, not the filter. Same
shape as `tests/test_rls.py`'s own coverage of I1's tables.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvoiceFacts, PaymentFacts, PurchaseFacts

pytestmark = pytest.mark.integration


def _invoice(organization_id: uuid.UUID) -> InvoiceFacts:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    return InvoiceFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        number="INV-1",
        document_type="invoice",
        status="sent",
        issued_date=date(2026, 9, 10),
        due_date=date(2026, 9, 25),
        currency="INR",
        subtotal_minor=1000,
        tax_minor=180,
        total_minor=1180,
        customer_ref=uuid.uuid4(),
        source_created_at=now,
        source_updated_at=now,
    )


def _payment(organization_id: uuid.UUID) -> PaymentFacts:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    return PaymentFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        reference="UTR-1",
        received_date=date(2026, 9, 10),
        method="upi",
        currency="INR",
        amount_minor=1180,
        source_created_at=now,
        source_updated_at=now,
    )


def _purchase(organization_id: uuid.UUID) -> PurchaseFacts:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    return PurchaseFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        number="BILL-1",
        document_type="invoice",
        document_date=date(2026, 9, 10),
        currency="INR",
        taxable_minor=1000,
        tax_minor=180,
        total_minor=1180,
        source_created_at=now,
        source_updated_at=now,
    )


async def test_invoice_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(_invoice(other_org))

    rows = (await session.execute(select(InvoiceFacts))).scalars().all()
    assert rows == []

    admin_rows = (
        (
            await admin_session.execute(
                select(InvoiceFacts).where(InvoiceFacts.organization_id == other_org)
            )
        )
        .scalars()
        .all()
    )
    assert len(admin_rows) == 1


async def test_payment_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(_payment(other_org))
    rows = (await session.execute(select(PaymentFacts))).scalars().all()
    assert rows == []


async def test_purchase_facts_is_isolated(
    session: AsyncSession, admin_session: AsyncSession, other_org: uuid.UUID
) -> None:
    async with admin_session.begin():
        admin_session.add(_purchase(other_org))
    rows = (await session.execute(select(PurchaseFacts))).scalars().all()
    assert rows == []


async def test_invoice_facts_write_is_confined_to_the_bound_tenant(
    session: AsyncSession, other_org: uuid.UUID
) -> None:
    """`WITH CHECK` (ADR-0002 §3): a write naming another organization's id must be refused, not
    silently reassigned."""
    session.add(_invoice(other_org))
    with pytest.raises(DBAPIError):
        await session.flush()
