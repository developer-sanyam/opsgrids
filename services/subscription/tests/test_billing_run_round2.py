"""The billing run's second review round (ADR-0005 addendum, "Review fixes, round 2").

Same machinery as the other billing-run suites. Every test here fails against the code before its
fix:

* **B1** a change to another cadence (or to `one_time`) is refused; direction is per-day.
* **B2** an upgrade while a renewal or a trial conversion is claimed is prorated against the
  claim's period; a downgrade scheduled after a claim is not applied under that claim's snapshot.
* **M3** usage up to a pin change is closed at the old pin — immediate, deferred, and equal-price.
* **M4** a suspension or cancellation with a renewal claim open neither double-bills nor wedges
  (a period that opened before the suspension is charged once — round 3);
  cancel and record serialise on the row; any failure backs the claim off.
* **M5** run invoices are drafts until the record step commits; a superseded one is voided and never
  announced; a crash between record and finalize finalizes exactly once.
* **m9** a failed closing-period charge on a cancelled subscription is counted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, selectors, services
from app.billing_run import BillingRunSweep
from app.config import settings
from app.models import (
    PERIOD_FINALIZE_PENDING,
    PERIOD_FINALIZED,
    PERIOD_PENDING,
    STATE_ACTIVE,
    STATE_TRIAL,
    BillingPeriod,
)
from app.schemas import ChangePlanIn, PaymentOutcome, SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.db import TENANT_GUC
from tests.billing_fakes import Crash, FakeBilling, expire_leases
from tests.helpers import FakeCatalog, customer_principal, make_version
from tests.test_billing_run import _periods, _subscription

pytestmark = pytest.mark.integration

DAY = timedelta(days=1)


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


def _wall() -> datetime:
    return datetime.now(UTC)


async def _admin(sql: str, **params: Any) -> None:
    engine = create_async_engine(settings().admin_dsn)
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)
    await engine.dispose()


async def _subscribe(
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    amount_minor: int,
    *,
    interval: str = "month",
    trial: bool = False,
) -> tuple[uuid.UUID, Any]:
    version = catalog.add(
        make_version(
            product_key="hrms",
            currency="INR",
            amount_minor=amount_minor,
            billing_interval=interval,
        )
    )
    async with session.begin():
        sub = await services.subscribe(
            session,
            _ctx(org_id),
            catalog,
            SubscribeIn(
                product_key="hrms", plan_version_id=version.id, trial_days=14 if trial else None
            ),
        )
        return sub.id, version


async def _version(catalog: FakeCatalog, amount_minor: int, interval: str = "month") -> Any:
    return catalog.add(
        make_version(
            product_key="hrms", currency="INR", amount_minor=amount_minor, billing_interval=interval
        )
    )


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


async def _period_ended(subscription_id: uuid.UUID) -> tuple[datetime, datetime]:
    """Move an active subscription's current period so it ended a day ago."""
    end = _wall() - DAY
    start = end - timedelta(days=30)
    await _admin(
        "UPDATE subscription.subscriptions SET current_period_start = :s, "
        "current_period_end = :e WHERE id = :i",
        s=start,
        e=end,
        i=subscription_id,
    )
    # The first period's opening charge moves with it, so the history stays one a real
    # subscription could have: [start, end) charged on opening, the renewal charging the next.
    await _admin(
        "UPDATE subscription.billing_periods SET period_start = :s, full_period_start = :s, "
        "period_end = :e WHERE subscription_id = :i AND kind = 'opening'",
        s=start,
        e=end,
        i=subscription_id,
    )
    return start, end


async def _open_renewal_claim(
    run: BillingRunSweep, billing: FakeBilling, subscription_id: uuid.UUID
) -> None:
    """Leave a renewal claimed and not recorded — the process died before calling billing."""
    billing.crash_before = True
    with pytest.raises(Crash):
        await run.tick(now=_wall())


def _of(periods: list[BillingPeriod], kind: str) -> list[BillingPeriod]:
    return [p for p in periods if p.kind == kind]


# --------------------------------------------------------------------- B1: cadence and direction


