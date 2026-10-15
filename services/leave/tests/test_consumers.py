"""Event consumers: the employee directory copy and the approval round trip.

The consumer opens its own `session_scope`, so this mirrors `services/attendance/tests/
test_consumers.py`'s shape: the process engine is initialised by an autouse fixture standing in
for the app lifespan, the handler is called directly (never through the AMQP loop), and
assertions read back through the test's own tenant-bound `session` fixture after
`session.expire_all()` — the handler wrote through a different connection.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app import selectors, services
from app.config import settings
from app.consumers import (
    on_approval_request_decided,
    on_approval_request_opened,
    on_employee_onboarded,
    on_employee_updated,
)
from app.models import AuditLog, EmployeeDirectory, OutboxEvent, ProcessedEvent
from app.schemas import LeaveBalanceGrantIn, LeaveRequestIn
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed
from tests.helpers import (
    approval_decided_envelope,
    approval_opened_envelope,
    ctx_for,
    employee_hierarchy_envelope,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer's `session_scope` needs the process engine initialised — the app lifespan
    does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _pending_request(
    session, org_id, employee_id, leave_type_id, *, half_days=10
) -> uuid.UUID:
    admin_ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
    async with session.begin():
        await services.grant_leave_balance(
            session,
            admin_ctx,
            LeaveBalanceGrantIn(
                employee_id=employee_id, leave_type_id=leave_type_id, half_days=half_days
            ),
        )
    ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
    import datetime as dt

    async with session.begin():
        req = await services.apply_for_leave(
            session,
            ctx,
            LeaveRequestIn(
                leave_type_id=leave_type_id,
                start_date=dt.date(2026, 9, 21),
                end_date=dt.date(2026, 9, 22),
                reason="consumer round trip",
            ),
        )
    return req.id


async def _leave_type_id(session, org_id) -> uuid.UUID:
    import datetime as _dt  # noqa: F401 - imported for readability at call sites only

    from app.schemas import LeaveTypeIn

    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_type.write", Scope.ORG)
    async with session.begin():
        leave_type = await services.create_leave_type(
            session, ctx, LeaveTypeIn(code="EL", name="Earned leave")
        )
        leave_type_id = leave_type.id
    return leave_type_id


class TestApprovalOpened:
    async def test_opening_records_the_routed_approver(self, session, org_id) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)
        approver_id = uuid.uuid4()

        envelope = approval_opened_envelope(
            organization_id=org_id,
            source_ref=req_id,
            approver_employee_id=approver_id,
            approver_name="Manager Manoj",
        )
        await on_approval_request_opened(envelope)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.approver_employee_id == approver_id
        assert req.approver_name == "Manager Manoj"
        assert req.status == "pending"

    async def test_a_request_type_that_is_not_leave_is_ignored(self, session, org_id) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        envelope = approval_opened_envelope(
            organization_id=org_id, source_ref=req_id, request_type="hrms.time.regularization"
        )
        await on_approval_request_opened(envelope)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.approval_opened_at is None


class TestApprovalDecidedApplies:
    async def test_approved_posts_the_consumption_and_marks_applied(self, session, org_id) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=req_id, outcome="approved"
        )
        await on_approval_request_decided(envelope)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "approved"
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 6

        applied = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "hrms.time.leave.applied")
            )
        ).scalar_one()
        assert applied.payload["outcome"] == "applied"

    async def test_the_audit_row_names_the_decider_and_marks_self_approval(
        self, session, org_id
    ) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        req_id = await _pending_request(session, org_id, uuid.uuid4(), leave_type_id)
        decider = uuid.uuid4()

        await on_approval_request_decided(
            approval_decided_envelope(
                organization_id=org_id,
                source_ref=req_id,
                decided_by_user_id=decider,
                self_approved=True,
            )
        )

        session.expire_all()
        row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "leave.request.approved")
            )
        ).scalar_one()
        assert row.actor_id == decider
        assert row.acting_as == "leave.consumer"
        assert row.after["self_approved"] is True

    async def test_rejected_leaves_the_balance_untouched(self, session, org_id) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=req_id, outcome="rejected"
        )
        await on_approval_request_decided(envelope)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "rejected"
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 10

    async def test_redelivery_of_the_same_event_id_is_a_no_op(self, session, org_id) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        event_id = uuid.uuid4()
        envelope = approval_decided_envelope(
            event_id=event_id, organization_id=org_id, source_ref=req_id, outcome="approved"
        )
        await on_approval_request_decided(envelope)
        await on_approval_request_decided(envelope)  # redelivered

        session.expire_all()
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 6  # consumed once, not twice

        applied_events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "hrms.time.leave.applied")
                )
            )
            .scalars()
            .all()
        )
        assert len(applied_events) == 1

    async def test_a_decision_for_an_unknown_request_is_logged_and_dropped(
        self, session, org_id
    ) -> None:
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=uuid.uuid4(), outcome="approved"
        )
        await on_approval_request_decided(envelope)  # must not raise

        session.expire_all()
        event_id = uuid.UUID(envelope["event_id"])
        assert await already_processed(session, ProcessedEvent, event_id)

    async def test_a_decision_arriving_after_a_cancellation_is_a_no_op(
        self, session, org_id
    ) -> None:
        """The row is no longer `pending` by the time `.decided` lands — a no-op, never an
        overwrite of a terminal state (the same rule attendance's identical consumer states)."""
        leave_type_id = await _leave_type_id(session, org_id)
        employee_id = uuid.uuid4()
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        from app.schemas import LeaveCancelIn

        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.cancel_leave_request(session, ctx, req, LeaveCancelIn())

        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=req_id, outcome="approved"
        )
        await on_approval_request_decided(envelope)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "cancelled"  # unchanged
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 10  # the late approval never posted a consumption


