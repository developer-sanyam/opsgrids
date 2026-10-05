"""Reads for storage put-away and find-stock: the suggestion's candidate bins, a put-away's
capture, and "where is this item" joined to the *published* floor layout.

Every query filters on `organization_id` explicitly (RLS is the net, not the filter — CLAUDE.md).
Nothing here returns a coordinate: `get_capture` hands back the ORM row only to the reveal service,
and `detail_out` never copies the fix across.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import BigInteger, Select, and_, cast, exists, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import domain
from app.inventory.putaway_schemas import (
    LocatePlacementOut,
    LocateRowOut,
    PutawayDetailOut,
    PutawayFromOut,
    PutawayOut,
    PutawaySuggestionOut,
    PutawayToOut,
)
from app.inventory.quantity import format_qty
from app.models import (
    OFF_SITE_STATUSES,
    InventoryBalance,
    InventoryItem,
    LayoutVersion,
    Location,
    LocationPlacement,
    Lot,
    PutawayCapture,
    Roll,
    Warehouse,
    WarehouseFloor,
    Worker,
)
from platform_core.auth import ScopeContext

#: How many bins one `locate` returns before it says `truncated`. Far above how widely one item
#: spreads in a real warehouse; it bounds the query, not the answer.
LOCATE_MAX_ROWS = 500
#: How far up the location tree a path or an ancestor placement is looked for. Real trees are
#: zone -> aisle -> rack -> shelf -> bin; the cap makes a (write-time-prevented, never assumed
#: absent) cycle a bounded query.
MAX_PATH_DEPTH = 25

LAYOUT_READ_PERMISSION = "operations.warehouse.layout.read"


# ------------------------------------------------------------------ the location tree, upward


@dataclass(frozen=True, slots=True)
class ChainNode:
    """One location on the way up from a start location (depth 0 is the start itself)."""

    id: uuid.UUID
    code: str
    name: str
    depth: int


async def ancestor_chains(
    session: AsyncSession, org_id: uuid.UUID, location_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[ChainNode]]:
    """For each location, the chain from itself up to its root, nearest first — an **upward**
    recursive CTE (`app.inventory.selectors.descendant_location_ids` is the only other tree walk
    and goes down). Organization-filtered at every level; a soft-deleted ancestor ends the chain.
    One query for the whole set, not one per location."""
    ids = set(location_ids)
    if not ids:
        return {}
    start = (
        select(
            Location.id.label("start_id"),
            Location.id.label("node_id"),
            Location.parent_id.label("parent_id"),
            Location.code.label("code"),
            Location.name.label("name"),
            literal(0).label("depth"),
        )
        .where(
            Location.organization_id == org_id,
            Location.id.in_(ids),
            Location.deleted_at.is_(None),
        )
        .cte("chain", recursive=True)
    )
    parent = aliased(Location)
    chain = start.union_all(
        select(
            start.c.start_id,
            parent.id,
            parent.parent_id,
            parent.code,
            parent.name,
            start.c.depth + 1,
        )
        .join(parent, parent.id == start.c.parent_id)
        .where(
            parent.organization_id == org_id,
            parent.deleted_at.is_(None),
            start.c.depth < MAX_PATH_DEPTH,
        )
    )
    rows = (
        await session.execute(
            select(
                chain.c.start_id, chain.c.node_id, chain.c.code, chain.c.name, chain.c.depth
            ).order_by(chain.c.start_id, chain.c.depth)
        )
    ).all()
    out: dict[uuid.UUID, list[ChainNode]] = {}
    for row in rows:
        out.setdefault(row.start_id, []).append(
            ChainNode(id=row.node_id, code=row.code, name=row.name, depth=row.depth)
        )
    return out


def path_of(chain: Sequence[ChainNode]) -> str:
    """`ZONE-A / RACK-03 / BIN-02`: codes from the root down to the bin (what is printed on the
    shelf and scanned), not names — a default bin's name is empty."""
    return " / ".join(node.code for node in reversed(chain))


# ------------------------------------------------------------------ suggestion


