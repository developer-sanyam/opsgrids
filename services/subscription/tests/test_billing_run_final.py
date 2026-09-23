"""The billing run's final review (ADR-0005 addendum, "Final review").

Same machinery as the other billing-run suites; every test here fails against the code before its
fix:

* **Blocker** a payment is not a key to every suspension: an operator's suspension is never lifted
  by a capture, and a dunning suspension only by a capture of the invoice its window was about —
  never by the run's closing-usage invoice, which is captured routinely after a suspension.
* **Major 3** the billing cutover (`BILLING_RUN_NOT_BEFORE`): recurring money for a period that
  began before it is skipped `pre_cutover`, usage from before it is never billed, a trial that
  ended before it converts with no charge, the loop refuses to start without it, and the dry run
  says what would be invoiced and what skipped.
* **A stop while the run lags** (found by the property test once dunning suspensions joined it):
  a downgrade whose boundary has passed is applied at that boundary before the stop closes usage,
  so usage after it is not priced at the old, dearer pin.

The cancel that is never refused (major 2) is pinned in `test_billing_run_round3.py`, where the
refusal it replaced was.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app import domain, services
from app.billing_run import BillingRunSweep, billing_run_loop, summarise_preview
from app.config import Settings, settings
from app.models import (
    PERIOD_SKIPPED,
    STATE_ACTIVE,
    STATE_SUSPENDED,
)
from app.schemas import PaymentOutcome, SubscribeIn
from tests.billing_fakes import FakeBilling, FakePayment, _make_sweep
from tests.helpers import FakeCatalog, make_version
from tests.test_billing_run import _events, _periods, _subscription, _transitions
from tests.test_billing_run_review import _cycle, _dunning_suspend
from tests.test_billing_run_round2 import (
    _admin,
    _change,
    _of,
    _period_ended,
    _subscribe,
    _version,
)
from tests.test_billing_run_round3 import _cancel, _charged

pytestmark = pytest.mark.integration

DAY = timedelta(days=1)


def _wall() -> datetime:
    return datetime.now(UTC)


async def _capture(session: Any, subscription_id: uuid.UUID, invoice_id: uuid.UUID | None) -> str:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.record_payment_captured(session, sub, PaymentOutcome(invoice_id=invoice_id))
    return (await _subscription(session, subscription_id)).state


async def _backdate(subscription_id: uuid.UUID, start: datetime) -> None:
    await _admin(
        "UPDATE subscription.subscriptions SET current_period_start = :s WHERE id = :i",
        s=start,
        i=subscription_id,
    )


# --------------------------------------------------------------------- blocker: resumption


class TestAPaymentIsNotAKeyToEverySuspension:
    async def test_no_capture_lifts_an_operator_suspension(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A fraud or abuse hold is lifted by a person. Before the fix, the run's own closing-usage
        invoice — raised by the suspension, captured by payment moments later — lifted it."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await run.tick(now=_wall())
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.suspend(session, sub, operator_id="ops")
        await run.tick(now=_wall())
        periods = await _periods(session, subscription_id)
        (close,) = _charged(periods, domain.KIND_CLOSE, billing)
        (opening,) = _charged(periods, domain.KIND_OPENING, billing)

        assert await _capture(session, subscription_id, close.invoice_id) == STATE_SUSPENDED
        assert await _capture(session, subscription_id, opening.invoice_id) == STATE_SUSPENDED
        assert await _capture(session, subscription_id, None) == STATE_SUSPENDED
        assert domain.REASON_DUNNING_RECOVERED not in await _transitions(session, subscription_id)

    async def test_the_close_invoice_does_not_lift_a_dunning_suspension_the_owed_one_does(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A customer suspended for an unpaid ₹1,000 invoice must not resume by paying the small
        closing-usage invoice the suspension raised. Paying the owed invoice resumes."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await run.tick(now=_wall())
        await _backdate(subscription_id, _wall() - timedelta(days=10))
        owed = uuid.uuid4()
        await _dunning_suspend(
            session,
            subscription_id,
            invoice_id=owed,
            failed_at=_wall() - timedelta(days=5),
            grace_ends_at=_wall() - timedelta(hours=1),
        )
        await run.tick(now=_wall())
        (close,) = _charged(await _periods(session, subscription_id), domain.KIND_CLOSE, billing)

        assert await _capture(session, subscription_id, close.invoice_id) == STATE_SUSPENDED
        assert await _capture(session, subscription_id, uuid.uuid4()) == STATE_SUSPENDED
        assert await _capture(session, subscription_id, owed) == STATE_ACTIVE
        assert domain.REASON_DUNNING_RECOVERED in await _transitions(session, subscription_id)
        cycle = await _cycle(session, subscription_id)
        assert cycle is not None and cycle.closed_reason == "grace_expired"

    async def test_an_operator_suspension_after_a_dunning_one_is_not_lifted_by_the_old_invoice(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """Dunning suspended, the owed invoice paid, then an operator suspended: the old dunning
        invoice, paid again (a duplicate capture), must not lift the operator's hold."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _backdate(subscription_id, _wall() - timedelta(days=10))
        owed = uuid.uuid4()
        await _dunning_suspend(
            session,
            subscription_id,
            invoice_id=owed,
            failed_at=_wall() - timedelta(days=5),
            grace_ends_at=_wall() - timedelta(hours=1),
        )
        assert await _capture(session, subscription_id, owed) == STATE_ACTIVE
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            await services.suspend(session, sub, operator_id="ops")
        assert await _capture(session, subscription_id, owed) == STATE_SUSPENDED


