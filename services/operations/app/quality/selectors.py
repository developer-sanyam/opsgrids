"""Reads for quality (plan Phase 4). Every query filters on `organization_id` explicitly; RLS is
the net, not the filter (CLAUDE.md). A row outside the caller's organization is simply not found —
the router turns that into a 404, never a 403 that would confirm it exists elsewhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.models import (
    Inspection,
    InspectionResult,
    InventoryItem,
    Location,
    QcTemplate,
    QcTemplateParam,
    Warehouse,
)
from app.quality import domain
from app.quality.schemas import (
    InspectionOut,
    InspectionResultOut,
    QcParamOut,
    QcTemplateOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ templates


def templates_stmt(
    ctx: ScopeContext,
    *,
    kind: str | None = None,
    item_id: uuid.UUID | None = None,
    active: bool | None = None,
) -> Select[Any]:
    stmt = select(QcTemplate).where(
        QcTemplate.organization_id == ctx.organization_id, QcTemplate.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if kind is not None:
        stmt = stmt.where(QcTemplate.kind == kind)
    if item_id is not None:
        stmt = stmt.where(QcTemplate.item_id == item_id)
    if active is not None:
        stmt = stmt.where(QcTemplate.is_active.is_(active))
    return stmt.order_by(QcTemplate.code.asc())


async def get_template(
    session: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID, *, for_update: bool = False
) -> QcTemplate | None:
    stmt = select(QcTemplate).where(
        QcTemplate.id == template_id,
        QcTemplate.organization_id == org_id,
        QcTemplate.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_template_by_code(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> QcTemplate | None:
    stmt = select(QcTemplate).where(
        QcTemplate.organization_id == org_id,
        QcTemplate.code == code,
        QcTemplate.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_template_by_create_key(
    session: AsyncSession, org_id: uuid.UUID, key: str
) -> QcTemplate | None:
    stmt = select(QcTemplate).where(
        QcTemplate.organization_id == org_id, QcTemplate.create_key == key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def params_for_templates(
    session: AsyncSession, org_id: uuid.UUID, template_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[QcTemplateParam]]:
    ids = list(template_ids)
    out: dict[uuid.UUID, list[QcTemplateParam]] = {i: [] for i in ids}
    if not ids:
        return out
    stmt = (
        select(QcTemplateParam)
        .where(
            QcTemplateParam.organization_id == org_id,
            QcTemplateParam.template_id.in_(ids),
            QcTemplateParam.deleted_at.is_(None),
        )
        .order_by(QcTemplateParam.position.asc(), QcTemplateParam.name.asc())
    )
    for param in (await session.execute(stmt)).scalars():
        out[param.template_id].append(param)
    return out


def _fmt(milli: int | None) -> str | None:
    return None if milli is None else format_qty(milli)


def template_out(row: QcTemplate, params: Sequence[QcTemplateParam]) -> QcTemplateOut:
    return QcTemplateOut(
        id=str(row.id),
        code=row.code,
        name=row.name,
        kind=row.kind,
        item_id=str(row.item_id) if row.item_id else None,
        description=row.description,
        is_active=row.is_active,
        params=[
            QcParamOut(
                id=str(p.id),
                name=p.name,
                kind=p.kind,
                unit=p.unit,
                min=_fmt(p.min_milli),
                max=_fmt(p.max_milli),
                is_critical=p.is_critical,
                position=p.position,
            )
            for p in params
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ------------------------------------------------------------------ inspections


def inspections_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    kind: str | None = None,
    item_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
) -> Select[Any]:
    stmt = select(Inspection).where(
        Inspection.organization_id == ctx.organization_id, Inspection.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if status is not None:
        stmt = stmt.where(Inspection.status == status)
    if kind is not None:
        stmt = stmt.where(Inspection.kind == kind)
    if item_id is not None:
        stmt = stmt.where(Inspection.item_id == item_id)
    if lot_id is not None:
        stmt = stmt.where(Inspection.lot_id == lot_id)
    return stmt.order_by(Inspection.created_at.desc(), Inspection.id.desc())


async def get_inspection(
    session: AsyncSession,
    org_id: uuid.UUID,
    inspection_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Inspection | None:
    stmt = select(Inspection).where(
        Inspection.id == inspection_id,
        Inspection.organization_id == org_id,
        Inspection.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_inspection_by_create_key(
    session: AsyncSession, org_id: uuid.UUID, key: str
) -> Inspection | None:
    stmt = select(Inspection).where(
        Inspection.organization_id == org_id, Inspection.create_key == key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def results_for(
    session: AsyncSession, org_id: uuid.UUID, inspection_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[InspectionResult]]:
    ids = list(inspection_ids)
    out: dict[uuid.UUID, list[InspectionResult]] = {i: [] for i in ids}
    if not ids:
        return out
    stmt = (
        select(InspectionResult)
        .where(
            InspectionResult.organization_id == org_id,
            InspectionResult.inspection_id.in_(ids),
            InspectionResult.deleted_at.is_(None),
        )
        .order_by(InspectionResult.position.asc(), InspectionResult.param_name.asc())
    )
    for row in (await session.execute(stmt)).scalars():
        out[row.inspection_id].append(row)
    return out


async def item_info(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str, str]]:
    """`item_id -> (sku, name, uom)`."""
    ids = list(set(item_ids))
    if not ids:
        return {}
    stmt = select(InventoryItem.id, InventoryItem.sku, InventoryItem.name, InventoryItem.uom).where(
        InventoryItem.organization_id == org_id, InventoryItem.id.in_(ids)
    )
    return {r.id: (r.sku, r.name, r.uom) for r in (await session.execute(stmt)).all()}


async def location_codes(
    session: AsyncSession, org_id: uuid.UUID, location_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list(set(location_ids))
    if not ids:
        return {}
    stmt = (
        select(Location.id, Warehouse.code, Location.code)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(Location.organization_id == org_id, Location.id.in_(ids))
    )
    return {loc_id: f"{wh}/{code}" for loc_id, wh, code in (await session.execute(stmt)).all()}


def _result_out(row: InspectionResult) -> InspectionResultOut:
    return InspectionResultOut(
        id=str(row.id),
        param_name=row.param_name,
        param_kind=row.param_kind,
        unit=row.unit,
        min=_fmt(row.min_milli),
        max=_fmt(row.max_milli),
        is_critical=row.is_critical,
        value=_fmt(row.value_milli),
        passed=row.passed,
        note=row.note,
        recorded_by=row.recorded_by,
    )


def inspection_out(
    row: Inspection,
    *,
    results: Sequence[InspectionResult],
    item: tuple[str, str, str] | None,
    location_code: str,
) -> InspectionOut:
    judged = [domain.Judged(r.passed, r.is_critical) for r in results]
    sku, name, uom = item or ("", "", "")
    return InspectionOut(
        id=str(row.id),
        kind=row.kind,
        status=row.status,
        template_id=str(row.template_id) if row.template_id else None,
        item_id=str(row.item_id),
        item_sku=sku,
        item_name=name,
        uom=uom,
        location_id=str(row.location_id),
        location_code=location_code,
        lot_id=str(row.lot_id) if row.lot_id else None,
        roll_id=str(row.roll_id) if row.roll_id else None,
        supplier_id=str(row.supplier_id) if row.supplier_id else None,
        reference_type=row.reference_type,
        reference_id=str(row.reference_id) if row.reference_id else None,
        quantity=format_qty(row.quantity_milli),
        accepted=_fmt(row.accepted_milli),
        rejected=_fmt(row.rejected_milli),
        rejected_bucket=row.rejected_bucket,
        notes=row.notes,
        decision_note=row.decision_note,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        disposition=row.disposition,
        disposition_note=row.disposition_note,
        disposition_reference=row.disposition_reference,
        disposed_at=row.disposed_at,
        hold_move_id=str(row.hold_move_id) if row.hold_move_id else None,
        decision_move_id=str(row.decision_move_id) if row.decision_move_id else None,
        disposition_move_id=str(row.disposition_move_id) if row.disposition_move_id else None,
        suggested_outcome=domain.suggested_outcome(judged),
        can_release=domain.can_release(judged),
        results=[_result_out(r) for r in results],
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def inspections_out(
    session: AsyncSession, org_id: uuid.UUID, rows: Sequence[Inspection]
) -> list[InspectionOut]:
    """Hydrates a page of inspections with three batched lookups, never one per row."""
    results = await results_for(session, org_id, [r.id for r in rows])
    items = await item_info(session, org_id, [r.item_id for r in rows])
    locations = await location_codes(session, org_id, [r.location_id for r in rows])
    return [
        inspection_out(
            r,
            results=results.get(r.id, []),
            item=items.get(r.item_id),
            location_code=locations.get(r.location_id, ""),
        )
        for r in rows
    ]
