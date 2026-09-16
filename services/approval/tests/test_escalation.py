"""One escalation (ADR-0006 §8), exercised directly against `app/services.escalate_step` rather
than through the sweep loop (`tests/test_sweep.py` covers the loop's own discovery/fairness
behaviour): the deadline gate, the walk from the original approver past an exited manager,
delegation applied at the new rung, an exhausted chain landing unassigned, the original approver
still eligible to decide afterwards, and two genuinely concurrent escalators never both winning.

**Every `due_at` seeded here is anchored to the database's own clock** (`app.services.db_now`),
never to `datetime.now(UTC)` — see `tests/test_sweep.py`'s module docstring for why: this test
process and the database server are not guaranteed to agree on the time to the second, and
`escalate_step`'s own re-check always reads the database's clock, so a test seeding off a
different one is testing an assumption production code deliberately never makes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import ApprovalStep, AuditLog, Delegation, OutboxEvent
from app.services import db_now
from platform_core.db import TENANT_GUC
from tests.helpers import seed_pending_request_with_step, seed_person

pytestmark = pytest.mark.integration


async def _escalate(session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID) -> object:
    async with session.begin():
        return await services.escalate_step(session, organization_id=org_id, request_id=request_id)


async def _open_step(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalStep:
    stmt = select(ApprovalStep).where(
        ApprovalStep.organization_id == org_id,
        ApprovalStep.request_id == request_id,
        ApprovalStep.outcome == "open",
    )
    return (await session.execute(stmt)).scalar_one()


class TestDeadlineGated:
    async def test_a_step_not_yet_due_does_not_escalate(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now,
                due_at=now + timedelta(minutes=15),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is False

        async with session.begin():
            still_open = await _open_step(session, org_id, request.id)
        assert still_open.id == step.id
        assert still_open.outcome == "open"

    async def test_an_overdue_step_escalates(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director, display_name="Director")
            await seed_person(
                session, org_id, manager, reporting_manager_id=director, display_name="Manager"
            )
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True
        assert result.unassigned is False

        async with session.begin():
            closed = await session.get(ApprovalStep, step.id)
            await session.refresh(closed)
            new_open = await _open_step(session, org_id, request.id)
        assert closed.outcome == "escalated"
        assert closed.closed_at is not None
        assert new_open.level == step.level + 1
        assert new_open.approver_employee_id == director
        assert new_open.reason == "escalated"
        assert new_open.due_at is not None
        assert new_open.due_at > closed.due_at  # the SLA clock restarts (§8), unlike re-pointing


class TestOriginalApproverStillEligible:
    async def test_the_original_approver_can_still_decide_after_escalation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """§6's consequence: "the original approver stays eligible ... and after escalation (a
        on the escalated step)" — the manager whose step just escalated can still act on the
        request directly, even though the *open* step now belongs to the director."""
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director)
            await seed_person(session, org_id, manager, reporting_manager_id=director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True

        async with session.begin():
            decided = await services.decide_request(
                session,
                organization_id=org_id,
                request_id=request.id,
                caller_id=manager,
                has_org_decide=False,
                outcome="approve",
                comment=None,
            )
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == manager


class TestEscalationAppliesDelegation:
    async def test_the_next_rung_active_delegation_is_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director, delegate = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, manager, reporting_manager_id=director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=director,
                    delegate_employee_id=delegate,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=director,
                )
            )
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True

        async with session.begin():
            new_open = await _open_step(session, org_id, request.id)
        assert new_open.approver_employee_id == delegate
        assert new_open.acting_for_employee_id == director
        assert new_open.delegation_id is not None
        assert new_open.reason == "delegated"

    async def test_the_walk_skips_an_exited_manager_before_applying_delegation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, exited_director, vp = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, vp, display_name="VP")
            await seed_person(
                session,
                org_id,
                exited_director,
                reporting_manager_id=vp,
                status="exited",
                display_name="Exited Director",
            )
            await seed_person(session, org_id, manager, reporting_manager_id=exited_director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True

        async with session.begin():
            new_open = await _open_step(session, org_id, request.id)
        assert new_open.approver_employee_id == vp


class TestEscalationNeverHandsBackToWhoJustMissedIt:
    """Slice-4 review finding 5: `_resolve_step_assignment` used to exclude only the requester,
    so if the next rung's chosen candidate had an active delegation pointing at the person who
    just failed to answer — the previous approver, or (when that step was itself delegated) the
    original one it was opened for — the successor would open right back against them, the exact
    thing `escalate_step`'s own docstring promises never happens: "escalation only ever widens who
    may answer it". `_resolve_step_assignment` now takes `skip_ids` and falls back to the plain,
    undelegated candidate when the delegate it would otherwise resolve to is in that set."""

    async def test_a_delegation_back_to_the_previous_approver_is_not_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director, display_name="Director Dee")
            await seed_person(session, org_id, manager, reporting_manager_id=director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            # The next rung (`director`) has delegated to `manager` — the very person whose step
            # is escalating away from them for missing the deadline.
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=director,
                    delegate_employee_id=manager,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=director,
                )
            )
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True

        async with session.begin():
            new_open = await _open_step(session, org_id, request.id)
        # Without the fix, this would be `manager` (the delegate) with `acting_for = director` —
        # the step handed straight back to the person who just missed it.
        assert new_open.approver_employee_id == director
        assert new_open.acting_for_employee_id is None
        assert new_open.delegation_id is None
        assert new_open.reason == "escalated"

    async def test_a_delegation_back_to_the_original_approver_of_a_delegated_step_is_not_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The step that is escalating away was itself opened through a delegation (`approver` is
        the delegate who missed the deadline, `acting_for` is the original approver) — the next
        rung must not be allowed to hand it back to *either* of them."""
        requester, original, delegate, director = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director, display_name="Director Dee")
            await seed_person(session, org_id, original, reporting_manager_id=director)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=original)
            # `director` has delegated to `original` — the represented approver of the step that
            # is escalating away, not the delegate who actually missed it.
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=director,
                    delegate_employee_id=original,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=director,
                )
            )
            # The step that is escalating away needs its own, real delegation row —
            # `ck_approval_steps_delegated_iff_delegation` requires one whenever `reason =
            # 'delegated'`, and `trg_approval_steps_append_only` refuses an `UPDATE` that would set
            # `delegation_id` on an already-inserted open step, so this must exist *before* the
            # step is created, not be patched on afterwards.
            original_delegation = Delegation(
                organization_id=org_id,
                delegator_employee_id=original,
                delegate_employee_id=delegate,
                starts_at=now - timedelta(hours=2),
                ends_at=now + timedelta(days=1),
                created_by_employee_id=original,
            )
            session.add(original_delegation)
            await session.flush()
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=delegate,
                acting_for_employee_id=original,
                delegation_id=original_delegation.id,
                reason="delegated",
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True

        async with session.begin():
            new_open = await _open_step(session, org_id, request.id)
        # Without the fix, this would be `original` (the delegate at the new rung) with
        # `acting_for = director` — handing the step back to the very approver it had just been
        # opened on behalf of, and who never personally answered it either.
        assert new_open.approver_employee_id == director
        assert new_open.acting_for_employee_id is None
        assert new_open.delegation_id is None
        assert new_open.reason == "escalated"


