"""Wire contracts for purchase requisitions, goods receipts and item unit conversions (inventory
Phase 2). They are published through the same OpenAPI document as the rest of the service and kept
beside the code that serves them, for the reason `app.inventory.tracking_schemas` gives.

Every write model forbids unknown fields. Every quantity is a decimal **string** with at most three
decimals (`Qty`, ADR-0014 §2); a JSON number is refused by type, so a float can never reach the
domain layer. Money is integer minor units.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.jobwork import domain as jw_domain
from app.schemas import Qty, StockStatus
from app.schemas import TolerancePct as Pct

RequisitionStatus = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CONVERTED", "CANCELLED"]
ReceiptOutcome = Literal["PARTIAL", "EXACT", "SHORT", "OVER"]
#: A non-negative decimal string — a receipt line may be `"0"` when it only closes the line short.
NonNegQty = Annotated[str, Field(pattern=r"^\d+(\.\d{1,3})?$", examples=["82.5"])]


# ------------------------------------------------------------------ unit conversions


class UomConversionIn(BaseModel):
    """`POST /inventory/{item_id}/uom-conversions`: 1 `uom` = `numerator / denominator` of the
    item's own stock unit ("1 box = 12 pcs" is `{"uom": "box", "numerator": 12}`). Integers, never
    a decimal, so the ratio is exact."""

    model_config = ConfigDict(extra="forbid")

    uom: str = Field(min_length=1, max_length=20)
    numerator: StrictInt = Field(ge=1, le=10**9)
    denominator: StrictInt = Field(default=1, ge=1, le=10**9)


class UomConversionPatch(BaseModel):
    """`PATCH /inventory/uom-conversions/{id}`. A receipt already posted keeps the quantity it was
    converted to (it stores both the entered and the converted figure); only later receipts see
    the new ratio."""

    model_config = ConfigDict(extra="forbid")

    numerator: StrictInt | None = Field(default=None, ge=1, le=10**9)
    denominator: StrictInt | None = Field(default=None, ge=1, le=10**9)

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if self.numerator is None and self.denominator is None:
            raise ValueError("Send numerator and/or denominator")
        return self


class UomConversionOut(BaseModel):
    id: str
    inventory_item_id: str
    uom_id: str
    uom: str
    numerator: int
    denominator: int
    #: The item's own stock unit this ratio converts into.
    stock_uom: str
    created_at: datetime


# ------------------------------------------------------------------ purchase requisitions


class RequisitionLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    #: In the item's own stock unit.
    quantity: Qty
    suggested_supplier_id: UUID | None = None
    est_unit_price_minor: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("quantity")
    @classmethod
    def _positive(cls, value: str) -> str:
        if value.startswith("-") or float(value) == 0:
            raise ValueError("A requested quantity must be greater than zero")
        return value


class RequisitionIn(BaseModel):
    """`POST /purchase-requisitions`. Starts `DRAFT`; the number, status and requester id are
    server-owned."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID | None = None
    needed_by: date | None = None
    reason: str | None = Field(default=None, max_length=2000)
    #: Display name only, as on a purchase order.
    requested_by: str = Field(default="", max_length=120)
    lines: list[RequisitionLineIn] = Field(min_length=1, max_length=200)


