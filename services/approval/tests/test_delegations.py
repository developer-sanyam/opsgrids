"""Delegation (ADR-0006 §5, §11, slice 3): every refusal `POST /delegations` states, re-pointing
counts and closes correctly while the original approver stays eligible, a revoke is idempotent
and disturbs no decision already made, and a delegation that is not yet active or has already
expired neither routes to nor confers eligibility on the delegate. Negative-authz and
cross-tenant isolation for the whole delegation surface live here too, alongside the read/write
endpoints (`GET /delegations`, `POST /delegations`, `POST /delegations/{id}/revoke`) — the
candidate picker (`GET /delegate-candidates`) has its own module,
`tests/test_delegate_candidates.py`, because it is a read-only search surface with a different
shape of test (prefix matching, the cap, plan verification) than the CRUD surface here.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from app.models import ApprovalRequest, ApprovalStep, AuditLog, Delegation
from platform_core.auth import Principal, get_principal
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from tests.helpers import open_request, principal_for, seed_person

pytestmark = pytest.mark.integration

DELEGATION_WRITE = "core.approval.delegation.write"
DECIDE = "core.approval.request.decide"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _create(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    delegator_employee_id: uuid.UUID,
    delegate_employee_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    created_by_employee_id: uuid.UUID | None = None,
) -> tuple[Delegation, int]:
    async with session.begin():
        return await services.create_delegation(
            session,
            organization_id=org_id,
            delegator_employee_id=delegator_employee_id,
            delegate_employee_id=delegate_employee_id,
            starts_at=starts_at,
            ends_at=ends_at,
            created_by_employee_id=created_by_employee_id or delegator_employee_id,
        )


# --------------------------------------------------------------------- create: field refusals


class TestCreateValidation:
    async def test_delegating_to_yourself_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager = uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=manager,
                starts_at=now,
                ends_at=now + timedelta(days=1),
            )
        assert excinfo.value.errors[0]["code"] == "self_delegation"

    async def test_an_end_before_the_start_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=delegate,
                starts_at=now + timedelta(days=2),
                ends_at=now + timedelta(days=1),
            )
        assert excinfo.value.errors[0]["code"] == "window_backwards"

    async def test_an_end_equal_to_the_start_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The window is half-open `[starts_at, ends_at)` (data model §1.5) — a zero-length
        window is not a legal delegation."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC) + timedelta(hours=1)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=delegate,
                starts_at=now,
                ends_at=now,
            )
        assert excinfo.value.errors[0]["code"] == "window_backwards"

    async def test_an_end_in_the_past_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=delegate,
                starts_at=now - timedelta(days=2),
                ends_at=now - timedelta(days=1),
            )
        assert excinfo.value.errors[0]["code"] == "window_in_the_past"

    async def test_a_delegate_who_has_exited_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, exited_delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, exited_delegate, status="exited")
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=exited_delegate,
                starts_at=now,
                ends_at=now + timedelta(days=1),
            )
        assert excinfo.value.errors[0]["code"] == "delegate_not_available"

    async def test_a_delegate_the_copy_has_never_seen_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §5: "the delegate must have a hierarchy_nodes row whose status is not
        exited" — a row that does not exist at all fails the same test. The picker never offers
        such an id; this is the defence for a client that bypasses it."""
        manager = uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=uuid.uuid4(),
                starts_at=now,
                ends_at=now + timedelta(days=1),
            )
        assert excinfo.value.errors[0]["code"] == "delegate_not_available"

    async def test_a_delegator_the_copy_has_never_seen_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Symmetric with the delegate check above: `delegator_employee_id` is validated against
        the tenant's `hierarchy_nodes` copy too, not only used unvalidated. No cross-tenant
        effect today (`organization_id` is bound into every query regardless of this check), but
        an id this service has never seen for this org is unvalidated data all the same."""
        now = datetime.now(UTC)
        delegate = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, delegate)
        with pytest.raises(ValidationProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=uuid.uuid4(),
                delegate_employee_id=delegate,
                starts_at=now,
                ends_at=now + timedelta(days=1),
                created_by_employee_id=delegate,
            )
        assert excinfo.value.errors[0]["code"] == "delegator_not_in_tenant"

    async def test_an_overlapping_active_delegation_is_a_clean_conflict(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, first_delegate, second_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, first_delegate)
            await seed_person(session, org_id, second_delegate)
        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=first_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=5),
        )

        with pytest.raises(ConflictProblem):
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=second_delegate,
                starts_at=now + timedelta(days=1),
                ends_at=now + timedelta(days=2),
            )

    async def test_a_window_starting_exactly_when_another_ends_is_accepted(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Half-open `[)`: data model §1.5's own verified property — a one-day delegation can be
        followed immediately by another starting the moment it ends, with no gap and no
        overlap."""
        manager, first_delegate, second_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, first_delegate)
            await seed_person(session, org_id, second_delegate)
        first, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=first_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        second, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=second_delegate,
            starts_at=first.ends_at,
            ends_at=first.ends_at + timedelta(days=1),
        )
        # The property this test names: a window starting exactly when the previous one ends is
        # accepted, unchanged, not silently adjusted — `second.starts_at` is `first.ends_at`
        # itself, and the two are back-to-back with no gap and no overlap.
        assert second.starts_at == first.ends_at