class TestExhaustedChainLandsUnassigned:
    async def test_a_chain_with_nobody_left_above_opens_unassigned(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, manager, reporting_manager_id=None)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is True
        assert result.unassigned is True

        async with session.begin():
            new_open = await _open_step(session, org_id, request.id)
        assert new_open.approver_employee_id is None
        assert new_open.due_at is None
        assert new_open.reason == "unassigned"

    async def test_an_unassigned_step_never_escalates_again(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`ck_approval_steps_unassigned_has_no_due` makes this true by construction — the sweep's
        own discovery query already excludes `due_at IS NULL`, and `escalate_step` re-checks it
        under the lock too, so even a hand-fed request id is refused."""
        requester = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, requester, reporting_manager_id=None)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=None,
                reason="unassigned",
                due_at=None,
            )

        result = await _escalate(session, org_id, request.id)
        assert result.escalated is False


class TestAuditAndOutbox:
    async def test_escalation_writes_the_audit_row_and_publishes_the_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director)
            await seed_person(session, org_id, manager, reporting_manager_id=director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        await _escalate(session, org_id, request.id)

        async with session.begin():
            audit = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "approval.step.escalated",
                        )
                    )
                )
                .scalars()
                .one()
            )
            event = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.escalated",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert audit.actor_id is None
        assert audit.acting_as == "approval.sla_sweep"
        assert audit.reason == "sla_elapsed"

        new_open = await _open_step(session, org_id, request.id)
        assert event.payload == {
            "organization_id": str(org_id),
            "request_id": str(request.id),
            "request_type": request.request_type,
            "source_ref": str(request.source_ref),
            "step_id": str(new_open.id),
            "level": new_open.level,
            "from_approver_employee_id": str(manager),
            "to_approver_employee_id": str(director),
            "acting_for_employee_id": None,
            "due_at": new_open.due_at.isoformat(),
        }


async def _tenant_session(org_id: uuid.UUID) -> tuple[object, AsyncSession]:
    """A second, independent connection bound to the same organization — real network I/O, so two
    of these genuinely race for the request row's lock (the same shape
    `tests/test_decisions.py`'s own `_tenant_session` uses)."""
    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": "approval,public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    new_session = maker()

    @sa_event.listens_for(new_session.sync_session, "after_begin")
    def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    return engine, new_session


class TestConcurrentEscalationIsIdempotent:
    async def test_two_concurrent_escalators_only_one_escalates(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director)
            await seed_person(session, org_id, manager, reporting_manager_id=director)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        engine_a, session_a = await _tenant_session(org_id)
        engine_b, session_b = await _tenant_session(org_id)
        try:

            async def _run(s: AsyncSession) -> object:
                async with s.begin():
                    return await services.escalate_step(
                        s, organization_id=org_id, request_id=request.id
                    )

            results = await asyncio.gather(_run(session_a), _run(session_b))
        finally:
            await session_a.close()
            await session_b.close()
            await engine_a.dispose()
            await engine_b.dispose()

        # `escalated + skipped == 2` already fully accounts for both replicas — this is the whole
        # of "no double escalation, no missed one" for a single contended step; re-querying for
        # `outcome = 'open'` afterwards would only re-prove what
        # `uq_approval_steps_open_per_request` already guarantees can never exceed one row,
        # without covering anything this does not.
        escalated = [r for r in results if r.escalated]
        skipped = [r for r in results if not r.escalated]
        assert len(escalated) == 1, "two replicas both escalated the same step"
        assert len(skipped) == 1
