"""Writes for scheduler.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction:
a route with a principal takes its session from `get_tenant_session`, which opens the
request's one transaction and binds it to the caller's organization first (ADR-0002 §1),
so the state change, its audit entry and any event it emits all commit or all roll back
together.

Three writes (ADR-0015):

* **`ingest_schedule_request`** — the consumer's own write, turning one
  `platform.schedule.requested` event into (at most) one `scheduled_jobs` row.
* **`fire_job`** — the sweep's own write, turning one due row into an outbox event addressed
  at its target and marking the row fired. Never called outside a lock already held by the
  caller (`app/selectors.py:get_job_for_firing`).
* **`purge_stale_fired_jobs`** — the retention pass, deleting one organization's own `fired`
  rows past `SCHEDULED_JOB_RETENTION`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import STATUS_FIRED, OutboxEvent, ScheduledJob
from platform_core.events import publish_event
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("scheduler.services")

#: How long a fired row is kept before the retention pass deletes it (ADR-0015 §7). A `pending`
#: row is never touched by retention regardless of age — only a job's own audit trail ages out
#: once it has done its job.
SCHEDULED_JOB_RETENTION = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    """The parsed, validated shape of one `platform.schedule.requested` payload.

    Parsed at the boundary (`app/consumers.py`) before this module — the domain layer — ever
    sees it, per CLAUDE.md: "validate at the boundary." A payload that fails to parse is logged
    and dropped there, never raised into this function, because a malformed payload does not
    become processable by redelivering it.
    """

    idempotency_key: str
    due_at: datetime
    requested_event_id: uuid.UUID
    correlation_id: str | None
    target_event_type: str
    target_aggregate_type: str
    target_aggregate_id: uuid.UUID
    target_payload: dict[str, Any]
    target_actor: dict[str, Any] | None


async def ingest_schedule_request(
    session: AsyncSession, organization_id: uuid.UUID, request: ScheduleRequest
) -> None:
    """Turn one schedule request into (at most) one row.

    `ON CONFLICT (organization_id, idempotency_key) DO NOTHING` is the domain-level idempotency
    guard: `already_processed`/`mark_processed` on the *event* id (`app/consumers.py`) already
    covers a redelivered message, but two distinct events can legitimately name the same key —
    finance's own early-arrival defence deliberately does this — and both must still produce
    exactly one row.
    """
    stmt = (
        pg_insert(ScheduledJob)
        .values(
            organization_id=organization_id,
            idempotency_key=request.idempotency_key,
            due_at=request.due_at,
            status="pending",
            target_event_type=request.target_event_type,
            target_aggregate_type=request.target_aggregate_type,
            target_aggregate_id=request.target_aggregate_id,
            target_payload=request.target_payload,
            target_actor=request.target_actor,
            requested_event_id=request.requested_event_id,
            correlation_id=request.correlation_id,
        )
        .on_conflict_do_nothing(constraint="uq_scheduled_jobs_org_idempotency")
    )
    await session.execute(stmt)


@dataclass(frozen=True, slots=True)
class FireResult:
    fired: bool


async def fire_job(
    session: AsyncSession, *, organization_id: uuid.UUID, job_id: uuid.UUID
) -> FireResult:
    """Publish one due job's target event and mark it fired — inside the caller's lock.

    The caller (`app/cron.py`) must already hold `SELECT ... FOR UPDATE SKIP LOCKED` on this row
    (`app/selectors.py:get_job_for_firing`) before calling this; a `None` from that selector is a
    skip the caller handles itself and never reaches here. Publishing and marking the row fired
    share this one transaction, so a crash between the two cannot produce an event with no record
    of having sent it, or a `fired` row with no event behind it.
    """
    job = await selectors.get_job_for_firing(session, organization_id, job_id)
    if job is None:
        return FireResult(fired=False)

    # Carries the original request's correlation id across the wait — otherwise `publish_event`
    # would stamp whatever this process's own context var happens to hold (usually nothing, since
    # nothing set it for a background sweep), losing the chain ADR-0000 §7 exists to keep.
    token = correlation_id_ctx.set(job.correlation_id)
    try:
        fired_event_id = publish_event(
            session,
            OutboxEvent,
            organization_id=job.organization_id,
            event_type=job.target_event_type,
            aggregate_type=job.target_aggregate_type,
            aggregate_id=job.target_aggregate_id,
            payload=job.target_payload,
            causation_id=job.requested_event_id,
            actor=job.target_actor,
        )
    finally:
        correlation_id_ctx.reset(token)

    job.status = STATUS_FIRED
    job.fired_at = datetime.now(UTC)
    job.fired_event_id = fired_event_id
    log.info(
        "scheduler.job.fired",
        organization_id=str(organization_id),
        job_id=str(job_id),
        target_event_type=job.target_event_type,
        fired_event_id=str(fired_event_id),
    )
    return FireResult(fired=True)


async def purge_stale_fired_jobs(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    cutoff: datetime,
    batch_size: int = 500,
) -> int:
    """Delete one organization's own `fired` rows older than `cutoff`. Never touches `pending`.

    A plain `DELETE ... LIMIT` needs a subselect in Postgres (no `DELETE ... LIMIT` clause), so
    this deletes by id from a bounded discovery read rather than in one statement — the same
    "discover, then act on the ids" shape as firing, just without a lock, because nothing else
    ever mutates a `fired` row.
    """
    ids = (
        (
            await session.execute(
                select(ScheduledJob.id)
                .where(
                    ScheduledJob.organization_id == organization_id,
                    ScheduledJob.status == STATUS_FIRED,
                    ScheduledJob.fired_at < cutoff,
                )
                .limit(batch_size)
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0
    await session.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(ids)))
    return len(ids)
