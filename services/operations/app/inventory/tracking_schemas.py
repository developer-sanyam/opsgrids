"""Wire contracts for lots, rolls, stock operations and the stock/ledger/trace reads (inventory
Phase 1, I3-I5). Kept beside the code that serves them rather than in `app/schemas.py`, which is
already the largest module in the service; they are published through the same OpenAPI document.

Every write model forbids unknown fields. Every quantity is a decimal **string** with at most
three decimals (`Qty`, ADR-0014 §2) — a JSON number is refused by type, so a float can never
reach the domain layer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.domain import MAX_LOT_ATTRIBUTES
from app.schemas import Qty, StockStatus

LotQualityStatus = Literal["PENDING", "APPROVED", "ON_HOLD", "REJECTED"]
RollState = Literal["ACTIVE", "SPLIT", "MERGED"]
LotRelation = Literal["SPLIT", "MERGE", "BLEND", "REWORK", "DERIVED"]
#: What a stock operation may move between: never the off-site statuses, which need the documents
#: (transfer orders, job-work orders) of Phases 3 and 6.
OnSiteStatus = Literal[
    "AVAILABLE", "QUALITY_HOLD", "QUARANTINE", "DAMAGED", "REJECTED", "WIP", "BLOCKED"
]

AttributeValue = StrictStr | StrictInt | StrictBool
_ATTRIBUTE_KEY = r"^[A-Za-z][A-Za-z0-9_]{0,39}$"


def _check_attributes(value: dict[str, AttributeValue] | None) -> dict[str, AttributeValue] | None:
    if value is None:
        return None
    if len(value) > MAX_LOT_ATTRIBUTES:
        raise ValueError(f"a lot carries at most {MAX_LOT_ATTRIBUTES} attributes")
    for key, item in value.items():
        if not re.match(_ATTRIBUTE_KEY, key):
            raise ValueError(f"attribute key {key!r} is not a letter followed by letters/digits/_")
        if isinstance(item, str) and len(item) > 200:
            raise ValueError(f"attribute {key!r} is longer than 200 characters")
        if isinstance(item, int) and not isinstance(item, bool) and abs(item) > 10**12:
            raise ValueError(f"attribute {key!r} is too large")
    return value


# ------------------------------------------------------------------ lots


class LotIn(BaseModel):
    """`POST /inventory/lots`. `lot_no` is generated (`LOT-` + 8 hex) when omitted."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    lot_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )
    supplier_lot_no: str | None = Field(default=None, min_length=1, max_length=60)
    manufactured_on: date | None = None
    received_on: date | None = None
    expires_on: date | None = None
    quality_status: LotQualityStatus = "PENDING"
    source_type: str | None = Field(default=None, min_length=1, max_length=30)
    source_id: UUID | None = None
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
        return _check_attributes(value) or {}

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.source_type is None) != (self.source_id is None):
            raise ValueError("source_type and source_id are given together, or not at all")
        if self.manufactured_on and self.expires_on and self.expires_on < self.manufactured_on:
            raise ValueError("expires_on is before manufactured_on")
        return self


