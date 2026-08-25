"""The dunning ladder end to end (ADR-0005), against real PostgreSQL.

Supersedes `test_payment_failed_suspends.py`, which proved the MVP default this ADR replaces: a
failed payment used to suspend immediately. It now opens a grace window, and the whole point is
that entitlement survives it.

Everything below drives the real consumers and the real sweep — no mocks, no patched clock. The
sweep's `tick(now)` takes the moment as an argument, so the fourteen-day ladder runs in
milliseconds against real rows, real `FOR UPDATE SKIP LOCKED` claims and real row-level security.

The properties, and why each is here:

* **The full ladder.** fail → `past_due` → retry → retry → retry → `suspended`, with the retries
  landing on their scheduled days and the suspension landing on the window's close and not before.
* **Entitlement survives the window.** Every event emitted while dunning carries a state
  product-service projects to an entitling activation, until the last one. That is the whole
  policy, and it is asserted on the payload the projection actually reads.
* **Recovery at every rung**, including from `suspended` — which is why the ladder stops there
  instead of cancelling.
* **Idempotent redelivery**, at both layers: the same event id twice, and two genuinely distinct
  failures (which the `processed_events` ledger does *not* catch).
* **No double suspension**, from any direction — a redelivered close, a second sweep pass, a
  cycle whose subscription an operator suspended or cancelled underneath it.
* **Boundaries are computed from stored timestamps.** A sweep that runs days late must land on the
  original schedule, not push it out. This is the one that goes silently wrong in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.config import settings
from app.consumers import open_dunning_on_payment_failed, recover_on_payment_captured
from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    DunningCycle,
    OutboxEvent,
    Subscription,
    SubscriptionTransition,
)
from platform_core.db import dispose_engine, init_engine

pytestmark = pytest.mark.integration

#: States product-service projects to an activation that still entitles. Named here rather than
#: imported, because product's projection is another service's code — this is the contract between
#: them, and a test that asserted it by importing across the boundary would prove nothing.
ENTITLING_STATES = (STATE_ACTIVE, STATE_PAST_DUE, "trial")

INVOICE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
AMOUNT_MINOR = 149900  # ₹1,499.00 — under the UPI Autopay ceiling, so a retry can actually run
CURRENCY = "INR"


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumers open their own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


def _failed(
    org_id: uuid.UUID,
    subscription_id: uuid.UUID | None,
    *,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return _payment_event(org_id, subscription_id, "commerce.payment.failed", "failed", event_id)


def _captured(
    org_id: uuid.UUID,
    subscription_id: uuid.UUID | None,
    *,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return _payment_event(
        org_id, subscription_id, "commerce.payment.captured", "captured", event_id
    )


def _payment_event(
    org_id: uuid.UUID,
    subscription_id: uuid.UUID | None,
    event_type: str,
    state: str,
    event_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(org_id),
            "charge_id": str(uuid.uuid4()),
            "invoice_id": str(INVOICE_ID),
            "subscription_id": str(subscription_id) if subscription_id else None,
            "product_key": "hrms",
            "amount_minor": AMOUNT_MINOR,
            "currency": CURRENCY,
            "state": state,
        },
    }


async def _seed_subscription(
    session: AsyncSession, org_id: uuid.UUID, *, state: str = STATE_ACTIVE
) -> uuid.UUID:
    subscription_id = uuid.uuid4()
    async with session.begin():
        session.add(
            Subscription(
                id=subscription_id,
                organization_id=org_id,
                product_key="hrms",
                plan_version_id=uuid.uuid4(),
                state=state,
                currency=CURRENCY,
                current_period_start=datetime.now(UTC),
            )
        )
    return subscription_id


async def _state(session: AsyncSession, subscription_id: uuid.UUID) -> str:
    # The consumers and the sweep write through their *own* sessions, so anything this one has
    # already loaded is stale. `expunge_all` forces a real read rather than the identity map —
    # without it a test asserting on a counter reads the value it saw before the write.
    session.expunge_all()
    async with session.begin():
        subscription = await session.get(Subscription, subscription_id)
    assert subscription is not None
    return subscription.state


async def _cycle(session: AsyncSession, subscription_id: uuid.UUID) -> DunningCycle:
    session.expunge_all()
    async with session.begin():
        cycle = (
            await session.execute(
                select(DunningCycle).where(DunningCycle.subscription_id == subscription_id)
            )
        ).scalar_one()
    return cycle


async def _events(session: AsyncSession) -> list[OutboxEvent]:
    session.expunge_all()
    async with session.begin():
        rows = (
            (await session.execute(select(OutboxEvent).order_by(OutboxEvent.created_at)))
            .scalars()
            .all()
        )
    return list(rows)


async def _count(session: AsyncSession, event_type: str) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == event_type)
                )
            ).scalar_one()
        )


async def _open_the_window(
    session: AsyncSession, org_id: uuid.UUID
) -> tuple[uuid.UUID, DunningCycle]:
    """The first failure: a subscription in `active`, one `commerce.payment.failed`."""
    subscription_id = await _seed_subscription(session, org_id)
    await open_dunning_on_payment_failed(_failed(org_id, subscription_id))
    return subscription_id, await _cycle(session, subscription_id)


class TestTheFirstFailureOpensAWindow:
    async def test_it_moves_to_past_due_and_does_not_suspend(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The behaviour ADR-0005 replaces, asserted as an absence.

        Before this ADR the same event produced `commerce.subscription.suspended` and a 402. It
        must now produce `past_due` and nothing else — the customer keeps the product while the
        platform tries to collect.
        """
        subscription_id, cycle = await self._open(session, org_id)

        assert await _state(session, subscription_id) == STATE_PAST_DUE
        assert await _count(session, domain.EVT_PAST_DUE) == 1
        assert await _count(session, domain.EVT_SUSPENDED) == 0
        assert cycle.state == "open"
        assert cycle.attempts_made == 0
        assert cycle.failures_seen == 1

    async def test_the_window_is_recorded_at_open_not_derived_later(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`grace_ends_at` is a stored column, and the first retry is anchored to `opened_at`.

        Both are what the sweep will later compare against, so if either were recomputed from a
        clock the schedule could move under a customer who has already been told when their access
        ends.
        """
        _, cycle = await self._open(session, org_id)

        assert cycle.grace_ends_at == cycle.opened_at + domain.POLICY.grace
        assert cycle.next_action_at == cycle.opened_at + domain.POLICY.retry_offsets[0]

    async def test_the_event_tells_the_customer_what_happens_next(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0005 §5: the dunning facts ride the same event the projection reads, so "keep the
        product on" and "tell them we will retry on Tuesday" cannot land separately."""
        _, cycle = await self._open(session, org_id)

        (event,) = [e for e in await _events(session) if e.event_type == domain.EVT_PAST_DUE]
        dunning = event.payload["dunning"]
        assert event.payload["state"] == STATE_PAST_DUE
        assert dunning["grace_ends_at"] == cycle.grace_ends_at.isoformat()
        assert dunning["next_attempt_at"] == cycle.next_action_at.isoformat()
        assert dunning["max_attempts"] == domain.POLICY.max_attempts
        assert dunning["amount_minor"] == AMOUNT_MINOR
        assert dunning["currency"] == CURRENCY

    async def test_a_second_failure_does_not_open_a_second_window(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Two *distinct* failure events — not a redelivery, which the processed-events ledger
        would catch. A second charge failing must count against the window already open, not
        restart it: a cycle whose clock resets on every failure is a grace period the failures it
        exists to survive can extend indefinitely."""
        subscription_id, first = await self._open(session, org_id)

        await open_dunning_on_payment_failed(_failed(org_id, subscription_id))

        after = await _cycle(session, subscription_id)
        assert after.id == first.id
        assert after.opened_at == first.opened_at
        assert after.grace_ends_at == first.grace_ends_at
        assert after.failures_seen == 2
        assert await _count(session, domain.EVT_PAST_DUE) == 1, "a second window was opened"

    async def test_a_trial_is_not_dunned(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        """A trial carries no debt (ADR-0005 §1). A failed conversion charge must not end a trial
        early — the trial lapses on its own date, which product-service evaluates at read time."""
        subscription_id = await _seed_subscription(session, org_id, state="trial")

        await open_dunning_on_payment_failed(_failed(org_id, subscription_id))

        assert await _state(session, subscription_id) == "trial"
        async with session.begin():
            cycles = (
                (
                    await session.execute(
                        select(DunningCycle).where(DunningCycle.subscription_id == subscription_id)
                    )
                )
                .scalars()
                .all()
            )
        assert list(cycles) == []

    async def _open(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> tuple[uuid.UUID, DunningCycle]:
        return await _open_the_window(session, org_id)


class TestTheLadder:
    async def test_the_whole_ladder_from_failure_to_suspension(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """fail → past_due → retry → retry → retry → suspend, on the days ADR-0005 fixes.

        The headline. Each `tick` is the sweep waking at a moment; nothing else advances the
        ladder, which is the point — before this ADR one event did all of it at once.
        """
        subscription_id, cycle = await _open_the_window(session, org_id)
        opened = cycle.opened_at

        timeline: list[tuple[str, str, int]] = []
        for elapsed in (
            timedelta(hours=6),  # nothing due yet
            timedelta(days=3),  # retry 1
            timedelta(days=7),  # retry 2
            timedelta(days=12),  # retry 3 — the last automatic attempt
            timedelta(days=13),  # the quiet gap: ladder spent, window still open
            timedelta(days=14),  # the window closes
        ):
            report = await sweep.tick(opened + elapsed)
            timeline.append(
                (await _state(session, subscription_id), f"{elapsed.days}d", report.acted)
            )

        assert timeline == [
            (STATE_PAST_DUE, "0d", 0),
            (STATE_PAST_DUE, "3d", 1),
            (STATE_PAST_DUE, "7d", 1),
            (STATE_PAST_DUE, "12d", 1),
            (STATE_PAST_DUE, "13d", 0),
            (STATE_SUSPENDED, "14d", 1),
        ]
        assert await _count(session, domain.EVT_PAYMENT_RETRY_DUE) == 3
        assert await _count(session, domain.EVT_SUSPENDED) == 1

        closed = await _cycle(session, subscription_id)
        assert closed.state == "exhausted"
        assert closed.attempts_made == domain.POLICY.max_attempts
        assert closed.closed_reason == "grace_expired"
        assert closed.next_action_at is None, "a closed cycle must leave the sweep's due set"

    async def test_entitlement_survives_every_rung_and_stops_exactly_once(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """The policy, read off the events product-service actually consumes.

        The projection keys on the `state` in the payload, not the event name — so this asserts
        the contract at the seam: every lifecycle event emitted during the window carries an
        entitling state, and exactly one carries a de-entitling one.
        """
        _, cycle = await _open_the_window(session, org_id)
        for elapsed in (timedelta(days=3), timedelta(days=7), timedelta(days=12)):
            await sweep.tick(cycle.opened_at + elapsed)

        during = [
            e.payload["state"]
            for e in await _events(session)
            if e.event_type.startswith("commerce.subscription.")
            and e.event_type != domain.EVT_PAYMENT_RETRY_DUE
        ]
        assert during and all(s in ENTITLING_STATES for s in during), (
            "a customer inside their grace window was de-entitled"
        )

        await sweep.tick(cycle.grace_ends_at)

        after = [
            e.payload["state"]
            for e in await _events(session)
            if e.event_type.startswith("commerce.subscription.")
            and e.event_type != domain.EVT_PAYMENT_RETRY_DUE
        ]
        assert after[-1] == STATE_SUSPENDED
        assert after.count(STATE_SUSPENDED) == 1

    async def test_each_retry_asks_for_a_debit_under_its_own_idempotency_key(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """Razorpay has no idempotency key of its own (ADR-0004 §6), so a retry that reused the
        failed charge's key would be deduplicated into the charge that already failed — a ladder
        that looks like it is retrying and debits nobody."""
        subscription_id, cycle = await _open_the_window(session, org_id)
        for elapsed in (timedelta(days=3), timedelta(days=7), timedelta(days=12)):
            await sweep.tick(cycle.opened_at + elapsed)

        retries = [
            e for e in await _events(session) if e.event_type == domain.EVT_PAYMENT_RETRY_DUE
        ]
        assert [e.payload["attempt_no"] for e in retries] == [1, 2, 3]
        assert len({e.payload["idempotency_key"] for e in retries}) == 3
        for n, event in enumerate(retries, start=1):
            assert event.payload["idempotency_key"] == f"dunning:{cycle.id}:{n}"
            assert event.payload["subscription_id"] == str(subscription_id)
            assert event.payload["invoice_id"] == str(INVOICE_ID)
            assert event.payload["amount_minor"] == AMOUNT_MINOR
            assert event.payload["currency"] == CURRENCY

    async def test_a_retry_is_not_a_lifecycle_transition(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """Asking for money is not a change in what the customer has. If a retry wrote a
        transition, the operator console's churn and MRR series would count three moves per
        dunned subscription that never moved."""
        subscription_id, cycle = await _open_the_window(session, org_id)
        await sweep.tick(cycle.opened_at + domain.POLICY.retry_offsets[0])

        session.expunge_all()
        async with session.begin():
            transitions = (
                (
                    await session.execute(
                        select(SubscriptionTransition.to_state)
                        .where(SubscriptionTransition.subscription_id == subscription_id)
                        .order_by(SubscriptionTransition.occurred_at)
                    )
                )
                .scalars()
                .all()
            )
        assert list(transitions) == [STATE_PAST_DUE], "a retry request wrote a lifecycle transition"

    async def test_the_sweep_does_nothing_when_nothing_is_due(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        _, cycle = await _open_the_window(session, org_id)

        report = await sweep.tick(cycle.opened_at + timedelta(days=2, hours=23))

        assert report.considered == 0
        assert report.acted == 0


class TestRecovery:
    @pytest.mark.parametrize(
        "elapsed",
        [
            pytest.param(timedelta(hours=1), id="before-any-retry"),
            pytest.param(timedelta(days=4), id="after-retry-1"),
            pytest.param(timedelta(days=8), id="after-retry-2"),
            pytest.param(timedelta(days=13), id="after-the-last-retry"),
        ],
    )
    async def test_a_captured_payment_recovers_at_any_rung_of_the_ladder(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any, elapsed: timedelta
    ) -> None:
        subscription_id, cycle = await _open_the_window(session, org_id)
        for step in (timedelta(days=3), timedelta(days=7), timedelta(days=12)):
            if step < elapsed:
                await sweep.tick(cycle.opened_at + step)

        await recover_on_payment_captured(_captured(org_id, subscription_id))

        assert await _state(session, subscription_id) == STATE_ACTIVE
        closed = await _cycle(session, subscription_id)
        assert closed.state == "recovered"
        assert closed.closed_reason == "payment_captured"
        assert closed.next_action_at is None
        assert await _count(session, domain.EVT_ACTIVATED) == 1

    async def test_a_captured_payment_recovers_a_suspended_subscription(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """The reason the ladder ends at `suspended` and not `cancelled` (ADR-0005 §4).

        A customer who pays in week six comes back to the same subscription — same id, same pinned
        plan version, same history. `cancelled` is terminal, and a machine must not take an
        irreversible commercial act on a timer.
        """
        subscription_id, cycle = await _open_the_window(session, org_id)
        pinned_before = (await _cycle(session, subscription_id)).subscription_id
        await sweep.tick(cycle.grace_ends_at)
        assert await _state(session, subscription_id) == STATE_SUSPENDED

        await recover_on_payment_captured(_captured(org_id, subscription_id))

        assert await _state(session, subscription_id) == STATE_ACTIVE
        assert pinned_before == subscription_id

    async def test_a_recovered_subscription_can_be_dunned_again_next_period(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A closed cycle is history; the next failure opens a new one with its own window. The
        partial unique index only forbids two *open* cycles."""
        subscription_id, first = await _open_the_window(session, org_id)
        await recover_on_payment_captured(_captured(org_id, subscription_id))

        await open_dunning_on_payment_failed(_failed(org_id, subscription_id))

        session.expunge_all()
        async with session.begin():
            cycles = (
                (
                    await session.execute(
                        select(DunningCycle)
                        .where(DunningCycle.subscription_id == subscription_id)
                        .order_by(DunningCycle.opened_at)
                    )
                )
                .scalars()
                .all()
            )
        assert [c.state for c in cycles] == ["recovered", "open"]
        assert cycles[1].id != first.id
        assert await _state(session, subscription_id) == STATE_PAST_DUE

    async def test_a_capture_for_an_active_subscription_moves_nothing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The ordinary renewal case. Nothing changed, so nothing is emitted and the projection is
        not woken to rewrite the row it already holds."""
        subscription_id = await _seed_subscription(session, org_id)

        await recover_on_payment_captured(_captured(org_id, subscription_id))

        assert await _state(session, subscription_id) == STATE_ACTIVE
        assert await _events(session) == []


class TestIdempotencyAndNoDoubleSuspension:
    async def test_a_redelivered_failure_is_a_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Delivery is at-least-once. The same event id twice must not count twice — otherwise
        `failures_seen` is a number nobody can explain."""
        subscription_id = await _seed_subscription(session, org_id)
        envelope = _failed(org_id, subscription_id)

        await open_dunning_on_payment_failed(envelope)
        await open_dunning_on_payment_failed(envelope)

        assert await _count(session, domain.EVT_PAST_DUE) == 1
        assert (await _cycle(session, subscription_id)).failures_seen == 1

    async def test_a_redelivered_capture_is_a_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id, _ = await _open_the_window(session, org_id)
        envelope = _captured(org_id, subscription_id)

        await recover_on_payment_captured(envelope)
        await recover_on_payment_captured(envelope)

        assert await _count(session, domain.EVT_ACTIVATED) == 1

    async def test_a_second_sweep_pass_does_not_suspend_twice(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """A sweep replica, a restart, a retried pass — all of them re-run `tick` at a moment past
        the window. Exactly one `suspended` must ever be emitted, or the projection is asked to
        de-entitle a customer who may have recovered in between."""
        subscription_id, cycle = await _open_the_window(session, org_id)

        await sweep.tick(cycle.grace_ends_at)
        second = await sweep.tick(cycle.grace_ends_at + timedelta(hours=1))
        third = await sweep.tick(cycle.grace_ends_at + timedelta(days=30))

        assert await _count(session, domain.EVT_SUSPENDED) == 1
        assert second.considered == 0 and third.considered == 0
        assert await _state(session, subscription_id) == STATE_SUSPENDED

    async def test_a_failure_after_suspension_does_not_reopen_a_window(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """A charge raised before the window closed can fail after it. The ladder is over; a late
        failure must not start a second one on a suspended subscription."""
        subscription_id, cycle = await _open_the_window(session, org_id)
        await sweep.tick(cycle.grace_ends_at)

        await open_dunning_on_payment_failed(_failed(org_id, subscription_id))

        assert await _state(session, subscription_id) == STATE_SUSPENDED
        assert await _count(session, domain.EVT_SUSPENDED) == 1
        async with session.begin():
            open_cycles = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(DunningCycle)
                        .where(DunningCycle.state == "open")
                    )
                ).scalar_one()
            )
        assert open_cycles == 0

    async def test_a_cancelled_subscription_is_left_untouched(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`cancelled` is terminal, so a late payment failure neither suspends nor emits — it just
        records the event handled."""
        subscription_id = await _seed_subscription(session, org_id, state=STATE_CANCELLED)

        await open_dunning_on_payment_failed(_failed(org_id, subscription_id))

        assert await _state(session, subscription_id) == STATE_CANCELLED
        assert await _events(session) == []

    async def test_a_cancellation_mid_ladder_closes_the_cycle(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """Otherwise the sweep would still hold a scheduled suspension against a terminal
        subscription, and `past_due -> cancelled -> suspended` is an edge the state machine
        refuses — a handler that raises forever and dead-letters, in the money path."""
        from app import selectors, services
        from platform_core.auth import Scope, ScopeContext
        from tests.helpers import customer_principal

        subscription_id, cycle = await _open_the_window(session, org_id)
        ctx = ScopeContext(
            principal=customer_principal(org_id),
            scope=Scope.ORG,
            permission="core.product.activation.write",
        )
        async with session.begin():
            subscription = await selectors.get_subscription(session, subscription_id)
            assert subscription is not None
            await services.cancel(session, ctx, subscription)

        report = await sweep.tick(cycle.grace_ends_at)

        assert report.considered == 0
        assert await _state(session, subscription_id) == STATE_CANCELLED
        assert await _count(session, domain.EVT_SUSPENDED) == 0
        assert (await _cycle(session, subscription_id)).state == "abandoned"

    async def test_an_operator_suspension_mid_ladder_closes_the_cycle(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """An operator overrides the ladder rather than racing it. Without closing the cycle, the
        sweep would later attempt `suspended -> suspended`, which is not a legal edge."""
        from app import selectors, services

        subscription_id, cycle = await _open_the_window(session, org_id)
        async with session.begin():
            subscription = await selectors.get_subscription(session, subscription_id)
            assert subscription is not None
            await services.suspend(session, subscription, operator_id="console")

        report = await sweep.tick(cycle.grace_ends_at)

        assert report.considered == 0
        assert await _count(session, domain.EVT_SUSPENDED) == 1
        assert (await _cycle(session, subscription_id)).state == "abandoned"


class TestTheScheduleDoesNotDrift:
    async def test_a_late_sweep_lands_on_the_original_schedule(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """The failure that goes silently wrong in production.

        The sweep was down and only wakes at T+6d, long after retry 1 was due. Retry 1 runs — and
        the *next* action must still be the stored T+7d, not seven days from when the sweep got
        round to it. Computed as `now + interval` the answer would be T+13d, every late pass would
        compound, and fourteen days of grace would quietly become twenty.
        """
        subscription_id, cycle = await _open_the_window(session, org_id)
        opened = cycle.opened_at

        await sweep.tick(opened + timedelta(days=6))  # three days late

        after = await _cycle(session, subscription_id)
        assert after.attempts_made == 1
        assert after.next_action_at == opened + domain.POLICY.retry_offsets[1]

    async def test_the_window_never_moves_however_late_the_sweep_runs(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """`grace_ends_at` was promised to the customer at open. A sweep that runs a week late
        must suspend against the stored boundary, not extend it by a week."""
        subscription_id, cycle = await _open_the_window(session, org_id)
        promised = cycle.grace_ends_at

        await sweep.tick(promised + timedelta(days=7))

        after = await _cycle(session, subscription_id)
        assert after.grace_ends_at == promised
        assert await _state(session, subscription_id) == STATE_SUSPENDED

    async def test_a_ladder_missed_entirely_still_suspends_exactly_once(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """The sweep was off for the whole window. It must not fire three retries and a suspension
        on its first pass back: past the boundary the window dominates the ladder, so the answer is
        one suspension and no debits nobody is waiting for."""
        subscription_id, cycle = await _open_the_window(session, org_id)

        report = await sweep.tick(cycle.grace_ends_at + timedelta(days=3))

        assert report.suspended == 1
        assert report.retried == 0
        assert await _count(session, domain.EVT_PAYMENT_RETRY_DUE) == 0
        assert await _state(session, subscription_id) == STATE_SUSPENDED


class TestTenantIsolation:
    async def test_a_failure_naming_another_tenant_touches_nothing(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """Under organization B's binding, organization A's subscription id is invisible — so
        nothing is dunned and nothing is emitted."""
        subscription_id = await _seed_subscription(session, org_id)

        await open_dunning_on_payment_failed(_failed(other_org, subscription_id))

        assert await _state(session, subscription_id) == STATE_ACTIVE
        assert await _events(session) == []

    async def test_the_sweep_acts_on_each_tenants_own_cycle(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        sweep: Any,
    ) -> None:
        """Discovery is cross-tenant, every write is not.

        The sweep finds both organizations' due cycles in one bypass query and then suspends each
        under its own tenant binding. This is the seam ADR-0005 §6 describes: the bypass buys
        discovery, never authority — a bug that lost the per-tenant binding would fail the tenant
        policy here rather than suspend across the platform.
        """
        mine, cycle = await _open_the_window(session, org_id)

        theirs = uuid.uuid4()
        async with admin_session.begin():
            admin_session.add(
                Subscription(
                    id=theirs,
                    organization_id=other_org,
                    product_key="hrms",
                    plan_version_id=uuid.uuid4(),
                    state=STATE_PAST_DUE,
                    currency=CURRENCY,
                    current_period_start=cycle.opened_at,
                )
            )
            admin_session.add(
                DunningCycle(
                    organization_id=other_org,
                    subscription_id=theirs,
                    state="open",
                    opened_at=cycle.opened_at,
                    grace_ends_at=cycle.grace_ends_at,
                    next_action_at=cycle.grace_ends_at,
                    attempts_made=domain.POLICY.max_attempts,
                    failures_seen=1,
                )
            )

        report = await sweep.tick(cycle.grace_ends_at)

        assert report.considered == 2, "the cross-tenant discovery query missed a tenant"
        assert report.suspended == 2
        assert await _state(session, mine) == STATE_SUSPENDED
        async with admin_session.begin():
            other = await admin_session.get(Subscription, theirs)
        assert other is not None and other.state == STATE_SUSPENDED
