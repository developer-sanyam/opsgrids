"""Request and response contracts for the operations service.

This module is the published API surface: a change here is a change to the OpenAPI contract, and
the Operations SPA generates its client from it. The field names, nesting and enums mirror
`frontend/product-operations/src/api/types.ts` exactly, so wiring the SPA from fixtures to live
is a flag flip rather than a rewrite.

Money is integer minor units (`*_minor`) in an ISO `currency`, never a float (ADR-0000 §8). A
purchase order's total is **derived from its lines** and does not appear on the wire — the SPA
sums `line_items`, and the overview rollup does the same server-side. Names are denormalised onto
the read models (`supplier_name`, `warehouse`) so a list renders without a cross-service join.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

#: Validates a non-empty string as an email address — used where the field is "email format or
#: empty" rather than always-required, so `EmailStr` itself (which rejects empty) does not fit.
_EMAIL = TypeAdapter(EmailStr)


def _validate_email_or_empty(value: str) -> str:
    if not value:
        return value
    try:
        _EMAIL.validate_python(value)
    except Exception as exc:
        raise ValueError("must be a valid email address, or empty") from exc
    return value


# ------------------------------------------------------------------ vocabularies (match the SPA)

Category = Literal["raw_materials", "packaging", "components", "mro", "logistics", "services"]
SupplierStatus = Literal["active", "on_hold", "pending"]
PoStatus = Literal["draft", "approved", "received", "closed", "cancelled"]
AttentionKind = Literal["low_stock", "out_of_stock", "late_delivery"]
AttentionTone = Literal["danger", "late"]
StockMovementKind = Literal["opening", "receipt", "adjustment", "count", "relocation", "issue"]
StockStatus = Literal[
    "AVAILABLE",
    "QUALITY_HOLD",
    "QUARANTINE",
    "DAMAGED",
    "REJECTED",
    "WIP",
    "IN_TRANSIT",
    "AT_JOB_WORKER",
    "BLOCKED",
]
#: An exact quantity on the wire: a decimal string with at most three decimals (ADR-0014 §2).
#: Never a JSON number — a float cannot carry 0.1 exactly, and stock must be exact.
Qty = Annotated[str, Field(pattern=r"^-?\d+(\.\d{1,3})?$", examples=["82.5"])]
StockMovementReason = Literal["damaged", "lost", "found", "consumed", "returned", "other"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ suppliers


class SupplierOut(BaseModel):
    """Mirrors the SPA's `SupplierOut`. `spend_minor` is lifetime spend in minor units of
    `currency`; `on_time_pct` is the trailing on-time delivery rate, 0-100."""

    id: str
    code: str
    name: str
    category: Category
    status: SupplierStatus
    lead_time_days: int
    on_time_pct: float
    spend_minor: int
    currency: str
    open_pos: int
    contact_name: str
    contact_email: str
    location: str
    since: date


class SupplierIn(BaseModel):
    """`POST /suppliers`. `spend_minor`, `open_pos` and `on_time_pct` are system-maintained
    rollups — never accepted here, only ever moved by `_bump_supplier` and the (unbuilt)
    on-time-delivery rollup. `since` defaults to today rather than staying null, so the response
    is never the nullable-column 500 gap-12 found."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    category: Category
    status: SupplierStatus = "active"
    lead_time_days: int = Field(default=0, ge=0, le=365)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    contact_name: str = Field(default="", max_length=120)
    contact_email: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=160)
    since: date = Field(default_factory=date.today)

    @field_validator("contact_email")
    @classmethod
    def _contact_email_valid(cls, value: str) -> str:
        return _validate_email_or_empty(value)


