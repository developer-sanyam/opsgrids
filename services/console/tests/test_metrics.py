"""Metric correctness on a seeded transition stream, folded through the real consumer path.

The events are fed through `consumers.consume` — the same tenant-bound, idempotent wrapper the
broker drives — with a fake price resolver in place of catalog, so the arithmetic is exercised end
to end without a broker or a catalog. Then the operator metrics are read on the directory session,
exactly as the read API serves them.

MRR is the running sum of active subscriptions at their pinned price; trial-conversion is
`trial → active` over `trial` starts; churn is `active → cancelled` (ADR-0004 §4). The `from_state`
each metric needs is derived by the fold from the prior snapshot, because the thin event payload
carries only the new state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, selectors, services
from app.models import RevenueEvent
from tests.helpers import envelope, make_fake_resolver, subscription_payload

pytestmark = pytest.mark.integration

PV1 = "11111111-1111-1111-1111-111111111111"
PRICES = {PV1: (50000, "INR")}


def _resolver():
    return make_fake_resolver(PRICES)


async def _fold_subscription(env: dict) -> None:
    resolver = _resolver()

    async def fold(session, envelope_):  # type: ignore[no-untyped-def]
        await services.fold_subscription_event(session, envelope_, resolve_price=resolver)

    await consumers.consume(env, fold)


def _sub(event: str, org: uuid.UUID, *, product: str, state: str, **kw) -> dict:
    return envelope(
        f"commerce.subscription.{event}",
        organization_id=org,
        payload=subscription_payload(org, product_key=product, state=state, **kw),
    )


class TestMetricsOnASeededStream:
    async def test_mrr_trial_conversion_and_churn(
        self,
        engine_ready: None,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        org_c: uuid.UUID,
    ) -> None:
        trial_end = (datetime.now(UTC) + timedelta(days=14)).isoformat()

        # Org A: a trial that converts — the trial-conversion numerator, and MRR.
        await _fold_subscription(
            _sub(
                "trial_started",
                org_id,
                product="hrms",
                state="trial",
                plan_version_id=PV1,
                trial_ends_at=trial_end,
            )
        )
        await _fold_subscription(
            _sub("activated", org_id, product="hrms", state="active", plan_version_id=PV1)
        )
        # Org B: a trial that has not converted — a trial start with no activation.
        await _fold_subscription(
            _sub(
                "trial_started",
                org_b,
                product="crm",
                state="trial",
                plan_version_id=PV1,
                trial_ends_at=trial_end,
            )
        )
        # Org C: activates directly (not from trial), then churns — the churn numerator.
        await _fold_subscription(
            _sub("activated", org_c, product="hrms", state="active", plan_version_id=PV1)
        )
        await _fold_subscription(
            _sub("cancelled", org_c, product="hrms", state="cancelled", plan_version_id=PV1)
        )

        metrics = await selectors.platform_metrics(directory_session)

        # MRR: only org A is active (org B is trialling, org C cancelled). One active sub at 50000.
        assert metrics["mrr_minor"] == 50000
        assert metrics["currency"] == "INR"
        assert metrics["active_subscriptions"] == 1

        # Trial-conversion: two trials started (A, B), one converted (A; C never started a trial).
        assert metrics["trials_started"] == 2
        assert metrics["trial_conversions"] == 1
        assert metrics["trial_conversion_rate"] == 0.5

        # Churn: one active → cancelled transition (org C).
        assert metrics["churned"] == 1
        assert metrics["churn_rate"] == 0.5

    async def test_a_suspension_locks_and_drops_mrr(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A suspend de-entitles: MRR falls to zero and the org is marked locked (ADR-0004 §5)."""
        await _fold_subscription(
            _sub("activated", org_id, product="hrms", state="active", plan_version_id=PV1)
        )
        states = await selectors.all_org_states(directory_session)
        assert states[0].mrr_minor == 50000 and states[0].locked is False

        await _fold_subscription(
            _sub("suspended", org_id, product="hrms", state="suspended", plan_version_id=PV1)
        )
        # A production read is a fresh per-request directory session; this one is reused across the
        # test, so drop its identity-map cache before re-reading the row the fold just changed.
        directory_session.expire_all()
        states = await selectors.all_org_states(directory_session)
        assert states[0].mrr_minor == 0, "a suspended subscription pays nothing"
        assert states[0].locked is True, "a suspension locks the organization"


class TestOutOfOrderFold:
    async def test_a_stale_suspension_does_not_relock_a_reactivated_org(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The service wiring over the domain guard: a suspension delivered *after* the
        reactivation that superseded it must fold nothing — the snapshot stays active and
        unlocked — while the stale arrival is still appended to the revenue log, marked
        `stale` so churn/conversion reads can exclude it."""
        reactivated_at = datetime.now(UTC)
        suspended_at = reactivated_at - timedelta(minutes=5)

        await _fold_subscription(
            envelope(
                "commerce.subscription.activated",
                organization_id=org_id,
                payload=subscription_payload(
                    org_id, product_key="hrms", state="active", plan_version_id=PV1
                ),
                occurred_at=reactivated_at,
            )
        )
        await _fold_subscription(
            envelope(
                "commerce.subscription.suspended",
                organization_id=org_id,
                payload=subscription_payload(
                    org_id, product_key="hrms", state="suspended", plan_version_id=PV1
                ),
                occurred_at=suspended_at,
            )
        )

        states = await selectors.all_org_states(directory_session)
        assert states[0].mrr_minor == 50000, "a stale suspension zeroed a reactivated org's MRR"
        assert states[0].locked is False, "a stale suspension locked a reactivated org"

        rows = (
            (
                await directory_session.execute(
                    select(RevenueEvent).where(
                        RevenueEvent.kind == "commerce.subscription.suspended"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "the stale arrival must still be recorded"
        assert rows[0].meta["stale"] is True, "a stale fold must be marked in the revenue log"


class TestIdempotentFold:
    async def test_a_redelivered_event_is_a_no_op(
        self, engine_ready: None, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Delivery is at-least-once; folding the same event twice must not double-count MRR or
        append a second revenue row."""
        event = _sub("activated", org_id, product="hrms", state="active", plan_version_id=PV1)

        await _fold_subscription(event)
        await _fold_subscription(event)  # exact redelivery — same event_id

        states = await selectors.all_org_states(directory_session)
        assert states[0].mrr_minor == 50000, "redelivery double-counted MRR"

        metrics = await selectors.platform_metrics(directory_session)
        # One activation transition recorded, not two.
        assert metrics["active_subscriptions"] == 1
