"""Writes for insights (`docs/insights/data-model.md` §1.1, §2, §5).

Every write here is one version-guarded upsert, never a plain `INSERT`/`UPDATE`: the event
consumer (`app/consumers.py`), the backfill runner (`app/backfill.py`) and the snapshot sweep
(`app/snapshot.py`) all call these, each inside its own transaction, already bound to the
organization's tenant GUC (`session_scope`/`bulkhead_scope`). Nothing here opens or commits a
transaction, matching the service anatomy every other service follows.

I1 has one fact-shaped table, `org_profile` — every `*_facts` table I2+ adds gets its own
version-guarded upsert here, following `upsert_org_profile`'s shape.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySnapshot, MetricCoverage, OrgProfile


async def upsert_org_profile(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    timezone: str,
    currency: str,
    fiscal_year_start_month: int,
    source_updated_at: datetime,
) -> bool:
    """Apply one organization's settings, if and only if `source_updated_at` is not older than
    what is stored (data-model.md §1.1: `apply iff incoming.source_updated_at >=
    stored.source_updated_at`). `core.org.created`/`core.org.updated` (`app/consumers.py`) and
    the account export page (`app/backfill.py`) both call this — an event's version is its
    envelope `occurred_at`; the export's is the source row's own `updated_at`. Equal is a
    legitimate replay of the same version, which rewrites identical values rather than being
    dropped, so an event and a re-export of the same state always agree.

    Returns whether the row was written (created, or updated because its version passed the
    gate) — `False` for a stale or malformed-order write, which is the intended, non-error
    outcome under at-least-once delivery.
    """
    stmt = pg_insert(OrgProfile).values(
        organization_id=organization_id,
        timezone=timezone,
        currency=currency,
        fiscal_year_start_month=fiscal_year_start_month,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[OrgProfile.organization_id],
        set_={
            "timezone": stmt.excluded.timezone,
            "currency": stmt.excluded.currency,
            "fiscal_year_start_month": stmt.excluded.fiscal_year_start_month,
            "source_updated_at": stmt.excluded.source_updated_at,
            "ingested_at": func.now(),
        },
        # See `services/approval/app/services.py:upsert_hierarchy_nodes_bulk` for why rowcount,
        # not RETURNING, is what tells a dropped-as-stale row apart from "nothing to do": a
        # conflicting row whose WHERE fails is not counted by PostgreSQL's own command tag.
        where=OrgProfile.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_daily_snapshot(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    metric: str,
    dimension: str,
    day: date,
    value: int,
    currency: str | None,
    captured_at: datetime,
) -> None:
    """One hourly capture (`docs/insights/metrics.md` §8, data-model.md §5): "the last capture
    of a day is the day's value." Unconditional `DO UPDATE`, never version-guarded — unlike a
    fact, a snapshot has no source row to be older or newer than; it *is* the observation, and
    `app/snapshot.py`'s own sweep is the only caller, which only ever calls this for the
    organization's **current local day**. A closed day's row is never touched again because the
    sweep never asks to touch it, not because this function refuses to — see that module's own
    docstring for the guard that makes that true.
    """
    stmt = pg_insert(DailySnapshot).values(
        organization_id=organization_id,
        metric=metric,
        dimension=dimension,
        day=day,
        value=value,
        currency=currency,
        captured_at=captured_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            DailySnapshot.organization_id,
            DailySnapshot.metric,
            DailySnapshot.dimension,
            DailySnapshot.day,
        ],
        set_={
            "value": stmt.excluded.value,
            "currency": stmt.excluded.currency,
            "captured_at": stmt.excluded.captured_at,
        },
    )
    await session.execute(stmt)


async def record_metric_coverage(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    metric: str,
    history_from: date | None,
    basis: str,
) -> None:
    """Recomputed after every completed backfill run for the entity a metric reads, and by the
    sweep when a snapshot metric's first capture lands (`docs/insights/data-model.md` §5)."""
    stmt = pg_insert(MetricCoverage).values(
        organization_id=organization_id,
        metric=metric,
        history_from=history_from,
        basis=basis,
        computed_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MetricCoverage.organization_id, MetricCoverage.metric],
        set_={
            "history_from": stmt.excluded.history_from,
            "basis": stmt.excluded.basis,
            "computed_at": func.now(),
        },
    )
    await session.execute(stmt)
