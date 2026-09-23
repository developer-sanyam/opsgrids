"""The billing run end to end on subscription's side, against real PostgreSQL.

Real rows, real `FOR UPDATE SKIP LOCKED`, real row-level security, the real outbox-role discovery
— and billing and payment replaced by in-memory fakes that behave like the real routes: billing
answers one invoice per `(subscription, period_start)` however often it is asked, which is what
billing's own suite proves against its database (`services/billing/tests/test_billing_run.py`).
The clock is an argument to `tick(now)`, so a year of periods runs in milliseconds.

What is pinned, and why each matters:

* **Trials convert** — to `active` with a first invoice when a mandate can collect it (or there is
  nothing to collect), to `past_due` with a grace window anchored at the trial's end when it
  cannot (ADR-0013 §6, ADR-0005).
* **No double invoice** — on a re-run, on a crash between claim and record (both before and after
  billing committed), on redelivery, and with two sweeps racing.
* **Periods roll** from the stored boundary, one period per pass, with the closed period as the
  usage window; a suspended subscription rolls without an invoice; a cancelled one is never
  touched; a bundle is one subscription and one invoice.
* **A plan change is priced from the next period**, never mid-period.
* **No network call inside a transaction** — the fake billing checks `pg_stat_activity` for an
  open transaction on the service's role while it is being called.
* **Tenants stay apart**, and `/health/billing-run` reports counts, never ids.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, selectors, services
from app.billing_run import BilledInvoice, BillingRunSweep, PeriodRequest
from app.config import settings
from app.models import (
    PERIOD_INVOICED,
    PERIOD_PENDING,
    PERIOD_SKIPPED,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    BillingPeriod,
    DunningCycle,
    OutboxEvent,
    Subscription,
    SubscriptionItem,
    SubscriptionTransition,
)
from platform_core.db import TENANT_GUC, dispose_outbox_engine, init_outbox_engine

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
MONTH = timedelta(days=30)
PRICE_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
PRICE_SEAT = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")


class Crash(BaseException):
    """A process kill, as far as the sweep can tell: a `BaseException`, so the sweep's
    per-subscription `except Exception` does not catch it and nothing is recorded."""


# --------------------------------------------------------------------- fakes


class FakeBilling:
    """billing's `POST /internal/v1/subscription-invoices`, in memory, idempotent on the period.

    `totals` prices a subscription; `crash_before`/`crash_after` simulate a kill on either side of
    billing's commit; `fail` answers with an error. Every call also asserts that the sweep holds
    no open transaction on the subscription role while it waits — the claim → call → record rule.
    """

    def __init__(self, observer_dsn: str) -> None:
        # The bootstrap superuser: only it (or pg_read_all_stats) can read another role's
        # `pg_stat_activity.state`. Through the admin role every row reads as NULL and the
        # tripwire below would pass whatever the sweep did.
        self._admin = create_async_engine(observer_dsn)
        self.check_transactions = True
        self.invoices: dict[tuple[uuid.UUID, uuid.UUID, datetime], BilledInvoice] = {}
        self.requests: list[PeriodRequest] = []
        self.totals: dict[uuid.UUID, int] = {}
        self.crash_before = False
        self.crash_after = False
        self.fail: str | None = None
        self.gate: asyncio.Event | None = None

    async def aclose(self) -> None:
        await self._admin.dispose()

    async def _assert_no_open_transaction(self) -> None:
        if not self.check_transactions:
            return
        async with self._admin.connect() as conn:
            open_txns = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND usename = 'opsgrids_subscription' "
                        "AND state LIKE 'idle in transaction%'"
                    )
                )
            ).scalar_one()
        assert open_txns == 0, "billing was called while a subscription transaction was open"

    async def bill_period(self, request: PeriodRequest) -> BilledInvoice:
        await self._assert_no_open_transaction()
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.crash_before:
            self.crash_before = False
            raise Crash
        if self.fail is not None:
            raise RuntimeError(self.fail)
        key = (request.organization_id, request.subscription_id, request.period_start)
        existing = self.invoices.get(key)
        if existing is not None:
            answer = BilledInvoice(
                existing.invoice_id, existing.total_minor, existing.currency, False
            )
        else:
            answer = BilledInvoice(
                invoice_id=uuid.uuid4(),
                total_minor=self.totals.get(request.subscription_id, 149900),
                currency=request.currency,
                created=True,
            )
            self.invoices[key] = answer
        if self.crash_after:
            self.crash_after = False
            raise Crash
        return answer


class FakePayment:
    def __init__(self) -> None:
        self.usable = True
        self.asked: list[tuple[uuid.UUID, str, int]] = []

    async def can_collect(
        self, *, organization_id: uuid.UUID, currency: str, amount_minor: int
    ) -> bool:
        self.asked.append((organization_id, currency, amount_minor))
        return self.usable


# --------------------------------------------------------------------- fixtures


@pytest_asyncio.fixture
async def billing(session: AsyncSession) -> AsyncIterator[FakeBilling]:
    fake = FakeBilling(settings().database_url)
    yield fake
    await fake.aclose()


@pytest.fixture
def payment() -> FakePayment:
    return FakePayment()


def _make_sweep(
    discovery_engine: Any, work_engine: Any, billing: FakeBilling, payment: FakePayment
) -> BillingRunSweep:
    discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    return BillingRunSweep(
        discovery_factory=discovery, work_scope=work_scope, billing=billing, payment=payment
    )


@pytest.fixture
def run(
    sweep_engines: tuple[Any, Any], billing: FakeBilling, payment: FakePayment
) -> BillingRunSweep:
    return _make_sweep(*sweep_engines, billing, payment)


async def _seed(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    state: str = STATE_TRIAL,
    trial_ends_at: datetime | None = T0,
    period_start: datetime | None = T0 - timedelta(days=14),
    period_end: datetime | None = T0 - timedelta(days=14) + MONTH,
    product_key: str = "hrms",
    entitled: list[str] | None = None,
    interval: str | None = "month",
    currency: str = "INR",
) -> uuid.UUID:
    subscription_id = uuid.uuid4()
    async with session.begin():
        session.add(
            Subscription(
                id=subscription_id,
                organization_id=org_id,
                product_key=product_key,
                plan_version_id=uuid.uuid4(),
                state=state,
                trial_ends_at=trial_ends_at if state == STATE_TRIAL else None,
                current_period_start=period_start,
                current_period_end=period_end,
                billing_interval=interval,
                currency=currency,
                entitled_products_snapshot=entitled,
            )
        )
        await session.flush()
        session.add_all(
            [
                SubscriptionItem(
                    organization_id=org_id,
                    subscription_id=subscription_id,
                    price_id=PRICE_A,
                    quantity=1,
                ),
                SubscriptionItem(
                    organization_id=org_id,
                    subscription_id=subscription_id,
                    price_id=PRICE_SEAT,
                    quantity=7,
                ),
            ]
        )
    return subscription_id


async def _subscription(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    session.expunge_all()
    async with session.begin():
        row = await session.get(Subscription, subscription_id)
    assert row is not None
    return row


async def _periods(session: AsyncSession, subscription_id: uuid.UUID) -> list[BillingPeriod]:
    session.expunge_all()
    async with session.begin():
        rows = (
            await session.execute(
                select(BillingPeriod)
                .where(BillingPeriod.subscription_id == subscription_id)
                .order_by(BillingPeriod.period_start)
            )
        ).scalars()
        return list(rows.all())


async def _events(session: AsyncSession, event_type: str) -> list[OutboxEvent]:
    session.expunge_all()
    async with session.begin():
        rows = (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.event_type == event_type)
                .order_by(OutboxEvent.created_at)
            )
        ).scalars()
        return list(rows.all())


async def _transitions(session: AsyncSession, subscription_id: uuid.UUID) -> list[str]:
    session.expunge_all()
    async with session.begin():
        rows = (
            await session.execute(
                select(SubscriptionTransition.reason)
                .where(SubscriptionTransition.subscription_id == subscription_id)
                .order_by(SubscriptionTransition.occurred_at)
            )
        ).scalars()
        return [r or "" for r in rows.all()]


# --------------------------------------------------------------------- pure rules


class TestDomain:
    def test_a_trial_converts_at_its_own_end_not_at_now(self) -> None:
        late = T0 + timedelta(hours=6)
        due = domain.due_period(
            state=STATE_TRIAL,
            trial_ends_at=T0,
            current_period_start=T0 - timedelta(days=14),
            current_period_end=T0 + timedelta(days=16),
            billing_interval="month",
            now=late,
        )
        assert due is not None
        assert (due.kind, due.start, due.end) == (domain.KIND_TRIAL_CONVERSION, T0, T0 + MONTH)
        assert due.usage_start is None, "trial usage is not billed"

    def test_a_period_rolls_from_its_stored_end_with_the_closed_period_as_usage(self) -> None:
        due = domain.due_period(
            state=STATE_ACTIVE,
            trial_ends_at=None,
            current_period_start=T0,
            current_period_end=T0 + MONTH,
            billing_interval="month",
            now=T0 + MONTH + timedelta(days=3),
        )
        assert due is not None
        assert (due.start, due.end) == (T0 + MONTH, T0 + 2 * MONTH)
        assert (due.usage_start, due.usage_end) == (T0, T0 + MONTH)

    @pytest.mark.parametrize("state", [STATE_CANCELLED, STATE_TRIAL, STATE_ACTIVE])
    def test_nothing_is_due_before_its_boundary_or_ever_when_cancelled(self, state: str) -> None:
        now = T0 - timedelta(seconds=1) if state != STATE_CANCELLED else T0 + 10 * MONTH
        assert (
            domain.due_period(
                state=state,
                trial_ends_at=T0,
                current_period_start=T0 - MONTH,
                current_period_end=T0,
                billing_interval="month",
                now=now,
            )
            is None
        )

    def test_the_cadence_of_an_older_row_is_read_off_its_period_length(self) -> None:
        assert domain.infer_interval(T0, T0 + MONTH) == "month"
        assert domain.infer_interval(T0, T0 + timedelta(days=365)) == "year"
        assert domain.infer_interval(T0, T0 + timedelta(days=31)) is None

    def test_a_one_time_cadence_is_not_billed_by_guess(self) -> None:
        with pytest.raises(domain.NotBillable):
            domain.due_period(
                state=STATE_TRIAL,
                trial_ends_at=T0,
                current_period_start=None,
                current_period_end=None,
                billing_interval="one_time",
                now=T0,
            )

    def test_backoff_doubles_and_is_capped(self) -> None:
        assert domain.retry_backoff(1) == timedelta(minutes=5)
        assert domain.retry_backoff(3) == timedelta(minutes=20)
        assert domain.retry_backoff(50) == timedelta(hours=6)

    def test_the_new_edges_and_only_those(self) -> None:
        assert domain.is_legal(STATE_TRIAL, STATE_PAST_DUE)
        assert domain.is_legal(STATE_PAST_DUE, STATE_PAST_DUE)
        assert not domain.is_legal(STATE_SUSPENDED, STATE_SUSPENDED)
        assert not domain.is_legal(STATE_CANCELLED, STATE_ACTIVE)


# --------------------------------------------------------------------- trial end


class TestTrialEnd:
    async def test_a_trial_with_a_mandate_converts_and_raises_the_first_invoice(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)

        report = await run.tick(now=T0 + timedelta(minutes=5))

        assert report.activated == 1 and report.issued == 1
        subscription = await _subscription(session, subscription_id)
        assert subscription.state == STATE_ACTIVE
        assert subscription.current_period_start == T0
        assert subscription.current_period_end == T0 + MONTH

        (request,) = billing.requests
        assert request.period_start == T0 and request.period_end == T0 + MONTH
        assert request.usage_start is None and request.usage_end is None
        assert request.items == [
            {"price_id": str(PRICE_A), "quantity": 1},
            {"price_id": str(PRICE_SEAT), "quantity": 7},
        ]
        assert payment.asked == [(org_id, "INR", 149900)]

        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_INVOICED and period.outcome == "activated"
        assert period.total_minor == 149900

        (event,) = await _events(session, domain.EVT_ACTIVATED)
        assert event.payload["state"] == STATE_ACTIVE
        assert event.payload["billing"]["invoice_id"] == str(period.invoice_id)
        assert event.payload["billing"]["total_minor"] == 149900
        assert await _transitions(session, subscription_id) == [domain.REASON_TRIAL_CONVERT]

    async def test_a_trial_with_no_usable_mandate_goes_past_due_with_grace_from_its_end(
        self,
        run: BillingRunSweep,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """ADR-0013 §6: the product keeps working for ADR-0005's fourteen days, anchored at the
        trial's end — a sweep that ran a day late does not hand out fifteen."""
        payment.usable = False
        subscription_id = await _seed(session, org_id)

        report = await run.tick(now=T0 + timedelta(days=1))

        assert report.past_due == 1
        assert (await _subscription(session, subscription_id)).state == STATE_PAST_DUE
        session.expunge_all()
        async with session.begin():
            cycle = (
                await session.execute(
                    select(DunningCycle).where(DunningCycle.subscription_id == subscription_id)
                )
            ).scalar_one()
        (period,) = await _periods(session, subscription_id)
        assert cycle.opened_at == T0
        assert cycle.grace_ends_at == T0 + domain.POLICY.grace
        assert cycle.invoice_id == period.invoice_id
        assert cycle.amount_minor == 149900 and cycle.currency == "INR"
        (event,) = await _events(session, domain.EVT_PAST_DUE)
        assert event.payload["dunning"]["cycle_id"] == str(cycle.id)
        assert event.payload["billing"]["invoice_id"] == str(period.invoice_id)
        assert await _transitions(session, subscription_id) == [domain.REASON_TRIAL_UNPAID]

    async def test_a_zero_invoice_converts_without_asking_payment(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.totals[subscription_id] = 0
        payment.usable = False

        await run.tick(now=T0)

        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE
        assert payment.asked == []

    async def test_a_trial_still_running_is_left_alone(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        report = await run.tick(now=T0 - timedelta(seconds=1))
        assert report.considered == 0
        assert billing.requests == []
        assert (await _subscription(session, subscription_id)).state == STATE_TRIAL


# --------------------------------------------------------------------- exactly once


class TestExactlyOnce:
    async def test_a_rerun_raises_no_second_invoice(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        await run.tick(now=T0)
        await run.tick(now=T0)
        await run.tick(now=T0 + timedelta(days=1))
        assert len(billing.requests) == 1
        assert len(billing.invoices) == 1
        assert len(await _periods(session, subscription_id)) == 1
        assert len(await _events(session, domain.EVT_ACTIVATED)) == 1

    async def test_killed_after_billing_committed_and_before_record(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The invoice exists in billing; nothing was recorded here. The retry, once the lease
        lapses, sends the identical request and gets the same invoice back — one invoice, one
        conversion."""
        subscription_id = await _seed(session, org_id)
        billing.crash_after = True
        with pytest.raises(Crash):
            await run.tick(now=T0)

        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_PENDING and period.attempts == 1
        assert (await _subscription(session, subscription_id)).state == STATE_TRIAL

        # Inside the lease a second replica does not call billing at all.
        await run.tick(now=T0 + timedelta(minutes=5))
        assert len(billing.requests) == 1

        await run.tick(now=T0 + domain.BILLING_LEASE + timedelta(seconds=1))
        assert len(billing.requests) == 2
        assert billing.requests[0] == billing.requests[1], "the retry must ask for the same thing"
        assert len(billing.invoices) == 1
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_INVOICED and period.attempts == 2
        (invoice,) = billing.invoices.values()
        assert period.invoice_id == invoice.invoice_id
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE
        assert len(await _events(session, domain.EVT_ACTIVATED)) == 1

    async def test_killed_after_the_claim_before_billing_was_asked(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.crash_before = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        assert billing.invoices == {}

        await run.tick(now=T0 + domain.BILLING_LEASE + timedelta(seconds=1))
        assert len(billing.invoices) == 1
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE

    async def test_a_plan_change_after_the_claim_does_not_change_the_retried_request(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.crash_before = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        first_pin = billing.requests[0].plan_version_id
        async with session.begin():
            row = await session.get(Subscription, subscription_id)
            assert row is not None
            row.plan_version_id = uuid.uuid4()

        await run.tick(now=T0 + domain.BILLING_LEASE + timedelta(seconds=1))
        assert billing.requests[-1].plan_version_id == first_pin

    async def test_two_sweeps_racing_raise_one_invoice(
        self,
        sweep_engines: tuple[Any, Any],
        billing: FakeBilling,
        payment: FakePayment,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Two replicas discover the same trial at the same instant. One claims; the other finds
        the subscription locked or the period leased and does nothing."""
        subscription_id = await _seed(session, org_id)
        # Two sweeps interleave by design here, so one may be mid-claim while the other calls:
        # the tripwire would see the *other* sweep's short transaction. It is proven elsewhere.
        billing.check_transactions = False
        a = _make_sweep(*sweep_engines, billing, payment)
        b = _make_sweep(*sweep_engines, billing, payment)
        reports = await asyncio.gather(a.tick(now=T0), b.tick(now=T0))
        assert sum(r.activated for r in reports) == 1
        assert len(billing.requests) == 1
        assert len(billing.invoices) == 1
        assert await _transitions(session, subscription_id) == [domain.REASON_TRIAL_CONVERT]

    async def test_a_failed_call_backs_off_and_then_succeeds(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.fail = "billing answered 503: catalog is unavailable"

        report = await run.tick(now=T0)
        assert report.failed == 1
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_PENDING
        assert period.last_error is not None and "503" in period.last_error
        assert period.lease_until == T0 + domain.retry_backoff(1)

        billing.fail = None
        await run.tick(now=T0 + timedelta(minutes=1))  # still backing off
        assert (await _subscription(session, subscription_id)).state == STATE_TRIAL
        await run.tick(now=T0 + domain.retry_backoff(1))
        assert (await _subscription(session, subscription_id)).state == STATE_ACTIVE


# --------------------------------------------------------------------- periods


class TestPeriods:
    async def test_an_active_period_rolls_and_bills_the_closed_period_usage(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(
            session, org_id, state=STATE_ACTIVE, period_start=T0 - MONTH, period_end=T0
        )
        report = await run.tick(now=T0 + timedelta(hours=1))

        assert report.renewed == 1
        (request,) = billing.requests
        assert (request.period_start, request.period_end) == (T0, T0 + MONTH)
        assert (request.usage_start, request.usage_end) == (T0 - MONTH, T0)
        subscription = await _subscription(session, subscription_id)
        assert subscription.state == STATE_ACTIVE
        assert subscription.current_period_end == T0 + MONTH
        (event,) = await _events(session, domain.EVT_RENEWED)
        assert event.payload["billing"]["period_start"] == T0.isoformat()

        # Nothing more until the new period closes; then exactly the next one.
        await run.tick(now=T0 + MONTH - timedelta(seconds=1))
        assert len(billing.requests) == 1
        await run.tick(now=T0 + MONTH)
        assert billing.requests[-1].period_start == T0 + MONTH
        assert billing.requests[-1].usage_start == T0

    async def test_a_backlog_is_billed_one_period_per_pass(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The run was down for two months. Each missed period is its own invoice and key."""
        await _seed(session, org_id, state=STATE_ACTIVE, period_start=T0 - MONTH, period_end=T0)
        late = T0 + 2 * MONTH + timedelta(days=1)
        for _ in range(4):
            await run.tick(now=late)
        starts = [r.period_start for r in billing.requests]
        assert starts == [T0, T0 + MONTH, T0 + 2 * MONTH]
        assert len(billing.invoices) == 3

    async def test_a_cancelled_subscription_is_never_invoiced(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _seed(session, org_id, state=STATE_CANCELLED, period_start=T0 - MONTH, period_end=T0)
        report = await run.tick(now=T0 + 3 * MONTH)
        assert report.considered == 0
        assert billing.requests == []

    async def test_a_suspended_subscription_rolls_without_an_invoice(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(
            session, org_id, state=STATE_SUSPENDED, period_start=T0 - MONTH, period_end=T0
        )
        report = await run.tick(now=T0)
        assert report.skipped_suspended == 1
        assert billing.requests == []
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_SKIPPED and period.invoice_id is None
        subscription = await _subscription(session, subscription_id)
        assert subscription.state == STATE_SUSPENDED
        assert subscription.current_period_end == T0 + MONTH
        assert await _transitions(session, subscription_id) == []

    async def test_a_past_due_subscription_is_invoiced_and_its_window_left_alone(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(
            session, org_id, state=STATE_PAST_DUE, period_start=T0 - MONTH, period_end=T0
        )
        async with session.begin():
            session.add(
                DunningCycle(
                    organization_id=org_id,
                    subscription_id=subscription_id,
                    opened_at=T0 - timedelta(days=2),
                    grace_ends_at=T0 + timedelta(days=12),
                    next_action_at=T0 + timedelta(days=1),
                )
            )
        await run.tick(now=T0)
        assert len(billing.requests) == 1
        (event,) = await _events(session, domain.EVT_RENEWED)
        assert event.payload["state"] == STATE_PAST_DUE
        session.expunge_all()
        async with session.begin():
            cycle = (
                await session.execute(
                    select(DunningCycle).where(DunningCycle.subscription_id == subscription_id)
                )
            ).scalar_one()
        assert cycle.state == "open" and cycle.grace_ends_at == T0 + timedelta(days=12)

    async def test_a_bundle_is_one_subscription_and_one_invoice(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _seed(
            session,
            org_id,
            state=STATE_ACTIVE,
            product_key="suite",
            entitled=["hrms", "crm", "finance"],
            period_start=T0 - MONTH,
            period_end=T0,
        )
        await run.tick(now=T0)
        (request,) = billing.requests
        assert request.product_key == "suite"
        assert len(billing.invoices) == 1

    async def test_a_plan_change_is_priced_from_the_next_period(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No mid-period proration (ADR-0005 addendum): the change itself raises nothing, and
        the renewal that follows is priced off the new pin and rolled on its cadence."""
        subscription_id = await _seed(
            session, org_id, state=STATE_ACTIVE, period_start=T0 - MONTH, period_end=T0
        )
        new_pin = uuid.uuid4()
        async with session.begin():
            row = await session.get(Subscription, subscription_id)
            assert row is not None
            row.plan_version_id = new_pin
            row.billing_interval = "year"
        await run.tick(now=T0 - timedelta(days=1))
        assert billing.requests == [], "a plan change mid-period raises no invoice"
        await run.tick(now=T0)
        (request,) = billing.requests
        assert request.plan_version_id == new_pin
        assert request.period_end == T0 + timedelta(days=365)

    async def test_an_older_row_without_a_cadence_rolls_on_its_period_length(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _seed(
            session,
            org_id,
            state=STATE_ACTIVE,
            period_start=T0 - MONTH,
            period_end=T0,
            interval=None,
        )
        await run.tick(now=T0)
        assert billing.requests[0].period_end == T0 + MONTH

    async def test_a_one_time_trial_is_reported_not_guessed(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(
            session, org_id, period_start=None, period_end=None, interval="one_time"
        )
        report = await run.tick(now=T0)
        assert report.failed == 1
        assert billing.requests == []
        assert await _periods(session, subscription_id) == []

    async def test_a_claim_whose_subscription_was_cancelled_is_closed_not_billed(
        self, run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = await _seed(session, org_id)
        billing.crash_before = True
        with pytest.raises(Crash):
            await run.tick(now=T0)
        async with session.begin():
            row = await session.get(Subscription, subscription_id)
            assert row is not None
            row.state = STATE_CANCELLED

        report = await run.tick(now=T0 + domain.BILLING_LEASE + timedelta(seconds=1))
        assert report.stale_closed == 1
        assert billing.invoices == {}
        (period,) = await _periods(session, subscription_id)
        assert period.state == PERIOD_SKIPPED and period.outcome == "superseded"


# --------------------------------------------------------------------- tenancy & health


class TestTenancyAndHealth:
    async def test_each_tenant_is_billed_under_its_own_binding(
        self,
        run: BillingRunSweep,
        billing: FakeBilling,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        mine = await _seed(session, org_id)
        theirs = await _seed(admin_session, other_org)

        report = await run.tick(now=T0)

        assert report.activated == 2
        assert {(r.organization_id, r.subscription_id) for r in billing.requests} == {
            (org_id, mine),
            (other_org, theirs),
        }
        # The app role bound to org A cannot see, lock or record against org B's subscription.
        session.expunge_all()
        async with session.begin():
            assert await selectors.lock_subscription(session, theirs, skip_locked=True) is None
            rows = (
                await session.execute(select(func.count()).select_from(BillingPeriod))
            ).scalar_one()
        assert rows == 1

    async def test_the_health_probe_counts_and_names_nobody(
        self,
        run: BillingRunSweep,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        init_outbox_engine(settings())
        try:
            await _seed(session, org_id, trial_ends_at=datetime.now(UTC) - timedelta(days=2))
            before = (await client.get("/health/billing-run")).json()
            assert before["overdue"] == 1
            assert before["invoiced_24h"] == 0

            await run.tick(now=datetime.now(UTC))
            after = await client.get("/health/billing-run")
            assert after.status_code == 200
            body = after.json()
            assert body["overdue"] == 0 and body["pending"] == 0
            assert body["invoiced_24h"] == 1
            assert str(org_id) not in after.text
            assert set(body) == {
                "overdue",
                "pending",
                "failing",
                "oldest_pending_seconds",
                "invoiced_24h",
                "skipped_24h",
                "last_recorded_at",
            }
        finally:
            await dispose_outbox_engine()

    async def test_discovery_runs_on_the_outbox_role_and_sees_every_tenant(
        self,
        sweep_engines: tuple[Any, Any],
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """The grant in `migrations/env.py` is applied: without it this raises; without
        BYPASSRLS it would quietly return nothing — the stall this seam exists to prevent."""
        await _seed(session, org_id)
        await _seed(admin_session, other_org)
        discovery_engine, _ = sweep_engines
        maker = async_sessionmaker(discovery_engine)
        async with maker() as s, s.begin():
            due = await selectors.due_billing_subscriptions(s, now=T0)
        assert {org for org, _ in due} == {org_id, other_org}


async def test_the_tripwire_sees_an_open_transaction(
    billing: FakeBilling, session: AsyncSession
) -> None:
    """The fake billing's check is only worth something if it can fail."""
    async with session.begin():
        await session.execute(text("SELECT 1"))
        with pytest.raises(AssertionError):
            await billing._assert_no_open_transaction()


async def test_the_dry_run_lists_what_is_owed_and_writes_nothing(
    run: BillingRunSweep, billing: FakeBilling, session: AsyncSession, org_id: uuid.UUID
) -> None:
    """The first-deploy check: an expired trial and an active subscription two periods behind."""
    trial = await _seed(session, org_id)
    behind = await _seed(
        session,
        org_id,
        state=STATE_ACTIVE,
        product_key="crm",
        period_start=T0 - MONTH,
        period_end=T0,
    )
    rows = await run.preview(now=T0 + MONTH + timedelta(days=1))
    by_id = {r["subscription_id"]: r for r in rows}
    assert by_id[str(trial)]["kind"] == domain.KIND_TRIAL_CONVERSION
    assert by_id[str(trial)]["periods_owed"] == 2  # the first paid period, then its successor
    assert by_id[str(behind)]["kind"] == domain.KIND_RENEWAL
    assert by_id[str(behind)]["periods_owed"] == 2
    assert billing.requests == []
    assert await _periods(session, trial) == []
    assert (await _subscription(session, trial)).state == STATE_TRIAL


async def test_services_record_is_a_no_op_once_recorded(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """A second record for the same claim — a replica that finished its call after another
    recorded — changes nothing."""
    subscription_id = await _seed(session, org_id)
    async with session.begin():
        subscription = await selectors.lock_subscription(
            session, subscription_id, skip_locked=False
        )
        assert subscription is not None
        due = domain.due_period(
            state=subscription.state,
            trial_ends_at=subscription.trial_ends_at,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            billing_interval=subscription.billing_interval,
            now=T0,
        )
        assert due is not None
        period = await services.claim_billing_period(session, subscription, due, now=T0)
        assert period is not None
        invoice_id = uuid.uuid4()
        first = await services.record_billing_period(
            session,
            subscription,
            period,
            invoice_id=invoice_id,
            total_minor=10,
            collectable=True,
            now=T0,
        )
        second = await services.record_billing_period(
            session,
            subscription,
            period,
            invoice_id=uuid.uuid4(),
            total_minor=99,
            collectable=True,
            now=T0,
        )
    assert (first, second) == ("activated", "already_recorded")
    (row,) = await _periods(session, subscription_id)
    assert row.invoice_id == invoice_id and row.total_minor == 10


def test_engine_for_tests_is_not_the_shared_database() -> None:
    """The suite must never touch the shared `opsgrids` database."""
    from sqlalchemy.engine import make_url

    assert make_url(settings().app_dsn).database != "opsgrids"
