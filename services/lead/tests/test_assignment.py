"""Round-robin assignment, end to end against the real write path — `test_domain.py` already
covers `domain.next_owner` in isolation; these prove `services.create_lead` and
`services.set_assignment_rule` wire it up correctly, including the cursor surviving across
separate transactions the way separate HTTP requests would see it.
"""

from __future__ import annotations

import uuid

import pytest

from app import selectors, services
from platform_core.auth import Scope
from platform_core.errors import ForbiddenProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


async def _create(session, ctx, name: str, *, owner_id=None):
    return await services.create_lead(
        session,
        ctx,
        full_name=name,
        company=None,
        email=None,
        phone=None,
        source="form",
        owner_id=owner_id,
        notes="",
    )


class TestAssignmentRule:
    async def test_configuring_requires_org_scope(self, session, org_id) -> None:
        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=uuid.uuid4())
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.set_assignment_rule(
                    session, own, mode="round_robin", owner_ids=[uuid.uuid4()]
                )

    async def test_a_lead_created_with_no_rule_configured_gets_no_owner(
        self, session, org_id
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await _create(session, write, "Unassigned")
        assert lead.owner_id is None

    async def test_leads_rotate_through_the_configured_owners_in_order(
        self, session, org_id
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)

        assigned = []
        for i in range(5):
            async with session.begin():
                lead = await _create(session, write, f"Lead {i}")
            assigned.append(lead.owner_id)

        assert assigned == [owners[0], owners[1], owners[2], owners[0], owners[1]]

    async def test_an_explicit_owner_bypasses_the_rotation(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        explicit = uuid.uuid4()
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)
            lead = await _create(session, write, "Named owner", owner_id=explicit)
        assert lead.owner_id == explicit

        # The rotation did not advance for the explicitly-owned lead.
        rule = await selectors.get_assignment_rule(session, org_id)
        assert rule is not None
        assert rule.cursor == 0

    async def test_manual_mode_assigns_nobody(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.set_assignment_rule(
                session, write, mode="manual", owner_ids=[uuid.uuid4()]
            )
            lead = await _create(session, write, "Manual mode")
        assert lead.owner_id is None

    async def test_reconfiguring_resets_the_cursor(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owners = [uuid.uuid4(), uuid.uuid4()]
        async with session.begin():
            await services.set_assignment_rule(session, write, mode="round_robin", owner_ids=owners)
            await _create(session, write, "First")  # advances cursor to 1

        async with session.begin():
            rule = await services.set_assignment_rule(
                session, write, mode="round_robin", owner_ids=[uuid.uuid4(), uuid.uuid4()]
            )
        assert rule.cursor == 0
