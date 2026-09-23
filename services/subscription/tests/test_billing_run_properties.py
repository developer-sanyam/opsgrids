"""The billing run's money invariants, over randomised lifecycles (ADR-0005 addendum, round 3).

Three review rounds found money defects by constructing one unlucky interleaving at a time. This
suite generates them: for each seed, a subscription lives through a random sequence of trial,
upgrade, downgrade, equal-price switch, suspension, resumption, a failed and a recovered payment,
crashes between every step of the run, billing outages and passes of the run, on a controlled
clock — then is cancelled, the run catches up, and the whole history is checked against the
invariants below. Seeds are deterministic (`random.Random(seed)`), so a failure names a seed
that reproduces it exactly; `BILLING_PROPERTY_SEEDS` raises or lowers the count (default 200).

The invariants, all over what billing actually **finalized** (announced, and so chargeable):

1. **Recurring time is charged exactly once.** Base charges — a renewal or conversion period, or
   an opening charge — never overlap, never start before the paid start (a trial's end) or at or
   after the cancellation, and together cover every instant the subscription was served.
2. **Usage windows** never overlap, stay inside `[paid start, cancellation]`, and cover every
   served instant: usage is billed once, and none is dropped.
3. **At the pin in force.** A base charge and a usage window are priced at the pin in force at
   their start, and no pin change falls strictly inside a usage window.
4. **Nothing charged on a voided or skipped parent**, no finalized invoice ever voided, no charge
   on a row that is not `finalized`, no two charges for one upgrade, and every claim settled.
5. **Totals are integers.**
"""

from __future__ import annotations

import bisect
import contextlib
import itertools
import os
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import billing_run as billing_run_module
from app import domain, services
from app.billing_run import BillingRunSweep
from app.models import (
    PERIOD_FINALIZED,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    BillingPeriod,
    DunningCycle,
)
from app.schemas import PaymentOutcome
from tests.billing_fakes import Crash, FakeBilling, FakePayment
from tests.helpers import FakeCatalog
from tests.test_billing_run import _periods, _subscription
from tests.test_billing_run_round2 import _ctx, _subscribe, _version

pytestmark = pytest.mark.integration

SEEDS = int(os.environ.get("BILLING_PROPERTY_SEEDS", "200"))

CLOCK = [datetime(2026, 1, 1, tzinfo=UTC)]


