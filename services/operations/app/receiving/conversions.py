"""Item-specific unit conversions (`uom_conversions`): "1 box of this item = 12 pcs of it".

The table has existed since the Phase 1 UOM migration; Phase 2 gives it an API and applies it
where a container unit first appears on a document — the goods receipt (`app.receiving.receipts`)
and a purchase-order line ordered in a box (`app.receiving.selectors.ordered_milli`). The ratio is
two positive integers kept in lowest terms; every conversion through it is `Fraction` arithmetic
via `app.inventory.quantity.convert_by_ratio`, exact or refused, never rounded.

A unit with a *fixed* conversion to the item's stock unit (`cm` for an item stocked in `m`) needs
no row and is refused one: two sources of truth for one fact is how a ratio drifts.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import quantity
from app.inventory import services as inv_services
from app.models import AuditLog, InventoryItem, PurchaseOrder, PurchaseOrderLine, Uom, UomConversion
from app.receiving import domain
from app.receiving.schemas import UomConversionIn, UomConversionPatch
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, ValidationProblem

_OPEN_PO = ("draft", "approved", "partially_received")


def _unit_error(message: str, code: str) -> ValidationProblem:
    return ValidationProblem(message, errors=[{"field": "uom", "code": code, "message": message}])


async def _open_lines_using(
    session: AsyncSession, org_id: uuid.UUID, item: InventoryItem, code: str
) -> list[PurchaseOrderLine]:
    """Open purchase-order lines for this item ordered in the unit `code` — what a ratio change or
    removal would silently re-interpret."""
    rows = (
        (
            await session.execute(
                select(PurchaseOrderLine)
                .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
                .where(
                    PurchaseOrderLine.organization_id == org_id,
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrderLine.sku == item.sku,
                    PurchaseOrderLine.deleted_at.is_(None),
                    PurchaseOrder.deleted_at.is_(None),
                    PurchaseOrder.status.in_(_OPEN_PO),
                )
            )
        )
        .scalars()
        .all()
    )
    return [ln for ln in rows if ln.uom.strip() and quantity.canonical_uom_code(ln.uom) == code]


async def _refuse_if_received(
    session: AsyncSession, org_id: uuid.UUID, item: InventoryItem, code: str
) -> None:
    """A ratio that appears or changes after an open PO received in that unit would re-interpret
    the quantity already booked."""
    if any(ln.received_milli > 0 for ln in await _open_lines_using(session, org_id, item, code)):
        raise ConflictProblem(
            f"An open purchase order already received against {item.sku} in {code}; "
            "changing the ratio now would re-interpret what it has received"
        )


async def create_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    payload: UomConversionIn,
    *,
    request: Request | None = None,
) -> tuple[UomConversion, Uom]:
    org_id = ctx.organization_id
    code = quantity.canonical_uom_code(payload.uom)
    stock_code = quantity.canonical_uom_code(item.uom)
    if code == stock_code:
        raise _unit_error(
            f"{payload.uom!r} is already {item.sku}'s own unit; a conversion is from another unit",
            "same_unit",
        )
    if domain.stock_units_per(code, stock_code, {}) is not None:
        raise _unit_error(
            f"{code} converts to {stock_code} by a fixed ratio; it needs no per-item conversion",
            "fixed_conversion_exists",
        )
    try:
        numerator, denominator = domain.reduce_ratio(payload.numerator, payload.denominator)
    except quantity.QuantityError as exc:  # pragma: no cover - the schema already bounds both
        raise _unit_error(str(exc), "invalid_ratio") from exc
    await _refuse_if_received(session, org_id, item, code)
    uom = await inv_services.resolve_or_create_uom(session, org_id, code, str(ctx.principal.sub))
    row_id = (
        await session.execute(
            pg_insert(UomConversion)
            .values(
                id=uuid.uuid4(),
                organization_id=org_id,
                inventory_item_id=item.id,
                uom_id=uom.id,
                numerator=numerator,
                denominator=denominator,
                created_by=str(ctx.principal.sub),
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "inventory_item_id", "uom_id"]
            )
            .returning(UomConversion.id)
        )
    ).scalar_one_or_none()
    if row_id is None:
        raise ConflictProblem(
            f"{item.sku} already has a conversion from {code}; edit it instead",
            errors=[{"field": "uom", "code": "duplicate", "message": "already converted"}],
        )
    row = (
        await session.execute(select(UomConversion).where(UomConversion.id == row_id))
    ).scalar_one()
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="uom_conversion.created",
        entity="uom_conversion",
        entity_id=row.id,
        after=_snapshot(item, code, numerator, denominator),
        request=request,
    )
    return row, uom


def _snapshot(item: InventoryItem, code: str, numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "inventory_item_id": str(item.id),
        "sku": item.sku,
        "uom": code,
        "numerator": numerator,
        "denominator": denominator,
        "stock_uom": item.uom,
    }


async def update_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    row: UomConversion,
    uom: Uom,
    item: InventoryItem,
    payload: UomConversionPatch,
    *,
    request: Request | None = None,
) -> UomConversion:
    numerator = payload.numerator if payload.numerator is not None else row.numerator
    denominator = payload.denominator if payload.denominator is not None else row.denominator
    numerator, denominator = domain.reduce_ratio(numerator, denominator)
    if (numerator, denominator) != (row.numerator, row.denominator):
        await _refuse_if_received(session, ctx.organization_id, item, uom.code)
    before = _snapshot(item, uom.code, row.numerator, row.denominator)
    row.numerator, row.denominator = numerator, denominator
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="uom_conversion.updated",
        entity="uom_conversion",
        entity_id=row.id,
        before=before,
        after=_snapshot(item, uom.code, numerator, denominator),
        request=request,
    )
    return row


async def delete_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    row: UomConversion,
    uom: Uom,
    item: InventoryItem,
    *,
    request: Request | None = None,
) -> None:
    if await _open_lines_using(session, ctx.organization_id, item, uom.code):
        raise ConflictProblem(
            f"An open purchase order orders {item.sku} in {uom.code}; removing the conversion "
            "would make that order's quantity mean something else"
        )
    before = _snapshot(item, uom.code, row.numerator, row.denominator)
    await session.execute(
        delete(UomConversion).where(
            UomConversion.organization_id == ctx.organization_id, UomConversion.id == row.id
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="uom_conversion.deleted",
        entity="uom_conversion",
        entity_id=row.id,
        before=before,
        request=request,
    )
