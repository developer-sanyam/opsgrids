"""Opening a request (ADR-0006 §4) and applying delegation at open (§5): the manager routes it,
an exited manager is walked past, an exhausted chain and an unknown requester both produce an
unassigned step, a delegation active at open takes it instead, and opening is idempotent under
redelivery — both at the service layer (`open_regularization_request` called twice with the same
`source_ref`) and at the consumer layer (the same event id redelivered).
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.consumers import on_regularization_requested
from app.models import (
    ApprovalRequest,
    ApprovalStep,
    AuditLog,
    Delegation,
    OutboxEvent,
    ProcessedEvent,
)
from platform_core.db import dispose_engine, init_engine
from platform_core.events import already_processed
from tests.helpers import regularization_requested_envelope, seed_person

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine():
    """Only the consumer-redelivery test needs its own engine (`session_scope` opens a fresh
    connection); the fixture is autoused so every test in this module can call the consumer
    without repeating the boilerplate `test_consumers.py` documents."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _open(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID,
) -> ApprovalRequest | None:
    async with session.begin():
        return await services.open_regularization_request(
            session,
            organization_id=org_id,
            source_ref=source_ref,
            requester_employee_id=requester_employee_id,
            summary={"date": "2026-09-12", "from": "absent", "to": "present"},
            request_note="Forgot to punch in",
        )


