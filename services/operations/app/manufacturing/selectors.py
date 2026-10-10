"""Reads for manufacturing. Every query filters on `organization_id` explicitly; RLS is the net,
not the filter (CLAUDE.md). Builders (`*_out`) batch their lookups — one query per kind of
related row for a whole list, never one per row.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.manufacturing import domain
from app.manufacturing.models import (
    Bom,
    BomComponent,
    BomSubstitute,
    BomVersion,
    ProductionGenealogy,
    ProductionMaterial,
    ProductionOrder,
    ProductionOutput,
    ProductionPick,
    ProductionWastage,
    WastageThreshold,
)
from app.manufacturing.schemas import (
    BomComponentOut,
    BomDetailOut,
    BomOut,
    BomSubstituteOut,
    BomVersionDetailOut,
    BomVersionOut,
    ExplosionNodeOut,
    ExplosionOut,
    ExplosionTotalOut,
    ProductionGenealogyOut,
    ProductionMaterialOut,
    ProductionOrderDetailOut,
    ProductionOrderOut,
    ProductionOutputOut,
    ProductionPickOut,
    ProductionWastageOut,
    VarianceLineOut,
    VarianceOut,
    WastageThresholdOut,
)
from app.models import InventoryItem

# ------------------------------------------------------------------ items


@dataclass(frozen=True, slots=True)
class ItemFacts:
    id: uuid.UUID
    sku: str
    name: str
    uom: str
    deleted: bool


async def items_by_id(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ItemFacts]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(
                InventoryItem.id,
                InventoryItem.sku,
                InventoryItem.name,
                InventoryItem.uom,
                InventoryItem.deleted_at,
            ).where(InventoryItem.organization_id == org_id, InventoryItem.id.in_(wanted))
        )
    ).all()
    return {r.id: ItemFacts(r.id, r.sku, r.name, r.uom, r.deleted_at is not None) for r in rows}


async def produced_items(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these items have a live BOM of their own (so are produced, not purchased)."""
    wanted = set(item_ids)
    if not wanted:
        return set()
    rows = (
        await session.execute(
            select(Bom.item_id).where(
                Bom.organization_id == org_id,
                Bom.deleted_at.is_(None),
                Bom.is_active.is_(True),
                Bom.item_id.in_(wanted),
            )
        )
    ).all()
    return {r.item_id for r in rows}


# ------------------------------------------------------------------ BOMs


def boms_stmt(
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    item_id: uuid.UUID | None = None,
    is_active: bool | None = None,
) -> Select[Any]:
    stmt = select(Bom).where(Bom.organization_id == org_id, Bom.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Bom.code.ilike(like), Bom.name.ilike(like)))
    if item_id is not None:
        stmt = stmt.where(Bom.item_id == item_id)
    if is_active is not None:
        stmt = stmt.where(Bom.is_active.is_(is_active))
    return stmt.order_by(Bom.code)


