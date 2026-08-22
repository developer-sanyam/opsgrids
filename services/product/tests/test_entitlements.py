"""What an organization may use, and when it stops being allowed to.

`entitlements_for` is the source of every 402 in the platform: auth-service calls it while
minting a token, the list becomes the `ent` claim, and the gateway compares a route's product
against that claim on every request. A mistake here is either a customer locked out of
something they paid for, or one using something they did not.

The trial-expiry tests matter more than they look. Expiry is evaluated when the question is
asked rather than written into `status` by a nightly job, so these assert the behaviour that
makes "the job has not run yet" stop being a reason to keep serving a product.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import Activation

pytestmark = pytest.mark.integration


async def _add(
    session: AsyncSession,
    org_id: uuid.UUID,
    key: str,
    *,
    status: str = "active",
    trial_ends_at: datetime | None = None,
    deactivated_at: datetime | None = None,
) -> Activation:
    activation = Activation(
        organization_id=org_id,
        product_key=key,
        status=status,
        trial_ends_at=trial_ends_at,
        deactivated_at=deactivated_at,
    )
    session.add(activation)
    await session.flush()
    return activation


class TestWhatEntitles:
    async def test_platform_products_need_no_activation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Every customer has an organization and a profile, whatever they bought.

        The gateway makes the same call the other way round: a route with no product is never
        gated. If these disagree, a customer with no subscription cannot reach their own
        account settings.
        """
        async with session.begin():
            keys = await selectors.entitlements_for(session, org_id)
        assert "core" in keys

    async def test_an_active_product_entitles(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            await _add(session, org_id, "hrms", status="active")
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" in keys

    async def test_a_live_trial_entitles(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        async with session.begin():
            await _add(
                session,
                org_id,
                "crm",
                status="trial",
                trial_ends_at=datetime.now(UTC) + timedelta(days=3),
            )
            keys = await selectors.entitlements_for(session, org_id)
        assert "crm" in keys

    async def test_a_lapsed_trial_does_not_entitle(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The row still says `trial`. The clock says otherwise, and the clock wins.

        Nothing has run to update `status` — that is the point. Evaluating expiry at question
        time means a lapsed trial stops working the moment it lapses, not whenever a job next
        runs.
        """
        async with session.begin():
            await _add(
                session,
                org_id,
                "crm",
                status="trial",
                trial_ends_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            keys = await selectors.entitlements_for(session, org_id)
        assert "crm" not in keys

    async def test_a_deactivated_product_does_not_entitle(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The row is kept so billing can answer "when did they have this?" — but it stops
        opening the door immediately."""
        async with session.begin():
            await _add(
                session,
                org_id,
                "hrms",
                status="active",
                deactivated_at=datetime.now(UTC),
            )
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" not in keys

    async def test_a_suspended_product_does_not_entitle(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            await _add(session, org_id, "hrms", status="suspended")
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" not in keys


class TestOneTenantsActivationsAreItsOwn:
    async def test_another_organizations_activation_is_invisible(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Row-level security, asserted rather than assumed.

        The query carries no organization filter — `entitlements_for` scopes by the tenant
        binding, and the database enforces it. A leak here would hand one customer another's
        product access.
        """
        stranger = uuid.uuid4()
        async with session.begin():
            # Written through the tenant-bound session, so this row belongs to `org_id`.
            await _add(session, org_id, "hrms", status="active")
            mine = await selectors.entitlements_for(session, org_id)
            theirs = await selectors.entitlements_for(session, stranger)

        assert "hrms" in mine
        assert "hrms" not in theirs, (
            "an activation belonging to one organization was visible to another"
        )
