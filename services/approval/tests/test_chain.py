"""The chain walk (ADR-0006 §5, data model §4.3 / §2 pattern 12): an exited manager, a cycle,
depth > 32, and the endpoint's negative-authz case (ADR-0003 §3).
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.services import upsert_hierarchy_node
from tests.helpers import READ, principal_for

pytestmark = pytest.mark.integration


async def _seed(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    reporting_manager_id: uuid.UUID | None,
    status: str = "active",
    display_name: str = "Someone",
) -> None:
    await upsert_hierarchy_node(
        session,
        organization_id=org_id,
        employee_id=employee_id,
        reporting_manager_id=reporting_manager_id,
        dotted_line_manager_id=None,
        status=status,
        display_name=display_name,
        employee_no=f"EMP{str(employee_id)[:8]}",
        location_code="MUM",
        source_version=1,
        source_occurred_at=datetime.now(UTC),
    )


class TestWalkChain:
    async def test_a_simple_chain_walks_upward_in_order(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(
                session, org_id, director, reporting_manager_id=None, display_name="Director"
            )
            await _seed(
                session, org_id, manager, reporting_manager_id=director, display_name="Manager"
            )
            await _seed(
                session, org_id, requester, reporting_manager_id=manager, display_name="Requester"
            )

        walk = await selectors.walk_chain(session, org_id, requester)
        assert not walk.cycle_detected
        assert not walk.depth_exceeded
        assert [r.employee_id for r in walk.rungs] == [requester, manager, director]

        displayed = domain.skip_flags(walk.rungs, requester)
        assert [(r.employee_id, skipped) for r, skipped in displayed] == [
            (manager, False),
            (director, False),
        ]
        assert domain.pick_approver(walk.rungs, requester).employee_id == manager

    async def test_an_exited_manager_is_walked_past_and_marked_skipped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, exited_manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(
                session, org_id, director, reporting_manager_id=None, display_name="Director"
            )
            await _seed(
                session,
                org_id,
                exited_manager,
                reporting_manager_id=director,
                status="exited",
                display_name="Exited Manager",
            )
            await _seed(
                session,
                org_id,
                requester,
                reporting_manager_id=exited_manager,
                display_name="Requester",
            )

        walk = await selectors.walk_chain(session, org_id, requester)
        displayed = domain.skip_flags(walk.rungs, requester)
        assert [(r.employee_id, skipped) for r, skipped in displayed] == [
            (exited_manager, True),
            (director, False),
        ]
        # The walk does not stop at the exited node — it reaches the director beyond it.
        assert domain.pick_approver(walk.rungs, requester).employee_id == director

    async def test_an_exited_rung_beyond_the_picked_approver_is_not_marked_skipped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`pick_approver` stops at the first non-exited, non-requester rung and never looks
        further — so a rung that exits *above* the one actually picked was never walked past, and
        `skip_flags` must not claim otherwise (its own docstring: "a rung is skipped here if and
        only if pick_approver would have passed over it")."""
        requester, manager, exited_director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(
                session,
                org_id,
                exited_director,
                reporting_manager_id=None,
                status="exited",
                display_name="Exited Director",
            )
            await _seed(
                session,
                org_id,
                manager,
                reporting_manager_id=exited_director,
                display_name="Manager",
            )
            await _seed(
                session, org_id, requester, reporting_manager_id=manager, display_name="Requester"
            )

        walk = await selectors.walk_chain(session, org_id, requester)
        displayed = domain.skip_flags(walk.rungs, requester)
        assert [(r.employee_id, skipped) for r, skipped in displayed] == [
            (manager, False),
            (exited_director, False),
        ]
        assert domain.pick_approver(walk.rungs, requester).employee_id == manager

    async def test_a_cycle_terminates_the_walk_instead_of_looping(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        alpha, beta = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            # alpha -> beta -> alpha: a two-node cycle. The copy can be transiently cyclic even
            # though the source refuses cycles (ADR-0006 §5), so the CTE's CYCLE clause is what
            # has to catch it, not application discipline.
            await _seed(session, org_id, alpha, reporting_manager_id=beta, display_name="Alpha")
            await _seed(session, org_id, beta, reporting_manager_id=alpha, display_name="Beta")

        walk = await selectors.walk_chain(session, org_id, alpha)
        assert walk.cycle_detected is True
        assert walk.depth_exceeded is False

    async def test_a_chain_deeper_than_32_rungs_is_reported_as_depth_exceeded(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The walk's own row (the requester, depth 1) is not a rung, so the cap of
        `domain.MAX_CHAIN_DEPTH` (32) rungs above it means the walk returns at most 33 rows —
        matching both this module's docstring and employee-service's own cycle check, which
        accepts a 32-rung proposed chain and refuses a 33-rung one (data model §4.3)."""
        # 35 employees in a straight line: employee[0] reports to employee[1], ... employee[33]
        # reports to employee[34]. Walking from employee[0] needs 34 rungs, past the 32-rung cap.
        chain = [uuid.uuid4() for _ in range(35)]
        async with session.begin():
            await _seed(session, org_id, chain[-1], reporting_manager_id=None, display_name="Top")
            for lower, upper in itertools.pairwise(chain):
                await _seed(session, org_id, lower, reporting_manager_id=upper, display_name="Rung")

        walk = await selectors.walk_chain(session, org_id, chain[0])
        assert walk.cycle_detected is False
        assert walk.depth_exceeded is True
        assert len(walk.rungs) == 33  # the requester's own row, plus 32 rungs

    async def test_the_32nd_rung_pointing_at_a_manager_the_copy_has_not_seen_is_not_depth_exceeded(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A dangling pointer at exactly the depth cap must not be mistaken for a genuinely
        deeper chain: `HierarchyNode`'s docstring calls a manager the copy has not seen yet a
        normal, expected state (an `onboarded` event consumed out of order), not an error. The
        CTE alone cannot tell the two apart — both stop the walk at the cap — so the fix has to
        confirm the pointer's target actually resolves before calling it depth-exceeded."""
        # 33 employees in a straight line (the requester plus 32 rungs — exactly the cap), and
        # the 32nd rung names a manager nobody ever seeded. The walk stops at exactly the cap
        # either way; this time there was nothing genuinely beyond it.
        chain = [uuid.uuid4() for _ in range(33)]
        ghost_manager = uuid.uuid4()
        async with session.begin():
            await _seed(
                session,
                org_id,
                chain[-1],
                reporting_manager_id=ghost_manager,
                display_name="Top (dangling)",
            )
            for lower, upper in itertools.pairwise(chain):
                await _seed(session, org_id, lower, reporting_manager_id=upper, display_name="Rung")

        walk = await selectors.walk_chain(session, org_id, chain[0])
        assert walk.cycle_detected is False
        assert walk.depth_exceeded is False
        assert len(walk.rungs) == 33


class TestChainEndpointAuthz:
    async def test_an_own_scoped_caller_can_read_their_own_chain(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(session, org_id, manager, reporting_manager_id=None, display_name="Manager")
            await _seed(
                session, org_id, requester, reporting_manager_id=manager, display_name="Requester"
            )

        principal = principal_for(org_id, requester, [f"{READ}:OWN"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{requester}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 200
        body = response.json()
        assert body["employee_id"] == str(requester)
        assert body["chain"][0]["employee_id"] == str(manager)
        assert body["chain"][-1]["is_fallback"] is True

    async def test_an_own_scoped_caller_cannot_read_someone_elses_chain(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The negative-authz case ADR-0003 §3 requires per endpoint: a caller outside scope is
        refused, not silently served someone else's chain."""
        from app.main import app
        from platform_core.auth import get_principal

        caller, someone_else = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(session, org_id, someone_else, reporting_manager_id=None)

        principal = principal_for(org_id, caller, [f"{READ}:OWN"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{someone_else}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 403

    async def test_an_org_scoped_caller_can_read_any_employees_chain(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        caller, someone_else = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(session, org_id, someone_else, reporting_manager_id=None)

        principal = principal_for(org_id, caller, [f"{READ}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{someone_else}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 200

    async def test_a_team_scoped_caller_can_read_a_direct_reports_chain(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`core.approval.request.read`'s `max_scope` is `ORG`, and the scope ladder
        (`OWN < TEAM < LOCATION < ORG`, ADR-0000 §6) is a single ceiling on that field — there is
        no way to grant ORG while making TEAM or LOCATION ungrantable, since both rank below it.
        So a TEAM grant is legal here, and the endpoint has to honour it for real rather than
        refuse every caller who holds one: `assert_can_touch` needs the *subject's* manager, not
        the caller's, which is why the handler fetches the subject's hierarchy row first."""
        from app.main import app
        from platform_core.auth import get_principal

        manager, report = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _seed(session, org_id, manager, reporting_manager_id=None, display_name="Manager")
            await _seed(
                session, org_id, report, reporting_manager_id=manager, display_name="Report"
            )

        principal = principal_for(org_id, manager, [f"{READ}:TEAM"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{report}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 200
        assert response.json()["employee_id"] == str(report)

    async def test_a_team_scoped_caller_cannot_read_a_non_reports_chain(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        caller, someone_elses_report = uuid.uuid4(), uuid.uuid4()
        other_manager = uuid.uuid4()
        async with session.begin():
            await _seed(session, org_id, other_manager, reporting_manager_id=None)
            await _seed(session, org_id, someone_elses_report, reporting_manager_id=other_manager)

        principal = principal_for(org_id, caller, [f"{READ}:TEAM"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{someone_elses_report}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 403

    async def test_a_team_scoped_caller_is_refused_when_the_subject_has_no_hierarchy_row(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A subject the copy has not seen yet has no manager to compare against — TEAM must
        fail closed, not treat "unknown" as "mine"."""
        from app.main import app
        from platform_core.auth import get_principal

        manager, unknown_subject = uuid.uuid4(), uuid.uuid4()
        principal = principal_for(org_id, manager, [f"{READ}:TEAM"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{unknown_subject}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 403

    async def test_a_location_scoped_caller_can_read_a_chain_at_their_own_location(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        caller, subject = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await upsert_hierarchy_node(
                session,
                organization_id=org_id,
                employee_id=subject,
                reporting_manager_id=None,
                dotted_line_manager_id=None,
                status="active",
                display_name="Subject",
                employee_no="EMP0001",
                location_code="BLR",
                source_version=1,
                source_occurred_at=datetime.now(UTC),
            )

        principal = principal_for(org_id, caller, [f"{READ}:LOCATION"], locations=["BLR"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{subject}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 200
        assert response.json()["employee_id"] == str(subject)

    async def test_a_location_scoped_caller_cannot_read_a_chain_at_another_location(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        caller, subject = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await upsert_hierarchy_node(
                session,
                organization_id=org_id,
                employee_id=subject,
                reporting_manager_id=None,
                dotted_line_manager_id=None,
                status="active",
                display_name="Subject",
                employee_no="EMP0002",
                location_code="MUM",
                source_version=1,
                source_occurred_at=datetime.now(UTC),
            )

        principal = principal_for(org_id, caller, [f"{READ}:LOCATION"], locations=["BLR"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{subject}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 403

    async def test_no_permission_at_all_is_refused(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        caller = uuid.uuid4()
        principal = principal_for(org_id, caller, [])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/approvals/chain/{caller}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 403