class SupplierPatch(BaseModel):
    """`PATCH /suppliers/{id}`. `code` and `currency` are deliberately absent — join keys and
    the unit spend has already accumulated in, neither may move under a supplier. This is also
    how a supplier's status changes (`active` <-> `on_hold` <-> `pending`)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    status: SupplierStatus | None = None
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    contact_name: str | None = Field(default=None, max_length=120)
    contact_email: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=160)
    since: date | None = None

    @field_validator("contact_email")
    @classmethod
    def _contact_email_valid(cls, value: str | None) -> str | None:
        return value if value is None else _validate_email_or_empty(value)


# ------------------------------------------------------------------ purchase orders


class PoLineItem(BaseModel):
    """One line on a purchase order. `unit_price_minor` is per-unit minor units of the PO
    currency; the line total (`quantity * unit_price_minor`) is summed into the PO total."""

    id: str
    sku: str
    description: str
    quantity: int
    uom: str
    unit_price_minor: int
    received_qty: int


class PurchaseOrderOut(BaseModel):
    """Mirrors the SPA's `PurchaseOrderOut`. The total is derived from `line_items`, never
    stored, so it never appears here — the client sums the lines."""

    id: str
    po_number: str
    supplier_id: str
    supplier_name: str
    category: Category
    status: PoStatus
    currency: str
    warehouse: str
    raised_by: str
    created_date: date
    expected_date: date
    received_date: date | None
    line_items: list[PoLineItem] = Field(default_factory=list)
    notes: str | None


class PurchaseOrderReceiptOut(PurchaseOrderOut):
    """What receiving answers with: the order, plus the SKUs of the lines that matched no
    inventory item and therefore posted no stock (a freight or service line, or a SKU nobody
    has created yet)."""

    unmatched_skus: list[str] = Field(default_factory=list)


class PoLineIn(BaseModel):
    """One line on `PurchaseOrderIn`/`PurchaseOrderPatch.lines`. `sku` is free text — a line
    against a SKU with no matching inventory item is a service/logistics line, not an error; it
    simply posts no stock when the PO is received."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    quantity: int = Field(ge=1)
    uom: str = Field(default="", max_length=20)
    unit_price_minor: int = Field(default=0, ge=0)


class PurchaseOrderIn(BaseModel):
    """`POST /purchase-orders`. `status`, `number`, `received_date` and `received_qty` are
    server-owned and never accepted here: the number is assigned by `services.next_po_number`,
    status always starts `draft`, and receiving is its own endpoint. `raised_by_id` is always
    `ctx.subject_ref` — never the client's word for who is calling."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    warehouse: str = Field(min_length=1, max_length=120)
    expected_date: date
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    #: Display name only — `Principal` carries no name; the SPA sends `useAuth().user.full_name`.
    raised_by: str = Field(default="", max_length=120)
    lines: list[PoLineIn] = Field(min_length=1, max_length=200)

    @field_validator("expected_date")
    @classmethod
    def _expected_not_in_the_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("expected_date cannot be in the past")
        return value


class PurchaseOrderPatch(BaseModel):
    """`PATCH /purchase-orders/{id}`, draft only. When `lines` is present it is a **full
    replacement** of the PO's lines, never a merge. `status` never appears — every transition
    goes through its own endpoint (`/approve`, `/receive`, `/close`, `/cancel`)."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID | None = None
    warehouse: str | None = Field(default=None, min_length=1, max_length=120)
    expected_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[PoLineIn] | None = Field(default=None, min_length=1, max_length=200)


class ReceiveIn(BaseModel):
    """`POST /purchase-orders/{id}/receive`. The body is optional — an empty POST receives as of
    today — so this schema exists only to validate `received_date` when one is sent."""

    model_config = ConfigDict(extra="forbid")

    received_date: date | None = None

    @field_validator("received_date")
    @classmethod
    def _not_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("received_date cannot be in the future")
        return value


