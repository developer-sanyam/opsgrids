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
from typing import Any, Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DailySnapshot,
    EnquiryFacts,
    EnquiryLineFacts,
    MetricCoverage,
    OrgProfile,
    PurchaseOrderFacts,
    TaskFacts,
)
from platform_core.logging import get_logger

log = get_logger("insights.services")

#: `apply_fact_upsert`'s outcome (data-model.md §1.1). `"applied"` covers both a first insert
#: and a newer-version overwrite — the caller does not need to tell them apart. `"stale"` is the
#: intended, non-error outcome under at-least-once delivery and the export/event commute rule.
FactUpsertResult = Literal["applied", "stale"]
#: A partial transition's outcome (`*.deleted`, `purchase_order.{closed,cancelled}`,
#: `enquiry.status_changed`, data-model.md §1.1): `"orphan"` is a transition for a fact whose
#: creating event never arrived (or has not yet) — logged, acked, and repaired by the next event
#: or the weekly re-export, never an error.
PartialApplyResult = Literal["applied", "stale", "orphan"]


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


# --------------------------------------------------------------------- entity facts (I5-I7)


async def apply_fact_upsert(
    session: AsyncSession,
    model: type[Any],
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    values: dict[str, Any],
    source_created_at: datetime,
    source_updated_at: datetime,
    deleted_at: datetime | None = None,
    from_export: bool = False,
    page_requested_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> FactUpsertResult:
    """The one version-guarded full-state upsert every `*_facts` table's creating/full-state
    fold and export page share (data-model.md §1.1), parameterised by `model` and its
    entity-specific `values` rather than repeated once per entity.

    **From an event** (`from_export=False`): applies iff `source_updated_at` is not older than
    what is stored — `upsert_org_profile`'s own rule, generalised.

    **From an export page** (`from_export=True`): a point-in-time read, so the guard compares
    `ingested_at` against `page_requested_at` instead (an event folded after the page was
    requested may be newer, and yields to it). A declined row still records `last_seen_run` in a
    separate statement, so mark-and-sweep does not mistake "yielded to a newer event" for "gone
    at the source" (data-model.md §1.1).
    """
    insert_values: dict[str, Any] = {
        "organization_id": organization_id,
        "id": id,
        "source_created_at": source_created_at,
        "source_updated_at": source_updated_at,
        "deleted_at": deleted_at,
        **values,
    }
    if from_export:
        insert_values["last_seen_run"] = run_id

    stmt = pg_insert(model).values(**insert_values)
    set_: dict[str, Any] = {column: getattr(stmt.excluded, column) for column in values}
    set_["source_updated_at"] = stmt.excluded.source_updated_at
    set_["deleted_at"] = stmt.excluded.deleted_at
    set_["ingested_at"] = func.now()

    if from_export:
        set_["last_seen_run"] = stmt.excluded.last_seen_run
        where = model.ingested_at < page_requested_at
    else:
        where = model.source_updated_at <= stmt.excluded.source_updated_at

    stmt = stmt.on_conflict_do_update(
        index_elements=[model.organization_id, model.id], set_=set_, where=where
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    applied = bool(result.rowcount)

    if from_export and not applied:
        # The page yielded to a fresher event; still mark that this run saw the row (see the
        # docstring above) so a later sweep does not treat it as gone at the source.
        await session.execute(
            update(model)
            .where(model.organization_id == organization_id, model.id == id)
            .values(last_seen_run=run_id)
        )
    return "applied" if applied else "stale"


async def apply_fact_partial(
    session: AsyncSession,
    model: type[Any],
    *,
    organization_id: uuid.UUID,
    id: uuid.UUID,
    values: dict[str, Any],
    source_updated_at: datetime,
    orphan_event_type: str,
) -> PartialApplyResult:
    """A partial transition (`*.deleted`, `purchase_order.{closed,cancelled}`,
    `enquiry.status_changed`): `UPDATE ... WHERE source_updated_at <= :version`, **never
    insert** — a partial event cannot create a fact whose other columns would have to be
    guessed (data-model.md §1.1). Logs `insights.fold.orphan_transition` and returns `"orphan"`
    when the fact is not there at all — the creating event was lost or has not arrived yet — and
    lets the caller ack anyway: the weekly re-export repairs it.
    """
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(model)
            .where(
                model.organization_id == organization_id,
                model.id == id,
                model.source_updated_at <= source_updated_at,
            )
            .values(**values, source_updated_at=source_updated_at, ingested_at=func.now())
        ),
    )
    if result.rowcount:
        return "applied"

    exists = (
        await session.execute(
            select(model.id).where(model.organization_id == organization_id, model.id == id)
        )
    ).first()
    if exists is None:
        log.warning(
            "insights.fold.orphan_transition",
            organization_id=str(organization_id),
            entity_id=str(id),
            event_type=orphan_event_type,
        )
        return "orphan"
    return "stale"


