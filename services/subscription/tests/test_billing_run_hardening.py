"""The billing run's second post-review fixup: the cheap hardening around the money path.

Every test here fails against the code before its fix:

* **minor 4** which suspension is in force is decided by the transition's actor, not its free-text
  reason, and an operator may not borrow a reason only the machinery writes;
* **minor 2** `BILLING_RUN_NOT_BEFORE` accepts exactly an ISO instant with an offset — a bare
  number (pydantic reads it as epoch seconds: 1970, back-billing everything), an impossible date
  or a naive time is refused, and refused *softly*: the run does not start, the worker does;
* **minor 6** a resumption inside the free straddling period charges no opening; and the
  reviewer's cutover edge probes, made permanent.

The races (major 1) are in `test_billing_run_races.py`; the randomised cutover invariants in
`test_billing_run_properties.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app import domain, services
from app.billing_run import BillingRunSweep, billing_run_loop
from app.config import Settings, settings
from app.models import STATE_ACTIVE, STATE_SUSPENDED
from app.schemas import PaymentOutcome
from platform_core.errors import BusinessRuleProblem
from tests.billing_fakes import Crash, FakeBilling, FakePayment, _make_sweep, expire_leases
from tests.helpers import FakeCatalog
from tests.test_billing_run import _periods, _subscription
from tests.test_billing_run_review import _dunning_suspend
from tests.test_billing_run_round2 import _admin, _of, _period_ended, _subscribe
from tests.test_billing_run_round3 import _cancel

pytestmark = pytest.mark.integration

DAY = timedelta(days=1)


def _wall() -> datetime:
    return datetime.now(UTC)


async def _capture(session: Any, subscription_id: uuid.UUID, invoice_id: uuid.UUID) -> str:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.record_payment_captured(session, sub, PaymentOutcome(invoice_id=invoice_id))
    return (await _subscription(session, subscription_id)).state


async def _operator_suspend(session: Any, subscription_id: uuid.UUID, reason: str | None) -> None:
    session.expunge_all()
    async with session.begin():
        sub = await services.require_subscription(session, subscription_id)
        await services.suspend(session, sub, operator_id="ops", reason=reason)


# --------------------------------------------------------------------- minor 4: actor, not reason


class TestWhoSuspended:
    @pytest.mark.parametrize("reason", ["dunning_exhausted", " Grace_Expired ", "payment_failed"])
    async def test_an_operator_may_not_borrow_a_machine_reason(
        self, session: Any, catalog: FakeCatalog, org_id: uuid.UUID, reason: str
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        with pytest.raises(BusinessRuleProblem, match="only the platform's own machinery"):
            await _operator_suspend(session, subscription_id, reason)
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE

    async def test_an_operator_hold_recorded_as_dunning_exhausted_is_not_lifted_by_an_old_invoice(
        self,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hold written before the reason was reserved — or by any path that labels it so —
        still names its operator as the actor. Re-paying the earlier dunning episode's invoice
        must not lift it. (The stream is append-only, so the historical row is made the way it
        was made then: with the reserved list empty.)"""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _admin(
            "UPDATE subscription.subscriptions SET current_period_start = :s WHERE id = :i",
            s=_wall() - timedelta(days=10),
            i=subscription_id,
        )
        owed = uuid.uuid4()
        await _dunning_suspend(
            session,
            subscription_id,
            invoice_id=owed,
            failed_at=_wall() - timedelta(days=5),
            grace_ends_at=_wall() - timedelta(hours=1),
        )
        assert await _capture(session, subscription_id, owed) == STATE_ACTIVE
        with monkeypatch.context() as patch:
            patch.setattr(domain, "RESERVED_REASONS", frozenset())
            await _operator_suspend(session, subscription_id, "dunning_exhausted")
        assert await _capture(session, subscription_id, owed) == STATE_SUSPENDED


# --------------------------------------------------------------------- minor 2: the setting


class TestTheCutoverSetting:
    @pytest.mark.parametrize(
        "raw",
        [
            0,
            2026,
            "2026",
            "1700000000",
            "2026-10-01",
            "2026-10-01T09:00:00",  # naive
            "2026-09-31T00:00:00Z",  # no such day
            "2026-10-01 09:00:00+05:30",  # a space, not the T
            "next tuesday",
        ],
    )
    def test_anything_but_an_iso_instant_with_an_offset_is_refused_softly(self, raw: Any) -> None:
        cfg = Settings(billing_run_not_before=raw)  # must not raise: the worker must still start
        assert cfg.billing_run_not_before is None
        assert cfg.billing_run_not_before_invalid is not None

    @pytest.mark.parametrize(
        ("raw", "instant"),
        [
            ("2026-10-01T09:00:00+05:30", datetime(2026, 10, 1, 3, 30, tzinfo=UTC)),
            ("2026-10-01T03:30:00Z", datetime(2026, 10, 1, 3, 30, tzinfo=UTC)),
            ("2026-10-01T03:30Z", datetime(2026, 10, 1, 3, 30, tzinfo=UTC)),
            ("2026-10-01T03:30:00.250000+00:00", datetime(2026, 10, 1, 3, 30, 0, 250000, UTC)),
        ],
    )
    def test_an_iso_instant_with_an_offset_is_accepted(self, raw: str, instant: datetime) -> None:
        cfg = Settings(billing_run_not_before=raw)
        assert cfg.billing_run_not_before == instant
        assert cfg.billing_run_not_before_invalid is None

    def test_unset_and_empty_are_unset(self) -> None:
        assert Settings(billing_run_not_before="").billing_run_not_before is None
        assert Settings(billing_run_not_before="").billing_run_not_before_invalid is None
        assert Settings().billing_run_not_before_invalid is None

    def test_a_bad_value_from_the_environment_does_not_stop_the_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What compose hands the process: a string in the environment. Settings loads (the
        payment consumer and the dunning sweep start), and the run's loop is refused."""
        monkeypatch.setenv("BILLING_RUN_ENABLED", "true")
        monkeypatch.setenv("BILLING_RUN_NOT_BEFORE", "0")
        cfg = Settings()
        assert cfg.billing_run_enabled is True
        assert cfg.billing_run_not_before is None
        assert "0" in (cfg.billing_run_not_before_invalid or "")
        sweep = BillingRunSweep(
            discovery_factory=None,  # type: ignore[arg-type]
            work_scope=None,  # type: ignore[arg-type]
            billing=None,  # type: ignore[arg-type]
            payment=None,  # type: ignore[arg-type]
        )
        assert billing_run_loop(cfg, sweep) is None


