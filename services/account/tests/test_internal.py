"""The internal API, and the permission-version bump that makes revocation real.

Both of these were gaps rather than regressions: auth-service and both gateways were
written against endpoints this service did not serve, and every one of their test suites
passed because each side stubbed the other. Nothing caught it because nothing ran the two
together — which is the argument for the cross-service suite, not for more unit tests.

The bump has the same shape of problem. `bump_permission_version` existed, the gateway
checked `pv` on every request, `set_member_roles` said in its own docstring that it bumped
— and nothing called it, so the check could never fire.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.models import Membership, Organization
from app.schemas import GrantIn, MemberPatch
from platform_core.auth import Scope, ScopeContext
from platform_core.cache import permission_version_key
from tests.helpers import FakeRedis, ctx_for

pytestmark = pytest.mark.integration


def internal() -> dict[str, str]:
    return {"X-Internal-Token": settings().internal_service_token.get_secret_value()}


def admin_ctx(org: Organization, membership: Membership) -> ScopeContext:
    """The context an org admin's request would arrive with.

    The permission named here is not re-checked by the service functions — the route
    dependency does that — so any code works. It is spelled out rather than left blank so
    the intent of the call site stays readable.
    """
    return ctx_for(org.id, membership, "core.identity.membership.write", Scope.ORG)


class TestGuard:
    async def test_the_internal_api_refuses_a_request_with_no_service_token(
        self, client: AsyncClient
    ) -> None:
        """These endpoints read across every tenant. A user token must never reach them."""
        response = await client.get("/internal/v1/users/by-email?email=a@example.com")
        assert response.status_code == 401

    async def test_a_wrong_service_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(
            "/internal/v1/users/by-email?email=a@example.com",
            headers={"X-Internal-Token": "guessed"},
        )
        assert response.status_code == 401

    async def test_the_internal_api_is_absent_from_the_public_schema(
        self, client: AsyncClient
    ) -> None:
        """A cross-tenant endpoint documented in the customer-facing OpenAPI is an
        invitation."""
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if p.startswith("/internal")]


class TestDirectoryContract:
    """auth-service parses these responses in `HttpDirectory._parse`.

    Every field asserted here is one that method reads by name, so a rename that breaks
    sign-in fails here rather than in production.
    """

    async def test_a_user_is_returned_with_every_membership(
        self, client: AsyncClient, people: dict[str, Membership], org: Organization
    ) -> None:
        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "manager@example.com"},
            headers=internal(),
        )
        assert response.status_code == 200
        body = response.json()

        assert body["email"] == "manager@example.com"
        assert body["full_name"] == "Maya Manager"
        assert body["is_active"] is True

        membership = body["memberships"][0]
        assert membership["organization_id"] == str(org.id)
        assert membership["organization_slug"] == "acme"
        assert membership["organization_name"] == "Acme Technologies"
        assert membership["status"] == "active"
        assert "ORG_ADMIN" in membership["roles"]
        assert membership["permission_version"] == 0

    async def test_locations_come_back_as_a_list(
        self, client: AsyncClient, people: dict[str, Membership]
    ) -> None:
        """Stored comma-separated, consumed as a list — the split belongs here, once,
        not in every caller."""
        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "report@example.com"},
            headers=internal(),
        )
        assert response.json()["memberships"][0]["locations"] == ["MUM"]

    async def test_an_unknown_email_is_404_not_an_empty_body(self, client: AsyncClient) -> None:
        """`HttpDirectory` maps 404 to None, which sign-in turns into the same
        "wrong email or password" a bad password gets — so an attacker cannot use the
        login form to enumerate who has an account."""
        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "nobody@example.com"},
            headers=internal(),
        )
        assert response.status_code == 404

    async def test_lookup_by_id_returns_the_same_shape(
        self, client: AsyncClient, people: dict[str, Membership]
    ) -> None:
        by_email = (
            await client.get(
                "/internal/v1/users/by-email",
                params={"email": "manager@example.com"},
                headers=internal(),
            )
        ).json()
        by_id = (
            await client.get(f"/internal/v1/users/{by_email['user_id']}", headers=internal())
        ).json()
        assert by_id == by_email

    async def test_a_suspended_organization_suspends_its_memberships(
        self,
        client: AsyncClient,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
    ) -> None:
        """Whatever the membership row says. Otherwise suspending a customer for
        non-payment would leave everyone in it still able to sign in."""
        async with session.begin():
            org.status = "suspended"

        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "manager@example.com"},
            headers=internal(),
        )
        assert response.json()["memberships"][0]["status"] == "suspended"


class TestGrants:
    async def test_grants_come_back_as_code_and_scope(
        self, client: AsyncClient, people: dict[str, Membership]
    ) -> None:
        """The format the gateway forwards as `x-perms` and `platform_core.auth` parses."""
        membership = people["manager"]
        response = await client.get(
            f"/internal/v1/memberships/{membership.id}/grants", headers=internal()
        )
        assert response.status_code == 200

        grants = response.json()["grants"]
        assert grants, "an ORG_ADMIN with no grants means the role templates did not apply"
        assert all(":" in g for g in grants)
        assert all(g.split(":")[1] in {"OWN", "TEAM", "LOCATION", "ORG"} for g in grants)

    async def test_the_widest_scope_wins_when_two_roles_overlap(
        self,
        client: AsyncClient,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
    ) -> None:
        """A role granting ORG must not be narrowed by another granting OWN.

        Taking the last one seen instead would make the result depend on row order, so the
        same person's permissions would differ between two identical requests.
        """
        member = people["report"]
        ctx = admin_ctx(org, people["manager"])

        async with session.begin():
            wide = await services.create_role(
                session,
                ctx,
                _role("WIDE", "core.identity.membership.read", "ORG"),
            )
            narrow = await services.create_role(
                session,
                ctx,
                _role("NARROW", "core.identity.membership.read", "OWN"),
            )
            await session.refresh(member, ["roles"])
            await services.set_member_roles(session, ctx, member, [wide.code, narrow.code])

        response = await client.get(
            f"/internal/v1/memberships/{member.id}/grants", headers=internal()
        )
        grants = response.json()["grants"]
        assert "core.identity.membership.read:ORG" in grants
        assert "core.identity.membership.read:OWN" not in grants

    async def test_a_revoked_membership_has_no_grants(
        self,
        client: AsyncClient,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
    ) -> None:
        member = people["report"]
        async with session.begin():
            await session.refresh(member, ["roles"])
            await services.revoke_member(session, admin_ctx(org, people["manager"]), member)

        response = await client.get(
            f"/internal/v1/memberships/{member.id}/grants", headers=internal()
        )
        assert response.status_code == 200
        assert response.json()["grants"] == []

    async def test_an_unknown_membership_is_404(self, client: AsyncClient) -> None:
        response = await client.get(
            f"/internal/v1/memberships/{uuid.uuid4()}/grants", headers=internal()
        )
        assert response.status_code == 404


class TestPermissionVersionBump:
    """Without these, `pv` is a claim nothing ever changes and the gateway's staleness
    check is dead code."""

    async def test_changing_roles_bumps_the_version(
        self,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
        redis: FakeRedis,
    ) -> None:
        member = people["report"]
        async with session.begin():
            await session.refresh(member, ["roles"])
            await services.set_member_roles(
                session, admin_ctx(org, people["manager"]), member, ["ORG_ADMIN"]
            )

        assert redis.store[permission_version_key(str(org.id))] == "1"

    async def test_revoking_a_member_bumps_the_version(
        self,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
        redis: FakeRedis,
    ) -> None:
        """The one that matters most: otherwise somebody removed from the organization
        keeps working until their access token expires."""
        member = people["report"]
        async with session.begin():
            await session.refresh(member, ["roles"])
            await services.revoke_member(session, admin_ctx(org, people["manager"]), member)

        assert redis.store[permission_version_key(str(org.id))] == "1"

    async def test_editing_a_role_bumps_the_version(
        self,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
        redis: FakeRedis,
    ) -> None:
        """Editing a role changes what everybody holding it may do."""
        ctx = admin_ctx(org, people["manager"])
        async with session.begin():
            role = await services.create_role(
                session, ctx, _role("EDITABLE", "core.identity.membership.read", "OWN")
            )
        before = redis.store.get(permission_version_key(str(org.id)), "0")

        async with session.begin():
            await session.refresh(role, ["grants"])
            await services.replace_role_grants(
                session,
                ctx,
                role,
                [GrantIn(permission_code="core.identity.membership.read", scope="ORG")],
            )

        assert int(redis.store[permission_version_key(str(org.id))]) > int(before)

    async def test_moving_somebody_between_locations_bumps_the_version(
        self,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
        redis: FakeRedis,
    ) -> None:
        """`loc` is carried in the token and drives LOCATION scope, so a site move has to
        invalidate it."""
        member = people["report"]
        async with session.begin():
            await services.update_member(
                session,
                admin_ctx(org, people["manager"]),
                member,
                MemberPatch(location_codes=["DEL"]),
            )

        assert redis.store[permission_version_key(str(org.id))] == "1"

    async def test_a_cosmetic_change_does_not_bump_the_version(
        self,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
        redis: FakeRedis,
    ) -> None:
        """Bumping on every edit would sign the whole organization out because somebody
        fixed a job title."""
        async with session.begin():
            await services.update_member(
                session,
                admin_ctx(org, people["manager"]),
                people["report"],
                MemberPatch(job_title="Senior Analyst"),
            )

        assert permission_version_key(str(org.id)) not in redis.store


def _role(code: str, permission: str, scope: str):
    from app.schemas import RoleIn

    return RoleIn(
        code=code,
        name=code.title(),
        grants=[GrantIn(permission_code=permission, scope=scope)],
    )
