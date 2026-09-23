"""Reads for scheduler. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)`. A list endpoint that queries directly instead
of coming through a selector is how an organization leaks.

Two of the reads below are the exception the house pattern (`approval/app/selectors.py`,
`subscription/app/selectors.py`) always carves out for a cron discovery query: they run
cross-tenant, on the BYPASSRLS outbox role, because finding out *which* organizations have work
has no tenant to bind while asking (ADR-0015 §3). Both are served by a column-level grant
(`migrations/env.py:grant_sweep_reader`) rather than the outbox role's usual `SELECT, UPDATE` on
`outbox` alone, and both return bare ids — never `target_payload` or `target_actor` — so the
per-organization action (`app/cron.py`) re-fetches the row it actually acts on under the tenant-
bound app role, where row-level security applies and the lock is real.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScheduledJob

#: Every `pending` job whose `due_at` has arrived, ordered so the oldest overdue job fires first
#: platform-wide. No per-organization cap: unlike the SLA sweep's `approval_steps` (one row per
#: open step, potentially many per organization), a schedule request is one row per retry — the
#: volume that ever queues up for one organization is bounded by the backoff itself, so the
#: fairness problem the SLA sweep's `LATERAL` join solves does not arise here.
_DUE_JOBS_SQL = text(
    """
    SELECT organization_id, id
      FROM scheduler.scheduled_jobs
     WHERE status = 'pending' AND due_at <= :now
     ORDER BY due_at
     LIMIT :limit
    """
)


async def due_jobs(
    session: AsyncSession, *, now: datetime, limit: int = 200
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, id)` for every job the sweep should attempt to fire this tick.

    `now` is read once by the caller in the discovery transaction and passed in — one clock, and
    it is the database's (ADR-0006 §8's own amendment, followed here as everywhere else discovery
    and action are split). The per-job action re-reads its own, deliberately later `now()` when it
    re-checks the row under the lock.
    """
    rows = (await session.execute(_DUE_JOBS_SQL, {"now": now, "limit": limit})).mappings().all()
    return [(row["organization_id"], row["id"]) for row in rows]


async def get_job_for_firing(
    session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
) -> ScheduledJob | None:
    """The sweep's own lock: `SELECT ... FOR UPDATE SKIP LOCKED`, re-checked here rather than
    trusted from discovery's now-possibly-stale read. `None` covers both "another replica is
    already firing this row" and "it fired already, in the gap between discovery and here" — the
    caller treats either as a skip, never an error, which is what makes two concurrent drivers
    safe to run against the same row.
    """
    stmt = (
        select(ScheduledJob)
        .where(
            ScheduledJob.organization_id == organization_id,
            ScheduledJob.id == job_id,
            ScheduledJob.status == "pending",
        )
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


#: Retention's own discovery (ADR-0015 §7): which organizations have `fired` rows old enough to
#: purge. `DISTINCT` because the per-organization delete below removes a whole batch at once, not
#: one row per tick.
_STALE_FIRED_JOBS_SQL = text(
    """
    SELECT DISTINCT organization_id
      FROM scheduler.scheduled_jobs
     WHERE status = 'fired' AND fired_at < :cutoff
     LIMIT :limit
    """
)


async def orgs_with_stale_fired_jobs(
    session: AsyncSession, *, cutoff: datetime, limit: int = 200
) -> list[uuid.UUID]:
    rows = (
        (await session.execute(_STALE_FIRED_JOBS_SQL, {"cutoff": cutoff, "limit": limit}))
        .scalars()
        .all()
    )
    return list(rows)
