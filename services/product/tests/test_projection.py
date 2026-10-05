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
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
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

    async def test_past_due_still_entitles(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        """The grace window keeps the product working (ADR-0005 §2).

        A dunning cycle opening emits `past_due`, and this is the half of the policy that lives in
        product-service: if `past_due` de-entitled here, the whole retry ladder upstream would be
        theatre — the customer would have lost the product on the first failed debit and the two
        weeks of retries would be collecting for something already switched off. The 402 must land
        at the *end* of the window, not at its start.
        """
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.past_due", state="past_due")
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
            keys = await selectors.entitlements_for(session, org_id)
        assert activation is not None
        assert activation.status == "active", "past_due must project to an entitling activation"
        assert activation.entitles is True
        assert activation.deactivated_at is None
        assert "hrms" in keys, "the gateway would 402 a customer inside their grace window"

    async def test_the_whole_dunning_ladder_lands_the_402_only_at_the_end(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """past_due → past_due → suspended → activated, projected in order.

        The subscription-service half of this ladder is proven in that service's
        `tests/test_dunning.py`; this is the other end of the same chain — what the gateway would
        actually do at each rung. Entitlement survives every retry and stops exactly once, when the
        window closes, and comes back on the captured payment.
        """
        seen: list[bool] = []
        for event_type, state in (
            ("commerce.subscription.activated", "active"),
            ("commerce.subscription.past_due", "past_due"),
            ("commerce.subscription.past_due", "past_due"),
            ("commerce.subscription.suspended", "suspended"),
            ("commerce.subscription.activated", "active"),
        ):
            await project_subscription_event(_envelope(org_id, event_type=event_type, state=state))
            async with session.begin():
                keys = await selectors.entitlements_for(session, org_id)
            seen.append("hrms" in keys)

        assert seen == [True, True, True, False, True], (
            "entitlement must survive the grace window, stop when it closes, and return on payment"
        )

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


class TestOutOfOrderDelivery:
    """The `occurred_at` guard of migration a6b1c8d3e9f2.

    Idempotency (above) only dedupes an exact redelivery; a *distinct older* event folded late —
    multi-replica relays, consumer replicas sharing the queue, a DLQ replay — used to win by
    arriving last. The row now remembers when the fact it reflects occurred, and drops anything
    older. Delivery order must not change where the row ends up.
    """

    async def test_in_order_transitions_land_on_the_newer_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The baseline: delivered in event-time order, the newer transition wins (as ever)."""
        t1 = datetime.now(UTC) - timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=5)
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.activated",
                state="active",
                occurred_at=t1,
            )
        )
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.suspended",
                state="suspended",
                occurred_at=t2,
            )
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "suspended"
        assert activation.entitles is False

    async def test_the_same_transitions_out_of_order_also_land_on_the_newer_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The regression: the same two events, newer delivered first. Last-write-wins used to
        leave the row on the stale activation — the guard must drop it instead."""
        t1 = datetime.now(UTC) - timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=5)
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.suspended",
                state="suspended",
                occurred_at=t2,
            )
        )
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.activated",
                state="active",
                occurred_at=t1,
            )
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "suspended", (
            "a stale activation overwrote the newer suspension — the ordering guard failed"
        )
        assert activation.entitles is False

    async def test_a_reversed_suspend_reactivate_does_not_leave_the_customer_suspended(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The customer-facing shape of the bug: suspended then reactivated, delivered reversed.
        The late-arriving suspension is the stale one; the customer must stay entitled."""
        suspended_at = datetime.now(UTC) - timedelta(minutes=10)
        reactivated_at = suspended_at + timedelta(minutes=5)
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.activated",
                state="active",
                occurred_at=reactivated_at,
            )
        )
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.suspended",
                state="suspended",
                occurred_at=suspended_at,
            )
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "active", (
            "a stale suspension re-suspended a reactivated customer"
        )
        assert activation.entitles is True

    async def test_events_and_rows_without_occurred_at_still_fold(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No timestamp, no guard. A row with `state_occurred_at` NULL (the manual `activate`
        path writes these) accepts any event, and an event with no `occurred_at` folds onto a
        timestamped row — neither drops, neither crashes."""
        t1 = datetime.now(UTC) - timedelta(minutes=10)

        # An untimestamped event creates the row with state_occurred_at NULL — the same row
        # shape the manual activate path leaves behind.
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.state_occurred_at is None

        # A timestamped event folds onto the NULL row (nothing to compare against) and stamps it.
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.suspended",
                state="suspended",
                occurred_at=t1,
            )
        )
        async with session.begin():
            session.expire_all()
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "suspended"
        assert activation.state_occurred_at is not None

        # And an untimestamped event folds onto the timestamped row — no ordering claim, no drop.
        await project_subscription_event(
            _envelope(org_id, event_type="commerce.subscription.activated", state="active")
        )
        async with session.begin():
            session.expire_all()
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None
        assert activation.status == "active"


class TestTheGucIsLoadBearing:
    async def test_the_projected_write_fails_without_the_tenant_guc(
        self, unbound_app_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The RLS net. The consumer runs as `opsgrids_product` (NOBYPASSRLS); without a tenant
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


class TestTrialExtended:
    """`commerce.subscription.trial_extended` (ADR-0030): staff moved a trial's end later. The
    projection must take the new end, or a customer whose old end passed stays locked out while
    the trial they were promised is still running."""

    def test_the_consumer_binds_it(self) -> None:
        from app.consumers import SUBSCRIPTION_EVENTS, consumer

        assert "commerce.subscription.trial_extended" in SUBSCRIPTION_EVENTS
        assert "commerce.subscription.trial_extended" in consumer._handlers

    async def test_a_lapsed_trial_is_entitled_again_until_its_new_end(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        started = datetime.now(UTC) - timedelta(days=15)
        lapsed = datetime.now(UTC) - timedelta(hours=1)
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.trial_started",
                state="trial",
                trial_ends_at=lapsed.isoformat(),
                occurred_at=started,
            )
        )
        async with session.begin():
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None and activation.entitles is False

        new_end = datetime.now(UTC) + timedelta(days=10)
        envelope = _envelope(
            org_id,
            event_type="commerce.subscription.trial_extended",
            state="trial",
            trial_ends_at=new_end.isoformat(),
            occurred_at=datetime.now(UTC),
        )
        envelope["payload"]["previous_trial_ends_at"] = lapsed.isoformat()
        await project_subscription_event(envelope)
        async with session.begin():
            session.expire_all()
            activation = await selectors.get_activation(session, org_id, "hrms")
            keys = await selectors.entitlements_for(session, org_id)
        assert activation is not None
        assert activation.status == "trial" and activation.trial_ends_at == new_end
        assert activation.entitles is True
        assert "hrms" in keys

    async def test_a_stale_extension_does_not_undo_a_later_conversion(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Delivered after the conversion that followed it, the extension is older and dropped —
        the ordering guard every lifecycle event already gets."""
        t_extended = datetime.now(UTC) - timedelta(minutes=5)
        t_converted = datetime.now(UTC) - timedelta(minutes=1)
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.activated",
                state="active",
                occurred_at=t_converted,
            )
        )
        await project_subscription_event(
            _envelope(
                org_id,
                event_type="commerce.subscription.trial_extended",
                state="trial",
                trial_ends_at=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
                occurred_at=t_extended,
            )
        )
        async with session.begin():
            session.expire_all()
            activation = await selectors.get_activation(session, org_id, "hrms")
        assert activation is not None and activation.status == "active"