def _walk_order() -> tuple[Any, ...]:
    """Pick sequence ascending with none last, then code. The code is ordered `COLLATE "C"` (plain
    byte order, which for UTF-8 is code-point order) because `domain.suggest_putaway_bin` compares
    codes as Python strings: under the database's locale collation the head of this query's
    capped list would not be the head of the rule's order, and the cap would change the answer."""
    return (Location.pick_sequence.asc().nulls_last(), Location.code.collate("C").asc())


async def suggest_candidates(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID,
    item_id: uuid.UUID,
    lot_id: uuid.UUID | None,
) -> list[domain.PutawayBin]:
    """The bins `domain.suggest_putaway_bin` chooses among: up to `SUGGEST_CANDIDATE_CAP` storable
    bins already holding the item (this lot, when given) and as many empty ones, each in walk order.
    Two bounded queries rather than every bin in the warehouse — the rule only ever picks the first
    of each category, so the cap bounds the work without changing the answer.

    Only bins with a barcode are loaded, **in the query** and so before the cap: a put-away
    resolves its destination by barcode alone, and filtering afterwards would let barcode-less bins
    at the head of the walk fill the cap and hide every usable one."""
    holding_balance = (
        select(1)
        .select_from(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.location_id == Location.id,
            InventoryBalance.inventory_item_id == item_id,
            InventoryBalance.quantity_milli > 0,
            InventoryBalance.status.notin_(OFF_SITE_STATUSES),
        )
    )
    if lot_id is not None:
        holding_balance = holding_balance.where(InventoryBalance.lot_id == lot_id)
    any_balance = (
        select(1)
        .select_from(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.location_id == Location.id,
            InventoryBalance.quantity_milli > 0,
        )
    )
    base = select(
        Location.id, Location.code, Location.kind, Location.barcode, Location.pick_sequence
    ).where(
        Location.organization_id == org_id,
        Location.warehouse_id == warehouse_id,
        Location.deleted_at.is_(None),
        Location.kind.in_(domain.STORABLE_KINDS),
        Location.barcode.is_not(None),
        Location.barcode != "",
    )
    holding = (
        await session.execute(
            base.where(exists(holding_balance))
            .order_by(*_walk_order())
            .limit(domain.SUGGEST_CANDIDATE_CAP)
        )
    ).all()
    empty = (
        await session.execute(
            base.where(~exists(any_balance))
            .order_by(*_walk_order())
            .limit(domain.SUGGEST_CANDIDATE_CAP)
        )
    ).all()
    bins: dict[uuid.UUID, domain.PutawayBin] = {}
    for rows, is_holding in ((holding, True), (empty, False)):
        for row in rows:
            bins[row.id] = domain.PutawayBin(
                location_id=row.id,
                code=row.code,
                kind=row.kind,
                barcode=row.barcode,
                pick_sequence=row.pick_sequence,
                holds_item=is_holding,
                is_empty=not is_holding,
            )
    return list(bins.values())


async def suggest(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID,
    item_id: uuid.UUID,
    lot_id: uuid.UUID | None,
) -> domain.PutawaySuggestion | None:
    """The server's own answer to "where should this go" — what `matched_suggestion` is judged
    against, and what `GET /putaway/suggest` returns."""
    return domain.suggest_putaway_bin(
        await suggest_candidates(
            session, org_id, warehouse_id=warehouse_id, item_id=item_id, lot_id=lot_id
        )
    )


async def suggestion_out(
    session: AsyncSession, org_id: uuid.UUID, suggestion: domain.PutawaySuggestion | None
) -> PutawaySuggestionOut | None:
    if suggestion is None:
        return None
    location = (
        await session.execute(
            select(Location).where(
                Location.organization_id == org_id, Location.id == suggestion.location_id
            )
        )
    ).scalar_one()
    chains = await ancestor_chains(session, org_id, [location.id])
    reason: Literal["holds_item", "empty_bin"] = (
        "holds_item" if suggestion.reason == "holds_item" else "empty_bin"
    )
    return PutawaySuggestionOut(
        location_id=str(location.id),
        code=location.code,
        barcode=location.barcode,
        name=location.name,
        path=path_of(chains.get(location.id, [])),
        reason=reason,
    )