async def get_bom(
    session: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID, *, for_update: bool = False
) -> Bom | None:
    stmt = select(Bom).where(
        Bom.id == bom_id, Bom.organization_id == org_id, Bom.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_bom_for_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> Bom | None:
    return (
        await session.execute(
            select(Bom).where(
                Bom.organization_id == org_id, Bom.item_id == item_id, Bom.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def get_version(
    session: AsyncSession,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    bom_id: uuid.UUID | None = None,
    for_update: bool = False,
) -> BomVersion | None:
    stmt = select(BomVersion).where(
        BomVersion.id == version_id, BomVersion.organization_id == org_id
    )
    if bom_id is not None:
        stmt = stmt.where(BomVersion.bom_id == bom_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def versions_of(
    session: AsyncSession, org_id: uuid.UUID, bom_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[BomVersion]]:
    out: dict[uuid.UUID, list[BomVersion]] = defaultdict(list)
    if not bom_ids:
        return out
    rows = (
        (
            await session.execute(
                select(BomVersion)
                .where(BomVersion.organization_id == org_id, BomVersion.bom_id.in_(bom_ids))
                .order_by(BomVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        out[row.bom_id].append(row)
    return out


async def get_component(
    session: AsyncSession,
    org_id: uuid.UUID,
    component_id: uuid.UUID,
    *,
    version_id: uuid.UUID | None = None,
) -> BomComponent | None:
    stmt = select(BomComponent).where(
        BomComponent.id == component_id, BomComponent.organization_id == org_id
    )
    if version_id is not None:
        stmt = stmt.where(BomComponent.bom_version_id == version_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def components_of(
    session: AsyncSession, org_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[BomComponent]]:
    out: dict[uuid.UUID, list[BomComponent]] = defaultdict(list)
    if not version_ids:
        return out
    rows = (
        (
            await session.execute(
                select(BomComponent)
                .where(
                    BomComponent.organization_id == org_id,
                    BomComponent.bom_version_id.in_(version_ids),
                )
                .order_by(BomComponent.line_no)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        out[row.bom_version_id].append(row)
    return out


async def substitutes_of(
    session: AsyncSession, org_id: uuid.UUID, component_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[BomSubstitute]]:
    out: dict[uuid.UUID, list[BomSubstitute]] = defaultdict(list)
    if not component_ids:
        return out
    rows = (
        (
            await session.execute(
                select(BomSubstitute)
                .where(
                    BomSubstitute.organization_id == org_id,
                    BomSubstitute.bom_component_id.in_(component_ids),
                )
                .order_by(BomSubstitute.priority, BomSubstitute.created_at)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        out[row.bom_component_id].append(row)
    return out


async def get_substitute(
    session: AsyncSession, org_id: uuid.UUID, substitute_id: uuid.UUID, component_id: uuid.UUID
) -> BomSubstitute | None:
    return (
        await session.execute(
            select(BomSubstitute).where(
                BomSubstitute.id == substitute_id,
                BomSubstitute.organization_id == org_id,
                BomSubstitute.bom_component_id == component_id,
            )
        )
    ).scalar_one_or_none()


def version_facts(version: BomVersion) -> domain.VersionFacts:
    return domain.VersionFacts(
        id=str(version.id),
        version_no=version.version_no,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        status=version.status,
    )


def current_version(versions: Sequence[BomVersion], on: date) -> BomVersion | None:
    chosen = domain.resolve_version([version_facts(v) for v in versions], on)
    if chosen is None:
        return None
    return next(v for v in versions if str(v.id) == chosen.id)


async def edges_for_items(
    session: AsyncSession,
    org_id: uuid.UUID,
    parent_items: Iterable[uuid.UUID],
    *,
    exclude_version_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """parent produced item -> its component and substitute items, over every **published**
    version of its BOM (conservative: a cycle through any published version is a cycle).
    `exclude_version_id` leaves out the version being edited, whose edges the caller holds."""
    wanted = set(parent_items)
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if not wanted:
        return out
    stmt = (
        select(Bom.item_id, BomComponent.component_item_id, BomSubstitute.substitute_item_id)
        .select_from(Bom)
        .join(
            BomVersion,
            (BomVersion.bom_id == Bom.id) & (BomVersion.organization_id == org_id),
        )
        .join(
            BomComponent,
            (BomComponent.bom_version_id == BomVersion.id)
            & (BomComponent.organization_id == org_id),
        )
        .outerjoin(
            BomSubstitute,
            (BomSubstitute.bom_component_id == BomComponent.id)
            & (BomSubstitute.organization_id == org_id),
        )
        .where(
            Bom.organization_id == org_id,
            Bom.deleted_at.is_(None),
            Bom.item_id.in_(wanted),
            BomVersion.status == "PUBLISHED",
        )
    )
    if exclude_version_id is not None:
        stmt = stmt.where(BomVersion.id != exclude_version_id)
    for parent, component, substitute in (await session.execute(stmt)).all():
        out[parent].add(component)
        if substitute is not None:
            out[parent].add(substitute)
    return out


# ------------------------------------------------------------------ BOM builders


def _pct(bps: int) -> str:
    return domain.format_bps(bps)


def substitute_out(row: BomSubstitute, items: dict[uuid.UUID, ItemFacts]) -> BomSubstituteOut:
    item = items.get(row.substitute_item_id)
    return BomSubstituteOut(
        id=str(row.id),
        substitute_item_id=str(row.substitute_item_id),
        sku=item.sku if item else "",
        name=item.name if item else "",
        uom=item.uom if item else "",
        ratio=format_qty(row.ratio_milli),
        priority=row.priority,
    )


def component_out(
    row: BomComponent,
    items: dict[uuid.UUID, ItemFacts],
    subs: list[BomSubstitute],
    produced: set[uuid.UUID],
) -> BomComponentOut:
    item = items.get(row.component_item_id)
    return BomComponentOut(
        id=str(row.id),
        line_no=row.line_no,
        component_item_id=str(row.component_item_id),
        sku=item.sku if item else "",
        name=item.name if item else "",
        uom=item.uom if item else "",
        quantity=format_qty(row.quantity_milli),
        scrap_percent=_pct(row.scrap_bps),
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        notes=row.notes,
        is_produced=row.component_item_id in produced,
        substitutes=[substitute_out(s, items) for s in subs],
    )


def version_out(version: BomVersion, component_count: int) -> BomVersionOut:
    return BomVersionOut(
        id=str(version.id),
        bom_id=str(version.bom_id),
        version_no=version.version_no,
        status=version.status,
        output_quantity=format_qty(version.output_quantity_milli),
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        notes=version.notes,
        published_at=version.published_at,
        published_by=version.published_by,
        content_hash=version.content_hash,
        component_count=component_count,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


async def version_detail_out(
    session: AsyncSession, org_id: uuid.UUID, version: BomVersion
) -> BomVersionDetailOut:
    comps = (await components_of(session, org_id, [version.id]))[version.id]
    subs = await substitutes_of(session, org_id, [c.id for c in comps])
    ids: set[uuid.UUID] = {c.component_item_id for c in comps}
    ids.update(s.substitute_item_id for group in subs.values() for s in group)
    items = await items_by_id(session, org_id, ids)
    produced = await produced_items(session, org_id, {c.component_item_id for c in comps})
    base = version_out(version, len(comps))
    return BomVersionDetailOut(
        **base.model_dump(),
        components=[component_out(c, items, subs.get(c.id, []), produced) for c in comps],
    )


async def bom_outs(
    session: AsyncSession, org_id: uuid.UUID, boms: Sequence[Bom], *, today: date
) -> list[BomOut]:
    versions = await versions_of(session, org_id, [b.id for b in boms])
    items = await items_by_id(session, org_id, [b.item_id for b in boms])
    out: list[BomOut] = []
    for bom in boms:
        vs = versions.get(bom.id, [])
        current = current_version(vs, today)
        item = items.get(bom.item_id)
        out.append(
            BomOut(
                id=str(bom.id),
                code=bom.code,
                name=bom.name,
                item_id=str(bom.item_id),
                sku=item.sku if item else "",
                item_name=item.name if item else "",
                uom=item.uom if item else "",
                description=bom.description,
                is_active=bom.is_active,
                version_count=len(vs),
                latest_version_no=max((v.version_no for v in vs), default=None),
                current_version_id=str(current.id) if current else None,
                current_version_no=current.version_no if current else None,
                created_at=bom.created_at,
                updated_at=bom.updated_at,
            )
        )
    return out


async def bom_detail_out(
    session: AsyncSession, org_id: uuid.UUID, bom: Bom, *, today: date
) -> BomDetailOut:
    (base,) = await bom_outs(session, org_id, [bom], today=today)
    versions = (await versions_of(session, org_id, [bom.id]))[bom.id]
    counts = await component_counts(session, org_id, [v.id for v in versions])
    return BomDetailOut(
        **base.model_dump(),
        versions=[version_out(v, counts.get(v.id, 0)) for v in versions],
    )


async def component_counts(
    session: AsyncSession, org_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            select(BomComponent.bom_version_id, func.count())
            .where(
                BomComponent.organization_id == org_id,
                BomComponent.bom_version_id.in_(version_ids),
            )
            .group_by(BomComponent.bom_version_id)
        )
    ).all()
    return {r[0]: int(r[1]) for r in rows}


# ------------------------------------------------------------------ production orders


def orders_stmt(
    org_id: uuid.UUID,
    *,
    order_status: str | None = None,
    item_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(ProductionOrder).where(
        ProductionOrder.organization_id == org_id, ProductionOrder.deleted_at.is_(None)
    )
    if order_status:
        stmt = stmt.where(ProductionOrder.status == order_status)
    if item_id is not None:
        stmt = stmt.where(ProductionOrder.item_id == item_id)
    if q:
        stmt = stmt.where(ProductionOrder.number.ilike(f"%{q.strip()}%"))
    return stmt.order_by(ProductionOrder.seq.desc())


async def get_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> ProductionOrder | None:
    stmt = select(ProductionOrder).where(
        ProductionOrder.id == order_id,
        ProductionOrder.organization_id == org_id,
        ProductionOrder.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def materials_of(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> list[ProductionMaterial]:
    stmt = (
        select(ProductionMaterial)
        .where(
            ProductionMaterial.organization_id == org_id,
            ProductionMaterial.production_order_id == order_id,
        )
        .order_by(ProductionMaterial.line_no)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def picks_of(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> list[ProductionPick]:
    stmt = (
        select(ProductionPick)
        .where(
            ProductionPick.organization_id == org_id,
            ProductionPick.production_order_id == order_id,
        )
        .order_by(ProductionPick.created_at, ProductionPick.id)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def outputs_of(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[ProductionOutput]:
    return list(
        (
            await session.execute(
                select(ProductionOutput)
                .where(
                    ProductionOutput.organization_id == org_id,
                    ProductionOutput.production_order_id == order_id,
                )
                .order_by(ProductionOutput.created_at, ProductionOutput.id)
            )
        )
        .scalars()
        .all()
    )


async def wastage_of(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[ProductionWastage]:
    return list(
        (
            await session.execute(
                select(ProductionWastage)
                .where(
                    ProductionWastage.organization_id == org_id,
                    ProductionWastage.production_order_id == order_id,
                )
                .order_by(ProductionWastage.created_at, ProductionWastage.id)
            )
        )
        .scalars()
        .all()
    )


async def genealogy_of(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[ProductionGenealogy]:
    return list(
        (
            await session.execute(
                select(ProductionGenealogy)
                .where(
                    ProductionGenealogy.organization_id == org_id,
                    ProductionGenealogy.production_order_id == order_id,
                )
                .order_by(ProductionGenealogy.created_at, ProductionGenealogy.id)
            )
        )
        .scalars()
        .all()
    )


async def thresholds(session: AsyncSession, org_id: uuid.UUID) -> list[WastageThreshold]:
    return list(
        (
            await session.execute(
                select(WastageThreshold)
                .where(
                    WastageThreshold.organization_id == org_id,
                    WastageThreshold.deleted_at.is_(None),
                )
                .order_by(WastageThreshold.created_at)
            )
        )
        .scalars()
        .all()
    )


async def get_threshold(
    session: AsyncSession, org_id: uuid.UUID, threshold_id: uuid.UUID
) -> WastageThreshold | None:
    return (
        await session.execute(
            select(WastageThreshold).where(
                WastageThreshold.id == threshold_id,
                WastageThreshold.organization_id == org_id,
                WastageThreshold.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


# ------------------------------------------------------------------ order builders


def wip_of(row: ProductionMaterial | ProductionPick) -> int:
    return row.issued_milli - row.consumed_milli - row.returned_milli - row.wasted_milli


def material_out(
    row: ProductionMaterial, items: dict[uuid.UUID, ItemFacts]
) -> ProductionMaterialOut:
    item = items.get(row.item_id)
    component = items.get(row.component_item_id)
    covered = row.reserved_milli + row.issued_milli - row.returned_milli
    return ProductionMaterialOut(
        id=str(row.id),
        line_no=row.line_no,
        component_item_id=str(row.component_item_id),
        component_sku=component.sku if component else "",
        item_id=str(row.item_id),
        sku=item.sku if item else "",
        name=item.name if item else "",
        uom=item.uom if item else "",
        is_substitute=row.item_id != row.component_item_id,
        ratio=format_qty(row.ratio_milli),
        scrap_percent=_pct(row.scrap_bps),
        required=format_qty(row.required_milli),
        reserved=format_qty(row.reserved_milli),
        issued=format_qty(row.issued_milli),
        consumed=format_qty(row.consumed_milli),
        returned=format_qty(row.returned_milli),
        wasted=format_qty(row.wasted_milli),
        wip=format_qty(wip_of(row)),
        open_need=format_qty(max(row.required_milli - covered, 0)),
    )


def pick_out(row: ProductionPick) -> ProductionPickOut:
    return ProductionPickOut(
        id=str(row.id),
        material_id=str(row.material_id),
        item_id=str(row.item_id),
        source_location_id=str(row.source_location_id),
        wip_location_id=str(row.wip_location_id),
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        reservation_id=str(row.reservation_id) if row.reservation_id else None,
        reserved=format_qty(row.reserved_milli),
        issued=format_qty(row.issued_milli),
        consumed=format_qty(row.consumed_milli),
        returned=format_qty(row.returned_milli),
        wasted=format_qty(row.wasted_milli),
        wip=format_qty(wip_of(row)),
    )


def output_out(row: ProductionOutput) -> ProductionOutputOut:
    return ProductionOutputOut(
        id=str(row.id),
        production_order_id=str(row.production_order_id),
        item_id=str(row.item_id),
        quantity=format_qty(row.quantity_milli),
        location_id=str(row.location_id),
        status=row.status,
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        note=row.note,
        created_at=row.created_at,
    )


def wastage_out(row: ProductionWastage) -> ProductionWastageOut:
    return ProductionWastageOut(
        id=str(row.id),
        production_order_id=str(row.production_order_id),
        material_id=str(row.material_id),
        pick_id=str(row.pick_id),
        event_id=str(row.event_id),
        item_id=str(row.item_id),
        wastage_type=row.wastage_type,
        quantity=format_qty(row.quantity_milli),
        location_id=str(row.location_id),
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        note=row.note,
        created_at=row.created_at,
    )


def genealogy_out(row: ProductionGenealogy) -> ProductionGenealogyOut:
    return ProductionGenealogyOut(
        id=str(row.id),
        output_id=str(row.output_id),
        parent_item_id=str(row.parent_item_id),
        parent_lot_id=str(row.parent_lot_id) if row.parent_lot_id else None,
        parent_roll_id=str(row.parent_roll_id) if row.parent_roll_id else None,
        child_item_id=str(row.child_item_id),
        child_lot_id=str(row.child_lot_id) if row.child_lot_id else None,
        child_roll_id=str(row.child_roll_id) if row.child_roll_id else None,
        quantity=format_qty(row.quantity_milli),
    )


def variance_lines(
    order: ProductionOrder,
    materials: Sequence[ProductionMaterial],
    items: dict[uuid.UUID, ItemFacts],
) -> VarianceOut:
    """Standard vs actual for what has been produced so far. `standard` is the BOM's need
    (scrap allowance included) for the **produced** quantity; `actual` is what was consumed plus
    what was wasted. Material still sitting in WIP, or returned, is neither."""
    lines: list[VarianceLineOut] = []
    for m in materials:
        standard = domain.required_milli(
            component_milli=m.bom_quantity_milli,
            output_milli=m.output_milli,
            planned_milli=order.produced_quantity_milli,
            scrap_bps=m.scrap_bps,
            ratio_milli=m.ratio_milli,
            discrete=m.discrete,
        )
        theoretical = domain.theoretical_milli(
            component_milli=m.bom_quantity_milli,
            output_milli=m.output_milli,
            good_milli=order.produced_quantity_milli,
            ratio_milli=m.ratio_milli,
            discrete=m.discrete,
        )
        actual = m.consumed_milli + m.wasted_milli
        variance = actual - standard
        percent: str | None = None
        if standard:
            # Two decimals, rounded half away from zero, in integers.
            hundredths = (abs(variance) * 10_000 * 2 + standard) // (standard * 2)
            percent = f"{'-' if variance < 0 and hundredths else ''}{hundredths // 100}." + (
                f"{hundredths % 100:02d}"
            )
        item = items.get(m.item_id)
        lines.append(
            VarianceLineOut(
                material_id=str(m.id),
                item_id=str(m.item_id),
                sku=item.sku if item else "",
                name=item.name if item else "",
                uom=item.uom if item else "",
                standard=format_qty(standard),
                theoretical=format_qty(theoretical),
                consumed=format_qty(m.consumed_milli),
                wasted=format_qty(m.wasted_milli),
                actual=format_qty(actual),
                variance=format_qty(variance),
                variance_percent=percent,
            )
        )
    return VarianceOut(
        planned_quantity=format_qty(order.planned_quantity_milli),
        produced_quantity=format_qty(order.produced_quantity_milli),
        lines=lines,
    )


async def order_outs(
    session: AsyncSession, org_id: uuid.UUID, orders: Sequence[ProductionOrder]
) -> list[ProductionOrderOut]:
    items = await items_by_id(session, org_id, [o.item_id for o in orders])
    bom_ids = {o.bom_id for o in orders}
    version_ids = {o.bom_version_id for o in orders}
    boms: dict[uuid.UUID, str] = {}
    versions: dict[uuid.UUID, int] = {}
    if bom_ids:
        boms = {
            r.id: r.code
            for r in (
                await session.execute(
                    select(Bom.id, Bom.code).where(
                        Bom.organization_id == org_id, Bom.id.in_(bom_ids)
                    )
                )
            ).all()
        }
        versions = {
            r.id: r.version_no
            for r in (
                await session.execute(
                    select(BomVersion.id, BomVersion.version_no).where(
                        BomVersion.organization_id == org_id, BomVersion.id.in_(version_ids)
                    )
                )
            ).all()
        }
    return [
        order_out(
            o, items.get(o.item_id), boms.get(o.bom_id, ""), versions.get(o.bom_version_id, 0)
        )
        for o in orders
    ]


def order_out(
    order: ProductionOrder, item: ItemFacts | None, bom_code: str, version_no: int
) -> ProductionOrderOut:
    return ProductionOrderOut(
        id=str(order.id),
        number=order.number,
        status=order.status,
        bom_id=str(order.bom_id),
        bom_code=bom_code,
        bom_version_id=str(order.bom_version_id),
        bom_version_no=version_no,
        item_id=str(order.item_id),
        sku=item.sku if item else "",
        name=item.name if item else "",
        uom=item.uom if item else "",
        planned_quantity=format_qty(order.planned_quantity_milli),
        produced_quantity=format_qty(order.produced_quantity_milli),
        output_location_id=str(order.output_location_id),
        wip_location_id=str(order.wip_location_id) if order.wip_location_id else None,
        planned_start=order.planned_start,
        due_date=order.due_date,
        approved_at=order.approved_at,
        started_at=order.started_at,
        completed_at=order.completed_at,
        cancelled_at=order.cancelled_at,
        cancel_reason=order.cancel_reason,
        notes=order.notes,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


async def order_detail_out(
    session: AsyncSession, org_id: uuid.UUID, order: ProductionOrder
) -> ProductionOrderDetailOut:
    (base,) = await order_outs(session, org_id, [order])
    materials = await materials_of(session, org_id, order.id)
    picks = await picks_of(session, org_id, order.id)
    outputs = await outputs_of(session, org_id, order.id)
    wastage = await wastage_of(session, org_id, order.id)
    items = await items_by_id(
        session,
        org_id,
        {m.item_id for m in materials} | {m.component_item_id for m in materials},
    )
    return ProductionOrderDetailOut(
        **base.model_dump(),
        materials=[material_out(m, items) for m in materials],
        picks=[pick_out(p) for p in picks],
        outputs=[output_out(o) for o in outputs],
        wastage=[wastage_out(w) for w in wastage],
        variance=variance_lines(order, materials, items),
    )


def threshold_out(row: WastageThreshold, items: dict[uuid.UUID, ItemFacts]) -> WastageThresholdOut:
    item = items.get(row.item_id) if row.item_id else None
    return WastageThresholdOut(
        id=str(row.id),
        item_id=str(row.item_id) if row.item_id else None,
        sku=item.sku if item else None,
        wastage_type=row.wastage_type,
        max_percent=_pct(row.max_bps),
        created_at=row.created_at,
    )


# ------------------------------------------------------------------ explosion


async def explosion_out(
    session: AsyncSession,
    org_id: uuid.UUID,
    bom: Bom,
    version: BomVersion,
    quantity_milli: int,
    *,
    on: date,
) -> ExplosionOut:
    """The multi-level requirement for `quantity_milli` of the BOM's item, on `on`: each
    component that is itself produced is replaced by what *its* BOM in force needs. A cycle (which
    publishing refuses, but data can predate the guard) raises `ValueError`."""
    from app.inventory.quantity import is_discrete

    async def lines_for(v: BomVersion) -> list[BomComponent]:
        comps = (await components_of(session, org_id, [v.id]))[v.id]
        return [c for c in comps if domain.in_window(on, c.effective_from, c.effective_to)]

    uoms: dict[uuid.UUID, ItemFacts] = {}

    def explode_lines(rows: Sequence[BomComponent]) -> tuple[domain.ExplodeLine, ...]:
        return tuple(
            domain.ExplodeLine(
                component_item_id=str(c.component_item_id),
                quantity_milli=c.quantity_milli,
                scrap_bps=c.scrap_bps,
                discrete=is_discrete(uoms[c.component_item_id].uom)
                if c.component_item_id in uoms
                else False,
            )
            for c in rows
        )

    root_rows = await lines_for(version)
    uoms.update(await items_by_id(session, org_id, {c.component_item_id for c in root_rows}))
    cache: dict[str, domain.ExplodeBom | None] = {
        str(bom.item_id): domain.ExplodeBom(
            str(bom.item_id), version.output_quantity_milli, explode_lines(root_rows)
        )
    }
    frontier = {c.component_item_id for c in root_rows}
    while frontier:
        frontier = {i for i in frontier if str(i) not in cache}
        if not frontier:
            break
        boms = (
            (
                await session.execute(
                    select(Bom).where(
                        Bom.organization_id == org_id,
                        Bom.deleted_at.is_(None),
                        Bom.is_active.is_(True),
                        Bom.item_id.in_(frontier),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_item = {b.item_id: b for b in boms}
        versions = await versions_of(session, org_id, [b.id for b in boms])
        nxt: set[uuid.UUID] = set()
        for item_id in frontier:
            produced_bom = by_item.get(item_id)
            chosen = (
                current_version(versions.get(produced_bom.id, []), on) if produced_bom else None
            )
            if produced_bom is None or chosen is None:
                cache[str(item_id)] = None
                continue
            rows = await lines_for(chosen)
            uoms.update(await items_by_id(session, org_id, {c.component_item_id for c in rows}))
            cache[str(item_id)] = domain.ExplodeBom(
                str(item_id), chosen.output_quantity_milli, explode_lines(rows)
            )
            nxt |= {c.component_item_id for c in rows}
        frontier = nxt

    tree = domain.explode(str(bom.item_id), quantity_milli, lambda i: cache.get(i))
    items = await items_by_id(
        session, org_id, {uuid.UUID(n) for n in _node_ids(tree)} | {bom.item_id}
    )
    totals: dict[str, int] = defaultdict(int)

    def convert(node: domain.ExplodeNode) -> ExplosionNodeOut:
        item = items.get(uuid.UUID(node.item_id))
        if not node.is_produced and node.level > 0:
            totals[node.item_id] += node.quantity_milli
        return ExplosionNodeOut(
            item_id=node.item_id,
            sku=item.sku if item else "",
            name=item.name if item else "",
            uom=item.uom if item else "",
            level=node.level,
            quantity=format_qty(node.quantity_milli),
            is_produced=node.is_produced,
            children=[convert(c) for c in node.children],
        )

    root = convert(tree)
    leaf_totals = []
    for item_id_str, total in totals.items():
        item = items.get(uuid.UUID(item_id_str))
        leaf_totals.append(
            ExplosionTotalOut(
                item_id=item_id_str,
                sku=item.sku if item else "",
                name=item.name if item else "",
                uom=item.uom if item else "",
                quantity=format_qty(total),
            )
        )
    leaf_totals.sort(key=lambda t: t.sku)
    return ExplosionOut(
        bom_id=str(bom.id),
        version_id=str(version.id),
        quantity=format_qty(quantity_milli),
        root=root,
        leaf_totals=leaf_totals,
    )


def _node_ids(node: domain.ExplodeNode) -> set[str]:
    out = {node.item_id}
    for child in node.children:
        out |= _node_ids(child)
    return out