async def _open_step(
    session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalStep:
    stmt = select(ApprovalStep).where(
        ApprovalStep.organization_id == org_id,
        ApprovalStep.request_id == request_id,
        ApprovalStep.outcome == "open",
    )
    step = (await session.execute(stmt)).scalar_one()
    return step


class TestManagerIsRouted:
    async def test_level_one_approver_is_the_reporting_manager(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager, display_name="Manager")
            await seed_person(
                session, org_id, requester, reporting_manager_id=manager, display_name="Requester"
            )

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        assert request.status == "pending"

        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.level == 1
        assert step.approver_employee_id == manager
        assert step.acting_for_employee_id is None
        assert step.delegation_id is None
        assert step.reason == "assigned"
        assert step.due_at is None  # ADR-0006 §10 amended: no SLA clock in slices 2-3
        assert step.outcome == "open"

    async def test_no_due_date_is_stamped_regardless_of_policy(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Task note 3 / ADR-0006 §10 amended: `due_at` is always NULL in this slice, whether or
        not a policy row exists — there is no policy row here at all, and the step still opens
        with no due date, which is the point."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.due_at is None


class TestExitedManagerIsSkipped:
    async def test_the_walk_reaches_past_an_exited_manager(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, director, display_name="Director")
            await seed_person(
                session,
                org_id,
                exited_manager,
                reporting_manager_id=director,
                status="exited",
                display_name="Exited Manager",
            )
            await seed_person(
                session,
                org_id,
                requester,
                reporting_manager_id=exited_manager,
                display_name="Requester",
            )

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id == director
        assert step.reason == "assigned"


class TestUnassignedWhenNobodyIsFound:
    async def test_a_chain_with_no_manager_at_all_opens_unassigned(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, requester, reporting_manager_id=None)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id is None
        assert step.reason == "unassigned"
        assert step.due_at is None

    async def test_a_chain_where_every_manager_is_exited_opens_unassigned(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(
                session, org_id, exited_manager, reporting_manager_id=None, status="exited"
            )
            await seed_person(session, org_id, requester, reporting_manager_id=exited_manager)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id is None
        assert step.reason == "unassigned"

    async def test_a_requester_the_copy_has_never_seen_opens_unassigned(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §4: "the requester has no hierarchy_nodes row at all" is one of the four
        causes of an unassigned step, and is not retried — the backfill may not have reached this
        organization yet, or an onboarded event is lagging."""
        requester = uuid.uuid4()  # never seeded

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id is None
        assert step.reason == "unassigned"

    async def test_the_audit_row_names_a_requester_the_copy_never_saw(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.reason == "requester_not_in_hierarchy"

    async def test_the_audit_row_names_an_exhausted_chain_separately(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, requester, reporting_manager_id=None)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.reason == "chain_exhausted"

    async def test_the_audit_row_names_a_cyclic_chain_separately(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §4 rule 3 names a cycle as its own cause, distinct from a chain that simply
        runs out — the audit row must say which, not collapse both into `chain_exhausted`.

        `beta` is seeded `exited` — a plain two-node cycle of active nodes still resolves an
        approver (`pick_approver` picks the first non-exited, non-requester rung, which the cycle
        does not prevent), so the cycle has to also carry no usable approver to reach this branch
        at all, the same way `chain_exhausted` needs every manager exited."""
        alpha, beta = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            # alpha -> beta -> alpha: same two-node cycle `tests/test_chain.py` seeds for the walk
            # itself, requesting as `alpha` so `open_regularization_request` walks straight into it.
            await seed_person(session, org_id, alpha, reporting_manager_id=beta)
            await seed_person(session, org_id, beta, reporting_manager_id=alpha, status="exited")

        request = await _open(session, org_id, source_ref=uuid.uuid4(), requester_employee_id=alpha)
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.reason == "cycle"

    async def test_the_audit_row_names_a_depth_exceeded_chain_separately(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Same cause count as the cycle case above, this time for a chain deeper than the
        32-rung cap (`domain.MAX_CHAIN_DEPTH`) — same shape `tests/test_chain.py` uses for the
        walk itself.

        Reaching the cap alone is not enough to leave the step unassigned (`pick_approver` picks
        the *first* usable rung, so an active manager one level up resolves it regardless of how
        deep the chain runs beyond the cap) — every one of the 32 managers within the cap has to
        be exited too, with one genuine node beyond the cap so the walk is confirmed to run past
        it rather than simply ending there (`test_chain.py`'s own "dangling pointer at the cap"
        case is the negative of this)."""
        chain = [uuid.uuid4() for _ in range(34)]  # requester + 32 exited managers + one beyond
        async with session.begin():
            await seed_person(session, org_id, chain[-1], reporting_manager_id=None)
            for lower, upper in itertools.pairwise(chain[1:]):
                await seed_person(
                    session, org_id, lower, reporting_manager_id=upper, status="exited"
                )
            await seed_person(session, org_id, chain[0], reporting_manager_id=chain[1])

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=chain[0]
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == request.id,
                            AuditLog.action == "approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.reason == "depth_exceeded"


class TestDelegationAppliedAtOpen:
    async def test_an_active_delegation_takes_the_step_instead_of_the_manager(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager, display_name="Manager")
            await seed_person(session, org_id, delegate, display_name="Delegate")
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

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id == delegate
        assert step.acting_for_employee_id == manager
        assert step.delegation_id is not None
        assert step.reason == "delegated"

    async def test_delegation_is_not_applied_when_the_delegate_is_the_requester(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §5: "Delegate is the requester -> no delegation for this step." Skipping to
        the manager's own manager would let a delegation silently change who approves."""
        manager, requester = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=requester,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=manager,
                )
            )

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id == manager
        assert step.reason == "assigned"
        assert step.delegation_id is None

    async def test_delegation_is_not_applied_when_the_delegate_has_exited(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, exited_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, exited_delegate, status="exited")
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=exited_delegate,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=manager,
                )
            )

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id == manager
        assert step.reason == "assigned"

    async def test_a_future_dated_delegation_is_not_active_yet(
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
                    starts_at=now + timedelta(days=1),
                    ends_at=now + timedelta(days=2),
                    created_by_employee_id=manager,
                )
            )

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            step = await _open_step(session, org_id, request.id)
        assert step.approver_employee_id == manager


