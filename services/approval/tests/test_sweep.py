"""The SLA escalation sweep's own loop (`app/cron.py`, ADR-0006 §8): cross-tenant discovery
finding every organization's overdue work in one pass, the per-organization fairness cap holding
even when one tenant supplies far more than its share, two concurrent sweepers never both
escalating the same step, and the clock coming from the database rather than the process.
`tests/test_escalation.py` covers `escalate_step`'s own decision-making in detail; this module
covers the loop that discovers work and drives it.

**Every `due_at` seeded here is anchored to the database's own clock** (`app.services.db_now`),
never to `datetime.now(UTC)` — deliberately, and it is not a style nicety. A real, reproducible
gap was found while building this suite: the containerised Postgres clock lagging the host's wall
clock by several minutes after the host machine slept. A step seeded as "one minute overdue"
against the *host's* clock was, at that moment, not overdue at all against the *database's* —
which is exactly the class of skew ADR-0006 §8's "one clock, and it is the database's" amendment
exists to make irrelevant in production. `escalate_step`'s own re-check always reads the
database's clock (`app/services.py`'s `db_now`), so a test that seeds off a different clock is
testing an assumption production code deliberately never makes. Anchoring every seed to
`db_now(session)` makes each assertion true by construction, on any host, sleeping or not.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.cron import EscalationSweep
from app.models import ApprovalStep
from app.services import db_now
from platform_core.db import TENANT_GUC
from tests.helpers import seed_pending_request_with_step, seed_person

pytestmark = pytest.mark.integration


async def _seed_due_request(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    minutes_overdue: float,
) -> uuid.UUID:
    """A pending, assigned, open step whose `due_at` is `minutes_overdue` in the past **relative
    to the database's own clock**, read fresh inside this same transaction. See the module
    docstring for why that anchor, specifically, is load-bearing in this file."""
    requester, manager, director = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with session.begin():
        due_at = await db_now(session) - timedelta(minutes=minutes_overdue)
        await seed_person(session, org_id, director)
        await seed_person(session, org_id, manager, reporting_manager_id=director)
        await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request, _step = await seed_pending_request_with_step(
            session,
            org_id,
            requester_employee_id=requester,
            approver_employee_id=manager,
            opened_at=due_at - timedelta(minutes=15),
            due_at=due_at,
        )
    return request.id


@asynccontextmanager
async def _org_bound_session(organization_id: uuid.UUID) -> Any:
    """A second, independent connection bound to a different organization than the shared
    `session` fixture — real network I/O, matching `tests/test_decisions.py`'s own
    `_tenant_session` — used here to seed a second tenant's rows so a cross-tenant discovery test
    is exercised against two genuinely separate bindings rather than one session re-purposed.

    The GUC is bound via an `after_begin` listener, not a one-off `SELECT set_config(...)` before
    `yield` — `set_config(..., true)` is transaction-local (`platform_core.db`'s own load-bearing
    rule), so a `set_config` issued inside its own `async with s.begin(): ...` block is gone the
    moment that block commits, and every statement a caller runs afterwards opens a *new*
    transaction with nothing bound. The listener re-applies it at the start of every transaction
    this session ever opens, exactly as `tests/conftest.py`'s own `session` fixture does.
    """
    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": "approval,public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:

            @sa_event.listens_for(s.sync_session, "after_begin")
            def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(
                    f"SELECT set_config('{TENANT_GUC}', '{organization_id}', true)"
                )

            yield s
    finally:
        await engine.dispose()


class TestDiscoveryIsCrossTenant:
    async def test_the_sweep_acts_on_every_organizations_own_due_work(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        sweep: Any,
    ) -> None:
        """Discovery is cross-tenant, every write is not — the same seam
        `subscription/app/cron.py`'s dunning sweep documents: the bypass buys discovery, never
        authority. A bug that lost the per-tenant binding on the write side would fail the tenant
        policy here rather than escalate across the platform."""
        request_a = await _seed_due_request(session, org_id, minutes_overdue=5)

        async with _org_bound_session(other_org) as other_session:
            request_b = await _seed_due_request(other_session, other_org, minutes_overdue=5)

        report = await sweep.tick()  # database's own clock — both seeds are already in its past

        assert report.considered == 2
        assert report.escalated == 2

        async with session.begin():
            open_a = (
                await session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == org_id,
                        ApprovalStep.request_id == request_a,
                        ApprovalStep.outcome == "open",
                    )
                )
            ).scalar_one()
        assert open_a.level == 2

        # `request_b` lives in `other_org`, read back on its own binding — the point of this test
        # is that discovery crossed the tenant boundary and each *write* stayed inside its own, so
        # the escalation must actually be visible there, not merely counted in `report.escalated`.
        async with _org_bound_session(other_org) as other_session, other_session.begin():
            open_b = (
                await other_session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == other_org,
                        ApprovalStep.request_id == request_b,
                        ApprovalStep.outcome == "open",
                    )
                )
            ).scalar_one()
        assert open_b.level == 2


class TestFairnessCap:
    async def test_one_organization_cannot_starve_another(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID, sweep: Any
    ) -> None:
        """ADR-0006 §8's per-organization cap of 20 per tick, applied *inside* discovery
        (`app/selectors.py`'s `_DUE_ESCALATION_STEPS_SQL`) rather than over whatever a
        platform-wide slice happened to hand it — a tenant with far more overdue work than the cap
        must not prevent a smaller tenant's steps from being acted on in the same tick.

        Seeded well past the default `per_org_cap` (20), not merely near it: the slice-4 review
        finding this regresses (a platform-wide `ORDER BY due_at LIMIT 500` taken *before* any
        per-organization cap) only actually starves another tenant once the busy one supplies
        enough rows to dominate that global ordering — a fairness test that stops at, say, 21 seeded
        rows never crosses into the regime the bug lived in, and passes whether or not the fix is
        present. 30 is comfortably past the cap either way; the correctness claim below is exact,
        not a loose upper bound, which is what actually distinguishes "the cap holds" from "the
        sweep did nothing this tick" (both satisfy `escalated <= 21`)."""
        # `org_id`: 30 overdue steps — well over the default cap of 20.
        for i in range(30):
            await _seed_due_request(session, org_id, minutes_overdue=5 + i)

        # `other_org`: a single overdue step — the tenant this test proves is still served.
        async with _org_bound_session(other_org) as other_session:
            await _seed_due_request(other_session, other_org, minutes_overdue=5)

        report = await sweep.tick()

        # Exactly 21: 20 from `org_id` (the cap), 1 from `other_org` (its entire overdue backlog)
        # — not merely "no more than 21", which a sweep that escalated nothing at all would also
        # satisfy and which is exactly what let this test pass while the fairness cap was advisory.
        assert report.escalated == 21

        async with session.begin():
            escalated_in_org = (
                await session.execute(
                    select(func.count())
                    .select_from(ApprovalStep)
                    .where(
                        ApprovalStep.organization_id == org_id,
                        ApprovalStep.outcome == "escalated",
                    )
                )
            ).scalar_one()
            still_open_and_due = (
                (
                    await session.execute(
                        select(ApprovalStep).where(
                            ApprovalStep.organization_id == org_id,
                            ApprovalStep.outcome == "open",
                            ApprovalStep.due_at.is_not(None),
                            ApprovalStep.due_at <= (await db_now(session)),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert escalated_in_org == 20
        # 30 seeded, exactly 20 escalated this tick — the other 10 are still open and still due,
        # waiting for the next tick, not starved forever.
        assert len(still_open_and_due) == 10

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            escalated_in_other = (
                await other_session.execute(
                    select(func.count())
                    .select_from(ApprovalStep)
                    .where(
                        ApprovalStep.organization_id == other_org,
                        ApprovalStep.outcome == "escalated",
                    )
                )
            ).scalar_one()
        # The property the old, slice-then-cap query did not actually deliver: the small tenant is
        # served in the same tick as the busy one, not starved behind it.
        assert escalated_in_other == 1


class TestConcurrentSweepersNeverDoubleEscalate:
    async def test_two_sweepers_draining_the_same_backlog_agree_on_the_total(
        self, session: AsyncSession, org_id: uuid.UUID, sweep_engines: Any
    ) -> None:
        for i in range(5):
            await _seed_due_request(session, org_id, minutes_overdue=5 + i)

        discovery_engine, work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
        work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

        @asynccontextmanager
        async def work_scope(organization_id: uuid.UUID) -> Any:
            async with work_maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                    {"org": str(organization_id)},
                )
                yield s

        sweep_a = EscalationSweep(discovery_factory=discovery, work_scope=work_scope)
        sweep_b = EscalationSweep(discovery_factory=discovery, work_scope=work_scope)

        report_a, report_b = await asyncio.gather(sweep_a.tick(), sweep_b.tick())

        # `escalated + skipped` already fully accounts for both sweepers' ten combined attempts
        # (five requests, two racing sweepers each) — `== 5` is the whole of "no double escalation,
        # no missed one" for this backlog; a per-request re-query for `outcome = 'open'` afterwards
        # would only be re-proving what `uq_approval_steps_open_per_request` already guarantees can
        # never exceed one row, without covering anything this count does not.
        assert report_a.escalated + report_b.escalated == 5, (
            "two sweepers draining the same 5-row backlog escalated a different total than 5 — "
            "either a step was double-escalated or one was missed"
        )


class TestClockIsTheDatabases:
    async def test_tick_with_no_override_still_discovers_real_overdue_work(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        """The production path: no clock is threaded in from Python at all, and the sweep must
        still find a step seeded as overdue (module docstring: anchored to the database's own
        clock, so this holds regardless of any host/container clock skew)."""
        await _seed_due_request(session, org_id, minutes_overdue=1)

        report = await sweep.tick()  # no `now=` — reads the database's own clock

        assert report.considered >= 1
        assert report.escalated >= 1


class TestOneOrganizationsFailureDoesNotStopTheRest:
    async def test_a_failing_organization_is_counted_and_every_other_still_escalates(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        sweep: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`app/cron.py`'s `EscalationSweep._escalate` wraps `services.escalate_step` in its own
        `try`/`except` (slice-4 review finding 7). Discovery orders every tick's candidates by
        `due_at` **platform-wide** (`app/selectors.py`'s `_DUE_ESCALATION_STEPS_SQL`), so an
        organization whose step keeps raising is head-of-line on every tick — left unguarded, that
        exception would propagate out of `tick()` and abandon every candidate ordered after it, a
        permanent, platform-wide stall dressed up as a crash loop rather than one organization's
        own problem."""
        request_a = await _seed_due_request(session, org_id, minutes_overdue=10)
        async with _org_bound_session(other_org) as other_session:
            request_b = await _seed_due_request(other_session, other_org, minutes_overdue=5)

        real_escalate_step = services.escalate_step

        async def _raise_for_org_id(
            work_session: AsyncSession, *, organization_id: uuid.UUID, request_id: uuid.UUID
        ) -> Any:
            if organization_id == org_id:
                raise RuntimeError("simulated escalation failure")
            return await real_escalate_step(
                work_session, organization_id=organization_id, request_id=request_id
            )

        # `app/cron.py`'s `_escalate` imports `services` fresh (`from app import services`) at
        # call time and reads `services.escalate_step` off the module, so patching the module
        # attribute here reaches it — the same technique `tests/test_delegations.py` uses to force
        # an unrelated failure path.
        monkeypatch.setattr(services, "escalate_step", _raise_for_org_id)

        report = await sweep.tick()

        assert report.considered == 2
        assert report.failed == 1
        assert report.escalated == 1

        # The healthy organization's request still escalated — the poisoned one did not stall it.
        async with _org_bound_session(other_org) as other_session, other_session.begin():
            open_b = (
                await other_session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == other_org,
                        ApprovalStep.request_id == request_b,
                        ApprovalStep.outcome == "open",
                    )
                )
            ).scalar_one()
        assert open_b.level == 2

        # The failing organization's own step is untouched, not partially written: the raise
        # happened inside the tenant-bound transaction (`_work_scope`), which rolled back.
        async with session.begin():
            open_a = (
                await session.execute(
                    select(ApprovalStep).where(
                        ApprovalStep.organization_id == org_id,
                        ApprovalStep.request_id == request_a,
                        ApprovalStep.outcome == "open",
                    )
                )
            ).scalar_one()
        assert open_a.level == 1
