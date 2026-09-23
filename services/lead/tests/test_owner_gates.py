"""OWN-scope enforcement on `owner_id` itself, not just on which leads a caller may list or
touch: an OWN-scoped rep may capture or reassign a lead only to themselves, and unassigning a
lead (`owner_id: null`) is an ORG-only move regardless of scope on the lead being touched,
because it goes a step further than "reassign to yourself" that OWN scope was never meant to
cover. `test_authz.py` already covers the read/list and object-level halves of scope; this
file is specifically the `owner_id` write path `create_lead`/`update_lead` gate.
"""

from __future__ import annotations

import uuid

import pytest

from app import services
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.errors import ForbiddenProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


class TestCreateLeadOwnerScope:
    async def test_an_own_scoped_caller_may_create_a_lead_owned_by_themselves(
        self, session, org_id
    ) -> None:
        me = uuid.uuid4()
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        async with session.begin():
            lead = await services.create_lead(
                session,
                ctx,
                full_name="Self-assigned",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=me,
                notes="",
            )
        assert lead.owner_id == me

    async def test_an_own_scoped_caller_cannot_create_a_lead_owned_by_someone_else(
        self, session, org_id
    ) -> None:
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.create_lead(
                    session,
                    ctx,
                    full_name="Sneaky",
                    company=None,
                    email=None,
                    phone=None,
                    source="manual",
                    owner_id=someone_else,
                    notes="",
                )

    async def test_an_org_scoped_caller_may_create_a_lead_owned_by_anyone(
        self, session, org_id
    ) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        owner = uuid.uuid4()
        async with session.begin():
            lead = await services.create_lead(
                session,
                ctx,
                full_name="Assigned by an admin",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=owner,
                notes="",
            )
        assert lead.owner_id == owner


class TestUpdateLeadOwnerScope:
    async def test_an_own_scoped_caller_may_reassign_a_lead_to_themselves(
        self, session, org_id
    ) -> None:
        """`update_lead` now does its own object-level check against the lead's *current*
        owner (`services.update_lead`'s own row-locked fetch, not the route's) before it ever
        looks at what the patch is trying to change — so the only lead an OWN-scoped caller
        can reach at all is one they already own; an unowned lead is ORG's to hand out, never
        OWN's to claim by patching it. This proves the *new*-owner-id check still accepts
        "myself" once that gate is past, not that an unowned lead is up for grabs."""
        me = uuid.uuid4()
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Already mine",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=me,
                notes="",
            )

        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        async with session.begin():
            updated = await services.update_lead(session, own, lead.id, LeadPatch(owner_id=me))
        assert updated.owner_id == me

    async def test_an_own_scoped_caller_cannot_reach_an_unowned_lead_at_all(
        self, session, org_id
    ) -> None:
        me = uuid.uuid4()
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Up for grabs",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=None,
                notes="",
            )

        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.update_lead(session, own, lead.id, LeadPatch(owner_id=me))

    async def test_an_own_scoped_caller_cannot_reassign_a_lead_to_someone_else(
        self, session, org_id
    ) -> None:
        me = uuid.uuid4()
        someone_else = uuid.uuid4()
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Contested",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=me,
                notes="",
            )

        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.update_lead(session, own, lead.id, LeadPatch(owner_id=someone_else))

    async def test_an_own_scoped_caller_cannot_unassign_a_lead(self, session, org_id) -> None:
        me = uuid.uuid4()
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Owned",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=me,
                notes="",
            )

        own = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=me)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.update_lead(session, own, lead.id, LeadPatch(owner_id=None))

    async def test_an_org_scoped_caller_can_unassign_a_lead(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Owned",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=uuid.uuid4(),
                notes="",
            )

        async with session.begin():
            updated = await services.update_lead(session, write, lead.id, LeadPatch(owner_id=None))
        assert updated.owner_id is None
