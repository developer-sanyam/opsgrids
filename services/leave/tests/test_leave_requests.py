"""Leave requests, balances and settings — the business rules asserted directly against
`services.py`, the same two-halves shape `attendance/tests/test_regularization.py` uses:
object-level checks belong to `test_authz.py`, business-rule checks belong here.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import LeaveLedgerEntry, OutboxEvent
from app.schemas import (
    LeaveBalanceGrantIn,
    LeaveCancelIn,
    LeaveRequestIn,
    LeaveSettingsIn,
    LeaveTypeIn,
)
from platform_core.auth import Scope
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


async def _make_leave_type(
    session: AsyncSession, org_id: uuid.UUID, *, code: str = "EL", is_paid: bool = True
) -> uuid.UUID:
    """Returns a plain UUID, never the ORM row — a test that later calls `session.expire_all()`
    (to observe the consumer's own, separately-connected write) would otherwise trigger an
    implicit reload on a cached attribute access outside the async greenlet context and raise
    `MissingGreenlet`, the same trap `attendance/tests/test_consumers.py`'s own docstring notes."""
    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_type.write", Scope.ORG)
    async with session.begin():
        leave_type = await services.create_leave_type(
            session, ctx, LeaveTypeIn(code=code, name="Earned leave", is_paid=is_paid)
        )
        leave_type_id = leave_type.id
    return leave_type_id


async def _grant(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID,
    half_days: int,
) -> None:
    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
    async with session.begin():
        await services.grant_leave_balance(
            session,
            ctx,
            LeaveBalanceGrantIn(
                employee_id=employee_id, leave_type_id=leave_type_id, half_days=half_days
            ),
        )


class TestApplyForLeave:
    async def test_applying_opens_a_pending_request_and_publishes_the_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)

        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="family function",
                ),
            )

        assert req.status == "pending"
        assert req.total_half_days == 4
        event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "hrms.time.leave.requested")
            )
        ).scalar_one()
        assert event.payload["employee_id"] == str(employee_id)
        assert event.payload["total_half_days"] == 4

    async def test_an_overlapping_pending_request_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 20)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 23),
                    reason="first request",
                ),
            )

        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.apply_for_leave(
                    session,
                    ctx,
                    LeaveRequestIn(
                        leave_type_id=leave_type_id,
                        start_date=dt.date(2026, 9, 22),
                        end_date=dt.date(2026, 9, 24),
                        reason="overlapping request",
                    ),
                )

    async def test_a_non_overlapping_request_after_the_first_is_fine(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 20)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 21),
                    reason="first",
                ),
            )
        async with session.begin():
            second = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 22),
                    end_date=dt.date(2026, 9, 22),
                    reason="second, the next day",
                ),
            )
        assert second.status == "pending"

    async def test_insufficient_balance_on_a_paid_type_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id, is_paid=True)
        await _grant(session, org_id, employee_id, leave_type_id, 2)  # one day only
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        with pytest.raises(BusinessRuleProblem, match="Insufficient balance"):
            async with session.begin():
                await services.apply_for_leave(
                    session,
                    ctx,
                    LeaveRequestIn(
                        leave_type_id=leave_type_id,
                        start_date=dt.date(2026, 9, 21),
                        end_date=dt.date(2026, 9, 23),
                        reason="three days, only one banked",
                    ),
                )

    async def test_an_unpaid_type_never_checks_balance(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id, code="LOP", is_paid=False)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 23),
                    reason="loss of pay, no balance needed",
                ),
            )
        assert req.status == "pending"

    async def test_a_pure_weekend_range_is_a_business_rule_refusal_not_a_500(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        with pytest.raises(BusinessRuleProblem, match="no working days"):
            async with session.begin():
                await services.apply_for_leave(
                    session,
                    ctx,
                    LeaveRequestIn(
                        leave_type_id=leave_type_id,
                        start_date=dt.date(2026, 9, 19),
                        end_date=dt.date(2026, 9, 20),
                        reason="a weekend",
                    ),
                )

    async def test_a_caller_with_no_subject_ref_cannot_apply(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        leave_type_id = await _make_leave_type(session, org_id)
        ctx = ctx_for(org_id, None, "hrms.time.leave_request.write", Scope.OWN)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.apply_for_leave(
                    session,
                    ctx,
                    LeaveRequestIn(
                        leave_type_id=leave_type_id,
                        start_date=dt.date(2026, 9, 21),
                        end_date=dt.date(2026, 9, 21),
                        reason="no subject",
                    ),
                )

    async def test_applying_against_an_unknown_leave_type_is_a_404(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_request.write", Scope.OWN)
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.apply_for_leave(
                    session,
                    ctx,
                    LeaveRequestIn(
                        leave_type_id=uuid.uuid4(),
                        start_date=dt.date(2026, 9, 21),
                        end_date=dt.date(2026, 9, 21),
                        reason="no such type",
                    ),
                )


class TestApprovalRoundTrip:
    async def test_approval_posts_a_consumption_entry_and_publishes_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="approved leave",
                ),
            )
        req_id = req.id

        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="approved",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
                decided_by_name="Priya HR",
            )

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "approved"

        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 6  # 10 granted - 4 consumed

        consumption = (
            await session.execute(
                select(LeaveLedgerEntry).where(
                    LeaveLedgerEntry.leave_request_id == req_id,
                    LeaveLedgerEntry.entry_type == "consumption",
                )
            )
        ).scalar_one()
        assert consumption.delta_half_days == -4

        applied_event = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "hrms.time.leave.applied",
                    OutboxEvent.aggregate_id == req_id,
                )
            )
        ).scalar_one()
        assert applied_event.payload["outcome"] == "applied"

    async def test_rejection_posts_nothing_to_the_ledger(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 21),
                    reason="will be rejected",
                ),
            )
        req_id = req.id

        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="rejected",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
                comment="not enough notice",
            )

        session.expire_all()
        req = await selectors.get_leave_request(session, org_id, req_id)
        assert req is not None
        assert req.status == "rejected"
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 10

    async def test_a_balance_that_dropped_below_the_request_since_it_opened_fails_cleanly(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The transition that was legal at request time may no longer be — the same
        `attendance.services.apply_regularization_decision` "record failed, never raise" shape."""
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 4)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="uses the whole balance",
                ),
            )
        req_id = req.id

        # HR adjusts the balance downward (an over-grant correction) while the request is
        # in flight — a plain negative adjustment, the append-only correction CLAUDE.md asks for.
        async with session.begin():
            entry = LeaveLedgerEntry(
                organization_id=org_id,
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                entry_type="adjustment",
                delta_half_days=-4,
            )
            session.add(entry)

        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            req = await services.apply_leave_decision(
                session,
                req,
                outcome="approved",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
            )
        assert req.status == "failed"
        assert req.failure_reason is not None


class TestCancelLeaveRequest:
    async def test_cancelling_a_pending_request_posts_nothing_to_the_ledger(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="changed my mind",
                ),
            )

        async with session.begin():
            cancelled = await services.cancel_leave_request(
                session, ctx, req, LeaveCancelIn(reason="plans changed")
            )
        assert cancelled.status == "cancelled"
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 10

    async def test_cancelling_an_approved_request_posts_a_reversal(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="approved then withdrawn",
                ),
            )
        req_id = req.id

        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="approved",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
            )

        session.expire_all()
        async with session.begin():
            balance_after_approval = await selectors.balance_half_days(
                session, org_id, employee_id, leave_type_id
            )
        assert balance_after_approval == 6

        # A "today" well before the leave's dates — cancellation is legal.
        pinned_now = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            cancelled = await services.cancel_leave_request(
                session, ctx, req, LeaveCancelIn(reason="no longer needed"), now=pinned_now
            )
        assert cancelled.status == "cancelled"

        balance_after_cancel = await selectors.balance_half_days(
            session, org_id, employee_id, leave_type_id
        )
        assert balance_after_cancel == 10  # the reversal restored the full grant

        reversal = (
            await session.execute(
                select(LeaveLedgerEntry).where(
                    LeaveLedgerEntry.leave_request_id == req_id,
                    LeaveLedgerEntry.entry_type == "reversal",
                )
            )
        ).scalar_one()
        assert reversal.delta_half_days == 4

    async def test_a_leave_already_ended_cannot_be_withdrawn(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Timezone-correct "today": resolved from `LeaveSettings`, not a bare UTC date — see
        `TestTimezoneCorrectCancellation` below for the boundary case this guards."""
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 22),
                    reason="long past",
                ),
            )
        req_id = req.id
        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="approved",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
            )

        # Well after the leave ended, in UTC.
        pinned_now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
        with pytest.raises(BusinessRuleProblem, match="already ended"):
            async with session.begin():
                req = await selectors.get_leave_request(session, org_id, req_id)
                assert req is not None
                await services.cancel_leave_request(
                    session, ctx, req, LeaveCancelIn(), now=pinned_now
                )

    async def test_a_rejected_request_cannot_be_cancelled(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 21),
                    end_date=dt.date(2026, 9, 21),
                    reason="will be rejected",
                ),
            )
        req_id = req.id
        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="rejected",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
            )

        with pytest.raises(BusinessRuleProblem, match="rejected"):
            async with session.begin():
                req = await selectors.get_leave_request(session, org_id, req_id)
                assert req is not None
                await services.cancel_leave_request(session, ctx, req, LeaveCancelIn())


