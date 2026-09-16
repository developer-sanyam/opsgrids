"""The inbox's widened `escalated_to_me`/`escalated_away` arms (ADR-0006 §6, §11; screens §2.3,
slice 4): once a step has escalated, both the person who now holds it and the person it moved
away from must see the request in `GET /inbox` — the gap this slice closes was that a caller who
already knew the request id could *decide* it after escalation (`check_eligibility` walks the
full step lineage, `tests/test_escalation.py`'s own
`TestOriginalApproverStillEligible`), but the inbox listing never resurfaced it, because every
branch of `_INBOX_CANDIDATES_CTE` read only the current open step.

`escalate_step` is exercised directly (the same helper shape `tests/test_escalation.py` uses)
rather than through the sweep loop — this module is about what the inbox shows once an escalation
has happened, not about discovering or triggering one.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from app.models import ApprovalStep
from app.services import db_now
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for, seed_pending_request_with_step, seed_person

pytestmark = pytest.mark.integration

DECIDE_OWN = "core.approval.request.decide:OWN"
READ_OWN = "core.approval.request.read:OWN"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _escalate(session: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID) -> None:
    async with session.begin():
        result = await services.escalate_step(
            session, organization_id=org_id, request_id=request_id
        )
    assert result.escalated is True


async def _seed_escalated_request(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    manager: uuid.UUID,
    director: uuid.UUID,
    requester: uuid.UUID | None = None,
) -> uuid.UUID:
    """A pending request whose one step escalated from `manager` up to `director`."""
    requester = requester or uuid.uuid4()
    async with session.begin():
        now = await db_now(session)
        await seed_person(session, org_id, director, display_name="Director Dee")
        await seed_person(
            session, org_id, manager, reporting_manager_id=director, display_name="Manager Em"
        )
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
    return request.id


class TestEscalatedInboxReasons:
    async def test_the_new_holder_sees_escalated_to_me_with_the_previous_approvers_name(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, director = uuid.uuid4(), uuid.uuid4()
        request_id = await _seed_escalated_request(
            session, org_id, manager=manager, director=director
        )

        principal = principal_for(org_id, director, [DECIDE_OWN])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(request_id)
        assert item["inbox_reason"] == "escalated_to_me"
        assert item["from_name"] == "Manager Em"

    async def test_the_original_approver_sees_escalated_away_with_the_new_holders_name(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, director = uuid.uuid4(), uuid.uuid4()
        request_id = await _seed_escalated_request(
            session, org_id, manager=manager, director=director
        )

        principal = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(request_id)
        assert item["inbox_reason"] == "escalated_away"
        assert item["from_name"] == "Director Dee"

    async def test_a_bystander_sees_neither(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Escalation widens who may answer; it does not hand the request to anyone the chain
        never touched (ADR-0006 §6)."""
        manager, director = uuid.uuid4(), uuid.uuid4()
        await _seed_escalated_request(session, org_id, manager=manager, director=director)

        bystander = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, bystander)
        principal = principal_for(org_id, bystander, [DECIDE_OWN])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    async def test_the_original_approver_can_still_decide_once_it_resurfaces(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The inbox listing and the decision endpoint must agree: what `escalated_away` shows is
        exactly what route (a) on the caller's own escalated step already lets them decide."""
        manager, director = uuid.uuid4(), uuid.uuid4()
        request_id = await _seed_escalated_request(
            session, org_id, manager=manager, director=director
        )

        principal = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(principal):
            inbox = await client.get("/api/v1/approvals/inbox")
            decision = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision", json={"outcome": "approve"}
            )
        assert inbox.json()["items"][0]["inbox_reason"] == "escalated_away"
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert decision.json()["decided_by_employee_id"] == str(manager)


class TestInboxRowsCarryTheirDeadline:
    """`GET /inbox`'s own `due_at` (screens §4.A's Due column, the overdue banner, the Overdue
    filter) — off the step this row matched through, never recomputed by this endpoint. All four
    branches of `_INBOX_CANDIDATES_CTE` must agree, so a row's deadline does not depend on which
    branch matched it."""

    async def test_a_live_assigned_row_carries_its_own_open_steps_due_at(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            due_at = now + timedelta(minutes=30)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now,
                due_at=due_at,
            )

        principal = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["id"] == str(request.id)
        assert item["due_at"] is not None
        assert datetime.fromisoformat(item["due_at"]) == due_at

    async def test_an_unassigned_row_carries_no_due_at(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`ck_approval_steps_unassigned_has_no_due` — an unassigned step never has one to carry,
        and this endpoint must not fabricate one."""
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

        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.request.decide:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["id"] == str(request.id)
        assert item["due_at"] is None

    async def test_an_escalated_row_carries_the_successor_steps_due_at_not_the_closed_predecessors(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Both `escalated_to_me` (the new holder) and `escalated_away` (the original approver)
        must report the *current* open step's deadline — the closed, escalated-away predecessor's
        `due_at` already lapsed and is not anyone's deadline any more."""
        manager, director = uuid.uuid4(), uuid.uuid4()
        request_id = await _seed_escalated_request(
            session, org_id, manager=manager, director=director
        )

        async with session.begin():
            open_step = (
                await session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == org_id,
                        ApprovalStep.request_id == request_id,
                        ApprovalStep.outcome == "open",
                    )
                )
            ).scalar_one()
            closed_step = (
                await session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == org_id,
                        ApprovalStep.request_id == request_id,
                        ApprovalStep.outcome == "escalated",
                    )
                )
            ).scalar_one()
        assert open_step.due_at is not None
        assert closed_step.due_at is not None
        # Sanity on the fixture itself: the two deadlines must actually differ, or this test could
        # pass by accident regardless of which one the query picked.
        assert open_step.due_at != closed_step.due_at

        director_principal = principal_for(org_id, director, [DECIDE_OWN])
        with as_principal(director_principal):
            director_inbox = await client.get("/api/v1/approvals/inbox")
        director_item = director_inbox.json()["items"][0]
        assert director_item["inbox_reason"] == "escalated_to_me"
        assert datetime.fromisoformat(director_item["due_at"]) == open_step.due_at

        manager_principal = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(manager_principal):
            manager_inbox = await client.get("/api/v1/approvals/inbox")
        manager_item = manager_inbox.json()["items"][0]
        assert manager_item["inbox_reason"] == "escalated_away"
        assert datetime.fromisoformat(manager_item["due_at"]) == open_step.due_at