# ------------------------------------------------------------------ ids named in a query


async def lot_belongs_to_item(
    session: AsyncSession, org_id: uuid.UUID, lot_id: uuid.UUID, item_id: uuid.UUID
) -> bool:
    stmt = select(Lot.id).where(
        Lot.organization_id == org_id, Lot.id == lot_id, Lot.inventory_item_id == item_id
    )
    return (await session.execute(stmt)).first() is not None


async def roll_belongs_to_item(
    session: AsyncSession, org_id: uuid.UUID, roll_id: uuid.UUID, item_id: uuid.UUID
) -> bool:
    stmt = select(Roll.id).where(
        Roll.organization_id == org_id, Roll.id == roll_id, Roll.inventory_item_id == item_id
    )
    return (await session.execute(stmt)).first() is not None


async def worker_id_for_subject(
    session: AsyncSession, org_id: uuid.UUID, subject_ref: uuid.UUID | None
) -> uuid.UUID | None:
    """The caller's own `workers` row (`Worker.employee_id` is the membership's `subject_ref`), or
    `None`: a manager with no worker record can still put stock away, so this never refuses."""
    if subject_ref is None:
        return None
    stmt = select(Worker.id).where(
        Worker.organization_id == org_id,
        Worker.employee_id == subject_ref,
        Worker.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ captures


async def get_capture(
    session: AsyncSession, org_id: uuid.UUID, move_id: uuid.UUID
) -> PutawayCapture | None:
    """The capture of one put-away move, or `None` — the move has none (it was not a put-away, the
    capture could not be written, or it was purged), or it is another tenant's."""
    stmt = select(PutawayCapture).where(
        PutawayCapture.organization_id == org_id, PutawayCapture.move_id == move_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def detail_out(
    session: AsyncSession, org_id: uuid.UUID, capture: PutawayCapture
) -> PutawayDetailOut:
    """The capture without its fix — `has_location` says whether one exists."""
    location = (
        await session.execute(
            select(Location).where(
                Location.organization_id == org_id, Location.id == capture.location_id
            )
        )
    ).scalar_one()
    return PutawayDetailOut(
        move_id=str(capture.move_id),
        to_location=PutawayToOut(
            id=str(location.id), code=location.code, name=location.name, kind=location.kind
        ),
        worker_id=str(capture.worker_id) if capture.worker_id else None,
        captured_at=capture.captured_at,
        matched_suggestion=capture.matched_suggestion,
        has_location=capture.lat_micro is not None,
    )


def putaway_out(
    *,
    move_id: uuid.UUID,
    replayed: bool,
    source: Location,
    target: Location,
    matched_suggestion: bool | None,
    geo_recorded: bool,
    warnings: Sequence[str],
) -> PutawayOut:
    return PutawayOut(
        move_id=str(move_id),
        replayed=replayed,
        from_location=PutawayFromOut(id=str(source.id), code=source.code, name=source.name),
        to_location=PutawayToOut(
            id=str(target.id), code=target.code, name=target.name, kind=target.kind
        ),
        matched_suggestion=matched_suggestion,
        geo_recorded=geo_recorded,
        warnings=list(warnings),
    )


# ------------------------------------------------------------------ find stock


def locate_stmt(
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
) -> Select[Any]:
    """The bins holding a positive on-site balance of the item, one row per (bin, status) with the
    lots and rolls in it summed. Off-site statuses (in transit, at a job worker) are not in a bin
    on a floor and are left out. Ordered warehouse, bin code, status; one row more than
    `LOCATE_MAX_ROWS` is fetched so the caller can say `truncated`."""
    org_id = ctx.organization_id
    on_hand = cast(func.sum(InventoryBalance.quantity_milli), BigInteger).label("on_hand_milli")
    stmt = (
        select(
            Location.id.label("location_id"),
            Location.code.label("code"),
            Location.name.label("name"),
            Location.kind.label("kind"),
            Warehouse.id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            InventoryBalance.status.label("status"),
            on_hand,
        )
        .select_from(InventoryBalance)
        .join(
            InventoryItem,
            and_(
                InventoryItem.id == InventoryBalance.inventory_item_id,
                InventoryItem.organization_id == org_id,
            ),
        )
        .join(Location, Location.id == InventoryBalance.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(
            InventoryBalance.organization_id == org_id,
            Location.organization_id == org_id,
            Warehouse.organization_id == org_id,
            InventoryBalance.inventory_item_id == item_id,
            InventoryBalance.quantity_milli > 0,
            InventoryBalance.status.notin_(OFF_SITE_STATUSES),
            Location.deleted_at.is_(None),
            Warehouse.deleted_at.is_(None),
        )
    )
    stmt = ctx.apply(stmt, owner_col=InventoryItem.created_by)
    if lot_id is not None:
        stmt = stmt.where(InventoryBalance.lot_id == lot_id)
    if roll_id is not None:
        stmt = stmt.where(InventoryBalance.roll_id == roll_id)
    if warehouse_id is not None:
        stmt = stmt.where(Location.warehouse_id == warehouse_id)
    return (
        stmt.group_by(
            Location.id,
            Location.code,
            Location.name,
            Location.kind,
            Warehouse.id,
            Warehouse.name,
            InventoryBalance.status,
        )
        .order_by(Warehouse.name.asc(), Location.code.asc(), InventoryBalance.status.asc())
        .limit(LOCATE_MAX_ROWS + 1)
    )


@dataclass(frozen=True, slots=True)
class _PublishedPlacement:
    location_id: uuid.UUID
    layout_version_id: uuid.UUID
    floor_id: uuid.UUID
    floor_name: str
    x_mm: int
    y_mm: int
    z_mm: int | None
    width_mm: int
    depth_mm: int
    height_mm: int | None


async def published_placements(
    session: AsyncSession, org_id: uuid.UUID, location_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, _PublishedPlacement]:
    """Where each location sits on a *published* layout — the join runs through `LayoutVersion
    .status = 'published'`, **not** `Location.floor_id` (set on a publish and never cleared, so it
    would keep pointing at a floor whose layout has since dropped the bin, or a draft's). A
    location placed on two floors' published layouts is one answer: the most recently published.
    A location with no published placement is absent from the result."""
    ids = set(location_ids)
    if not ids:
        return {}
    stmt = (
        select(
            LocationPlacement.location_id,
            LayoutVersion.id,
            WarehouseFloor.id,
            WarehouseFloor.name,
            LocationPlacement.x_mm,
            LocationPlacement.y_mm,
            LocationPlacement.z_mm,
            LocationPlacement.width_mm,
            LocationPlacement.depth_mm,
            LocationPlacement.height_mm,
        )
        .join(LayoutVersion, LayoutVersion.id == LocationPlacement.layout_version_id)
        .join(WarehouseFloor, WarehouseFloor.id == LayoutVersion.warehouse_floor_id)
        .where(
            LocationPlacement.organization_id == org_id,
            LayoutVersion.organization_id == org_id,
            WarehouseFloor.organization_id == org_id,
            LocationPlacement.location_id.in_(ids),
            LayoutVersion.status == "published",
            LayoutVersion.deleted_at.is_(None),
            WarehouseFloor.deleted_at.is_(None),
        )
        .distinct(LocationPlacement.location_id)
        .order_by(
            LocationPlacement.location_id,
            LayoutVersion.published_at.desc().nulls_last(),
            LocationPlacement.id,
        )
    )
    return {row[0]: _PublishedPlacement(*row) for row in (await session.execute(stmt)).all()}


async def warehouses_with_published_layout(
    session: AsyncSession, org_id: uuid.UUID, warehouse_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these warehouses have at least one floor with a published layout — the difference
    between a bin that is `unplaced` (there is a plan, it is not on it) and a warehouse with
    `no_published_layout` (there is no plan to be on)."""
    ids = set(warehouse_ids)
    if not ids:
        return set()
    stmt = (
        select(WarehouseFloor.warehouse_id)
        .join(LayoutVersion, LayoutVersion.warehouse_floor_id == WarehouseFloor.id)
        .where(
            WarehouseFloor.organization_id == org_id,
            LayoutVersion.organization_id == org_id,
            WarehouseFloor.warehouse_id.in_(ids),
            WarehouseFloor.deleted_at.is_(None),
            LayoutVersion.status == "published",
            LayoutVersion.deleted_at.is_(None),
        )
        .distinct()
    )
    return {row[0] for row in (await session.execute(stmt)).all()}


async def locate(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    item_id: uuid.UUID,
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
) -> tuple[list[LocateRowOut], bool]:
    """Where an item (a lot, a roll) is: bins with stock, each with its path and — when the caller
    may read layouts — its place on the published floor plan.

    A bin that is not placed itself falls back to the nearest placed **ancestor** (a rack standing
    for its bins), marked `source: "ancestor"`. Geometry and the floor it implies are returned only
    to a caller who also holds `operations.warehouse.layout.read`; without it `placement` is null
    with `placement_reason: "no_permission"`. Four queries however many bins match: the rows, their
    ancestor chains, the published placements of every location on those chains, and which
    warehouses have a published layout at all."""
    org_id = ctx.organization_id
    fetched = (
        await session.execute(
            locate_stmt(
                ctx, item_id=item_id, lot_id=lot_id, roll_id=roll_id, warehouse_id=warehouse_id
            )
        )
    ).all()
    truncated = len(fetched) > LOCATE_MAX_ROWS
    rows = fetched[:LOCATE_MAX_ROWS]
    if not rows:
        return [], False

    can_read_layout = ctx.principal.has(LAYOUT_READ_PERMISSION)
    chains = await ancestor_chains(session, org_id, {r.location_id for r in rows})
    placements: dict[uuid.UUID, _PublishedPlacement] = {}
    layout_warehouses: set[uuid.UUID] = set()
    if can_read_layout:
        on_chains = {node.id for chain in chains.values() for node in chain}
        placements = await published_placements(session, org_id, on_chains)
        layout_warehouses = await warehouses_with_published_layout(
            session, org_id, {r.warehouse_id for r in rows}
        )

    out: list[LocateRowOut] = []
    for row in rows:
        chain = chains.get(row.location_id, [])
        placement: LocatePlacementOut | None = None
        floor_id: str | None = None
        floor_name: str | None = None
        reason: Literal["unplaced", "no_published_layout", "no_permission"] | None = None
        if not can_read_layout:
            reason = "no_permission"
        else:
            # The bin itself first (depth 0), then each container above it, nearest first.
            hit = next(((n, placements[n.id]) for n in chain if n.id in placements), None)
            if hit is not None:
                node, found = hit
                own = node.depth == 0
                placement = LocatePlacementOut(
                    layout_version_id=str(found.layout_version_id),
                    x_mm=found.x_mm,
                    y_mm=found.y_mm,
                    z_mm=found.z_mm,
                    width_mm=found.width_mm,
                    depth_mm=found.depth_mm,
                    height_mm=found.height_mm,
                    source="own" if own else "ancestor",
                    ancestor_location_id=None if own else str(node.id),
                )
                floor_id = str(found.floor_id)
                floor_name = found.floor_name
            elif row.warehouse_id in layout_warehouses:
                reason = "unplaced"
            else:
                reason = "no_published_layout"
        out.append(
            LocateRowOut(
                warehouse_id=str(row.warehouse_id),
                warehouse_name=row.warehouse_name,
                floor_id=floor_id,
                floor_name=floor_name,
                location_id=str(row.location_id),
                code=row.code,
                name=row.name,
                kind=row.kind,
                path=path_of(chain),
                quantity=format_qty(row.on_hand_milli),
                status=row.status,
                placement=placement,
                placement_reason=reason,
            )
        )
    return out, truncated
