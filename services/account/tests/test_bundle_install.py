"""A bundle subscription installs every member's permission manifest — not the bundle's.

Before this, a `suite` subscription event reached this consumer as `product_key: "suite"`,
`load_product_manifest("suite")` found nothing, and the consumer logged `manifest.missing` and
moved on. Even once product-service entitled the members, every request behind them would have
403'd: the roles did not exist and the owner held none of them. These prove the fan-out:

* a trio installs HRMS, CRM and Finance — their roles, and every admin template granted to the
  owner (CRM ships two);
* nothing is installed for `suite` itself;
* redelivery is a no-op; a de-entitling state installs nothing;
* the install lands in the event's organization and no other.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.consumers import install_registration_on_activation
from app.models import Membership, MembershipRole, Organization, ProcessedEvent
from app.schemas import OrganizationIn

pytestmark = pytest.mark.integration

TRIO = ["hrms", "crm", "finance"]
#: What every organization holds at creation: account's core roles plus approval's, installed
#: from the merged `core` manifest.
CORE_ROLES = {"ORG_OWNER", "ORG_ADMIN", "MEMBER", "APPROVAL_ADMIN"}
ADMIN_ROLES = {"HR_ADMIN", "CRM_BOOK_ADMIN", "CRM_PIPELINE_ADMIN", "FINANCE_ADMIN"}


def _suite_event(
    org_id: uuid.UUID,
    *,
    members: list[str] | None,
    state: str = "active",
    event_type: str = "commerce.subscription.activated",
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_id": str(org_id),
        "product_key": "suite",
        "state": state,
    }
    if members is not None:
        payload["entitled_products"] = members
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": payload,
    }


@pytest_asyncio.fixture
async def owner(session: AsyncSession, org: Organization) -> Membership:
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


async def _role_codes(session: AsyncSession, org_id: uuid.UUID) -> set[str]:
    async with session.begin():
        return {r.code for r in await selectors.list_roles(session, org_id)}


class TestFanOut:
    async def test_a_trio_installs_each_members_roles_and_grants_every_admin_template(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        await install_registration_on_activation(_suite_event(org.id, members=TRIO))

        codes = await _role_codes(session, org.id)
        assert codes >= ADMIN_ROLES, f"missing member roles: {ADMIN_ROLES - codes}"

        async with session.begin():
            grants = await selectors.resolve_grants(session, owner.id)
        for prefix in ("hrms.", "crm.", "finance."):
            assert any(g.startswith(prefix) for g in grants), f"owner holds nothing in {prefix}"

    async def test_the_bundle_itself_installs_nothing(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """A bundle event from before `entitled_products` names only `suite`, which has no
        manifest and never will; it is recorded as processed, not dead-lettered."""
        event = _suite_event(org.id, members=None)
        await install_registration_on_activation(event)
        assert await _role_codes(session, org.id) == CORE_ROLES
        async with session.begin():
            done = await session.get(ProcessedEvent, uuid.UUID(event["event_id"]))
        assert done is not None

    async def test_a_resync_event_installs_the_members(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        """The backfill's re-announcement is bound and folds like any entitling transition."""
        await install_registration_on_activation(
            _suite_event(
                org.id,
                members=["hrms", "finance"],
                event_type="commerce.subscription.entitlements_resynced",
            )
        )
        codes = await _role_codes(session, org.id)
        assert {"HR_ADMIN", "FINANCE_ADMIN"} <= codes
        assert not any(c.startswith("CRM_") for c in codes)

    async def test_a_de_entitling_bundle_event_installs_nothing(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        await install_registration_on_activation(
            _suite_event(org.id, members=TRIO, state="suspended")
        )
        assert await _role_codes(session, org.id) == CORE_ROLES


class TestIdempotencyAndTenancy:
    async def test_redelivery_installs_nothing_twice(
        self, session: AsyncSession, org: Organization, owner: Membership
    ) -> None:
        event_id = uuid.uuid4()
        await install_registration_on_activation(
            _suite_event(org.id, members=TRIO, event_id=event_id)
        )

        async def _snapshot() -> tuple[int, int]:
            async with session.begin():
                roles = len(await selectors.list_roles(session, org.id))
                links = await session.execute(
                    select(func.count())
                    .select_from(MembershipRole)
                    .where(MembershipRole.membership_id == owner.id)
                )
                return roles, int(links.scalar_one())

        first = await _snapshot()
        await install_registration_on_activation(
            _suite_event(org.id, members=TRIO, event_id=event_id)
        )
        assert await _snapshot() == first, "a redelivered bundle installed or granted again"

    async def test_the_install_lands_only_in_the_events_organization(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        owner: Membership,
    ) -> None:
        async with admin_session.begin():
            other = await services.create_organization(
                admin_session, OrganizationIn(name="Beta Corp", slug="beta"), actor_user_id=None
            )
        await install_registration_on_activation(_suite_event(org.id, members=TRIO))

        async with admin_session.begin():
            theirs = {r.code for r in await selectors.list_roles(admin_session, other.id)}
        assert theirs == CORE_ROLES, (
            "a bundle installed for one organization leaked roles into another"
        )

    async def test_a_malformed_member_list_raises_rather_than_guessing(
        self, org: Organization, owner: Membership
    ) -> None:
        event = _suite_event(org.id, members=None)
        event["payload"]["entitled_products"] = "hrms,crm"
        with pytest.raises(ValueError, match="entitled_products"):
            await install_registration_on_activation(event)
