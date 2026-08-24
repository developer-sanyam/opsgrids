"""The entitlement projection — commerce.subscription.* → Activation → the 402 gate.

This is the point of the phase (ADR-0004 §5): the subscription service owns the lifecycle and
emits an event on every transition; the product consumer turns that into the `Activation` row the
gateway reads, with **no nightly job** — `Activation.entitles` already evaluates expiry at read
time. The properties proven here are the ones the honesty of the gate rests on:

* an `activated` event projects an activation that **entitles**; a `suspended` one that does not;
* redelivery of the same event is a **no-op** (delivery is at-least-once);
* the projected write **fails without the tenant GUC** — the RLS net, proving the consumer's
  per-message `session_scope(organization_id=...)` is load-bearing, not decorative;
* one organization's projection is invisible to another (RLS).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.consumers import project_subscription_event
from platform_core.db import dispose_engine, init_engine

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


def _envelope(
    org_id: uuid.UUID,
    *,
    event_type: str,
    state: str,
    trial_ends_at: str | None = None,
    product_key: str = "hrms",
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(org_id),
            "product_key": product_key,
            "state": state,
            "trial_ends_at": trial_ends_at,
        },
    }


class TestProjection:
    async def test_activated_projects_an_entitling_activation(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "active"
        assert activation.entitles is True

    async def test_trial_started_entitles_until_it_lapses(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ends = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.trial_started",
                state="trial",
                trial_ends_at=ends,
            )
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "trial"
        assert activation.entitles is True

    async def test_suspended_de_entitles(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.suspended", state="suspended")
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "suspended"
        assert activation.deactivated_at is not None
        assert activation.entitles is False

    async def test_cancelled_de_entitles(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.cancelled", state="cancelled")
        )
        async with session.begin():
            keys = await selectors.entitlements_for(session, org_id)
        assert "hrms" not in keys

    async def test_redelivery_is_a_no_op(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        """At-least-once delivery: the same event twice must not double anything.

        The event is delivered as `active`; a redelivery carrying `suspended` under the **same
        event id** must be ignored, because the id was already processed — so the activation stays
        entitling. This proves idempotency guards the side effect, not merely the row count.
        """
        activated = _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        await project_subscription_event(activated)

        # Same event id, contradictory state — must be dropped as already processed.
        redelivered = dict(activated)
        redelivered["event_type"] = "commerce.subscription.suspended"
        redelivered["payload"] = {**activated["payload"], "state": "suspended"}
        await project_subscription_event(redelivered)

        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "active", (
            "a redelivered event changed state — idempotency failed"
        )
        assert activation.entitles is True

    async def test_another_organizations_projection_is_invisible(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        stranger = uuid.uuid4()
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        async with session.begin():
            theirs = await selectors.entitlements_for(session, stranger)
        assert "hrms" not in theirs


class TestTheGucIsLoadBearing:
    async def test_the_projected_write_fails_without_the_tenant_guc(
        self, unbound_app_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The RLS net. The consumer runs as `atmora_product` (NOBYPASSRLS); without a tenant
        bound, `activations` is under a forced policy whose `WITH CHECK` is false, so the insert is
        refused. This is exactly what the per-message `session_scope(organization_id=...)` prevents
        — proven by omitting it."""
        with pytest.raises(Exception, match="row-level security"):
            async with unbound_app_session.begin():
                await services.project_subscription(
                    unbound_app_session,
                    organization_id=org_id,
                    product_key="hrms",
                    state="active",
                    trial_ends_at=None,
                )