class TestCadence:
    @pytest.mark.parametrize(("have", "want"), [("month", "year"), ("year", "month")])
    async def test_a_change_to_another_cadence_is_refused(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        have: str,
        want: str,
    ) -> None:
        """The reviewer's examples: ₹1,000/month to ₹11,000/year, and back."""
        amount = 100000 if have == "month" else 1100000
        subscription_id, _ = await _subscribe(session, catalog, org_id, amount, interval=have)
        target = await _version(catalog, 1100000 if want == "year" else 100000, want)
        with pytest.raises(domain.CadenceChangeRefused, match="cancel and subscribe"):
            await _change(session, catalog, org_id, subscription_id, target.id)

    async def test_a_change_to_one_time_is_refused(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        target = await _version(catalog, 50000, "one_time")
        with pytest.raises(domain.CadenceChangeRefused):
            await _change(session, catalog, org_id, subscription_id, target.id)

    def test_direction_is_compared_per_day_in_integers(self) -> None:
        # ₹1,000 a month is ₹33.33/day; ₹11,000 a year is ₹30.14/day: the yearly plan is cheaper.
        assert domain.plan_change_direction(1100000, "year", 100000, "month") == -1
        assert domain.plan_change_direction(100000, "month", 1100000, "year") == 1
        assert domain.plan_change_direction(365000, "year", 30000, "month") == 0
        assert domain.plan_change_direction(100000, "month", 100000, "month") == 0

    async def test_an_equal_price_switch_raises_no_proration(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, old = await _subscribe(session, catalog, org_id, 100000)
        same = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, same.id)
        periods = await _periods(session, subscription_id)
        assert _of(periods, domain.KIND_PRORATION) == []
        # ...but usage so far is still closed at the old pin (M3).
        (close,) = _of(periods, domain.KIND_CLOSE)
        assert close.plan_version_id == old.id


# --------------------------------------------------------------------- B2: changes against a claim


class TestChangeAgainstAClaim:
    async def test_an_upgrade_while_a_renewal_is_claimed_is_prorated_against_that_claim(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        (claim,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)

        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)

        (proration,) = _of(await _periods(session, subscription_id), domain.KIND_PRORATION)
        assert proration.period_end == claim.period_end
        assert proration.full_period_start == claim.period_start
        assert proration.from_plan_version_id == low.id
        assert proration.plan_version_id == high.id

        # The record step's own check names the same change: still exactly one proration.
        await expire_leases(subscription_id)
        await run.tick(now=_wall())
        assert len(_of(await _periods(session, subscription_id), domain.KIND_PRORATION)) == 1

    async def test_an_upgrade_while_a_trial_conversion_is_claimed_is_charged(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000, trial=True)
        ended = _wall() - DAY
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=ended,
            i=subscription_id,
        )
        await _open_renewal_claim(run, billing, subscription_id)
        (claim,) = _of(await _periods(session, subscription_id), domain.KIND_TRIAL_CONVERSION)
        assert (await _subscription(session, subscription_id)).state == STATE_TRIAL

        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        (proration,) = _of(await _periods(session, subscription_id), domain.KIND_PRORATION)
        assert proration.period_end == claim.period_end
        assert proration.from_plan_version_id == low.id

    async def test_a_downgrade_after_a_claim_waits_for_the_next_unclaimed_period(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, high = await _subscribe(session, catalog, org_id, 200000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)

        await expire_leases(subscription_id)
        await run.tick(now=_wall())
        renewal = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL][-1]
        assert renewal.plan_version_id == high.id
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == high.id, "the claimed period keeps the pin it was billed at"
        assert sub.pending_plan_version_id == low.id
        assert sub.current_period_end is not None

        await run.tick(now=sub.current_period_end)
        nxt = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL][-1]
        assert nxt.plan_version_id == low.id
        assert (await _subscription(session, subscription_id)).plan_version_id == low.id


# --------------------------------------------------------------------- M3: usage at the old pin


