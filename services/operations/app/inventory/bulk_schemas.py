"""Wire contracts for the scan resolver and the bulk import / export (inventory Phase 9,
ADR-0027). Every quantity is a decimal string, every cell a string — a spreadsheet's number
formatting never reaches the server, and the server never parses a float."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.inventory.barcode import ScanType

ScanAction = Literal[
    "view", "transfer", "status_change", "count", "receive", "pick", "issue", "inspect"
]

ImportEntity = Literal["items", "locations", "lots", "rolls", "opening_stock"]
ExportEntity = Literal["items", "locations", "lots", "rolls", "stock", "ledger"]
OnDuplicate = Literal["fail", "skip"]
ImportMode = Literal["preview", "commit"]

#: The most rows one import may carry. Every row is one pass through the same write path the
#: single-record endpoints use, so this bounds a request's work; a bigger file is split.
MAX_IMPORT_ROWS = 5000
#: The most rows one export returns; `truncated` says when the list was longer.
MAX_EXPORT_ROWS = 50_000


class ScanEntityOut(BaseModel):
    """What a scan resolved to — deliberately slim. It is enough to show the person what they are
    holding and to start an action; it carries no cost, price or supplier data (a storekeeper
    with `stock.read` may scan, they may not read the item master)."""

    type: ScanType
    id: str
    #: The code the label carries (barcode when set, natural key otherwise).
    code: str
    title: str
    subtitle: str = ""
    uom: str | None = None
    #: Exact decimal string, for item / lot / roll; `null` for a location.
    on_hand_qty: str | None = None
    inventory_item_id: str | None = None
    sku: str | None = None
    lot_id: str | None = None
    lot_no: str | None = None
    roll_id: str | None = None
    location_id: str | None = None
    warehouse_id: str | None = None
    track_lot: bool = False
    track_roll: bool = False
    status: str | None = None


class ScanOut(BaseModel):
    payload: str
    type: ScanType
    entity: ScanEntityOut
    #: What *this caller* may do with it (their permissions, the entity's type) — a UI affordance,
    #: never an authorization: every action's endpoint checks its own permission.
    allowed_actions: list[ScanAction]


class ImportColumnOut(BaseModel):
    name: str
    required: bool
    description: str
    example: str


class ImportTemplateOut(BaseModel):
    entity: ImportEntity
    max_rows: int
    columns: list[ImportColumnOut]


class ImportIn(BaseModel):
    """`POST /inventory/data/import/{entity}`. `rows` are the file's data rows, header-keyed,
    every value a string (an empty cell is `""` or `null`)."""

    model_config = ConfigDict(extra="forbid")

    mode: ImportMode = "preview"
    #: `fail` (default): a record that already exists is a row error and stops the import.
    #: `skip`: it is left alone and reported as skipped; the rest still commit.
    on_duplicate: OnDuplicate = "fail"
    rows: list[dict[str, str | None]] = Field(min_length=1, max_length=MAX_IMPORT_ROWS)
    #: Free text recorded on the run (the file name), never interpreted.
    source_name: str | None = Field(default=None, max_length=200)


class ImportRowError(BaseModel):
    #: 1-based data row (the first row after the header is 1).
    row: int
    field: str | None = None
    code: str
    message: str


class ImportRowResult(BaseModel):
    row: int
    status: Literal["ok", "skipped", "error"]
    #: The natural key of the record (SKU, lot number, ...), when the row had one.
    key: str | None = None


class ImportResultOut(BaseModel):
    entity: ImportEntity
    mode: ImportMode
    #: `true` only for a commit that wrote everything. A preview, and a commit that was refused,
    #: write nothing.
    committed: bool
    replayed: bool = False
    run_id: str | None = None
    total: int
    created: int
    skipped: int
    error_count: int
    #: First 500 errors; `error_count` is the whole number.
    errors: list[ImportRowError]
    rows: list[ImportRowResult]


class ExportOut(BaseModel):
    entity: ExportEntity
    columns: list[str]
    rows: list[dict[str, str]]
    truncated: bool
