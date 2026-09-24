"""Two transactions at once, against real PostgreSQL — the races the advisory locks close.

An activation is the OR of its sources, and a source's out-of-order guard compares the event's
time with a watermark it reads. Both are read-then-write. Without a lock, two transactions each
read a view that excludes the other's uncommitted write, and whichever *commits* last wins:

* **A — leak.** The standalone HRMS and the suite are cancelled at the same moment. Each fold
  sees the other source still live, recomputes "active", and the product stays on with both of
  its sources suspended.
* **B — lockout.** The owner deactivates their own HRMS while the suite that includes it is
  being projected active. The deactivation sees no other live source and switches HRMS off
  under a live suite.
* **C — ordering bypass.** An older `cancelled` for the suite commits after a newer `active`;
  both read the same watermark, so the older one lands last and locks the trio out.

Each test holds the first transaction open, starts the second, proves the second is *waiting*
rather than racing, then commits the first and checks where the rows end up. These are the
reviewer's reproductions, kept as regressions.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.db import TENANT_GUC
from platform_core.errors import BusinessRuleProblem

pytestmark = pytest.mark.integration

TRIO = ["hrms", "crm", "finance"]
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
async def tx(session: AsyncSession, launched: tuple[str, ...]) -> AsyncIterator[Any]:
    """Open app-role transactions bound to a tenant, each on its own connection.

    Depends on `session` for its truncation and on `launched` so the trio is sellable.
    """
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=4,
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _open(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            await s.begin()
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
            )
            try:
                yield s
            finally:
                if s.in_transaction():
                    await s.rollback()

    yield _open
    await engine.dispose()


async def _fold(tx: Any, org_id: uuid.UUID, **kwargs: Any) -> None:
    async with tx(org_id) as s:
        await services.project_subscription(s, organization_id=org_id, trial_ends_at=None, **kwargs)
        await s.commit()


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    principal = Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=["core.product.activation.write:ORG"],
        entitlements=["core"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="core.product.activation.write"
    )


async def _race(
    tx: Any,
    org_id: uuid.UUID,
    first: Callable[[AsyncSession], Awaitable[Any]],
    second: Callable[[AsyncSession], Awaitable[Any]],
) -> BaseException | None:
    """Run `first` and hold its transaction open; start `second`; prove it waits; commit
    `first`; let `second` finish. Returns what `second` raised, if anything."""
    async with tx(org_id) as s1:
        await first(s1)

        async def _second() -> None:
            async with tx(org_id) as s2:
                await second(s2)
                await s2.commit()

        task = asyncio.create_task(_second())
        await asyncio.sleep(0.5)
        assert not task.done(), "the second transaction did not wait for the first's locks"
        await s1.commit()
    results = await asyncio.gather(task, return_exceptions=True)
    return results[0] if isinstance(results[0], BaseException) else None


async def _entitled(tx: Any, org_id: uuid.UUID) -> set[str]:
    async with tx(org_id) as s:
        return set(await selectors.entitlements_for(s, org_id)) - {"core", "notification"}


async def test_a_two_sources_cancelled_at_once_both_count(tx: Any, org_id: uuid.UUID) -> None:
    await _fold(tx, org_id, product_key="hrms", state="active", occurred_at=_at(0))
    await _fold(
        tx, org_id, product_key="suite", state="active", occurred_at=_at(1), entitled_products=TRIO
    )
    assert "hrms" in await _entitled(tx, org_id)

    async def cancel_own(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="hrms",
            state="cancelled",
            trial_ends_at=None,
            occurred_at=_at(2),
        )

    async def cancel_suite(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="suite",
            state="cancelled",
            trial_ends_at=None,
            occurred_at=_at(2),
            entitled_products=TRIO,
        )

    assert await _race(tx, org_id, cancel_own, cancel_suite) is None
    assert "hrms" not in await _entitled(tx, org_id), (
        "leak: hrms stayed entitled with both of its sources cancelled"
    )


async def test_b_deactivating_under_a_suite_being_projected_is_refused(
    tx: Any, org_id: uuid.UUID
) -> None:
    async with tx(org_id) as s:
        await services.activate(s, _ctx(org_id), product_key="hrms")
        await s.commit()

    async def suite_active(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="suite",
            state="active",
            trial_ends_at=None,
            occurred_at=_at(1),
            entitled_products=TRIO,
        )

    async def owner_deactivates(s: AsyncSession) -> None:
        await services.deactivate(s, _ctx(org_id), product_key="hrms")

    raised = await _race(tx, org_id, suite_active, owner_deactivates)
    assert isinstance(raised, BusinessRuleProblem), (
        f"the deactivation should have seen the suite and refused; got {raised!r}"
    )
    assert "hrms" in await _entitled(tx, org_id), "lockout: hrms off under a live suite"


async def test_c_an_older_event_committing_last_is_still_dropped(
    tx: Any, org_id: uuid.UUID
) -> None:
    await _fold(
        tx,
        org_id,
        product_key="suite",
        state="suspended",
        occurred_at=_at(0),
        entitled_products=TRIO,
    )

    async def newer_active(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="suite",
            state="active",
            trial_ends_at=None,
            occurred_at=_at(2),
            entitled_products=TRIO,
        )

    async def older_cancelled(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="suite",
            state="cancelled",
            trial_ends_at=None,
            occurred_at=_at(1),
            entitled_products=TRIO,
        )

    assert await _race(tx, org_id, newer_active, older_cancelled) is None
    assert await _entitled(tx, org_id) == set(TRIO), (
        "ordering bypass: an older cancellation committed last and locked the trio out"
    )