class CancelIn(BaseModel):
    """`POST /purchase-orders/{id}/cancel`. The reason is recorded in the audit row's `after`,
    never silently dropped — cancelling committed spend needs a "why" on the record."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ inventory


class InventoryItemOut(BaseModel):
    """Mirrors the SPA's `InventoryItemOut`. Stock value = `on_hand * unit_value_minor`;
    low-stock is derived by the client (and the overview) as `on_hand <= reorder_point`.

    Stock is read from the ledger's balances (ADR-0014 §10). `on_hand` is the whole-unit figure
    the API has always returned — exact while stock is a whole number of units, which is all
    the item-level API can create — and `on_hand_qty` / `reserved_qty` / `available_qty` are the
    exact decimal strings every new client should read.
    """

    id: str
    sku: str
    name: str
    category: Category
    warehouse: str
    on_hand: int
    on_hand_qty: Qty
    reserved_qty: Qty
    available_qty: Qty
    reorder_point: int
    uom: str
    unit_value_minor: int
    currency: str
    updated_at: date


class InventoryItemIn(BaseModel):
    """`POST /inventory`. `on_hand` is never accepted — `opening_on_hand` becomes the item's
    first `opening` movement instead, so `on_hand` is always the sum of its movements from the
    moment the row exists."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    category: Category
    #: Must name an existing warehouse in the org — validated against the warehouse directory,
    #: not just a non-empty string.
    warehouse: str = Field(min_length=1, max_length=120)
    uom: str = Field(min_length=1, max_length=20)
    reorder_point: int = Field(default=0, ge=0)
    unit_value_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    opening_on_hand: int = Field(default=0, ge=0)


class InventoryItemPatch(BaseModel):
    """`PATCH /inventory/{id}`. `sku`, `currency` and — above all — `on_hand` are deliberately
    absent: `sku` is the string a PO line joins on, `currency` is the unit the stock value
    accumulated in, and `on_hand` may only ever move through a movement (`/adjustments`).
    Sending any of the three is a 422 by `extra="forbid"`, not a silently ignored field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category | None = None
    warehouse: str | None = Field(default=None, min_length=1, max_length=120)
    uom: str | None = Field(default=None, min_length=1, max_length=20)
    reorder_point: int | None = Field(default=None, ge=0)
    unit_value_minor: int | None = Field(default=None, ge=0)


class StockAdjustmentIn(BaseModel):
    """`POST /inventory/{id}/adjustments`. Exactly one of `quantity_delta` (a relative add/remove)
    or `counted_on_hand` (an absolute physical count) — never both, never neither. `reason` is
    required with a delta adjustment (why it moved) and forbidden with a count (a count is a
    fact, not a reason)."""

    model_config = ConfigDict(extra="forbid")

    quantity_delta: int | None = None
    counted_on_hand: int | None = Field(default=None, ge=0)
    reason: StockMovementReason | None = None
    note: str = Field(default="", max_length=500)
    occurred_on: date = Field(default_factory=date.today)

    @field_validator("quantity_delta")
    @classmethod
    def _delta_nonzero(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("quantity_delta must not be zero")
        return value

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> Self:
        has_delta = self.quantity_delta is not None
        has_count = self.counted_on_hand is not None
        if has_delta == has_count:
            raise ValueError("Provide exactly one of quantity_delta or counted_on_hand")
        if has_delta and self.reason is None:
            raise ValueError("reason is required with quantity_delta")
        if has_count and self.reason is not None:
            raise ValueError("reason is not accepted with counted_on_hand")
        return self


class StockMovementOut(BaseModel):
    """One row of an item's ledger, with its move's kind/reason/reference. Append-only, newest
    first. `quantity_delta` / `on_hand_after` are the whole-unit figures the API always returned;
    `quantity` / `balance_after` are exact. `balance_after` is the balance of the row's own
    bucket (`location_id` + `status`), which for a single-location item is its on-hand."""

    id: str
    kind: StockMovementKind
    reason: StockMovementReason | None
    quantity_delta: int
    on_hand_after: int
    quantity: Qty
    balance_after: Qty
    location_id: str
    status: StockStatus
    reference_type: Literal["purchase_order"] | None
    reference_id: str | None
    note: str
    occurred_on: date
    created_at: datetime
    created_by: str | None


class StockBalanceOut(BaseModel):
    """One bucket of an item's stock: where it is, in what state, and how much of it is
    promised. Exact decimal strings in the item's UOM."""

    location_id: str
    warehouse_code: str
    warehouse: str
    location_code: str
    status: StockStatus
    lot_id: str | None
    roll_id: str | None
    on_hand_qty: Qty
    reserved_qty: Qty
    available_qty: Qty


