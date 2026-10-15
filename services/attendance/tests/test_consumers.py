"""`core.approval.request.decided`, filtered to `hrms.time.regularization` (ADR-0006 §12).

The consumer opens its own `session_scope`, so this mirrors `services/approval/tests/
test_consumers.py`'s shape: the process engine is initialised by an autouse fixture standing in
for the app lifespan, the handler is called directly (never through the AMQP loop), and
assertions read back through the test's own tenant-bound `session` fixture rather than the
(committed, but separately connected) session the handler used — which is also why every helper
here returns a plain `uuid.UUID` rather than the ORM row: touching an attribute on an object this
session already cached, after `session.expire_all()`, would trigger an implicit reload outside
the async greenlet context and raise `MissingGreenlet`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from app import selectors, services
from app.config import settings
from app.consumers import on_approval_request_decided, on_approval_request_opened
from app.models import AuditLog, OutboxEvent, ProcessedEvent
from app.schemas import RegularizationRequestIn
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed
from tests.helpers import approval_decided_envelope, approval_opened_envelope
from tests.helpers import ctx_for as _ctx_for

pytestmark = pytest.mark.integration


def ctx_for(org_id, subject_ref, permission, scope):
    return _ctx_for(org_id, subject_ref, permission, scope)


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer's `session_scope` needs the process engine initialised — the app lifespan
    does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _pending_request(
    session, org_id, employee_id, attendance_id, *, requested_status
) -> uuid.UUID:
    ctx = ctx_for(org_id, employee_id, "hrms.time.attendance.request", Scope.OWN)
    async with session.begin():
        record = await selectors.get_attendance(session, org_id, attendance_id)
        assert record is not None
        reg = await services.request_regularization(
            session,
            ctx,
            record,
            RegularizationRequestIn(requested_status=requested_status, reason="left early"),
        )
        reg_id = reg.id
    return reg_id


async def _applied_event(session, aggregate_id: uuid.UUID):
    return (
        await session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "hrms.time.regularization.applied",
                OutboxEvent.aggregate_id == aggregate_id,
            )
        )
    ).scalar_one()


class TestApprovedDecisionApplies:
    async def test_the_audit_row_names_the_decider_and_marks_self_approval(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        decider = uuid.uuid4()

        await on_approval_request_decided(
            approval_decided_envelope(
                organization_id=org_id,
                source_ref=reg_id,
                decided_by_user_id=decider,
                self_approved=True,
            )
        )

        session.expire_all()
        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "attendance.regularization_approved",
                    AuditLog.entity_id == reg_id,
                )
            )
        ).scalar_one()
        assert row.actor_id == decider
        assert row.acting_as == "attendance.consumer"
        assert row.after["self_approved"] is True

    async def test_a_legal_transition_is_applied_and_both_events_are_emitted(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )

        await on_approval_request_decided(envelope)
        # The consumer wrote through a different connection (its own `session_scope`); this
        # session's identity map otherwise returns the pre-decision objects it already cached.
        session.expire_all()

        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert record is not None
        assert updated is not None
        assert record.status == "half_day"
        assert updated.status == "approved"
        assert updated.approval_request_id is not None
        assert updated.decided_at is not None

        regularized_count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.time.attendance.regularized",
                    OutboxEvent.aggregate_id == records["alice_record"],
                )
            )
        ).scalar_one()
        assert regularized_count == 1

        applied = await _applied_event(session, reg_id)
        assert applied.payload["outcome"] == "applied"
        assert applied.payload["regularization_request_id"] == str(reg_id)
        assert "failure_reason" not in applied.payload


class TestIllegalByTheTimeItLands:
    async def test_marks_failed_with_a_reason_and_does_not_raise(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        # HR overrides the day directly while the request is still in flight — exactly the race
        # ADR-0006 §12 names.
        admin_ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.ORG)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.regularize_attendance(
                session, admin_ctx, record, "regularized", reason="signed off directly"
            )

        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )
        await on_approval_request_decided(envelope)  # must not raise
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
        assert updated is not None
        assert updated.status == "failed"
        assert updated.failure_reason
        assert record is not None
        assert record.status == "regularized"  # unchanged by the failed apply

        applied = await _applied_event(session, reg_id)
        assert applied.payload["outcome"] == "failed"
        assert applied.payload["failure_reason"]


class TestRejectedDecision:
    async def test_marks_rejected_and_still_reports_applied(self, session, org_id, records) -> None:
        """ADR-0006 §12: the event means "I applied your decision", not "I corrected the day" —
        a rejection is a faithful application too."""
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="rejected"
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["bob_record"])
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert record is not None
        assert updated is not None
        assert record.status == "present"  # untouched
        assert updated.status == "rejected"

        regularized_count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.time.attendance.regularized",
                    OutboxEvent.aggregate_id == records["bob_record"],
                )
            )
        ).scalar_one()
        assert regularized_count == 0

        applied = await _applied_event(session, reg_id)
        assert applied.payload["outcome"] == "applied"


class TestIgnoresOtherRequestTypes:
    async def test_a_decision_for_a_request_type_this_product_does_not_own_is_a_no_op(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            outcome="approved",
            request_type="hrms.leave.application",
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert record is not None
        assert updated is not None
        assert record.status == "present"
        assert updated.status == "pending"


class TestRedeliveryIsIdempotent:
    async def test_the_same_event_id_delivered_twice_is_applied_once(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        event_id = uuid.uuid4()
        envelope = approval_decided_envelope(
            event_id=event_id, organization_id=org_id, source_ref=reg_id, outcome="approved"
        )

        await on_approval_request_decided(envelope)
        await on_approval_request_decided(envelope)  # redelivered, identical body
        session.expire_all()

        async with session.begin():
            applied_count = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == "hrms.time.regularization.applied",
                        OutboxEvent.aggregate_id == reg_id,
                    )
                )
            ).scalar_one()
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert applied_count == 1
        assert processed is True

    async def test_an_event_for_a_request_no_longer_pending_is_a_no_op(
        self, session, org_id, records
    ) -> None:
        """A stale replay, or `.opened` arriving after `.decided`, finds the row already settled
        (ADR-0006 §12) — a *different* event id than the one that settled it, so this exercises
        the "acts only if still pending" guard rather than the `processed_events` ledger."""
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        first = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )
        await on_approval_request_decided(first)

        replay = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="rejected"
        )
        await on_approval_request_decided(replay)  # must not flip an already-approved request
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.status == "approved"


class TestDecidedEventStoresComment:
    """`.decided`'s `comment` field (`services/approval/app/services.py`'s own `payload={...}` —
    `decide_request` publishes it as `"comment": comment`) is the only piece of "who decided and
    why" that event actually carries; there is no `decided_by` display name on it at all."""

    async def test_a_rejection_comment_is_stored_verbatim(self, session, org_id, records) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            outcome="rejected",
            comment="Insufficient documentation attached",
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.status == "rejected"
        assert updated.decision_comment == "Insufficient documentation attached"

    async def test_no_comment_stores_null_rather_than_an_empty_string(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.decision_comment is None


class TestOpenedEventPopulatesApprover:
    """`.opened`, consumed for the first time (ADR-0006 §12's data model always intended this half
    of the round trip). Read directly off `services/approval/app/services.py`'s own
    `payload={...}` for `EVT_REQUEST_OPENED`: `approver_employee_id` is the one field this test
    exercises — that event carries no display name for the approver at all, so "populates the
    approver" here means the id, not a name (see `app/consumers.py`'s module docstring)."""

    async def test_an_assigned_step_records_the_approver_and_the_approval_request_id(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        approver_id = uuid.uuid4()
        envelope = approval_opened_envelope(
            organization_id=org_id, source_ref=reg_id, approver_employee_id=approver_id
        )

        await on_approval_request_opened(envelope)
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.status == "pending"  # unaffected — `.opened` never decides anything
        assert reg.approval_opened_at is not None
        assert reg.approver_employee_id == approver_id
        assert reg.approval_request_id is not None


class TestOpenedEventUnassignedStepIsDistinctFromNotYetOpened:
    """A `null` `approver_employee_id` means two different things depending on whether `.opened`
    has landed yet at all — ADR-0006 §4's unassigned step must be representable as its own state,
    not collapsed into "routing unknown"."""

    async def test_before_opened_the_approver_is_unknown_not_unassigned(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approval_opened_at is None
        assert reg.approver_employee_id is None

    async def test_an_unassigned_step_sets_opened_at_with_a_null_approver(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        envelope = approval_opened_envelope(
            organization_id=org_id, source_ref=reg_id, approver_employee_id=None
        )

        await on_approval_request_opened(envelope)
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approval_opened_at is not None  # now known: opened, and...
        assert reg.approver_employee_id is None  # ...genuinely unassigned, not merely unknown


class TestLateOrDuplicateOpenedNeverOverwritesDecided:
    async def test_an_opened_event_arriving_after_decided_is_a_no_op(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        decided = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )
        await on_approval_request_decided(decided)

        late_opened = approval_opened_envelope(
            organization_id=org_id, source_ref=reg_id, approver_employee_id=uuid.uuid4()
        )
        await on_approval_request_opened(late_opened)  # must not raise or touch the settled row
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.status == "approved"
        assert reg.approval_opened_at is None  # the late `.opened` never wrote through
        assert reg.approver_employee_id is None

    async def test_the_same_opened_event_delivered_twice_is_applied_once(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="half_day"
        )
        event_id = uuid.uuid4()
        approver_id = uuid.uuid4()
        envelope = approval_opened_envelope(
            event_id=event_id,
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=approver_id,
        )

        await on_approval_request_opened(envelope)
        await on_approval_request_opened(envelope)  # redelivered, identical body
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert reg is not None
        assert reg.approver_employee_id == approver_id
        assert processed is True


class TestOpenedEventIgnoresOtherRequestTypes:
    async def test_an_opened_event_for_a_request_type_this_product_does_not_own_is_a_no_op(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_opened_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=uuid.uuid4(),
            request_type="hrms.leave.application",
        )

        await on_approval_request_opened(envelope)
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approval_opened_at is None
        assert reg.approver_employee_id is None


class TestOpenedEventStoresApproverName:
    """`.opened`'s own `approver_name` (approval's fix for the contract gap `app/consumers.py`'s
    module docstring used to describe) — the id alone is not enough to render "Waiting on
    {Name}"."""

    async def test_an_assigned_step_records_the_approvers_name(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        approver_id = uuid.uuid4()
        envelope = approval_opened_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=approver_id,
            approver_name="Priya Sharma",
        )

        await on_approval_request_opened(envelope)
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approver_employee_id == approver_id
        assert reg.approver_name == "Priya Sharma"

    async def test_an_unassigned_step_has_no_approver_name(self, session, org_id, records) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        envelope = approval_opened_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=None,
            approver_name=None,
        )

        await on_approval_request_opened(envelope)
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approver_employee_id is None
        assert reg.approver_name is None

    async def test_an_event_published_without_the_name_key_stores_null_without_raising(
        self, session, org_id, records
    ) -> None:
        """A pre-upgrade producer, or a replay from before approval's change landed — the key is
        simply absent from the payload, not `null`. Must not raise or dead-letter."""
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        approver_id = uuid.uuid4()
        envelope = approval_opened_envelope(
            organization_id=org_id, source_ref=reg_id, approver_employee_id=approver_id
        )
        assert "approver_name" not in envelope["payload"]  # the shape this test exercises

        await on_approval_request_opened(envelope)  # must not raise
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approver_employee_id == approver_id
        assert reg.approver_name is None


class TestDecidedEventStoresDeciderName:
    """`.decided`'s own `decided_by_name` — stored alongside `decision_comment` on a decided row
    so the screen can show "{who} rejected/approved, with comment"."""

    async def test_the_deciders_name_is_stored_on_approval(self, session, org_id, records) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            outcome="approved",
            decided_by_name="Rahul Verma",
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.status == "approved"
        assert updated.decided_by_name == "Rahul Verma"

    async def test_the_deciders_name_is_stored_on_rejection(self, session, org_id, records) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="absent"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            outcome="rejected",
            decided_by_name="Rahul Verma",
            comment="Not enough documentation",
        )

        await on_approval_request_decided(envelope)
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.status == "rejected"
        assert updated.decided_by_name == "Rahul Verma"
        assert updated.decision_comment == "Not enough documentation"

    async def test_a_decider_outside_the_hierarchy_copy_has_no_name_without_raising(
        self, session, org_id, records
    ) -> None:
        """A route-(c) `decide:ORG` decision may be made by someone with no denormalized name at
        all — legitimate, not an error (ADR-0006). The event carries the key, but `null`."""
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved", decided_by_name=None
        )

        await on_approval_request_decided(envelope)  # must not raise
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.status == "approved"
        assert updated.decided_by_name is None

    async def test_an_event_published_without_the_name_key_stores_null_without_raising(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="half_day"
        )
        envelope = approval_decided_envelope(
            organization_id=org_id, source_ref=reg_id, outcome="approved"
        )
        assert "decided_by_name" not in envelope["payload"]  # the shape this test exercises

        await on_approval_request_decided(envelope)  # must not raise
        session.expire_all()

        async with session.begin():
            updated = await selectors.get_regularization(session, org_id, reg_id)
        assert updated is not None
        assert updated.decided_by_name is None


class TestLateOpenedNeverOverwritesDecidedNames:
    async def test_a_late_opened_event_does_not_overwrite_a_decided_rows_stored_names(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        decided = approval_decided_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            outcome="approved",
            decided_by_name="Rahul Verma",
        )
        await on_approval_request_decided(decided)

        late_opened = approval_opened_envelope(
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=uuid.uuid4(),
            approver_name="Someone Else",
        )
        await on_approval_request_opened(late_opened)  # must not raise or touch the settled row
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.status == "approved"
        assert reg.approver_name is None  # the late `.opened` never wrote through
        assert reg.decided_by_name == "Rahul Verma"  # the decided row's own name is untouched


class TestNameFieldsAreIdempotentUnderRedelivery:
    async def test_a_redelivered_opened_event_applies_the_name_once(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["bob"], records["bob_record"], requested_status="half_day"
        )
        event_id = uuid.uuid4()
        envelope = approval_opened_envelope(
            event_id=event_id,
            organization_id=org_id,
            source_ref=reg_id,
            approver_employee_id=uuid.uuid4(),
            approver_name="Priya Sharma",
        )

        await on_approval_request_opened(envelope)
        await on_approval_request_opened(envelope)  # redelivered, identical body
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert reg is not None
        assert reg.approver_name == "Priya Sharma"
        assert processed is True

    async def test_a_redelivered_decided_event_applies_the_name_once(
        self, session, org_id, records
    ) -> None:
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        event_id = uuid.uuid4()
        envelope = approval_decided_envelope(
            event_id=event_id,
            organization_id=org_id,
            source_ref=reg_id,
            outcome="approved",
            decided_by_name="Rahul Verma",
        )

        await on_approval_request_decided(envelope)
        await on_approval_request_decided(envelope)  # redelivered, identical body
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert reg is not None
        assert reg.decided_by_name == "Rahul Verma"
        assert processed is True


class TestOpenedEventCrossTenantIsolation:
    async def test_an_opened_event_addressed_to_another_organization_does_not_touch_this_row(
        self, session, org_id, records
    ) -> None:
        """The consumer's `session_scope(organization_id=...)` binds the transaction-local tenant
        GUC to the event's *own* `organization_id` — the same tenant-scoping chokepoint every
        other query in this service goes through. An event that names a different organization
        than the one this row lives in must find nothing, under RLS, exactly as a cross-tenant
        HTTP request would (ADR-0002)."""
        reg_id = await _pending_request(
            session, org_id, records["alice"], records["alice_record"], requested_status="half_day"
        )
        other_org = uuid.uuid4()
        envelope = approval_opened_envelope(
            organization_id=other_org, source_ref=reg_id, approver_employee_id=uuid.uuid4()
        )

        await on_approval_request_opened(envelope)  # looks up (other_org, reg_id) — no such row
        session.expire_all()

        async with session.begin():
            reg = await selectors.get_regularization(session, org_id, reg_id)
        assert reg is not None
        assert reg.approval_opened_at is None
        assert reg.approver_employee_id is None
