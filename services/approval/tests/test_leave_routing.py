"""HRMS leave slice 1: `open_leave_request`/`apply_leave_outcome` through the generic engine,
the consumers that call them (`on_leave_requested`, `on_leave_applied`, `on_leave_cancelled`),
and the one invariant a shared `request_type`-parametrized engine must never get wrong — a
`.applied` (or `.cancelled`) event for one product's request type must never touch another
product's request even when both happen to name the same `source_ref`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.consumers import on_leave_applied, on_leave_cancelled, on_leave_requested
from app.models import ApprovalRequest, ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed
from tests.helpers import (
    leave_applied_envelope,
    leave_cancelled_envelope,
    leave_requested_envelope,
    open_leave,
    open_request,
    seed_person,
)

pytestmark = pytest.mark.integration


async def _open_step_for(
    session: AsyncSession, org_id: uuid.UUID, request_type: str, source_ref: uuid.UUID
):
    request = await selectors.get_request_by_source(session, org_id, request_type, source_ref)
    if request is None:
        return None
    return await selectors.get_open_step(session, org_id, request.id)


@pytest.fixture(autouse=True)
async def _engine():
    """The consumers open their own `session_scope` — the app lifespan does this in production;
    here the fixture stands in for it, the same shape `tests/test_consumers.py` uses."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _decide(
    session: AsyncSession, org_id: uuid.UUID, *, request_id: uuid.UUID, caller_id: uuid.UUID
) -> ApprovalRequest:
    async with session.begin():
        return await services.decide_request(
            session,
            organization_id=org_id,
            request_id=request_id,
            caller_id=caller_id,
            has_org_decide=False,
            outcome="approve",
            comment=None,
        )


class TestOpeningALeaveRequest:
    async def test_opens_with_the_leave_request_type_and_one_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_leave(session, org_id, requester_employee_id=requester)
        assert request is not None
        assert request.request_type == "hrms.time.leave"
        assert request.status == "pending"
        # Captured before `expire_all()` below — an ORM attribute read afterwards, outside of
        # any `await`, is exactly the "flush/expire, then a plain `getattr` needs a lazy load
        # the async session cannot service" trap `app/services.py::update_leave_type`'s own
        # fix (leave-service, this same review round) documents.
        request_id, source_ref = request.id, request.source_ref

        session.expire_all()
        async with session.begin():
            row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.leave", source_ref
            )
        assert row is not None
        assert row.id == request_id

    async def test_reopening_the_same_source_ref_is_a_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        source_ref = uuid.uuid4()
        first = await open_leave(
            session, org_id, source_ref=source_ref, requester_employee_id=requester
        )
        second = await open_leave(
            session, org_id, source_ref=source_ref, requester_employee_id=requester
        )
        assert first is not None
        assert second is None