# ------------------------------------------------------------ the race the pre-check misses


class TestOverlapRaceFallback:
    """`app/services.create_delegation`'s `except IntegrityError` sits right after `flush()`,
    which drains every pending object in the session — so it must distinguish `
    ex_delegations_no_overlap` firing from any other integrity failure, or it reports every one
    of them as "you already have a delegation that covers those dates," which is simply wrong
    for anything else. Every overlap test above exits through the opportunistic pre-check
    (`selectors.get_overlapping_delegation`) and never reaches `flush()` with a conflict still
    live, so the race path itself was unproven — these two tests reach it directly."""

    async def test_a_race_the_precheck_misses_still_gets_a_clean_conflict(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defeats the pre-check (returns `None`, as a concurrent transaction that only commits
        between our read and our insert would) so the real `ex_delegations_no_overlap` exclusion
        constraint is what actually refuses the second delegation, and the `except IntegrityError`
        branch — not the pre-check's own `if overlap is not None` — is what turns it into a 409.
        The generic message that branch writes (no conflicting range named, unlike the pre-check's
        own message) is the tell that it, and not the pre-check, fired.
        """
        manager, first_delegate, second_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, first_delegate)
            await seed_person(session, org_id, second_delegate)
        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=first_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=5),
        )

        async def _miss(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(services.selectors, "get_overlapping_delegation", _miss)

        with pytest.raises(ConflictProblem) as excinfo:
            await _create(
                session,
                org_id,
                delegator_employee_id=manager,
                delegate_employee_id=second_delegate,
                starts_at=now + timedelta(days=1),
                ends_at=now + timedelta(days=2),
            )
        assert excinfo.value.detail == "You already have a delegation that covers those dates."

    async def test_an_integrity_error_unrelated_to_overlap_is_not_reported_as_a_conflict(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A `flush()` failure whose constraint is *not* `ex_delegations_no_overlap` must
        propagate as-is (the platform's own `IntegrityError` handler turns it into a generic,
        non-leaking 409 — see `platform_core/errors.py`), never as the delegation-specific
        overlap message that would misreport what actually went wrong."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)

        class _FakeDriverError(Exception):
            constraint_name = "some_other_constraint"

        async def _raise_flush() -> None:
            raise IntegrityError("INSERT", {}, _FakeDriverError())

        with pytest.raises(IntegrityError):
            async with session.begin():
                monkeypatch.setattr(session, "flush", _raise_flush)
                await services.create_delegation(
                    session,
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=delegate,
                    starts_at=now,
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=manager,
                )


# --------------------------------------------------------------------- re-pointing (§5)


class TestRepointing:
    async def test_repointed_count_and_the_original_step_closes_as_delegated(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        delegation, repointed_count = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        assert repointed_count == 1

        async with session.begin():
            steps = (
                (
                    await session.execute(
                        select(ApprovalStep)
                        .where(ApprovalStep.request_id == request.id)
                        .order_by(ApprovalStep.opened_at)
                    )
                )
                .scalars()
                .all()
            )
        assert len(steps) == 2
        original, successor = steps
        assert original.outcome == "delegated"
        assert original.closed_at is not None
        assert original.approver_employee_id == manager

        assert successor.outcome == "open"
        assert successor.approver_employee_id == delegate
        assert successor.acting_for_employee_id == manager
        assert successor.delegation_id == delegation.id
        assert successor.reason == "delegated"
        assert successor.level == original.level
        # "the predecessor's due_at carried over unchanged" (ADR-0006 §5) — the default policy
        # arms a real value at open (slice 4), so this now exercises the carry-through with a
        # non-trivial value rather than two `None`s trivially matching.
        assert original.due_at is not None
        assert successor.due_at == original.due_at

    async def test_the_original_approver_can_still_decide_after_repointing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Route (b): re-pointing moves the *current* step, it does not remove the original
        approver's own eligibility (ADR-0006 §5: "the original approver stays eligible")."""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

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
        # Deciding in their own right (route b), not through the delegation.
        assert decided.acting_for_employee_id is None

    async def test_the_delegate_can_decide_the_repointed_step(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        async with session.begin():
            decided = await services.decide_request(
                session,
                organization_id=org_id,
                request_id=request.id,
                caller_id=delegate,
                has_org_decide=False,
                outcome="approve",
                comment=None,
            )
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == delegate
        assert decided.acting_for_employee_id == manager

    async def test_a_future_dated_delegation_repoints_nothing_at_creation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §5: "Only delegations whose starts_at <= now re-point at creation. A
        future-dated delegation affects only steps opened after it starts, because nothing
        re-points it when it starts.\""""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        _, repointed_count = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        assert repointed_count == 0

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(ApprovalStep.request_id == request.id)
                    )
                )
                .scalars()
                .one()
            )
        assert step.outcome == "open"
        assert step.approver_employee_id == manager

    async def test_a_step_already_delegated_elsewhere_is_not_repointed_again(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`acting_for_employee_id IS NULL` is the re-pointing rule's own guard (ADR-0006 §5): a
        step already reached through delegation A is left alone when delegation B, from a
        *different* delegator, is created — re-pointing is not transitive."""
        requester, manager, first_delegate, second_delegate = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, first_delegate)
            await seed_person(session, org_id, second_delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            # An active delegation manager -> first_delegate *before* the request opens, so
            # routing applies it directly at step-open (ADR-0006 §5's "delegation applied at
            # step open" — a different code path from re-pointing).
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=first_delegate,
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=5),
                    created_by_employee_id=manager,
                )
            )
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(ApprovalStep.request_id == request.id)
                    )
                )
                .scalars()
                .one()
            )
        assert step.approver_employee_id == first_delegate
        assert step.acting_for_employee_id == manager

        # A second, unrelated delegator (`first_delegate`, now standing in for `manager`)
        # delegates to someone else — this must not touch the step above, whose
        # `acting_for_employee_id` is already set.
        _, repointed_count = await _create(
            session,
            org_id,
            delegator_employee_id=first_delegate,
            delegate_employee_id=second_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        assert repointed_count == 0

    async def test_repointing_never_hands_the_request_to_its_own_requester(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The same "delegate is the requester -> no delegation for this step" rule step-open
        already enforces (ADR-0006 §5) applies to re-pointing too: the manager's own report must
        never become eligible to decide their own request through a delegation."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        _, repointed_count = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=requester,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        assert repointed_count == 0

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(ApprovalStep.request_id == request.id)
                    )
                )
                .scalars()
                .one()
            )
        assert step.outcome == "open"
        assert step.approver_employee_id == manager

    async def test_repointing_writes_audit_rows(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        async with session.begin():
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.action.in_(
                                ("approval.delegation.created", "approval.step.delegated")
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(actions) == ["approval.delegation.created", "approval.step.delegated"]


# --------------------------------------------------------------------- not-yet-active / expired


class TestInactiveDelegationsConferNothing:
    async def test_a_not_yet_active_delegation_does_not_route_or_confer_eligibility(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, future_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, future_delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=future_delegate,
                    starts_at=now + timedelta(days=1),
                    ends_at=now + timedelta(days=2),
                    created_by_employee_id=manager,
                )
            )
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(ApprovalStep.request_id == request.id)
                    )
                )
                .scalars()
                .one()
            )
        assert step.approver_employee_id == manager  # did not route to the future delegate

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.decide_request(
                    session,
                    organization_id=org_id,
                    request_id=request.id,
                    caller_id=future_delegate,
                    has_org_decide=False,
                    outcome="approve",
                    comment=None,
                )

    async def test_an_expired_delegation_does_not_route_or_confer_eligibility(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, expired_delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, expired_delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            session.add(
                Delegation(
                    organization_id=org_id,
                    delegator_employee_id=manager,
                    delegate_employee_id=expired_delegate,
                    starts_at=now - timedelta(days=2),
                    ends_at=now - timedelta(days=1),
                    created_by_employee_id=manager,
                )
            )
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(ApprovalStep.request_id == request.id)
                    )
                )
                .scalars()
                .one()
            )
        assert step.approver_employee_id == manager  # did not route to the expired delegate

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.decide_request(
                    session,
                    organization_id=org_id,
                    request_id=request.id,
                    caller_id=expired_delegate,
                    has_org_decide=False,
                    outcome="approve",
                    comment=None,
                )


