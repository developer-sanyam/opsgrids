"""Reads for lots, rolls, stock buckets, the org-wide ledger and the genealogy traces (inventory
Phase 1, I3/I5). Every query filters on `organization_id` explicitly (RLS is the net, not the
filter — CLAUDE.md) and every list statement passes through `ctx.apply()` before pagination.

Stock is shared organization data, so `apply()` has no narrower reading to offer here: the routes
floor these permissions at ORG, and `apply()` is still called on every statement so that a future
change to that floor fails closed (TEAM/LOCATION raise, OWN matches nothing a lot or ledger row
can be `created_by`) instead of quietly returning the whole organization.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import BigInteger, Select, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.inventory.tracking_schemas import (
    LedgerRowOut,
    LotGenealogyOut,
    LotOut,
    RollEventOut,
    RollOut,
    StockRowOut,
    TraceNodeOut,
)
from app.models import (
    OFF_SITE_STATUSES,
    SCHEMA,
    InventoryBalance,
    InventoryItem,
    InventoryMove,
    LedgerEntry,
    Location,
    Lot,
    LotGenealogy,
    Roll,
    RollEvent,
    Warehouse,
)
from platform_core.auth import ScopeContext

#: A trace never walks further than this many hops. Real genealogies are a handful deep; the cap
#: is what makes a (prevented, but never assumed absent) cycle a bounded query, not a runaway.
MAX_TRACE_DEPTH = 25


def _on_hand(model: type[Lot] | type[Roll], column: Any) -> Any:
    """Physical stock of a lot/roll across every bucket — a correlated scalar subquery, so a list
    page costs one query, not one per row."""
    return (
        select(cast(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0), BigInteger))
        .where(
            InventoryBalance.organization_id == model.organization_id,
            column == model.id,
            InventoryBalance.status.notin_(OFF_SITE_STATUSES),
        )
        .correlate(model)
        .scalar_subquery()
    )


# ------------------------------------------------------------------ lots


def lot_rows_stmt(org_id: uuid.UUID) -> Select[Any]:
    """`(Lot, sku, uom, on_hand_milli)` for every lot in the organization."""
    return (
        select(Lot, InventoryItem.sku, InventoryItem.uom, _on_hand(Lot, InventoryBalance.lot_id))
        .join(InventoryItem, InventoryItem.id == Lot.inventory_item_id)
        .where(Lot.organization_id == org_id, InventoryItem.organization_id == org_id)
    )


def lots_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID | None = None,
    quality_status: str | None = None,
    q: str | None = None,
    expires_before: date | None = None,
) -> Select[Any]:
    stmt = ctx.apply(lot_rows_stmt(ctx.organization_id), owner_col=Lot.created_by)
    if item_id is not None:
        stmt = stmt.where(Lot.inventory_item_id == item_id)
    if quality_status is not None:
        stmt = stmt.where(Lot.quality_status == quality_status)
    if q:
        needle = f"{q.lower()}%"
        stmt = stmt.where(
            func.lower(Lot.lot_no).like(needle) | func.lower(Lot.supplier_lot_no).like(needle)
        )
    if expires_before is not None:
        stmt = stmt.where(Lot.expires_on.is_not(None), Lot.expires_on < expires_before)
    return stmt


async def get_lot_row(
    session: AsyncSession, org_id: uuid.UUID, lot_id: uuid.UUID
) -> tuple[Lot, str, str, int] | None:
    stmt = lot_rows_stmt(org_id).where(Lot.id == lot_id)
    row = (await session.execute(stmt.execution_options(populate_existing=True))).first()
    return (row[0], row[1], row[2], int(row[3])) if row else None


async def get_lot(
    session: AsyncSession, org_id: uuid.UUID, lot_id: uuid.UUID, *, for_update: bool = False
) -> Lot | None:
    stmt = select(Lot).where(Lot.organization_id == org_id, Lot.id == lot_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_lot_by_no(session: AsyncSession, org_id: uuid.UUID, lot_no: str) -> Lot | None:
    stmt = select(Lot).where(Lot.organization_id == org_id, Lot.lot_no == lot_no)
    return (await session.execute(stmt)).scalar_one_or_none()


def lot_out(row: tuple[Lot, str, str, int] | Any) -> LotOut:
    lot, sku, uom, on_hand = row[0], row[1], row[2], int(row[3])
    return LotOut(
        id=str(lot.id),
        lot_no=lot.lot_no,
        supplier_lot_no=lot.supplier_lot_no,
        inventory_item_id=str(lot.inventory_item_id),
        sku=sku,
        uom=uom,
        manufactured_on=lot.manufactured_on,
        received_on=lot.received_on,
        expires_on=lot.expires_on,
        quality_status=lot.quality_status,
        source_type=lot.source_type,
        source_id=str(lot.source_id) if lot.source_id else None,
        attributes=dict(lot.attributes or {}),
        on_hand_qty=format_qty(on_hand),
        created_at=lot.created_at,
        updated_at=lot.updated_at,
    )


def lot_genealogy_stmt(ctx: ScopeContext, lot_id: uuid.UUID) -> Select[Any]:
    """Every genealogy edge touching a lot, either side, newest first."""
    stmt = select(LotGenealogy).where(
        LotGenealogy.organization_id == ctx.organization_id,
        (LotGenealogy.parent_lot_id == lot_id) | (LotGenealogy.child_lot_id == lot_id),
    )
    return ctx.apply(stmt, owner_col=LotGenealogy.created_by)


def genealogy_out(edge: LotGenealogy) -> LotGenealogyOut:
    return LotGenealogyOut(
        id=str(edge.id),
        parent_lot_id=str(edge.parent_lot_id),
        child_lot_id=str(edge.child_lot_id),
        relation=edge.relation,
        move_id=str(edge.move_id) if edge.move_id else None,
        note=edge.note,
        created_at=edge.created_at,
    )


# ------------------------------------------------------------------ rolls


def roll_rows_stmt(org_id: uuid.UUID) -> Select[Any]:
    """`(Roll, sku, uom, lot_no, on_hand_milli)` for every roll in the organization."""
    return (
        select(
            Roll,
            InventoryItem.sku,
            InventoryItem.uom,
            Lot.lot_no,
            _on_hand(Roll, InventoryBalance.roll_id),
        )
        .join(InventoryItem, InventoryItem.id == Roll.inventory_item_id)
        .outerjoin(Lot, (Lot.organization_id == Roll.organization_id) & (Lot.id == Roll.lot_id))
        .where(Roll.organization_id == org_id, InventoryItem.organization_id == org_id)
    )


def rolls_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    state: str | None = None,
    shade: str | None = None,
    grade: str | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = ctx.apply(roll_rows_stmt(ctx.organization_id), owner_col=Roll.created_by)
    if item_id is not None:
        stmt = stmt.where(Roll.inventory_item_id == item_id)
    if lot_id is not None:
        stmt = stmt.where(Roll.lot_id == lot_id)
    if state is not None:
        stmt = stmt.where(Roll.state == state)
    if shade is not None:
        stmt = stmt.where(Roll.shade == shade)
    if grade is not None:
        stmt = stmt.where(Roll.grade == grade)
    if q:
        stmt = stmt.where(func.lower(Roll.roll_no).like(f"{q.lower()}%"))
    return stmt


async def get_roll_row(
    session: AsyncSession, org_id: uuid.UUID, roll_id: uuid.UUID
) -> tuple[Roll, str, str, str | None, int] | None:
    stmt = roll_rows_stmt(org_id).where(Roll.id == roll_id)
    row = (await session.execute(stmt.execution_options(populate_existing=True))).first()
    return (row[0], row[1], row[2], row[3], int(row[4])) if row else None


async def roll_rows_by_ids(
    session: AsyncSession, org_id: uuid.UUID, roll_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Any]:
    """Several rolls' output rows in one query, keyed by id."""
    if not roll_ids:
        return {}
    stmt = roll_rows_stmt(org_id).where(Roll.id.in_(roll_ids))
    rows = (await session.execute(stmt.execution_options(populate_existing=True))).all()
    return {row[0].id: row for row in rows}