# --------------------------------------------------------------------- a stop while the run lags


class TestAStopWhileTheRunLags:
    @pytest.mark.parametrize("stop", ["cancel", "suspend"])
    async def test_a_lapsed_downgrade_applies_at_its_boundary_before_the_stop_closes_usage(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        stop: str,
    ) -> None:
        subscription_id, high = await _subscribe(session, catalog, org_id, 200000)
        low = await _version(catalog, 100000)
        await _change(session, catalog, org_id, subscription_id, low.id)
        start, end = await _period_ended(subscription_id)  # the run never rolled it
        session.expunge_all()
        async with session.begin():
            sub = await services.require_subscription(session, subscription_id)
            if stop == "cancel":
                await services.cancel(session, services_ctx(org_id), sub)
                stopped = sub.cancel_at
            else:
                await services.suspend(session, sub, operator_id="ops")
                stopped = sub.suspended_at

        closes = sorted(
            (p.usage_start, p.usage_end, p.plan_version_id)
            for p in _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        )
        # Up to the boundary at the old pin; after it, at the one in force from it.
        assert closes == [(start, end, high.id), (end, stopped, low.id)]
        sub = await _subscription(session, subscription_id)
        assert sub.plan_version_id == low.id and sub.pending_change is None
        assert domain.REASON_DOWNGRADE in await _transitions(session, subscription_id)


def services_ctx(org_id: uuid.UUID) -> Any:
    from tests.test_billing_run_round2 import _ctx

    return _ctx(org_id)


# --------------------------------------------------------------------- major 3: the cutover


def _sweep(
    engines: tuple[Any, Any], billing: FakeBilling, payment: FakePayment, not_before: datetime
) -> BillingRunSweep:
    return _make_sweep(*engines, billing, payment, not_before=not_before)


