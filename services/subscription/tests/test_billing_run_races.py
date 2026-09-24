"""Payment outcomes and the dunning sweep against a writer that got there first (final money
review, major 1 — the reviewer's three races, made permanent).

The payment consumer reads the subscription unlocked and hands it to `record_payment_captured` /
`record_payment_failure`. Every other writer — cancel, suspend, the billing run, the sweep —
locks the row first, so without the lock these handlers decided from state a concurrent writer
had already replaced:

* an operator's fraud hold racing a capture of the owed invoice ended `active`;
* a cancel of a dunning-suspended subscription racing a capture ended `active` with `cancel_at`
  set — then renewed and charged;
* a cancel racing a payment failure ended `past_due` — dunning on a cancelled customer.

Each race is driven the same way: session A makes its write and holds the transaction open;
session B reads the row (seeing A's *un*committed predecessor), then calls the handler; A commits
while B waits. With the lock B blocks on A, re-reads, and decides from what A committed.

And the sweep: it used to lock the cycle and then the subscription, the reverse of a cancel —
40P01 when the two met. Every path now takes subscription, then cycle.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import STATE_CANCELLED, STATE_PAST_DUE, STATE_SUSPENDED
from app.schemas import PaymentOutcome
from platform_core.db import TENANT_GUC
from tests.helpers import FakeCatalog
from tests.test_billing_run import _subscription, _transitions
from tests.test_billing_run_review import _cycle, _dunning_suspend, _fail_charge
from tests.test_billing_run_round2 import _ctx, _subscribe

pytestmark = pytest.mark.integration


def _wall() -> datetime:
    return datetime.now(UTC)


class _Two:
    """Two app-role sessions bound to one tenant, each with an open transaction."""

    async def __aenter__(self) -> tuple[AsyncSession, AsyncSession]:
        cfg = settings()
        args = {"server_settings": {"search_path": "subscription,public"}}
        self._engines = [create_async_engine(cfg.app_dsn, connect_args=args) for _ in range(2)]
        self._sessions = [async_sessionmaker(e, expire_on_commit=False)() for e in self._engines]
        for s in self._sessions:
            await s.begin()
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(self.org_id)}
            )
        return self._sessions[0], self._sessions[1]

    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    async def __aexit__(self, *exc: Any) -> None:
        for s in self._sessions:
            await s.close()
        for e in self._engines:
            await e.dispose()


async def _race(a: AsyncSession, b_work: Any) -> None:
    """Start B's handler while A's transaction is open; commit A; let B finish and commit."""
    task = asyncio.create_task(b_work)
    await asyncio.sleep(0.5)
    assert not task.done(), "B did not wait for A's row lock"
    await a.commit()
    await asyncio.wait_for(task, 10)


async def test_a_capture_racing_an_operator_suspension_leaves_it_suspended(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> None:
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
    owed = uuid.uuid4()
    await _fail_charge(session, subscription_id, owed)
    assert (await _subscription(session, subscription_id)).state == STATE_PAST_DUE

    async with _Two(org_id) as (a, b):
        held = await services.require_subscription(a, subscription_id)
        await services.suspend(a, held, operator_id="ops", reason="fraud_hold")
        stale = await selectors.get_subscription(b, subscription_id)
        assert stale is not None and stale.state == STATE_PAST_DUE
        await _race(a, services.record_payment_captured(b, stale, PaymentOutcome(invoice_id=owed)))
        await b.commit()

    assert (await _subscription(session, subscription_id)).state == STATE_SUSPENDED
    assert "dunning_recovered" not in await _transitions(session, subscription_id)


async def test_a_capture_racing_a_cancel_of_a_dunning_suspension_leaves_it_cancelled(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> None:
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
    owed = uuid.uuid4()
    await _dunning_suspend(
        session,
        subscription_id,
        invoice_id=owed,
        failed_at=_wall() - timedelta(days=5),
        grace_ends_at=_wall() - timedelta(hours=1),
    )

    async with _Two(org_id) as (a, b):
        held = await services.require_subscription(a, subscription_id)
        await services.cancel(a, _ctx(org_id), held)
        stale = await selectors.get_subscription(b, subscription_id)
        assert stale is not None and stale.state == STATE_SUSPENDED
        await _race(a, services.record_payment_captured(b, stale, PaymentOutcome(invoice_id=owed)))
        await b.commit()

    sub = await _subscription(session, subscription_id)
    assert sub.state == STATE_CANCELLED and sub.cancel_at is not None


async def test_a_failure_racing_a_cancel_opens_no_dunning(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
) -> None:
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)

    async with _Two(org_id) as (a, b):
        held = await services.require_subscription(a, subscription_id)
        await services.cancel(a, _ctx(org_id), held)
        stale = await selectors.get_subscription(b, subscription_id)
        assert stale is not None
        await _race(
            a,
            services.record_payment_failure(
                b,
                stale,
                PaymentOutcome(
                    invoice_id=uuid.uuid4(),
                    amount_minor=100000,
                    currency="INR",
                    failure_reason="insufficient_funds",
                ),
            ),
        )
        await b.commit()

    assert (await _subscription(session, subscription_id)).state == STATE_CANCELLED
    assert await _cycle(session, subscription_id) is None, "a cancelled customer was dunned"


async def test_the_sweep_and_a_cancel_do_not_deadlock(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, sweep: Any
) -> None:
    """The cancel holds the subscription; the sweep, due to suspend, used to take the cycle and
    then wait for the subscription — while the cancel waited for the cycle to close it."""
    subscription_id, _ = await _subscribe(session, catalog, org_id, 100000)
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
            now=_wall() - timedelta(days=20),  # the window closed six days ago: due to suspend
        )

    async with _Two(org_id) as (a, _b):
        await selectors.lock_subscription(a, org_id, subscription_id, skip_locked=False)
        pass_ = asyncio.create_task(sweep.tick(_wall()))
        await asyncio.sleep(0.5)
        held = await services.require_subscription(a, subscription_id)
        await services.cancel(a, _ctx(org_id), held)  # closes the cycle: needs its row
        await a.commit()
        report = await asyncio.wait_for(pass_, 15)

    assert report.failed == 0, "the sweep hit a deadlock (40P01) or another error"
    assert (await _subscription(session, subscription_id)).state == STATE_CANCELLED
    cycle = await _cycle(session, subscription_id)
    assert cycle is not None and cycle.closed_reason == "subscription_cancelled"
