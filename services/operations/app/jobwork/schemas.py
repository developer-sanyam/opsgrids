"""Wire contracts for job work (Inventory plan Phase 6) — the OpenAPI surface of `app.jobwork`.

Quantities are **decimal strings** with at most three decimals, never JSON numbers (ADR-0014 §2);
money is integer minor units with an ISO currency. A float cannot enter or leave this module.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Same shape as `app.schemas.Qty`, restated here so this package does not import the service's
#: monolithic schema module (and so the OpenAPI component is named once per use).
Qty = Annotated[str, Field(pattern=r"^\d+(\.\d{1,3})?$", examples=["82.5"])]

JobWorkStatus = Literal[
    "DRAFT", "APPROVED", "ISSUED", "PARTIALLY_RECEIVED", "RECEIVED", "CLOSED", "CANCELLED"
]
EwayStatus = Literal["NOT_REQUIRED", "REQUESTED", "GENERATED", "FAILED"]
GoodStatus = Literal["AVAILABLE", "QUALITY_HOLD"]


# ------------------------------------------------------------------ writes


class JobWorkMaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    #: The bucket issued from. Defaults to the item's home bin.
    location_id: UUID | None = None
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    quantity: Qty


class JobWorkOrderIn(BaseModel):
    """`POST /operations/jobwork/orders` — a draft order. Nothing moves until it is approved and
    issued."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    process: str = Field(default="", max_length=120)
    due_date: date | None = None
    source_warehouse_id: UUID | None = None
    receive_location_id: UUID | None = None
    qc_required: bool = False
    notes: str = Field(default="", max_length=1000)
    materials: list[JobWorkMaterialIn] = Field(min_length=1, max_length=200)


class JobWorkOrderPatch(BaseModel):
    """`PATCH` — header fields of a `DRAFT` order. `expected_version` is the optimistic-concurrency
    check; a stale one is a 409."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    process: str | None = Field(default=None, max_length=120)
    due_date: date | None = None
    clear_due_date: bool = False
    receive_location_id: UUID | None = None
    qc_required: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


class JobWorkTransitionIn(BaseModel):
    """Body of `:approve`, `:cancel` and `:close` — all optional."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class JobWorkIssueIn(BaseModel):
    """`:issue` — the transport facts the delivery challan and a later e-way bill need."""

    model_config = ConfigDict(extra="forbid")

    vehicle_no: str | None = Field(default=None, max_length=20)
    transporter_id: str | None = Field(default=None, max_length=30)
    distance_km: int | None = Field(default=None, ge=0, le=5000)
    note: str = Field(default="", max_length=500)


class JobWorkReceiptLineIn(BaseModel):
    """One material's share of a receipt: how much came back good, how much came back unusable,
    and how much the vendor reports lost. At least one must be positive."""

    model_config = ConfigDict(extra="forbid")

    material_id: UUID
    received: Qty = "0"
    rejected: Qty = "0"
    shortage: Qty = "0"
    #: Where the returned goods land. Defaults to the order's receive location, then to the bucket
    #: the material was issued from.
    location_id: UUID | None = None


class JobWorkReceiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[JobWorkReceiptLineIn] = Field(min_length=1, max_length=200)
    received_on: date | None = None
    note: str = Field(default="", max_length=500)


class ChallanEwayIn(BaseModel):
    """`POST /challans/{id}:request-eway` — supply the transport facts and (re-)ask finance to
    consider an e-way bill."""

    model_config = ConfigDict(extra="forbid")

    distance_km: int = Field(ge=1, le=5000)
    vehicle_no: str | None = Field(default=None, max_length=20)
    transporter_id: str | None = Field(default=None, max_length=30)


# ------------------------------------------------------------------ reads


class JobWorkMaterialOut(BaseModel):
    id: str
    line_no: int
    item_id: str
    sku: str
    uom: str
    lot_id: str | None
    roll_id: str | None
    location_id: str
    quantity: Qty
    sent: Qty
    received: Qty
    rejected: Qty
    shortage: Qty
    outstanding: Qty
    unit_value_minor: int
    currency: str


class JobWorkReceiptOut(BaseModel):
    id: str
    receipt_no: str
    material_id: str
    sku: str
    received: Qty
    rejected: Qty
    shortage: Qty
    location_id: str
    good_status: GoodStatus
    qc_handoff: bool
    received_on: date
    note: str
    created_by: str | None
    created_at: datetime


class ChallanLineOut(BaseModel):
    sku: str
    uom: str
    quantity: Qty
    unit_value_minor: int
    value_minor: int
    lot_id: str | None = None
    roll_id: str | None = None


class ChallanOut(BaseModel):
    id: str
    order_id: str
    order_number: str
    challan_number: str
    kind: Literal["DELIVERY"]
    issued_at: datetime
    supplier_id: str
    supplier_name: str
    process: str
    lines: list[ChallanLineOut]
    total_value_minor: int
    currency: str
    vehicle_no: str | None
    transporter_id: str | None
    distance_km: int | None
    eway_status: EwayStatus
    eway_bill_no: str | None
    eway_requested_at: datetime | None


class ReconciliationLineOut(BaseModel):
    material_id: str
    sku: str
    uom: str
    sent: Qty
    received: Qty
    rejected: Qty
    shortage: Qty
    outstanding: Qty


class ReconciliationDriftOut(BaseModel):
    item_id: str
    sku: str
    documents: Qty
    ledger: Qty


class ReconciliationOut(BaseModel):
    """What was sent, what came back good, what came back rejected, what the vendor reports short,
    and what is still out — and whether the documents and the ledger agree."""

    order_id: str
    order_status: JobWorkStatus
    issued: bool
    lines: list[ReconciliationLineOut]
    total_sent: Qty
    total_received: Qty
    total_rejected: Qty
    total_shortage: Qty
    total_outstanding: Qty
    documents_settled: bool
    ledger_consistent: bool
    drift: list[ReconciliationDriftOut]
    #: Settled **and** consistent — the one condition `:close` requires.
    balanced: bool


class JobWorkOrderOut(BaseModel):
    id: str
    number: str
    supplier_id: str
    supplier_name: str
    process: str
    status: JobWorkStatus
    due_date: date | None
    is_overdue: bool
    days_overdue: int
    qc_required: bool
    notes: str
    version: int
    source_warehouse_id: str | None
    receive_location_id: str | None
    material_count: int
    total_sent: Qty
    total_outstanding: Qty
    issued_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobWorkOrderDetailOut(JobWorkOrderOut):
    materials: list[JobWorkMaterialOut]
    receipts: list[JobWorkReceiptOut]
    challans: list[ChallanOut]
    reconciliation: ReconciliationOut
