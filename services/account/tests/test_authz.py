"""Authorization, against real PostgreSQL.

These are the tests that stand between a manager and somebody else's team. They are
written as *negative* assertions wherever possible — proving a call is refused is worth
more than proving it succeeds, because the success path is the one everybody exercises by
hand anyway.

Three of them exist because of specific defects in the system this platform replaces:

* `test_a_member_cannot_invite_or_remove_people` — every member holds the OWN grant for
  self-service, so a route guarded only by the permission *name* let anyone administer the
  organization.
* `test_scope_does_not_leak_through_the_detail_route` — filtering the list and forgetting
  the detail view.
* `test_location_scope_sees_only_its_own_sites` — object-level checks that returned early
  for LOCATION, letting a location-scoped caller reach anything they could name.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import Membership, Organization
from app.schemas import GrantIn, MemberPatch, OrgUnitIn, RoleIn
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, ForbiddenProblem
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

ALL_MEMBER_PERMS = [
    "core.identity.membership.read:ORG",
    "core.identity.membership.write:ORG",
]


def ctx_for(org, membership, permission, scope, *, locations=None):
    return _ctx_for(org.id, membership, permission, scope, locations=locations)


class TestOrganizationBootstrap:
    async def test_a_new_organization_gets_its_role_templates(
        self, session: AsyncSession, org: Organization
    ) -> None:
        roles = await selectors.list_roles(session, org.id)
        codes = {r.code for r in roles if r.organization_id == org.id}
        assert {"ORG_OWNER", "ORG_ADMIN", "MEMBER"} <= codes

    async def test_the_default_role_is_assigned_when_none_is_named(
        self, session: AsyncSession, org: Organization
    ) -> None:
        async with session.begin():
            member = await services.add_member(
                session,
                org_id=org.id,
                email="nobody@example.com",
                full_name="No Body",
                actor_user_id=None,
            )
        assert sorted(mr.role.code for mr in member.roles) == ["MEMBER"]

    async def test_a_taken_slug_is_refused(self, session: AsyncSession, org: Organization) -> None:
        from app.schemas import OrganizationIn

        with pytest.raises(ConflictProblem, match="already taken"):
            async with session.begin():
                await services.create_organization(
                    session,
                    OrganizationIn(name="Imposter", slug=org.slug),
                    actor_user_id=None,
                )


class TestOneIdentityManyOrganizations:
    async def test_the_same_person_joins_a_second_organization_without_a_second_account(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """The single-identity promise, asserted rather than assumed."""
        from app.schemas import OrganizationIn

        async with session.begin():
            first = await services.add_member(
                session,
                org_id=org.id,
                email="shared@example.com",
                full_name="Sam Shared",
                actor_user_id=None,
            )
        async with session.begin():
            other = await services.create_organization(
                session, OrganizationIn(name="Beta Corp", slug="beta"), actor_user_id=None
            )
        async with session.begin():
            second = await services.add_member(
                session,
                org_id=other.id,
                email="shared@example.com",
                full_name="Sam Shared",
                actor_user_id=None,
            )

        assert first.user_id == second.user_id, "one person, one account"
        assert first.id != second.id, "two memberships"

    async def test_joining_the_same_organization_twice_is_refused(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        with pytest.raises(ConflictProblem, match="already a member"):
            async with session.begin():
                await services.add_member(
                    session,
                    org_id=org.id,
                    email="report@example.com",
                    full_name="Rhea Report",
                    actor_user_id=None,
                )


class TestMemberListScope:
    async def _codes(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        stmt = selectors.members_stmt(ctx)
        rows = (await session.execute(stmt)).all()
        return {r[1].email for r in rows}

    async def test_org_scope_sees_everyone(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.read", Scope.ORG)
        assert len(await self._codes(session, ctx)) == 4

    async def test_team_scope_sees_only_direct_reports(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.read", Scope.TEAM)
        assert await self._codes(session, ctx) == {"report@example.com"}

    async def test_own_scope_sees_only_yourself(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["report"], "core.identity.membership.read", Scope.OWN)
        assert await self._codes(session, ctx) == {"report@example.com"}

    async def test_a_search_cannot_widen_scope(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Filters narrow within scope. They never escape it."""
        ctx = ctx_for(org, people["manager"], "core.identity.membership.read", Scope.TEAM)
        stmt = selectors.members_stmt(ctx, q="Otto")
        assert (await session.execute(stmt)).all() == []

    async def test_location_scope_sees_only_its_own_sites(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(
            org,
            people["manager"],
            "core.identity.membership.read",
            Scope.LOCATION,
            locations=["MUM"],
        )
        assert await self._codes(session, ctx) == {"report@example.com"}

    async def test_location_scope_with_no_locations_sees_nothing(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The failure mode that matters: an empty list must mean none, never all."""
        ctx = ctx_for(
            org, people["manager"], "core.identity.membership.read", Scope.LOCATION, locations=[]
        )
        assert await self._codes(session, ctx) == set()

    async def test_a_location_code_is_not_matched_by_prefix(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with session.begin():
            people["outsider"].location_codes = "MUMBAI-2"
        ctx = ctx_for(
            org,
            people["manager"],
            "core.identity.membership.read",
            Scope.LOCATION,
            locations=["MUM"],
        )
        assert "outsider@example.com" not in await self._codes(session, ctx)


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The named vulnerability: a correct list filter beside an unguarded detail view."""
        ctx = ctx_for(org, people["manager"], "core.identity.membership.read", Scope.TEAM)
        outsider = people["outsider"]

        with pytest.raises(ForbiddenProblem):
            ctx.assert_can_touch(owner_id=outsider.id, team_owner_id=outsider.manager_membership_id)

        # ...and the manager's own report is still reachable.
        report = people["report"]
        ctx.assert_can_touch(owner_id=report.id, team_owner_id=report.manager_membership_id)


class TestWriteScopeFloors:
    async def test_a_member_cannot_invite_or_remove_people(
        self, client, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Everyone holds membership.write at OWN for self-service.

        Without `minimum=Scope.ORG` on the route, that grant would let any member invite
        and remove colleagues. This is the regression guard for that.
        """
        principal = principal_for(org.id, people["report"], ["core.identity.membership.write:OWN"])
        # Read the id up front. The client shares this test's session, so the first refused
        # request rolls its transaction back and expires every loaded object — a later
        # attribute access would then try to refresh outside the async context. A real client
        # gets a fresh session per request and never meets this.
        outsider_id = people["outsider"].id

        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: principal
        try:
            invited = await client.post(
                "/api/v1/invitations", json={"email": "intruder@example.com"}
            )
            removed = await client.delete(f"/api/v1/members/{outsider_id}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert invited.status_code == 403
        assert removed.status_code == 403
        assert invited.headers["content-type"].startswith("application/problem+json")

    async def test_an_admin_at_org_scope_may_invite(
        self, client, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], ["core.identity.membership.write:ORG"])
        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(
                "/api/v1/invitations", json={"email": "newcomer@example.com"}
            )
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert response.status_code == 201
        body = response.json()
        # The raw token is returned exactly once, here.
        assert body["token"]
        assert "token_hash" not in body


class TestRoleGrants:
    async def test_a_grant_cannot_exceed_the_permissions_ceiling(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """`profile.self.*` is OWN-only; a role editor must not widen it to ORG."""
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        with pytest.raises(BusinessRuleProblem, match="widest it supports is OWN"):
            async with session.begin():
                await services.create_role(
                    session,
                    ctx,
                    RoleIn(
                        code="SNOOP",
                        name="Snoop",
                        grants=[GrantIn(permission_code="core.profile.self.read", scope="ORG")],
                    ),
                )

    async def test_a_system_role_cannot_be_edited(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.role.write", Scope.ORG)
        with pytest.raises(BusinessRuleProblem, match="Clone it"):
            async with session.begin():
                member_role = await selectors.get_role_by_code(session, org.id, "MEMBER")
                assert member_role is not None
                await services.replace_role_grants(session, ctx, member_role, [])

    async def test_resolved_grants_take_the_widest_scope(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The resolver and the runtime check must agree, or a token lies."""
        grants = await selectors.resolve_grants(session, people["manager"].id)
        by_code = dict(g.split(":") for g in grants)
        assert by_code["core.identity.membership.read"] == "ORG"
        # ORG_ADMIN holds it at ORG and MEMBER at OWN; the widest wins.
        assert by_code["core.profile.self.read"] == "OWN"


class TestOrgUnitTree:
    async def test_a_department_cannot_become_its_own_ancestor(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A cycle makes the tree unrenderable and any descendant walk non-terminating."""
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            parent = await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Delivery", code="DEL")
            )
        async with session.begin():
            child = await services.create_org_unit(
                session, ctx, OrgUnitIn(name="Platform", code="PLT", parent_id=parent.id)
            )

        with pytest.raises(BusinessRuleProblem, match="own sub-departments"):
            async with session.begin():
                await services.move_org_unit(session, ctx, parent, child.id)

    async def test_a_department_cannot_report_to_itself(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.org.unit.write", Scope.ORG)
        async with session.begin():
            unit = await services.create_org_unit(session, ctx, OrgUnitIn(name="Ops", code="OPS"))
        with pytest.raises(BusinessRuleProblem, match="cannot report to itself"):
            async with session.begin():
                await services.move_org_unit(session, ctx, unit, unit.id)


class TestMembershipRules:
    async def test_somebody_cannot_report_to_themselves(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        report = people["report"]
        with pytest.raises(BusinessRuleProblem, match="report to themselves"):
            async with session.begin():
                await services.update_member(
                    session, ctx, report, MemberPatch(manager_membership_id=report.id)
                )

    async def test_removing_somebody_keeps_their_platform_account(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Access ends; history does not. They may work for another customer."""
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        async with session.begin():
            await services.revoke_member(session, ctx, people["report"])

        assert people["report"].status == "removed"
        user = await selectors.get_user_by_email(session, "report@example.com")
        assert user is not None and user.deleted_at is None

    async def test_the_owner_cannot_be_removed(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        async with session.begin():
            people["manager"].is_owner = True
        with pytest.raises(BusinessRuleProblem, match="Transfer ownership"):
            async with session.begin():
                await services.revoke_member(session, ctx, people["manager"])


class TestInvitations:
    async def test_accepting_creates_the_membership_with_the_invited_roles(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        from app.schemas import InvitationIn

        async with session.begin():
            _, raw = await services.invite(
                session, ctx, InvitationIn(email="joiner@example.com", role_codes=["ORG_ADMIN"])
            )
        async with session.begin():
            membership = await services.accept_invitation(session, raw, "Jo Joiner")

        assert sorted(mr.role.code for mr in membership.roles) == ["ORG_ADMIN"]

    async def test_a_token_works_once(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.membership.write", Scope.ORG)
        from app.schemas import InvitationIn
        from platform_core.errors import NotFoundProblem

        async with session.begin():
            _, raw = await services.invite(session, ctx, InvitationIn(email="once@example.com"))
        async with session.begin():
            await services.accept_invitation(session, raw, "Once Only")

        with pytest.raises(NotFoundProblem, match="not valid"):
            async with session.begin():
                await services.accept_invitation(session, raw, "Once Only")

    async def test_an_unknown_token_is_indistinguishable_from_an_expired_one(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """A distinct 'expired' reply tells whoever is guessing that they found a real one."""
        from platform_core.errors import NotFoundProblem

        with pytest.raises(NotFoundProblem, match="not valid"):
            async with session.begin():
                await services.accept_invitation(session, "definitely-not-a-token", "Nobody")


class TestAuditAndEvents:
    async def test_a_write_records_audit_and_stages_an_event_together(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The audit row, the change and the event share one transaction."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        audits = (
            await session.execute(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "org.created")
            )
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "core.org.created")
            )
        ).scalar_one()
        assert audits == 1
        assert events == 1

    async def test_an_event_is_staged_unpublished_for_the_relay(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """Nothing publishes inline — that is the whole point of the outbox."""
        from sqlalchemy import select

        from app.models import OutboxEvent

        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "core.org.created")
                )
            )
            .scalars()
            .all()
        )
        assert rows and all(r.published_at is None for r in rows)