class InventoryDiscrepancyOut(BaseModel):
    """A stored stock figure the reconciler found disagreeing with its source of truth.
    `kind=balance`: the bucket's balance vs the sum of its ledger. `kind=reserved`: its reserved
    figure vs its open reservations. Nothing repairs these automatically."""

    id: str
    kind: Literal["balance", "reserved"]
    inventory_item_id: str
    location_id: str
    status: str
    lot_id: str | None
    roll_id: str | None
    stored_qty: Qty
    expected_qty: Qty
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str


class ReconcileOut(BaseModel):
    """What an on-demand reconciliation of the caller's organization found."""

    run_id: str
    buckets_checked: int
    findings: int
    new_findings: int
    discrepancies: list[InventoryDiscrepancyOut]


class DiscrepancyResolveIn(BaseModel):
    """`POST /inventory/discrepancies/{id}/resolve` — what was done about it, in words."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ warehouses (reference)


class WarehouseOut(BaseModel):
    """A stocking location. Reference data behind the warehouse names the POs and inventory
    rows carry."""

    id: str
    code: str
    name: str
    location: str


class WarehouseIn(BaseModel):
    """`POST /warehouses`. `code` is immutable once created — it never appears on
    `WarehousePatch`."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=160)


class WarehousePatch(BaseModel):
    """`PATCH /warehouses/{id}`. A rename cascades to `purchase_orders.warehouse` and
    `inventory_items.warehouse` in the same transaction — those columns are denormalised copies
    of `name`, not a join."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=160)


# ------------------------------------------------------------------ overview rollups


class CategorySpend(BaseModel):
    """One category's share of committed spend — grouped from purchase orders. Mirrors the
    SPA's `CategorySpend`."""

    key: Category
    name: str
    spend_minor: int
    #: Share of the resolved total, 0-100.
    pct: int


class StatusSlice(BaseModel):
    """One slice of the PO-status donut — grouped by status. Mirrors the SPA's `StatusSlice`."""

    key: PoStatus
    label: str
    count: int
    pct: int


class SpendPoint(BaseModel):
    """A point on the spend-over-time chart, in minor units. Mirrors the SPA's `SpendPoint`."""

    label: str
    spend_minor: int


class SupplierSpend(BaseModel):
    """One supplier's share of lifetime spend — the supplier-breakdown row on the overview."""

    id: str
    name: str
    category: Category
    spend_minor: int
    #: Share of total supplier spend, 0-100.
    pct: int


class AttentionItem(BaseModel):
    """One row in the "needs attention" queue. Mirrors the SPA's `AttentionItem`."""

    id: str
    kind: AttentionKind
    title: str
    detail: str
    href: str
    tone: AttentionTone


class OverviewOut(BaseModel):
    """The Operations overview's server-side rollup. The SPA can derive all of this from the
    list endpoints; this exposes the same arithmetic once, so a reporting view can share it.

    `open_pos` counts POs still in flight (draft or approved); `inventory_value_minor` is the
    value of stock on hand; `low_stock_count` is items at or below their reorder point;
    `on_time_pct` is the spend-weighted on-time delivery rate. The series and breakdowns feed the
    charts."""

    open_pos: int
    inventory_value_minor: int
    low_stock_count: int
    on_time_pct: float
    currency: str
    spend_trend: list[SpendPoint] = Field(default_factory=list)
    spend_by_category: list[CategorySpend] = Field(default_factory=list)
    po_status_mix: list[StatusSlice] = Field(default_factory=list)
    top_suppliers: list[SupplierSpend] = Field(default_factory=list)
    attention: list[AttentionItem] = Field(default_factory=list)
