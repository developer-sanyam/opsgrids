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

* **D — the gate read.** The owner's hand activation arrives while a cancellation's fold is in
  flight. The hand-activation gate reads the activation's sources after taking the activation
  lock, so it waits for the fold to commit and then sees its provenance: refused, not undoing a
  cancellation that had not yet been committed when the request began.
* **E — the other order.** The hand activation commits first, and the fold that was waiting on
  the same lock overwrites the row: the subscription wins, whichever got there first. **For a
  standalone subscription only**, whose source is the very row the hand activation wrote.
* **F — the other order, for a bundle member.** A suite writes a different row (`suite`) for the
  member, so the hand activation's own row is never overwritten and outlives the suite's cancel.
  That is not a win for the subscription, and it is pinned here as the known leak it is (ADR-0037
  (e)); the RUNBOOK's audit query 2 finds it, since no live subscription source keeps the member on.

Each race test holds the first transaction open, starts the second, proves the second is *waiting*
rather than racing, then commits the first and checks where the rows end up (F is sequential: its
order is the whole point). These are the reviewer's reproductions, kept as regressions.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, ActivationSource, OutboxEvent
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


async def test_d_a_hand_activation_waiting_on_a_cancel_fold_is_refused(
    tx: Any, org_id: uuid.UUID
) -> None:
    """The gate reads the activation's sources *after* the activation lock. The cancellation's fold
    holds that lock and has written provenance nobody can see yet; the owner's request waits for
    it, then reads the committed row and is refused — a read taken before the lock would have found
    nothing, and switched the product back on under a cancellation."""

    async def cancel(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="hrms",
            state="cancelled",
            trial_ends_at=None,
            occurred_at=_at(0),
            subscription_id=uuid.uuid4(),
        )

    async def owner_activates(s: AsyncSession) -> None:
        await services.activate(s, _ctx(org_id), product_key="hrms", trial_days=365)

    raised = await _race(tx, org_id, cancel, owner_activates)
    assert isinstance(raised, services.SubscriptionManagedConflict), (
        f"the hand activation should have seen the cancellation's provenance; got {raised!r}"
    )
    assert "hrms" not in await _entitled(tx, org_id), "the owner undid a cancellation"
    async with tx(org_id) as s:
        outbox = (await s.execute(select(func.count()).select_from(OutboxEvent))).scalar_one()
        (source,) = await selectors.sources_for(s, org_id, "hrms")
        status, trial_ends_at = source.status, source.trial_ends_at
    assert outbox == 0, "a refused activation staged an event"
    assert status == "suspended" and trial_ends_at is None


async def test_e_a_fold_waiting_on_a_hand_activation_overwrites_it(
    tx: Any, org_id: uuid.UUID
) -> None:
    """The other order: nothing manages the product yet, so the hand activation is allowed and
    commits. The cancellation's fold, which was waiting for the same lock, then upserts the very
    same row to `suspended` and stamps its provenance — the subscription wins either way."""

    async def owner_activates(s: AsyncSession) -> None:
        await services.activate(s, _ctx(org_id), product_key="hrms")

    async def cancel(s: AsyncSession) -> None:
        await services.project_subscription(
            s,
            organization_id=org_id,
            product_key="hrms",
            state="cancelled",
            trial_ends_at=None,
            occurred_at=_at(0),
            subscription_id=uuid.uuid4(),
        )

    assert await _race(tx, org_id, owner_activates, cancel) is None
    assert "hrms" not in await _entitled(tx, org_id), "the hand activation outlived the cancel"
    async with tx(org_id) as s:
        (source,) = (
            (
                await s.execute(
                    select(ActivationSource).where(ActivationSource.product_key == "hrms")
                )
            )
            .scalars()
            .all()
        )
        managed, status = source.is_subscription_managed, source.status
    assert managed and status == "suspended"


async def test_f_a_bundle_members_hand_activation_before_the_suite_folds_outlives_the_cancel(
    tx: Any, org_id: uuid.UUID
) -> None:
    """E's order for a bundle member is **not** a subscription win, and this pins what happens.

    Before any event of the suite has folded, nothing manages `hrms`, so the owner's hand
    activation (a year of trial) is allowed and writes the member's own row, `(hrms, hrms)`. The
    suite then folds into a *different* row, `(hrms, suite)`: nothing overwrites the hand row, so
    when the suite is cancelled `hrms` stays entitled through it. `crm` and `finance`, which only
    the suite fed, go off. The gate does close from here on (the suite row carries provenance), but
    it cannot undo what was allowed before there was any.

    This is a known leak, documented in ADR-0037 and found after the fact by the RUNBOOK's audit
    (query 2). Closing it would mean the fold retiring a hand row, a policy change on legitimate
    gifts, which this fix deliberately does not make. If that is ever decided, this test is the one
    to change.
    """
    suite_id = uuid.uuid4()
    async with tx(org_id) as s:
        await services.activate(s, _ctx(org_id), product_key="hrms", trial_days=365)
        await s.commit()
    for state, minute in (("active", 0), ("cancelled", 1)):
        await _fold(
            tx,
            org_id,
            product_key="suite",
            state=state,
            occurred_at=_at(minute),
            entitled_products=TRIO,
            subscription_id=suite_id,
        )

    assert await _entitled(tx, org_id) == {"hrms"}, "the hand trial is the only thing left on"
    async with tx(org_id) as s:
        rows = (
            (
                await s.execute(
                    select(ActivationSource)
                    .where(ActivationSource.product_key == "hrms")
                    .order_by(ActivationSource.source_product_key)
                )
            )
            .scalars()
            .all()
        )
        seen = [(r.source_product_key, r.status, r.is_subscription_managed) for r in rows]
    assert seen == [("hrms", "trial", False), ("suite", "suspended", True)]

    async with tx(org_id) as s:
        with pytest.raises(services.SubscriptionManagedConflict):
            await services.activate(s, _ctx(org_id), product_key="hrms", trial_days=365)
