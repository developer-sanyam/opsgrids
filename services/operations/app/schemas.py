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

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

Category = Literal["raw_materials", "packaging", "components", "mro", "logistics", "services"]
SupplierStatus = Literal["active", "on_hold", "pending"]
PoStatus = Literal["draft", "approved", "received", "closed"]
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


# ------------------------------------------------------------------ warehouses (reference)


class WarehouseOut(BaseModel):
    """A stocking location. Reference data behind the warehouse names the POs and inventory
    rows carry."""

    id: str
    code: str
    name: str
    location: str


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