class LotPatch(BaseModel):
    """`PATCH /inventory/lots/{id}` — only what can change after the fact. The item and the
    internal lot number never do (the ledger already refers to them)."""

    model_config = ConfigDict(extra="forbid")

    supplier_lot_no: str | None = Field(default=None, min_length=1, max_length=60)
    manufactured_on: date | None = None
    received_on: date | None = None
    expires_on: date | None = None
    quality_status: LotQualityStatus | None = None
    attributes: dict[str, AttributeValue] | None = None

    @field_validator("attributes")
    @classmethod
    def _attributes(
        cls, value: dict[str, AttributeValue] | None
    ) -> dict[str, AttributeValue] | None:
        return _check_attributes(value)

    @model_validator(mode="after")
    def _no_null_required(self) -> Self:
        for name in ("quality_status", "attributes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class LotGenealogyIn(BaseModel):
    """`POST /inventory/lots/{id}/genealogy` — this lot's material went into `child_lot_id`."""

    model_config = ConfigDict(extra="forbid")

    child_lot_id: UUID
    relation: Literal["BLEND", "REWORK", "DERIVED"]
    note: str = Field(default="", max_length=500)


class LotOut(BaseModel):
    id: str
    lot_no: str
    supplier_lot_no: str | None
    inventory_item_id: str
    sku: str
    uom: str
    manufactured_on: date | None
    received_on: date | None
    expires_on: date | None
    quality_status: LotQualityStatus
    source_type: str | None
    source_id: str | None
    attributes: dict[str, Any]
    #: Physical stock of this lot across every bucket (not off-site statuses), exact.
    on_hand_qty: Qty
    created_at: datetime
    updated_at: datetime


class LotGenealogyOut(BaseModel):
    id: str
    parent_lot_id: str
    child_lot_id: str
    relation: LotRelation
    move_id: str | None
    note: str
    created_at: datetime


# ------------------------------------------------------------------ rolls


class RollIn(BaseModel):
    """`POST /inventory/rolls` — register a roll. It holds no stock until an opening (or a later
    receipt) posts into its bucket. `primary_qty` is in the item's own UOM (82.5 m); the measured
    secondary quantity is a separate fact with its own unit (14.8 kg), never derived."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    lot_id: UUID | None = None
    roll_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )
    primary_qty: Qty
    secondary_qty: Qty | None = None
    secondary_uom: str | None = Field(default=None, min_length=1, max_length=10)
    width_mm: int | None = Field(default=None, gt=0, le=100_000)
    gsm: int | None = Field(default=None, gt=0, le=100_000)
    shade: str | None = Field(default=None, min_length=1, max_length=40)
    grade: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def _secondary_pair(self) -> Self:
        if (self.secondary_qty is None) != (self.secondary_uom is None):
            raise ValueError("secondary_qty and secondary_uom are given together, or not at all")
        return self


class RollPatch(BaseModel):
    """`PATCH /inventory/rolls/{id}` — re-measure or re-classify. The primary quantity is not
    editable: how much is physically there is the ledger's to say (use a count)."""

    model_config = ConfigDict(extra="forbid")

    secondary_qty: Qty | None = None
    secondary_uom: str | None = Field(default=None, min_length=1, max_length=10)
    width_mm: int | None = Field(default=None, gt=0, le=100_000)
    gsm: int | None = Field(default=None, gt=0, le=100_000)
    shade: str | None = Field(default=None, min_length=1, max_length=40)
    grade: str | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def _secondary_pair(self) -> Self:
        given = self.model_fields_set
        if ("secondary_qty" in given) != ("secondary_uom" in given):
            raise ValueError("secondary_qty and secondary_uom change together")
        if ("secondary_qty" in given) and (self.secondary_qty is None) != (
            self.secondary_uom is None
        ):
            raise ValueError("secondary_qty and secondary_uom are both set or both null")
        return self


class RollOut(BaseModel):
    id: str
    roll_no: str
    inventory_item_id: str
    sku: str
    uom: str
    lot_id: str | None
    lot_no: str | None
    state: RollState
    primary_qty: Qty
    secondary_qty: Qty | None
    secondary_uom: str | None
    width_mm: int | None
    gsm: int | None
    shade: str | None
    grade: str | None
    parent_roll_id: str | None
    #: Physical stock of this roll across every bucket (not off-site statuses), exact.
    on_hand_qty: Qty
    created_at: datetime
    updated_at: datetime


class RollEventOut(BaseModel):
    id: str
    kind: str
    related_roll_id: str | None
    move_id: str | None
    quantity: Qty | None
    secondary_qty: Qty | None
    detail: dict[str, Any]
    occurred_at: datetime


# ------------------------------------------------------------------ stock operations


class OpeningLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    location_id: UUID
    quantity: Qty
    status: OnSiteStatus = "AVAILABLE"
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    #: Capped well under `BIGINT` so `quantity * cost` and the column both stay representable —
    #: an out-of-range figure is a 422 here, not a database overflow (500) later.
    unit_cost_minor: int | None = Field(default=None, ge=0, le=10**15)
    unit_cost_currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _cost_pair(self) -> Self:
        if (self.unit_cost_minor is None) != (self.unit_cost_currency is None):
            raise ValueError("unit_cost_minor and unit_cost_currency are given together")
        return self


class OpeningStockIn(BaseModel):
    """`POST /inventory/stock/opening` — bring existing stock onto the books, per location, lot
    and roll. A single `opening` move; all lines post or none do."""

    model_config = ConfigDict(extra="forbid")

    lines: list[OpeningLineIn] = Field(min_length=1, max_length=100)
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class StockTransferIn(BaseModel):
    """`POST /inventory/stock/transfers` — bin to bin inside one warehouse, immediate."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    quantity: Qty
    status: OnSiteStatus = "AVAILABLE"
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.from_location_id == self.to_location_id:
            raise ValueError("from_location_id and to_location_id must differ")
        return self

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class StockStatusChangeIn(BaseModel):
    """`POST /inventory/stock/status-changes` — move stock between statuses where it sits (e.g.
    AVAILABLE -> QUALITY_HOLD): one paired move, physical total unchanged (ADR-0014 §4)."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    location_id: UUID
    from_status: OnSiteStatus
    to_status: OnSiteStatus
    quantity: Qty
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _differ(self) -> Self:
        if self.from_status == self.to_status:
            raise ValueError("from_status and to_status must differ")
        return self

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class SplitChildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: Qty
    roll_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )


