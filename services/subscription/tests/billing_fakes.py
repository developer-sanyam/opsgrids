"""The billing run's in-memory siblings, shared by the billing-run suites.

`FakeBilling` behaves like billing's `POST /internal/v1/subscription-invoices` (one invoice per
claim key, however often asked) and its `…:void`, and asserts on every call that the sweep holds no
open database transaction on the subscription role — the claim → call → record rule. The fixtures
built from these live in `conftest.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.billing_run import BilledInvoice, BillingRunSweep, PeriodRequest, VoidResult
from app.config import settings
from platform_core.db import TENANT_GUC

_ = datetime


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
        self.invoices: dict[tuple[Any, ...], BilledInvoice] = {}
        self.voided: set[uuid.UUID] = set()
        #: Invoices finalized — announced, and so chargeable (round 2, M5). Everything else in
        #: `invoices` is still a draft nothing downstream has seen.
        self.finalized: list[uuid.UUID] = []
        #: Prorations to answer "nothing to bill" for (a change that nets to no more money).
        self.nothing_for: set[uuid.UUID] = set()
        self.crash_before_finalize = False
        self.fail_for: set[uuid.UUID] = set()
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

    async def bill_period(self, request: PeriodRequest) -> BilledInvoice | None:
        await self._assert_no_open_transaction()
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.crash_before:
            self.crash_before = False
            raise Crash
        if self.fail is not None:
            raise RuntimeError(self.fail)
        if request.subscription_id in self.fail_for:
            raise RuntimeError("billing answered 503: catalog is unavailable")
        if request.billing_kind == "proration" and request.subscription_id in self.nothing_for:
            return None
        key = self.key(request)
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

    @staticmethod
    def key(request: PeriodRequest) -> tuple[Any, ...]:
        return (
            request.organization_id,
            request.subscription_id,
            request.billing_kind,
            request.period_start if request.billing_kind != "proration" else request.change_id,
        )

    async def finalize_period(self, request: PeriodRequest) -> bool:
        await self._assert_no_open_transaction()
        if self.crash_before_finalize:
            self.crash_before_finalize = False
            raise Crash
        existing = self.invoices.get(self.key(request))
        assert existing is not None, "finalize for a draft billing never raised"
        assert existing.invoice_id not in self.voided, "a voided draft must never be finalized"
        if existing.invoice_id in self.finalized:
            return False
        self.finalized.append(existing.invoice_id)
        return True

    async def void_period(self, request: PeriodRequest, *, reason: str) -> VoidResult:
        await self._assert_no_open_transaction()
        existing = self.invoices.get(self.key(request))
        if existing is None:
            return VoidResult(voided=False, invoice_id=None)
        self.voided.add(existing.invoice_id)
        return VoidResult(voided=True, invoice_id=existing.invoice_id)


async def expire_leases(subscription_id: uuid.UUID) -> None:
    """Make a claim's lease and backoff lapse, as the wall clock would after ten minutes. Leases
    are wall-clock (review fix 7), so a synthetic `now` can no longer do it."""
    engine = create_async_engine(settings().admin_dsn)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE subscription.billing_periods "
                "SET lease_until = now() - interval '1 second', "
                "next_attempt_at = CASE WHEN next_attempt_at IS NULL THEN NULL "
                "ELSE now() - interval '1 second' END WHERE subscription_id = :s"
            ),
            {"s": subscription_id},
        )
    await engine.dispose()


class FakePayment:
    def __init__(self) -> None:
        self.usable = True
        self.asked: list[tuple[uuid.UUID, str, int]] = []

    async def can_collect(
        self, *, organization_id: uuid.UUID, currency: str, amount_minor: int
    ) -> bool:
        self.asked.append((organization_id, currency, amount_minor))
        return self.usable


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
