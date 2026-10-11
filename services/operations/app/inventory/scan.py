"""Resolve a scanned payload to the entity it names, and to what the caller may do with it
(inventory Phase 9, ADR-0027).

Every lookup carries the organization filter and returns `None` for a code that is not in this
organization *or* is in another one — the caller answers both with the same 404, never a 403 that
would confirm the code is real somewhere. A code may name a row either by its printed `barcode` or
by its natural key (SKU, lot number, roll number); if the two name *different* rows of one type
the scan is ambiguous and the resolver refuses to guess (a 409), which is the one case where a
careless label could pick the wrong bin.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import selectors as inv_selectors
from app.inventory import tracking_selectors as sel
from app.inventory.barcode import (
    SCAN_TYPES,
    ScanPayload,
    ScanPayloadError,
    ScanType,
    format_payload,
    parse_payload,
)
from app.inventory.bulk_schemas import ScanAction, ScanEntityOut, ScanOut
from app.inventory.quantity import format_qty
from app.models import InventoryItem, Location, Lot, Roll, Warehouse
from platform_core.auth import Principal
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

_ActionRule = tuple[ScanAction, str | tuple[str, ...]]

#: Receiving posts a goods receipt, which needs both of these (see app/receiving/api.py).
_RECEIVE = ("operations.procurement.receipt.write", "operations.inventory.stock.receive")

#: Which permission each action needs, per type. The server never *performs* an action here — it
#: only tells the UI which buttons to draw; every action's own endpoint enforces its permission.
_RULES: dict[ScanType, tuple[_ActionRule, ...]] = {
    "item": (
        ("transfer", "operations.inventory.stock.transfer"),
        ("status_change", "operations.inventory.stock.transfer"),
        ("issue", "operations.inventory.stock.adjust"),
        ("receive", _RECEIVE),
        ("pick", "operations.fulfilment.task.execute"),
        ("inspect", "operations.quality.inspection.inspect"),
    ),
    "lot": (
        ("transfer", "operations.inventory.stock.transfer"),
        ("status_change", "operations.inventory.stock.transfer"),
        ("inspect", "operations.quality.inspection.inspect"),
    ),
    "roll": (
        ("transfer", "operations.inventory.stock.transfer"),
        ("status_change", "operations.inventory.stock.transfer"),
        ("issue", "operations.inventory.stock.adjust"),
        ("inspect", "operations.quality.inspection.inspect"),
    ),
    "location": (
        ("count", "operations.inventory.count.execute"),
        ("receive", _RECEIVE),
    ),
}


def allowed_actions(type_: ScanType, principal: Principal) -> list[ScanAction]:
    actions: list[ScanAction] = ["view"]
    actions.extend(
        action
        for action, perm in _RULES[type_]
        if all(principal.has(p) for p in ((perm,) if isinstance(perm, str) else perm))
    )
    return actions


async def _one(session: AsyncSession, stmt: Any) -> Any:
    """The single row `stmt` names, `None` for none, `ConflictProblem` for several."""
    rows = (await session.execute(stmt.limit(2))).scalars().all()
    if len(rows) > 1:
        raise ConflictProblem(
            "That code matches more than one record; scan the printed label (og:<type>:<code>)"
        )
    return rows[0] if rows else None


async def _find(
    session: AsyncSession, org_id: uuid.UUID, type_: ScanType, code: str
) -> InventoryItem | Lot | Roll | Location | None:
    found: Any = None
    if type_ == "item":
        found = await _one(
            session,
            select(InventoryItem).where(
                InventoryItem.organization_id == org_id,
                InventoryItem.deleted_at.is_(None),
                or_(InventoryItem.barcode == code, InventoryItem.sku == code),
            ),
        )
    elif type_ == "lot":
        found = await _one(
            session,
            select(Lot).where(
                Lot.organization_id == org_id, or_(Lot.barcode == code, Lot.lot_no == code)
            ),
        )
    elif type_ == "roll":
        found = await _one(
            session,
            select(Roll).where(
                Roll.organization_id == org_id, or_(Roll.barcode == code, Roll.roll_no == code)
            ),
        )
    else:
        found = await inv_selectors.get_location_by_barcode(session, org_id, code)
    return found  # type: ignore[no-any-return]


async def _entity(
    session: AsyncSession, org_id: uuid.UUID, type_: ScanType, row: Any
) -> ScanEntityOut:
    if type_ == "item":
        item: InventoryItem = row
        return ScanEntityOut(
            type="item",
            id=str(item.id),
            code=item.barcode or item.sku,
            title=item.name,
            subtitle=item.sku,
            uom=item.uom,
            on_hand_qty=format_qty(item.on_hand_milli),
            inventory_item_id=str(item.id),
            sku=item.sku,
            track_lot=item.track_lot,
            track_roll=item.track_roll,
            status="ACTIVE" if item.is_active else "INACTIVE",
        )
    if type_ == "lot":
        lot_row = await sel.get_lot_row(session, org_id, row.id)
        assert lot_row is not None
        lot, sku, uom, on_hand = lot_row
        return ScanEntityOut(
            type="lot",
            id=str(lot.id),
            code=lot.barcode or lot.lot_no,
            title=lot.lot_no,
            subtitle=sku,
            uom=uom,
            on_hand_qty=format_qty(on_hand),
            inventory_item_id=str(lot.inventory_item_id),
            sku=sku,
            lot_id=str(lot.id),
            lot_no=lot.lot_no,
            status=lot.quality_status,
        )
    if type_ == "roll":
        roll_row = await sel.get_roll_row(session, org_id, row.id)
        assert roll_row is not None
        roll, sku, uom, lot_no, on_hand = roll_row
        return ScanEntityOut(
            type="roll",
            id=str(roll.id),
            code=roll.barcode or roll.roll_no,
            title=roll.roll_no,
            subtitle=f"{sku} · {lot_no}" if lot_no else sku,
            uom=uom,
            on_hand_qty=format_qty(on_hand),
            inventory_item_id=str(roll.inventory_item_id),
            sku=sku,
            lot_id=str(roll.lot_id) if roll.lot_id else None,
            lot_no=lot_no,
            roll_id=str(roll.id),
            status=roll.state,
        )
    location: Location = row
    warehouse = (
        await session.execute(
            select(Warehouse.name).where(
                Warehouse.organization_id == org_id, Warehouse.id == location.warehouse_id
            )
        )
    ).scalar_one_or_none()
    return ScanEntityOut(
        type="location",
        id=str(location.id),
        code=location.barcode or location.code,
        title=location.name or location.code,
        subtitle=f"{warehouse} · {location.code}" if warehouse else location.code,
        location_id=str(location.id),
        warehouse_id=str(location.warehouse_id),
        status=location.kind,
    )


async def resolve(
    session: AsyncSession, org_id: uuid.UUID, principal: Principal, text: str
) -> ScanOut:
    try:
        parsed: ScanPayload = parse_payload(text)
    except ScanPayloadError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": "payload", "code": "invalid_payload", "message": str(exc)}]
        ) from exc

    candidates: list[tuple[ScanType, Any]] = []
    for type_ in [parsed.type] if parsed.type else SCAN_TYPES:
        row = await _find(session, org_id, type_, parsed.code)
        if row is not None:
            candidates.append((type_, row))
    if not candidates:
        raise NotFoundProblem("That code does not match anything in this organization")
    if len(candidates) > 1:
        raise ConflictProblem(
            "That code matches more than one kind of record; scan the printed label "
            "(og:<type>:<code>)"
        )
    type_, row = candidates[0]
    entity = await _entity(session, org_id, type_, row)
    return ScanOut(
        payload=format_payload(type_, entity.code),
        type=type_,
        entity=entity,
        allowed_actions=allowed_actions(type_, principal),
    )