class RollSplitIn(BaseModel):
    """`POST /inventory/rolls/{id}:split` — one roll into n. The children's quantities must add
    up exactly to what the roll's bucket holds; the roll's measured weight is apportioned in
    proportion (floor, remainder to the last child)."""

    model_config = ConfigDict(extra="forbid")

    location_id: UUID
    status: OnSiteStatus = "AVAILABLE"
    children: list[SplitChildIn] = Field(min_length=2, max_length=50)
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class RollMergeIn(BaseModel):
    """`POST /inventory/rolls:merge` — n rolls of one item, lot and shade, all in the named bucket,
    into one new roll holding their combined quantity."""

    model_config = ConfigDict(extra="forbid")

    roll_ids: list[UUID] = Field(min_length=2, max_length=50)
    location_id: UUID
    status: OnSiteStatus = "AVAILABLE"
    roll_no: str | None = Field(
        default=None, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._/-]+$"
    )
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.roll_ids)) != len(self.roll_ids):
            raise ValueError("roll_ids must be distinct")
        return self

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class StockMoveLineOut(BaseModel):
    ledger_entry_id: str
    inventory_item_id: str
    location_id: str
    status: str
    lot_id: str | None
    roll_id: str | None
    quantity: Qty
    balance_after: Qty


class StockMoveOut(BaseModel):
    """The move a stock operation posted. `replayed` is true when an `Idempotency-Key` matched an
    earlier request: nothing new was written and this is that request's result."""

    move_id: str
    kind: str
    reference_type: str | None
    reference_id: str | None
    occurred_on: date
    replayed: bool
    lines: list[StockMoveLineOut]


class RollSplitOut(BaseModel):
    move: StockMoveOut
    parent: RollOut
    children: list[RollOut]


class RollMergeOut(BaseModel):
    move: StockMoveOut
    result: RollOut
    sources: list[RollOut]


# ------------------------------------------------------------------ reads


class StockRowOut(BaseModel):
    """One bucket of stock: an item at a location, in a status, optionally in a lot / on a roll."""

    id: str
    inventory_item_id: str
    sku: str
    item_name: str
    uom: str
    warehouse_id: str
    warehouse_code: str
    location_id: str
    location_code: str
    status: StockStatus
    lot_id: str | None
    lot_no: str | None
    roll_id: str | None
    roll_no: str | None
    on_hand_qty: Qty
    reserved_qty: Qty
    available_qty: Qty


class LedgerRowOut(BaseModel):
    id: str
    move_id: str
    kind: str
    reason: str | None
    reference_type: str | None
    reference_id: str | None
    note: str
    occurred_on: date
    inventory_item_id: str
    sku: str
    uom: str
    location_id: str
    location_code: str
    status: str
    lot_id: str | None
    roll_id: str | None
    quantity: Qty
    balance_after: Qty
    created_at: datetime
    created_by: str | None


class TraceNodeOut(BaseModel):
    """One lot or roll reached by a trace. `from_id` is the node it was reached from (so the
    list is a tree, root excluded); `relation` / `move_id` say how and by which move."""

    id: str
    number: str
    depth: int
    from_id: str
    relation: str | None
    move_id: str | None
    lot_id: str | None = None
    lot_no: str | None = None


class LotTraceOut(BaseModel):
    """A lot with every lot it came from (`ancestors`, nearest first) and everything that came
    of it (`descendants`)."""

    lot: LotOut
    ancestors: list[TraceNodeOut]
    descendants: list[TraceNodeOut]


class RollTraceOut(BaseModel):
    """A roll with the rolls it came from and the rolls that came of it, and its lot — so the
    walk child -> parent -> lot -> source document is one response. `lot_trace` is the lot's own
    ancestry (where that lot came from)."""

    roll: RollOut
    lot: LotOut | None
    ancestors: list[TraceNodeOut]
    descendants: list[TraceNodeOut]
    lot_ancestors: list[TraceNodeOut]
