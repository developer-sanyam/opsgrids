"""Wire contracts for inventory Phase 3: control settings, inter-warehouse transfers, the
in-transit report, reservations and adjustment requests.

As elsewhere in `operations`: every write model forbids unknown fields, and every quantity is a
decimal **string** with at most three decimals (`Qty`) — a JSON number is refused by type, so a
float can never reach the domain layer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.inventory.tracking_schemas import OnSiteStatus
from app.schemas import Qty, StockMovementReason

TransferStatus = Literal[
    "DRAFT", "REQUESTED", "APPROVED", "IN_TRANSIT", "RECEIVED", "CANCELLED", "REJECTED"
]
ApprovalRoute = Literal["AUTO", "DIRECT", "SERVICE"]
AdjustmentStatus = Literal["PENDING_APPROVAL", "APPLIED", "REJECTED", "CANCELLED", "FAILED"]
ReservationReference = Literal["order", "production_order", "manual"]
ReservationStatus = Literal["active", "released", "consumed", "transferred"]


# ------------------------------------------------------------------ control settings


class ControlSettingsIn(BaseModel):
    """`PUT /inventory/control-settings` — replaces both thresholds. `null` clears one (no
    threshold: transfers need a direct sign-off, adjustments apply at once). Each is compared to
    every line's quantity **in that item's own unit**."""

    model_config = ConfigDict(extra="forbid")

    transfer_approval_threshold: Qty | None = None
    adjustment_approval_threshold: Qty | None = None


class ControlSettingsOut(BaseModel):
    transfer_approval_threshold: Qty | None
    adjustment_approval_threshold: Qty | None
    updated_at: datetime | None
    updated_by: str | None


# ------------------------------------------------------------------ transfers


class TransferLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    from_location_id: UUID
    quantity: Qty
    lot_id: UUID | None = None
    roll_id: UUID | None = None


class TransferIn(BaseModel):
    """`POST /inventory/transfers` — a DRAFT between two different warehouses."""

    model_config = ConfigDict(extra="forbid")

    from_warehouse_id: UUID
    to_warehouse_id: UUID
    lines: list[TransferLineIn] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)
    expected_on: date | None = None

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("from_warehouse_id and to_warehouse_id must differ")
        return self


class TransferPatch(BaseModel):
    """`PATCH /inventory/transfers/{id}` — a DRAFT only. `lines`, when given, **replaces** them."""

    model_config = ConfigDict(extra="forbid")

    to_warehouse_id: UUID | None = None
    lines: list[TransferLineIn] | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    expected_on: date | None = None


class TransferDecisionIn(BaseModel):
    """`:approve` / `:reject` (the direct route). A rejection must say why."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(default="", max_length=500)


class TransferCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class TransferDispatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)
    expected_on: date | None = None


class TransferReceiveLineIn(BaseModel):
    """What the destination accounts for on one line: `received_quantity` goes into stock at
    `to_location_id` (default: the destination warehouse's default bin); `shortage_quantity` is
    declared lost in transit and written off from the in-transit bucket. Together they cannot
    exceed what is still in transit on the line."""

    model_config = ConfigDict(extra="forbid")

    line_id: UUID
    received_quantity: Qty = "0"
    shortage_quantity: Qty = "0"
    to_location_id: UUID | None = None

    @field_validator("received_quantity", "shortage_quantity")
    @classmethod
    def _not_negative(cls, value: str) -> str:
        if value.startswith("-"):
            raise ValueError("a received or short quantity cannot be negative")
        return value


class TransferReceiveIn(BaseModel):
    """`:receive` — one or more lines, any number of times, until every line is settled."""

    model_config = ConfigDict(extra="forbid")

    lines: list[TransferReceiveLineIn] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _one_per_line(self) -> Self:
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("each transfer line may appear once per receipt")
        return self


class WarehouseRefOut(BaseModel):
    id: str
    code: str
    name: str


class TransferLineOut(BaseModel):
    id: str
    line_no: int
    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    from_location_id: str
    from_location_code: str
    to_location_id: str | None
    in_transit_location_id: str | None
    lot_id: str | None
    roll_id: str | None
    quantity: Qty
    dispatched: Qty
    received: Qty
    shortage: Qty
    #: dispatched - received - shortage: still on its way, still in the IN_TRANSIT bucket.
    in_transit: Qty


