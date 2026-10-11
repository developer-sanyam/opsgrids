"""All-or-nothing bulk import (inventory Phase 9, ADR-0027).

An import is **the existing create paths, called once per row, inside one transaction**: items go
through `create_inventory_item`, locations through `create_location`, lots and rolls through
`tracking.create_lot` / `create_roll`, opening stock through `stock_ops.post_opening_stock` — so
every rule, audit row and outbox event those paths already own applies unchanged, and **stock only
ever moves through `post_move`**. This module adds the file-shaped part: reading a row, naming its
problems by row and column, detecting duplicates, and refusing to commit anything unless every row
is good.

Mechanics. Each row runs in its own SAVEPOINT, so a bad row reports its error without poisoning
the rows after it and the whole file is checked in one pass. The rows as a whole run inside an
outer SAVEPOINT that is **rolled back** unless the mode is `commit` *and* no row failed — which is
what makes a preview exact (it ran the real writes, then undid them) and a refused commit leave no
trace: no items, no ledger rows, no audit rows, no outbox events.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory import stock_ops
from app.inventory import tracking_selectors as sel
from app.inventory import tracking_services as tracking
from app.inventory.bulk_schemas import (
    MAX_IMPORT_ROWS,
    ImportColumnOut,
    ImportEntity,
    ImportIn,
    ImportResultOut,
    ImportRowError,
    ImportRowResult,
    ImportTemplateOut,
    OnDuplicate,
)
from app.inventory.tracking_schemas import (
    LotIn,
    OpeningLineIn,
    OpeningStockIn,
    RollIn,
)
from app.models import (
    InventoryBalance,
    InventoryImportRun,
    InventoryItem,
    Lot,
    Roll,
)
from app.schemas import InventoryItemIn, LocationIn
from platform_core.auth import ScopeContext
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    ValidationProblem,
)

EVT_IMPORT_COMMITTED = "operations.inventory.import.committed"
#: Errors returned to the client; `error_count` always carries the full number.
MAX_REPORTED_ERRORS = 500
#: `OpeningStockIn` takes at most 100 lines per move.
OPENING_CHUNK = 100

Row = dict[str, str | None]


# --------------------------------------------------------------------------- column definitions


@dataclass(frozen=True)
class Col:
    name: str
    required: bool
    description: str
    example: str


def _c(name: str, required: bool, description: str, example: str) -> Col:
    return Col(name, required, description, example)


COLUMNS: dict[ImportEntity, tuple[Col, ...]] = {
    "items": (
        _c("sku", True, "Unique per organization: letters, digits, . _ -", "FAB-001"),
        _c("name", True, "Item name", "Cotton poplin 58in"),
        _c(
            "category",
            True,
            "raw_materials, packaging, components, mro, logistics or services",
            "raw_materials",
        ),
        _c("warehouse", True, "Name of an existing warehouse (the item's home)", "Weaving shed"),
        _c("uom", True, "Unit of measure code", "m"),
        _c("reorder_point", False, "Whole units; default 0", "100"),
        _c(
            "unit_value_minor",
            False,
            "Cost per unit, integer minor units (paise); default 0",
            "12500",
        ),
        _c("currency", False, "ISO currency of unit_value_minor; default INR", "INR"),
        _c("hsn_code", False, "4, 6 or 8 digits", "5208"),
        _c("track_lot", False, "true/false", "true"),
        _c("track_roll", False, "true/false", "true"),
        _c("track_expiry", False, "true/false", "false"),
        _c("track_quality", False, "true/false", "false"),
        _c("is_active", False, "true/false; default true", "true"),
        _c("item_category_code", False, "Code of an existing item category", "FABRIC"),
        _c(
            "barcode",
            False,
            "Printed / manufacturer code, unique; default: the SKU",
            "8901234567890",
        ),
        _c(
            "selling_price_minor",
            False,
            "Integer minor units; give with selling_price_currency",
            "18000",
        ),
        _c("selling_price_currency", False, "ISO currency", "INR"),
    ),
    "locations": (
        _c("warehouse_code", True, "Code of an existing warehouse", "WH-WEAVE"),
        _c("kind", True, "zone, aisle, rack, shelf, bin, staging, ...", "bin"),
        _c("code", True, "Unique within the warehouse: letters, digits, _ -", "A-01-03"),
        _c("name", False, "Defaults to the code", "Aisle A rack 1 bin 3"),
        _c(
            "parent_code",
            False,
            "Code of a location in the same warehouse (earlier in this file is fine)",
            "A-01",
        ),
        _c("barcode", False, "Unique across the organization; no spaces or /", "WH1-A-01-03"),
        _c("pick_sequence", False, "Whole number", "30"),
        _c("storage_type", False, "Free text", "rack"),
        _c("max_weight_grams", False, "Whole grams", "500000"),
        _c("max_volume_milli", False, "Whole milli-litres", "2000000"),
    ),
    "lots": (
        _c("sku", True, "SKU of a lot-tracked item", "FAB-001"),
        _c("lot_no", False, "Generated when blank; unique", "LOT-2026-10"),
        _c("supplier_lot_no", False, "As printed by the supplier", "S-4471"),
        _c("manufactured_on", False, "YYYY-MM-DD", "2026-09-01"),
        _c("received_on", False, "YYYY-MM-DD", "2026-09-20"),
        _c("expires_on", False, "YYYY-MM-DD", ""),
        _c("quality_status", False, "PENDING, APPROVED, ON_HOLD or REJECTED", "APPROVED"),
        _c("barcode", False, "A code someone else printed; unique", "SUP-LOT-4471"),
    ),
    "rolls": (
        _c("sku", True, "SKU of a roll-tracked item", "FAB-001"),
        _c("primary_qty", True, "Decimal string in the item's unit, up to 3 places", "82.5"),
        _c("roll_no", False, "Generated when blank; unique", "R-0001"),
        _c("lot_no", False, "An existing lot of the same item", "LOT-2026-10"),
        _c("secondary_qty", False, "Measured second quantity", "14.8"),
        _c("secondary_uom", False, "Unit of secondary_qty", "kg"),
        _c("width_mm", False, "Whole millimetres", "1500"),
        _c("gsm", False, "Whole number", "180"),
        _c("shade", False, "Free text", "NAVY"),
        _c("grade", False, "Free text", "A"),
        _c("barcode", False, "A code someone else printed; unique", "MILL-R-0001"),
    ),
    "opening_stock": (
        _c("sku", True, "SKU of an existing item", "FAB-001"),
        _c("warehouse_code", True, "Code of the warehouse the location is in", "WH-WEAVE"),
        _c("location_code", True, "Code of a location in that warehouse", "A-01-03"),
        _c("quantity", True, "Decimal string in the item's unit, up to 3 places, > 0", "82.5"),
        _c("lot_no", False, "Required for a lot-tracked item", "LOT-2026-10"),
        _c("roll_no", False, "Required for a roll-tracked item", "R-0001"),
        _c("status", False, "AVAILABLE (default), QUALITY_HOLD, QUARANTINE, ...", "AVAILABLE"),
        _c("unit_cost_minor", False, "Integer minor units; give with unit_cost_currency", "12500"),
        _c("unit_cost_currency", False, "ISO currency", "INR"),
    ),
}

#: The permission an import additionally needs, per entity: `data.import` never grants more than
#: the entity's own write permission.
WRITE_PERMISSION: dict[ImportEntity, str] = {
    "items": "operations.inventory.item.write",
    "locations": "operations.inventory.location.write",
    "lots": "operations.inventory.lot.write",
    "rolls": "operations.inventory.roll.write",
    "opening_stock": "operations.inventory.stock.adjust",
}


def template(entity: ImportEntity) -> ImportTemplateOut:
    return ImportTemplateOut(
        entity=entity,
        max_rows=MAX_IMPORT_ROWS,
        columns=[
            ImportColumnOut(
                name=c.name, required=c.required, description=c.description, example=c.example
            )
            for c in COLUMNS[entity]
        ],
    )


# --------------------------------------------------------------------------- row reading


class RowFailure(Exception):
    """A row the importer refuses, named by column and code."""

    def __init__(self, field: str | None, code: str, message: str) -> None:
        super().__init__(message)
        self.field, self.code, self.message = field, code, message


class RowDuplicate(RowFailure):
    """The record this row would create already exists. An error, unless `on_duplicate=skip`."""

    def __init__(self, field: str, message: str, key: str | None = None) -> None:
        super().__init__(field, "already_exists", message)
        self.key = key


def _clean(row: Row) -> dict[str, str]:
    return {k: v.strip() for k, v in row.items() if v is not None and v.strip() != ""}


def _need(row: dict[str, str], *names: str) -> None:
    for name in names:
        if name not in row:
            raise RowFailure(name, "required", f"{name} is required")


_BOOL = {
    "true": True,
    "yes": True,
    "y": True,
    "1": True,
    "false": False,
    "no": False,
    "n": False,
    "0": False,
}


def _bool(row: dict[str, str], name: str, default: bool) -> bool:
    if name not in row:
        return default
    try:
        return _BOOL[row[name].lower()]
    except KeyError:
        raise RowFailure(name, "invalid_boolean", f"{name} must be true or false") from None


INT32_MAX = 2**31 - 1


def _int(row: dict[str, str], name: str, maximum: int | None = None) -> int | None:
    if name not in row:
        return None
    if not re.fullmatch(r"\d{1,15}", row[name]):
        raise RowFailure(
            name, "invalid_integer", f"{name} must be a whole number, not {row[name]!r}"
        )
    value = int(row[name])
    if maximum is not None and value > maximum:
        raise RowFailure(name, "out_of_range", f"{name} must be at most {maximum}")
    return value


def _date(row: dict[str, str], name: str) -> date | None:
    if name not in row:
        return None
    try:
        return date.fromisoformat(row[name])
    except ValueError:
        raise RowFailure(
            name, "invalid_date", f"{name} must be YYYY-MM-DD, not {row[name]!r}"
        ) from None


def _build[M: BaseModel](model: type[M], values: dict[str, Any]) -> M:
    try:
        return model.model_validate(values)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or None
        raise RowFailure(field, "invalid_value", f"{field}: {first['msg']}") from None


def _problem_failure(exc: ProblemError) -> RowFailure:
    first = (exc.errors or [{}])[0]
    return RowFailure(first.get("field"), first.get("code") or exc.problem_type, exc.detail)


# --------------------------------------------------------------------------- handlers

Handler = Callable[
    [AsyncSession, ScopeContext, dict[str, str], Request | None], Awaitable[str | None]
]


async def _item_by_sku(
    session: AsyncSession, org_id: uuid.UUID, sku: str, field: str = "sku"
) -> InventoryItem:
    item = await inv_selectors.get_inventory_by_sku(session, org_id, sku)
    if item is None:
        raise RowFailure(field, "unknown_sku", f"No item with SKU {sku!r}")
    return item


async def _warehouse_by_code(session: AsyncSession, org_id: uuid.UUID, code: str):  # type: ignore[no-untyped-def]
    warehouse = await inv_selectors.get_warehouse_by_code(session, org_id, code)
    if warehouse is None:
        raise RowFailure("warehouse_code", "unknown_warehouse", f"No warehouse coded {code!r}")
    return warehouse


async def _import_item(
    session: AsyncSession, ctx: ScopeContext, row: dict[str, str], request: Request | None
) -> str | None:
    _need(row, "sku", "name", "category", "warehouse", "uom")
    org = ctx.organization_id
    if await inv_selectors.get_inventory_by_sku(session, org, row["sku"]):
        raise RowDuplicate("sku", f"An item with SKU {row['sku']!r} already exists", row["sku"])
    category_id: str | None = None
    if "item_category_code" in row:
        category = await inv_selectors.get_item_category_by_code(
            session, org, row["item_category_code"]
        )
        if category is None:
            raise RowFailure(
                "item_category_code",
                "unknown_category",
                f"No item category coded {row['item_category_code']!r}",
            )
        category_id = str(category.id)
    values: dict[str, Any] = {
        "sku": row["sku"],
        "name": row["name"],
        "category": row["category"],
        "warehouse": row["warehouse"],
        "uom": row["uom"],
        "reorder_point": _int(row, "reorder_point", INT32_MAX) or 0,
        "unit_value_minor": _int(row, "unit_value_minor") or 0,
        "currency": row.get("currency", "INR"),
        "hsn_code": row.get("hsn_code", ""),
        "track_lot": _bool(row, "track_lot", False),
        "track_roll": _bool(row, "track_roll", False),
        "track_expiry": _bool(row, "track_expiry", False),
        "track_quality": _bool(row, "track_quality", False),
        "is_active": _bool(row, "is_active", True),
        "item_category_id": category_id,
        "barcode": row.get("barcode"),
        "selling_price_minor": _int(row, "selling_price_minor"),
        "selling_price_currency": row.get("selling_price_currency"),
    }
    payload = _build(InventoryItemIn, values)
    if await inv_selectors.get_warehouse_by_name(session, org, payload.warehouse) is None:
        raise RowFailure(
            "warehouse", "unknown_warehouse", f"No warehouse named {payload.warehouse!r}"
        )
    await inv_services.create_inventory_item(
        session,
        ctx,
        sku=payload.sku,
        name=payload.name,
        category=payload.category,
        warehouse=payload.warehouse,
        on_hand=0,
        reorder_point=payload.reorder_point,
        uom=payload.uom,
        unit_value_minor=payload.unit_value_minor,
        currency=payload.currency,
        item_category_id=payload.item_category_id,
        secondary_uom_id=None,
        hsn_code=payload.hsn_code,
        track_lot=payload.track_lot,
        track_roll=payload.track_roll,
        track_expiry=payload.track_expiry,
        track_quality=payload.track_quality,
        is_active=payload.is_active,
        selling_price_minor=payload.selling_price_minor,
        selling_price_currency=payload.selling_price_currency,
        barcode=payload.barcode,
        request=request,
    )
    return payload.sku


async def _import_location(
    session: AsyncSession, ctx: ScopeContext, row: dict[str, str], request: Request | None
) -> str | None:
    _need(row, "warehouse_code", "kind", "code")
    org = ctx.organization_id
    warehouse = await _warehouse_by_code(session, org, row["warehouse_code"])
    key = f"{row['warehouse_code']}/{row['code']}"
    if await inv_selectors.get_location_by_code(session, org, warehouse.id, row["code"]):
        raise RowDuplicate("code", f"{key} already exists", key)
    parent_id: str | None = None
    if "parent_code" in row:
        parent = await inv_selectors.get_location_by_code(
            session, org, warehouse.id, row["parent_code"]
        )
        if parent is None:
            raise RowFailure(
                "parent_code",
                "unknown_parent",
                f"No location coded {row['parent_code']!r} in {row['warehouse_code']}",
            )
        parent_id = str(parent.id)
    payload = _build(
        LocationIn,
        {
            "kind": row["kind"],
            "code": row["code"],
            "name": row.get("name", row["code"]),
            "parent_id": parent_id,
            "barcode": row.get("barcode"),
            "pick_sequence": _int(row, "pick_sequence", INT32_MAX),
            "max_weight_grams": _int(row, "max_weight_grams"),
            "max_volume_milli": _int(row, "max_volume_milli"),
            "storage_type": row.get("storage_type"),
        },
    )
    await inv_services.create_location(
        session,
        ctx,
        warehouse,
        kind=payload.kind,
        code=payload.code,
        name=payload.name,
        parent_id=payload.parent_id,
        barcode=payload.barcode,
        pick_sequence=payload.pick_sequence,
        max_weight_grams=payload.max_weight_grams,
        max_volume_milli=payload.max_volume_milli,
        storage_type=payload.storage_type,
        request=request,
    )
    return key


async def _import_lot(
    session: AsyncSession, ctx: ScopeContext, row: dict[str, str], request: Request | None
) -> str | None:
    _need(row, "sku")
    org = ctx.organization_id
    item = await _item_by_sku(session, org, row["sku"])
    if "lot_no" in row and await sel.get_lot_by_no(session, org, row["lot_no"]):
        raise RowDuplicate("lot_no", f"Lot {row['lot_no']!r} already exists", row["lot_no"])
    payload = _build(
        LotIn,
        {
            "inventory_item_id": item.id,
            "lot_no": row.get("lot_no"),
            "supplier_lot_no": row.get("supplier_lot_no"),
            "manufactured_on": _date(row, "manufactured_on"),
            "received_on": _date(row, "received_on"),
            "expires_on": _date(row, "expires_on"),
            "quality_status": row.get("quality_status", "PENDING"),
            "barcode": row.get("barcode"),
        },
    )
    lot = await tracking.create_lot(session, ctx, payload, request=request)
    return lot.lot_no


async def _import_roll(
    session: AsyncSession, ctx: ScopeContext, row: dict[str, str], request: Request | None
) -> str | None:
    _need(row, "sku", "primary_qty")
    org = ctx.organization_id
    item = await _item_by_sku(session, org, row["sku"])
    if "roll_no" in row and await sel.get_roll_by_no(session, org, row["roll_no"]):
        raise RowDuplicate("roll_no", f"Roll {row['roll_no']!r} already exists", row["roll_no"])
    lot_id: uuid.UUID | None = None
    if "lot_no" in row:
        lot = await sel.get_lot_by_no(session, org, row["lot_no"])
        if lot is None:
            raise RowFailure("lot_no", "unknown_lot", f"No lot numbered {row['lot_no']!r}")
        lot_id = lot.id
    payload = _build(
        RollIn,
        {
            "inventory_item_id": item.id,
            "lot_id": lot_id,
            "roll_no": row.get("roll_no"),
            "primary_qty": row["primary_qty"],
            "secondary_qty": row.get("secondary_qty"),
            "secondary_uom": row.get("secondary_uom"),
            "width_mm": _int(row, "width_mm", INT32_MAX),
            "gsm": _int(row, "gsm", INT32_MAX),
            "shade": row.get("shade"),
            "grade": row.get("grade"),
            "barcode": row.get("barcode"),
        },
    )
    roll = await tracking.create_roll(session, ctx, payload, request=request)
    return roll.roll_no


HANDLERS: dict[str, Handler] = {
    "items": _import_item,
    "locations": _import_location,
    "lots": _import_lot,
    "rolls": _import_roll,
}


@dataclass
class _Line:
    row_no: int
    key: str
    line: OpeningLineIn


async def _precheck_opening(
    session: AsyncSession, ctx: ScopeContext, row: dict[str, str]
) -> tuple[OpeningLineIn, str, tuple[Any, ...]]:
    """Validate one opening-stock row without writing; returns the line, its bucket label and
    the bucket key (to catch two rows naming one bucket)."""
    _need(row, "sku", "warehouse_code", "location_code", "quantity")
    org = ctx.organization_id
    item = await _item_by_sku(session, org, row["sku"])
    warehouse = await _warehouse_by_code(session, org, row["warehouse_code"])
    location = await inv_selectors.get_location_by_code(
        session, org, warehouse.id, row["location_code"]
    )
    if location is None:
        raise RowFailure(
            "location_code",
            "unknown_location",
            f"No location coded {row['location_code']!r} in {row['warehouse_code']}",
        )
    lot: Lot | None = None
    roll: Roll | None = None
    if "lot_no" in row:
        lot = await sel.get_lot_by_no(session, org, row["lot_no"])
        if lot is None:
            raise RowFailure("lot_no", "unknown_lot", f"No lot numbered {row['lot_no']!r}")
        if lot.inventory_item_id != item.id:
            raise RowFailure(
                "lot_no", "lot_item_mismatch", f"Lot {lot.lot_no} belongs to another item"
            )
    if "roll_no" in row:
        roll = await sel.get_roll_by_no(session, org, row["roll_no"])
        if roll is None:
            raise RowFailure("roll_no", "unknown_roll", f"No roll numbered {row['roll_no']!r}")
        if roll.inventory_item_id != item.id:
            raise RowFailure(
                "roll_no", "roll_item_mismatch", f"Roll {roll.roll_no} belongs to another item"
            )
        if lot is not None and roll.lot_id != lot.id:
            raise RowFailure(
                "roll_no", "roll_lot_mismatch", f"Roll {roll.roll_no} is not in lot {lot.lot_no}"
            )
    values = {
        "inventory_item_id": item.id,
        "location_id": location.id,
        "quantity": row["quantity"],
        "status": row.get("status", "AVAILABLE"),
        "lot_id": lot.id if lot else None,
        "roll_id": roll.id if roll else None,
        "unit_cost_minor": _int(row, "unit_cost_minor"),
        "unit_cost_currency": row.get("unit_cost_currency"),
    }
    line = _build(OpeningLineIn, values)
    try:
        stock_ops.check_tracking(item, line.lot_id, line.roll_id)
        stock_ops.parse_positive(line.quantity, item.uom, "quantity")
    except ProblemError as exc:
        raise _problem_failure(exc) from exc
    bucket = (item.id, location.id, line.status, line.lot_id, line.roll_id)
    label = f"{row['sku']} @ {row['warehouse_code']}/{row['location_code']}"
    return line, label, bucket


async def _bucket_has_stock(
    session: AsyncSession, org_id: uuid.UUID, bucket: tuple[Any, ...]
) -> bool:
    item_id, location_id, status, lot_id, roll_id = bucket
    stmt = select(InventoryBalance.id).where(
        InventoryBalance.organization_id == org_id,
        InventoryBalance.inventory_item_id == item_id,
        InventoryBalance.location_id == location_id,
        InventoryBalance.status == status,
        InventoryBalance.lot_id.is_(lot_id)
        if lot_id is None
        else InventoryBalance.lot_id == lot_id,
        InventoryBalance.roll_id.is_(roll_id)
        if roll_id is None
        else InventoryBalance.roll_id == roll_id,
        InventoryBalance.quantity_milli != 0,
    )
    return (await session.execute(stmt.limit(1))).first() is not None


# --------------------------------------------------------------------------- the run


def _natural_key(entity: ImportEntity, row: dict[str, str]) -> str | None:
    if entity == "items":
        return row.get("sku")
    if entity == "locations":
        return f"{row.get('warehouse_code', '')}/{row['code']}" if "code" in row else None
    if entity == "lots":
        return row.get("lot_no")
    if entity == "rolls":
        return row.get("roll_no")
    if all(k in row for k in ("sku", "warehouse_code", "location_code")):
        return "|".join(
            (
                row["sku"],
                row["warehouse_code"],
                row["location_code"],
                row.get("lot_no", ""),
                row.get("roll_no", ""),
                row.get("status", "AVAILABLE"),
            )
        )
    return None


def _barcode_keys(entity: ImportEntity, row: dict[str, str]) -> list[str]:
    """Columns that must be unique across the file, as `entity:value` strings."""
    if entity in ("items", "locations", "lots", "rolls") and "barcode" in row:
        return [row["barcode"]]
    return []


def request_hash(payload: ImportIn, entity: ImportEntity) -> str:
    canonical = json.dumps(
        {"entity": entity, "on_duplicate": payload.on_duplicate, "rows": payload.rows},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _reject_unknown_columns(entity: ImportEntity, rows: list[Row]) -> None:
    known = {c.name for c in COLUMNS[entity]}
    unknown = sorted({k for r in rows for k in r} - known)
    if unknown:
        raise ValidationProblem(
            f"Unknown column(s) for {entity}: {', '.join(unknown)}",
            errors=[
                {
                    "field": name,
                    "code": "unknown_column",
                    "message": f"{name} is not a {entity} column",
                }
                for name in unknown
            ],
        )


async def run_import(
    session: AsyncSession,
    ctx: ScopeContext,
    entity: ImportEntity,
    payload: ImportIn,
    *,
    idempotency_key: str | None,
    request: Request | None = None,
) -> ImportResultOut:
    org = ctx.organization_id
    commit = payload.mode == "commit"
    _reject_unknown_columns(entity, payload.rows)
    digest = request_hash(payload, entity)

    # Two imports into one organization never interleave: their duplicate checks would pass
    # side by side and both commit. Taken before the replay lookup so a retry that queued behind
    # the first attempt sees its committed run instead of re-running the file.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"opsgrids:inventory-import:{org}"},
    )

    if commit:
        if not idempotency_key:
            raise ValidationProblem(
                "An Idempotency-Key header is required to commit an import",
                errors=[
                    {
                        "field": "Idempotency-Key",
                        "code": "idempotency_key_required",
                        "message": "send one key per import you mean to make",
                    }
                ],
            )
        prior = (
            await session.execute(
                select(InventoryImportRun).where(
                    InventoryImportRun.organization_id == org,
                    InventoryImportRun.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if prior is not None:
            if prior.request_hash != digest:
                raise ConflictProblem(
                    "That Idempotency-Key was already used for a different import"
                )
            return ImportResultOut(**{**prior.summary, "replayed": True})

    rows = [_clean(r) for r in payload.rows]
    errors: list[ImportRowError] = []
    results: list[ImportRowResult] = []
    created = skipped = 0

    seen_keys: dict[str, int] = {}
    seen_barcodes: dict[str, int] = {}

    def fail(row_no: int, failure: RowFailure, key: str | None) -> None:
        errors.append(
            ImportRowError(
                row=row_no, field=failure.field, code=failure.code, message=failure.message
            )
        )
        results.append(ImportRowResult(row=row_no, status="error", key=key))

    outer = await session.begin_nested()
    opening: list[_Line] = []
    opening_buckets: dict[tuple[Any, ...], int] = {}
    try:
        for index, row in enumerate(rows, start=1):
            key = _natural_key(entity, row)
            # --- duplicates inside the file: always an error, whatever on_duplicate says.
            if key is not None and key in seen_keys:
                fail(
                    index,
                    RowFailure(
                        None,
                        "duplicate_in_file",
                        f"{key} also appears in row {seen_keys[key]}",
                    ),
                    key,
                )
                continue
            dup_barcode = next((b for b in _barcode_keys(entity, row) if b in seen_barcodes), None)
            if dup_barcode is not None:
                fail(
                    index,
                    RowFailure(
                        "barcode",
                        "duplicate_in_file",
                        f"Barcode {dup_barcode} also appears in row {seen_barcodes[dup_barcode]}",
                    ),
                    key,
                )
                continue
            if key is not None:
                seen_keys[key] = index
            for b in _barcode_keys(entity, row):
                seen_barcodes[b] = index

            nested = await session.begin_nested()
            try:
                if entity == "opening_stock":
                    line, label, bucket = await _precheck_opening(session, ctx, row)
                    if bucket in opening_buckets:
                        raise RowFailure(
                            None,
                            "duplicate_in_file",
                            f"{label} is the same bucket as row {opening_buckets[bucket]}",
                        )
                    if await _bucket_has_stock(session, org, bucket):
                        raise RowDuplicate(
                            "quantity",
                            f"{label} already holds stock; opening stock only fills an empty "
                            "bucket",
                            label,
                        )
                    opening_buckets[bucket] = index
                    opening.append(_Line(index, label, line))
                    result_key: str | None = label
                else:
                    result_key = await HANDLERS[entity](session, ctx, row, request)
                await nested.commit()
                created += 1
                results.append(ImportRowResult(row=index, status="ok", key=result_key or key))
            except RowDuplicate as dup:
                await nested.rollback()
                if payload.on_duplicate == "skip":
                    skipped += 1
                    results.append(ImportRowResult(row=index, status="skipped", key=dup.key or key))
                else:
                    fail(index, dup, key)
            except RowFailure as failure:
                await nested.rollback()
                fail(index, failure, key)
            except ProblemError as exc:
                await nested.rollback()
                fail(index, _problem_failure(exc), key)
            except IntegrityError:
                await nested.rollback()
                fail(
                    index,
                    RowFailure(None, "conflict", "The row conflicts with an existing record"),
                    key,
                )

        # --- opening stock posts its validated lines through the kernel, in moves of <= 100.
        if entity == "opening_stock" and opening:
            lines = opening
            digest_key = hashlib.sha256((idempotency_key or "preview").encode()).hexdigest()[:32]
            for start in range(0, len(lines), OPENING_CHUNK):
                chunk = lines[start : start + OPENING_CHUNK]
                nested = await session.begin_nested()
                try:
                    await stock_ops.post_opening_stock(
                        session,
                        ctx,
                        OpeningStockIn(
                            lines=[c.line for c in chunk],
                            note=(payload.source_name or "bulk import")[:500],
                        ),
                        idempotency_key=f"import:{digest_key}:{start // OPENING_CHUNK}"
                        if commit
                        else None,
                        occurred_on_explicit=False,
                        request=request,
                        fresh_buckets_only=True,  # the same rule as the single-record route
                    )
                    await nested.commit()
                except (ProblemError, IntegrityError) as exc:
                    await nested.rollback()
                    detail = exc.detail if isinstance(exc, ProblemError) else "conflict"
                    created -= len(chunk)
                    refusal = RowFailure(
                        None,
                        "kernel_refused",
                        f"Rows {chunk[0].row_no}-{chunk[-1].row_no} were refused: {detail}",
                    )
                    errors.append(
                        ImportRowError(
                            row=chunk[0].row_no,
                            field=None,
                            code=refusal.code,
                            message=refusal.message,
                        )
                    )
                    for c in chunk:
                        for r in results:
                            if r.row == c.row_no:
                                r.status = "error"
        refused = bool(errors)
        if commit and not refused:
            run_id = uuid.uuid4()
            result = ImportResultOut(
                entity=entity,
                mode="commit",
                committed=True,
                run_id=str(run_id),
                total=len(rows),
                created=created,
                skipped=skipped,
                error_count=0,
                errors=[],
                rows=results,
            )
            # Append-only table: the replay body is complete at insert (counts, not the per-row
            # list — up to 5000 entries per run).
            session.add(
                InventoryImportRun(
                    id=run_id,
                    organization_id=org,
                    idempotency_key=idempotency_key,
                    request_hash=digest,
                    entity=entity,
                    on_duplicate=payload.on_duplicate,
                    row_count=len(rows),
                    created_count=created,
                    skipped_count=skipped,
                    summary=result.model_copy(update={"rows": []}).model_dump(mode="json"),
                    created_by=str(ctx.principal.sub),
                )
            )
            await session.flush()
            stock_ops.stage(
                session,
                ctx,
                action="inventory.import.committed",
                event_type=EVT_IMPORT_COMMITTED,
                entity="inventory_import_run",
                entity_id=run_id,
                after={
                    "entity": entity,
                    "rows": len(rows),
                    "created": created,
                    "skipped": skipped,
                    "on_duplicate": payload.on_duplicate,
                    "source_name": payload.source_name,
                },
                payload={
                    "run_id": str(run_id),
                    "entity": entity,
                    "created": created,
                    "skipped": skipped,
                },
                reason=payload.source_name,
                request=request,
            )
            await outer.commit()
            return result
    except BaseException:
        if outer.is_active:
            await outer.rollback()
        raise

    # Preview, or a commit that found a problem: undo everything the rows wrote.
    await outer.rollback()
    return ImportResultOut(
        entity=entity,
        mode=payload.mode,
        committed=False,
        total=len(rows),
        created=created,
        skipped=skipped,
        error_count=len(errors),
        errors=errors[:MAX_REPORTED_ERRORS],
        rows=sorted(results, key=lambda r: r.row),
    )


def refused(result: ImportResultOut) -> ValidationProblem:
    """The 422 a commit with errors answers: nothing was imported, here is every reason."""
    n = result.error_count
    return ValidationProblem(
        f"Nothing was imported: {n} row{'s' if n != 1 else ''} had problems. "
        "Fix them and commit again.",
        errors=[e.model_dump() for e in result.errors],
    )


__all__ = [
    "COLUMNS",
    "WRITE_PERMISSION",
    "NotFoundProblem",
    "OnDuplicate",
    "refused",
    "run_import",
    "template",
]
