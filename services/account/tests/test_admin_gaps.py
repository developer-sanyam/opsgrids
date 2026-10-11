"""The core CRUD audit's backend gaps: the caller's own grants, the invitation/role-assignment
privilege-escalation fix, and org-unit/location/role administration the dashboard has nowhere
to call yet.

Cross-tenant isolation for the three new write routes (`PATCH /org-units/{id}`,
`PATCH /locations/{id}`, `DELETE /roles/{id}`) is **not** re-proven here: they are mapped into
`test_cross_tenant_sweep.py`'s generated sweep, which already asserts "another organization's id
answers 404, not 403, to a caller holding every permission" for every write route in the service.
What is proven here instead is the half that sweep cannot cover — a caller who holds the *wrong*
permission gets 403 — and the business rules each new write enforces.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.models import Membership, Organization, Role
from app.schemas import (
    GrantIn,
    InvitationIn,
    LocationIn,
    LocationPatch,
    OrgUnitIn,
    OrgUnitPatch,
    RoleIn,
)
from platform_core.auth import Scope, get_principal
from platform_core.cache import permission_version_key
from platform_core.errors import BusinessRuleProblem, ConflictProblem, ForbiddenProblem
from tests.helpers import FakeRedis, principal_for
from tests.helpers import ctx_for as _ctx_for

pytestmark = pytest.mark.integration


def ctx_for(org, membership, permission, scope, *, locations=None):
    return _ctx_for(org.id, membership, permission, scope, locations=locations)


async def _owner(session: AsyncSession, org: Organization) -> Membership:
    async with session.begin():
        owner = await services.add_member(
            session,
            org_id=org.id,
            email="owner@example.com",
            full_name="Olivia Owner",
            actor_user_id=None,
            role_codes=["ORG_OWNER"],
        )
        owner.is_owner = True
    return owner


class _AsPrincipal:
    """Override `get_principal` for the duration of one `async with` block."""

    def __init__(self, principal) -> None:
        self._principal = principal

    async def __aenter__(self) -> None:
        app.dependency_overrides[get_principal] = lambda: self._principal

    async def __aexit__(self, *exc: object) -> None:
        app.dependency_overrides.pop(get_principal, None)


class TestMyGrants:
    """`GET /api/v1/members/me/grants` — no permission code, own membership only."""

    async def test_a_member_reads_their_own_grants(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        manager = people["manager"]
        principal = principal_for(org.id, manager, ["core.identity.membership.write:ORG"])

        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/members/me/grants")

        assert response.status_code == 200
        body = response.json()
        assert body["membership_id"] == str(manager.id)
        assert body["grants"], "an ORG_ADMIN with no grants means the role template never applied"
        assert all(":" in g for g in body["grants"])

    async def test_a_suspended_membership_gets_an_empty_list_not_a_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        report = people["report"]
        report_id = report.id
        async with session.begin():
            await services.revoke_member(
                session,
                ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG),
                report,
            )

        principal = principal_for(org.id, report, ["core.profile.self.read:OWN"])
        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/members/me/grants")

        assert response.status_code == 200
        body = response.json()
        assert body["membership_id"] == str(report_id)
        assert body["grants"] == []

    async def test_pv_is_carried_when_redis_has_a_version(
        self,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
        redis: FakeRedis,
    ) -> None:
        redis.store[permission_version_key(str(org.id))] = "3"
        principal = principal_for(org.id, people["manager"], ["core.identity.membership.write:ORG"])

        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/members/me/grants")

        assert response.json()["pv"] == 3

    async def test_pv_is_omitted_when_redis_has_never_been_bumped(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], ["core.identity.membership.write:ORG"])

        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/members/me/grants")

        assert "pv" not in response.json()

    async def test_a_membership_from_another_organization_is_not_found(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """A token claiming *this* organization but naming a membership that only exists in
        somebody else's must not resolve — there is no way through this route to read
        another member's grants, whatever id is put in the claim."""
        from app.schemas import OrganizationIn

        async with admin_session.begin():
            other_org = await services.create_organization(
                admin_session, OrganizationIn(name="Other Co", slug="otherco"), actor_user_id=None
            )
            stranger = await services.add_member(
                admin_session,
                org_id=other_org.id,
                email="stranger@example.com",
                full_name="Sam Stranger",
                actor_user_id=None,
                role_codes=["ORG_OWNER"],
            )

        principal = principal_for(org.id, people["manager"], ["core.identity.membership.write:ORG"])
        principal = principal.model_copy(update={"membership_id": stranger.id})

        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/members/me/grants")

        assert response.status_code == 404