async def lock_rolls(
    session: AsyncSession, org_id: uuid.UUID, roll_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Roll]:
    """The rolls, locked `FOR UPDATE` in id order — every roll operation takes its roll locks in
    this one order (before any balance lock the kernel then takes), so two operations over the
    same rolls queue instead of deadlocking, and a second split of one roll waits for the first to
    commit and then sees it `SPLIT`. `populate_existing` for the same reason `lock_bucket` uses
    it: a row already in the identity map must come back with the values the lock waited for."""
    if not roll_ids:
        return {}
    stmt = (
        select(Roll)
        .where(Roll.organization_id == org_id, Roll.id.in_(set(roll_ids)))
        .order_by(Roll.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {roll.id: roll for roll in (await session.execute(stmt)).scalars().all()}


async def get_roll_by_no(session: AsyncSession, org_id: uuid.UUID, roll_no: str) -> Roll | None:
    stmt = select(Roll).where(Roll.organization_id == org_id, Roll.roll_no == roll_no)
    return (await session.execute(stmt)).scalar_one_or_none()


async def existing_roll_nos(
    session: AsyncSession, org_id: uuid.UUID, roll_nos: Sequence[str]
) -> set[str]:
    stmt = select(Roll.roll_no).where(Roll.organization_id == org_id, Roll.roll_no.in_(roll_nos))
    return set((await session.execute(stmt)).scalars().all())


async def bucket_balance(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    status: str,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> int:
    """A bucket's current quantity (0 if it has no row). Plain read — callers that act on it hold
    the roll lock, and the kernel re-checks under the balance lock."""
    stmt = select(InventoryBalance.quantity_milli).where(
        InventoryBalance.organization_id == org_id,
        InventoryBalance.inventory_item_id == item_id,
        InventoryBalance.location_id == location_id,
        InventoryBalance.status == status,
        InventoryBalance.lot_id.is_not_distinct_from(lot_id),
        InventoryBalance.roll_id.is_not_distinct_from(roll_id),
    )
    return int((await session.execute(stmt)).scalar_one_or_none() or 0)


async def lock_roll_balances(
    session: AsyncSession, org_id: uuid.UUID, roll_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[InventoryBalance]]:
    """Every balance row of the given rolls (every location and status, zero rows included),
    locked `FOR UPDATE` in the kernel's bucket order — call it *after* `lock_rolls`, so the lock
    order stays roll -> balance. This is what makes "the roll lives in one bucket" a check that
    cannot go stale: nobody can move stock into or out of any of these buckets until we commit."""
    found: dict[uuid.UUID, list[InventoryBalance]] = {roll_id: [] for roll_id in roll_ids}
    if not roll_ids:
        return found
    stmt = (
        select(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.roll_id.in_(set(roll_ids)),
        )
        .order_by(
            InventoryBalance.inventory_item_id,
            InventoryBalance.location_id,
            InventoryBalance.status,
            InventoryBalance.lot_id,
            InventoryBalance.roll_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for balance in (await session.execute(stmt)).scalars().all():
        assert balance.roll_id is not None
        found[balance.roll_id].append(balance)
    return found


def roll_out(row: Any) -> RollOut:
    roll, sku, uom, lot_no, on_hand = row[0], row[1], row[2], row[3], int(row[4])
    return RollOut(
        id=str(roll.id),
        roll_no=roll.roll_no,
        inventory_item_id=str(roll.inventory_item_id),
        sku=sku,
        uom=uom,
        lot_id=str(roll.lot_id) if roll.lot_id else None,
        lot_no=lot_no,
        state=roll.state,
        primary_qty=format_qty(roll.primary_milli),
        secondary_qty=format_qty(roll.secondary_milli)
        if roll.secondary_milli is not None
        else None,
        secondary_uom=roll.secondary_uom,
        width_mm=roll.width_mm,
        gsm=roll.gsm,
        shade=roll.shade,
        grade=roll.grade,
        parent_roll_id=str(roll.parent_roll_id) if roll.parent_roll_id else None,
        on_hand_qty=format_qty(on_hand),
        created_at=roll.created_at,
        updated_at=roll.updated_at,
    )


def roll_events_stmt(ctx: ScopeContext, roll_id: uuid.UUID) -> Select[Any]:
    stmt = select(RollEvent).where(
        RollEvent.organization_id == ctx.organization_id, RollEvent.roll_id == roll_id
    )
    return ctx.apply(stmt, owner_col=RollEvent.created_by)


def roll_event_out(event: RollEvent) -> RollEventOut:
    return RollEventOut(
        id=str(event.id),
        kind=event.kind,
        related_roll_id=str(event.related_roll_id) if event.related_roll_id else None,
        move_id=str(event.move_id) if event.move_id else None,
        quantity=format_qty(event.quantity_milli) if event.quantity_milli is not None else None,
        secondary_qty=(
            format_qty(event.secondary_milli) if event.secondary_milli is not None else None
        ),
        detail=dict(event.detail or {}),
        occurred_at=event.occurred_at,
    )


# ------------------------------------------------------------------ stock buckets and the ledger


def stock_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    status: str | None = None,
    include_empty: bool = False,
) -> Select[Any]:
    """Stock by bucket: item x location x status x lot x roll, with the names a screen needs.
    Empty buckets (nothing on hand, nothing reserved) are left out unless asked for."""
    stmt = (
        select(
            InventoryBalance,
            InventoryItem.sku,
            InventoryItem.name,
            InventoryItem.uom,
            Warehouse.id,
            Warehouse.code,
            Location.code,
            Lot.lot_no,
            Roll.roll_no,
        )
        .join(InventoryItem, InventoryItem.id == InventoryBalance.inventory_item_id)
        .join(Location, Location.id == InventoryBalance.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .outerjoin(
            Lot,
            (Lot.organization_id == InventoryBalance.organization_id)
            & (Lot.id == InventoryBalance.lot_id),
        )
        .outerjoin(
            Roll,
            (Roll.organization_id == InventoryBalance.organization_id)
            & (Roll.id == InventoryBalance.roll_id),
        )
        .where(
            InventoryBalance.organization_id == ctx.organization_id,
            InventoryItem.organization_id == ctx.organization_id,
            Location.organization_id == ctx.organization_id,
        )
    )
    stmt = ctx.apply(stmt, owner_col=InventoryItem.created_by)
    if not include_empty:
        stmt = stmt.where(
            (InventoryBalance.quantity_milli != 0) | (InventoryBalance.reserved_milli != 0)
        )
    if item_id is not None:
        stmt = stmt.where(InventoryBalance.inventory_item_id == item_id)
    if location_id is not None:
        stmt = stmt.where(InventoryBalance.location_id == location_id)
    if warehouse_id is not None:
        stmt = stmt.where(Location.warehouse_id == warehouse_id)
    if lot_id is not None:
        stmt = stmt.where(InventoryBalance.lot_id == lot_id)
    if roll_id is not None:
        stmt = stmt.where(InventoryBalance.roll_id == roll_id)
    if status is not None:
        stmt = stmt.where(InventoryBalance.status == status)
    return stmt


def stock_row_out(row: Any) -> StockRowOut:
    balance: InventoryBalance = row[0]
    return StockRowOut(
        id=str(balance.id),
        inventory_item_id=str(balance.inventory_item_id),
        sku=row[1],
        item_name=row[2],
        uom=row[3],
        warehouse_id=str(row[4]),
        warehouse_code=row[5],
        location_id=str(balance.location_id),
        location_code=row[6],
        status=balance.status,
        lot_id=str(balance.lot_id) if balance.lot_id else None,
        lot_no=row[7],
        roll_id=str(balance.roll_id) if balance.roll_id else None,
        roll_no=row[8],
        on_hand_qty=format_qty(balance.quantity_milli),
        reserved_qty=format_qty(balance.reserved_milli),
        available_qty=format_qty(balance.quantity_milli - balance.reserved_milli),
    )


def ledger_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    kind: str | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> Select[Any]:
    """The organization-wide ledger: one row per bucket delta with its move header, newest first
    once paginated. `from_date`/`to_date` bound the move's business date (`occurred_on`),
    inclusive."""
    stmt = (
        select(LedgerEntry, InventoryMove, InventoryItem.sku, InventoryItem.uom, Location.code)
        .join(InventoryMove, InventoryMove.id == LedgerEntry.move_id)
        .join(InventoryItem, InventoryItem.id == LedgerEntry.inventory_item_id)
        .join(Location, Location.id == LedgerEntry.location_id)
        .where(
            LedgerEntry.organization_id == ctx.organization_id,
            InventoryMove.organization_id == ctx.organization_id,
            InventoryItem.organization_id == ctx.organization_id,
            Location.organization_id == ctx.organization_id,
        )
    )
    stmt = ctx.apply(stmt, owner_col=LedgerEntry.created_by)
    if item_id is not None:
        stmt = stmt.where(LedgerEntry.inventory_item_id == item_id)
    if location_id is not None:
        stmt = stmt.where(LedgerEntry.location_id == location_id)
    if lot_id is not None:
        stmt = stmt.where(LedgerEntry.lot_id == lot_id)
    if roll_id is not None:
        stmt = stmt.where(LedgerEntry.roll_id == roll_id)
    if kind is not None:
        stmt = stmt.where(InventoryMove.kind == kind)
    if status is not None:
        stmt = stmt.where(LedgerEntry.status == status)
    if from_date is not None:
        stmt = stmt.where(InventoryMove.occurred_on >= from_date)
    if to_date is not None:
        stmt = stmt.where(InventoryMove.occurred_on <= to_date)
    return stmt


def ledger_row_out(row: Any) -> LedgerRowOut:
    entry: LedgerEntry = row[0]
    move: InventoryMove = row[1]
    return LedgerRowOut(
        id=str(entry.id),
        move_id=str(move.id),
        kind=move.kind,
        reason=move.reason,
        reference_type=move.reference_type,
        reference_id=str(move.reference_id) if move.reference_id else None,
        note=move.note,
        occurred_on=move.occurred_on,
        inventory_item_id=str(entry.inventory_item_id),
        sku=row[2],
        uom=row[3],
        location_id=str(entry.location_id),
        location_code=row[4],
        status=entry.status,
        lot_id=str(entry.lot_id) if entry.lot_id else None,
        roll_id=str(entry.roll_id) if entry.roll_id else None,
        quantity=format_qty(entry.quantity_milli),
        balance_after=format_qty(entry.balance_after_milli),
        created_at=entry.created_at,
        created_by=entry.created_by,
    )


# ------------------------------------------------------------------ traces (recursive CTEs)

#: Lot graph walk. `:forward` picks the direction by swapping which end of an edge we stand on.
#: Self-edges (a roll split/merge inside one lot) are evidence, not ancestry, and are skipped.
#: `UNION` (not `UNION ALL`) plus the depth cap keeps a cycle from running away; creating one is
#: refused at write time (`lots.link_lots`), this is the belt to that brace.
_LOT_TRACE_SQL = text(
    f"""
    WITH RECURSIVE walk AS (
        SELECT CASE WHEN :forward THEN g.child_lot_id ELSE g.parent_lot_id END AS node_id,
               CASE WHEN :forward THEN g.parent_lot_id ELSE g.child_lot_id END AS from_id,
               1 AS depth, g.relation, g.move_id
          FROM {SCHEMA}.lot_genealogy g
         WHERE g.organization_id = :org
           AND g.parent_lot_id <> g.child_lot_id
           AND (CASE WHEN :forward THEN g.parent_lot_id ELSE g.child_lot_id END) = :root
        UNION
        SELECT CASE WHEN :forward THEN g.child_lot_id ELSE g.parent_lot_id END,
               CASE WHEN :forward THEN g.parent_lot_id ELSE g.child_lot_id END,
               w.depth + 1, g.relation, g.move_id
          FROM {SCHEMA}.lot_genealogy g
          JOIN walk w
            ON (CASE WHEN :forward THEN g.parent_lot_id ELSE g.child_lot_id END) = w.node_id
         WHERE g.organization_id = :org
           AND g.parent_lot_id <> g.child_lot_id
           AND w.depth < :max_depth
    ),
    nearest AS (
        SELECT DISTINCT ON (node_id) node_id, from_id, depth, relation, move_id
          FROM walk
         ORDER BY node_id, depth, from_id
    )
    SELECT n.node_id, l.lot_no, n.depth, n.from_id, n.relation, n.move_id
      FROM nearest n
      JOIN {SCHEMA}.lots l ON l.id = n.node_id AND l.organization_id = :org
     WHERE n.node_id <> :root
     ORDER BY n.depth, l.lot_no
    """  # noqa: S608 — only the SCHEMA constant is interpolated; values are bound
)

#: Roll graph walk. An edge is a split (`rolls.parent_roll_id` -> child) or a merge (each source
#: -> the result, from `roll_events.MERGE_RESULT`). Same direction trick, same cap.
_ROLL_TRACE_SQL = text(
    f"""
    WITH RECURSIVE edges AS (
        SELECT r.parent_roll_id AS parent_id, r.id AS child_id, 'SPLIT'::text AS relation,
               NULL::uuid AS move_id
          FROM {SCHEMA}.rolls r
         WHERE r.organization_id = :org AND r.parent_roll_id IS NOT NULL
        UNION ALL
        SELECT e.related_roll_id, e.roll_id, 'MERGE'::text, e.move_id
          FROM {SCHEMA}.roll_events e
         WHERE e.organization_id = :org AND e.kind = 'MERGE_RESULT'
           AND e.related_roll_id IS NOT NULL
    ),
    walk AS (
        SELECT CASE WHEN :forward THEN e.child_id ELSE e.parent_id END AS node_id,
               CASE WHEN :forward THEN e.parent_id ELSE e.child_id END AS from_id,
               1 AS depth, e.relation, e.move_id
          FROM edges e
         WHERE (CASE WHEN :forward THEN e.parent_id ELSE e.child_id END) = :root
        UNION
        SELECT CASE WHEN :forward THEN e.child_id ELSE e.parent_id END,
               CASE WHEN :forward THEN e.parent_id ELSE e.child_id END,
               w.depth + 1, e.relation, e.move_id
          FROM edges e
          JOIN walk w ON (CASE WHEN :forward THEN e.parent_id ELSE e.child_id END) = w.node_id
         WHERE w.depth < :max_depth
    ),
    nearest AS (
        SELECT DISTINCT ON (node_id) node_id, from_id, depth, relation, move_id
          FROM walk
         ORDER BY node_id, depth, from_id
    )
    SELECT n.node_id, r.roll_no, n.depth, n.from_id, n.relation, n.move_id, r.lot_id, l.lot_no
      FROM nearest n
      JOIN {SCHEMA}.rolls r ON r.id = n.node_id AND r.organization_id = :org
      LEFT JOIN {SCHEMA}.lots l ON l.id = r.lot_id AND l.organization_id = :org
     WHERE n.node_id <> :root
     ORDER BY n.depth, r.roll_no
    """  # noqa: S608 — only the SCHEMA constant is interpolated; values are bound
)


async def trace_lots(
    session: AsyncSession, org_id: uuid.UUID, lot_id: uuid.UUID, *, forward: bool
) -> list[TraceNodeOut]:
    """Every lot upstream (`forward=False`: where this lot's material came from) or downstream
    (`forward=True`: what was made of it), nearest first. A recursive CTE, filtered on the
    organization at every step."""
    rows = (
        await session.execute(
            _LOT_TRACE_SQL,
            {"org": org_id, "root": lot_id, "forward": forward, "max_depth": MAX_TRACE_DEPTH},
        )
    ).all()
    return [
        TraceNodeOut(
            id=str(r.node_id),
            number=r.lot_no,
            depth=r.depth,
            from_id=str(r.from_id),
            relation=r.relation,
            move_id=str(r.move_id) if r.move_id else None,
        )
        for r in rows
    ]


#: Is `:target` reachable from `:start` following parent -> child edges? Uncapped, unlike the
#: display trace: the write-time loop check must see the whole graph, or a chain longer than the
#: display cap could be closed into a loop. `UNION` (not `UNION ALL`) de-duplicates nodes, so the
#: walk visits each lot once and terminates even over data that already contains a cycle.
_LOT_REACHES_SQL = text(
    f"""
    WITH RECURSIVE reach(node_id) AS (
        SELECT g.child_lot_id
          FROM {SCHEMA}.lot_genealogy g
         WHERE g.organization_id = :org
           AND g.parent_lot_id = :start
           AND g.parent_lot_id <> g.child_lot_id
        UNION
        SELECT g.child_lot_id
          FROM {SCHEMA}.lot_genealogy g
          JOIN reach r ON g.parent_lot_id = r.node_id
         WHERE g.organization_id = :org
           AND g.parent_lot_id <> g.child_lot_id
    )
    SELECT EXISTS (SELECT 1 FROM reach WHERE node_id = :target)
    """  # noqa: S608 — only the SCHEMA constant is interpolated; values are bound
)


async def lot_reaches(
    session: AsyncSession, org_id: uuid.UUID, start: uuid.UUID, target: uuid.UUID
) -> bool:
    """Whether `target` is `start` or descends from it through `lot_genealogy`, with no depth cap
    (write-time loop check — see `_LOT_REACHES_SQL`)."""
    if start == target:
        return True
    result = await session.execute(
        _LOT_REACHES_SQL, {"org": org_id, "start": start, "target": target}
    )
    return bool(result.scalar_one())


async def trace_rolls(
    session: AsyncSession, org_id: uuid.UUID, roll_id: uuid.UUID, *, forward: bool
) -> list[TraceNodeOut]:
    """The roll equivalent of `trace_lots`: parents/sources up, children/results down."""
    rows = (
        await session.execute(
            _ROLL_TRACE_SQL,
            {"org": org_id, "root": roll_id, "forward": forward, "max_depth": MAX_TRACE_DEPTH},
        )
    ).all()
    return [
        TraceNodeOut(
            id=str(r.node_id),
            number=r.roll_no,
            depth=r.depth,
            from_id=str(r.from_id),
            relation=r.relation,
            move_id=str(r.move_id) if r.move_id else None,
            lot_id=str(r.lot_id) if r.lot_id else None,
            lot_no=r.lot_no,
        )
        for r in rows
    ]
