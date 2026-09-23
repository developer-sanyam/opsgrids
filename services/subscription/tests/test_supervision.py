"""The worker's loops survive a poisoned row and a database blip, and die together when one dies.

A production crash loop, fixed in the billing-run slice: a dunning pass that raised went straight
through `asyncio.gather` and killed the payment-event consumer beside it — with the poisoned cycle
first in line again on every restart — and `gather` never noticed a consumer that simply
*returned*. Pinned here:

* a poisoned cycle is logged and pushed back; the other due cycles are still acted on;
* a discovery that raises is retried by the loop, which keeps running;
* a loop that returns — or raises — stops the whole worker, non-zero, cancelling the others;
* `CancelledError` is never swallowed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.cron import CYCLE_FAILURE_BACKOFF, DunningSweep
from app.models import STATE_PAST_DUE, DunningCycle, Subscription
from app.supervise import LoopStopped, backoff_delay, backoff_loop, supervise

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


async def _past_due_with_retry_due(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    subscription_id = uuid.uuid4()
    async with session.begin():
        session.add(
            Subscription(
                id=subscription_id,
                organization_id=org_id,
                product_key=f"p{subscription_id.hex[:6]}",
                plan_version_id=uuid.uuid4(),
                state=STATE_PAST_DUE,
                currency="INR",
            )
        )
        await session.flush()
        session.add(
            DunningCycle(
                organization_id=org_id,
                subscription_id=subscription_id,
                opened_at=NOW - timedelta(days=4),
                grace_ends_at=NOW + timedelta(days=10),
                next_action_at=NOW - timedelta(days=1),
                invoice_id=uuid.uuid4(),
                amount_minor=1000,
                currency="INR",
            )
        )
    return subscription_id


async def _cycle(session: AsyncSession, subscription_id: uuid.UUID) -> DunningCycle:
    session.expunge_all()
    async with session.begin():
        return (
            await session.execute(
                select(DunningCycle).where(DunningCycle.subscription_id == subscription_id)
            )
        ).scalar_one()


class TestPoisonedCycle:
    async def test_a_poisoned_cycle_does_not_stop_the_others(
        self,
        sweep: DunningSweep,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        poisoned = await _past_due_with_retry_due(session, org_id)
        healthy = await _past_due_with_retry_due(session, org_id)
        real = services.request_dunning_retry

        async def flaky(session: AsyncSession, subscription: Any, cycle: Any, **kw: Any) -> Any:
            if subscription.id == poisoned:
                raise RuntimeError("poisoned row")
            return await real(session, subscription, cycle, **kw)

        monkeypatch.setattr(services, "request_dunning_retry", flaky)
        before = datetime.now(UTC)
        report = await sweep.tick(now=NOW)

        assert report.failed == 1 and report.retried == 1
        assert (await _cycle(session, healthy)).attempts_made == 1
        bad = await _cycle(session, poisoned)
        assert bad.attempts_made == 0
        assert bad.next_action_at is not None
        assert bad.next_action_at >= before + CYCLE_FAILURE_BACKOFF - timedelta(seconds=5)
        # Pushed back, so the next pass does not hit it first again.
        assert (await sweep.tick(now=NOW)).considered == 0


class TestLoops:
    async def test_a_failing_discovery_is_retried_and_the_loop_keeps_running(self) -> None:
        calls: list[int] = []
        delays: list[float] = []
        stop = False

        async def tick() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("database restarting")
            return "ok"

        async def sleep(seconds: float) -> None:
            nonlocal stop
            delays.append(seconds)
            stop = len(calls) >= 2

        await backoff_loop("test", tick, poll_interval=1.0, stopped=lambda: stop, sleep=sleep)
        assert len(calls) == 2
        assert delays == [2.0, 1.0], "backed off after the failure, back to normal after success"

    def test_backoff_is_capped(self) -> None:
        assert backoff_delay(300.0, 0) == 300.0
        assert backoff_delay(300.0, 1) == 600.0
        assert backoff_delay(300.0, 50) == 3600.0

    async def test_the_sweep_loop_survives_a_raising_tick(
        self, sweep: DunningSweep, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        passes = 0

        async def tick(now: datetime | None = None) -> Any:
            nonlocal passes
            passes += 1
            if passes == 1:
                raise ConnectionError("discovery failed")
            sweep.stop()
            return type("R", (), {"acted": 0})()

        monkeypatch.setattr(sweep, "tick", tick)
        sweep._poll_interval = 0.001
        await asyncio.wait_for(sweep.run(), timeout=5)
        assert passes == 2

    async def test_a_loop_that_returns_stops_the_worker_and_cancels_the_rest(self) -> None:
        cancelled = asyncio.Event()

        async def consumer_that_ends() -> None:
            return None

        async def sweep_forever() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with pytest.raises(LoopStopped, match="payment_consumer returned"):
            await supervise(
                {"payment_consumer": consumer_that_ends(), "dunning_sweep": sweep_forever()}
            )
        assert cancelled.is_set()

    async def test_a_loop_that_raises_stops_the_worker(self) -> None:
        async def broken() -> None:
            raise RuntimeError("consumer lost its channel")

        async def forever() -> None:
            await asyncio.sleep(3600)

        with pytest.raises(LoopStopped, match="payment_consumer failed"):
            await supervise({"payment_consumer": broken(), "billing_run": forever()})

    async def test_cancellation_is_never_swallowed(self) -> None:
        async def tick() -> None:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await backoff_loop("test", tick, poll_interval=0.001, stopped=lambda: False)

        async def forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(supervise({"a": forever(), "b": forever()}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
