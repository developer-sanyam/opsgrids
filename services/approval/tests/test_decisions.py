"""Deciding a request (ADR-0006 §6, §7): every eligibility route, the requester refused (even as
an ORG holder), an exited caller refused through any route, a `decide:ORG` holder refused on an
assigned step and allowed on an unassigned or abandoned one, first-decision-wins under two
genuinely concurrent decisions, and the write-once application trio (§12).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import ApprovalRequest, AuditLog, Delegation, OutboxEvent
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem, ForbiddenProblem
from tests.helpers import open_request, seed_person

pytestmark = pytest.mark.integration


async def _decide(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    caller_id: uuid.UUID | None,
    has_org_decide: bool = False,
    outcome: str = "approve",
    comment: str | None = None,
    caller_user_id: uuid.UUID | None = None,
) -> ApprovalRequest:
    async with session.begin():
        return await services.decide_request(
            session,
            organization_id=org_id,
            request_id=request_id,
            caller_id=caller_id,
            has_org_decide=has_org_decide,
            outcome=outcome,
            comment=comment,
            caller_user_id=caller_user_id,
        )


class TestRouteAssigned:
    async def test_the_direct_approver_may_decide(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        decided = await _decide(session, org_id, request_id=request.id, caller_id=manager)
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == manager
        assert decided.acting_for_employee_id is None


class TestRouteDelegated:
    async def test_the_active_delegate_may_decide_and_acting_for_is_recorded(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=delegate,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=manager,
                )
            )
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        decided = await _decide(session, org_id, request_id=request.id, caller_id=delegate)
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == delegate
        assert decided.acting_for_employee_id == manager

    async def test_a_revoked_delegation_refuses_the_delegate(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §5: "Revoke the delegation and the delegate loses the power to decide
        immediately." The delegation is still recorded on the step (routing never changes), but
        eligibility is re-checked live, under the lock."""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            delegation = Delegation(
                organization_id=org_id,
                delegator_employee_id=manager,
                delegate_employee_id=delegate,
                starts_at=now - timedelta(hours=1),
                ends_at=now + timedelta(days=1),
                created_by_employee_id=manager,
            )
            session.add(delegation)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            await session.refresh(delegation)
            delegation.revoked_at = now
            delegation.revoked_by_employee_id = manager

        with pytest.raises(ForbiddenProblem):
            await _decide(session, org_id, request_id=request.id, caller_id=delegate)

    async def test_the_original_approver_stays_eligible_after_delegation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Route (b): the original approver decides in their own right, `acting_for` on the
        decided request stays NULL (ADR-0006 §7: NULL "when they decided in their own right...
        as the represented original")."""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=delegate,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=manager,
                )
            )
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        decided = await _decide(session, org_id, request_id=request.id, caller_id=manager)
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == manager
        assert decided.acting_for_employee_id is None


