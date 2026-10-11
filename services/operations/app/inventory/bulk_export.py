"""Export every inventory list as rows of strings (inventory Phase 9, ADR-0027).

The same statements the list endpoints use — so an export can never show a caller more than the
list would — with the organization filter and the caller's scope applied by them, capped at
`MAX_EXPORT_ROWS`. Quantities are the exact decimal strings; money is integer minor units. The
import columns come first (an export is a usable starting layout for an import of the same
entity), then read-only ones (ids, derived quantities) which an import refuses as unknown columns.

A cell is never a formula: when the CSV is written, a string starting with `=`, `+`, `-`, `@`,
tab or carriage return is prefixed with an apostrophe — except a plain decimal number, which a
negative ledger quantity legitimately is.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.inventory import selectors as inv_selectors
from app.inventory import tracking_selectors as sel
from app.inventory.bulk_import import COLUMNS
from app.inventory.bulk_schemas import MAX_EXPORT_ROWS, ExportEntity, ExportOut
from app.models import (
    InventoryItem,
    ItemCategory,
    LedgerEntry,
    Location,
    Lot,
    Roll,
    Warehouse,
)
from platform_core.auth import ScopeContext

#: The read permission an export additionally needs — exporting never reveals what the list
#: would not.
READ_PERMISSION: dict[ExportEntity, str] = {
    "items": "operations.inventory.item.read",
    "locations": "operations.inventory.location.read",
    "lots": "operations.inventory.stock.read",
    "rolls": "operations.inventory.stock.read",
    "stock": "operations.inventory.stock.read",
    "ledger": "operations.inventory.stock.read",
}

_EXTRA: dict[ExportEntity, tuple[str, ...]] = {
    "items": ("id", "on_hand_qty", "reserved_qty", "available_qty"),
    "locations": ("id",),
    "lots": ("id", "on_hand_qty"),
    "rolls": ("id", "state", "on_hand_qty"),
    "stock": ("reserved_qty", "available_qty"),
    "ledger": (),
}


def _import_columns(entity: str) -> list[str]:
    return [c.name for c in COLUMNS[entity]]  # type: ignore[index]


COLUMNS_FOR: dict[ExportEntity, list[str]] = {
    "items": [*_import_columns("items"), *_EXTRA["items"]],
    "locations": [*_import_columns("locations"), *_EXTRA["locations"]],
    "lots": [*_import_columns("lots"), *_EXTRA["lots"]],
    "rolls": [*_import_columns("rolls"), *_EXTRA["rolls"]],
    "stock": [*_import_columns("opening_stock"), *_EXTRA["stock"]],
    "ledger": [
        "occurred_on",
        "move_id",
        "kind",
        "reason",
        "reference_type",
        "sku",
        "location_code",
        "status",
        "lot_id",
        "roll_id",
        "quantity",
        "balance_after",
        "note",
        "id",
    ],
}


def _s(value: Any) -> str:
    return "" if value is None else str(value)


async def export_rows(session: AsyncSession, ctx: ScopeContext, entity: ExportEntity) -> ExportOut:
    org = ctx.organization_id
    limit = MAX_EXPORT_ROWS + 1
    rows: list[dict[str, str]] = []

    if entity == "items":
        categories = {
            c.id: c.code
            for c in (
                await session.execute(
                    ctx.apply(
                        select(ItemCategory).where(
                            ItemCategory.organization_id == org, ItemCategory.deleted_at.is_(None)
                        ),
                        owner_col=None,
                    )
                )
            ).scalars()
        }
        items = (
            (await session.execute(inv_selectors.inventory_stmt(ctx).limit(limit))).scalars().all()
        )
        for item in items:
            item_out = inv_selectors.inventory_out(item)
            rows.append(
                {
                    "sku": item.sku,
                    "name": item.name,
                    "category": item.category,
                    "warehouse": item.warehouse,
                    "uom": item.uom,
                    "reorder_point": _s(item.reorder_point),
                    "unit_value_minor": _s(item.unit_value_minor),
                    "currency": item.currency,
                    "hsn_code": item.hsn_code,
                    "track_lot": _bool(item.track_lot),
                    "track_roll": _bool(item.track_roll),
                    "track_expiry": _bool(item.track_expiry),
                    "track_quality": _bool(item.track_quality),
                    "is_active": _bool(item.is_active),
                    "item_category_code": categories.get(item.item_category_id, "")
                    if item.item_category_id
                    else "",
                    "barcode": _s(item.barcode),
                    "selling_price_minor": _s(item.selling_price_minor),
                    "selling_price_currency": _s(item.selling_price_currency),
                    "id": str(item.id),
                    "on_hand_qty": item_out.on_hand_qty,
                    "reserved_qty": item_out.reserved_qty,
                    "available_qty": item_out.available_qty,
                }
            )
    elif entity == "locations":
        parent = aliased(Location)
        stmt = (
            ctx.apply(
                select(Location, Warehouse.code, parent.code)
                .join(Warehouse, Warehouse.id == Location.warehouse_id)
                .outerjoin(parent, parent.id == Location.parent_id)
                .where(
                    Location.organization_id == org,
                    Warehouse.organization_id == org,
                    Location.deleted_at.is_(None),
                ),
                owner_col=None,
            )
            .order_by(Warehouse.code.asc(), Location.code.asc())
            .limit(limit)
        )
        for loc, wh_code, parent_code in (await session.execute(stmt)).all():
            rows.append(
                {
                    "warehouse_code": wh_code,
                    "kind": loc.kind,
                    "code": loc.code,
                    "name": loc.name,
                    "parent_code": _s(parent_code),
                    "barcode": _s(loc.barcode),
                    "pick_sequence": _s(loc.pick_sequence),
                    "storage_type": _s(loc.storage_type),
                    "max_weight_grams": _s(loc.max_weight_grams),
                    "max_volume_milli": _s(loc.max_volume_milli),
                    "id": str(loc.id),
                }
            )
    elif entity == "lots":
        stmt = sel.lots_stmt(ctx).order_by(Lot.lot_no.asc()).limit(limit)
        for row in (await session.execute(stmt)).all():
            lot = row[0]
            lot_o = sel.lot_out(row)
            rows.append(
                {
                    "sku": lot_o.sku,
                    "lot_no": lot.lot_no,
                    "supplier_lot_no": _s(lot.supplier_lot_no),
                    "manufactured_on": _s(lot.manufactured_on),
                    "received_on": _s(lot.received_on),
                    "expires_on": _s(lot.expires_on),
                    "quality_status": lot.quality_status,
                    "barcode": _s(lot.barcode),
                    "id": str(lot.id),
                    "on_hand_qty": lot_o.on_hand_qty,
                }
            )
    elif entity == "rolls":
        stmt = sel.rolls_stmt(ctx).order_by(Roll.roll_no.asc()).limit(limit)
        for row in (await session.execute(stmt)).all():
            roll = row[0]
            roll_o = sel.roll_out(row)
            rows.append(
                {
                    "sku": roll_o.sku,
                    "primary_qty": roll_o.primary_qty,
                    "roll_no": roll.roll_no,
                    "lot_no": _s(roll_o.lot_no),
                    "secondary_qty": _s(roll_o.secondary_qty),
                    "secondary_uom": _s(roll_o.secondary_uom),
                    "width_mm": _s(roll.width_mm),
                    "gsm": _s(roll.gsm),
                    "shade": _s(roll.shade),
                    "grade": _s(roll.grade),
                    "barcode": _s(roll.barcode),
                    "id": str(roll.id),
                    "state": roll.state,
                    "on_hand_qty": roll_o.on_hand_qty,
                }
            )
    elif entity == "stock":
        stmt = (
            sel.stock_stmt(ctx)
            .order_by(InventoryItem.sku.asc(), Warehouse.code.asc(), Location.code.asc())
            .limit(limit)
        )
        for row in (await session.execute(stmt)).all():
            stock_o = sel.stock_row_out(row)
            rows.append(
                {
                    "sku": stock_o.sku,
                    "warehouse_code": stock_o.warehouse_code,
                    "location_code": stock_o.location_code,
                    "quantity": stock_o.on_hand_qty,
                    "lot_no": _s(stock_o.lot_no),
                    "roll_no": _s(stock_o.roll_no),
                    "status": stock_o.status,
                    "unit_cost_minor": "",
                    "unit_cost_currency": "",
                    "reserved_qty": stock_o.reserved_qty,
                    "available_qty": stock_o.available_qty,
                }
            )
    else:
        stmt = (
            sel.ledger_stmt(ctx)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(limit)
        )
        for row in (await session.execute(stmt)).all():
            led_o = sel.ledger_row_out(row)
            rows.append(
                {
                    "occurred_on": led_o.occurred_on.isoformat(),
                    "move_id": led_o.move_id,
                    "kind": led_o.kind,
                    "reason": _s(led_o.reason),
                    "reference_type": _s(led_o.reference_type),
                    "sku": led_o.sku,
                    "location_code": led_o.location_code,
                    "status": led_o.status,
                    "lot_id": _s(led_o.lot_id),
                    "roll_id": _s(led_o.roll_id),
                    "quantity": led_o.quantity,
                    "balance_after": led_o.balance_after,
                    "note": led_o.note,
                    "id": led_o.id,
                }
            )

    truncated = len(rows) > MAX_EXPORT_ROWS
    return ExportOut(
        entity=entity, columns=COLUMNS_FOR[entity], rows=rows[:MAX_EXPORT_ROWS], truncated=truncated
    )


def _bool(value: bool) -> str:
    return "true" if value else "false"


_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def csv_cell(value: str) -> str:
    """One CSV cell, never a formula."""
    if value and value[0] in "=+-@\t\r" and not _NUMBER.match(value):
        return "'" + value
    return value


def to_csv(columns: list[str], rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([csv_cell(row.get(c, "")) for c in columns])
    return buffer.getvalue()


__all__ = ["COLUMNS_FOR", "READ_PERMISSION", "csv_cell", "export_rows", "to_csv"]
