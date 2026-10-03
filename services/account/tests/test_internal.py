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
from app.schemas import GrantIn, InvitationIn, MemberPatch, RoleIn
from platform_core.auth import Scope, ScopeContext
from platform_core.cache import permission_version_key
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeRedis, ctx_for

pytestmark = pytest.mark.integration


def internal_as(caller: str) -> dict[str, str]:
    """A freshly minted service token, as a real caller sends, addressed as `caller` names.

    Not the signing key itself: that is exactly what this replaced. A static secret on the
    wire meant anything capturing request headers captured the credential rather than one
    expiring use of it.
    """
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="account", caller=caller),
        "X-Calling-Service": caller,
    }


def internal() -> dict[str, str]:
    return internal_as("tests")


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

    async def test_role_names_are_index_aligned_with_role_codes(
        self, client: AsyncClient, people: dict[str, Membership], org: Organization
    ) -> None:
        """The one source for a friendly role label is the `roles` table's own `name`
        column — the same one `to_operator_member_out` reads for the members list. This is
        additive: `roles` (codes) is untouched, `role_names` rides alongside it."""
        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "manager@example.com"},
            headers=internal(),
        )
        assert response.status_code == 200
        membership = response.json()["memberships"][0]
        assert membership["roles"] == ["ORG_ADMIN"]
        assert membership["role_names"] == ["Administrator"]

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

    async def test_citext_folds_a_dotted_capital_i_to_plain_i(
        self, client: AsyncClient, session: AsyncSession, org: Organization
    ) -> None:
        """Round 3's finding, reproduced against the real route: Postgres `CITEXT` (this
        column's actual type) folds `İ` (U+0130, LATIN CAPITAL LETTER I WITH DOT ABOVE) to
        plain `i` under its default collation — a different fold than Python's own casefold or
        `idna`'s UTS-46 mapping use. This is a fact about the column, not a bug in this route;
        it is *why* `auth-service`'s `sso_callback` may no longer trust a non-ASCII identity at
        all (see `services/auth/app/services.py::sso_callback`, round 3, BLOCKER)."""
        async with session.begin():
            await services.add_member(
                session,
                org_id=org.id,
                email="victim@victim.com",
                full_name="Victim",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )

        response = await client.get(
            "/internal/v1/users/by-email",
            params={"email": "victim@vİctim.com"},
            headers=internal(),
        )
        assert response.status_code == 200
        assert response.json()["email"] == "victim@victim.com"

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


class TestMembershipLookup:
    """`notification`'s conversation-assignment endpoint calls this before trusting a membership
    id a caller supplied — see that service's `app/memberships.py`."""

    async def test_an_active_membership_in_the_right_organization_is_confirmed(
        self, client: AsyncClient, people: dict[str, Membership], org: Organization
    ) -> None:
        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{people['manager'].id}",
            headers=internal_as("notification"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["exists"] is True
        assert body["active"] is True

    async def test_an_unknown_membership_id_does_not_exist(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{uuid.uuid4()}",
            headers=internal_as("notification"),
        )
        assert response.status_code == 200
        assert response.json() == {
            "membership_id": response.json()["membership_id"],
            "exists": False,
            "active": False,
        }

    async def test_a_real_membership_asked_under_the_wrong_organization_does_not_exist(
        self, client: AsyncClient, people: dict[str, Membership]
    ) -> None:
        """The whole point of this endpoint: a membership id real somewhere else must read
        exactly like one that is real nowhere, so a caller cannot use it to confirm an id
        belongs to an organization it has no business asking about."""
        response = await client.get(
            f"/internal/v1/organizations/{uuid.uuid4()}/memberships/{people['manager'].id}",
            headers=internal_as("notification"),
        )
        assert response.status_code == 200
        assert response.json()["exists"] is False

    async def test_a_removed_membership_does_not_exist(
        self,
        client: AsyncClient,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
    ) -> None:
        """`revoke_member` sets `status="removed"`, and `selectors.get_member` excludes that
        status entirely — a removed membership answers exactly like one that was never real,
        which is the right answer for an assignment target: nobody should be routed work through
        a membership that no longer belongs to the organization."""
        member = people["report"]
        async with session.begin():
            await session.refresh(member, ["roles"])
            await services.revoke_member(session, admin_ctx(org, people["manager"]), member)

        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{member.id}",
            headers=internal_as("notification"),
        )
        body = response.json()
        assert body["exists"] is False
        assert body["active"] is False

    async def test_a_membership_in_a_non_active_status_is_not_active(
        self,
        client: AsyncClient,
        session: AsyncSession,
        people: dict[str, Membership],
        org: Organization,
    ) -> None:
        """`status` carries values besides `active` and `removed` in principle (a suspension, an
        unaccepted invitation); `get_member` only excludes `removed`, so this is the case that
        proves `active` and `exists` are genuinely two different questions."""
        member = people["report"]
        async with session.begin():
            member.status = "suspended"
            session.add(member)

        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{member.id}",
            headers=internal_as("notification"),
        )
        body = response.json()
        assert body["exists"] is True
        assert body["active"] is False

    async def test_it_needs_a_service_token(self, client: AsyncClient, org: Organization) -> None:
        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{uuid.uuid4()}"
        )
        assert response.status_code == 401

    async def test_a_caller_other_than_notification_is_refused(
        self, client: AsyncClient, people: dict[str, Membership], org: Organization
    ) -> None:
        """Every other internal route on this router answers any caller holding a token
        addressed to account; this one does not. It exists for `notification`'s own
        assignment check, and a token minted for a different service — real, correctly
        audienced, just not the one this route is for — must not widen into a place it was
        never meant to be reached from."""
        response = await client.get(
            f"/internal/v1/organizations/{org.id}/memberships/{people['manager'].id}",
            headers=internal_as("billing"),
        )
        assert response.status_code == 403


