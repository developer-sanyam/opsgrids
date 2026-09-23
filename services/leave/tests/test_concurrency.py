"""Genuine concurrency, against real PostgreSQL and two independent connections — the same shape
`services/finance/tests/test_concurrency.py` and `services/operations/tests/test_stock.py` use,
for the identical reason: only a real server can arbitrate two transactions racing for the same
lock. The one shared fixture `session` cannot exercise a race against itself.

Three races, each closing a blocker found in review:

1. Two concurrent cancels of the same approved request must post exactly one reversal, never two
   (a row lock, backed by a partial unique index as the database's own backstop).
2. A cancel racing the approval-decision consumer's `.decided` application must never leave the
   request `approved` with a consumption and no reversal, and never post two reversals either —
   whichever gets the row lock first determines the interleaving, and both legal interleavings
   converge on the same final state: `cancelled`, balance fully restored.
3. Two different pending requests for the same employee and leave type, each individually
   affordable but not both together, must not both be approved — the advisory lock in
   `services._lock_balance` serialises the balance check the same way a row lock could not (they
   are two different `LeaveRequest` rows).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, LeaveLedgerEntry
from app.schemas import LeaveBalanceGrantIn, LeaveCancelIn, LeaveRequestIn, LeaveTypeIn
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import BusinessRuleProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — the race needs two real sessions, not
    the one shared fixture session everything else in this suite uses."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _make_leave_type(session: AsyncSession, org_id: uuid.UUID, code: str = "EL") -> uuid.UUID:
    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.leave_type.write", Scope.ORG)
    async with session.begin():
        leave_type = await services.create_leave_type(
            session, ctx, LeaveTypeIn(code=code, name="Earned leave")
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


async def _apply(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID,
    *,
    start: date,
    end: date,
    reason: str,
) -> uuid.UUID:
    ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
    async with session.begin():
        req = await services.apply_for_leave(
            session,
            ctx,
            LeaveRequestIn(
                leave_type_id=leave_type_id, start_date=start, end_date=end, reason=reason
            ),
        )
        req_id = req.id
    return req_id


async def _approve(session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID) -> str:
    """Mirrors `consumers.on_approval_request_decided`'s own guard, not just its call to
    `apply_leave_decision`: the consumer takes the row lock via `get_leave_request_by_source`
    (which delegates to `get_leave_request_for_update`) and then only applies the decision if
    the row is still `pending` once that lock is held — a cancel that got there first, and has
    already committed `cancelled`, is left alone rather than forced back to `approved`.
    `apply_leave_decision` itself trusts its caller for that check (its own docstring says so),
    the same contract `attendance.apply_regularization_decision` documents; a helper that skipped
    the guard would not be exercising the real race, it would just be finding a way to call the
    function that production code never calls that way."""
    async with session.begin():
        req = await selectors.get_leave_request_by_source(session, org_id, request_id)
        assert req is not None
        if req.status != "pending":
            return req.status
        req = await services.apply_leave_decision(
            session,
            req,
            outcome="approved",
            approval_request_id=uuid.uuid4(),
            decided_at=datetime.now(UTC),
        )
        return req.status


async def _cancel(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID, request_id: uuid.UUID
) -> str:
    ctx = ctx_for(org_id, employee_id, "hrms.time.leave_request.write", Scope.OWN)
    async with session.begin():
        req = await selectors.get_leave_request_for_update(session, org_id, request_id)
        assert req is not None
        updated = await services.cancel_leave_request(session, ctx, req, LeaveCancelIn())
        return updated.status


async def _ledger_counts(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, int]:
    stmt = (
        select(LeaveLedgerEntry.entry_type, func.count())
        .where(
            LeaveLedgerEntry.organization_id == org_id,
            LeaveLedgerEntry.leave_request_id == request_id,
        )
        .group_by(LeaveLedgerEntry.entry_type)
    )
    rows = (await session.execute(stmt)).all()
    return dict(rows)


class TestDoubleCancel:
    async def test_two_concurrent_cancels_of_an_approved_request_post_exactly_one_reversal(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        leave_type_id = await _make_leave_type(session, org_id)
        employee_id = uuid.uuid4()
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        req_id = await _apply(
            session,
            org_id,
            employee_id,
            leave_type_id,
            start=date(2027, 3, 1),
            end=date(2027, 3, 2),
            reason="double cancel race",
        )
        assert await _approve(session, org_id, req_id) == "approved"

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _cancel(s1, org_id, employee_id, req_id),
                    _cancel(s2, org_id, employee_id, req_id),
                    return_exceptions=True,
                ),
                timeout=15,
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        successes = [r for r in results if r == "cancelled"]
        failures = [r for r in results if isinstance(r, BusinessRuleProblem)]
        assert len(successes) == 1, results
        assert len(failures) == 1, results

        async with session.begin():
            counts = await _ledger_counts(session, org_id, req_id)
            balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert counts.get("reversal", 0) == 1, counts
        assert counts.get("consumption", 0) == 1, counts
        assert balance == 10  # fully restored — the grant, minus one consumption, plus one reversal


class TestCancelRacingDecision:
    async def test_cancel_racing_the_decision_consumer_converges_on_cancelled_with_balance_restored(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        leave_type_id = await _make_leave_type(session, org_id)
        employee_id = uuid.uuid4()
        await _grant(session, org_id, employee_id, leave_type_id, 10)
        req_id = await _apply(
            session,
            org_id,
            employee_id,
            leave_type_id,
            start=date(2027, 3, 1),
            end=date(2027, 3, 2),
            reason="cancel vs decide race",
        )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _approve(s1, org_id, req_id),
                    _cancel(s2, org_id, employee_id, req_id),
                    return_exceptions=True,
                ),
                timeout=15,
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for r in results:
            if isinstance(r, BaseException):
                raise r

        async with session.begin():
            req = await selectors.get_leave_request(session, org_id, req_id)
            assert req is not None
            counts = await _ledger_counts(session, org_id, req_id)
            balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)

        # Cancel always wins the "final say": it accepts either `pending` or `approved` as a
        # starting point, so whichever order the two transactions actually ran in, the request
        # ends cancelled — either with no ledger entries at all (cancel got the lock first, while
        # still pending) or with exactly one consumption and one reversal (decide got the lock
        # first). Both are legal, and both restore the full balance.
        assert req.status == "cancelled"
        assert counts.get("consumption", 0) == counts.get("reversal", 0)
        assert counts.get("consumption", 0) in (0, 1)
        assert balance == 10


class TestConcurrentDecisionsOverdraw:
    async def test_two_requests_that_each_fit_alone_do_not_both_get_approved(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """10 half-days banked; two pending requests for 6 half-days each individually fit, but
        approving both would leave the balance at -2. Exactly one may be approved."""
        leave_type_id = await _make_leave_type(session, org_id)
        employee_id = uuid.uuid4()
        await _grant(session, org_id, employee_id, leave_type_id, 10)

        # Two disjoint date ranges so the overlap-exclusion constraint does not itself refuse the
        # second application — this test is about the balance race, not the calendar one.
        req_a = await _apply(
            session,
            org_id,
            employee_id,
            leave_type_id,
            start=date(2027, 3, 1),
            end=date(2027, 3, 3),
            reason="first, 6 half-days",
        )
        req_b = await _apply(
            session,
            org_id,
            employee_id,
            leave_type_id,
            start=date(2027, 3, 8),
            end=date(2027, 3, 10),
            reason="second, 6 half-days",
        )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _approve(s1, org_id, req_a),
                    _approve(s2, org_id, req_b),
                    return_exceptions=True,
                ),
                timeout=15,
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for r in results:
            if isinstance(r, BaseException):
                raise r

        approved = [r for r in results if r == "approved"]
        failed = [r for r in results if r == "failed"]
        assert len(approved) == 1, results
        assert len(failed) == 1, results

        async with session.begin():
            balance = await selectors.balance_half_days(session, org_id, employee_id, leave_type_id)
        assert balance == 4  # 10 granted, 6 consumed by the one approved request
        assert balance >= 0
