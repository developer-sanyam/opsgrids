"""The billing run's third review round (ADR-0005 addendum, "Review fixes, round 3").

Same machinery as the other billing-run suites; every test here fails against the code before its
fix. The randomised invariants live in `test_billing_run_properties.py`; these are the named
scenarios the review found, pinned one by one:

* **B1** a proration raised against a pending claim is charged only once that claim is, and
  follows it when it is voided or skipped — cancel, dunning suspension, and a trial cancelled with
  its conversion claimed.
* **B2** a second change against the same claim prices from the last pin already prorated: an
  equal-price switch after an upgrade charges exactly one proration, by either door.
* **M3** a plan change while a period is due and unclaimed is a 409; a cancel never is (final
  review) and bills only its closing usage.
* **m7** a failed proration charge on a cancelled subscription is counted.
* **Openings** a subscription that starts paid is charged its first period; a resumption after a
  suspension that crossed a roll is charged the rest of the period.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import domain, selectors, services
from app.billing_run import BillingRunSweep
from app.main import app
from app.models import (
    PERIOD_FINALIZED,
    PERIOD_SKIPPED,
    PERIOD_VOIDED,
    STATE_ACTIVE,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    BillingPeriod,
)
from app.schemas import ChangePlanIn, OperatorChangePlanIn, PaymentOutcome
from tests.billing_fakes import Crash, FakeBilling, expire_leases
from tests.helpers import FakeCatalog, FakeRegistry, customer_principal
from tests.test_billing_run import _periods, _subscription
from tests.test_billing_run_review import _cycle, _dunning_suspend
from tests.test_billing_run_round2 import (
    _admin,
    _change,
    _ctx,
    _of,
    _open_renewal_claim,
    _period_ended,
    _subscribe,
    _version,
)

pytestmark = pytest.mark.integration

DAY = timedelta(days=1)


def _wall() -> datetime:
    return datetime.now(UTC)


def _charged(periods: list[BillingPeriod], kind: str, billing: FakeBilling) -> list[BillingPeriod]:
    return [
        p
        for p in _of(periods, kind)
        if p.state == PERIOD_FINALIZED and p.invoice_id in billing.finalized
    ]


async def _cancel(session: AsyncSession, org_id: uuid.UUID, subscription_id: uuid.UUID) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.cancel(session, _ctx(org_id), sub)


async def _settle(run: BillingRunSweep, subscription_id: uuid.UUID) -> None:
    await expire_leases(subscription_id)
    await run.tick(now=_wall())
    await run.tick(now=_wall())


# ------------------------------------------------------------ B1: prorations follow parents


class TestProrationFollowsItsClaim:
    async def test_a_cancel_voids_the_claim_and_its_upgrade_proration(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The reviewer's probe, inverted: the upgrade difference was charged for a period whose
        base charge had been voided."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        (proration,) = _of(await _periods(session, subscription_id), domain.KIND_PRORATION)
        (renewal,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)
        assert proration.against_period_id == renewal.id

        await _cancel(session, org_id, subscription_id)
        await _settle(run, subscription_id)

        periods = await _periods(session, subscription_id)
        (renewal,) = _of(periods, domain.KIND_RENEWAL)
        (proration,) = _of(periods, domain.KIND_PRORATION)
        assert renewal.state in (PERIOD_SKIPPED, PERIOD_VOIDED)
        assert proration.state == PERIOD_SKIPPED and proration.outcome == "parent_voided"
        assert not _charged(periods, domain.KIND_PRORATION, billing)
        assert not [r for r in billing.requests if r.kind == domain.KIND_PRORATION]

    async def test_a_dunning_suspension_voids_the_claim_and_its_upgrade_proration(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Grace ran out before the claimed period opened; the sweep suspended late. The period
        is voided — nobody was served into it — and so is the upgrade priced against it."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        _, end = await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)

        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.record_payment_failure(
                session,
                sub,
                PaymentOutcome(
                    invoice_id=uuid.uuid4(),
                    amount_minor=100000,
                    currency="INR",
                    failure_reason="insufficient_funds",
                ),
                # The failure predates the claimed period, so its grace can end before it opens.
                now=end - timedelta(days=5),
            )
        assert (await _subscription(session, subscription_id)).state == STATE_PAST_DUE
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None
        await _admin(
            "UPDATE subscription.dunning_cycles SET grace_ends_at = :g WHERE id = :i",
            g=end - timedelta(hours=1),
            i=cycle.id,
        )
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            cycle = await _cycle_in(session, subscription_id)
            await services.suspend_for_dunning(session, sub, cycle, now=_wall())
        assert (await _subscription(session, subscription_id)).state == STATE_SUSPENDED

        await _settle(run, subscription_id)
        periods = await _periods(session, subscription_id)
        (renewal,) = _of(periods, domain.KIND_RENEWAL)
        (proration,) = _of(periods, domain.KIND_PRORATION)
        assert renewal.state == PERIOD_VOIDED
        assert proration.state == PERIOD_SKIPPED
        assert not _charged(periods, domain.KIND_PRORATION, billing)

    async def test_a_suspension_after_the_period_opened_charges_claim_and_proration_once(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The other side of the same rule: the customer was served into the claimed period
        before an operator suspended them, so the period and its upgrade are each charged once."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.suspend(session, sub, operator_id="ops")

        await _settle(run, subscription_id)
        periods = await _periods(session, subscription_id)
        assert len(_charged(periods, domain.KIND_RENEWAL, billing)) == 1
        assert len(_charged(periods, domain.KIND_PRORATION, billing)) == 1

    async def test_a_trial_cancelled_with_its_conversion_claimed_charges_no_upgrade(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=_wall() - DAY,
            i=subscription_id,
        )
        await _open_renewal_claim(run, billing, subscription_id)
        (conversion,) = _of(await _periods(session, subscription_id), domain.KIND_TRIAL_CONVERSION)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        (proration,) = _of(await _periods(session, subscription_id), domain.KIND_PRORATION)
        assert proration.against_period_id == conversion.id

        await _cancel(session, org_id, subscription_id)
        await _settle(run, subscription_id)

        periods = await _periods(session, subscription_id)
        (conversion,) = _of(periods, domain.KIND_TRIAL_CONVERSION)
        (proration,) = _of(periods, domain.KIND_PRORATION)
        assert conversion.state in (PERIOD_SKIPPED, PERIOD_VOIDED)
        assert proration.state == PERIOD_SKIPPED
        assert not _charged(periods, domain.KIND_PRORATION, billing)
        assert not _charged(periods, domain.KIND_TRIAL_CONVERSION, billing)

    async def test_a_proration_waits_for_its_claim_to_be_charged(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        sweep_engines: tuple[Any, Any],
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        (proration,) = _of(await _periods(session, subscription_id), domain.KIND_PRORATION)

        discovery_engine, _ = sweep_engines
        async with async_sessionmaker(discovery_engine)() as s, s.begin():
            actionable = await selectors.actionable_periods(s, wall=_wall(), limit=100)
        assert proration.id not in {period_id for _, period_id in actionable}

        await _settle(run, subscription_id)
        periods = await _periods(session, subscription_id)
        assert len(_charged(periods, domain.KIND_RENEWAL, billing)) == 1
        assert len(_charged(periods, domain.KIND_PRORATION, billing)) == 1


async def _cycle_in(session: AsyncSession, subscription_id: uuid.UUID) -> Any:
    cycle = await selectors.get_open_cycle(session, subscription_id)
    assert cycle is not None
    return cycle


# --------------------------------------------------------------------- B2: last pin is the baseline


class TestRepeatChangesAgainstAClaim:
    @pytest.mark.parametrize("door", ["customer", "operator"])
    async def test_an_equal_price_switch_after_an_upgrade_charges_one_proration(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        door: str,
    ) -> None:
        """The reviewer's probe, inverted: A → B (dearer) → B' (same price as B) against one
        claim charged (B - A) twice over the claim's remainder."""
        subscription_id, low = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        same = await _version(catalog, 200000)

        async def change(version_id: uuid.UUID) -> None:
            if door == "customer":
                await _change(session, catalog, org_id, subscription_id, version_id)
                return
            session.expunge_all()
            async with session.begin():
                sub = await services.require_subscription(session, subscription_id)
                await services.change_plan_as_operator(
                    session,
                    catalog,
                    FakeRegistry(),
                    sub,
                    OperatorChangePlanIn(
                        organization_id=org_id, plan_version_id=version_id, operator_id="ops"
                    ),
                )

        await change(high.id)
        await change(same.id)
        await _settle(run, subscription_id)

        periods = await _periods(session, subscription_id)
        (charged,) = _charged(periods, domain.KIND_PRORATION, billing)
        assert (charged.from_plan_version_id, charged.plan_version_id) == (low.id, high.id)
        (marker,) = [p for p in _of(periods, domain.KIND_PRORATION) if p is not charged]
        assert marker.state == PERIOD_SKIPPED and marker.outcome == "equal_price"
        assert len([r for r in billing.requests if r.kind == domain.KIND_PRORATION]) == 1

    async def test_the_record_steps_backstop_prices_from_the_last_prorated_pin(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The record step re-checks the pin against the claim's (round 2, B2) and prorates a
        move nobody claimed. After an upgrade A → B was prorated, a further unclaimed move to B'
        is priced from B — the last pin prorated against the claim — not from A, which would
        charge the A → B difference a second time."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        same = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        # A pin move that claimed nothing against the claim — what the backstop exists for.
        await _admin(
            "UPDATE subscription.subscriptions SET plan_version_id = :v WHERE id = :i",
            v=same.id,
            i=subscription_id,
        )
        await _settle(run, subscription_id)

        froms = {
            r.plan_version_id: r.from_plan_version_id
            for r in billing.requests
            if r.kind == domain.KIND_PRORATION
        }
        assert froms[same.id] == high.id

    async def test_a_second_upgrade_prorates_from_the_first(
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
        mid = await _version(catalog, 150000)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, mid.id)
        await _change(session, catalog, org_id, subscription_id, high.id)
        await _settle(run, subscription_id)

        charged = _charged(await _periods(session, subscription_id), domain.KIND_PRORATION, billing)
        pairs = sorted(
            ((p.from_plan_version_id, p.plan_version_id) for p in charged), key=lambda x: str(x)
        )
        assert sorted([(low.id, mid.id), (mid.id, high.id)], key=lambda x: str(x)) == pairs


# --------------------------------------------------------------------- M3: 409 while billing lags


class TestRefusedWhileBillingCatchesUp:
    async def test_a_customer_change_is_refused_while_a_period_is_due_and_unclaimed(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        high = await _version(catalog, 200000)
        with pytest.raises(services.BillingCatchingUp, match="billing is catching up"):
            await _change(session, catalog, org_id, subscription_id, high.id)

    async def test_an_operator_change_is_refused_too(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        high = await _version(catalog, 200000)
        session.expunge_all()
        with pytest.raises(services.BillingCatchingUp):
            async with session.begin():
                sub = await services.require_subscription(session, subscription_id)
                await services.change_plan_as_operator(
                    session,
                    catalog,
                    FakeRegistry(),
                    sub,
                    OperatorChangePlanIn(
                        organization_id=org_id, plan_version_id=high.id, operator_id="ops"
                    ),
                )

    async def test_a_customer_cancel_is_never_refused_and_bills_only_closing_usage(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Final review, major 2: with the run off by default, refusing a cancel while a period
        is due would refuse every cancel in production. Leaving always works; the period that
        was due and never opened is never billed — the run bills the closing usage, nothing
        recurring."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        start, _end = await _period_ended(subscription_id)
        await _cancel(session, org_id, subscription_id)
        sub = await _subscription(session, subscription_id)
        assert sub.cancel_at is not None

        await run.tick(now=_wall())
        await run.tick(now=_wall())
        kinds = sorted(r.kind for r in billing.requests)
        # The opening (moved with the period by `_period_ended`) and the close. No renewal.
        assert kinds == [domain.KIND_CLOSE, domain.KIND_OPENING]
        (close,) = [r for r in billing.requests if r.kind == domain.KIND_CLOSE]
        assert (close.usage_start, close.usage_end) == (start, sub.cancel_at)
        assert not _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)

    async def test_a_trial_end_that_is_due_refuses_a_change(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=_wall() - DAY,
            i=subscription_id,
        )
        high = await _version(catalog, 200000)
        with pytest.raises(services.BillingCatchingUp):
            await _change(session, catalog, org_id, subscription_id, high.id)

    async def test_the_change_route_answers_409_and_the_cancel_route_does_not(
        self,
        client: AsyncClient,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        from platform_core.auth import get_principal

        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        high = await _version(catalog, 200000)
        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id)
        change = await client.post(
            f"/api/v1/subscriptions/{subscription_id}/change-plan",
            json=ChangePlanIn(plan_version_id=high.id).model_dump(mode="json"),
        )
        assert change.status_code == 409, change.text
        assert change.json()["title"] == "Billing is catching up"
        assert "try again shortly" in change.json()["detail"]
        cancel = await client.post(f"/api/v1/subscriptions/{subscription_id}/cancel", json={})
        assert cancel.status_code == 200, cancel.text
        assert (await _subscription(session, subscription_id)).cancel_at is not None


# --------------------------------------------------------------------- m7


async def test_a_failed_proration_charge_after_cancellation_is_counted(
    run: BillingRunSweep,
    billing: FakeBilling,
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    sweep_engines: tuple[Any, Any],
) -> None:
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
    high = await _version(catalog, 200000)
    await _change(session, catalog, org_id, subscription_id, high.id)
    await run.tick(now=_wall())
    (proration,) = _charged(
        await _periods(session, subscription_id), domain.KIND_PRORATION, billing
    )
    await _cancel(session, org_id, subscription_id)

    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.record_payment_failure(
            session,
            sub,
            PaymentOutcome(
                invoice_id=proration.invoice_id,
                amount_minor=1,
                currency="INR",
                failure_reason="insufficient_funds",
            ),
        )
    (proration,) = [p for p in await _periods(session, subscription_id) if p.id == proration.id]
    assert proration.payment_failed_at is not None

    discovery_engine, _ = sweep_engines
    async with async_sessionmaker(discovery_engine)() as s, s.begin():
        health = await selectors.billing_run_health(
            s, now=_wall(), overdue_after=timedelta(hours=1)
        )
    assert health["proration_charges_failed"] == 1


# --------------------------------------------------------------------- m6: the health probe


class TestHealthCountsEveryClaimInFlight:
    async def _health(self, sweep_engines: tuple[Any, Any]) -> dict[str, Any]:
        discovery_engine, _ = sweep_engines
        async with async_sessionmaker(discovery_engine)() as s, s.begin():
            return await selectors.billing_run_health(
                s, now=_wall(), overdue_after=timedelta(hours=1)
            )

    async def test_a_failing_finalize_is_counted_as_failing(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        sweep_engines: tuple[Any, Any],
    ) -> None:
        """A draft whose finalize keeps failing is as stuck as a claim that cannot be priced —
        and, before round 3, invisible to `failing` and `failing_permanent`."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        billing.crash_before_finalize = True
        with pytest.raises(Crash):
            await run.tick(now=_wall())
        (stuck,) = [
            p for p in await _periods(session, subscription_id) if p.state == "finalize_pending"
        ]
        await _admin(
            "UPDATE subscription.billing_periods SET last_error = 'boom', permanent_failure = true "
            "WHERE id = :i",
            i=stuck.id,
        )
        health = await self._health(sweep_engines)
        assert health["failing"] == 1 and health["failing_permanent"] == 1

    async def test_a_finalized_proration_on_a_voided_parent_is_counted(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        sweep_engines: tuple[Any, Any],
    ) -> None:
        """The state the cascade exists to make impossible — alarmed on if it ever appears."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        await _open_renewal_claim(run, billing, subscription_id)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        await _settle(run, subscription_id)
        assert (await self._health(sweep_engines))["prorations_on_voided_parent"] == 0
        (renewal,) = _of(await _periods(session, subscription_id), domain.KIND_RENEWAL)
        await _admin(
            "UPDATE subscription.billing_periods SET state = 'voided' WHERE id = :i", i=renewal.id
        )
        assert (await self._health(sweep_engines))["prorations_on_voided_parent"] == 1


# --------------------------------------------------------------------- openings


class TestOpeningCharges:
    async def test_a_subscription_that_starts_paid_is_charged_its_first_period_once(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Pre-fix, a subscription created active was never charged until its first renewal: a
        month of recurring revenue per new customer, silently."""
        subscription_id, version = await _subscribe(session, catalog, org_id, 100000)
        sub = await _subscription(session, subscription_id)
        assert sub.state == STATE_ACTIVE
        report = await run.tick(now=_wall())
        again = await run.tick(now=_wall())
        assert report.opened == 1 and again.opened == 0
        (opening,) = _charged(
            await _periods(session, subscription_id), domain.KIND_OPENING, billing
        )
        assert opening.plan_version_id == version.id
        assert (opening.period_start, opening.period_end) == (
            sub.current_period_start,
            sub.current_period_end,
        )
        (request,) = billing.requests
        assert request.billing_kind == "proration" and request.from_items == []

    async def test_a_trial_is_not_charged_an_opening(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        await run.tick(now=_wall())
        assert not _of(await _periods(session, subscription_id), domain.KIND_OPENING)
        assert billing.requests == []

    async def test_resuming_after_a_suspension_that_crossed_a_roll_charges_the_rest(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Suspended by dunning before the period ended, the roll skips the next period's advance
        charge (nobody was served into it). Paying the dunning invoice mid-way through that period
        restores service — and the rest of it is charged, once."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await run.tick(now=_wall())
        _, end = await _period_ended(subscription_id)
        owed = uuid.uuid4()
        await _dunning_suspend(
            session,
            subscription_id,
            invoice_id=owed,
            failed_at=end - timedelta(days=5),
            grace_ends_at=end - DAY,
        )
        await run.tick(now=_wall())
        sub = await _subscription(session, subscription_id)
        assert sub.current_period_start == end
        assert not _charged(await _periods(session, subscription_id), domain.KIND_RENEWAL, billing)

        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.record_payment_captured(session, sub, PaymentOutcome(invoice_id=owed))
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE
        await run.tick(now=_wall())
        await run.tick(now=_wall())
        sub = await _subscription(session, subscription_id)
        openings = _charged(await _periods(session, subscription_id), domain.KIND_OPENING, billing)
        remainder = [o for o in openings if o.period_end == sub.current_period_end]
        assert len(remainder) == 1
        assert remainder[0].full_period_start == end
        assert end < remainder[0].period_start < sub.current_period_end
