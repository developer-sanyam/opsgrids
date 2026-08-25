"""Product registration lands on activation — the manifest → catalogue → owner-grant path.

The gap this closes was live and invisible: support was activated for the demo org, the 402
gate opened, and every request behind it still 403d with "Missing permission:
support.desk.ticket.read" — because activation flipped product-service's row and nothing ever
carried the product's catalogue into account-service. These tests prove the consumer closes
that loop, and prove the properties the loop's honesty rests on:

* an activation event installs the product's permissions, instantiates its role templates as
  org-owned system roles, and grants the admin-most template to the organization's owners —
  after which the internal grants endpoint (what the gateway resolves on every request, and
  what a token minted after re-login carries) returns the product's codes;
* redelivery of the same event is a **no-op** (delivery is at-least-once), and a *fresh*
  re-activation re-installs safely — nothing duplicated, nobody double-granted;
* a product with no manifest installs nothing and is recorded as processed — redelivery
  cannot conjure a file the deployment does not ship;
* a manifest spanning two services (hrms: employee + attendance) arrives merged, so the
  shared `HR_ADMIN` template carries both slices' grants;
* the permission-version bump fires when a grant lands and only then — a pure re-install
  must not invalidate every token in the organization.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.catalogue import load_product_manifest, registration_from_manifest
from app.config import settings
from app.consumers import install_registration_on_activation
from app.models import Membership, MembershipRole, Organization, Permission, RolePermission
from platform_core.cache import permission_version_key
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeRedis

pytestmark = pytest.mark.integration


def _activation_envelope(
    org_id: uuid.UUID,
    product_key: str,
    *,
    event_id: uuid.UUID | None = None,
    event_type: str = "core.product.activated",
    state: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_id": str(org_id),
        "product_key": product_key,
        "status": "active",
    }
    if state is not None:
        payload["state"] = state
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": payload,
    }


@pytest_asyncio.fixture
async def owner(session: AsyncSession, org: Organization) -> Membership:
    """The organization's owner — who activation grants the product's admin role to."""
    async with session.begin():
        membership = await services.add_member(
            session,
            org_id=org.id,
            email="owner@example.com",
            full_name="Olive Owner",
            actor_user_id=None,
            role_codes=["ORG_OWNER"],
        )
        membership.is_owner = True
    return membership


async def _support_role_grant_count(session: AsyncSession, org: Organization) -> int:
    role = await selectors.get_role_by_code(session, org.id, "SUPPORT_ADMIN")
    assert role is not None
    count = await session.execute(
        select(func.count()).select_from(RolePermission).where(RolePermission.role_id == role.id)
    )
    return int(count.scalar_one())


class TestInstallOnActivation:
    async def test_activation_installs_permissions_and_roles(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        await install_registration_on_activation(_activation_envelope(org.id, "support"))

        async with session.begin():
            for code in ("support.desk.ticket.read", "support.desk.ticket.write"):
                assert await selectors.get_permission_by_code(session, code) is not None
            for role_code in ("SUPPORT_ADMIN", "SUPPORT_AGENT", "SUPPORT_VIEWER"):
                role = await selectors.get_role_by_code(session, org.id, role_code)
                assert role is not None, f"{role_code} was not instantiated"
                assert role.is_system is True
                assert role.organization_id == org.id

    async def test_the_owner_is_granted_the_admin_most_role(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """And only the admin-most one — an owner does not become a support agent."""
        await install_registration_on_activation(_activation_envelope(org.id, "support"))

        async with session.begin():
            grants = await selectors.resolve_grants(session, owner.id)
        assert "support.desk.ticket.read:ORG" in grants
        assert "support.desk.ticket.write:ORG" in grants

    async def test_a_plain_member_is_not_granted_anything(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """The grant goes to owners, not to everybody — holding a seat is not running the desk."""
        async with session.begin():
            member = await services.add_member(
                session,
                org_id=org.id,
                email="member@example.com",
                full_name="Mel Member",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )
        await install_registration_on_activation(_activation_envelope(org.id, "support"))

        async with session.begin():
            grants = await selectors.resolve_grants(session, member.id)
        assert not any(code.startswith("support.") for code in grants)

    async def test_the_grants_endpoint_serves_the_new_codes(
        self,
        session: AsyncSession,
        org: Organization,
        owner: Membership,
        client: AsyncClient,
    ) -> None:
        """The read the gateway makes on every request — and the reason a token minted after
        re-login carries the product: `pv` moved, so the old token is refused and the fresh
        one resolves to these grants."""
        await install_registration_on_activation(_activation_envelope(org.id, "support"))

        response = await client.get(
            f"/internal/v1/memberships/{owner.id}/grants",
            headers={
                "X-Internal-Token": mint_service_token(
                    settings(), audience="account", caller="tests"
                ),
                "X-Calling-Service": "tests",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "support.desk.ticket.write:ORG" in body["grants"]
        assert body["permission_version"] >= 1

    async def test_the_grant_bumps_the_permission_version_once(
        self, session: AsyncSession, org: Organization, owner: Membership, redis: FakeRedis
    ) -> None:
        key = permission_version_key(str(org.id))
        await install_registration_on_activation(_activation_envelope(org.id, "support"))
        assert redis.store.get(key) == "1"

        # A fresh re-activation grants nobody anything new, so it must not invalidate
        # every token in the organization again.
        await install_registration_on_activation(_activation_envelope(org.id, "support"))
        assert redis.store.get(key) == "1"


class TestIdempotency:
    async def test_redelivery_of_the_same_event_is_a_no_op(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        event_id = uuid.uuid4()
        envelope = _activation_envelope(org.id, "support", event_id=event_id)
        await install_registration_on_activation(envelope)
        await install_registration_on_activation(envelope)

        async with session.begin():
            assert await _support_role_grant_count(session, org) == 2
            owner_roles = await session.execute(
                select(func.count())
                .select_from(MembershipRole)
                .where(MembershipRole.membership_id == owner.id)
            )
            # ORG_OWNER from joining, SUPPORT_ADMIN from the activation. Not three.
            assert int(owner_roles.scalar_one()) == 2

    async def test_reactivation_reinstalls_safely(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """A returning customer's second activation is a new event, not a redelivery."""
        await install_registration_on_activation(_activation_envelope(org.id, "support"))
        await install_registration_on_activation(_activation_envelope(org.id, "support"))

        async with session.begin():
            assert await _support_role_grant_count(session, org) == 2
            permissions = await session.execute(
                select(func.count()).select_from(Permission).where(Permission.product == "support")
            )
            assert int(permissions.scalar_one()) == 2