class TestEmployeeDirectory:
    async def test_onboarded_writes_the_directory_row(self, session, org_id) -> None:
        employee_id = uuid.uuid4()
        manager_id = uuid.uuid4()
        envelope = employee_hierarchy_envelope(
            organization_id=org_id,
            employee_id=employee_id,
            reporting_manager_id=manager_id,
            status="active",
            hierarchy_version=1,
        )
        await on_employee_onboarded(envelope)

        session.expire_all()
        entry = await selectors.get_directory_entry(session, org_id, employee_id)
        assert entry is not None
        assert entry.reporting_manager_id == manager_id
        assert entry.status == "active"
        assert entry.source_version == 1

    async def test_a_stale_or_equal_version_is_dropped(self, session, org_id) -> None:
        employee_id = uuid.uuid4()
        await on_employee_onboarded(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=employee_id,
                status="active",
                hierarchy_version=5,
            )
        )
        await on_employee_updated(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=employee_id,
                status="exited",
                hierarchy_version=5,  # equal, not greater
                event_type="hrms.people.employee.updated",
            )
        )

        session.expire_all()
        entry = await selectors.get_directory_entry(session, org_id, employee_id)
        assert entry is not None
        assert entry.status == "active"  # the equal-version event was dropped
        assert entry.source_version == 5

    async def test_a_newer_version_overwrites(self, session, org_id) -> None:
        employee_id = uuid.uuid4()
        await on_employee_onboarded(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=employee_id,
                status="active",
                hierarchy_version=1,
            )
        )
        await on_employee_updated(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=employee_id,
                status="on_notice",
                hierarchy_version=2,
                event_type="hrms.people.employee.updated",
            )
        )

        session.expire_all()
        entry = await selectors.get_directory_entry(session, org_id, employee_id)
        assert entry is not None
        assert entry.status == "on_notice"
        assert entry.source_version == 2

    async def test_an_un_upgraded_pod_with_no_hierarchy_version_is_ignored(
        self, session, org_id
    ) -> None:
        employee_id = uuid.uuid4()
        envelope = employee_hierarchy_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=None
        )
        await on_employee_onboarded(envelope)  # must not raise

        session.expire_all()
        entry = await selectors.get_directory_entry(session, org_id, employee_id)
        assert entry is None

    async def test_redelivery_of_the_same_event_id_does_not_reapply(self, session, org_id) -> None:
        employee_id = uuid.uuid4()
        event_id = uuid.uuid4()
        envelope = employee_hierarchy_envelope(
            event_id=event_id, organization_id=org_id, employee_id=employee_id, hierarchy_version=1
        )
        await on_employee_onboarded(envelope)
        await on_employee_onboarded(envelope)  # redelivered, same event id

        session.expire_all()
        rows = (
            (
                await session.execute(
                    select(EmployeeDirectory).where(
                        EmployeeDirectory.organization_id == org_id,
                        EmployeeDirectory.employee_id == employee_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_a_leave_request_snapshots_the_directory_manager_at_apply_time(
        self, session, org_id
    ) -> None:
        """The scope-ladder TEAM column on `LeaveRequest` — see `app/models.py`'s docstring on
        why it is a snapshot rather than a live read."""
        employee_id = uuid.uuid4()
        manager_id = uuid.uuid4()
        await on_employee_onboarded(
            employee_hierarchy_envelope(
                organization_id=org_id,
                employee_id=employee_id,
                reporting_manager_id=manager_id,
                hierarchy_version=1,
            )
        )
        session.expire_all()

        leave_type_id = await _leave_type_id(session, org_id)
        req_id = await _pending_request(session, org_id, employee_id, leave_type_id)

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.reporting_manager_id == manager_id


class TestTenantMismatchIsRefused:
    """`_tenant_from_envelope` refuses to bind `session_scope` to either organization when the
    envelope and the payload disagree — a producer bug or a forged event must never process one
    tenant's row under another tenant's isolation. Logged and dropped, never marked processed,
    so a corrected redelivery with matching ids still applies."""

    async def test_a_directory_event_with_a_mismatched_payload_org_is_dropped(
        self, session, org_id
    ) -> None:
        employee_id = uuid.uuid4()
        envelope = employee_hierarchy_envelope(
            organization_id=org_id, employee_id=employee_id, hierarchy_version=1
        )
        envelope["payload"]["organization_id"] = str(uuid.uuid4())  # disagrees with the envelope

        await on_employee_onboarded(envelope)  # must not raise

        session.expire_all()
        entry = await selectors.get_directory_entry(session, org_id, employee_id)
        assert entry is None

    async def test_an_approval_opened_event_with_a_mismatched_payload_org_is_dropped(
        self, session, org_id
    ) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        req_id = await _pending_request(session, org_id, uuid.uuid4(), leave_type_id)
        envelope = approval_opened_envelope(organization_id=org_id, source_ref=req_id)
        envelope["payload"]["organization_id"] = str(uuid.uuid4())

        await on_approval_request_opened(envelope)  # must not raise

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.approval_opened_at is None

    async def test_an_approval_decided_event_with_a_mismatched_payload_org_is_dropped(
        self, session, org_id
    ) -> None:
        leave_type_id = await _leave_type_id(session, org_id)
        req_id = await _pending_request(session, org_id, uuid.uuid4(), leave_type_id)
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=req_id, outcome="approved"
        )
        envelope["payload"]["organization_id"] = str(uuid.uuid4())

        await on_approval_request_decided(envelope)  # must not raise

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "pending"  # never decided — the mismatched event never touched it

    async def test_a_mismatched_event_is_never_marked_processed(self, session, org_id) -> None:
        """So a corrected, matching redelivery of the same event id still applies — the mismatch
        refusal must not poison the idempotency ledger for a legitimate later attempt."""
        employee_id = uuid.uuid4()
        event_id = uuid.uuid4()
        envelope = employee_hierarchy_envelope(
            event_id=event_id, organization_id=org_id, employee_id=employee_id, hierarchy_version=1
        )
        envelope["payload"]["organization_id"] = str(uuid.uuid4())
        await on_employee_onboarded(envelope)

        async with session.begin():
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert processed is False