class TransferTransitionOut(BaseModel):
    from_status: TransferStatus | None
    to_status: TransferStatus
    actor: str
    note: str
    created_at: datetime


class TransferSummaryOut(BaseModel):
    """A row of the transfer list."""

    id: str
    number: str
    status: TransferStatus
    from_warehouse: WarehouseRefOut
    to_warehouse: WarehouseRefOut
    line_count: int
    approval_route: ApprovalRoute | None
    expected_on: date | None
    requested_at: datetime | None
    dispatched_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class TransferOut(TransferSummaryOut):
    note: str
    approval_request_id: str | None
    approver_name: str | None
    requested_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    decision_comment: str
    dispatched_by: str | None
    cancelled_at: datetime | None
    cancel_reason: str
    #: True while it is REQUESTED and the approval service (not a person here) must say yes.
    awaiting_approval: bool
    lines: list[TransferLineOut]
    transitions: list[TransferTransitionOut] = Field(default_factory=list)
    #: True when this answer is a retried request replayed rather than applied again.
    replayed: bool = False


class InTransitRowOut(BaseModel):
    """One (item, destination warehouse) with stock on its way. `physical` is the item's on-hand
    **in that warehouse** and is reported separately: in-transit stock is owned but not yet
    there, so it is never inside `physical`."""

    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    warehouse_id: str
    warehouse_code: str
    in_transit: Qty
    physical: Qty


# ------------------------------------------------------------------ reservations


class ReservationIn(BaseModel):
    """`POST /inventory/reservations` — promise AVAILABLE stock in one bucket to a demand."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    location_id: UUID
    quantity: Qty
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    reference_type: ReservationReference = "manual"
    reference_id: UUID | None = None
    expires_at: datetime | None = Field(
        default=None, description="Timezone-aware, in the future. Omit for a hold that stays."
    )
    note: str = Field(default="", max_length=500)

    @field_validator("quantity")
    @classmethod
    def _positive(cls, value: str) -> str:
        if value.startswith("-") or float(value) == 0:
            raise ValueError("a reservation is a positive quantity")
        return value

    @model_validator(mode="after")
    def _reference(self) -> Self:
        if self.reference_type != "manual" and self.reference_id is None:
            raise ValueError("an order or production-order reservation names that document")
        return self


class ReservationDetailOut(BaseModel):
    id: str
    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    location_id: str
    location_code: str
    lot_id: str | None
    roll_id: str | None
    quantity: Qty
    consumed: Qty
    released: Qty
    transferred: Qty
    remaining: Qty
    status: ReservationStatus
    reference_type: str | None
    reference_id: str | None
    expires_at: datetime | None
    expired_at: datetime | None
    note: str
    created_at: datetime
    closed_at: datetime | None
    replayed: bool = False


# ------------------------------------------------------------------ adjustments


class AdjustmentIn(BaseModel):
    """`POST /inventory/adjustments` — a signed change to one bucket, with the reason it is
    being made. At or over the organization's threshold it is **held for approval** (and must
    carry `evidence_document_id`); under it, it applies at once."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    location_id: UUID
    quantity_delta: Qty = Field(description="Signed: negative takes stock out, positive adds.")
    reason: StockMovementReason
    status: OnSiteStatus = "AVAILABLE"
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    note: str = Field(default="", max_length=500)
    evidence_document_id: UUID | None = None
    occurred_on: date = Field(default_factory=date.today)

    @field_validator("quantity_delta")
    @classmethod
    def _non_zero(cls, value: str) -> str:
        if float(value) == 0:
            raise ValueError("an adjustment must change the quantity")
        return value

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class AdjustmentCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class AdjustmentOut(BaseModel):
    id: str
    number: str
    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    location_id: str
    location_code: str
    stock_status: str
    lot_id: str | None
    roll_id: str | None
    quantity_delta: Qty
    reason: StockMovementReason
    note: str
    evidence_document_id: str | None
    occurred_on: date
    status: AdjustmentStatus
    approval_required: bool
    approval_request_id: str | None
    approver_name: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_comment: str
    requested_by: str
    applied_move_id: str | None
    applied_at: datetime | None
    failure_reason: str
    created_at: datetime
    replayed: bool = False