class TestPrivilegeEscalation:
    """`POST /invitations` and `PUT /members/{id}/roles` refuse a role the caller could not
    grant themselves."""

    async def test_an_org_admin_cannot_invite_somebody_as_owner(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        with pytest.raises(ForbiddenProblem, match="ORG_OWNER"):
            async with session.begin():
                await services.invite(
                    session,
                    ctx,
                    InvitationIn(email="escalate@example.com", role_codes=["ORG_OWNER"]),
                )

    async def test_an_org_admin_can_still_invite_a_member(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        async with session.begin():
            invitation, _raw = await services.invite(
                session, ctx, InvitationIn(email="fine@example.com", role_codes=["MEMBER"])
            )
        assert invitation.role_codes == ["MEMBER"]

    async def test_role_write_alone_is_not_enough_to_promote_somebody_to_owner(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """`PUT /members/{id}/roles` has the same hole, checked the same way."""
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        with pytest.raises(ForbiddenProblem, match="ORG_OWNER"):
            async with session.begin():
                await session.refresh(people["report"], ["roles"])
                await services.set_member_roles(session, ctx, people["report"], ["ORG_OWNER"])

    async def test_role_write_cannot_strip_the_owner(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Removing the owner is refused; emptying their roles is the same thing by another door,
        and would leave the account with nobody able to administer roles or billing."""
        from platform_core.errors import BusinessRuleProblem

        owner = await _owner(session, org)
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        with pytest.raises(BusinessRuleProblem, match="owner"):
            async with session.begin():
                await session.refresh(owner, ["roles"])
                await services.set_member_roles(session, ctx, owner, [])

    async def test_an_owner_may_assign_any_role(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        owner = await _owner(session, org)
        ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        async with session.begin():
            await session.refresh(people["report"], ["roles"])
            updated = await services.set_member_roles(session, ctx, people["report"], ["ORG_ADMIN"])
        assert sorted(mr.role.code for mr in updated.roles) == ["ORG_ADMIN"]

    async def test_the_invitation_route_answers_403_with_a_readable_detail(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], ["core.identity.membership.write:ORG"])
        async with _AsPrincipal(principal):
            response = await client.post(
                "/api/v1/invitations",
                json={"email": "escalate2@example.com", "role_codes": ["ORG_OWNER"]},
            )

        assert response.status_code == 403
        assert "ORG_OWNER" in response.json()["detail"]

    async def test_the_roles_route_answers_403_for_the_same_escalation(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], ["core.identity.role.write:ORG"])
        outsider_id = people["outsider"].id

        async with _AsPrincipal(principal):
            response = await client.put(
                f"/api/v1/members/{outsider_id}/roles", json={"role_codes": ["ORG_OWNER"]}
            )

        assert response.status_code == 403


class TestRoleGrantCeiling:
    """`PUT /roles/{id}` and `POST /roles` are the sibling hole `_resolve_assignable_roles`
    closed for `invite`/`set_member_roles`: only holding `core.identity.role.write` let a
    caller edit *any* non-system role — including one they already hold — into carrying a
    permission they could never have assigned through those two routes, because
    `_replace_grants` checked a grant's `max_scope` and nothing about the caller
    (SECURITY-FIX M3). `_replace_grants` now shares `_caller_grant_ceiling` with
    `_resolve_assignable_roles`, so there is one definition of "the caller holds this code
    at this scope or wider" for every way a grant reaches a role.
    """

    async def _editor(
        self,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        owner_ctx,
        *,
        grants: list[GrantIn],
    ) -> tuple[Membership, Role]:
        """A membership holding a fresh custom role granting exactly `grants`, and the role."""
        async with session.begin():
            role = await services.create_role(
                session, owner_ctx, RoleIn(code="ROLE_EDITOR", name="Role editor", grants=grants)
            )
        async with session.begin():
            await session.refresh(people["outsider"], ["roles"])
            await services.set_member_roles(session, owner_ctx, people["outsider"], [role.code])
        return people["outsider"], role

    async def test_cannot_add_a_permission_it_does_not_hold(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        editor, role = await self._editor(
            session,
            org,
            people,
            owner_ctx,
            grants=[GrantIn(permission_code="core.identity.role.write", scope="ORG")],
        )
        editor_ctx = ctx_for(org, editor, "core.identity.role.write", Scope.ORG)

        with pytest.raises(ForbiddenProblem, match=re.escape("core.billing.invoice.read")):
            async with session.begin():
                await session.refresh(role, ["grants"])
                await services.replace_role_grants(
                    session,
                    editor_ctx,
                    role,
                    RoleIn(
                        code=role.code,
                        name=role.name,
                        grants=[
                            GrantIn(permission_code="core.identity.role.write", scope="ORG"),
                            GrantIn(permission_code="core.billing.invoice.read", scope="ORG"),
                        ],
                    ),
                )

        # Refused, not applied: the billing grant never reached the role.
        async with session.begin():
            await session.refresh(role, ["grants"])
            codes = {g.permission.code for g in role.grants}
        assert "core.billing.invoice.read" not in codes

    async def test_cannot_widen_a_scope_beyond_what_it_holds(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        editor, role = await self._editor(
            session,
            org,
            people,
            owner_ctx,
            grants=[
                GrantIn(permission_code="core.identity.role.write", scope="ORG"),
                GrantIn(permission_code="core.identity.membership.write", scope="TEAM"),
            ],
        )
        editor_ctx = ctx_for(org, editor, "core.identity.role.write", Scope.ORG)

        with pytest.raises(ForbiddenProblem, match=re.escape("core.identity.membership.write")):
            async with session.begin():
                await session.refresh(role, ["grants"])
                await services.replace_role_grants(
                    session,
                    editor_ctx,
                    role,
                    RoleIn(
                        code=role.code,
                        name=role.name,
                        grants=[
                            GrantIn(permission_code="core.identity.role.write", scope="ORG"),
                            GrantIn(permission_code="core.identity.membership.write", scope="ORG"),
                        ],
                    ),
                )

    async def test_can_still_edit_within_what_it_holds(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        editor, role = await self._editor(
            session,
            org,
            people,
            owner_ctx,
            grants=[
                GrantIn(permission_code="core.identity.role.write", scope="ORG"),
                GrantIn(permission_code="core.identity.membership.write", scope="TEAM"),
            ],
        )
        editor_ctx = ctx_for(org, editor, "core.identity.role.write", Scope.ORG)

        async with session.begin():
            await session.refresh(role, ["grants"])
            updated = await services.replace_role_grants(
                session,
                editor_ctx,
                role,
                RoleIn(
                    code=role.code,
                    name="Role editor, renamed",
                    grants=[
                        GrantIn(permission_code="core.identity.role.write", scope="ORG"),
                        GrantIn(permission_code="core.identity.membership.write", scope="TEAM"),
                    ],
                ),
            )
        assert updated.name == "Role editor, renamed"
        assert {g.permission.code for g in updated.grants} == {
            "core.identity.role.write",
            "core.identity.membership.write",
        }

    async def test_the_owner_can_still_build_an_arbitrary_role(
        self, session: AsyncSession, org: Organization
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session,
                owner_ctx,
                RoleIn(
                    code="FINANCE",
                    name="Finance",
                    grants=[
                        GrantIn(permission_code="core.billing.invoice.read", scope="ORG"),
                        GrantIn(permission_code="core.billing.credit.read", scope="ORG"),
                    ],
                ),
            )
        assert {g.permission.code for g in role.grants} == {
            "core.billing.invoice.read",
            "core.billing.credit.read",
        }

    async def test_creating_a_role_is_bounded_the_same_way(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        editor, _role = await self._editor(
            session,
            org,
            people,
            owner_ctx,
            grants=[GrantIn(permission_code="core.identity.role.write", scope="ORG")],
        )
        editor_ctx = ctx_for(org, editor, "core.identity.role.write", Scope.ORG)

        with pytest.raises(ForbiddenProblem, match=re.escape("core.billing.invoice.read")):
            async with session.begin():
                await services.create_role(
                    session,
                    editor_ctx,
                    RoleIn(
                        code="NEW_ROLE",
                        name="New role",
                        grants=[GrantIn(permission_code="core.billing.invoice.read", scope="ORG")],
                    ),
                )

    async def test_the_route_answers_403_naming_the_permission(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        owner = await _owner(session, org)
        owner_ctx = ctx_for(org, owner, "core.identity.role.write", Scope.ORG)
        editor, role = await self._editor(
            session,
            org,
            people,
            owner_ctx,
            grants=[GrantIn(permission_code="core.identity.role.write", scope="ORG")],
        )
        principal = principal_for(org.id, editor, ["core.identity.role.write:ORG"])

        async with _AsPrincipal(principal):
            response = await client.put(
                f"/api/v1/roles/{role.id}",
                json={
                    "code": role.code,
                    "name": role.name,
                    "grants": [
                        {"permission_code": "core.identity.role.write", "scope": "ORG"},
                        {"permission_code": "core.billing.invoice.read", "scope": "ORG"},
                    ],
                },
            )

        assert response.status_code == 403
        assert "core.billing.invoice.read" in response.json()["detail"]


class TestOrgUnitPatch:
    async def test_rename_and_recategorise(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            unit = await services.create_org_unit(session, ctx, OrgUnitIn(name="Ops", code="OPS"))
        async with session.begin():
            updated = await services.update_org_unit(
                session, ctx, unit, OrgUnitPatch(name="Operations", kind="division")
            )
        assert updated.name == "Operations"
        assert updated.kind == "division"
        assert updated.code == "OPS", "code is not in OrgUnitPatch and must never change"

    async def test_deactivating_a_unit_with_an_active_child_is_refused(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            parent = await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Delivery", code="DEL2")
            )
        async with session.begin():
            await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Platform", code="PLT2", parent_id=parent.id)
            )
        with pytest.raises(BusinessRuleProblem, match="sub-departments"):
            async with session.begin():
                await services.update_org_unit(session, ctx, parent, OrgUnitPatch(is_active=False))

    async def test_deactivating_a_unit_with_active_members_is_refused(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            unit = await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Support", code="SUP")
            )
            people["report"].org_unit_id = unit.id
        with pytest.raises(BusinessRuleProblem, match="members assigned"):
            async with session.begin():
                await services.update_org_unit(session, ctx, unit, OrgUnitPatch(is_active=False))

    async def test_deactivating_an_empty_unit_succeeds(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            unit = await services.create_org_unit(session, ctx, OrgUnitIn(name="Temp", code="TMP"))
        async with session.begin():
            updated = await services.update_org_unit(
                session, ctx, unit, OrgUnitPatch(is_active=False)
            )
        assert updated.is_active is False

    async def test_a_member_without_the_grant_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            unit = await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Guarded", code="GRD")
            )
        unit_id = unit.id

        principal = principal_for(org.id, people["report"], ["core.org.unit.read:ORG"])
        async with _AsPrincipal(principal):
            response = await client.patch(f"/api/v1/org-units/{unit_id}", json={"name": "Hijacked"})

        assert response.status_code == 403


class TestLocationPatch:
    async def test_rename_and_update_address(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.location.write", Scope.ORG)
        async with session.begin():
            location = await services.create_location(
                session, ctx, LocationIn(name="Pune", code="PNQ")
            )
        async with session.begin():
            updated = await services.update_location(
                session, ctx, location, LocationPatch(name="Pune HQ", address={"city": "Pune"})
            )
        assert updated.name == "Pune HQ"
        assert updated.address == {"city": "Pune"}
        assert updated.code == "PNQ", "code is not in LocationPatch and must never change"

    async def test_deactivate(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.location.write", Scope.ORG)
        async with session.begin():
            location = await services.create_location(
                session, ctx, LocationIn(name="Delhi", code="DEL3")
            )
        async with session.begin():
            updated = await services.update_location(
                session, ctx, location, LocationPatch(is_active=False)
            )
        assert updated.is_active is False

    async def test_a_member_without_the_grant_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.location.write", Scope.ORG)
        async with session.begin():
            location = await services.create_location(
                session, ctx, LocationIn(name="Chennai", code="MAA")
            )
        location_id = location.id

        principal = principal_for(org.id, people["report"], ["core.org.location.read:ORG"])
        async with _AsPrincipal(principal):
            response = await client.patch(f"/api/v1/locations/{location_id}", json={"name": "Nope"})

        assert response.status_code == 403


class TestRoleRenameAndDelete:
    async def test_rename_applies_name_and_description_and_keeps_code(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session, ctx, RoleIn(code="AUDITOR", name="Auditor", grants=[])
            )
        async with session.begin():
            await session.refresh(role, ["grants"])
            updated = await services.replace_role_grants(
                session,
                ctx,
                role,
                RoleIn(
                    code="IGNORED-CODE",
                    name="Senior Auditor",
                    description="Reviews everything",
                    grants=[],
                ),
            )
        assert updated.name == "Senior Auditor"
        assert updated.description == "Reviews everything"
        assert updated.code == "AUDITOR", "code is ignored on PUT /roles/{id} and never changes"

    async def test_a_system_role_cannot_be_deleted(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                member_role = await selectors.get_role_by_code(session, org.id, "MEMBER")
                assert member_role is not None
                await services.delete_role(session, ctx, member_role)

    async def test_a_role_still_held_by_a_membership_cannot_be_deleted(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session, ctx, RoleIn(code="HELD", name="Held", grants=[])
            )
        async with session.begin():
            await session.refresh(people["report"], ["roles"])
            await services.set_member_roles(session, ctx, people["report"], ["HELD"])
        with pytest.raises(ConflictProblem, match="held by at least one member"):
            async with session.begin():
                await session.refresh(role, ["grants"])
                await services.delete_role(session, ctx, role)

    async def test_a_role_named_on_a_pending_invitation_cannot_be_deleted(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        ctx_invite = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session, ctx, RoleIn(code="PROMISED", name="Promised", grants=[])
            )
        async with session.begin():
            await services.invite(
                session,
                ctx_invite,
                InvitationIn(email="waiting@example.com", role_codes=["PROMISED"]),
            )
        with pytest.raises(ConflictProblem, match="pending invitation"):
            async with session.begin():
                await session.refresh(role, ["grants"])
                await services.delete_role(session, ctx, role)

    async def test_an_unused_custom_role_can_be_deleted(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session, ctx, RoleIn(code="SPARE", name="Spare", grants=[])
            )
        role_id = role.id
        async with session.begin():
            await session.refresh(role, ["grants"])
            await services.delete_role(session, ctx, role)

        assert await selectors.get_role(session, org.id, role_id) is None

    async def test_a_member_without_the_grant_cannot_delete(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        async with session.begin():
            role = await services.create_role(
                session, ctx, RoleIn(code="TARGET", name="Target", grants=[])
            )
        role_id = role.id

        principal = principal_for(org.id, people["report"], ["core.identity.role.read:ORG"])
        async with _AsPrincipal(principal):
            response = await client.delete(f"/api/v1/roles/{role_id}")

        assert response.status_code == 403

        # It was refused, not deleted.
        assert await selectors.get_role(session, org.id, role_id) is not None
