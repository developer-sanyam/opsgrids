"""`apply_page` for the I5/I7 `BackfillSource`s (`app/operations_client.py`,
`app/storefront_client.py`) against real PostgreSQL — field mapping from an export row into the
fact table, and the export upsert rule's `page_requested_at` guard (data-model.md §1.1). I6's
`TaskBackfillSource` gets the fuller, HTTP-level test in `test_backfill_sources.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import operations_client, storefront_client
from app.models import EnquiryFacts, EnquiryLineFacts, PurchaseOrderFacts

pytestmark = pytest.mark.integration


async def test_purchase_order_export_row_maps_onto_the_fact(
    org_id: uuid.UUID, work_scope, admin_session
) -> None:
    po_id = uuid.uuid4()
    supplier_id = uuid.uuid4()
    row = {
        "id": str(po_id),
        "purchase_order_id": str(po_id),
        "number": "PO-2026-0099",
        "status": "received",
        "supplier_id": str(supplier_id),
        "supplier_name": "Meridian Metals",
        "raised_by_id": None,
        "created_date": "2026-08-01",
        "expected_date": "2026-08-20",
        "currency": "INR",
        "total_minor": 125000,
        "received_date": "2026-08-18",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-18T00:00:00+00:00",
        "deleted_at": None,
    }
    run_id = uuid.uuid4()
    page_requested_at = datetime.now(UTC) + timedelta(seconds=1)
    async with work_scope(org_id) as session:
        applied = await operations_client.SOURCE.apply_page(
            session,
            organization_id=org_id,
            rows=[row],
            run_id=run_id,
            page_requested_at=page_requested_at,
        )
    assert applied == 1

    fact = (
        await admin_session.execute(
            select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
        )
    ).scalar_one()
    assert fact.status == "received"
    assert fact.supplier_ref == supplier_id
    assert fact.total_minor == 125000
    assert fact.last_seen_run == run_id


async def test_enquiry_export_row_with_no_storefront_is_skipped_not_guessed(
    org_id: uuid.UUID, work_scope, admin_session
) -> None:
    enquiry_id = uuid.uuid4()
    row = {
        "id": str(enquiry_id),
        "enquiry_id": str(enquiry_id),
        "store_id": None,  # the outer join found no storefront row for this organization
        "company": "Shah Traders",
        "status": "new",
        "line_count": 0,
        "lines": [],
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
        "deleted_at": None,
    }
    async with work_scope(org_id) as session:
        applied = await storefront_client.SOURCE.apply_page(
            session,
            organization_id=org_id,
            rows=[row],
            run_id=uuid.uuid4(),
            page_requested_at=datetime.now(UTC),
        )
    assert applied == 0
    rows = (
        (await admin_session.execute(select(EnquiryFacts).where(EnquiryFacts.id == enquiry_id)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_enquiry_export_row_folds_its_lines(
    org_id: uuid.UUID, work_scope, admin_session
) -> None:
    enquiry_id = uuid.uuid4()
    store_id = uuid.uuid4()
    product_id = uuid.uuid4()
    row = {
        "id": str(enquiry_id),
        "enquiry_id": str(enquiry_id),
        "store_id": str(store_id),
        "company": "Shah Traders",
        "status": "contacted",
        "line_count": 1,
        "lines": [{"product_id": str(product_id), "name": "Basmati Rice", "quantity": 10}],
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
        "deleted_at": None,
    }
    async with work_scope(org_id) as session:
        applied = await storefront_client.SOURCE.apply_page(
            session,
            organization_id=org_id,
            rows=[row],
            run_id=uuid.uuid4(),
            page_requested_at=datetime.now(UTC),
        )
    assert applied == 1
    fact = (
        await admin_session.execute(select(EnquiryFacts).where(EnquiryFacts.id == enquiry_id))
    ).scalar_one()
    assert fact.status == "contacted"
    lines = (
        (
            await admin_session.execute(
                select(EnquiryLineFacts).where(EnquiryLineFacts.enquiry_id == enquiry_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(lines) == 1
    assert lines[0].product_ref == product_id