class _Clock(datetime):
    """`datetime` with a `now` the test drives — patched into the modules that read the clock."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return CLOCK[0]


OPS = (
    "upgrade",
    "upgrade",
    "equal",
    "downgrade",
    "suspend",
    "resume",
    "pay_fail",
    "pay_ok",
    "crash",
    "crash_after",
    "crash_finalize",
    "outage",
    "tick",
    "tick",
    "tick",
)


class History:
    """What the test did and saw, on the controlled clock."""

    def __init__(self, start: datetime) -> None:
        self.states: list[tuple[datetime, str]] = [(start, "")]
        self.pins: list[tuple[datetime, uuid.UUID]] = []
        self.log: list[Any] = []

    def pin_at(self, at: datetime) -> uuid.UUID:
        times = [t for t, _ in self.pins]
        index = bisect.bisect_right(times, at) - 1
        return self.pins[max(index, 0)][1]

    def changes_inside(self, start: datetime, end: datetime) -> list[datetime]:
        return [t for t, _ in self.pins[1:] if start < t < end]


async def _run(
    seed: int,
    run: BillingRunSweep,
    billing: FakeBilling,
    payment: FakePayment,
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
) -> list[Any]:
    rng = random.Random(seed)  # noqa: S311 — deterministic test data, not secrets
    CLOCK[0] = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=seed % 97, minutes=seed)
    trial = rng.random() < 0.35
    prices: dict[uuid.UUID, int] = {}
    subscription_id, version = await _subscribe(session, catalog, org_id, 100000, trial=trial)
    prices[version.id] = 100000
    sub = await _subscription(session, subscription_id)
    started = CLOCK[0]
    paid_start = sub.trial_ends_at if trial and sub.trial_ends_at else started
    history = History(started)
    history.pins.append((started, version.id))

    async def observe(after_tick: bool = False) -> Any:
        sub = await _subscription(session, subscription_id)
        if sub.state != history.states[-1][1]:
            history.states.append((CLOCK[0], sub.state))
        if sub.plan_version_id != history.pins[-1][1]:
            at = CLOCK[0]
            if after_tick:
                # A deferred downgrade takes effect at the start of the period it was applied to.
                rows = await _periods(session, subscription_id)
                starts = [
                    r.period_start
                    for r in rows
                    if r.kind == domain.KIND_RENEWAL and r.plan_version_id == sub.plan_version_id
                ]
                at = min(starts) if starts else at
            history.pins.append((at, sub.plan_version_id))
        return sub

    async def tick() -> None:
        with contextlib.suppress(Crash):
            await run.tick(now=CLOCK[0])
        await observe(after_tick=True)

    async def latest_charged_invoice() -> uuid.UUID | None:
        rows = await _periods(session, subscription_id)
        ids = [r.invoice_id for r in rows if r.invoice_id in billing.finalized]
        return ids[-1] if ids else None

    async def open_cycle() -> DunningCycle | None:
        session.expunge_all()
        async with session.begin():
            return (
                await session.execute(
                    select(DunningCycle).where(
                        DunningCycle.subscription_id == subscription_id,
                        DunningCycle.state == "open",
                    )
                )
            ).scalar_one_or_none()

    async def captured(invoice_id: uuid.UUID | None) -> None:
        session.expunge_all()
        async with session.begin():
            s = await services.require_subscription(session, subscription_id)
            await services.record_payment_captured(
                session, s, PaymentOutcome(invoice_id=invoice_id)
            )

    for _ in range(rng.randint(6, 16)):
        CLOCK[0] += rng.choice(
            [timedelta(minutes=11), timedelta(hours=7), timedelta(days=rng.randint(1, 25))]
        )
        op = rng.choice(OPS)
        sub = await _subscription(session, subscription_id)
        history.log.append((CLOCK[0].isoformat(), op, sub.state))
        try:
            if op in ("upgrade", "equal", "downgrade"):
                current = prices.get(sub.plan_version_id, 100000)
                target = {
                    "upgrade": current + 50000,
                    "equal": current,
                    "downgrade": max(10000, current - 50000),
                }[op]
                new = await _version(catalog, target)
                prices[new.id] = target
                session.expunge_all()
                async with session.begin():
                    s = await services.require_subscription(session, subscription_id)
                    from app.schemas import ChangePlanIn

                    await services.change_plan(
                        session, _ctx(org_id), catalog, s, ChangePlanIn(plan_version_id=new.id)
                    )
            elif op == "suspend" and sub.state in (STATE_ACTIVE, STATE_PAST_DUE):
                session.expunge_all()
                async with session.begin():
                    s = await services.require_subscription(session, subscription_id)
                    await services.suspend(session, s, operator_id="ops")
            elif op == "resume" and sub.state == STATE_SUSPENDED:
                await captured(None)
            elif op == "pay_fail" and sub.state == STATE_ACTIVE:
                invoice_id = await latest_charged_invoice()
                if invoice_id is not None:
                    session.expunge_all()
                    async with session.begin():
                        s = await services.require_subscription(session, subscription_id)
                        await services.record_payment_failure(
                            session, s, PaymentOutcome(invoice_id=invoice_id, amount_minor=1)
                        )
            elif op == "pay_ok" and sub.state == STATE_PAST_DUE:
                cycle = await open_cycle()
                await captured(cycle.invoice_id if cycle is not None else None)
            elif op == "crash":
                billing.crash_before = True
                await tick()
                billing.crash_before = False
            elif op == "crash_after":
                billing.crash_after = True
                await tick()
                billing.crash_after = False
            elif op == "crash_finalize":
                billing.crash_before_finalize = True
                await tick()
                billing.crash_before_finalize = False
            elif op == "outage":
                billing.fail = "billing answered 503"
                await tick()
                billing.fail = None
            elif op == "tick":
                await tick()
        except Exception as exc:
            history.log.append(("refused", type(exc).__name__, str(exc)[:90]))
        await observe()
        # Leases and backoff read the same clock; a crash's lease lapses by the next step.

    # Cancel — once billing has caught up enough to allow it — then let the run finish.
    cancelled_at: datetime | None = None
    for _ in range(12):
        CLOCK[0] += timedelta(minutes=30)
        await tick()
        sub = await _subscription(session, subscription_id)
        if sub.state == STATE_CANCELLED:
            break
        try:
            session.expunge_all()
            async with session.begin():
                s = await services.require_subscription(session, subscription_id)
                await services.cancel(session, _ctx(org_id), s)
                cancelled_at = s.cancel_at
            break
        except Exception as exc:
            history.log.append(("cancel_refused", type(exc).__name__, str(exc)[:90]))
    await observe()
    for _ in range(10):
        CLOCK[0] += timedelta(hours=7)
        await tick()

    sub = await _subscription(session, subscription_id)
    cancelled_at = sub.cancel_at or cancelled_at
    rows = await _periods(session, subscription_id)
    return _check(history, rows, billing, paid_start, cancelled_at, trial)


def _served(
    history: History, paid_start: datetime, stop: datetime
) -> list[tuple[datetime, datetime]]:
    """The intervals the subscription was served and owed for: from the paid start to the stop,
    minus suspensions."""
    out: list[tuple[datetime, datetime]] = []
    states = [*history.states, (stop, STATE_CANCELLED)]
    for (t, state), (t_next, _) in itertools.pairwise(states):
        start, end = max(t, paid_start), min(t_next, stop)
        if state in ("", STATE_ACTIVE, STATE_PAST_DUE, STATE_TRIAL) and start < end:
            out.append((start, end))
    return out


def _uncovered(
    need: list[tuple[datetime, datetime]], have: list[tuple[datetime, datetime]]
) -> list[tuple[str, str]]:
    gaps = []
    spans = sorted(have)
    for start, end in need:
        cursor = start
        for s, e in spans:
            if e <= cursor or s >= end:
                continue
            if s > cursor:
                gaps.append((cursor.isoformat(), s.isoformat()))
            cursor = max(cursor, e)
            if cursor >= end:
                break
        if cursor < end:
            gaps.append((cursor.isoformat(), end.isoformat()))
    return gaps


def _overlaps(spans: list[tuple[datetime, datetime, str]]) -> list[Any]:
    spans = sorted(spans)
    return [(a, b) for a, b in itertools.pairwise(spans) if b[0] < a[1]]


def _check(
    history: History,
    rows: list[BillingPeriod],
    billing: FakeBilling,
    paid_start: datetime,
    cancelled_at: datetime | None,
    trial: bool,
) -> list[Any]:
    problems: list[Any] = []
    finalized = set(billing.finalized)
    charged = [r for r in rows if r.invoice_id is not None and r.invoice_id in finalized]
    by_id = {r.id: r for r in rows}

    # 4. settlement and state discipline
    for r in rows:
        if r.state in ("pending", "finalize_pending", "void_pending"):
            problems.append(("unsettled", r.kind, r.state, r.last_error))
    for r in charged:
        if r.state != PERIOD_FINALIZED:
            problems.append(("charged_but_row_not_finalized", r.kind, r.state))
        if r.invoice_id in billing.voided:
            problems.append(("finalized_then_voided", r.kind))
        parent = getattr(r, "against_period_id", None)
        if parent is not None and (
            parent not in by_id or by_id[parent].invoice_id not in finalized
        ):
            problems.append(("charged_on_uncharged_parent", r.kind, str(parent)))
        if not isinstance(r.total_minor, int):
            problems.append(("non_integer_total", r.kind))  # 5.
    upgrades: dict[Any, int] = {}
    for r in charged:
        if r.kind == domain.KIND_PRORATION:
            key = (r.full_period_start, r.from_plan_version_id, r.plan_version_id)
            upgrades[key] = upgrades.get(key, 0) + 1
    problems += [("upgrade_charged_twice", n) for n in upgrades.values() if n > 1]

    stop = cancelled_at or max(t for t, _ in history.states)
    served = _served(history, paid_start, stop)

    # 1. recurring time
    base_kinds = (domain.KIND_RENEWAL, domain.KIND_TRIAL_CONVERSION, "opening")
    base = [
        (r.period_start, r.period_end, r.kind)
        for r in charged
        if r.kind in base_kinds and r.period_end is not None
    ]
    problems += [("recurring_charged_twice", a, b) for a, b in _overlaps(base)]
    for s, _e, kind in base:
        if s < paid_start:
            problems.append(("charged_for_trial_time", kind, s.isoformat()))
        if cancelled_at is not None and s >= cancelled_at:
            problems.append(("charged_after_cancel", kind, s.isoformat()))
    problems += [
        ("recurring_uncharged", g) for g in _uncovered(served, [(s, e) for s, e, _ in base])
    ]

    # 2. usage
    usage = [
        (r.usage_start, r.usage_end, r.kind)
        for r in charged
        if r.usage_start is not None and r.usage_end is not None
    ]
    problems += [("usage_billed_twice", a, b) for a, b in _overlaps(usage)]
    for s, e, kind in usage:
        if s < paid_start or (cancelled_at is not None and e > cancelled_at):
            problems.append(("usage_outside_paid_time", kind, s.isoformat(), e.isoformat()))
    problems += [("usage_unbilled", g) for g in _uncovered(served, [(s, e) for s, e, _ in usage])]

    # 3. pins
    for r in charged:
        if r.kind in (domain.KIND_RENEWAL, domain.KIND_CLOSE, "opening"):
            at = r.usage_start if r.kind == domain.KIND_CLOSE and r.usage_start else r.period_start
            if r.plan_version_id != history.pin_at(at):
                problems.append(("priced_at_wrong_pin", r.kind, at.isoformat()))
        if r.usage_start is not None and r.usage_end is not None:
            inside = history.changes_inside(r.usage_start, r.usage_end)
            if inside:
                problems.append(
                    ("pin_changed_inside_usage", r.kind, [t.isoformat() for t in inside])
                )
    _ = trial
    return problems


@pytest.mark.parametrize("seed", range(SEEDS))
async def test_money_invariants_hold(
    seed: int,
    run: BillingRunSweep,
    billing: FakeBilling,
    payment: FakePayment,
    session: AsyncSession,
    catalog: FakeCatalog,
    org_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(services, "datetime", _Clock)
    monkeypatch.setattr(billing_run_module, "datetime", _Clock)
    billing.check_transactions = False  # the clock is fake; the tripwire is proven elsewhere
    problems = await _run(seed, run, billing, payment, session, catalog, org_id)
    assert not problems, f"seed {seed}: {problems[:6]}"
