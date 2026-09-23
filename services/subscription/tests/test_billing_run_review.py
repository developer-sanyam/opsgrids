"""The billing run's review fixes (ADR-0005 addendum, "Review fixes, 2026-09-24"), one class each.

Same machinery as `test_billing_run.py` — real PostgreSQL, real locks, real RLS, fake billing and
payment that behave like the real routes — and each test fails against the code before its fix:

1. A conversion charge that fails before the run records the conversion opens the grace window
   instead of being dropped — in either order.
2. A stuck claim never blocks the batch behind it; a `one_time` cadence is a persisted,
   backed-off, permanent failure the health probe counts.
3. `past_due` cannot change plan; a capture for another invoice does not close the window.
4. An upgrade bills its prorated difference now; a downgrade waits for the next period.
5. A cancellation and a suspension claim their closing usage; a recovered suspension's next
   renewal does not bill that usage again.
7-13. Wall-clock leases; guarded failure writes; org-filtered locks; column-level grants; voided
   superseded invoices; the dry-run's split.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, selectors, services
from app.billing_run import BilledInvoice, BillingRunSweep, PeriodRequest
from app.config import Settings, settings
from app.models import (
    PERIOD_FINALIZED,
    PERIOD_PENDING,
    PERIOD_VOIDED,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    BillingPeriod,
    DunningCycle,
    Subscription,
)
from app.schemas import ChangePlanIn, PaymentOutcome, SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import BusinessRuleProblem
from tests.billing_fakes import Crash, FakeBilling, FakePayment, _make_sweep, expire_leases
from tests.helpers import FakeCatalog, customer_principal, make_version
from tests.test_billing_run import (
    MONTH,
    T0,
    _events,
    _periods,
    _seed,
    _subscription,
    _transitions,
)

pytestmark = pytest.mark.integration


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _wall() -> datetime:
    return datetime.now(UTC)


async def _cycle(session: AsyncSession, subscription_id: uuid.UUID) -> DunningCycle | None:
    session.expunge_all()
    async with session.begin():
        return (
            (
                await session.execute(
                    select(DunningCycle)
                    .where(DunningCycle.subscription_id == subscription_id)
                    .order_by(DunningCycle.opened_at.desc())
                )
            )
            .scalars()
            .first()
        )


async def _fail_charge(
    session: AsyncSession, subscription_id: uuid.UUID, invoice_id: uuid.UUID | None
) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await selectors.get_subscription(session, subscription_id)
        assert sub is not None
        await services.record_payment_failure(
            session,
            sub,
            PaymentOutcome(
                invoice_id=invoice_id,
                amount_minor=149900,
                currency="INR",
                failure_reason="insufficient_funds",
            ),
        )


async def _subscribe(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, amount_minor: int
) -> tuple[uuid.UUID, Any]:
    version = catalog.add(
        make_version(product_key="hrms", currency="INR", amount_minor=amount_minor)
    )
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(product_key="hrms", plan_version_id=version.id),
        )
        return sub.id, version


async def _change(
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    subscription_id: uuid.UUID,
    version_id: uuid.UUID,
) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.change_plan(
            session, _ctx(org_id), catalog, sub, ChangePlanIn(plan_version_id=version_id)
        )


# --------------------------------------------------------------------- 1. conversion charge


class TestConversionChargeFailure:
    async def test_nothing_is_chargeable_before_the_record_step(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Round 2 (M5) closes round 1's race at its root: billing's invoice is a draft until the
        run has recorded the conversion, so no charge — and no charge failure — can come first."""
        subscription_id = await _seed(session, org_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        assert billing.invoices and billing.finalized == [], "a draft was announced"
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_PENDING

    async def test_a_failure_for_another_invoice_is_not_stamped_on_the_conversion(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Round 2, m7: only a failure of the conversion's own invoice is written onto it."""
        subscription_id = await _seed(session, org_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        await _fail_charge(session, subscription_id, uuid.uuid4())
        (period,) = await _periods(session, subscription_id)
        assert period.payment_failed_at is None

    async def test_a_recorded_charge_failure_takes_past_due_whatever_the_mandate(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The record step still honours a failure written onto the claim (round 1, fix 1)."""
        subscription_id = await _seed(session, org_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        engine = create_async_engine(settings().admin_dsn)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE subscription.billing_periods SET payment_failed_at = now(), "
                    "payment_failure_reason = 'insufficient_funds' WHERE subscription_id = :s"
                ),
                {"s": subscription_id},
            )
        await engine.dispose()
        payment.usable = True
        await expire_leases(subscription_id)
        report = await run.tick(now=T0)
        assert report.past_due == 1 and report.activated == 0
        ((_, invoice),) = billing.invoices.items()
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None and cycle.invoice_id == invoice.invoice_id
        assert cycle.opened_at == T0

    async def test_a_failure_after_the_record_takes_the_ordinary_path(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)
        await run.tick(now=T0)
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE
        ((_, invoice),) = billing.invoices.items()

        await _fail_charge(session, subscription_id, invoice.invoice_id)
        assert (await _subscription(session, subscription_id)).state == STATE_PAST_DUE
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None and cycle.invoice_id == invoice.invoice_id


# --------------------------------------------------------------------- 2. head-of-line


class TestNoHeadOfLineBlocking:
    async def test_a_healthy_subscription_is_billed_behind_stuck_ones(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        run = _make_sweep(*sweep_engines, billing, payment)
        run._batch_size = 2
        stuck = [
            await _seed(session, org_id, product_key=key, trial_ends_at=T0 - timedelta(days=3))
            for key in ("crm", "itsm")
        ]
        healthy = await _seed(session, org_id, product_key="hrms", trial_ends_at=T0)
        billing.fail_for.update(stuck)

        first = await run.tick(now=T0)
        assert first.failed == 2
        assert (await _subscription(session, healthy)).state == STATE_TRIAL  # not reached yet

        second = await run.tick(now=T0)
        assert second.activated == 1
        assert (await _subscription(session, healthy)).state == STATE_ACTIVE
        for sub in stuck:
            (period,) = await _periods(session, sub)
            assert period.state == PERIOD_PENDING and period.attempts == 1

    async def test_discovery_does_not_return_a_subscription_that_is_backing_off(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        sweep_engines: tuple[Any, Any],
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The exclusion itself, apart from the ordering: a backing-off claim is not work."""
        stuck = await _seed(session, org_id)
        billing.fail_for.add(stuck)
        await run.tick(now=T0)
        discovery_engine, _ = sweep_engines
        async with async_sessionmaker(discovery_engine)() as s, s.begin():
            due = await selectors.due_billing_subscriptions(s, now=T0, wall=_wall())
        assert stuck not in {sid for _, sid in due}

    async def test_a_permanent_failure_is_counted_by_the_health_probe(
        self,
        run: BillingRunSweep,
        sweep_engines: tuple[Any, Any],
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        await _seed(session, org_id, period_start=None, period_end=None, interval="one_time")
        await run.tick(now=T0)
        discovery_engine, _ = sweep_engines
        async with async_sessionmaker(discovery_engine)() as s, s.begin():
            health = await selectors.billing_run_health(
                s, now=_wall(), overdue_after=timedelta(hours=1)
            )
        assert health["failing_permanent"] == 1
        assert health["failing"] == 1


# --------------------------------------------------------------------- 3. change-plan & capture


class TestPastDueGuards:
    @pytest.mark.parametrize("state", [STATE_PAST_DUE, STATE_SUSPENDED])
    async def test_a_subscription_that_owes_money_cannot_change_plan(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, state: str
    ) -> None:
        # A period that is not yet due, so the refusal is the owed-money rule rather than the
        # billing-run catch-up guard.
        subscription_id = await _seed(
            session,
            org_id,
            state=state,
            period_start=_wall() - timedelta(days=10),
            period_end=_wall() + timedelta(days=20),
        )
        target = catalog.add(make_version(product_key="hrms", currency="INR", amount_minor=99))
        with pytest.raises(BusinessRuleProblem, match="cannot change plan"):
            await _change(session, catalog, org_id, subscription_id, target.id)

    async def test_a_capture_for_another_invoice_does_not_close_the_window(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(
            session, org_id, state=STATE_ACTIVE, period_start=T0 - MONTH, period_end=T0
        )
        owed, other = uuid.uuid4(), uuid.uuid4()
        await _fail_charge(session, subscription_id, owed)

        async def capture(invoice_id: uuid.UUID) -> None:
            session.expunge_all()
            async with session.begin():
                sub = await selectors.get_subscription(session, subscription_id)
                assert sub is not None
                await services.record_payment_captured(
                    session, sub, PaymentOutcome(invoice_id=invoice_id)
                )

        await capture(other)
        assert (await _subscription(session, subscription_id)).state == STATE_PAST_DUE
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None and cycle.state == "open"

        await capture(owed)
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None and cycle.state == "recovered"


# --------------------------------------------------------------------- 4. upgrades & downgrades


class TestPlanChangeMoney:
    async def test_an_upgrade_mid_period_raises_one_proration_invoice(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000)
        high = catalog.add(make_version(product_key="hrms", currency="INR", amount_minor=200000))
        await _change(session, catalog, org_id, subscription_id, high.id)

        report = await run.tick(now=_wall())
        again = await run.tick(now=_wall())
        assert report.prorated == 1 and again.prorated == 0
        (request,) = [r for r in billing.requests if r.kind == domain.KIND_PRORATION]
        assert request.billing_kind == "proration"
        assert request.plan_version_id == high.id
        assert request.from_plan_version_id == low.id
        assert request.change_id is not None
        sub = await _subscription(session, subscription_id)
        assert request.period_end == sub.current_period_end
        assert request.full_period_start == sub.current_period_start
        (period,) = [p for p in await _periods(session, subscription_id) if p.kind == "proration"]
        assert period.state == PERIOD_FINALIZED and period.outcome == "prorated"
        assert period.invoice_id in billing.finalized

    async def test_a_downgrade_bills_nothing_now_and_prices_the_next_period_lower(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, high = await _subscribe(session, catalog, org_id, 200000)
        low = catalog.add(make_version(product_key="hrms", currency="INR", amount_minor=100000))
        await _change(session, catalog, org_id, subscription_id, low.id)

        await run.tick(now=_wall())
        # The first period is charged once, at the pin in force when it opened; the downgrade
        # moves no money now.
        (opening,) = billing.requests
        assert opening.kind == domain.KIND_OPENING and opening.plan_version_id == high.id
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == high.id and sub.pending_plan_version_id == low.id
        assert sub.current_period_end is not None

        await run.tick(now=sub.current_period_end)
        (request,) = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL]
        assert request.kind == domain.KIND_RENEWAL
        assert request.plan_version_id == low.id
        assert request.items == [{"price_id": str(low.prices[0].id), "quantity": 1}]
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == low.id and sub.pending_change is None
        assert len(await _events(session, domain.EVT_DOWNGRADED)) == 1
        assert domain.REASON_DOWNGRADE in await _transitions(session, subscription_id)

    async def test_up_then_down_within_a_period_keeps_the_proration_billed(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000)
        high = catalog.add(make_version(product_key="hrms", currency="INR", amount_minor=200000))
        await _change(session, catalog, org_id, subscription_id, high.id)
        await _change(session, catalog, org_id, subscription_id, low.id)

        report = await run.tick(now=_wall())
        assert report.prorated == 1
        assert len([r for r in billing.requests if r.kind == domain.KIND_PRORATION]) == 1
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == high.id
        assert sub.pending_plan_version_id == low.id


# --------------------------------------------------------------------- 5. closing usage


class TestClosingUsage:
    async def _active(self, session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
        wall = _wall()
        return await _seed(
            session,
            org_id,
            state=STATE_ACTIVE,
            period_start=wall - timedelta(days=10),
            period_end=wall + timedelta(days=20),
        )

    async def test_a_cancellation_bills_its_closing_usage(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await self._active(session, org_id)
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            start = sub.current_period_start
            await services.cancel(session, _ctx(org_id), sub)
            cancelled_at = sub.cancel_at

        report = await run.tick(now=_wall())
        assert report.closed == 1
        (request,) = billing.requests
        assert request.billing_kind == "close"
        assert (request.usage_start, request.usage_end) == (start, cancelled_at)
        assert (await _subscription(session, subscription_id)).state == STATE_CANCELLED

    async def test_a_suspension_closes_its_usage_and_the_next_renewal_does_not_rebill_it(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await self._active(session, org_id)
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            start, end = sub.current_period_start, sub.current_period_end
            await services.suspend(session, sub, operator_id="ops")
            suspended_at = sub.usage_billed_through
        assert suspended_at is not None

        await run.tick(now=_wall())
        (close,) = [r for r in billing.requests if r.kind == domain.KIND_CLOSE]
        assert (close.usage_start, close.usage_end) == (start, suspended_at)

        # Paid in the same period: recovered, then the period closes and rolls.
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.record_payment_captured(session, sub)
        assert end is not None
        await run.tick(now=end)
        (renewal,) = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL]
        assert (renewal.usage_start, renewal.usage_end) == (suspended_at, end)


# --------------------------------------------------------------------- 6. kill switch


def test_the_billing_run_is_off_unless_turned_on() -> None:
    assert Settings.model_fields["billing_run_enabled"].default is False


# --------------------------------------------------------------------- 7-13


class TestMinors:
    async def test_the_lease_is_on_the_wall_clock_not_the_synthetic_now(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.crash_before = True
        with pytest.raises(Crash):
            await run.tick(now=T0)  # three weeks before the wall clock
        (period,) = await _periods(session, subscription_id)
        assert period.lease_until is not None
        assert period.lease_until > _wall() + domain.BILLING_LEASE - timedelta(minutes=1)

    async def test_a_failure_report_does_not_touch_a_recorded_period(
        self, run: BillingRunSweep, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        await run.tick(now=T0)
        session.expunge_all()
        async with session.begin():
            period = (
                await session.execute(
                    select(BillingPeriod).where(BillingPeriod.subscription_id == subscription_id)
                )
            ).scalar_one()
            written = await services.fail_billing_period(session, period, error="late 503")
        assert written is False
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_FINALIZED and period.last_error is None

    async def test_locks_filter_the_organization_even_without_rls(
        self,
        run: BillingRunSweep,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)
        await run.tick(now=T0)
        (period,) = await _periods(session, subscription_id)
        async with admin_session.begin():
            assert (
                await selectors.lock_subscription(
                    admin_session, other_org, subscription_id, skip_locked=False
                )
                is None
            )
            assert (
                await selectors.lock_billing_period_by_id(admin_session, other_org, period.id)
                is None
            )
            assert (
                await selectors.lock_billing_period(
                    admin_session, other_org, subscription_id, period.kind, period.period_start
                )
                is None
            )
            assert (
                await selectors.lock_subscription(
                    admin_session, org_id, subscription_id, skip_locked=False
                )
                is not None
            )

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT plan_version_id FROM subscription.subscriptions",
            "SELECT currency FROM subscription.subscriptions",
            "SELECT items FROM subscription.billing_periods",
            "SELECT total_minor FROM subscription.billing_periods",
        ],
    )
    async def test_the_outbox_role_reads_only_the_discovery_columns(
        self, sweep_engines: tuple[Any, Any], session: AsyncSession, sql: str
    ) -> None:
        discovery_engine, _ = sweep_engines
        async with discovery_engine.connect() as conn:
            await conn.execute(text("SELECT id, state FROM subscription.subscriptions"))
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(sql))

    async def test_a_period_superseded_between_claim_and_record_is_voided(
        self,
        sweep_engines: tuple[Any, Any],
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The customer cancels while billing is pricing the conversion. The invoice billing made
        must not stand: the record step sends it to be voided, and the run voids it."""
        subscription_id = await _seed(session, org_id)

        class CancelDuringCall(FakeBilling):
            async def bill_period(self, request: PeriodRequest) -> BilledInvoice:
                answer = await super().bill_period(request)
                engine = create_async_engine(settings().admin_dsn)
                async with engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE subscription.subscriptions SET state='cancelled' WHERE id=:i"),
                        {"i": subscription_id},
                    )
                await engine.dispose()
                return answer

        billing = CancelDuringCall(settings().database_url)
        try:
            run = _make_sweep(*sweep_engines, billing, payment)
            report = await run.tick(now=T0)
        finally:
            await billing.aclose()
        assert report.superseded == 1 and report.voided == 1
        ((_, invoice),) = billing.invoices.items()
        assert billing.voided == {invoice.invoice_id}
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_VOIDED and period.invoice_id == invoice.invoice_id

    async def test_a_stale_claim_whose_invoice_exists_is_voided(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        async with session.begin():
            row = await session.get(Subscription, subscription_id)
            assert row is not None
            row.state = STATE_CANCELLED
        await expire_leases(subscription_id)
        report = await run.tick(now=T0)
        assert report.stale_closed == 1 and report.voided == 1
        ((_, invoice),) = billing.invoices.items()
        assert billing.voided == {invoice.invoice_id}

    async def test_the_dry_run_splits_invoiced_from_rolled_and_flags_expired_grace(
        self, run: BillingRunSweep, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        now = T0 + MONTH + timedelta(days=1)
        suspended = await _seed(
            session,
            org_id,
            state=STATE_SUSPENDED,
            product_key="crm",
            period_start=T0 - MONTH,
            period_end=T0,
        )
        old_trial = await _seed(session, org_id, trial_ends_at=T0 - timedelta(days=20))
        rows = {r["subscription_id"]: r for r in await run.preview(now=now)}
        assert rows[str(suspended)]["periods_suspended_roll"] == 2
        assert rows[str(suspended)]["periods_invoiced"] == 0
        assert rows[str(old_trial)]["grace_already_expired"] is True
        assert rows[str(old_trial)]["periods_invoiced"] >= 1


_ = Awaitable, Callable