class TestInboxCountsStayConsistentWithThePage:
    async def test_total_matches_the_full_result_across_pages(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Count and page share the same widened CTE by construction
        (`app/selectors.py`'s `_INBOX_COUNT_SQL`/`_INBOX_PAGE_SQL`), exercised here across the new
        `escalated_away` branch specifically — two separate requests escalated away from the same
        manager, to two different directors."""
        manager = uuid.uuid4()
        director_a, director_b = uuid.uuid4(), uuid.uuid4()
        request_a = await _seed_escalated_request(
            session, org_id, manager=manager, director=director_a
        )
        request_b = await _seed_escalated_request(
            session, org_id, manager=manager, director=director_b
        )

        principal = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(principal):
            whole = await client.get("/api/v1/approvals/inbox", params={"size": 200})
            page_1 = await client.get("/api/v1/approvals/inbox", params={"page": 1, "size": 1})
            page_2 = await client.get("/api/v1/approvals/inbox", params={"page": 2, "size": 1})

        assert whole.json()["total"] == 2
        assert {i["id"] for i in whole.json()["items"]} == {str(request_a), str(request_b)}

        assert page_1.json()["total"] == 2
        assert page_2.json()["total"] == 2
        assert len(page_1.json()["items"]) == 1
        assert len(page_2.json()["items"]) == 1
        paged_ids = {page_1.json()["items"][0]["id"], page_2.json()["items"][0]["id"]}
        assert paged_ids == {str(request_a), str(request_b)}


class TestRepointedThenEscalatedStepDoesNotDoubleCount:
    """Regression (slice-4 review): delegation re-pointing inserts its successor at the *same*
    `level` as the step it replaces (ADR-0006 §5 — re-pointing is not a rung,
    `tests/test_delegations.py`'s own `assert successor.level == original.level`), so a step that
    is re-pointed and *then* escalates leaves **two** rows at `level - 1` for the request: the
    original, re-pointed-away step (`outcome = 'delegated'`) and the re-pointed successor that
    actually let the SLA lapse (`outcome = 'escalated'`). Before this fix, `_INBOX_CANDIDATES_CTE`'s
    predecessor self-join (`prev.level = s.level - 1`, with no outcome filter) matched both, so the
    same open step's own `UNION` branch produced two tuples that differed only in
    `predecessor_approver_employee_id` — `UNION` cannot dedup them, and the request appeared twice
    in the caller's inbox with two different "from" names for what is genuinely one pending
    request. `app/api/routes.py`'s request-detail predecessor lookup had the identical bug, from
    the identical cause, on its own `next(...)` scan of the loaded step history."""

    async def test_the_inbox_and_the_detail_chip_both_name_the_step_that_actually_escalated(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, delegate, director = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, director, display_name="Director Dee")
            await seed_person(
                session, org_id, manager, reporting_manager_id=director, display_name="Manager Em"
            )
            await seed_person(session, org_id, delegate, display_name="Delegate Del")
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            request, _step = await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        async with session.begin():
            _delegation, repointed_count = await services.create_delegation(
                session,
                organization_id=org_id,
                delegator_employee_id=manager,
                delegate_employee_id=delegate,
                starts_at=now - timedelta(minutes=1),
                ends_at=now + timedelta(days=1),
                created_by_employee_id=manager,
            )
        assert repointed_count == 1, "setup didn't actually re-point the step — test is unsound"

        # The re-pointed step (now held by `delegate`, acting for `manager`) is the one whose
        # `due_at` (carried over unchanged, ADR-0006 §5) is already overdue.
        await _escalate(session, org_id, request.id)

        principal = principal_for(org_id, director, [DECIDE_OWN])
        with as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")
        assert response.status_code == 200
        body = response.json()
        # The bug this regresses: `total` read 2 here — one row per level-1 predecessor the
        # unfiltered self-join matched — for what is one pending request.
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(request.id)
        assert item["inbox_reason"] == "escalated_to_me"
        # The predecessor that actually escalated the request was the *re-pointed* step (held by
        # `delegate`), not the original, re-pointed-away one (`manager`) — `outcome = 'escalated'`
        # on the self-join is what picks the right one.
        assert item["from_name"] == "Delegate Del"

        # `GET /requests/{id}` needs `request.read`, not `decide` — the director reaches OWN
        # through `caller_is_on_any_step` (they are the current open step's approver).
        reader = principal_for(org_id, director, [DECIDE_OWN, READ_OWN])
        with as_principal(reader):
            detail = await client.get(f"/api/v1/approvals/requests/{request.id}")
        assert detail.status_code == 200
        # Same fix, exercised through `_build_detail`'s own predecessor lookup rather than the
        # inbox's self-join — the two must agree.
        assert detail.json()["from_name"] == "Delegate Del"