# ---------------------------------------------------------------- minor 6a: resume in the straddle


async def test_a_resumption_inside_the_straddling_period_charges_no_opening(
    sweep_engines: tuple[Any, Any],
    billing: FakeBilling,
    payment: FakePayment,
    session: Any,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The period that straddles the cutover is free. A customer dunning-suspended and resumed
    inside it is not charged its remainder either."""
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
    began = _wall() - timedelta(days=10)
    await _admin(
        "UPDATE subscription.subscriptions SET current_period_start = :s WHERE id = :i",
        s=began,
        i=subscription_id,
    )
    await _admin(
        "UPDATE subscription.billing_periods SET period_start = :s, full_period_start = :s "
        "WHERE subscription_id = :i AND kind = 'opening'",
        s=began,
        i=subscription_id,
    )
    cutover = _wall() - timedelta(days=5)
    monkeypatch.setattr(settings(), "billing_run_not_before", cutover)
    sweep = _make_sweep(*sweep_engines, billing, payment, not_before=cutover)
    await sweep.tick(now=_wall())  # the opening began before the cutover: skipped
    owed = uuid.uuid4()
    await _dunning_suspend(
        session,
        subscription_id,
        invoice_id=owed,
        failed_at=_wall() - timedelta(days=4),
        grace_ends_at=_wall() - timedelta(hours=2),
    )
    assert await _capture(session, subscription_id, owed) == STATE_ACTIVE
    await sweep.tick(now=_wall())

    periods = await _periods(session, subscription_id)
    openings = _of(periods, domain.KIND_OPENING)
    assert [(o.state, o.outcome) for o in openings] == [("skipped", domain.OUTCOME_PRE_CUTOVER)]
    assert not [r for r in billing.requests if r.kind == domain.KIND_OPENING]


# --------------------------------------------------------------------- the reviewer's edge probes


class TestCutoverEdges:
    async def test_an_opening_exactly_at_the_cutover_is_billed(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        cut = (await _subscription(session, subscription_id)).current_period_start
        assert cut is not None
        await _make_sweep(*sweep_engines, billing, payment, not_before=cut).tick(now=_wall())
        assert domain.KIND_OPENING in [r.kind for r in billing.requests]

    async def test_a_trial_ending_exactly_at_the_cutover_is_billed(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000, trial=True)
        ended = _wall() - timedelta(hours=2)
        await _admin(
            "UPDATE subscription.subscriptions SET trial_ends_at = :t WHERE id = :i",
            t=ended,
            i=subscription_id,
        )
        await _make_sweep(*sweep_engines, billing, payment, not_before=ended).tick(now=_wall())
        assert [r.kind for r in billing.requests] == [domain.KIND_TRIAL_CONVERSION]

    async def test_moving_the_cutover_earlier_rebills_nothing_already_skipped(
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
        later = _wall() - timedelta(hours=1)
        await _make_sweep(*sweep_engines, billing, payment, not_before=later).tick(now=_wall())
        assert billing.requests == []
        before = {(r.kind, r.state, r.outcome) for r in await _periods(session, subscription_id)}
        earlier = end - timedelta(days=40)
        for _ in range(3):
            await _make_sweep(*sweep_engines, billing, payment, not_before=earlier).tick(
                now=_wall()
            )
        after = {(r.kind, r.state, r.outcome) for r in await _periods(session, subscription_id)}
        assert billing.requests == [] and before == after

    async def test_a_future_cutover_rolls_once_and_then_waits(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _period_ended(subscription_id)
        sweep = _make_sweep(*sweep_engines, billing, payment, not_before=_wall() + DAY)
        seen = []
        for _ in range(4):
            report = await sweep.tick(now=_wall())
            seen.append((report.skipped_pre_cutover, len(await _periods(session, subscription_id))))
        assert seen[-1] == seen[-2] and billing.requests == []

    async def test_a_close_retried_after_the_cutover_moved_later_keeps_its_first_window(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: Any,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The documented limitation, pinned: a close clamped once is not clamped again when the
        cutover is moved later — the retry sends the window billing already priced."""
        subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
        await _admin(
            "UPDATE subscription.subscriptions SET current_period_start = :s WHERE id = :i",
            s=_wall() - timedelta(days=10),
            i=subscription_id,
        )
        first = _wall() - timedelta(days=5)
        # The opening (subscribed just now, after the cutover) is billed first, out of the way.
        await _make_sweep(*sweep_engines, billing, payment, not_before=first).tick(now=_wall())
        await _cancel(session, org_id, subscription_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await _make_sweep(*sweep_engines, billing, payment, not_before=first).tick(now=_wall())
        await expire_leases(subscription_id)
        await _make_sweep(
            *sweep_engines, billing, payment, not_before=_wall() - timedelta(days=2)
        ).tick(now=_wall())
        windows = {(r.usage_start, r.usage_end) for r in billing.requests if r.kind == "close"}
        assert len(windows) == 1 and next(iter(windows))[0] == first
