"""Inventory Phase 3: the two request types operations routes through this service
(`operations.inventory.transfer`, `operations.inventory.adjustment`) — opened from operations'
`.requested` events, answered up the requester's reporting line, acknowledged by its `.applied`
return leg, withdrawn by its `.cancelled` — through the real consumers and the generic engine.

The envelopes are shaped the way `services/operations/app/movements/` publishes them (the payload
fields were read off that code, and its own tests validate them against the committed JSON
contracts), and the one invariant a shared engine must never get wrong is checked here too: an event
for one request type never touches another's request that happens to share a `source_ref`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, selectors, services
from app.config import settings
from app.models import ApprovalRequest, OutboxEvent, ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from tests.helpers import open_leave, seed_person

pytestmark = pytest.mark.integration

TRANSFER = "operations.inventory.transfer"
ADJUSTMENT = "operations.inventory.adjustment"


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The consumers open their own `session_scope`; the fixture stands in for the lifespan."""
    init_engine(settings())
    yield
    await dispose_engine()


def envelope(
    event_type: str,
    org_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    event_id: uuid.UUID | None = None,
    payload_org: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(org_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"organization_id": str(payload_org or org_id), **payload},
    }


def transfer_requested(
    org_id: uuid.UUID,
    transfer_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    approval_required: bool = True,
    event_id: uuid.UUID | None = None,
    payload_org: uuid.UUID | None = None,
) -> dict[str, Any]:
    return envelope(
        "operations.inventory.transfer.requested",
        org_id,
        {
            "transfer_id": str(transfer_id),
            "number": "TRF-000007",
            "from_warehouse_id": str(uuid.uuid4()),
            "to_warehouse_id": str(uuid.uuid4()),
            "approval_route": "SERVICE" if approval_required else "AUTO",
            "approval_required": approval_required,
            "employee_id": str(employee_id) if approval_required else None,
            "line_count": 2,
            "summary": {
                "transfer_number": "TRF-000007",
                "from_warehouse": "WH-WEAVE",
                "to_warehouse": "WH-FIN",
                "line_count": 2,
                "largest_line": "40 kg DYE-RED-01",
            },
            "reason": "restock finishing",
        },
        event_id=event_id,
        payload_org=payload_org,
    )


def adjustment_requested(
    org_id: uuid.UUID, adjustment_id: uuid.UUID, employee_id: uuid.UUID
) -> dict[str, Any]:
    return envelope(
        "operations.inventory.adjustment.requested",
        org_id,
        {
            "adjustment_id": str(adjustment_id),
            "number": "ADJ-000003",
            "approval_required": True,
            "employee_id": str(employee_id),
            "inventory_item_id": str(uuid.uuid4()),
            "evidence_document_id": str(uuid.uuid4()),
            "summary": {
                "adjustment_number": "ADJ-000003",
                "sku": "DYE-RED-01",
                "quantity_delta": "-25",
                "uom": "kg",
                "location": "DEFAULT",
                "reason_code": "damaged",
            },
            "reason": "drum split in the godown",
        },
    )


def applied(
    kind: str,
    org_id: uuid.UUID,
    source: uuid.UUID,
    approval_request_id: uuid.UUID | None,
    *,
    outcome: str = "applied",
    failure_reason: str | None = None,
) -> dict[str, Any]:
    transfer = kind == "transfer"
    return envelope(
        f"operations.inventory.{kind}.applied",
        org_id,
        {
            "transfer_id" if transfer else "adjustment_id": str(source),
            "approval_request_id": str(approval_request_id) if approval_request_id else None,
            "request_type": TRANSFER if transfer else ADJUSTMENT,
            "outcome": outcome,
            "decision": "approved",
            **({"failure_reason": failure_reason} if failure_reason else {}),
        },
    )


