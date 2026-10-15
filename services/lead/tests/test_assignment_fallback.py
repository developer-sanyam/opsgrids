"""Auto-assign falls back to the person capturing the lead (2026-10-15 customer retest).

A self-serve organization has one login: the owner, ORG-scoped, with no employee record and at
most one roster row (the one `PUT /customers/owners/me` makes at the membership id). No
round-robin is configured, so "Auto-assign" used to leave every lead unassigned -- and a lead
with no owner cannot be converted. These pin the fallback: when the rotation hands the lead to
nobody, the creator owns it, provided customer's directory confirms they are an active rep.
"""

from __future__ import annotations

import uuid

import pytest

from app import clients, services
from app.main import app
from platform_core.auth import Scope, get_principal
from platform_core.errors import ForbiddenProblem
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


async def _create(session, ctx, name: str, *, owner_id=None, fallback_owner_id=None):
    return await services.create_lead(
        session,
        ctx,
        full_name=name,
        company=None,
        email=None,
        phone=None,
        source="manual",
        owner_id=owner_id,
        fallback_owner_id=fallback_owner_id,
        notes="",
    )


def _directory(monkeypatch: pytest.MonkeyPatch, active: set[uuid.UUID]) -> list[list[uuid.UUID]]:
    """customer's `owners:validate`, answering from `active`; returns the calls it saw."""
    calls: list[list[uuid.UUID]] = []

    async def _validate(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
        calls.append(list(owner_ids))
        return [o for o in owner_ids if o not in active]

    monkeypatch.setattr(clients, "invalid_owner_ids", _validate)
    return calls


class TestCreatorFallback:
    async def test_no_rule_assigns_the_org_owner_by_membership_id(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        membership_id = owner.principal.membership_id
        assert membership_id is not None
        _directory(monkeypatch, {membership_id})

        async with session.begin():
            lead = await _create(session, owner, "Asha")

        assert lead.owner_id == membership_id

    async def test_no_rule_prefers_the_employee_link(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rep = uuid.uuid4()
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG, subject_ref=rep)
        _directory(monkeypatch, {rep})

        async with session.begin():
            lead = await _create(session, ctx, "Ravi")

        assert lead.owner_id == rep

    async def test_an_own_scoped_rep_gets_their_own_lead(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rep = uuid.uuid4()
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=rep)
        _directory(monkeypatch, {rep})

        async with session.begin():
            lead = await _create(session, ctx, "Meera")

        assert lead.owner_id == rep

    async def test_an_own_scoped_login_with_no_employee_link_is_not_given_a_membership_owner(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # OWN reads compare the employee link; a lead owned by the membership id would vanish
        # from the person who just captured it. Same rule as deal-service's `default_owner`.
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN)
        _directory(monkeypatch, {ctx.principal.membership_id})  # type: ignore[arg-type]

        async with session.begin():
            lead = await _create(session, ctx, "Kiran")

        assert lead.owner_id is None

    async def test_a_creator_who_is_not_an_active_rep_leaves_the_lead_unassigned(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        _directory(monkeypatch, set())

        async with session.begin():
            lead = await _create(session, owner, "Nobody")

        assert lead.owner_id is None

    async def test_an_unreachable_directory_still_captures_the_lead(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _down(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            raise clients.OwnersValidateUnavailable("down")

        monkeypatch.setattr(clients, "invalid_owner_ids", _down)
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

        async with session.begin():
            lead = await _create(session, owner, "Captured anyway")

        assert lead.owner_id is None

    async def test_the_named_fallback_is_used_when_the_rotation_is_empty(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The SPA names the login's own roster row -- which may be an older row with its own id,
        # matched by email -- so the creator still owns the lead.
        mine = uuid.uuid4()
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        _directory(monkeypatch, {mine})

        async with session.begin():
            lead = await _create(session, owner, "Named fallback", fallback_owner_id=mine)

        assert lead.owner_id == mine

    async def test_a_configured_rotation_wins_over_the_fallback(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rotation = [uuid.uuid4(), uuid.uuid4()]
        mine = uuid.uuid4()
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        _directory(monkeypatch, {*rotation, mine})

        async with session.begin():
            await services.set_assignment_rule(
                session, owner, mode="round_robin", owner_ids=rotation
            )
        async with session.begin():
            lead = await _create(session, owner, "Rotated", fallback_owner_id=mine)

        assert lead.owner_id == rotation[0]

    async def test_a_rotation_whose_reps_all_left_falls_back_to_the_creator(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gone = uuid.uuid4()
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        membership_id = owner.principal.membership_id
        _directory(monkeypatch, {gone, membership_id})  # type: ignore[arg-type]
        async with session.begin():
            await services.set_assignment_rule(session, owner, mode="round_robin", owner_ids=[gone])
        services._assignment_active_cache.clear()
        _directory(monkeypatch, {membership_id})  # type: ignore[arg-type]

        async with session.begin():
            lead = await _create(session, owner, "Orphaned rotation")

        assert lead.owner_id == membership_id

    async def test_manual_mode_still_assigns_nobody(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        _directory(monkeypatch, {owner.principal.membership_id})  # type: ignore[arg-type]
        async with session.begin():
            await services.set_assignment_rule(session, owner, mode="manual", owner_ids=[])
            lead = await _create(session, owner, "Manual")

        assert lead.owner_id is None

    async def test_an_own_scoped_rep_cannot_name_a_colleague_as_the_fallback(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rep, colleague = uuid.uuid4(), uuid.uuid4()
        ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.OWN, subject_ref=rep)
        _directory(monkeypatch, {rep, colleague})

        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await _create(session, ctx, "Not yours", fallback_owner_id=colleague)


class TestCreatorFallbackOverHttp:
    async def test_post_leads_with_no_owner_lands_on_the_creator(
        self, client, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        principal = principal_for(org_id, ["crm.lead.lead.write:ORG", "crm.lead.lead.read:ORG"])
        _directory(monkeypatch, {principal.membership_id})  # type: ignore[arg-type]
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post("/api/v1/leads", json={"full_name": "From the form"})
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 201, response.text
        assert response.json()["owner_id"] == str(principal.membership_id)
