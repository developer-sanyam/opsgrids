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

import uuid
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

# ------------------------------------------------------------------ vocabularies (match the SPA)

Category = Literal["raw_materials", "packaging", "components", "mro", "logistics", "services"]
SupplierStatus = Literal["active", "on_hold", "pending"]
PoStatus = Literal["draft", "approved", "received", "closed"]
#: The statuses a PO may be *raised* in. `received` is reached through the receive endpoint —
#: which also fills the line quantities — and `closed` is a later transition, so neither is
#: something a client may assert at creation.
PoRaiseStatus = Literal["draft", "approved"]
AttentionKind = Literal["low_stock", "out_of_stock", "late_delivery"]
AttentionTone = Literal["danger", "late"]


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
    """Add a supplier to the directory.

    The performance figures on `SupplierOut` are not accepted here. `spend_minor` and `open_pos`
    are rollups of this organization's own purchase orders and `on_time_pct` is a trailing
    delivery rate: all three are things the platform observes, not things a client asserts, and a
    supplier being added has no history yet. `since` defaults to today because the read contract
    is not nullable.
    """

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    category: Category
    status: SupplierStatus = "active"
    lead_time_days: int = Field(default=0, ge=0, le=365)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    contact_name: str = Field(default="", max_length=120)
    contact_email: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=160)
    since: date = Field(default_factory=date.today)


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


class PoLineIn(BaseModel):
    """One requested line. `amount_minor` is not accepted — the server computes it as
    `quantity * unit_price_minor`, so the lines foot to the derived total by construction and a
    client cannot assert a total of its own. `received_qty` is not accepted either: a line has
    received nothing until the PO is received."""

    sku: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    quantity: int = Field(gt=0)
    #: Per-unit price, integer minor units of the PO currency. Never a float (ADR-0000 §8).
    unit_price_minor: int = Field(ge=0)
    uom: str = Field(default="", max_length=20)


class PurchaseOrderIn(BaseModel):
    """Raise a purchase order, naming the supplier by `supplier_id` or by `supplier_code`.

    What the server derives does not appear here: the total (summed from the lines), each line's
    `amount_minor`, the PO's `category` (denormalised from the supplier) and `raised_by_id` —
    the raiser is the caller, because that column is what OWN scope narrows on.
    """

    number: str = Field(min_length=1, max_length=40)
    supplier_id: uuid.UUID | None = None
    supplier_code: str | None = Field(default=None, max_length=40)
    status: PoRaiseStatus = "draft"
    warehouse: str = Field(default="", max_length=120)
    #: Display name of the raiser; the identity OWN scope uses comes from the caller, not here.
    raised_by: str = Field(default="", max_length=120)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    created_date: date = Field(default_factory=date.today)
    expected_date: date
    notes: str | None = Field(default=None, max_length=8000)
    lines: list[PoLineIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_a_supplier(self) -> Self:
        if self.supplier_id is None and not self.supplier_code:
            raise ValueError("Name the supplier with supplier_id or supplier_code")
        return self


# ------------------------------------------------------------------ inventory


class InventoryItemOut(BaseModel):
    """Mirrors the SPA's `InventoryItemOut`. Stock value = `on_hand * unit_value_minor`;
    low-stock is derived by the client (and the overview) as `on_hand <= reorder_point`."""

    id: str
    sku: str
    name: str
    category: Category
    warehouse: str
    on_hand: int
    reorder_point: int
    uom: str
    unit_value_minor: int
    currency: str
    updated_at: date


class InventoryItemIn(BaseModel):
    """Stock an item at a warehouse.

    Stock value (`on_hand * unit_value_minor`) and low-stock (`on_hand <= reorder_point`) are
    derived on read and never sent. `stocked_at` — the date of the count, surfaced as
    `updated_at` — defaults to today.
    """

    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    category: Category
    warehouse: str = Field(default="", max_length=120)
    on_hand: int = Field(default=0, ge=0)
    reorder_point: int = Field(default=0, ge=0)
    uom: str = Field(default="", max_length=20)
    #: Per-unit stock value, integer minor units of `currency`.
    unit_value_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    stocked_at: date = Field(default_factory=date.today)


# ------------------------------------------------------------------ warehouses (reference)


class WarehouseOut(BaseModel):
    """A stocking location. Reference data behind the warehouse names the POs and inventory
    rows carry."""

    id: str
    code: str
    name: str
    location: str


class WarehouseIn(BaseModel):
    """Open a stocking location. `code` (`WH-PUN`) is its identity within the organization."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=160)


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
