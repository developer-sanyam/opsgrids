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
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.models import Membership, Organization, OrgDomain
from app.schemas import GrantIn, MemberPatch, RoleIn
from platform_core.auth import Scope, ScopeContext
from platform_core.cache import permission_version_key
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeRedis, ctx_for

pytestmark = pytest.mark.integration


def internal() -> dict[str, str]:
    """A freshly minted service token, as a real caller sends.

    Not the signing key itself: that is exactly what this replaced. A static secret on the
    wire meant anything capturing request headers captured the credential rather than one
    expiring use of it.
    """
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="account", caller="tests"),
        "X-Calling-Service": "tests",
    }


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

    async def test_the_raw_signing_key_is_not_a_credential(self, client: AsyncClient) -> None:
        """The whole point of the change.

        Sending the shared secret itself used to be how a service authenticated. It is now
        only a signing key, and presenting it as a token is refused — so a leaked config value
        is not immediately a working credential.
        """
        response = await client.get(
            "/internal/v1/users/by-email?email=a@example.com",
            headers={"X-Internal-Token": settings().internal_service_token.get_secret_value()},
        )
        assert response.status_code == 401

    async def test_a_token_for_another_service_is_refused(self, client: AsyncClient) -> None:
        """Audience binding. The static secret it replaced was accepted by everything.

        A token minted for billing-service must be useless against account-service, or a
        compromise anywhere is a compromise everywhere.
        """
        wrong = mint_service_token(settings(), audience="billing", caller="tests")
        response = await client.get(
            "/internal/v1/users/by-email?email=a@example.com",
            headers={"X-Internal-Token": wrong},
        )
        assert response.status_code == 401

    async def test_an_expired_token_is_refused(self, client: AsyncClient) -> None:
        """Sixty seconds is the window a captured header is worth anything for."""
        from datetime import UTC, datetime, timedelta

        import jwt

        from platform_core.internal_auth import ALGORITHM

        stale = jwt.encode(
            {
                "iss": "tests",
                "aud": "account",
                "iat": datetime.now(UTC) - timedelta(minutes=10),
                "exp": datetime.now(UTC) - timedelta(minutes=5),
                "jti": "expired",
                "typ": "service",
            },
            settings().internal_service_token.get_secret_value(),
            algorithm=ALGORITHM,
        )
        response = await client.get(
            "/internal/v1/users/by-email?email=a@example.com",
            headers={"X-Internal-Token": stale},
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
        non-payment would leave everyone in it still able to sign in.

        Written through the session rather than by assigning to `org`: that object is created
        on the admin session and is detached from this one, so a bare attribute assignment
        would update nothing and the test would pass for the wrong reason.
        """
        async with session.begin():
            await session.execute(
                text("UPDATE account.organizations SET status = 'suspended' WHERE id = :id"),
                {"id": org.id},
            )

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
                RoleIn(
                    code=role.code,
                    name=role.name,
                    grants=[GrantIn(permission_code="core.identity.membership.read", scope="ORG")],
                ),
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


class TestDomainLookup:
    """The verified-domain lookup single sign-on starts from.

    auth-service asks this to find out *which organization* proved ownership of an email
    domain. What it must never do is answer as though an unproven claim were a proven one:
    domain routing without proof of ownership is an account-takeover primitive, which is why
    `verified` travels with the answer instead of being assumed by the caller.
    """

    async def test_it_answers_with_the_organization_and_the_verification_state(
        self, client: AsyncClient, session: AsyncSession, org: Organization
    ) -> None:
        async with session.begin():
            session.add(
                OrgDomain(
                    organization_id=org.id,
                    domain="acme.example",
                    verification_token="token",  # pragma: allowlist secret
                    verified_at=datetime.now(UTC),
                )
            )

        response = await client.get("/internal/v1/domains/acme.example", headers=internal())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization_id"] == str(org.id)
        assert body["organization_slug"] == "acme"
        assert body["verified"] is True

    async def test_an_unverified_domain_is_a_404_like_one_nobody_claimed(
        self, client: AsyncClient, session: AsyncSession, org: Organization
    ) -> None:
        """A pending claim is not proof of anything, so it is not reported — the same 404 an
        unclaimed domain gets, which is exactly what stops a pending row from ever routing SSO
        through this lookup."""
        async with session.begin():
            session.add(
                OrgDomain(
                    organization_id=org.id,
                    domain="unproven.example",
                    verification_token="token",  # pragma: allowlist secret
                )
            )

        response = await client.get("/internal/v1/domains/unproven.example", headers=internal())
        assert response.status_code == 404

    async def test_a_domain_nobody_claimed_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get("/internal/v1/domains/nobody.example", headers=internal())
        assert response.status_code == 404

    async def test_several_pending_claims_on_the_same_domain_are_still_a_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
    ) -> None:
        """More than one organization may hold the same domain pending at once
        (`uq_org_domains_org_domain` is per-organization, not global). None of them is the
        answer — there is no honest way to pick one, so the lookup stays a 404 rather than
        arbitrarily disclosing whichever row a query plan happened to find first."""
        from app.schemas import OrganizationIn

        async with admin_session.begin():
            other = await services.create_organization(
                admin_session,
                OrganizationIn(name="Contender", slug="contender"),
                actor_user_id=None,
            )
        async with session.begin():
            session.add(
                OrgDomain(
                    organization_id=org.id,
                    domain="contested.example",
                    verification_token="token-a",  # pragma: allowlist secret
                )
            )
        async with admin_session.begin():
            admin_session.add(
                OrgDomain(
                    organization_id=other.id,
                    domain="contested.example",
                    verification_token="token-b",  # pragma: allowlist secret
                )
            )

        response = await client.get("/internal/v1/domains/contested.example", headers=internal())
        assert response.status_code == 404

    async def test_the_verified_holder_is_returned_even_with_other_orgs_pending(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
    ) -> None:
        from app.schemas import OrganizationIn

        async with admin_session.begin():
            other = await services.create_organization(
                admin_session,
                OrganizationIn(name="Contender", slug="contender"),
                actor_user_id=None,
            )
            admin_session.add(
                OrgDomain(
                    organization_id=other.id,
                    domain="mostly-contested.example",
                    verification_token="token-b",  # pragma: allowlist secret
                )
            )
        async with session.begin():
            session.add(
                OrgDomain(
                    organization_id=org.id,
                    domain="mostly-contested.example",
                    verification_token="token-a",  # pragma: allowlist secret
                    verified_at=datetime.now(UTC),
                )
            )

        response = await client.get(
            "/internal/v1/domains/mostly-contested.example", headers=internal()
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization_id"] == str(org.id)
        assert body["verified"] is True

    async def test_it_needs_a_service_token_like_every_other_internal_route(
        self, client: AsyncClient
    ) -> None:
        assert (await client.get("/internal/v1/domains/acme.example")).status_code == 401


def _role(code: str, permission: str, scope: str):
    return RoleIn(
        code=code,
        name=code.title(),
        grants=[GrantIn(permission_code=permission, scope=scope)],
    )