class TestCutover:
    async def test_a_subscription_behind_the_cutover_starts_paying_at_its_first_period_after_it(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        _, end = await _period_ended(subscription_id)
        cutover = _wall() - timedelta(hours=12)
        sweep = _sweep(sweep_engines, billing, payment, cutover)

        report = await sweep.tick(now=_wall())
        assert billing.requests == [], "nothing that began before the cutover is invoiced"
        assert report.skipped_pre_cutover == 2  # the ended period's roll, and its opening
        periods = await _periods(session, subscription_id)
        for p in _of(periods, domain.KIND_RENEWAL) + _of(periods, domain.KIND_OPENING):
            assert (p.state, p.outcome) == (PERIOD_SKIPPED, domain.OUTCOME_PRE_CUTOVER)
        sub = await _subscription(session, subscription_id)
        assert sub.current_period_start == end and sub.state == STATE_ACTIVE
        assert sub.current_period_end is not None

        # The first period that begins after the cutover is invoiced — with usage from the
        # cutover on, never before it.
        next_start = sub.current_period_end
        await sweep.tick(now=next_start)
        (renewal,) = [r for r in billing.requests if r.kind == domain.KIND_RENEWAL]
        assert renewal.period_start == next_start
        assert (renewal.usage_start, renewal.usage_end) == (cutover, next_start)

    async def test_a_trial_that_ended_before_the_cutover_converts_without_a_charge(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        trial_end = _wall() - 2 * DAY
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=trial_end,
            i=subscription_id,
        )
        sweep = _sweep(sweep_engines, billing, payment, _wall() - DAY)

        report = await sweep.tick(now=_wall())
        assert report.skipped_pre_cutover == 1
        assert billing.requests == [] and payment.asked == []
        sub = await _subscription(session, subscription_id)
        assert sub.state == STATE_ACTIVE
        assert sub.current_period_start == trial_end, "the paid period opens at the trial's end"
        assert await _cycle(session, subscription_id) is None, "no charge, so nothing to dun"
        (conversion,) = _of(await _periods(session, subscription_id), domain.KIND_TRIAL_CONVERSION)
        assert (conversion.state, conversion.outcome) == (
            PERIOD_SKIPPED,
            domain.OUTCOME_PRE_CUTOVER,
        )
        (activated,) = await _events(session, domain.EVT_ACTIVATED)
        assert activated.payload["billing"]["invoiced"] is False

    async def test_a_trial_that_ends_after_the_cutover_is_invoiced_as_before(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=_wall() - timedelta(hours=1),
            i=subscription_id,
        )
        sweep = _sweep(sweep_engines, billing, payment, _wall() - DAY)
        report = await sweep.tick(now=_wall())
        assert report.skipped_pre_cutover == 0
        (conversion,) = billing.requests
        assert conversion.kind == domain.KIND_TRIAL_CONVERSION

    async def test_usage_straddling_the_cutover_is_billed_from_it(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        began = _wall() - timedelta(days=10)
        await _backdate(subscription_id, began)
        cutover = _wall() - timedelta(days=5)
        await _cancel(session, org_id, subscription_id)
        cancelled_at = (await _subscription(session, subscription_id)).cancel_at

        await _sweep(sweep_engines, billing, payment, cutover).tick(now=_wall())
        (close,) = [r for r in billing.requests if r.kind == domain.KIND_CLOSE]
        assert (close.usage_start, close.usage_end) == (cutover, cancelled_at)
        assert close.period_start == began, "the billing key does not move"

    async def test_usage_that_ended_before_the_cutover_is_never_billed(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _backdate(subscription_id, _wall() - timedelta(days=10))
        await _dunning_suspend(
            session,
            subscription_id,
            invoice_id=uuid.uuid4(),
            failed_at=_wall() - timedelta(days=9),
            grace_ends_at=_wall() - timedelta(days=6),
        )
        report = await _sweep(sweep_engines, billing, payment, _wall() - timedelta(days=5)).tick(
            now=_wall()
        )
        assert not [r for r in billing.requests if r.kind == domain.KIND_CLOSE]
        (close,) = _of(await _periods(session, subscription_id), domain.KIND_CLOSE)
        assert (close.state, close.outcome) == (PERIOD_SKIPPED, domain.OUTCOME_PRE_CUTOVER)
        assert report.skipped_pre_cutover >= 1

    async def test_a_proration_and_an_opening_from_before_the_cutover_are_skipped(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        high = await _version(catalog, 200000)
        await _change(session, catalog, org_id, subscription_id, high.id)
        report = await _sweep(sweep_engines, billing, payment, _wall()).tick(now=_wall())
        assert billing.requests == []
        periods = await _periods(session, subscription_id)
        for kind in (domain.KIND_PRORATION, domain.KIND_OPENING, domain.KIND_CLOSE):
            (row,) = _of(periods, kind)
            assert (row.state, row.outcome) == (PERIOD_SKIPPED, domain.OUTCOME_PRE_CUTOVER)
        assert report.skipped_pre_cutover == 3

    async def test_the_dry_run_splits_pre_cutover_from_invoiced_and_writes_nothing(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        behind, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(behind)  # its roll, and its opening, began before the cutover
        crm = catalog.add(make_version(product_key="crm", currency="INR", amount_minor=100000))
        async with session.begin():
            recent = (
                await services.subscribe(
                    session,
                    services_ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="crm", plan_version_id=crm.id),
                )
            ).id
        await _admin(
            "UPDATE subscription.subscriptions SET current_period_start = :s, "
            "current_period_end = :e WHERE id = :i",
            s=_wall() - timedelta(days=30, hours=1),
            e=_wall() - timedelta(hours=1),
            i=recent,
        )
        cutover = _wall() - timedelta(hours=12)
        sweep = _sweep(sweep_engines, billing, payment, cutover)
        before = len(await _periods(session, behind)) + len(await _periods(session, recent))

        rows = {r["subscription_id"]: r for r in await sweep.preview()}
        assert (
            rows[str(behind)]["periods_pre_cutover"],
            rows[str(behind)]["periods_invoiced"],
        ) == (
            1,
            0,
        )
        assert (
            rows[str(recent)]["periods_pre_cutover"],
            rows[str(recent)]["periods_invoiced"],
        ) == (
            0,
            1,
        )
        backlog = await sweep.preview_backlog()
        assert backlog["backlog"][domain.KIND_OPENING] == 2
        assert (backlog["backlog_pre_cutover"], backlog["backlog_invoiced"]) == (1, 1)
        summary = summarise_preview(list(rows.values()), backlog, cutover)
        assert summary["not_before"] == cutover.isoformat()
        assert (summary["periods_pre_cutover"], summary["periods_invoiced"]) == (1, 1)

        assert billing.requests == []
        after = len(await _periods(session, behind)) + len(await _periods(session, recent))
        assert after == before, "the dry run wrote a row"


def test_the_loop_refuses_to_start_without_a_cutover() -> None:
    sweep = BillingRunSweep(
        discovery_factory=None,  # type: ignore[arg-type]
        work_scope=None,  # type: ignore[arg-type]
        billing=None,  # type: ignore[arg-type]
        payment=None,  # type: ignore[arg-type]
    )
    base = settings()
    off = base.model_copy(update={"billing_run_enabled": False})
    assert billing_run_loop(off, sweep) is None
    no_cutover = base.model_copy(
        update={"billing_run_enabled": True, "billing_run_not_before": None}
    )
    assert billing_run_loop(no_cutover, sweep) is None
    ready = base.model_copy(
        update={
            "billing_run_enabled": True,
            "billing_run_not_before": datetime(2026, 10, 1, 3, 30, tzinfo=UTC),
        }
    )
    loop = billing_run_loop(ready, sweep)
    assert loop is not None
    loop.close()


def test_the_cutover_is_an_instant_with_an_offset() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        Settings(billing_run_not_before="2026-10-01T09:00:00")  # type: ignore[arg-type]
    assert Settings(billing_run_not_before="").billing_run_not_before is None  # type: ignore[arg-type]
    aware = Settings(billing_run_not_before="2026-10-01T09:00:00+05:30")  # type: ignore[arg-type]
    assert aware.billing_run_not_before == datetime(2026, 10, 1, 3, 30, tzinfo=UTC)
    assert Settings.model_fields["billing_run_not_before"].default is None


def test_what_the_cutover_does_to_each_kind() -> None:
    cut = datetime(2026, 10, 1, tzinfo=UTC)
    before, after = cut - DAY, cut + DAY

    def act(kind: str, start: datetime, u0: datetime | None, u1: datetime | None) -> Any:
        return domain.cutover_action(
            kind=kind, period_start=start, usage_start=u0, usage_end=u1, not_before=cut
        )

    for kind in (
        domain.KIND_RENEWAL,
        domain.KIND_TRIAL_CONVERSION,
        domain.KIND_PRORATION,
        domain.KIND_OPENING,
    ):
        assert act(kind, before, None, None) == domain.CUTOVER_SKIP
        assert act(kind, cut, None, None) is None
    assert act(domain.KIND_CLOSE, before - DAY, before - DAY, before) == domain.CUTOVER_SKIP
    assert act(domain.KIND_CLOSE, before, before, cut) == domain.CUTOVER_SKIP
    assert act(domain.KIND_CLOSE, before, before, after) == domain.CUTOVER_CLAMP
    assert act(domain.KIND_CLOSE, after, after, after + DAY) is None
    assert (
        domain.cutover_action(
            kind=domain.KIND_RENEWAL,
            period_start=before,
            usage_start=None,
            usage_end=None,
            not_before=None,
        )
        is None
    )
    assert domain.usage_floor(None, cut) == cut
    assert domain.usage_floor(before, cut) == cut
    assert domain.usage_floor(after, cut) == after
    assert domain.usage_floor(before, None) == before
