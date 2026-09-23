"""The due-job sweep (ADR-0015): a job waits for its own `due_at`, never fires twice under two
concurrent drivers, and its retention pass only ever touches rows that have already fired.

Same shape as `services/approval/tests/test_sweep.py`'s `TestConcurrentSweepersNeverDoubleEscalate`
— real PostgreSQL, real locks, two independent engines racing the same row.

Every assertion below reads its plain Python values (never a lazy ORM attribute) out from
*inside* the `async with session.begin()` block it was queried in, and always after an explicit
`session.expire(...)` when the row was mutated by a different session (`app/cron.py`'s
`work_scope` sessions never are `session`/`admin_session` themselves). Two independent reasons:
`expire_on_commit=False` on these fixtures means a session's identity map does not know a
different session changed a row it already holds, and accessing an expired attribute *outside*
an active transaction raises `MissingGreenlet` — async SQLAlchemy's lazy-reload needs the
greenlet context an open transaction provides.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cron import Sweep
from app.models import OutboxEvent, ScheduledJob
from platform_core.db import TENANT_GUC

pytestmark = pytest.mark.integration


async def _seed_job(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    due_at: datetime,
    idempotency_key: str | None = None,
    target_event_type: str = "finance.compliance.invoice.filing_requested",
) -> ScheduledJob:
    job = ScheduledJob(
        organization_id=org_id,
        idempotency_key=idempotency_key or f"job-{uuid.uuid4()}",
        due_at=due_at,
        status="pending",
        target_event_type=target_event_type,
        target_aggregate_type="invoice",
        target_aggregate_id=uuid.uuid4(),
        target_payload={"attempt": 2, "trigger": "retry"},
        requested_event_id=uuid.uuid4(),
        correlation_id="corr-1",
    )
    session.add(job)
    await session.flush()
    return job


class TestFutureJobsAreNotFiredEarly:
    async def test_a_job_due_in_the_future_is_left_pending(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            job = await _seed_job(session, org_id, due_at=datetime.now(UTC) + timedelta(hours=1))
            job_id = job.id

        report = await sweep.tick()

        assert report.fired == 0
        async with session.begin():
            refreshed = await session.get(ScheduledJob, job_id)
            assert refreshed is not None
            status, fired_at = refreshed.status, refreshed.fired_at
        assert status == "pending"
        assert fired_at is None

    async def test_a_due_job_fires(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            job = await _seed_job(session, org_id, due_at=datetime.now(UTC) - timedelta(minutes=1))
            job_id, target_aggregate_id, requested_event_id = (
                job.id,
                job.target_aggregate_id,
                job.requested_event_id,
            )
            target_event_type, target_payload = job.target_event_type, job.target_payload

        report = await sweep.tick()

        assert report.fired == 1
        async with session.begin():
            # The sweep fired this row on a *different* session (its own `work_scope`); this
            # session's identity map still holds the pre-fire object unless told otherwise.
            session.expire(job)
            refreshed = await session.get(ScheduledJob, job_id)
            assert refreshed is not None
            status, fired_at = refreshed.status, refreshed.fired_at
            outbox_rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.aggregate_id == target_aggregate_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(outbox_rows) == 1
            outbox_row = outbox_rows[0]
            event_type, event_payload, causation_id = (
                outbox_row.event_type,
                outbox_row.payload,
                outbox_row.causation_id,
            )
        assert status == "fired"
        assert fired_at is not None
        assert event_type == target_event_type
        assert event_payload == target_payload
        assert str(causation_id) == str(requested_event_id)


class TestConcurrentDriversNeverDoubleFire:
    async def test_two_drivers_racing_the_same_due_job_fire_it_exactly_once(
        self, session: AsyncSession, org_id: uuid.UUID, sweep_engines: tuple[Any, Any]
    ) -> None:
        async with session.begin():
            job = await _seed_job(session, org_id, due_at=datetime.now(UTC) - timedelta(minutes=1))
            job_id, target_aggregate_id = job.id, job.target_aggregate_id

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

        sweep_a = Sweep(discovery_factory=discovery, work_scope=work_scope)
        sweep_b = Sweep(discovery_factory=discovery, work_scope=work_scope)

        report_a, report_b = await asyncio.gather(sweep_a.tick(), sweep_b.tick())

        assert report_a.fired + report_b.fired == 1, (
            "two sweepers racing one due job fired a different total than 1 — either it fired "
            "twice or neither driver fired it"
        )
        async with session.begin():
            # One of the two drivers fired this on its own `work_scope` session, not this one.
            session.expire(job)
            refreshed = await session.get(ScheduledJob, job_id)
            assert refreshed is not None
            status = refreshed.status
            outbox_rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.aggregate_id == target_aggregate_id)
                    )
                )
                .scalars()
                .all()
            )
        assert status == "fired"
        assert len(outbox_rows) == 1


class TestRetention:
    async def test_a_fired_row_past_retention_is_purged(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        from app import services

        async with session.begin():
            job = await _seed_job(session, org_id, due_at=datetime.now(UTC) - timedelta(days=30))
            job.status = "fired"
            job.fired_at = datetime.now(UTC) - services.SCHEDULED_JOB_RETENTION - timedelta(days=1)
            job_id = job.id

        report = await sweep.tick()

        assert report.purged == 1
        async with session.begin():
            # The sweep deleted this row on its own `work_scope` session.
            session.expire(job)
            refreshed = await session.get(ScheduledJob, job_id)
        assert refreshed is None

    async def test_a_recently_fired_row_is_kept(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            job = await _seed_job(session, org_id, due_at=datetime.now(UTC) - timedelta(hours=1))
            job.status = "fired"
            job.fired_at = datetime.now(UTC) - timedelta(hours=1)
            job_id = job.id

        report = await sweep.tick()

        assert report.purged == 0
        async with session.begin():
            session.expire(job)
            refreshed = await session.get(ScheduledJob, job_id)
        assert refreshed is not None


class TestTenantIsolation:
    async def test_a_sweep_never_fires_another_organizations_job_into_the_wrong_scope(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        admin_session: AsyncSession,
        sweep: Any,
    ) -> None:
        """The row this seeds belongs to `other_org`, written directly on the bypass role — the
        sweep must still fire it (discovery is cross-tenant by design) but the row that lands
        stays `other_org`'s, never `org_id`'s, because the firing transaction binds the tenant
        the row itself names, not whichever organization a test happens to be looking through."""
        async with admin_session.begin():
            job = await _seed_job(
                admin_session, other_org, due_at=datetime.now(UTC) - timedelta(minutes=1)
            )
            job_id = job.id

        report = await sweep.tick()
        assert report.fired == 1

        async with session.begin():
            # `session` is bound to `org_id`; row-level security must show it nothing.
            visible = (
                (await session.execute(select(ScheduledJob).where(ScheduledJob.id == job_id)))
                .scalars()
                .all()
            )
        assert visible == []

        async with admin_session.begin():
            # The sweep fired this on its own `work_scope` session, not `admin_session`.
            admin_session.expire(job)
            refreshed = await admin_session.get(ScheduledJob, job_id)
            assert refreshed is not None
            organization_id, status = refreshed.organization_id, refreshed.status
        assert organization_id == other_org
        assert status == "fired"