async def upsert_purchase_order_fact(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    purchase_order_id: uuid.UUID,
    number: str,
    status: str,
    supplier_ref: uuid.UUID,
    supplier_name: str,
    owner_ref: uuid.UUID | None,
    created_date: date,
    expected_date: date,
    received_date: date | None,
    currency: str,
    total_minor: int,
    source_created_at: datetime,
    source_updated_at: datetime,
    deleted_at: datetime | None = None,
    from_export: bool = False,
    page_requested_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> FactUpsertResult:
    """`purchase_order.raised`/`.updated` and the export page (I5) — full state, so this is
    always the full-upsert shape, never the partial one. `deleted_at` is only ever non-`None`
    from an export page (data-model.md §3: "soft-deleted rows included, so the deletion
    propagates") — the events this folds from are never staged for an already-deleted PO."""
    return await apply_fact_upsert(
        session,
        PurchaseOrderFacts,
        organization_id=organization_id,
        id=purchase_order_id,
        values={
            "number": number,
            "status": status,
            "supplier_ref": supplier_ref,
            "supplier_name": supplier_name,
            "owner_ref": owner_ref,
            "created_date": created_date,
            "expected_date": expected_date,
            "received_date": received_date,
            "currency": currency,
            "total_minor": total_minor,
        },
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        deleted_at=deleted_at,
        from_export=from_export,
        page_requested_at=page_requested_at,
        run_id=run_id,
    )


async def apply_purchase_order_partial(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    purchase_order_id: uuid.UUID,
    values: dict[str, Any],
    source_updated_at: datetime,
    event_type: str,
) -> PartialApplyResult:
    """`purchase_order.{approved,received,closed,cancelled,deleted}` (I5) — a status/date
    transition on an already-created PO, never a full state."""
    return await apply_fact_partial(
        session,
        PurchaseOrderFacts,
        organization_id=organization_id,
        id=purchase_order_id,
        values=values,
        source_updated_at=source_updated_at,
        orphan_event_type=event_type,
    )


async def upsert_task_fact(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    project_ref: uuid.UUID,
    title: str,
    stage: str,
    priority: str,
    points: int,
    due_date: date | None,
    owner_ref: uuid.UUID | None,
    completed_at: datetime | None,
    source_created_at: datetime,
    source_updated_at: datetime,
    deleted_at: datetime | None = None,
    from_export: bool = False,
    page_requested_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> FactUpsertResult:
    """`task.created`/`.updated` and the export page (I6) — full state. `deleted_at` is only
    ever non-`None` from an export page, the same as `upsert_purchase_order_fact`."""
    return await apply_fact_upsert(
        session,
        TaskFacts,
        organization_id=organization_id,
        id=task_id,
        values={
            "project_ref": project_ref,
            "title": title,
            "stage": stage,
            "priority": priority,
            "points": points,
            "due_date": due_date,
            "owner_ref": owner_ref,
            "completed_at": completed_at,
        },
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        deleted_at=deleted_at,
        from_export=from_export,
        page_requested_at=page_requested_at,
        run_id=run_id,
    )


async def apply_task_deleted(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    deleted_at: datetime,
    source_updated_at: datetime,
) -> PartialApplyResult:
    """`task.deleted` (I6) — a partial transition; never inserts."""
    return await apply_fact_partial(
        session,
        TaskFacts,
        organization_id=organization_id,
        id=task_id,
        values={"deleted_at": deleted_at},
        source_updated_at=source_updated_at,
        orphan_event_type="projects.board.task.deleted",
    )


async def upsert_enquiry_fact(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    store_ref: uuid.UUID,
    company: str,
    status: str,
    lines: list[dict[str, Any]],
    source_created_at: datetime,
    source_updated_at: datetime,
    deleted_at: datetime | None = None,
    from_export: bool = False,
    page_requested_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> FactUpsertResult:
    """`enquiry.created` and the export page (I7) — full state, including its lines. Lines are
    written once with the enquiry (append-only at the source, data-model.md §3): whenever this
    upsert actually applies, the line set is replaced wholesale with the given one, which is
    idempotent for a replay of the same event or a re-export of the same, unchanged enquiry.
    """
    result = await apply_fact_upsert(
        session,
        EnquiryFacts,
        organization_id=organization_id,
        id=enquiry_id,
        values={
            "store_ref": store_ref,
            "company": company,
            "status": status,
            "line_count": len(lines),
        },
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        deleted_at=deleted_at,
        from_export=from_export,
        page_requested_at=page_requested_at,
        run_id=run_id,
    )
    if result == "applied":
        await session.execute(
            delete(EnquiryLineFacts).where(
                EnquiryLineFacts.organization_id == organization_id,
                EnquiryLineFacts.enquiry_id == enquiry_id,
            )
        )
        for line_no, line in enumerate(lines):
            await session.execute(
                pg_insert(EnquiryLineFacts).values(
                    organization_id=organization_id,
                    enquiry_id=enquiry_id,
                    line_no=line_no,
                    product_ref=uuid.UUID(str(line["product_id"])),
                    product_name=str(line["name"]),
                    quantity=int(line["quantity"]),
                )
            )
    return result


async def apply_enquiry_status_changed(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    status: str,
    source_updated_at: datetime,
) -> PartialApplyResult:
    """`enquiry.status_changed` (I7) — a partial transition; never inserts."""
    return await apply_fact_partial(
        session,
        EnquiryFacts,
        organization_id=organization_id,
        id=enquiry_id,
        values={"status": status},
        source_updated_at=source_updated_at,
        orphan_event_type="storefront.enquiry.status_changed",
    )