class TestTimezoneCorrectCancellation:
    """The exact boundary `domain.today_in` exists for — an organization whose timezone is not
    UTC must not have "today" answered from a bare `datetime.now(UTC).date()` (`app/models.py`'s
    module docstring on the defect this rebuild does not repeat)."""

    async def test_a_utc_naive_today_would_wrongly_allow_this_cancellation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)

        # The organization is in Kolkata (UTC+5:30, the service default).
        settings_ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_type.write", Scope.ORG)
        async with session.begin():
            await services.update_leave_settings(
                session, settings_ctx, LeaveSettingsIn(timezone="Asia/Kolkata")
            )

        async with session.begin():
            req = await services.apply_for_leave(
                session,
                ctx,
                LeaveRequestIn(
                    leave_type_id=leave_type_id,
                    start_date=dt.date(2026, 9, 22),
                    end_date=dt.date(2026, 9, 22),
                    reason="ends today, in Kolkata",
                ),
            )
        req_id = req.id
        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            await services.apply_leave_decision(
                session,
                req,
                outcome="approved",
                approval_request_id=uuid.uuid4(),
                decided_at=dt.datetime.now(dt.UTC),
            )

        # 19:00 UTC on the 22nd is already 00:30 IST on the 23rd — a UTC-naive "today" (the
        # 22nd) would still think the leave's last day has not passed and allow the withdrawal;
        # `today_in` resolved in Kolkata's own timezone correctly says the 23rd, one day past
        # `end_date`, and refuses it.
        pinned_now = dt.datetime(2026, 9, 22, 19, 0, tzinfo=dt.UTC)
        # the naive answer this test guards against:
        assert pinned_now.date() == dt.date(2026, 9, 22)

        with pytest.raises(BusinessRuleProblem, match="already ended"):
            async with session.begin():
                req = await selectors.get_leave_request(session, org_id, req_id)
                assert req is not None
                await services.cancel_leave_request(
                    session, ctx, req, LeaveCancelIn(), now=pinned_now
                )