class TestNegativeCases:
    async def test_an_unknown_product_installs_nothing(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """No manifest, no install — and the event is processed, because redelivery cannot
        conjure a file the deployment does not ship."""
        event_id = uuid.uuid4()
        envelope = _activation_envelope(org.id, "galactic", event_id=event_id)
        await install_registration_on_activation(envelope)
        await install_registration_on_activation(envelope)  # the duplicate is also quiet

        async with session.begin():
            permissions = await session.execute(
                select(func.count()).select_from(Permission).where(Permission.product == "galactic")
            )
            assert int(permissions.scalar_one()) == 0
            roles = await selectors.list_roles(session, org.id)
        assert {r.code for r in roles} == {"ORG_OWNER", "ORG_ADMIN", "MEMBER"}

    async def test_a_de_entitling_subscription_event_installs_nothing(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """Decided from the payload's state, not the routing key, mirroring the projection."""
        await install_registration_on_activation(
            _activation_envelope(
                org.id,
                "support",
                event_type="commerce.subscription.activated",
                state="cancelled",
            )
        )
        async with session.begin():
            roles = await selectors.list_roles(session, org.id)
        assert not any(r.code.startswith("SUPPORT_") for r in roles)


class TestMergedManifests:
    async def test_a_two_service_product_arrives_as_one_registration(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """hrms spans employee- and attendance-service; the committed manifest is their merge,
        so the shared HR_ADMIN template carries both slices' grants."""
        await install_registration_on_activation(
            _activation_envelope(
                org.id,
                "hrms",
                event_type="commerce.subscription.activated",
                state="active",
            )
        )

        async with session.begin():
            for code in ("hrms.people.employee.read", "hrms.time.shift.write"):
                assert await selectors.get_permission_by_code(session, code) is not None
            role = await selectors.get_role_by_code(session, org.id, "HR_ADMIN")
            assert role is not None
            grant_count = await session.execute(
                select(func.count())
                .select_from(RolePermission)
                .where(RolePermission.role_id == role.id)
            )
            assert int(grant_count.scalar_one()) == 7
            employee = await selectors.get_role_by_code(session, org.id, "EMPLOYEE")
            assert employee is not None
            assert employee.is_default is True


class TestManifestLoader:
    def test_the_committed_support_manifest_parses(self) -> None:
        manifest = load_product_manifest("support")
        assert manifest is not None
        assert manifest.product == "support"
        assert manifest.admin_role_codes == ("SUPPORT_ADMIN",)
        assert "support.desk.ticket.read" in manifest.registration.catalogue.codes()

    def test_an_unknown_product_has_no_manifest(self) -> None:
        assert load_product_manifest("galactic") is None

    def test_a_key_that_is_not_a_product_never_reaches_the_filesystem(self) -> None:
        """The key arrives in an event payload; a path is not a product."""
        assert load_product_manifest("../openapi/account") is None
        assert load_product_manifest("SUPPORT") is None

    def test_a_manifest_reaching_into_a_foreign_product_is_refused(self) -> None:
        """The same boundary the in-code catalogues enforce at import time: a support
        template does not get to grant HRMS."""
        with pytest.raises(ValueError, match="does not declare"):
            registration_from_manifest(
                {
                    "manifest_version": 1,
                    "product": "support",
                    "permissions": [],
                    "role_templates": [
                        {
                            "code": "SNEAKY",
                            "name": "Sneaky",
                            "grants": [{"code": "hrms.people.employee.read", "scope": "ORG"}],
                        }
                    ],
                }
            )

    def test_a_grant_the_catalogue_does_not_declare_is_refused(self) -> None:
        """`install_registration` would silently skip it, and a silently skipped grant is a
        role that quietly does less than its manifest says."""
        with pytest.raises(ValueError, match="does not declare"):
            registration_from_manifest(
                {
                    "manifest_version": 1,
                    "product": "support",
                    "permissions": [],
                    "role_templates": [
                        {
                            "code": "SUPPORT_ADMIN",
                            "name": "Support administrator",
                            "grants": [{"code": "support.desk.ticket.read", "scope": "ORG"}],
                        }
                    ],
                }
            )
