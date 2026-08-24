"""The loop's pivot — `commerce.payment.failed` suspends the subscription (ADR-0004 §5).

This is the subscription-service half of the entitlement loop, and the point of the consumer edit
in this phase. payment-service emits `commerce.payment.failed`; the handler here consumes it and
moves the named subscription to `suspended`, which emits `commerce.subscription.suspended` — the
event product-service already projects to a de-entitled `Activation` (see
product `tests/test_projection.py::test_suspended_de_entitles`), which the gateway already turns
into a 402. The properties proven here are the ones that link carries:

* an active subscription named by a failure event is suspended, and one `commerce.subscription.
  suspended` is emitted for the projection to consume;
* redelivery of the same event is a no-op (delivery is at-least-once);
* a subscription already terminal (`cancelled`) is left untouched — a failed payment cannot revive
  or double-suspend it;
* a failure for another tenant's subscription touches nothing (RLS).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.consumers import suspend_on_payment_failed
from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    OutboxEvent,
    Subscription,
)
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
    subscription_id: uuid.UUID | None,
    *,
    event_id: uuid.UUID | None = None,
    product_key: str = "hrms",
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "commerce.payment.failed",
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(org_id),
            "subscription_id": str(subscription_id) if subscription_id else None,
            "product_key": product_key,
            "amount_minor": 4900,
            "currency": "USD",
            "state": "failed",
        },
    }


async def _seed_subscription(
    session: AsyncSession, org_id: uuid.UUID, *, state: str = STATE_ACTIVE
) -> uuid.UUID:
    subscription_id = uuid.uuid4()
    async with session.begin():
        session.add(
            Subscription(
                id=subscription_id,
                organization_id=org_id,
                product_key="hrms",
                plan_version_id=uuid.uuid4(),
                state=state,
                currency="USD",
                current_period_start=datetime.now(UTC),
            )
        )
    return subscription_id


class TestPaymentFailedSuspends:
    async def test_a_failed_payment_suspends_an_active_subscription(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed_subscription(session, org_id)

        await suspend_on_payment_failed(_envelope(org_id, subscription_id))

        async with session.begin():
            subscription = await session.get(Subscription, subscription_id)
            emitted = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.subscription.suspended")
                )
            ).scalar_one()
        assert subscription is not None and subscription.state == "suspended"
        assert emitted == 1, "the projection needs exactly one commerce.subscription.suspended"

    async def test_redelivery_is_a_no_op(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        subscription_id = await _seed_subscription(session, org_id)
        envelope = _envelope(org_id, subscription_id)

        await suspend_on_payment_failed(envelope)
        await suspend_on_payment_failed(envelope)  # same event id — already processed

        async with session.begin():
            emitted = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.subscription.suspended")
                )
            ).scalar_one()
        assert emitted == 1, "a redelivered failure emitted a second suspension"

    async def test_a_cancelled_subscription_is_left_untouched(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No-dunning MVP still respects the state machine: a cancelled subscription is terminal,
        so a late payment failure neither suspends nor emits — it just records the event handled."""
        subscription_id = await _seed_subscription(session, org_id, state=STATE_CANCELLED)

        await suspend_on_payment_failed(_envelope(org_id, subscription_id))

        async with session.begin():
            subscription = await session.get(Subscription, subscription_id)
            emitted = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.subscription.suspended")
                )
            ).scalar_one()
        assert subscription is not None and subscription.state == "cancelled"
        assert emitted == 0

    async def test_another_tenants_subscription_is_untouched(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """A failure event bound to organization B cannot suspend organization A's subscription:
        under B's binding the id is invisible, so nothing is suspended and nothing is emitted."""
        subscription_id = await _seed_subscription(session, org_id)

        # Same subscription id, but the event names a *different* organization.
        await suspend_on_payment_failed(_envelope(other_org, subscription_id))

        async with session.begin():
            subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None and subscription.state == "active", (
            "another tenant's failure suspended this subscription"
        )