class TestGrantLeaveBalance:
    async def test_a_grant_is_idempotent_on_its_key(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        leave_type_id = await _make_leave_type(session, org_id)
        ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)

        async with session.begin():
            first = await services.grant_leave_balance(
                session,
                ctx,
                LeaveBalanceGrantIn(
                    employee_id=employee_id,
                    leave_type_id=leave_type_id,
                    half_days=24,
                    idempotency_key="annual-2026",
                ),
            )
        async with session.begin():
            second = await services.grant_leave_balance(
                session,
                ctx,
                LeaveBalanceGrantIn(
                    employee_id=employee_id,
                    leave_type_id=leave_type_id,
                    half_days=24,
                    idempotency_key="annual-2026",
                ),
            )

        assert first.id == second.id
        balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 24  # posted once, not twice


# --------------------------------------------------------------------- append-only at the DB


class TestLedgerIsAppendOnlyAtTheDatabase:
    """The net under append-only (CLAUDE.md): even a raw UPDATE or DELETE, bypassing every
    service-level check, is refused — the app role (`opsgrids_leave`) simply does not hold the
    privilege on `leave_ledger_entries` (`migrations/versions/e5b7c9d1a2f4...`). The same shape
    `metering/tests/test_ingest.py::test_update_on_usage_events_is_denied_to_the_app_role` uses
    for the identical guarantee on `usage_events`."""

    async def test_update_on_the_ledger_is_denied_to_the_app_role(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        leave_type_id = await _make_leave_type(session, org_id)
        ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
        async with session.begin():
            await services.grant_leave_balance(
                session,
                ctx,
                LeaveBalanceGrantIn(
                    employee_id=uuid.uuid4(), leave_type_id=leave_type_id, half_days=10
                ),
            )

        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE leave.leave_ledger_entries SET delta_half_days = 999 "
                        "WHERE organization_id = :org"
                    ),
                    {"org": org_id},
                )

    async def test_delete_on_the_ledger_is_denied_to_the_app_role(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        leave_type_id = await _make_leave_type(session, org_id)
        ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_balance.write", Scope.ORG)
        async with session.begin():
            await services.grant_leave_balance(
                session,
                ctx,
                LeaveBalanceGrantIn(
                    employee_id=uuid.uuid4(), leave_type_id=leave_type_id, half_days=10
                ),
            )

        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM leave.leave_ledger_entries WHERE organization_id = :org"),
                    {"org": org_id},
                )
