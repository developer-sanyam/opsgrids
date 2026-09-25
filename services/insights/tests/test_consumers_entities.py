"""Idempotent folds for I5 (purchase orders), I6 (tasks) and I7 (enquiries) —
`docs/insights/events.md` §1, data-model.md §1.1: a redelivered event folds once, a partial
transition never inserts, and an orphan partial transition (its creating event never arrived)
is logged and acked rather than raised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import consumers
from app.models import EnquiryFacts, EnquiryLineFacts, ProcessedEvent, PurchaseOrderFacts, TaskFacts

pytestmark = pytest.mark.integration


def _envelope(
    *,
    event_id: uuid.UUID,
    event_type: str,
    organization_id: uuid.UUID,
    occurred_at: datetime,
    payload: dict[str, object],
    with_org_in_payload: bool = True,
) -> dict[str, object]:
    body = {"organization_id": str(organization_id), **payload} if with_org_in_payload else payload
    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "organization_id": str(organization_id),
        "occurred_at": occurred_at.isoformat(),
        "payload": body,
    }


@pytest.fixture(autouse=True)
def _bind_test_session_scope(work_scope) -> None:
    consumers.use_session_scope(work_scope)
    yield
    consumers.use_session_scope(consumers._default_session_scope)


def _po_payload(po_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    payload = {
        "purchase_order_id": str(po_id),
        "number": "PO-2026-0001",
        "status": "draft",
        "supplier_id": str(uuid.uuid4()),
        "supplier_name": "Meridian Metals",
        "raised_by_id": str(uuid.uuid4()),
        "created_date": "2026-09-01",
        "expected_date": "2026-09-20",
        "currency": "INR",
        "total_minor": 500000,
    }
    payload.update(overrides)
    return payload


class TestPurchaseOrderFacts:
    async def test_raised_then_updated_folds_full_state(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        po_id = uuid.uuid4()
        occurred_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        await consumers.on_po_raised(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_RAISED,
                organization_id=org_id,
                occurred_at=occurred_at,
                payload=_po_payload(po_id),
            )
        )
        row = (
            await admin_session.execute(
                select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
            )
        ).scalar_one()
        assert row.status == "draft"
        assert row.total_minor == 500000

        updated_at = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
        await consumers.on_po_updated(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_UPDATED,
                organization_id=org_id,
                occurred_at=updated_at,
                payload=_po_payload(po_id, status="draft", total_minor=750000, changed=["notes"]),
            )
        )
        admin_session.expire_all()  # `expire_on_commit=False`: force a fresh read of the row
        row = (
            await admin_session.execute(
                select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
            )
        ).scalar_one()
        assert row.total_minor == 750000

    async def test_a_stale_replay_never_overwrites_a_newer_version(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        po_id = uuid.uuid4()
        newer = datetime(2026, 9, 5, tzinfo=UTC)
        older = datetime(2026, 9, 1, tzinfo=UTC)
        await consumers.on_po_raised(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_RAISED,
                organization_id=org_id,
                occurred_at=newer,
                payload=_po_payload(po_id, number="PO-NEWER"),
            )
        )
        await consumers.on_po_updated(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_UPDATED,
                organization_id=org_id,
                occurred_at=older,
                payload=_po_payload(po_id, number="PO-OLDER", changed=["number"]),
            )
        )
        await admin_session.commit()
        row = (
            await admin_session.execute(
                select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
            )
        ).scalar_one()
        assert row.number == "PO-NEWER"

    async def test_redelivery_is_idempotent(self, org_id: uuid.UUID, admin_session) -> None:
        event_id = uuid.uuid4()
        envelope = _envelope(
            event_id=event_id,
            event_type=consumers.EVT_PO_RAISED,
            organization_id=org_id,
            occurred_at=datetime.now(UTC),
            payload=_po_payload(uuid.uuid4()),
        )
        await consumers.on_po_raised(envelope)
        await consumers.on_po_raised(envelope)  # redelivered, same event_id

        processed = (
            await admin_session.execute(
                select(func.count())
                .select_from(ProcessedEvent)
                .where(ProcessedEvent.event_id == event_id)
            )
        ).scalar_one()
        assert processed == 1

    async def test_approved_received_closed_are_partial_transitions(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        po_id = uuid.uuid4()
        t0 = datetime(2026, 9, 1, tzinfo=UTC)
        await consumers.on_po_raised(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_RAISED,
                organization_id=org_id,
                occurred_at=t0,
                payload=_po_payload(po_id),
            )
        )
        await consumers.on_po_approved(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_APPROVED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
                payload={
                    "purchase_order_id": str(po_id),
                    "number": "PO-2026-0001",
                    "spend_applied": True,
                    "currency": "INR",
                    "total_minor": 500000,
                },
            )
        )
        await consumers.on_po_received(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_RECEIVED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
                payload={
                    "purchase_order_id": str(po_id),
                    "number": "PO-2026-0001",
                    "received_date": "2026-09-10",
                    "expected_date": "2026-09-20",
                    "unmatched_skus": [],
                    "matched_lines": [],
                },
            )
        )
        await admin_session.commit()
        row = (
            await admin_session.execute(
                select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
            )
        ).scalar_one()
        assert row.status == "received"
        assert row.received_date.isoformat() == "2026-09-10"

    async def test_a_partial_transition_with_no_creating_event_is_an_orphan_and_never_inserts(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        po_id = uuid.uuid4()
        await consumers.on_po_cancelled(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_PO_CANCELLED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                payload={
                    "purchase_order_id": str(po_id),
                    "number": "PO-GHOST",
                    "reason": "never raised in insights",
                },
            )
        )
        rows = (
            (
                await admin_session.execute(
                    select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


def _task_payload(task_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    payload = {
        "task_id": str(task_id),
        "project_id": str(uuid.uuid4()),
        "stage": "backlog",
        "title": "Bulk-order CSV import",
        "assignee_id": str(uuid.uuid4()),
        "priority": "medium",
        "due_date": None,
        "points": 3,
        "completed_at": None,
    }
    payload.update(overrides)
    return payload


class TestTaskFacts:
    async def test_created_then_moved_to_done_stamps_completed_at(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        task_id = uuid.uuid4()
        await consumers.on_task_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_TASK_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                payload=_task_payload(task_id),
            )
        )
        completed_at = datetime(2026, 9, 5, tzinfo=UTC)
        await consumers.on_task_updated(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_TASK_UPDATED,
                organization_id=org_id,
                occurred_at=completed_at,
                payload=_task_payload(task_id, stage="done", completed_at=completed_at.isoformat()),
            )
        )
        await admin_session.commit()
        row = (
            await admin_session.execute(select(TaskFacts).where(TaskFacts.id == task_id))
        ).scalar_one()
        assert row.stage == "done"
        assert row.completed_at == completed_at

    async def test_deleted_is_partial_and_orphan_when_never_created(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        task_id = uuid.uuid4()
        await consumers.on_task_deleted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_TASK_DELETED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                payload={"task_id": str(task_id), "project_id": str(uuid.uuid4())},
            )
        )
        rows = (
            (await admin_session.execute(select(TaskFacts).where(TaskFacts.id == task_id)))
            .scalars()
            .all()
        )
        assert rows == []

        await consumers.on_task_created(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_TASK_CREATED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                payload=_task_payload(task_id),
            )
        )
        await consumers.on_task_deleted(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_TASK_DELETED,
                organization_id=org_id,
                occurred_at=datetime(2026, 9, 2, tzinfo=UTC),
                payload={"task_id": str(task_id), "project_id": str(uuid.uuid4())},
            )
        )
        await admin_session.commit()
        row = (
            await admin_session.execute(select(TaskFacts).where(TaskFacts.id == task_id))
        ).scalar_one()
        assert row.deleted_at is not None


class TestEnquiryFacts:
    async def test_created_carries_no_payload_org_and_folds_lines(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        enquiry_id = uuid.uuid4()
        product_id = uuid.uuid4()
        envelope = _envelope(
            event_id=uuid.uuid4(),
            event_type=consumers.EVT_ENQUIRY_CREATED,
            organization_id=org_id,
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            payload={
                "enquiry_id": str(enquiry_id),
                "store_id": str(uuid.uuid4()),
                "company": "Shah Traders",
                "status": "new",
                "line_count": 1,
                "lines": [{"product_id": str(product_id), "name": "Basmati Rice", "quantity": 5}],
            },
            with_org_in_payload=False,
        )
        await consumers.on_enquiry_created(envelope)
        row = (
            await admin_session.execute(select(EnquiryFacts).where(EnquiryFacts.id == enquiry_id))
        ).scalar_one()
        assert row.company == "Shah Traders"
        assert row.line_count == 1
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

    async def test_status_changed_is_partial_and_orphan_when_never_created(
        self, org_id: uuid.UUID, admin_session
    ) -> None:
        enquiry_id = uuid.uuid4()
        await consumers.on_enquiry_status_changed(
            _envelope(
                event_id=uuid.uuid4(),
                event_type=consumers.EVT_ENQUIRY_STATUS_CHANGED,
                organization_id=org_id,
                occurred_at=datetime.now(UTC),
                payload={"enquiry_id": str(enquiry_id), "from": "new", "to": "contacted"},
            )
        )
        rows = (
            (await admin_session.execute(select(EnquiryFacts).where(EnquiryFacts.id == enquiry_id)))
            .scalars()
            .all()
        )
        assert rows == []


class TestCrossTenantIsolation:
    async def test_a_tenant_mismatch_between_envelope_and_payload_is_dropped(
        self, org_id: uuid.UUID, other_org: uuid.UUID, admin_session
    ) -> None:
        po_id = uuid.uuid4()
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": consumers.EVT_PO_RAISED,
            "organization_id": str(org_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {"organization_id": str(other_org), **_po_payload(po_id)},
        }
        await consumers.on_po_raised(envelope)  # must not raise, and must write nothing
        rows = (
            (
                await admin_session.execute(
                    select(PurchaseOrderFacts).where(PurchaseOrderFacts.id == po_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