class TestLeaveAndRegularizationDoNotCrossTalk:
    """The one invariant a shared, `request_type`-parametrized engine must never get wrong: two
    different products' requests that happen to share a `source_ref` (a UUID collision is
    astronomically unlikely in production, but the engine's own correctness must not depend on
    that — it depends on `request_type` being part of every lookup key) stay fully independent."""

    async def test_applying_a_leave_outcome_never_touches_a_regularization_with_the_same_ref(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        shared_ref = uuid.uuid4()
        leave_req = await open_leave(
            session, org_id, source_ref=shared_ref, requester_employee_id=requester
        )
        reg_req = await open_request(
            session, org_id, source_ref=shared_ref, requester_employee_id=requester
        )
        assert leave_req is not None
        assert reg_req is not None
        assert leave_req.id != reg_req.id  # two distinct rows despite the shared source_ref

        async with session.begin():
            applied = await services.apply_leave_outcome(
                session,
                organization_id=org_id,
                source_ref=shared_ref,
                outcome="applied",
                failure_reason=None,
            )
        # Still pending — `apply_leave_outcome` returns the request unchanged rather than `None`
        # (`None` means "no request exists yet at all", a different no-op case).
        assert applied is not None
        assert applied.application_state is None

        session.expire_all()
        async with session.begin():
            reg_row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.regularization", shared_ref
            )
        assert reg_row is not None
        assert reg_row.application_state is None  # untouched by the leave-typed call

    async def test_applying_a_regularization_outcome_never_touches_a_leave_with_the_same_ref(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        shared_ref = uuid.uuid4()
        leave_req = await open_leave(
            session, org_id, source_ref=shared_ref, requester_employee_id=requester
        )
        reg_req = await open_request(
            session, org_id, source_ref=shared_ref, requester_employee_id=requester
        )
        assert leave_req is not None
        assert reg_req is not None

        async with session.begin():
            await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=shared_ref,
                outcome="applied",
                failure_reason=None,
            )

        session.expire_all()
        async with session.begin():
            leave_row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.leave", shared_ref
            )
        assert leave_row is not None
        assert leave_row.application_state is None  # untouched by the regularization-typed call

    async def test_withdrawing_a_leave_request_never_touches_a_regularization_with_the_same_ref(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        shared_ref = uuid.uuid4()
        await open_leave(session, org_id, source_ref=shared_ref, requester_employee_id=requester)
        await open_request(session, org_id, source_ref=shared_ref, requester_employee_id=requester)

        async with session.begin():
            await services.withdraw_request(
                session,
                organization_id=org_id,
                request_type="hrms.time.leave",
                source_ref=shared_ref,
            )

        session.expire_all()
        async with session.begin():
            reg_step = await _open_step_for(session, org_id, "hrms.time.regularization", shared_ref)
        assert reg_step is not None, "the regularization's own open step must survive untouched"


class TestApplyLeaveOutcomeThroughTheRealDecision:
    async def test_a_real_approval_reports_applied_through_the_consumer(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_leave(session, org_id, requester_employee_id=requester)
        assert request is not None
        request_id, source_ref = request.id, request.source_ref

        decided = await _decide(session, org_id, request_id=request_id, caller_id=manager)
        assert decided.status == "approved"

        envelope = leave_applied_envelope(
            organization_id=org_id,
            leave_request_id=source_ref,
            approval_request_id=request_id,
            outcome="applied",
        )
        await on_leave_applied(envelope)

        session.expire_all()
        async with session.begin():
            row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.leave", source_ref
            )
        assert row is not None
        assert row.application_state == "applied"


class TestOnLeaveRequestedConsumer:
    async def test_redelivery_of_the_same_event_id_opens_once(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        event_id = uuid.uuid4()
        leave_request_id = uuid.uuid4()
        employee_id = uuid.uuid4()
        envelope = leave_requested_envelope(
            event_id=event_id,
            organization_id=org_id,
            leave_request_id=leave_request_id,
            employee_id=employee_id,
        )
        await on_leave_requested(envelope)
        await on_leave_requested(envelope)  # redelivered, same event id

        async with session.begin():
            rows = (
                await session.execute(
                    select(ApprovalRequest.id).where(
                        ApprovalRequest.organization_id == org_id,
                        ApprovalRequest.source_ref == leave_request_id,
                    )
                )
            ).all()
        assert len(rows) == 1

    async def test_a_tenant_mismatch_is_dropped_and_never_marked_processed(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        event_id = uuid.uuid4()
        leave_request_id = uuid.uuid4()
        envelope = leave_requested_envelope(
            event_id=event_id,
            organization_id=org_id,
            leave_request_id=leave_request_id,
            employee_id=uuid.uuid4(),
            payload_organization_id=uuid.uuid4(),
        )
        await on_leave_requested(envelope)  # must not raise

        async with session.begin():
            row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.leave", leave_request_id
            )
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert row is None
        assert processed is False


class TestOnLeaveCancelledConsumer:
    async def test_cancelling_a_pending_request_closes_its_open_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_leave(session, org_id, requester_employee_id=requester)
        assert request is not None
        source_ref = request.source_ref

        envelope = leave_cancelled_envelope(
            organization_id=org_id,
            leave_request_id=source_ref,
            employee_id=requester,
            was_status="pending",
        )
        await on_leave_cancelled(envelope)

        session.expire_all()
        async with session.begin():
            step = await _open_step_for(session, org_id, "hrms.time.leave", source_ref)
        assert step is None, "the open step must be closed once approval learns of the withdrawal"

    async def test_cancelling_an_already_decided_request_is_a_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_leave(session, org_id, requester_employee_id=requester)
        assert request is not None
        request_id, source_ref = request.id, request.source_ref
        decided = await _decide(session, org_id, request_id=request_id, caller_id=manager)
        assert decided.status == "approved"

        envelope = leave_cancelled_envelope(
            organization_id=org_id,
            leave_request_id=source_ref,
            employee_id=requester,
            was_status="approved",
        )
        await on_leave_cancelled(envelope)  # must not raise, and must not touch the decision

        session.expire_all()
        async with session.begin():
            row = await selectors.get_request_by_source(
                session, org_id, "hrms.time.leave", source_ref
            )
        assert row is not None
        assert row.status == "approved"

    async def test_redelivery_of_the_same_cancellation_is_idempotent(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_leave(session, org_id, requester_employee_id=requester)
        assert request is not None
        source_ref = request.source_ref
        event_id = uuid.uuid4()
        envelope = leave_cancelled_envelope(
            event_id=event_id,
            organization_id=org_id,
            leave_request_id=source_ref,
            employee_id=requester,
        )
        await on_leave_cancelled(envelope)
        await on_leave_cancelled(envelope)  # redelivered, same event id — must not raise

        session.expire_all()
        async with session.begin():
            step = await _open_step_for(session, org_id, "hrms.time.leave", source_ref)
        assert step is None