def cancelled(kind: str, org_id: uuid.UUID, source: uuid.UUID) -> dict[str, Any]:
    transfer = kind == "transfer"
    return envelope(
        f"operations.inventory.{kind}.cancelled",
        org_id,
        {
            "transfer_id" if transfer else "adjustment_id": str(source),
            "was_status": "REQUESTED" if transfer else "PENDING_APPROVAL",
            "reason": "changed my mind",
        },
    )


async def request_row(session: AsyncSession, org_id, request_type, source) -> Any:  # type: ignore[no-untyped-def]
    async with session.begin():
        row = await selectors.get_request_by_source(session, org_id, request_type, source)
        if row is not None:
            await session.refresh(row)
        return row


async def decide(session: AsyncSession, org_id, request_id, caller_id, outcome="approve") -> Any:  # type: ignore[no-untyped-def]
    async with session.begin():
        return await services.decide_request(
            session,
            organization_id=org_id,
            request_id=request_id,
            caller_id=caller_id,
            has_org_decide=False,
            outcome=outcome,
            comment="looks right",
        )


@pytest.fixture
async def chain(session: AsyncSession, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A requester and the manager above them: (requester, manager)."""
    requester, manager = uuid.uuid4(), uuid.uuid4()
    async with session.begin():
        await seed_person(session, org_id, manager, display_name="Meera Shah")
        await seed_person(session, org_id, requester, reporting_manager_id=manager)
    return requester, manager


def test_both_request_types_are_known_to_the_policy_settings() -> None:
    assert services.REQUEST_TYPE_INVENTORY_TRANSFER == TRANSFER
    assert services.REQUEST_TYPE_INVENTORY_ADJUSTMENT == ADJUSTMENT
    assert {TRANSFER, ADJUSTMENT} <= set(services.KNOWN_REQUEST_TYPES)


def test_every_handler_is_bound_to_its_event() -> None:
    bound = set(consumers.consumer._handlers)
    assert set(consumers.INVENTORY_EVENTS) <= bound
    assert len(consumers.INVENTORY_EVENTS) == 6


class TestOpening:
    async def test_a_transfer_over_the_threshold_opens_a_request_routed_to_the_manager(
        self, session, org_id, chain
    ) -> None:
        requester, manager = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)
        )
        row = await request_row(session, org_id, TRANSFER, transfer_id)
        assert row is not None
        assert (row.status, row.requester_employee_id) == ("pending", requester)
        assert row.summary["largest_line"] == "40 kg DYE-RED-01"
        assert row.request_note == "restock finishing"
        async with session.begin():
            step = await selectors.get_open_step(session, org_id, row.id)
        assert step is not None and step.approver_employee_id == manager

    async def test_an_adjustment_opens_the_same_way(self, session, org_id, chain) -> None:
        requester, manager = chain
        adjustment_id = uuid.uuid4()
        await consumers.on_inventory_adjustment_requested(
            adjustment_requested(org_id, adjustment_id, requester)
        )
        row = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        assert row is not None and row.request_type == ADJUSTMENT
        assert row.summary["quantity_delta"] == "-25"
        assert row.request_note == "drum split in the godown"
        async with session.begin():
            step = await selectors.get_open_step(session, org_id, row.id)
        assert step is not None and step.approver_employee_id == manager

    async def test_the_opened_event_names_the_approver_for_operations_to_show(
        self, session, org_id, chain
    ) -> None:
        requester, _manager = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)
        )
        row = await request_row(session, org_id, TRANSFER, transfer_id)
        async with session.begin():
            opened = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.aggregate_id == row.id,
                        OutboxEvent.event_type == "core.approval.request.opened",
                    )
                )
            ).scalar_one()
        payload = dict(opened.payload)
        # The three fields operations' consumer reads: which request, which of its own rows, and
        # who it is waiting on.
        assert payload["request_id"] == str(row.id)
        assert (payload["request_type"], payload["source_ref"]) == (TRANSFER, str(transfer_id))
        assert payload["approver_name"] == "Meera Shah"

    async def test_a_request_that_needs_no_approval_is_acknowledged_and_opens_nothing(
        self, session, org_id
    ) -> None:
        transfer_id = uuid.uuid4()
        event_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(
                org_id, transfer_id, uuid.uuid4(), approval_required=False, event_id=event_id
            )
        )
        assert await request_row(session, org_id, TRANSFER, transfer_id) is None
        async with session.begin():
            processed = (
                await session.execute(
                    select(func.count())
                    .select_from(ProcessedEvent)
                    .where(ProcessedEvent.event_id == event_id)
                )
            ).scalar_one()
        assert processed == 1

    async def test_redelivery_and_a_republish_open_it_once(self, session, org_id, chain) -> None:
        requester, _ = chain
        transfer_id = uuid.uuid4()
        same = transfer_requested(org_id, transfer_id, requester, event_id=uuid.uuid4())
        await consumers.on_inventory_transfer_requested(same)
        await consumers.on_inventory_transfer_requested(same)  # redelivered, same event id
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)  # republished, new event id
        )
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(ApprovalRequest)
                    .where(
                        ApprovalRequest.organization_id == org_id,
                        ApprovalRequest.source_ref == transfer_id,
                    )
                )
            ).scalar_one()
        assert count == 1

    async def test_an_envelope_and_payload_that_disagree_are_dropped(
        self, session, org_id, chain
    ) -> None:
        requester, _ = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester, payload_org=uuid.uuid4())
        )
        assert await request_row(session, org_id, TRANSFER, transfer_id) is None


class TestTheDecisionAndTheReturnLeg:
    async def test_a_decision_publishes_what_operations_needs_to_apply_it(
        self, session, org_id, chain
    ) -> None:
        requester, manager = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)
        )
        row = await request_row(session, org_id, TRANSFER, transfer_id)
        decided = await decide(session, org_id, row.id, manager)
        assert decided.status == "approved"
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.aggregate_id == row.id,
                        OutboxEvent.event_type == "core.approval.request.decided",
                    )
                )
            ).scalar_one()
        payload = dict(event.payload)
        assert (payload["request_type"], payload["source_ref"], payload["outcome"]) == (
            TRANSFER,
            str(transfer_id),
            "approved",
        )
        assert payload["decided_by_name"] == "Meera Shah" and payload["comment"] == "looks right"

    async def test_the_applied_return_leg_is_recorded(self, session, org_id, chain) -> None:
        requester, manager = chain
        adjustment_id = uuid.uuid4()
        await consumers.on_inventory_adjustment_requested(
            adjustment_requested(org_id, adjustment_id, requester)
        )
        row = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        await decide(session, org_id, row.id, manager)
        await consumers.on_inventory_adjustment_applied(
            applied("adjustment", org_id, adjustment_id, row.id)
        )
        done = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        assert done.application_state == "applied"

    async def test_a_failed_application_keeps_its_reason(self, session, org_id, chain) -> None:
        requester, manager = chain
        adjustment_id = uuid.uuid4()
        await consumers.on_inventory_adjustment_requested(
            adjustment_requested(org_id, adjustment_id, requester)
        )
        row = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        await decide(session, org_id, row.id, manager)
        await consumers.on_inventory_adjustment_applied(
            applied(
                "adjustment",
                org_id,
                adjustment_id,
                row.id,
                outcome="failed",
                failure_reason="Cannot take 25 kg: 5 kg on hand",
            )
        )
        done = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        assert done.application_state == "failed"
        assert done.application_failure_reason == "Cannot take 25 kg: 5 kg on hand"

    async def test_an_applied_event_with_no_approval_request_has_nothing_to_record(
        self, session, org_id, chain
    ) -> None:
        requester, _ = chain
        adjustment_id = uuid.uuid4()
        await consumers.on_inventory_adjustment_requested(
            adjustment_requested(org_id, adjustment_id, requester)
        )
        # An adjustment that needed no approval publishes `.applied` with no approval id at all.
        await consumers.on_inventory_adjustment_applied(
            applied("adjustment", org_id, adjustment_id, None)
        )
        row = await request_row(session, org_id, ADJUSTMENT, adjustment_id)
        assert row.application_state is None

    async def test_applied_before_the_decision_lands_is_left_for_later(
        self, session, org_id, chain
    ) -> None:
        requester, _ = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)
        )
        row = await request_row(session, org_id, TRANSFER, transfer_id)
        await consumers.on_inventory_transfer_applied(
            applied("transfer", org_id, transfer_id, row.id)
        )
        assert (await request_row(session, org_id, TRANSFER, transfer_id)).application_state is None


class TestWithdrawal:
    async def test_cancelling_closes_the_open_step_so_nobody_can_decide_it(
        self, session, org_id, chain
    ) -> None:
        requester, _manager = chain
        transfer_id = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, transfer_id, requester)
        )
        row = await request_row(session, org_id, TRANSFER, transfer_id)
        await consumers.on_inventory_transfer_cancelled(cancelled("transfer", org_id, transfer_id))
        async with session.begin():
            step = await selectors.get_open_step(session, org_id, row.id)
        assert step is None
        # Cancelling something that was never routed (a draft, or a direct-route transfer) is fine.
        await consumers.on_inventory_transfer_cancelled(cancelled("transfer", org_id, uuid.uuid4()))
        await consumers.on_inventory_adjustment_cancelled(
            cancelled("adjustment", org_id, uuid.uuid4())
        )

    async def test_an_adjustment_cancellation_withdraws_only_its_own_request(
        self, session, org_id, chain
    ) -> None:
        requester, _ = chain
        shared = uuid.uuid4()
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, shared, requester)
        )
        await consumers.on_inventory_adjustment_requested(
            adjustment_requested(org_id, shared, requester)
        )
        await consumers.on_inventory_adjustment_cancelled(cancelled("adjustment", org_id, shared))
        transfer = await request_row(session, org_id, TRANSFER, shared)
        adjustment = await request_row(session, org_id, ADJUSTMENT, shared)
        async with session.begin():
            transfer_step = await selectors.get_open_step(session, org_id, transfer.id)
            adjustment_step = await selectors.get_open_step(session, org_id, adjustment.id)
        assert transfer_step is not None, "the transfer's own request must survive"
        assert adjustment_step is None


class TestTypesDoNotCrossTalk:
    async def test_an_inventory_event_never_touches_another_types_request_with_the_same_ref(
        self, session, org_id, chain
    ) -> None:
        requester, manager = chain
        shared = uuid.uuid4()
        leave = await open_leave(
            session, org_id, source_ref=shared, requester_employee_id=requester
        )
        assert leave is not None
        await consumers.on_inventory_transfer_requested(
            transfer_requested(org_id, shared, requester)
        )
        transfer = await request_row(session, org_id, TRANSFER, shared)
        assert transfer is not None and transfer.id != leave.id
        await decide(session, org_id, transfer.id, manager)
        await consumers.on_inventory_transfer_applied(
            applied("transfer", org_id, shared, transfer.id)
        )
        await consumers.on_inventory_adjustment_applied(
            applied("adjustment", org_id, shared, transfer.id)
        )
        leave_row = await request_row(session, org_id, "hrms.time.leave", shared)
        assert leave_row.application_state is None and leave_row.status == "pending"
        assert (await request_row(session, org_id, TRANSFER, shared)).application_state == "applied"

    async def test_open_inventory_request_refuses_a_foreign_type(self, session, org_id) -> None:
        with pytest.raises(ValueError, match="not an inventory request type"):
            async with session.begin():
                await services.open_inventory_request(
                    session,
                    request_type="hrms.time.leave",
                    organization_id=org_id,
                    source_ref=uuid.uuid4(),
                    requester_employee_id=uuid.uuid4(),
                    summary={},
                    request_note=None,
                )