class TestOrganizationsBulkList:
    """`services/console/app/backfill.py`'s discovery read: every live organization's identity,
    paginated, restricted to console alone (a materially bigger cross-tenant surface than any
    other route on this router)."""

    async def test_it_needs_a_service_token(self, client: AsyncClient) -> None:
        assert (await client.get("/internal/v1/organizations")).status_code == 401

    async def test_a_caller_other_than_console_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/internal/v1/organizations", headers=internal_as("billing"))
        assert response.status_code == 401

    async def test_console_sees_the_seeded_organization(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get("/internal/v1/organizations", headers=internal_as("console"))
        assert response.status_code == 200, response.text
        body = response.json()
        ids = {row["id"] for row in body["items"]}
        assert str(org.id) in ids
        row = next(r for r in body["items"] if r["id"] == str(org.id))
        assert row["slug"] == "acme"
        assert row["name"] == "Acme Technologies"
        assert "updated_at" in row
        assert "created_at" in row
        assert "country" in row

    async def test_keyset_pagination_terminates_with_a_null_cursor(
        self, client: AsyncClient, admin_session: AsyncSession, org: Organization
    ) -> None:
        from app.schemas import OrganizationIn

        async with admin_session.begin():
            await services.create_organization(
                admin_session,
                OrganizationIn(name="Second Org", slug="second-org"),
                actor_user_id=None,
            )

        first = await client.get(
            "/internal/v1/organizations",
            params={"limit": 1},
            headers=internal_as("console"),
        )
        assert first.status_code == 200
        page = first.json()
        assert len(page["items"]) == 1
        assert page["next_cursor"] is not None

        second = await client.get(
            "/internal/v1/organizations",
            params={"limit": 1, "cursor": page["next_cursor"]},
            headers=internal_as("console"),
        )
        assert second.status_code == 200
        second_page = second.json()
        assert len(second_page["items"]) == 1
        assert second_page["items"][0]["id"] != page["items"][0]["id"]

    async def test_the_route_is_absent_from_the_public_schema(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "/internal/v1/organizations" not in schema["paths"]


class TestSignupFederated:
    """auth-service's own entry point for a verified Google/Microsoft identity that has no
    organisation yet (ADR per docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md
    §4). Calls the same `services.signup` the public `/account/signup` form uses — only the
    caller differs, and that caller has already proven the email out of band, so there is no
    password-reset step to defer to.
    """

    async def test_it_creates_an_organisation_and_returns_the_owner(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/internal/v1/signup-federated",
            json={
                "organization_name": "Acme Federated",
                "slug": "acme-federated",
                "full_name": "Ada Federated",
                "email": "ada@acme-federated.com",
            },
            headers=internal_as("auth"),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert "organization_id" in body
        assert "user_id" in body

    async def test_it_needs_a_service_token(self, client: AsyncClient) -> None:
        """A user token must never reach this, same as every other internal route."""
        response = await client.post(
            "/internal/v1/signup-federated",
            json={
                "organization_name": "Acme",
                "slug": "acme-federated-no-token",
                "full_name": "Ada",
                "email": "ada-no-token@acme-federated.com",
            },
        )
        assert response.status_code == 401

    async def test_a_caller_other_than_auth_is_refused(self, client: AsyncClient) -> None:
        """Finding 5 of the final whole-branch review: the spec's stated trust model is
        "auth-service, already holding a verified identity" — a valid service token minted
        for an unrelated service must not reach this route either, same posture
        `require_console_caller` already holds for console's own narrower routes above."""
        response = await client.post(
            "/internal/v1/signup-federated",
            json={
                "organization_name": "Acme",
                "slug": "acme-federated-wrong-caller",
                "full_name": "Ada",
                "email": "ada-wrong-caller@acme-federated.com",
            },
            headers=internal_as("console"),
        )
        assert response.status_code == 401

    async def test_a_taken_slug_is_a_409_same_as_the_public_route(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.post(
            "/internal/v1/signup-federated",
            json={
                "organization_name": "Duplicate Slug",
                "slug": org.slug,
                "full_name": "Dup",
                "email": "dup-slug@acme-federated.com",
            },
            headers=internal_as("auth"),
        )
        assert response.status_code == 409

    async def test_a_known_email_is_a_409_same_as_the_public_route(
        self, client: AsyncClient, people: dict[str, Membership]
    ) -> None:
        """An existing account does not get a second organisation from this route either —
        the public route's own duplicate-email behaviour, reused unchanged."""
        response = await client.post(
            "/internal/v1/signup-federated",
            json={
                "organization_name": "Duplicate Email",
                "slug": "acme-federated-dup-email",
                "full_name": "Dup",
                "email": "manager@example.com",
            },
            headers=internal_as("auth"),
        )
        assert response.status_code == 409

    async def test_the_route_is_absent_from_the_public_schema(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "/internal/v1/signup-federated" not in schema["paths"]


async def _invite_raw(
    session: AsyncSession, org: Organization, inviter: Membership, *, email: str
) -> str:
    """Create a pending invitation on a tenant session and return its raw token — the same
    shape `tests/test_invitation_accept.py::_invite` uses, trimmed to what these tests need."""
    async with session.begin():
        _, raw = await services.invite(
            session, admin_ctx(org, inviter), InvitationIn(email=email, role_codes=["MEMBER"])
        )
    return raw


class TestAcceptInvitationFederated:
    """The federated counterpart to the public `POST /invitations/accept` — the route that
    makes `services.accept_invitation`'s `expected_email` guard load-bearing rather than
    theoretical. See docs/superpowers/specs/2026-10-03-oauth2-social-login-design.md §4/§6."""

    async def test_it_accepts_a_matching_verified_email(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        raw = await _invite_raw(session, org, people["manager"], email="invitee@example.com")
        response = await client.post(
            "/internal/v1/invitations/accept-federated",
            json={"token": raw, "email": "invitee@example.com", "full_name": "Invitee"},
            headers=internal_as("auth"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization_id"] == str(org.id)
        assert "user_id" in body

    async def test_an_email_mismatch_is_the_same_opaque_404_as_every_other_invalid_invitation(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The property that stops token enumeration, now exercised over HTTP: a forged
        attempt that has the right token but the wrong email must read exactly like one with
        no real token at all, not like a distinct "close, but no" reply."""
        raw = await _invite_raw(session, org, people["manager"], email="victim@example.com")
        response = await client.post(
            "/internal/v1/invitations/accept-federated",
            json={"token": raw, "email": "attacker@evil.com", "full_name": "Attacker"},
            headers=internal_as("auth"),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "That invitation is not valid"

    async def test_an_unknown_token_is_the_same_404(self, client: AsyncClient) -> None:
        response = await client.post(
            "/internal/v1/invitations/accept-federated",
            json={"token": "z" * 40, "email": "nobody@example.com", "full_name": "Nobody"},
            headers=internal_as("auth"),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "That invitation is not valid"

    async def test_it_needs_a_service_token(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        raw = await _invite_raw(session, org, people["manager"], email="no-token@example.com")
        response = await client.post(
            "/internal/v1/invitations/accept-federated",
            json={"token": raw, "email": "no-token@example.com", "full_name": "X"},
        )
        assert response.status_code == 401

    async def test_a_caller_other_than_auth_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """Finding 5 of the final whole-branch review: same narrower trust model as
        `signup-federated` above — a valid service token minted for an unrelated service must
        not reach this route either."""
        raw = await _invite_raw(session, org, people["manager"], email="wrong-caller@example.com")
        response = await client.post(
            "/internal/v1/invitations/accept-federated",
            json={"token": raw, "email": "wrong-caller@example.com", "full_name": "X"},
            headers=internal_as("console"),
        )
        assert response.status_code == 401

    async def test_the_route_is_absent_from_the_public_schema(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "/internal/v1/invitations/accept-federated" not in schema["paths"]


def _role(code: str, permission: str, scope: str):
    return RoleIn(
        code=code,
        name=code.title(),
        grants=[GrantIn(permission_code=permission, scope=scope)],
    )
