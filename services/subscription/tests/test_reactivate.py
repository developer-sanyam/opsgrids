"""Operator reactivation — `services.reactivate` and the internal reactivate route.

`POST /internal/v1/subscriptions/{id}/reactivate` is `suspend`'s inverse, the delegate console's
`reactivate` override calls.

What has to hold, in the order the money and the audit trail depend on it:

1. **Only a suspended subscription reactivates.** Any other state is a **409** (not the state
   machine's 422), writes nothing, and — being a conflict rather than a repeat — means a retry of
   an already-applied reactivation cannot emit a second `activated` event.
2. **It is `suspend` run backwards, on the record:** state `active`, `suspended_at` cleared, one
   transition row `suspended -> active` naming the operator and their reason, one audit row, one
   `commerce.subscription.activated` event.
3. **A reason is required**, non-blank, and may not borrow one only the platform's machinery
   records.
4. **Console only.** The route takes the narrower caller check `suspend` predates — any other
   service's token, however genuine, is refused — and a request naming the wrong organization
   finds nothing (RLS), leaving the real subscription suspended.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_SUSPENDED,
    AuditLog,
    BillingPeriod,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from app.schemas import SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from platform_core.internal_auth import mint_service_token
from tests.helpers import FakeCatalog, customer_principal, make_version

pytestmark = pytest.mark.integration


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _headers(*, caller: str = "console") -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="subscription", caller=caller)
    }


async def _suspended_subscription(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> uuid.UUID:
    version = catalog.add(make_version(product_key="hrms", amount_minor=1000))
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
        await services.suspend(session, fresh, operator_id="ops-1", reason="nonpayment")
    return sub_id


async def _events(admin_session: AsyncSession, org_id: uuid.UUID) -> list[str]:
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
    return [r.event_type for r in rows]


class TestReactivateService:
    async def test_it_lifts_the_suspension_on_the_record(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            assert fresh.state == STATE_SUSPENDED and fresh.suspended_at is not None
            reactivated = await services.reactivate(
                session, fresh, operator_id="ops-2", reason="paid offline"
            )

        assert reactivated.state == STATE_ACTIVE
        assert reactivated.suspended_at is None

        transitions = (
            (
                await admin_session.execute(
                    select(SubscriptionTransition)
                    .where(SubscriptionTransition.subscription_id == sub_id)
                    .order_by(SubscriptionTransition.created_at)
                )
            )
            .scalars()
            .all()
        )
        last = transitions[-1]
        assert (last.from_state, last.to_state) == (STATE_SUSPENDED, STATE_ACTIVE)
        assert last.reason == "paid offline"
        assert last.actor == {"type": "operator", "id": "ops-2"}

        assert (await _events(admin_session, org_id))[-1] == "commerce.subscription.activated"

        audit = (
            (
                await admin_session.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_id == sub_id, AuditLog.action.like("%activated"))
                    .order_by(AuditLog.at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert audit is not None
        assert audit.acting_as == "ops-2"
        assert audit.reason == "paid offline"

    @pytest.mark.parametrize("state", [STATE_ACTIVE, STATE_CANCELLED])
    async def test_any_state_other_than_suspended_is_a_conflict_that_writes_nothing(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        state: str,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
            sub_id = sub.id
        if state == STATE_CANCELLED:
            async with session.begin():
                fresh = await services.require_subscription(session, sub_id)
                await services.cancel(session, _ctx(org_id), fresh, reason="done")
        events_before = await _events(admin_session, org_id)

        with pytest.raises(ConflictProblem):
            async with session.begin():
                fresh = await services.require_subscription(session, sub_id)
                await services.reactivate(session, fresh, operator_id="ops-2", reason="oops")

        assert await _events(admin_session, org_id) == events_before

    async def test_a_retry_after_success_is_a_conflict_not_a_second_activation(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.reactivate(session, fresh, operator_id="ops-2", reason="first")
        activated = (await _events(admin_session, org_id)).count("commerce.subscription.activated")

        with pytest.raises(ConflictProblem):
            async with session.begin():
                fresh = await services.require_subscription(session, sub_id)
                await services.reactivate(session, fresh, operator_id="ops-2", reason="retry")

        after = (await _events(admin_session, org_id)).count("commerce.subscription.activated")
        assert after == activated

    async def test_a_reason_only_the_machinery_records_is_refused(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                fresh = await services.require_subscription(session, sub_id)
                await services.reactivate(
                    session, fresh, operator_id="ops-2", reason="dunning_recovered"
                )


async def _openings(admin_session: AsyncSession, sub_id: uuid.UUID) -> list[BillingPeriod]:
    rows = (
        (
            await admin_session.execute(
                select(BillingPeriod).where(
                    BillingPeriod.subscription_id == sub_id, BillingPeriod.kind == "opening"
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


class TestReactivateMoney:
    """Money follows the platform's existing "back from suspension mid-period" policy — the same
    `charge_uncovered_remainder` the dunning-recovery resume calls — never a second rule."""

    async def test_a_period_already_paid_for_is_not_charged_again(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        before = await _openings(admin_session, sub_id)
        assert len(before) == 1, "subscribing claimed the period's charge"

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.reactivate(session, fresh, operator_id="ops-2", reason="paid offline")

        assert len(await _openings(admin_session, sub_id)) == 1

    async def test_an_uncovered_remainder_is_charged_from_now(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        # The period's charge never landed (a lagging run, a claim lost): nothing covers `now`.
        async with admin_session.begin():
            await admin_session.execute(
                delete(BillingPeriod).where(
                    BillingPeriod.subscription_id == sub_id, BillingPeriod.kind == "opening"
                )
            )

        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            period_end = fresh.current_period_end
            await services.reactivate(session, fresh, operator_id="ops-2", reason="paid offline")

        (opening,) = await _openings(admin_session, sub_id)
        assert opening.period_end == period_end
        assert opening.period_start > datetime.now(UTC) - timedelta(minutes=5), (
            "the remainder is charged from the moment access came back, not from the period start"
        )


class TestReactivateRoute:
    async def test_console_reactivates_a_suspended_subscription(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/reactivate",
            json={
                "organization_id": str(org_id),
                "operator_id": "operator:abc",
                "reason": "customer paid",
            },
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == STATE_ACTIVE

    async def test_a_subscription_that_is_not_suspended_is_a_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms", amount_minor=1000))
        async with session.begin():
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="hrms", plan_version_id=version.id),
            )
            sub_id = sub.id
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/reactivate",
            json={"organization_id": str(org_id), "reason": "oops"},
            headers=_headers(),
        )
        assert response.status_code == 409, response.text
        assert "suspended" in response.json()["detail"]

    async def test_a_reason_is_required_and_blank_is_not_a_reason(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        url = f"/internal/v1/subscriptions/{uuid.uuid4()}/reactivate"
        missing = await client.post(url, json={"organization_id": str(org_id)}, headers=_headers())
        blank = await client.post(
            url, json={"organization_id": str(org_id), "reason": "   "}, headers=_headers()
        )
        assert missing.status_code == 422
        assert blank.status_code == 422

    async def test_unknown_fields_are_rejected(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/reactivate",
            json={"organization_id": str(org_id), "reason": "ok", "state": "active"},
            headers=_headers(),
        )
        assert response.status_code == 422

    async def test_an_unknown_subscription_is_a_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/reactivate",
            json={"organization_id": str(org_id), "reason": "ok"},
            headers=_headers(),
        )
        assert response.status_code == 404

    async def test_naming_another_organization_finds_nothing_and_changes_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        sub_id = await _suspended_subscription(session, catalog, org_id)
        response = await client.post(
            f"/internal/v1/subscriptions/{sub_id}/reactivate",
            json={"organization_id": str(other_org), "reason": "not mine"},
            headers=_headers(),
        )
        assert response.status_code == 404
        state = (
            await admin_session.execute(select(Subscription.state).where(Subscription.id == sub_id))
        ).scalar_one()
        assert state == STATE_SUSPENDED

    async def test_without_a_service_token_it_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/reactivate",
            json={"organization_id": str(org_id), "reason": "ok"},
        )
        assert response.status_code == 401

    async def test_a_service_token_from_another_caller_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            f"/internal/v1/subscriptions/{uuid.uuid4()}/reactivate",
            json={"organization_id": str(org_id), "reason": "ok"},
            headers=_headers(caller="some-other-service"),
        )
        assert response.status_code == 401
