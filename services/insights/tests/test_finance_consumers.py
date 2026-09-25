"""Finance event folds (ADR-0020 slice I2, `docs/insights/events.md` §1-§2): idempotency, the
version guard, the partial `invoice.paid` transition and the tenant-mismatch drop — the same
shape `tests/test_consumers.py` already proves for `core.org.*`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers
from app.models import InvoiceFacts, PaymentFacts, ProcessedEvent, PurchaseFacts

pytestmark = pytest.mark.integration


def _envelope(
    *,
    event_id: uuid.UUID,
    event_type: str,
    organization_id: uuid.UUID,
    occurred_at: datetime,
    **payload: object,
) -> dict[str, object]:
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "organization_id": str(organization_id),
        "occurred_at": occurred_at.isoformat(),
        "payload": {"organization_id": str(organization_id), **payload},
    }


@pytest.fixture(autouse=True)
def _bind_test_session_scope(work_scope) -> None:
    consumers.use_session_scope(work_scope)
    yield
    consumers.use_session_scope(consumers._default_session_scope)


def _invoice_issued_envelope(
    *, org_id: uuid.UUID, invoice_id: uuid.UUID, occurred_at: datetime, **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "invoice_id": str(invoice_id),
        "number": "INV-1",
        "document_type": "invoice",
        "customer_id": str(uuid.uuid4()),
        "status": "sent",
        "issued_date": "2026-09-10",
        "due_date": "2026-09-25",
        "currency": "INR",
        "subtotal_minor": 100000,
        "tax_minor": 18000,
        "total_minor": 118000,
        "supply_type": "b2b",
        "place_of_supply": "27",
        "requires_einvoice": False,
    }
    payload.update(overrides)
    return _envelope(
        event_id=uuid.uuid4(),
        event_type=consumers.EVT_INVOICE_ISSUED,
        organization_id=org_id,
        occurred_at=occurred_at,
        **payload,
    )


async def test_invoice_issued_folds_into_invoice_facts(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    invoice_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    await consumers.on_invoice_issued(
        _invoice_issued_envelope(org_id=org_id, invoice_id=invoice_id, occurred_at=occurred_at)
    )
    row = (
        await admin_session.execute(
            select(InvoiceFacts).where(
                InvoiceFacts.organization_id == org_id, InvoiceFacts.id == invoice_id
            )
        )
    ).scalar_one()
    assert row.number == "INV-1"
    assert row.status == "sent"
    assert row.issued_date == date(2026, 9, 10)
    assert row.subtotal_minor == 100000
    assert row.tax_minor == 18000
    assert row.total_minor == 118000
    assert row.paid_at is None


async def test_invoice_issued_redelivery_is_idempotent(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    invoice_id = uuid.uuid4()
    event_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    envelope = _invoice_issued_envelope(
        org_id=org_id, invoice_id=invoice_id, occurred_at=occurred_at
    )
    envelope["event_id"] = str(event_id)

    await consumers.on_invoice_issued(envelope)
    await consumers.on_invoice_issued(envelope)  # redelivered, same event_id

    processed = (
        await admin_session.execute(
            select(func.count())
            .select_from(ProcessedEvent)
            .where(ProcessedEvent.event_id == event_id)
        )
    ).scalar_one()
    assert processed == 1
    rows = (
        (
            await admin_session.execute(
                select(InvoiceFacts).where(
                    InvoiceFacts.organization_id == org_id, InvoiceFacts.id == invoice_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_an_older_replay_never_overwrites_a_newer_one(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    """The version guard (data-model.md §1.1): a reordered redelivery is harmless."""
    invoice_id = uuid.uuid4()
    newer = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    older = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

    await consumers.on_invoice_issued(
        _invoice_issued_envelope(
            org_id=org_id, invoice_id=invoice_id, occurred_at=newer, status="sent", number="NEWER"
        )
    )
    await consumers.on_invoice_issued(
        _invoice_issued_envelope(
            org_id=org_id, invoice_id=invoice_id, occurred_at=older, status="sent", number="OLDER"
        )
    )
    row = (
        await admin_session.execute(
            select(InvoiceFacts).where(
                InvoiceFacts.organization_id == org_id, InvoiceFacts.id == invoice_id
            )
        )
    ).scalar_one()
    assert row.number == "NEWER"


async def test_invoice_paid_is_a_partial_update_never_an_insert(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    invoice_id = uuid.uuid4()
    issued_at = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    paid_at = datetime(2026, 9, 15, 10, 30, tzinfo=UTC)
    await consumers.on_invoice_issued(
        _invoice_issued_envelope(org_id=org_id, invoice_id=invoice_id, occurred_at=issued_at)
    )
    await consumers.on_invoice_paid(
        _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_INVOICE_PAID,
            organization_id=org_id,
            occurred_at=paid_at,
            invoice_id=str(invoice_id),
            number="INV-1",
            payment_id=str(uuid.uuid4()),
            amount_paid_minor=118000,
            currency="INR",
            paid_date="2026-09-15",
        )
    )
    row = (
        await admin_session.execute(
            select(InvoiceFacts).where(
                InvoiceFacts.organization_id == org_id, InvoiceFacts.id == invoice_id
            )
        )
    ).scalar_one()
    assert row.status == "paid"
    assert row.paid_at == paid_at


async def test_invoice_paid_for_a_missing_row_is_an_orphan_transition_not_an_error(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    """The creating `invoice.issued` fold never landed (data-model.md §1.1): the transition is
    dropped, not fabricated into a half-populated row, and nothing raises."""
    invoice_id = uuid.uuid4()
    await consumers.on_invoice_paid(
        _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_INVOICE_PAID,
            organization_id=org_id,
            occurred_at=datetime.now(UTC),
            invoice_id=str(invoice_id),
            number="INV-1",
            payment_id=str(uuid.uuid4()),
            amount_paid_minor=118000,
            currency="INR",
            paid_date="2026-09-15",
        )
    )
    rows = (
        (
            await admin_session.execute(
                select(InvoiceFacts).where(
                    InvoiceFacts.organization_id == org_id, InvoiceFacts.id == invoice_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_a_tenant_mismatch_between_envelope_and_payload_is_dropped(
    org_id: uuid.UUID, other_org: uuid.UUID, admin_session: AsyncSession
) -> None:
    envelope = _invoice_issued_envelope(
        org_id=other_org, invoice_id=uuid.uuid4(), occurred_at=datetime.now(UTC)
    )
    envelope["organization_id"] = str(org_id)  # envelope disagrees with its own payload
    await consumers.on_invoice_issued(envelope)  # must not raise, and must write nothing
    rows = (await admin_session.execute(select(InvoiceFacts))).scalars().all()
    assert rows == []


async def test_a_field_missing_from_an_un_upgraded_producer_raises(org_id: uuid.UUID) -> None:
    """`docs/insights/events.md` §1: a payload missing a field this consumer needs raises, so
    the message dead-letters rather than writing a wrong default."""
    envelope = _envelope(
        event_id=uuid.uuid4(),
        event_type=consumers.EVT_INVOICE_ISSUED,
        organization_id=org_id,
        occurred_at=datetime.now(UTC),
        invoice_id=str(uuid.uuid4()),
        number="INV-1",
        document_type="invoice",
        customer_id=str(uuid.uuid4()),
        status="sent",
        # issued_date, due_date, subtotal_minor, tax_minor, currency, total_minor are missing.
    )
    with pytest.raises(KeyError):
        await consumers.on_invoice_issued(envelope)


async def test_payment_received_folds_into_payment_facts(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    payment_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)
    await consumers.on_payment_received(
        _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_PAYMENT_RECEIVED,
            organization_id=org_id,
            occurred_at=occurred_at,
            payment_id=str(payment_id),
            reference="UTR-1",
            customer_id=str(uuid.uuid4()),
            received_date="2026-09-15",
            method="bank_transfer",
            currency="INR",
            amount_minor=118000,
            allocations=[],
        )
    )
    row = (
        await admin_session.execute(
            select(PaymentFacts).where(
                PaymentFacts.organization_id == org_id, PaymentFacts.id == payment_id
            )
        )
    ).scalar_one()
    assert row.reference == "UTR-1"
    assert row.currency == "INR"
    assert row.amount_minor == 118000


async def test_purchase_recorded_folds_into_purchase_facts(
    org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    purchase_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
    await consumers.on_purchase_recorded(
        _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_PURCHASE_RECORDED,
            organization_id=org_id,
            occurred_at=occurred_at,
            purchase_document_id=str(purchase_id),
            number="BILL-1",
            document_type="invoice",
            document_date="2026-09-12",
            currency="INR",
            taxable_minor=50000,
            tax_minor=9000,
            total_minor=59000,
        )
    )
    row = (
        await admin_session.execute(
            select(PurchaseFacts).where(
                PurchaseFacts.organization_id == org_id, PurchaseFacts.id == purchase_id
            )
        )
    ).scalar_one()
    assert row.number == "BILL-1"
    assert row.taxable_minor == 50000
    assert row.total_minor == 59000