# --------------------------------------------------------------------- revoke


class TestRevoke:
    async def test_revoking_is_idempotent_the_second_call_is_a_clean_conflict(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        async with session.begin():
            await services.revoke_delegation(
                session,
                organization_id=org_id,
                delegation_id=delegation.id,
                caller_id=manager,
                is_org=False,
            )

        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.revoke_delegation(
                    session,
                    organization_id=org_id,
                    delegation_id=delegation.id,
                    caller_id=manager,
                    is_org=False,
                )

    async def test_a_revoked_delegate_loses_eligibility_immediately(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        delegation, repointed_count = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        assert repointed_count == 1

        async with session.begin():
            await services.revoke_delegation(
                session,
                organization_id=org_id,
                delegation_id=delegation.id,
                caller_id=manager,
                is_org=False,
            )

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.decide_request(
                    session,
                    organization_id=org_id,
                    request_id=request.id,
                    caller_id=delegate,
                    has_org_decide=False,
                    outcome="approve",
                    comment=None,
                )

    async def test_revoke_does_not_disturb_a_decision_the_delegate_already_made(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ "Revocation and expiry never re-route... What it changes is eligibility" (ADR-0006
        §5) — a decision already recorded through the delegate stands exactly as it was."""
        requester, manager, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        async with session.begin():
            decided = await services.decide_request(
                session,
                organization_id=org_id,
                request_id=request.id,
                caller_id=delegate,
                has_org_decide=False,
                outcome="approve",
                comment=None,
            )
        assert decided.status == "approved"
        assert decided.decided_by_employee_id == delegate

        async with session.begin():
            await services.revoke_delegation(
                session,
                organization_id=org_id,
                delegation_id=delegation.id,
                caller_id=manager,
                is_org=False,
            )

        async with session.begin():
            reread = await session.get(ApprovalRequest, decided.id)
        assert reread is not None
        assert reread.status == "approved"
        assert reread.decided_by_employee_id == delegate
        assert reread.acting_for_employee_id == manager

    async def test_a_bystander_cannot_see_or_revoke_someone_elses_delegation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate, bystander = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.revoke_delegation(
                    session,
                    organization_id=org_id,
                    delegation_id=delegation.id,
                    caller_id=bystander,
                    is_org=False,
                )

    async def test_the_delegate_can_see_but_not_revoke_it(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Below ORG, only the delegator may revoke (ADR-0006 §11) — the delegate is visible
        through `GET /delegations`' OWN reading but must be refused with 403, not 404, because
        they already know the row exists."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.revoke_delegation(
                    session,
                    organization_id=org_id,
                    delegation_id=delegation.id,
                    caller_id=delegate,
                    is_org=False,
                )

    async def test_org_scope_may_revoke_any_delegation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate, hr_admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        async with session.begin():
            revoked = await services.revoke_delegation(
                session,
                organization_id=org_id,
                delegation_id=delegation.id,
                caller_id=hr_admin,
                is_org=True,
            )
        assert revoked.revoked_at is not None
        assert revoked.revoked_by_employee_id == hr_admin


# --------------------------------------------------------------------- HTTP: authorization


class TestCreateEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id: uuid.UUID) -> None:
        response = await client.post(
            "/api/v1/approvals/delegations",
            json={
                "delegate_employee_id": str(uuid.uuid4()),
                "starts_at": datetime.now(UTC).isoformat(),
                "ends_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 401

    async def test_own_scope_cannot_create_for_another_employee(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The negative-authz case the task calls out by name: OWN cannot create on another
        employee's behalf, even naming themselves correctly as caller — only ORG reaches the
        `delegator_employee_id` field at all."""
        caller, someone_else, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, someone_else)
            await seed_person(session, org_id, delegate)

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegator_employee_id": str(someone_else),
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 403

    async def test_team_scope_cannot_create_for_another_employee(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`delegation.write`'s `max_scope` is ORG (`app/catalogue.py`), so a TEAM grant is legal
        on the permission ladder even though it is read as OWN everywhere below ORG
        (`app/catalogue.py`'s collapse: "OWN only where the caller is the delegator") — this must
        behave exactly like the OWN test above, not silently reach `delegator_employee_id` just
        because the grant ranks above OWN. The regression this guards is the one slice 2 shipped
        on `/requests`: a scope legal on the ladder but never actually exercised."""
        caller, someone_else, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, someone_else)
            await seed_person(session, org_id, delegate)

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegator_employee_id": str(someone_else),
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 403

    async def test_location_scope_cannot_create_for_another_employee(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Same regression, the other scope below ORG that is legal on the ladder and must still
        collapse to OWN's behaviour."""
        caller, someone_else, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, someone_else)
            await seed_person(session, org_id, delegate)

        principal = principal_for(
            org_id, caller, [f"{DELEGATION_WRITE}:LOCATION"], locations=["BLR"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegator_employee_id": str(someone_else),
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 403

    async def test_team_scope_creating_for_self_succeeds(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The OWN reading is still available through a TEAM grant — collapsing to OWN narrows
        who the caller may name, it does not refuse the caller their own, ordinary delegation."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 201
        assert response.json()["delegator_employee_id"] == str(manager)

    async def test_org_scope_can_create_for_another_employee(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        hr_admin, someone_else, delegate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, someone_else)
            await seed_person(session, org_id, delegate)

        principal = principal_for(org_id, hr_admin, [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegator_employee_id": str(someone_else),
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["delegator_employee_id"] == str(someone_else)
        assert body["delegate_employee_id"] == str(delegate)
        assert body["repointed_count"] == 0
        assert body["status"] == "active"

    async def test_own_scope_creating_for_self_succeeds(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager, display_name="Priya Manager")
            await seed_person(session, org_id, delegate, display_name="Rohan Delegate")

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["delegator_employee_id"] == str(manager)
        assert body["delegator_name"] == "Priya Manager"
        assert body["delegate_name"] == "Rohan Delegate"

    async def test_a_client_supplied_unknown_field_is_rejected(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/approvals/delegations",
                json={
                    "delegate_employee_id": str(delegate),
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(days=1)).isoformat(),
                    "reason": "unexpected",
                },
            )
        assert response.status_code == 422


class TestListEndpointVisibility:
    async def test_own_scope_sees_rows_where_it_is_either_party(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate, bystander_manager, bystander_delegate = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, bystander_manager)
            await seed_person(session, org_id, bystander_delegate)
        mine, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=bystander_manager,
            delegate_employee_id=bystander_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, delegate, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(mine.id)}

    async def test_org_scope_sees_every_delegation(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager_a, delegate_a, manager_b, delegate_b = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        now = datetime.now(UTC)
        async with session.begin():
            for e in (manager_a, delegate_a, manager_b, delegate_b):
                await seed_person(session, org_id, e)
        first, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager_a,
            delegate_employee_id=delegate_a,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        second, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager_b,
            delegate_employee_id=delegate_b,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(first.id), str(second.id)}

    async def test_a_caller_with_no_subject_ref_sees_nothing_below_org(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, None, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_team_scope_reads_as_own(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`delegation.write`'s `max_scope` is ORG, so a TEAM grant is legal on the ladder — and
        `selectors.delegations_stmt` collapses everything below ORG to the same "either party"
        read (`app/catalogue.py`). This is the list-endpoint half of the regression slice 2
        shipped on `/requests`: nothing proved a TEAM grant here actually reads as OWN rather
        than, say, being silently refused or silently widened."""
        manager, delegate, bystander_manager, bystander_delegate = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, bystander_manager)
            await seed_person(session, org_id, bystander_delegate)
        mine, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=bystander_manager,
            delegate_employee_id=bystander_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, delegate, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(mine.id)}

    async def test_location_scope_reads_as_own(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Same regression, LOCATION — the other scope below ORG that is legal on the ladder."""
        manager, delegate, bystander_manager, bystander_delegate = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await seed_person(session, org_id, bystander_manager)
            await seed_person(session, org_id, bystander_delegate)
        mine, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=bystander_manager,
            delegate_employee_id=bystander_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(
            org_id, delegate, [f"{DELEGATION_WRITE}:LOCATION"], locations=["BLR"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {str(mine.id)}


class TestListEndpointEmployeeFilter:
    """`GET /delegations?employee_id=` (screens §4.E's ORG "filtered to a person" state).

    Two properties, and they are the whole of the contract: the filter matches **either
    direction** (that person as delegator or as delegate), and it `AND`s with the endpoint's
    scope predicate rather than replacing it. The second is the one a regression would be
    invisible without — a reading that swapped the filter *in* for the scope check would pass
    every single-scope assertion in `TestListEndpointVisibility` above and still hand a
    below-ORG caller someone else's delegations.

    The filter is deliberately **not** refused below ORG: intersecting "rows I am a party to"
    with "rows they are a party to" can only ever return rows the caller's own unfiltered page
    already contains, and a non-matching id is an empty page rather than a distinguishable
    error — see `app/api/routes.py`'s `list_delegations` docstring for the full reasoning and
    for why `POST /delegations`' `delegator_employee_id` is a different question.
    """

    async def test_org_scope_filters_to_a_person_in_both_directions(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subject, their_delegate, their_delegator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        stranger_a, stranger_b = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            for e in (subject, their_delegate, their_delegator, stranger_a, stranger_b):
                await seed_person(session, org_id, e)
        as_delegator, _ = await _create(
            session,
            org_id,
            delegator_employee_id=subject,
            delegate_employee_id=their_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        as_delegate, _ = await _create(
            session,
            org_id,
            delegator_employee_id=their_delegator,
            delegate_employee_id=subject,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=stranger_a,
            delegate_employee_id=stranger_b,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            unfiltered = await client.get("/api/v1/approvals/delegations")
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(subject)}
            )
        assert unfiltered.json()["total"] == 3
        assert response.status_code == 200
        body = response.json()
        assert {item["id"] for item in body["items"]} == {
            str(as_delegator.id),
            str(as_delegate.id),
        }
        assert body["total"] == 2

    async def test_own_scope_filtering_to_self_returns_own_rows_both_directions(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller, their_delegate, their_delegator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        stranger_a, stranger_b = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            for e in (caller, their_delegate, their_delegator, stranger_a, stranger_b):
                await seed_person(session, org_id, e)
        mine_as_delegator, _ = await _create(
            session,
            org_id,
            delegator_employee_id=caller,
            delegate_employee_id=their_delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        mine_as_delegate, _ = await _create(
            session,
            org_id,
            delegator_employee_id=their_delegator,
            delegate_employee_id=caller,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=stranger_a,
            delegate_employee_id=stranger_b,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(caller)}
            )
        assert response.status_code == 200
        body = response.json()
        assert {item["id"] for item in body["items"]} == {
            str(mine_as_delegator.id),
            str(mine_as_delegate.id),
        }
        assert body["total"] == 2

    async def test_own_scope_naming_a_third_party_gets_only_the_intersection(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The property the whole parameter turns on: the filter `AND`s with scope. `third` is a
        party to two rows — one the caller shares with them, one the caller has nothing to do
        with — and only the shared one comes back. A reading that replaced the scope predicate
        with the filter would return both, which is the leak this asserts the absence of."""
        caller, third, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            for e in (caller, third, outsider):
                await seed_person(session, org_id, e)
        shared, _ = await _create(
            session,
            org_id,
            delegator_employee_id=caller,
            delegate_employee_id=third,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        # `third` as delegator with someone the caller is not a party to — visible to ORG, and to
        # `third` themselves, but never through the caller's own scope predicate.
        theirs_alone, _ = await _create(
            session,
            org_id,
            delegator_employee_id=third,
            delegate_employee_id=outsider,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, caller, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(third)}
            )
        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body["items"]] == [str(shared.id)]
        assert body["total"] == 1

        # …and an ORG caller, filtering on the same id, does see both — so the row the caller was
        # denied is a real row that exists, not an artefact of the fixture.
        org_principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(org_principal):
            org_response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(third)}
            )
        assert {item["id"] for item in org_response.json()["items"]} == {
            str(shared.id),
            str(theirs_alone.id),
        }
        assert org_response.json()["total"] == 2

    async def test_a_caller_with_no_subject_ref_still_sees_nothing_below_org(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`employee_id` is a narrowing filter, never a substitute identity: a membership with no
        employee link has nothing to match the scope predicate against and still matches nothing,
        exactly as it does unfiltered."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, None, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(manager)}
            )
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    async def test_total_counts_the_filtered_set_across_more_than_one_page(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The paging lie this parameter exists to close. The shipped UI filtered an
        already-loaded page client-side and rendered the server's *unfiltered* `total`, so the
        footer claimed more rows than the filter could ever produce. `total` must count the
        filtered set, and the pages must add up to it — which only more rows than one page holds
        can prove."""
        subject = uuid.uuid4()
        delegates = [uuid.uuid4() for _ in range(3)]
        stranger_a, stranger_b, stranger_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            for e in (subject, *delegates, stranger_a, stranger_b, stranger_c):
                await seed_person(session, org_id, e)
        mine = set()
        for i, d in enumerate(delegates):
            # Non-overlapping windows: `ex_delegations_no_overlap` forbids two live delegations
            # for the same delegator from overlapping, so the three are spaced two days apart.
            created, _ = await _create(
                session,
                org_id,
                delegator_employee_id=subject,
                delegate_employee_id=d,
                starts_at=now + timedelta(days=2 * i),
                ends_at=now + timedelta(days=2 * i + 1),
            )
            mine.add(str(created.id))
        await _create(
            session,
            org_id,
            delegator_employee_id=stranger_a,
            delegate_employee_id=stranger_b,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        await _create(
            session,
            org_id,
            delegator_employee_id=stranger_b,
            delegate_employee_id=stranger_c,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            unfiltered = await client.get("/api/v1/approvals/delegations", params={"size": 2})
            first = await client.get(
                "/api/v1/approvals/delegations",
                params={"employee_id": str(subject), "size": 2, "page": 1},
            )
            second = await client.get(
                "/api/v1/approvals/delegations",
                params={"employee_id": str(subject), "size": 2, "page": 2},
            )

        assert unfiltered.json()["total"] == 5  # what the old client-side filter would have shown
        assert first.status_code == 200
        assert first.json()["total"] == 3
        assert first.json()["pages"] == 2
        assert len(first.json()["items"]) == 2
        assert second.json()["total"] == 3
        assert len(second.json()["items"]) == 1
        seen = {item["id"] for item in first.json()["items"]} | {
            item["id"] for item in second.json()["items"]
        }
        assert seen == mine

    async def test_an_employee_id_matching_nobody_is_an_empty_page_not_an_error(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No existence oracle: an id nobody holds a delegation with — including one belonging to
        no employee at all — is the same empty page as an id that simply shares no row with the
        caller, never a 404 and never a 403."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(uuid.uuid4())}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["pages"] == 0

    async def test_a_malformed_employee_id_is_422(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": "not-a-uuid"}
            )
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "employee_id"

    async def test_an_employee_id_from_another_tenant_matches_nothing(
        self, client, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Cross-tenant: the organization filter runs first and the `employee_id` narrows within
        it, so naming an employee who holds delegations in *another* organization is the same
        empty page as naming nobody."""
        other_org = uuid.uuid4()
        foreign_manager, foreign_delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with admin_session.begin():
            admin_session.add(
                Delegation(
                    organization_id=other_org,
                    delegator_employee_id=foreign_manager,
                    delegate_employee_id=foreign_delegate,
                    starts_at=now,
                    ends_at=now + timedelta(days=1),
                    created_by_employee_id=foreign_manager,
                )
            )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/approvals/delegations", params={"employee_id": str(foreign_manager)}
            )
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0


class TestRevokeEndpoint:
    async def test_own_scope_delegator_can_revoke(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/approvals/delegations/{delegation.id}/revoke")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "revoked"
        assert body["revoked_by_employee_id"] == str(manager)

    async def test_revoking_twice_over_http_is_a_clean_409(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:OWN"])
        with as_principal(principal):
            first = await client.post(f"/api/v1/approvals/delegations/{delegation.id}/revoke")
            second = await client.post(f"/api/v1/approvals/delegations/{delegation.id}/revoke")
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.headers["content-type"].startswith("application/problem+json")

    async def test_an_unknown_id_is_404(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.post(f"/api/v1/approvals/delegations/{uuid.uuid4()}/revoke")
        assert response.status_code == 404

    async def test_team_scope_delegator_can_revoke_but_delegate_cannot(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The same OWN collapse as the list and create endpoints, proven on revoke's own
        object-level rule: a TEAM grant reaches only the delegator, exactly as OWN does — the
        delegate, who can see the row, still cannot revoke it."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )
        # Captured before any HTTP call: a failing request rolls back the shared session's
        # transaction (`get_tenant_session`), which expires every attribute on `delegation` —
        # re-reading `.id` off the ORM object after that would need a lazy load this sync test
        # body has no greenlet context for. A plain `UUID` has no such attribute to expire.
        delegation_id = delegation.id

        delegate_principal = principal_for(org_id, delegate, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(delegate_principal):
            forbidden = await client.post(f"/api/v1/approvals/delegations/{delegation_id}/revoke")
        assert forbidden.status_code == 403

        manager_principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:TEAM"])
        with as_principal(manager_principal):
            response = await client.post(f"/api/v1/approvals/delegations/{delegation_id}/revoke")
        assert response.status_code == 200
        assert response.json()["revoked_by_employee_id"] == str(manager)

    async def test_location_scope_delegator_can_revoke(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Same regression, LOCATION."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
        delegation, _ = await _create(
            session,
            org_id,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(
            org_id, manager, [f"{DELEGATION_WRITE}:LOCATION"], locations=["BLR"]
        )
        with as_principal(principal):
            response = await client.post(f"/api/v1/approvals/delegations/{delegation.id}/revoke")
        assert response.status_code == 200
        assert response.json()["revoked_by_employee_id"] == str(manager)


# --------------------------------------------------------------------- cross-tenant isolation


class TestCrossTenantIsolation:
    async def test_a_cross_tenant_delegation_id_is_refused_on_revoke(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with admin_session.begin():
            await seed_person(admin_session, other_org, manager)
            await seed_person(admin_session, other_org, delegate)
        other_delegation, _ = await _create(
            admin_session,
            other_org,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        # `manager` also exists (unrelated) in `org_id`, and holds ORG there.
        async with session.begin():
            await seed_person(session, org_id, manager)

        principal = principal_for(org_id, manager, [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/approvals/delegations/{other_delegation.id}/revoke"
            )
        assert response.status_code == 404

    async def test_a_cross_tenant_delegation_never_appears_in_the_list(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with admin_session.begin():
            await seed_person(admin_session, other_org, manager)
            await seed_person(admin_session, other_org, delegate)
        other_delegation, _ = await _create(
            admin_session,
            other_org,
            delegator_employee_id=manager,
            delegate_employee_id=delegate,
            starts_at=now,
            ends_at=now + timedelta(days=1),
        )

        principal = principal_for(org_id, uuid.uuid4(), [f"{DELEGATION_WRITE}:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/delegations")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["items"]}
        assert str(other_delegation.id) not in ids
