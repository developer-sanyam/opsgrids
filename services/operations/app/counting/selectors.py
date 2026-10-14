"""Reads for cycle counting (ADR-0026, `docs/fulfilment/plan.md` F8). Every query filters on
`organization_id` explicitly; RLS is the net, not the filter (CLAUDE.md).

Session and line counts on `CountSessionOut` (`line_count`/`counted_count`/`pending_review_count`)
are computed here with `func.count`, never stored on the row — the same "derived, not cached"
preference `InventoryItem.on_hand_milli` sets for stock figures, and there is no concurrent-write
volume on one session's lines that would make a live aggregate expensive.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import CountLine, CountSession
from app.schemas import CountLineOut, CountSessionOut

# ------------------------------------------------------------------ sessions


def count_sessions_stmt(
    org_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    session_status: str | None = None,
) -> Select[Any]:
    stmt = select(CountSession).where(
        CountSession.organization_id == org_id, CountSession.deleted_at.is_(None)
    )
    if warehouse_id is not None:
        stmt = stmt.where(CountSession.warehouse_id == warehouse_id)
    if session_status is not None:
        stmt = stmt.where(CountSession.status == session_status)
    return stmt.order_by(CountSession.created_at.desc())


async def get_session(
    session: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID, *, for_update: bool = False
) -> CountSession | None:
    stmt = select(CountSession).where(
        CountSession.id == session_id,
        CountSession.organization_id == org_id,
        CountSession.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_live_session_for_warehouse(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> CountSession | None:
    """The warehouse's one `ACTIVE`/`REVIEW` session, if any (ADR-0026 §5). Callers that are about
    to open a new one call this only *after* locking the warehouse row
    (`app.counting.services.lock_warehouse`) — the lock is what makes the check race-free; this
    query alone is just a read."""
    stmt = select(CountSession).where(
        CountSession.organization_id == org_id,
        CountSession.warehouse_id == warehouse_id,
        CountSession.status.in_(("ACTIVE", "REVIEW")),
        CountSession.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def session_counters(
    session: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[int, int, int]:
    """`(line_count, counted_count, pending_review_count)` for one session."""
    stmt = select(
        func.count(),
        func.count(CountLine.counted_at),
        func.count().filter(CountLine.review_status == "PENDING"),
    ).where(CountLine.organization_id == org_id, CountLine.session_id == session_id)
    row = (await session.execute(stmt)).one()
    return int(row[0]), int(row[1]), int(row[2])


def session_out(
    row: CountSession, *, line_count: int, counted_count: int, pending_review_count: int
) -> CountSessionOut:
    return CountSessionOut(
        id=str(row.id),
        warehouse_id=str(row.warehouse_id),
        scope_kind=row.scope_kind,
        zone_location_id=str(row.zone_location_id) if row.zone_location_id else None,
        location_ids=row.location_ids,
        status=row.status,
        label=row.label,
        line_count=line_count,
        counted_count=counted_count,
        pending_review_count=pending_review_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ------------------------------------------------------------------ lines


def count_lines_stmt(
    org_id: uuid.UUID, session_id: uuid.UUID, *, review_status: str | None = None
) -> Select[Any]:
    stmt = select(CountLine).where(
        CountLine.organization_id == org_id, CountLine.session_id == session_id
    )
    if review_status is not None:
        stmt = stmt.where(CountLine.review_status == review_status)
    return stmt.order_by(CountLine.created_at.asc())


async def get_line(
    session: AsyncSession, org_id: uuid.UUID, line_id: uuid.UUID, *, for_update: bool = False
) -> CountLine | None:
    stmt = select(CountLine).where(CountLine.id == line_id, CountLine.organization_id == org_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_line_by_bucket(
    session: AsyncSession,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    inventory_item_id: uuid.UUID,
    location_id: uuid.UUID,
    status: str,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> CountLine | None:
    stmt = select(CountLine).where(
        CountLine.organization_id == org_id,
        CountLine.session_id == session_id,
        CountLine.inventory_item_id == inventory_item_id,
        CountLine.location_id == location_id,
        CountLine.status == status,
        CountLine.lot_id.is_(lot_id) if lot_id is None else CountLine.lot_id == lot_id,
        CountLine.roll_id.is_(roll_id) if roll_id is None else CountLine.roll_id == roll_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def skus_for_items(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    from app.models import InventoryItem  # local: avoids a module-level cycle with app.selectors

    if not item_ids:
        return {}
    stmt = select(InventoryItem.id, InventoryItem.sku).where(
        InventoryItem.organization_id == org_id, InventoryItem.id.in_(item_ids)
    )
    return {row.id: row.sku for row in (await session.execute(stmt)).all()}


def line_out(row: CountLine, *, sku: str = "") -> CountLineOut:
    return CountLineOut(
        id=str(row.id),
        session_id=str(row.session_id),
        inventory_item_id=str(row.inventory_item_id),
        sku=sku,
        location_id=str(row.location_id),
        status=row.status,
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        is_unlisted=row.is_unlisted,
        expected=format_qty(row.expected_milli),
        counted=format_qty(row.counted_milli) if row.counted_milli is not None else None,
        count_method=row.count_method,
        scanned_barcode=row.scanned_barcode,
        counted_by=row.counted_by,
        counted_at=row.counted_at,
        review_status=row.review_status,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
        posted_move_id=str(row.posted_move_id) if row.posted_move_id else None,
        created_at=row.created_at,
    )