class RequisitionPatch(BaseModel):
    """`PATCH /purchase-requisitions/{id}`, DRAFT only. `lines`, when present, replaces them."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID | None = None
    needed_by: date | None = None
    reason: str | None = Field(default=None, max_length=2000)
    lines: list[RequisitionLineIn] | None = Field(default=None, min_length=1, max_length=200)


class RequisitionDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class RequisitionRejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class RequisitionPriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: UUID
    unit_price_minor: int = Field(ge=0)


class RequisitionConvertIn(BaseModel):
    """`POST /purchase-requisitions/{id}/convert`. Turns an APPROVED requisition into **one draft
    purchase order** with this supplier. A PO line's quantity is a whole number of units today, so
    a requisition line with a fraction cannot be converted until the PO line is edited by hand."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    expected_date: date
    #: A warehouse name (a PO names its warehouse by name); defaults to the requisition's own.
    warehouse: str | None = Field(default=None, min_length=1, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    #: Per-line prices that override the requisition's estimates.
    prices: list[RequisitionPriceIn] = Field(default_factory=list, max_length=200)

    @field_validator("expected_date")
    @classmethod
    def _not_in_the_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("expected_date cannot be in the past")
        return value


class RequisitionLineOut(BaseModel):
    id: str
    line_no: int
    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    quantity: Qty
    suggested_supplier_id: str | None
    est_unit_price_minor: int | None
    note: str | None


class RequisitionOut(BaseModel):
    id: str
    number: str
    status: RequisitionStatus
    requested_by: str
    requested_by_id: str | None
    warehouse_id: str | None
    warehouse_name: str | None
    needed_by: date | None
    reason: str | None
    decision_note: str | None
    submitted_at: datetime | None
    decided_at: datetime | None
    converted_po_id: str | None
    converted_po_number: str | None
    created_at: datetime
    updated_at: datetime
    lines: list[RequisitionLineOut] = Field(default_factory=list)


class RequisitionSummaryOut(BaseModel):
    """A list row: no lines, a line count instead."""

    id: str
    number: str
    status: RequisitionStatus
    requested_by: str
    warehouse_name: str | None
    needed_by: date | None
    line_count: int
    converted_po_number: str | None
    created_at: datetime


# ------------------------------------------------------------------ goods receipts


class ReceiptRollIn(BaseModel):
    """One physical roll in a delivery. `primary_qty` is in the item's stock unit (82.5 m);
    `secondary_qty` is a separately **measured** figure in another unit (14.8 kg), never derived."""

    model_config = ConfigDict(extra="forbid")

    roll_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )
    primary_qty: Qty
    secondary_qty: NonNegQty | None = None
    secondary_uom: str | None = Field(default=None, min_length=1, max_length=10)
    width_mm: int | None = Field(default=None, ge=1, le=100_000)
    gsm: int | None = Field(default=None, ge=1, le=100_000)
    shade: str | None = Field(default=None, min_length=1, max_length=40)
    grade: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def _secondary_pair(self) -> Self:
        if (self.secondary_qty is None) != (self.secondary_uom is None):
            raise ValueError("secondary_qty and secondary_uom go together")
        return self


class ReceiptLotIn(BaseModel):
    """The lot a line's stock goes into: an existing one (`lot_id`, e.g. a second delivery of the
    same supplier lot), or the fields of a new one. Omitted entirely on a lot-tracked item, a new
    lot with a generated number is created."""

    model_config = ConfigDict(extra="forbid")

    lot_id: UUID | None = None
    lot_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )
    supplier_lot_no: str | None = Field(default=None, min_length=1, max_length=60)
    manufactured_on: date | None = None
    expires_on: date | None = None

    @model_validator(mode="after")
    def _existing_or_new(self) -> Self:
        if self.lot_id is not None and any(
            v is not None
            for v in (self.lot_no, self.supplier_lot_no, self.manufactured_on, self.expires_on)
        ):
            raise ValueError("Name an existing lot_id, or the fields of a new lot — not both")
        return self


class ReceiptLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_line_id: UUID
    #: How much arrived, in `uom` (default: the item's stock unit). `"0"` is allowed only with
    #: `close_short`.
    quantity: NonNegQty
    #: The unit `quantity` is in: a container with a recorded ratio for this item ("box"), or a
    #: fixed unit of the same dimension ("cm" for an item stocked in "m").
    uom: str | None = Field(default=None, min_length=1, max_length=20)
    #: Where the stock goes; defaults to the item's home bin.
    location_id: UUID | None = None
    #: Receive into `QUALITY_HOLD` even though the item does not track quality. (An item that does
    #: track quality is always received into hold.)
    hold: bool = False
    #: Nothing more is expected on this PO line: it stops holding the order open.
    close_short: bool = False
    lot: ReceiptLotIn | None = None
    rolls: list[ReceiptRollIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _zero_needs_close_short(self) -> Self:
        if float(self.quantity) == 0 and not self.close_short:
            raise ValueError("A zero quantity only makes sense with close_short")
        if float(self.quantity) == 0 and (self.rolls or self.lot is not None):
            raise ValueError("A line that receives nothing carries no lot or rolls")
        return self


class GoodsReceiptIn(BaseModel):
    """`POST /goods-receipts`. One delivery against one purchase order, any number of its lines
    (a PO line may appear more than once, each with its own lot). Send an `Idempotency-Key`: a
    retry replays the first result."""

    model_config = ConfigDict(extra="forbid")

    purchase_order_id: UUID
    received_on: date = Field(default_factory=jw_domain.ist_today)
    delivery_note: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=200)

    @field_validator("received_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > jw_domain.ist_today() + jw_domain.RECEIVED_ON_FUTURE_TOLERANCE:
            raise ValueError("received_on cannot be in the future")
        return value


class ReceiptRollOut(BaseModel):
    id: str
    roll_no: str
    primary_qty: Qty
    secondary_qty: Qty | None
    secondary_uom: str | None


class GoodsReceiptLineOut(BaseModel):
    id: str
    line_no: int
    purchase_order_line_id: str
    sku: str
    description: str
    inventory_item_id: str | None
    stock_uom: str | None
    location_id: str | None
    location_label: str | None
    stock_status: StockStatus | None
    lot_id: str | None
    lot_no: str | None
    supplier_lot_no: str | None
    rolls: list[ReceiptRollOut] = Field(default_factory=list)
    entered_qty: Qty
    entered_uom: str
    #: In the item's stock unit — what actually went onto the books.
    received_qty: Qty
    ordered_qty: Qty
    prior_received_qty: Qty
    cumulative_qty: Qty
    tolerance_pct: Pct
    close_short: bool
    outcome: ReceiptOutcome
    unit_cost_minor: int | None
    unit_cost_currency: str | None


class GoodsReceiptOut(BaseModel):
    id: str
    number: str
    purchase_order_id: str
    po_number: str
    po_status: str
    supplier_id: str
    supplier_name: str
    received_on: date
    delivery_note: str | None
    note: str | None
    status: Literal["POSTED"]
    #: True when this is the first result of a retried `Idempotency-Key`, not a new posting.
    replayed: bool = False
    created_at: datetime
    created_by: str | None
    lines: list[GoodsReceiptLineOut] = Field(default_factory=list)


class GoodsReceiptSummaryOut(BaseModel):
    id: str
    number: str
    purchase_order_id: str
    po_number: str
    supplier_name: str
    received_on: date
    delivery_note: str | None
    line_count: int
    created_at: datetime


class ReceivingConversionOut(BaseModel):
    uom: str
    numerator: int
    denominator: int


class ReceivingLineOut(BaseModel):
    """What a receiving screen needs to know about one PO line, computed under no lock — advisory;
    the posting re-checks everything under the PO's row lock."""

    purchase_order_line_id: str
    sku: str
    description: str
    inventory_item_id: str | None
    stock_uom: str | None
    ordered_qty: Qty
    received_qty: Qty
    remaining_qty: Qty
    #: The most that may still be received (ordered + tolerance - received), never negative.
    max_receivable_qty: Qty
    tolerance_pct: Pct
    short_closed: bool
    track_lot: bool
    track_roll: bool
    track_expiry: bool
    track_quality: bool
    default_location_id: str | None
    default_location_label: str | None
    conversions: list[ReceivingConversionOut] = Field(default_factory=list)


class ReceivingContextOut(BaseModel):
    purchase_order_id: str
    po_number: str
    po_status: str
    warehouse: str
    supplier_name: str
    #: Whether the PO is in a state a goods receipt can be posted against.
    can_receive: bool
    lines: list[ReceivingLineOut]