class TestOutboxAndAudit:
    async def test_opening_publishes_the_outbox_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager, display_name="Priya Manager")
            await seed_person(session, org_id, requester, reporting_manager_id=manager)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.payload["approver_employee_id"] == str(manager)
        # Additive: the approver's display name off the hierarchy copy, so an ordinary employee
        # (who cannot look the approver up themselves) can still be told who a pending step is
        # waiting on.
        assert row.payload["approver_name"] == "Priya Manager"
        assert row.payload["requester_employee_id"] == str(requester)
        assert row.payload["due_at"] is None
        # request_note never travels on a published event payload (ADR-0006 §3).
        assert "request_note" not in row.payload
        assert "summary" not in row.payload

    async def test_an_unassigned_step_publishes_a_null_approver_name(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()  # never seeded -> unassigned (requester_not_in_hierarchy)

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.payload["approver_employee_id"] is None
        assert row.payload["approver_name"] is None

    async def test_a_delegated_steps_approver_name_is_the_delegates_own(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The published name follows whoever the step actually points at — the delegate, not
        the delegator — the same substitution `step.approver_employee_id` itself makes at open
        (ADR-0006 §5)."""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager, display_name="Manager")
            await seed_person(session, org_id, delegate, display_name="Delegate")
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

        request = await _open(
            session, org_id, source_ref=uuid.uuid4(), requester_employee_id=requester
        )
        assert request is not None
        async with session.begin():
            row = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == request.id,
                            OutboxEvent.event_type == "core.approval.request.opened",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert row.payload["approver_employee_id"] == str(delegate)
        assert row.payload["approver_name"] == "Delegate"

    async def test_a_name_lookup_missing_from_the_copy_resolves_to_none_not_an_exception(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The defensive half of `approver_name`'s contract: "null, not an error, when the
        approver has no hierarchy row." Unreachable through `open_regularization_request` itself
        in this slice — `step_approver_id` is only ever set from a `Rung`/`HierarchyNode` row
        already read off the copy (`pick_approver`'s output, or a delegate confirmed present via
        `get_hierarchy_node` before it is used — see `app/services.py`), so there is no code path
        where a name lookup for the chosen approver could come back empty. This exercises the
        same selector directly, against an id that was never seeded, to pin the "absence resolves
        to `None`, never raises" behaviour the reuse relies on."""
        never_seeded = uuid.uuid4()
        async with session.begin():
            node = await selectors.get_hierarchy_node(session, org_id, never_seeded)
        assert node is None


class TestIdempotentOpening:
    async def test_the_same_source_ref_opened_twice_creates_one_request(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        source_ref = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)

        first = await _open(session, org_id, source_ref=source_ref, requester_employee_id=requester)
        second = await _open(
            session, org_id, source_ref=source_ref, requester_employee_id=requester
        )

        assert first is not None
        assert second is None  # ON CONFLICT DO NOTHING: the no-op redelivery signal

        async with session.begin():
            count = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.organization_id == org_id,
                            ApprovalRequest.source_ref == source_ref,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(count) == 1

    async def test_the_same_event_id_redelivered_opens_nothing_twice(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The consumer layer's own idempotency (`processed_events`), on top of the service
        layer's `ON CONFLICT` — two independent guards against two independent causes of
        repetition (at-least-once delivery, and a producer republishing under a new event id)."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        source_ref = uuid.uuid4()
        event_id = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)

        envelope = regularization_requested_envelope(
            event_id=event_id,
            organization_id=org_id,
            regularization_request_id=source_ref,
            employee_id=requester,
        )
        await on_regularization_requested(envelope)
        await on_regularization_requested(envelope)  # exact redelivery, same event id

        async with session.begin():
            requests = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.organization_id == org_id,
                            ApprovalRequest.source_ref == source_ref,
                        )
                    )
                )
                .scalars()
                .all()
            )
            processed = await already_processed(session, ProcessedEvent, event_id)
        assert len(requests) == 1
        assert processed is True

    async def test_the_consumer_maps_the_attendance_payload_contract_correctly(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Verifies the field mapping ADR-0006 §12 specifies against the envelope shape
        attendance actually publishes (`regularization_requested_envelope`, checked against
        `services/attendance/app/services.py` while this slice was built)."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        source_ref = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)

        envelope = regularization_requested_envelope(
            organization_id=org_id,
            regularization_request_id=source_ref,
            employee_id=requester,
            requested_status="present",
            current_status="absent",
            reason="Forgot to punch in",
            date="2026-09-12",
        )
        await on_regularization_requested(envelope)

        async with session.begin():
            request = (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.organization_id == org_id,
                        ApprovalRequest.source_ref == source_ref,
                    )
                )
            ).scalar_one()
        assert request.requester_employee_id == requester
        assert request.summary == {"date": "2026-09-12", "from": "absent", "to": "present"}
        assert request.request_note == "Forgot to punch in"
        assert request.request_type == "hrms.time.regularization"
