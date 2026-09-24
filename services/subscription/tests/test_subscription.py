"""The load-bearing behaviours of the subscription service, against real PostgreSQL.

The lifecycle is the place entitlement is decided (ADR-0004 §4/§5), so the properties that carry
weight are the ones that keep it honest:

* a subscribe pins a validated, published, visible catalog version, and every accepted move emits
  exactly one `commerce.subscription.*` event with the ADR-0000 §7 envelope;
* a change-plan re-pins and **preserves the old pin** on the transition row — history is never
  overwritten (ADR-0004 §2);
* the transition stream is append-only at the database;
* the state machine refuses an illegal move;
* one organization cannot see or touch another's subscription (RLS);
* the platform permission is required, and the internal suspend needs a service token.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from app.schemas import ChangePlanIn, OperatorChangePlanIn, SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import build_envelope
from tests.helpers import FakeCatalog, FakeRegistry, customer_principal, make_version

pytestmark = pytest.mark.integration


def _ctx(org_id: uuid.UUID, permission: str = "core.product.activation.write") -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id), scope=Scope.ORG, permission=permission
    )


async def _outbox(admin_session: AsyncSession, org_id: uuid.UUID) -> list[OutboxEvent]:
    rows = (
        (
            await admin_session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.organization_id == org_id)
                .order_by(OutboxEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# --------------------------------------------------------------------- subscribe


class TestSubscribe:
    async def test_subscribe_active_pins_and_emits(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms", currency="USD", amount_minor=1000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )

        assert sub.state == STATE_ACTIVE
        assert sub.plan_version_id == version.id
        assert sub.currency == "USD"
        assert [i.price_id for i in sub.items] == [version.prices[0].id]

        # One transition, birth -> active, and one matching event with the §7 envelope.
        rows = await _outbox(admin_session, org_id)
        assert [r.event_type for r in rows] == ["commerce.subscription.activated"]
        envelope = build_envelope(rows[0], producer="subscription")
        assert envelope["aggregate_type"] == "subscription"
        assert envelope["payload"]["state"] == "active"
        assert envelope["payload"]["product_key"] == "hrms"
        assert envelope["payload"]["plan_version_id"] == str(version.id)
        # The §7 envelope carries provenance keys even when null.
        assert "correlation_id" in envelope and "causation_id" in envelope
        assert envelope["actor"]["type"] == "user"

    async def test_subscribe_trial_starts_a_trial(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="crm"))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="crm", plan_version_id=version.id, trial_days=14),
            )
        assert sub.state == STATE_TRIAL
        assert sub.trial_ends_at is not None
        rows = await _outbox(admin_session, org_id)
        assert [r.event_type for r in rows] == ["commerce.subscription.trial_started"]
        assert rows[0].payload["trial_ends_at"] is not None

    async def test_a_second_subscription_for_a_product_conflicts(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version = catalog.add(make_version(product_key="hrms"))
        async with session.begin():
            await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="hrms", plan_version_id=version.id),
                )

    async def test_an_unpublished_version_is_refused(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        draft = catalog.add(make_version(product_key="hrms", state="draft"))
        with pytest.raises(BusinessRuleProblem, match="not published"):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="hrms", plan_version_id=draft.id),
                )

    async def test_a_bespoke_version_for_another_org_is_hidden(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """The audience-of-one rule (ADR-0004 §2): a version negotiated for another organization
        is refused with the same 404 a nonexistent one gets — its existence is never disclosed."""
        bespoke = catalog.add(make_version(product_key="hrms", restricted_to_org_id=other_org))
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="hrms", plan_version_id=bespoke.id),
                )

    async def test_an_unknown_version_is_not_found(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="hrms", plan_version_id=uuid.uuid4()),
                )


# --------------------------------------------------------------------- change-plan / pin history


class TestChangePlan:
    async def test_change_plan_repins_and_preserves_the_old_pin(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        v1 = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        v2 = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=v1.id),
            )
            sub_id = sub.id

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.change_plan(
                session, _ctx(org_id), catalog, fresh, ChangePlanIn(plan_version_id=v2.id)
            )

        # The live subscription now points at v2; the previous pin survives on the transition row.
        async with session.begin():
            after = await services.require_subscription(session, sub_id)
        assert after.plan_version_id == v2.id
        assert [i.price_id for i in after.items] == [v2.prices[0].id]

        transitions = (
            (
                await admin_session.execute(
                    select(SubscriptionTransition)
                    .where(SubscriptionTransition.subscription_id == sub_id)
                    .order_by(SubscriptionTransition.occurred_at)
                )
            )
            .scalars()
            .all()
        )
        change = transitions[-1]
        assert change.from_plan_version_id == v1.id, "the previous pin must be preserved in history"
        assert change.to_plan_version_id == v2.id
        assert change.reason == domain.REASON_UPGRADE

        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type == "commerce.subscription.upgraded"

    async def test_a_cheaper_plan_is_a_downgrade(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        v1 = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        v2 = catalog.add(make_version(product_key="hrms", amount_minor=500))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=v1.id),
            )
            sub_id = sub.id
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.change_plan(
                session, _ctx(org_id), catalog, fresh, ChangePlanIn(plan_version_id=v2.id)
            )
        # A paid downgrade waits for the next period (ADR-0005 addendum §4): nothing is emitted
        # and the pin does not move now; the billing run applies it — and emits `downgraded` —
        # when it claims the next period (tests/test_billing_run_review.py).
        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type != "commerce.subscription.downgraded"
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
        assert fresh.plan_version_id == v1.id
        assert fresh.pending_plan_version_id == v2.id


# --------------------------------------------------------------------- operator-forced timing
# (fix round 1, PAID-UPGRADE TIMING): an operator re-pin on an active, paying subscription
# always waits for the next period when `timing="next_period"` is asked for — up or down —
# rather than letting an upgrade prorate and charge immediately the way a customer's own
# change-plan does.


class TestOperatorForcedTiming:
    async def test_an_upgrade_forced_to_next_period_does_not_charge_now(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        v1 = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        v2 = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=v1.id),
            )
            sub_id = sub.id

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            result = await services.change_plan_as_operator(
                session,
                catalog,
                FakeRegistry(),
                fresh,
                OperatorChangePlanIn(
                    organization_id=org_id,
                    plan_version_id=v2.id,
                    operator_id="operator:test",
                    timing="next_period",
                ),
            )

        # Without `timing`, this exact upgrade re-pins and prorates immediately
        # (test_change_plan_repins_and_preserves_the_old_pin, above) — forced to the next period
        # instead, nothing about the live pin or the money moves now.
        assert result.plan_version_id == v1.id, "the pin must not move now"
        assert result.pending_plan_version_id == v2.id

        rows = await _outbox(admin_session, org_id)
        assert not any(r.event_type == "commerce.subscription.upgraded" for r in rows), (
            "an operator-forced next-period re-pin must not emit the immediate-upgrade event"
        )

        audit = (
            (
                await admin_session.execute(
                    select(AuditLog)
                    .where(AuditLog.organization_id == org_id)
                    .order_by(AuditLog.at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        assert audit is not None
        assert audit.action == "commerce.subscription.change_scheduled", (
            "an upgrade deferred by operator request is not a 'downgrade', and the audit trail "
            "must not call it one"
        )
        assert audit.reason == domain.REASON_UPGRADE

    async def test_a_downgrade_forced_to_next_period_keeps_the_downgrade_action_and_reason(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        v1 = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        v2 = catalog.add(make_version(product_key="hrms", amount_minor=500))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=v1.id),
            )
            sub_id = sub.id

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            result = await services.change_plan_as_operator(
                session,
                catalog,
                FakeRegistry(),
                fresh,
                OperatorChangePlanIn(
                    organization_id=org_id,
                    plan_version_id=v2.id,
                    operator_id="operator:test",
                    timing="next_period",
                ),
            )

        assert result.plan_version_id == v1.id
        assert result.pending_plan_version_id == v2.id

        audit = (
            (
                await admin_session.execute(
                    select(AuditLog)
                    .where(AuditLog.organization_id == org_id)
                    .order_by(AuditLog.at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        assert audit is not None
        assert audit.action == "commerce.subscription.downgrade_scheduled"
        assert audit.reason == domain.REASON_DOWNGRADE

    async def test_a_trial_ignores_the_forced_timing_and_repins_immediately(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """There is no money to defer on a trial with no claimed conversion — `paid` is false
        regardless of `timing`, so a forced `next_period` is simply a no-op guard, never a
        subscription stuck refusing to move because console sent a timing hint it did not need
        to check the state for first."""
        v1 = catalog.add(make_version(product_key="hrms", amount_minor=1000, state="published"))
        v2 = catalog.add(make_version(product_key="hrms", amount_minor=2000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=v1.id, trial_days=14),
            )
            sub_id = sub.id
            assert sub.state == STATE_TRIAL

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            result = await services.change_plan_as_operator(
                session,
                catalog,
                FakeRegistry(),
                fresh,
                OperatorChangePlanIn(
                    organization_id=org_id,
                    plan_version_id=v2.id,
                    operator_id="operator:test",
                    timing="next_period",
                ),
            )

        assert result.plan_version_id == v2.id, "a trial re-pins immediately regardless of timing"
        assert result.pending_plan_version_id is None


# --------------------------------------------------------------------- append-only


class TestAppendOnly:
    async def _seed(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> uuid.UUID:
        version = catalog.add(make_version(product_key="hrms"))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
            return sub.id

    async def test_update_of_a_transition_is_denied(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        await self._seed(session, catalog, org_id)
        with pytest.raises(IntegrityError, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("UPDATE subscription.subscription_transitions SET reason = 'tamper'")
                )

    async def test_delete_of_a_transition_is_denied(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        await self._seed(session, catalog, org_id)
        with pytest.raises(IntegrityError, match="append-only"):
            async with session.begin():
                await session.execute(text("DELETE FROM subscription.subscription_transitions"))


# --------------------------------------------------------------------- the state machine (pure)


class TestStateMachine:
    def test_cancel_to_active_is_illegal(self) -> None:
        """The negative case that matters: a cancelled subscription is terminal."""
        with pytest.raises(domain.IllegalTransition):
            domain.decide(from_state=STATE_CANCELLED, to_state=STATE_ACTIVE)

    def test_suspended_to_active_is_a_reactivation(self) -> None:
        decision = domain.decide(from_state=STATE_SUSPENDED, to_state=STATE_ACTIVE)
        assert decision.event_type == domain.EVT_ACTIVATED

    def test_trial_converts_to_active(self) -> None:
        decision = domain.decide(from_state=STATE_TRIAL, to_state=STATE_ACTIVE)
        assert decision.event_type == domain.EVT_ACTIVATED

    def test_a_renewal_keeps_active_and_emits_renewed(self) -> None:
        decision = domain.decide(
            from_state=STATE_ACTIVE, to_state=STATE_ACTIVE, reason=domain.REASON_RENEW
        )
        assert decision.event_type == domain.EVT_RENEWED

    def test_past_due_from_active_is_legal(self) -> None:
        assert domain.is_legal(STATE_ACTIVE, "past_due")

    def test_active_from_cancelled_is_not_legal(self) -> None:
        assert not domain.is_legal(STATE_CANCELLED, STATE_ACTIVE)


# --------------------------------------------------------------------- cancel


class TestCancel:
    async def test_cancel_moves_to_cancelled_and_emits(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms"))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
            sub_id = sub.id
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            cancelled = await services.cancel(session, _ctx(org_id), fresh, reason="done")
        assert cancelled.state == STATE_CANCELLED
        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type == "commerce.subscription.cancelled"


# --------------------------------------------------------------------- cross-tenant isolation


class TestCrossTenant:
    async def test_another_orgs_subscription_is_invisible(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """Seed a subscription for another organization on the bypass session; the org-bound
        query cannot see it — RLS, asserted rather than assumed."""
        stranger = Subscription(
            organization_id=other_org,
            product_key="hrms",
            plan_version_id=uuid.uuid4(),
            state=STATE_ACTIVE,
            currency="USD",
        )
        admin_session.add(stranger)
        await admin_session.commit()

        async with session.begin():
            found = await selectors.get_subscription(session, stranger.id)
        assert found is None, "one organization's subscription leaked to another"

    async def test_cancelling_another_orgs_subscription_is_404(
        self,
        client,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        stranger = Subscription(
            organization_id=other_org,
            product_key="hrms",
            plan_version_id=uuid.uuid4(),
            state=STATE_ACTIVE,
            currency="USD",
        )
        admin_session.add(stranger)
        await admin_session.commit()

        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id)
        resp = await client.post(f"/api/v1/subscriptions/{stranger.id}/cancel", json={})
        assert resp.status_code == 404


# --------------------------------------------------------------------- negative-authz & internal


class TestAuthorization:
    async def test_subscribe_without_permission_is_forbidden(
        self, client, org_id: uuid.UUID
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id, perms=[])
        resp = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": "hrms", "plan_version_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 403

    async def test_list_without_permission_is_forbidden(self, client, org_id: uuid.UUID) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id, perms=[])
        resp = await client.get("/api/v1/subscriptions")
        assert resp.status_code == 403

    async def test_internal_suspend_requires_a_service_token(self, client) -> None:
        # Root-mounted, not under /api/v1. No X-Internal-Token → unauthorized.
        resp = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/suspend",
            json={"organization_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401


class TestSuspendService:
    async def test_suspend_flips_state_and_emits(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms"))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
            sub_id = sub.id
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            suspended = await services.suspend(
                session, fresh, operator_id="ops-1", reason="nonpayment"
            )
        assert suspended.state == STATE_SUSPENDED
        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type == "commerce.subscription.suspended"
        assert rows[-1].actor["type"] == "operator"