class TestRouteOrgUnassignedAndAbandoned:
    async def test_an_org_holder_may_decide_an_unassigned_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, hr = uuid.uuid4(), uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None  # no hierarchy row at all -> unassigned

        decided = await _decide(
            session, org_id, request_id=request.id, caller_id=hr, has_org_decide=True
        )
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == hr

    async def test_an_own_holder_cannot_decide_an_unassigned_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, someone = uuid.uuid4(), uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        with pytest.raises(ForbiddenProblem):
            await _decide(
                session, org_id, request_id=request.id, caller_id=someone, has_org_decide=False
            )

    async def test_an_org_holder_may_decide_a_step_whose_approver_has_exited(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_manager, hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, exited_manager)
            await seed_person(session, org_id, requester, reporting_manager_id=exited_manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        # The manager exits *after* the step opened (rule 6: hierarchy changes never re-route).
        async with session.begin():
            await seed_person(session, org_id, exited_manager, status="exited", source_version=2)

        hr_user = uuid.uuid4()
        decided = await _decide(
            session,
            org_id,
            request_id=request.id,
            caller_id=hr,
            has_org_decide=True,
            caller_user_id=hr_user,
        )
        assert decided.status == "approved"

        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.decided",
                        )
                    )
                )
                .scalars()
                .one()
            )
        # The audit actor is the deciding user (the dashboard resolves user ids to names).
        assert row.actor_id == hr_user and decided.decided_by_employee_id == hr

    async def test_an_org_holder_cannot_decide_an_assigned_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §6: "decide:ORG does not let HR decide an assigned request... It covers
        unassigned steps and nothing else." """
        requester, manager, hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        with pytest.raises(ForbiddenProblem):
            await _decide(session, org_id, request_id=request.id, caller_id=hr, has_org_decide=True)

    async def test_an_exited_approver_cannot_decide_their_own_step_even_via_org(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The exited approver themselves, even holding `decide:ORG`, cannot use the ORG route to
        decide the very step their own exit made abandoned (ADR-0006 §9's negative-authz case)."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            await seed_person(session, org_id, manager, status="exited", source_version=2)

        with pytest.raises(ForbiddenProblem):
            await _decide(
                session, org_id, request_id=request.id, caller_id=manager, has_org_decide=True
            )


class TestRequesterNeverEligible:
    async def test_the_requester_cannot_decide_their_own_assigned_request(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        with pytest.raises(ForbiddenProblem):
            await _decide(session, org_id, request_id=request.id, caller_id=requester)

    async def test_the_requester_cannot_decide_their_own_unassigned_request_even_as_org(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Rule 3 overrides every other route, including `decide:ORG` (ADR-0006 §6)."""
        requester = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        with pytest.raises(ForbiddenProblem):
            await _decide(
                session,
                org_id,
                request_id=request.id,
                caller_id=requester,
                has_org_decide=True,
            )


class TestOwnerSelfApproval:
    """The audited fallback: a lone owner whose own request has no approver may confirm it."""

    async def _own_unassigned(self, session: AsyncSession, org_id: uuid.UUID):  # type: ignore[no-untyped-def]
        owner = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=owner)
        assert request is not None
        return owner, request

    async def test_the_owner_may_approve_their_own_unassigned_request_when_they_confirm(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner, request = await self._own_unassigned(session, org_id)
        user = uuid.uuid4()
        async with session.begin():
            decided = await services.decide_request(
                session,
                organization_id=org_id,
                request_id=request.id,
                caller_id=owner,
                has_org_decide=True,
                outcome="approve",
                comment=None,
                is_owner=True,
                self_approve=True,
                caller_user_id=user,
            )
        assert decided.status == "approved" and decided.self_approved is True
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "core.approval.request.decided"
                    )
                )
            ).scalar_one()
        assert event.payload["decided_by_user_id"] == str(user)
        assert event.payload["self_approved"] is True
        async with session.begin():
            audit = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "approval.request.self_approved")
                    )
                )
                .scalars()
                .all()
            )
        assert len(audit) == 1 and audit[0].actor_id == user

    async def test_without_the_confirmation_the_refusal_explains_what_to_do(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner, request = await self._own_unassigned(session, org_id)
        with pytest.raises(ForbiddenProblem, match="Settings") as exc:
            await _decide(
                session, org_id, request_id=request.id, caller_id=owner, has_org_decide=True
            )
        assert "no longer" not in exc.value.detail
        assert exc.value.errors and exc.value.errors[0]["code"] == "self_approval_blocked"

    async def test_a_non_owner_org_holder_cannot_self_approve(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        hr, request = await self._own_unassigned(session, org_id)
        with pytest.raises(ForbiddenProblem) as exc:
            async with session.begin():
                await services.decide_request(
                    session,
                    organization_id=org_id,
                    request_id=request.id,
                    caller_id=hr,
                    has_org_decide=True,
                    outcome="approve",
                    comment=None,
                    is_owner=False,
                    self_approve=True,
                )
        assert exc.value.errors and exc.value.errors[0]["code"] == "self_approval_blocked"

    async def test_the_owner_cannot_self_approve_an_assigned_request_or_reject_it(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, owner, reporting_manager_id=manager)
        assigned = await open_request(session, org_id, requester_employee_id=owner)
        assert assigned is not None
        _, unassigned = await self._own_unassigned(session, org_id)
        for request_id, caller, outcome in (
            (assigned.id, owner, "approve"),
            (unassigned.id, unassigned.requester_employee_id, "reject"),
        ):
            with pytest.raises(ForbiddenProblem) as exc:
                async with session.begin():
                    await services.decide_request(
                        session,
                        organization_id=org_id,
                        request_id=request_id,
                        caller_id=caller,
                        has_org_decide=True,
                        outcome=outcome,
                        comment=None,
                        is_owner=True,
                        self_approve=True,
                    )
            assert exc.value.errors and exc.value.errors[0]["code"] == "self_approval_blocked"


class TestNoSubjectRef:
    async def test_a_caller_with_no_employee_link_decides_nothing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        with pytest.raises(ForbiddenProblem):
            await _decide(
                session,
                org_id,
                request_id=request.id,
                caller_id=None,
                has_org_decide=True,
            )


class TestAlreadyDecided:
    async def test_a_second_decision_is_a_conflict(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        await _decide(session, org_id, request_id=request.id, caller_id=manager, outcome="approve")
        with pytest.raises(ConflictProblem, match="already"):
            await _decide(
                session, org_id, request_id=request.id, caller_id=manager, outcome="reject"
            )


class TestDecisionSideEffects:
    async def test_deciding_publishes_the_outbox_event_without_the_note_or_summary(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(
            session, org_id, requester_employee_id=requester, request_note="Personal reasons"
        )
        assert request is not None

        await _decide(
            session,
            org_id,
            request_id=request.id,
            caller_id=manager,
            outcome="reject",
            comment="Already accounted for",
        )

        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.decided",
                        )
                    )
                )
                .scalars()
                .one()
            )
            audit_row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.decided",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.payload["outcome"] == "rejected"
        assert row.payload["decided_by_employee_id"] == str(manager)
        # Additive: reuses `check_eligibility`'s own `caller_node` lookup — see
        # `Eligibility.decider_name` in `app/services.py` — so a known decider's name travels on
        # the event with no extra query.
        assert row.payload["decided_by_name"] == "Someone"  # seed_person's default display_name
        assert row.payload["comment"] == "Already accounted for"
        assert "request_note" not in row.payload
        assert "summary" not in row.payload
        # Audit before/after never carries the requester's free text or the decider's comment.
        assert "request_note" not in (audit_row.after or {})
        assert "comment" not in (audit_row.after or {})

    async def test_a_decider_outside_the_hierarchy_copy_publishes_a_null_name(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Route (c): a `decide:ORG` holder deciding an unassigned step need not be anyone's
        manager, and may legitimately have no `hierarchy_nodes` row at all — that is a `None`
        name on the wire, not a failure (mirrors `TestRouteOrgUnassignedAndAbandoned`'s own
        `hr` fixture, which is never seeded)."""
        requester, hr = uuid.uuid4(), uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        await _decide(session, org_id, request_id=request.id, caller_id=hr, has_org_decide=True)

        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.decided",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.payload["decided_by_employee_id"] == str(hr)
        assert row.payload["decided_by_name"] is None


async def _tenant_session(
    org_id: uuid.UUID,
) -> tuple[object, AsyncSession]:
    """A second, independent connection bound to the same organization — real network I/O, so
    two of these genuinely race for a Postgres row lock rather than merely interleaving Python
    bytecode (the same shape `tests/conftest.py`'s own `session` fixture uses)."""
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


class TestFirstDecisionWinsUnderConcurrency:
    async def test_two_concurrent_decisions_one_wins_one_conflicts(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        engine_a, session_a = await _tenant_session(org_id)
        engine_b, session_b = await _tenant_session(org_id)
        try:

            async def _approve() -> object:
                async with session_a.begin():
                    return await services.decide_request(
                        session_a,
                        organization_id=org_id,
                        request_id=request.id,
                        caller_id=manager,
                        has_org_decide=False,
                        outcome="approve",
                        comment=None,
                    )

            async def _reject() -> object:
                async with session_b.begin():
                    return await services.decide_request(
                        session_b,
                        organization_id=org_id,
                        request_id=request.id,
                        caller_id=manager,
                        has_org_decide=False,
                        outcome="reject",
                        comment=None,
                    )

            results = await asyncio.gather(_approve(), _reject(), return_exceptions=True)
        finally:
            await session_a.close()
            await session_b.close()
            await engine_a.dispose()
            await engine_b.dispose()

        successes = [r for r in results if not isinstance(r, Exception)]
        conflicts = [r for r in results if isinstance(r, ConflictProblem)]
        assert len(successes) == 1
        assert len(conflicts) == 1

        async with session.begin():
            row = await session.get(ApprovalRequest, request.id)
            await session.refresh(row)
        # First decision wins: `status`'s own CHECK constraint already guarantees it lands in the
        # closed vocabulary, so the property worth pinning here is that the persisted row agrees
        # with *the winning transaction's own outcome* — not merely that it is some legal value —
        # which is what would actually catch a lock that let both writers land.
        assert row.status == successes[0].status


class TestApplicationState:
    async def test_an_approval_is_recorded_as_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None
        await _decide(session, org_id, request_id=request.id, caller_id=manager)

        async with session.begin():
            reported = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="applied",
                failure_reason=None,
            )
        assert reported is not None
        assert reported.application_state == "applied"
        assert reported.applied_at is not None
        assert reported.application_failure_reason is None

    async def test_a_rejection_is_reported_too(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §12: "a rejection is applied too" — the row moved to `rejected`, which is the
        faithful application of that decision."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None
        await _decide(session, org_id, request_id=request.id, caller_id=manager, outcome="reject")

        async with session.begin():
            reported = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="applied",
                failure_reason=None,
            )
        assert reported is not None
        assert reported.status == "rejected"
        assert reported.application_state == "applied"

    async def test_a_failure_carries_its_reason(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None
        await _decide(session, org_id, request_id=request.id, caller_id=manager)

        async with session.begin():
            reported = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="failed",
                failure_reason="The record has since moved on",
            )
        assert reported is not None
        assert reported.application_state == "failed"
        assert reported.application_failure_reason == "The record has since moved on"

    async def test_it_is_written_exactly_once(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None
        await _decide(session, org_id, request_id=request.id, caller_id=manager)

        async with session.begin():
            await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="applied",
                failure_reason=None,
            )
        # A second, out-of-order report (a redelivery, or a producer that reports twice) must be
        # a silent no-op, never a second write and never an exception from the guard trigger.
        async with session.begin():
            replay = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="failed",
                failure_reason="should never land",
            )
        assert replay is not None
        assert replay.application_state == "applied"
        assert replay.application_failure_reason is None

    async def test_applying_before_a_decision_is_a_noop(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None  # still pending; never decided

        async with session.begin():
            result = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=request.source_ref,
                outcome="applied",
                failure_reason=None,
            )
        assert result is not None
        assert result.application_state is None

    async def test_applying_for_an_unknown_source_ref_is_a_noop(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            result = await services.apply_regularization_outcome(
                session,
                organization_id=org_id,
                source_ref=uuid.uuid4(),
                outcome="applied",
                failure_reason=None,
            )
        assert result is None