class TestUsageAtTheOldPin:
    async def test_an_upgrade_closes_usage_so_far_at_the_old_pin(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000)
        start = (await _subscription(session, subscription_id)).current_period_start
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)

        (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        sub = await _subscription(session, subscription_id)
        assert close.plan_version_id == low.id
        assert (close.usage_start, close.usage_end) == (start, sub.usage_billed_through)

        await run.tick(now=_wall())
        (billed,) = [r for r in billing.requests if r.billing_kind == "close"]
        assert billed.plan_version_id == low.id

    async def test_a_deferred_downgrade_closes_the_ending_period_at_the_old_pin(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, high = await _subscribe(session, catalog, org_id, 200000)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)
        start, end = await _period_ended(subscription_id)

        await run.tick(now=_wall())
        (renewal,) = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL]
        assert renewal.plan_version_id == low.id
        assert renewal.usage_start is None, "the renewal must not price old usage at the new pin"
        (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        assert close.plan_version_id == high.id
        assert (close.usage_start, close.usage_end) == (start, end)


# --------------------------------------------------------------------- M4: stops against a claim


class TestStopsAgainstAClaim:
    async def test_a_suspension_after_a_claimed_period_opened_charges_it_once_with_usage(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        start, end = await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.suspend(session, sub, operator_id="ops")
            suspended_at = sub.usage_billed_through

        await expire_leases(subscription_id)
        report = await run.tick(now=_wall())
        assert report.failed == 0, "the suspended roll must not collide with the open claim"
        periods = await _periods(session, subscription_id)
        (renewal,) = _of(periods, domain.KIND_RENEWAL)
        # The claimed period opened a day before the suspension, while the subscription was still
        # served: it is charged like any period entered while active (round 3 — the run being late
        # to roll it must not make it free), and exactly once.
        assert renewal.state == PERIOD_FINALIZED
        assert billing.finalized.count(renewal.invoice_id) == 1
        windows = sorted(
            (p.usage_start, p.usage_end)
            for p in [renewal, *_of(periods, domain.KIND_CLOSE)]
            if p.usage_start is not None
        )
        # The renewal bills the ended period's usage; the stop's close bills the rest, no overlap.
        assert windows == [(start, end), (end, suspended_at)]
        assert (await _subscription(session, subscription_id)).current_period_start == end

    async def test_a_cancellation_with_a_renewal_claimed_closes_from_the_claim(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        start, end = await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.cancel(session, _ctx(org_id), sub)
            cancelled_at = sub.cancel_at

        (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        assert (close.usage_start, close.usage_end) == (end, cancelled_at)
        await expire_leases(subscription_id)
        await run.tick(now=_wall())
        windows = sorted(
            (p.usage_start, p.usage_end)
            for p in _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        )
        assert windows == [(start, end), (end, cancelled_at)]

    async def test_cancel_waits_for_a_record_in_flight_and_closes_from_its_period(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A record step holds the row and rolls the period; a cancel racing it must decide from
        the rolled period, not from what it read before the lock."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        new_start = _wall() - timedelta(hours=1)
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": "subscription,public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        holding = asyncio.Event()

        async def record_in_flight() -> None:
            async with maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org_id)}
                )
                await selectors.lock_subscription(s, org_id, subscription_id, skip_locked=False)
                holding.set()
                await asyncio.sleep(0.3)
                await s.execute(
                    text(
                        "UPDATE subscription.subscriptions SET current_period_start = :p "
                        "WHERE id = :i"
                    ),
                    {"p": new_start, "i": subscription_id},
                )

        async def cancel() -> None:
            await holding.wait()
            async with maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org_id)}
                )
                sub = await services.require_subscription(s, subscription_id)  # read unlocked
                await services.cancel(s, _ctx(org_id), sub)

        try:
            await asyncio.gather(record_in_flight(), cancel())
        finally:
            await engine.dispose()
        (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        assert close.usage_start == new_start

    async def test_any_failure_in_the_record_step_backs_the_claim_off(
        self,
        run: BillingRunSweep,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)

        async def broken(*_: Any, **__: Any) -> str:
            raise RuntimeError("record exploded")

        monkeypatch.setattr(services, "record_billing_period", broken)
        report = await run.tick(now=_wall())
        assert report.failed == 1
        (renewal,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)
        assert renewal.state == PERIOD_PENDING
        assert renewal.next_attempt_at is not None and renewal.last_error == "record exploded"


# --------------------------------------------------------------------- M5: finalize after record


class TestFinalizeAfterRecord:
    async def test_a_superseded_period_is_a_voided_draft_never_announced(
        self,
        sweep_engines: tuple[Any, Any],
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        from tests.billing_fakes import FakePayment, _make_sweep
        from tests.test_billing_run import _seed

        subscription_id = await _seed(session, org_id)

        class CancelDuringCall(FakeBilling):
            async def bill_period(self, request: Any) -> Any:
                answer = await super().bill_period(request)
                await _admin(
                    "UPDATE subscription.subscriptions SET state='cancelled' WHERE id=:i",
                    i=subscription_id,
                )
                return answer

        billing = CancelDuringCall(settings().database_url)
        try:
            run = _make_sweep(*sweep_engines, billing, FakePayment())
            report = await run.tick(now=datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
        finally:
            await billing.aclose()
        assert report.voided == 1
        ((_, invoice),) = billing.invoices.items()
        assert billing.voided == {invoice.invoice_id}
        assert billing.finalized == [], "a superseded period was announced — it could be charged"

    async def test_a_crash_between_record_and_finalize_finalizes_exactly_once(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        sweep_engines: tuple[Any, Any],
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        billing.crash_before_finalize = True
        with pytest.raises(Crash):
            await run.tick(now=_wall())
        (renewal,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)
        assert renewal.state == PERIOD_FINALIZE_PENDING
        assert billing.finalized == []
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE

        discovery_engine, _ = sweep_engines
        async with async_sessionmaker(discovery_engine)() as s, s.begin():
            health = await selectors.billing_run_health(
                s, now=_wall(), overdue_after=timedelta(hours=1)
            )
        assert health["finalize_pending"] == 1

        await expire_leases(subscription_id)
        await run.tick(now=_wall())
        await run.tick(now=_wall())
        (renewal,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)
        assert renewal.state == PERIOD_FINALIZED
        assert billing.finalized.count(renewal.invoice_id) == 1


# --------------------------------------------------------------------- m9


async def test_a_failed_closing_charge_after_cancellation_is_counted(
    run: BillingRunSweep,
    billing: FakeBilling,
    sweep_engines: tuple[Any, Any],
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
) -> None:
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.cancel(session, _ctx(org_id), sub)
    await run.tick(now=_wall())
    (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
    assert close.state == PERIOD_FINALIZED and close.invoice_id is not None

    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.record_payment_failure(
            session, sub, PaymentOutcome(invoice_id=close.invoice_id, failure_reason="declined")
        )
    discovery_engine, _ = sweep_engines
    async with async_sessionmaker(discovery_engine)() as s, s.begin():
        health = await selectors.billing_run_health(
            s, now=_wall(), overdue_after=timedelta(hours=1)
        )
    assert health["close_charges_failed"] == 1


_ = STATE_ACTIVE
