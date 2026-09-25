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

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompanyFact, DailySnapshot, DealFact, LeadFact, MetricCoverage, OrgProfile


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


# --------------------------------------------------------------------- deal_facts (I3)


async def upsert_deal_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    deal_id: uuid.UUID,
    name: str,
    stage: str,
    value_minor: int,
    currency: str,
    owner_ref: uuid.UUID,
    company_ref: uuid.UUID | None,
    close_date: date | None,
    probability: int,
    closed_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`crm.pipeline.deal.created`/`.updated`: both carry the deal's full state, so both are
    one version-guarded upsert (data-model.md §1.1's event rule). Returns whether the write was
    applied — `False` for a stale or replayed version, the intended outcome under at-least-once
    delivery (`app/consumers.py` logs it as a duplicate, not an error)."""
    stmt = pg_insert(DealFact).values(
        organization_id=organization_id,
        id=deal_id,
        name=name,
        stage=stage,
        value_minor=value_minor,
        currency=currency,
        owner_ref=owner_ref,
        company_ref=company_ref,
        close_date=close_date,
        probability=probability,
        closed_at=closed_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DealFact.organization_id, DealFact.id],
        set_={
            "name": stmt.excluded.name,
            "stage": stmt.excluded.stage,
            "value_minor": stmt.excluded.value_minor,
            "currency": stmt.excluded.currency,
            "owner_ref": stmt.excluded.owner_ref,
            "company_ref": stmt.excluded.company_ref,
            "close_date": stmt.excluded.close_date,
            "probability": stmt.excluded.probability,
            "closed_at": stmt.excluded.closed_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "deleted_at": None,
            "ingested_at": func.now(),
        },
        where=DealFact.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def apply_deal_stage_changed(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    deal_id: uuid.UUID,
    stage: str,
    name: str,
    value_minor: int,
    currency: str,
    owner_ref: uuid.UUID,
    probability: int,
    closed_at: datetime | None,
    source_updated_at: datetime,
) -> bool:
    """`crm.pipeline.deal.stage_changed` is a **partial** event — it carries no `company_id` or
    `close_date` (data-model.md §1.1) — so this only ever `UPDATE`s an existing row, guarded by
    the same version rule, and never inserts. `False` means either a stale version or an orphan
    transition (the row is not there because its `created` event was lost); the caller
    (`app/consumers.py`) tells the two apart with one extra read, only on this non-applied path,
    and logs `insights.fold.orphan_transition` for the second."""
    stmt = (
        update(DealFact)
        .where(DealFact.organization_id == organization_id, DealFact.id == deal_id)
        .where(DealFact.source_updated_at <= source_updated_at)
        .values(
            stage=stage,
            name=name,
            value_minor=value_minor,
            currency=currency,
            owner_ref=owner_ref,
            probability=probability,
            closed_at=closed_at,
            source_updated_at=source_updated_at,
            ingested_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def mark_deal_deleted(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    deal_id: uuid.UUID,
    source_updated_at: datetime,
) -> bool:
    """`crm.pipeline.deal.deleted` — a partial event, `UPDATE`-only like every delete
    (data-model.md §1.1)."""
    stmt = (
        update(DealFact)
        .where(DealFact.organization_id == organization_id, DealFact.id == deal_id)
        .where(DealFact.source_updated_at <= source_updated_at)
        .values(
            deleted_at=source_updated_at,
            source_updated_at=source_updated_at,
            ingested_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_deal_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    deal_id: uuid.UUID,
    name: str,
    stage: str,
    value_minor: int,
    currency: str,
    owner_ref: uuid.UUID,
    company_ref: uuid.UUID | None,
    close_date: date | None,
    probability: int,
    closed_at: datetime | None,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> bool:
    """An export page's own write rule (data-model.md §1.1): a point-in-time read overwrites
    the fact **unless an event folded it after this page was requested**
    (`ingested_at >= page_requested_at`). When the row yields to a fresher event, `last_seen_run`
    still records that this run saw it — a separate `UPDATE` below — so mark-and-sweep never
    mistakes "yielded to a newer event" for "gone at the source"."""
    stmt = pg_insert(DealFact).values(
        organization_id=organization_id,
        id=deal_id,
        name=name,
        stage=stage,
        value_minor=value_minor,
        currency=currency,
        owner_ref=owner_ref,
        company_ref=company_ref,
        close_date=close_date,
        probability=probability,
        closed_at=closed_at,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DealFact.organization_id, DealFact.id],
        set_={
            "name": stmt.excluded.name,
            "stage": stmt.excluded.stage,
            "value_minor": stmt.excluded.value_minor,
            "currency": stmt.excluded.currency,
            "owner_ref": stmt.excluded.owner_ref,
            "company_ref": stmt.excluded.company_ref,
            "close_date": stmt.excluded.close_date,
            "probability": stmt.excluded.probability,
            "closed_at": stmt.excluded.closed_at,
            "deleted_at": stmt.excluded.deleted_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=DealFact.ingested_at < page_requested_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    applied = bool(result.rowcount)
    if not applied:
        await session.execute(
            update(DealFact)
            .where(DealFact.organization_id == organization_id, DealFact.id == deal_id)
            .values(last_seen_run=run_id)
        )
    return applied


# --------------------------------------------------------------------- lead_facts (I4)


async def upsert_lead_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    status: str,
    source: str,
    owner_ref: uuid.UUID | None,
    converted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`crm.lead.created`/`.updated`: both carry the lead's full state (`docs/insights/
    events.md` §2), so both are one version-guarded upsert."""
    stmt = pg_insert(LeadFact).values(
        organization_id=organization_id,
        id=lead_id,
        status=status,
        source=source,
        owner_ref=owner_ref,
        converted_at=converted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[LeadFact.organization_id, LeadFact.id],
        set_={
            "status": stmt.excluded.status,
            "source": stmt.excluded.source,
            "owner_ref": stmt.excluded.owner_ref,
            "converted_at": stmt.excluded.converted_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "deleted_at": None,
            "ingested_at": func.now(),
        },
        where=LeadFact.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def apply_lead_converted(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    owner_ref: uuid.UUID | None,
    converted_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`crm.lead.converted` carries no `source` (`docs/insights/events.md` §2: "insights uses
    it for lead_facts.status/converted_at" only) — treated as a partial transition like
    `deal.stage_changed`: `UPDATE`-only, never insert, so a lost `created` event never leaves a
    fact with a guessed `source`."""
    stmt = (
        update(LeadFact)
        .where(LeadFact.organization_id == organization_id, LeadFact.id == lead_id)
        .where(LeadFact.source_updated_at <= source_updated_at)
        .values(
            status="converted",
            owner_ref=owner_ref,
            converted_at=converted_at,
            source_updated_at=source_updated_at,
            ingested_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_lead_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    lead_id: uuid.UUID,
    status: str,
    source: str,
    owner_ref: uuid.UUID | None,
    converted_at: datetime | None,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> bool:
    """The export page's own write rule (data-model.md §1.1) — see
    `upsert_deal_fact_from_export`'s docstring for the reasoning shared by every entity."""
    stmt = pg_insert(LeadFact).values(
        organization_id=organization_id,
        id=lead_id,
        status=status,
        source=source,
        owner_ref=owner_ref,
        converted_at=converted_at,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[LeadFact.organization_id, LeadFact.id],
        set_={
            "status": stmt.excluded.status,
            "source": stmt.excluded.source,
            "owner_ref": stmt.excluded.owner_ref,
            "converted_at": stmt.excluded.converted_at,
            "deleted_at": stmt.excluded.deleted_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=LeadFact.ingested_at < page_requested_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    applied = bool(result.rowcount)
    if not applied:
        await session.execute(
            update(LeadFact)
            .where(LeadFact.organization_id == organization_id, LeadFact.id == lead_id)
            .values(last_seen_run=run_id)
        )
    return applied


# --------------------------------------------------------------------- company_facts (I4)


async def upsert_company_fact_from_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    company_id: uuid.UUID,
    name: str,
    status: str,
    owner_ref: uuid.UUID,
    source_created_at: datetime,
    source_updated_at: datetime,
) -> bool:
    """`crm.book.company.created`/`.updated`: both carry the company's full state, so both are
    one version-guarded upsert."""
    stmt = pg_insert(CompanyFact).values(
        organization_id=organization_id,
        id=company_id,
        name=name,
        status=status,
        owner_ref=owner_ref,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyFact.organization_id, CompanyFact.id],
        set_={
            "name": stmt.excluded.name,
            "status": stmt.excluded.status,
            "owner_ref": stmt.excluded.owner_ref,
            "source_updated_at": stmt.excluded.source_updated_at,
            "deleted_at": None,
            "ingested_at": func.now(),
        },
        where=CompanyFact.source_updated_at <= stmt.excluded.source_updated_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def mark_company_deleted(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    company_id: uuid.UUID,
    source_updated_at: datetime,
) -> bool:
    """`crm.book.company.deleted` — `UPDATE`-only, like every delete (data-model.md §1.1)."""
    stmt = (
        update(CompanyFact)
        .where(CompanyFact.organization_id == organization_id, CompanyFact.id == company_id)
        .where(CompanyFact.source_updated_at <= source_updated_at)
        .values(
            deleted_at=source_updated_at,
            source_updated_at=source_updated_at,
            ingested_at=func.now(),
        )
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount)


async def upsert_company_fact_from_export(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    company_id: uuid.UUID,
    name: str,
    status: str,
    owner_ref: uuid.UUID,
    deleted_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    run_id: uuid.UUID,
    page_requested_at: datetime,
) -> bool:
    """The export page's own write rule (data-model.md §1.1) — see
    `upsert_deal_fact_from_export`'s docstring for the reasoning shared by every entity."""
    stmt = pg_insert(CompanyFact).values(
        organization_id=organization_id,
        id=company_id,
        name=name,
        status=status,
        owner_ref=owner_ref,
        deleted_at=deleted_at,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        last_seen_run=run_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyFact.organization_id, CompanyFact.id],
        set_={
            "name": stmt.excluded.name,
            "status": stmt.excluded.status,
            "owner_ref": stmt.excluded.owner_ref,
            "deleted_at": stmt.excluded.deleted_at,
            "source_updated_at": stmt.excluded.source_updated_at,
            "last_seen_run": stmt.excluded.last_seen_run,
            "ingested_at": func.now(),
        },
        where=CompanyFact.ingested_at < page_requested_at,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    applied = bool(result.rowcount)
    if not applied:
        await session.execute(
            update(CompanyFact)
            .where(CompanyFact.organization_id == organization_id, CompanyFact.id == company_id)
            .values(last_seen_run=run_id)
        )
    return applied


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
