"""Pydantic contracts for manufacturing — the OpenAPI surface. Quantities are decimal strings
(at most three decimals), percentages decimal strings (at most two); ids are strings. Nothing
here is ever a float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.manufacturing.domain import OUTPUT_STATUSES, WASTAGE_TYPES  # noqa: F401
from app.schemas import Qty

#: A percentage as a decimal string: `"2.5"`. At most two decimals.
Pct = Annotated[str, Field(pattern=r"^\d{1,3}(\.\d{1,2})?$", examples=["2.5"])]

BomVersionStatus = Literal["DRAFT", "PUBLISHED", "RETIRED"]
ProductionOrderStatus = Literal["DRAFT", "APPROVED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
WastageType = Literal[
    "CUTTING", "DEFECT", "SPILLAGE", "DAMAGE", "PROCESS_LOSS", "LEFTOVER", "OTHER"
]
OutputStatus = Literal["AVAILABLE", "QUALITY_HOLD"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ BOMs


class BomSubstituteIn(_In):
    substitute_item_id: UUID
    #: How much substitute replaces one unit of the component: `"1"` is 1:1, `"1.25"` is 1.25.
    ratio: Qty = "1"
    priority: int = Field(default=1, ge=1, le=99)


class BomSubstituteOut(BaseModel):
    id: str
    substitute_item_id: str
    sku: str
    name: str
    uom: str
    ratio: str
    priority: int


class BomComponentIn(_In):
    component_item_id: UUID
    quantity: Qty
    scrap_percent: Pct = "0"
    effective_from: date | None = None
    effective_to: date | None = None
    notes: str = Field(default="", max_length=300)


class BomComponentPatch(_In):
    quantity: Qty | None = None
    scrap_percent: Pct | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    notes: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _no_null_required(self) -> Self:
        for name in ("quantity", "scrap_percent", "notes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class BomComponentOut(BaseModel):
    id: str
    line_no: int
    component_item_id: str
    sku: str
    name: str
    uom: str
    quantity: str
    scrap_percent: str
    effective_from: date | None
    effective_to: date | None
    notes: str
    #: True when this component has a BOM of its own — what makes the BOM multi-level.
    is_produced: bool
    substitutes: list[BomSubstituteOut]


class BomVersionIn(_In):
    output_quantity: Qty = "1"
    effective_from: date | None = None
    effective_to: date | None = None
    notes: str = Field(default="", max_length=500)
    #: Start the new draft from an earlier version's lines (components and substitutes).
    copy_from_version_id: UUID | None = None


class BomVersionPatch(_In):
    output_quantity: Qty | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _no_null_required(self) -> Self:
        for name in ("output_quantity", "notes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class BomVersionOut(BaseModel):
    id: str
    bom_id: str
    version_no: int
    status: BomVersionStatus
    output_quantity: str
    effective_from: date | None
    effective_to: date | None
    notes: str
    published_at: datetime | None
    published_by: str | None
    content_hash: str | None
    component_count: int
    created_at: datetime
    updated_at: datetime


class BomVersionDetailOut(BomVersionOut):
    components: list[BomComponentOut]


class BomIn(_In):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    item_id: UUID
    description: str = Field(default="", max_length=500)
    #: The batch size the first draft's component quantities are stated for.
    output_quantity: Qty = "1"


class BomPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_null(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class BomOut(BaseModel):
    id: str
    code: str
    name: str
    item_id: str
    sku: str
    item_name: str
    uom: str
    description: str
    is_active: bool
    version_count: int
    latest_version_no: int | None
    #: The published version in force today (null while nothing is published).
    current_version_id: str | None
    current_version_no: int | None
    created_at: datetime
    updated_at: datetime


class BomDetailOut(BomOut):
    versions: list[BomVersionOut]


class ExplosionNodeOut(BaseModel):
    item_id: str
    sku: str
    name: str
    uom: str
    level: int
    quantity: str
    is_produced: bool
    children: list[ExplosionNodeOut]


class ExplosionTotalOut(BaseModel):
    item_id: str
    sku: str
    name: str
    uom: str
    quantity: str


class ExplosionOut(BaseModel):
    """The multi-level requirement for `quantity` of the BOM's item: the tree, and the purchased
    (leaf) items it adds up to."""

    bom_id: str
    version_id: str
    quantity: str
    root: ExplosionNodeOut
    leaf_totals: list[ExplosionTotalOut]


# ------------------------------------------------------------------ production orders


class ProductionOrderIn(_In):
    #: Name the BOM, or the produced item (which has one live BOM).
    bom_id: UUID | None = None
    item_id: UUID | None = None
    #: Pin a specific published version; default is the version in force on `planned_start`
    #: (or today).
    bom_version_id: UUID | None = None
    quantity: Qty
    output_location_id: UUID
    wip_location_id: UUID | None = None
    planned_start: date | None = None
    due_date: date | None = None
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _names_a_bom(self) -> Self:
        if self.bom_id is None and self.item_id is None:
            raise ValueError("Name the bom_id or the item_id to produce")
        return self


class ProductionOrderPatch(_In):
    quantity: Qty | None = None
    output_location_id: UUID | None = None
    wip_location_id: UUID | None = None
    planned_start: date | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _no_null_required(self) -> Self:
        for name in ("quantity", "output_location_id", "notes"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ProductionCancelIn(_In):
    reason: str = Field(default="", max_length=500)


class MaterialSubstituteIn(_In):
    #: One of the BOM line's declared substitutes; omit to go back to the BOM's own item.
    substitute_item_id: UUID | None = None


class ReserveLineIn(_In):
    material_id: UUID
    location_id: UUID | None = None
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    #: Default: everything the material still needs.
    quantity: Qty | None = None


class ReserveIn(_In):
    #: Omit to reserve every material's open need from whatever AVAILABLE stock there is.
    lines: list[ReserveLineIn] | None = Field(default=None, max_length=200)


class IssueLineIn(_In):
    material_id: UUID | None = None
    pick_id: UUID | None = None
    location_id: UUID | None = None
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    #: Default: the pick's whole open reservation.
    quantity: Qty | None = None

    @model_validator(mode="after")
    def _names_a_target(self) -> Self:
        if self.pick_id is None and self.material_id is None:
            raise ValueError("Name the pick_id, or the material_id with a location")
        if self.pick_id is None and self.location_id is None:
            raise ValueError("Issuing without a pick needs a location_id")
        return self


class IssueIn(_In):
    #: Omit to issue every open reservation in full (the BOM-based issue).
    lines: list[IssueLineIn] | None = Field(default=None, max_length=200)
    #: Allow issuing past a material's requirement (over-use is then a variance).
    over_issue: bool = False


class ReturnLineIn(_In):
    pick_id: UUID
    #: Default: everything of the pick still in WIP.
    quantity: Qty | None = None
    #: Default: the location it was issued from.
    location_id: UUID | None = None


class ReturnIn(_In):
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=200)


class OutputIn(_In):
    quantity: Qty
    location_id: UUID | None = None
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    status: OutputStatus = "AVAILABLE"
    note: str = Field(default="", max_length=300)


class WastageIn(_In):
    material_id: UUID
    wastage_type: WastageType
    quantity: Qty
    #: Draw from one pick (lot); default: oldest issued first.
    pick_id: UUID | None = None
    note: str = Field(default="", max_length=300)


class CompleteIn(_In):
    #: What to do with material still in WIP: put it back, or write it off as wastage. Required
    #: when anything is left.
    leftover: Literal["return", "scrap"] | None = None
    note: str = Field(default="", max_length=300)


class ProductionMaterialOut(BaseModel):
    id: str
    line_no: int
    component_item_id: str
    component_sku: str
    item_id: str
    sku: str
    name: str
    uom: str
    is_substitute: bool
    ratio: str
    scrap_percent: str
    required: str
    reserved: str
    issued: str
    consumed: str
    returned: str
    wasted: str
    wip: str
    #: What is still not covered by a reservation or an issue.
    open_need: str


class ProductionPickOut(BaseModel):
    id: str
    material_id: str
    item_id: str
    source_location_id: str
    wip_location_id: str
    lot_id: str | None
    roll_id: str | None
    reservation_id: str | None
    reserved: str
    issued: str
    consumed: str
    returned: str
    wasted: str
    wip: str


class ProductionOutputOut(BaseModel):
    id: str
    production_order_id: str
    item_id: str
    quantity: str
    location_id: str
    status: OutputStatus
    lot_id: str | None
    roll_id: str | None
    note: str
    created_at: datetime


class ProductionWastageOut(BaseModel):
    id: str
    production_order_id: str
    material_id: str
    pick_id: str
    event_id: str
    item_id: str
    wastage_type: WastageType
    quantity: str
    location_id: str
    lot_id: str | None
    roll_id: str | None
    note: str
    created_at: datetime


class ProductionGenealogyOut(BaseModel):
    id: str
    output_id: str
    parent_item_id: str
    parent_lot_id: str | None
    parent_roll_id: str | None
    child_item_id: str
    child_lot_id: str | None
    child_roll_id: str | None
    quantity: str


class VarianceLineOut(BaseModel):
    material_id: str
    item_id: str
    sku: str
    name: str
    uom: str
    #: What the BOM (scrap allowance included) says the produced quantity should have taken.
    standard: str
    #: The same, without the scrap allowance.
    theoretical: str
    consumed: str
    wasted: str
    #: consumed + wasted.
    actual: str
    #: actual - standard; positive is over-use.
    variance: str
    #: variance as a percentage of standard, two decimals; null when standard is zero.
    variance_percent: str | None


class VarianceOut(BaseModel):
    planned_quantity: str
    produced_quantity: str
    lines: list[VarianceLineOut]


class ProductionOrderOut(BaseModel):
    id: str
    number: str
    status: ProductionOrderStatus
    bom_id: str
    bom_code: str
    bom_version_id: str
    bom_version_no: int
    item_id: str
    sku: str
    name: str
    uom: str
    planned_quantity: str
    produced_quantity: str
    output_location_id: str
    wip_location_id: str | None
    planned_start: date | None
    due_date: date | None
    approved_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str
    notes: str
    created_at: datetime
    updated_at: datetime


class ProductionOrderDetailOut(ProductionOrderOut):
    materials: list[ProductionMaterialOut]
    picks: list[ProductionPickOut]
    outputs: list[ProductionOutputOut]
    wastage: list[ProductionWastageOut]
    variance: VarianceOut


class ShortageOut(BaseModel):
    material_id: str
    item_id: str
    sku: str
    required: str
    covered: str
    short: str


class ProductionActionOut(ProductionOrderDetailOut):
    """The order after an action. `shortages` is filled by a reservation that could not cover
    everything (and then `operations.production.material_shortage` was staged)."""

    shortages: list[ShortageOut] = []
    #: True when an `Idempotency-Key` matched an earlier request: nothing new was done.
    replayed: bool = False


# ------------------------------------------------------------------ wastage thresholds


class WastageThresholdIn(_In):
    #: The produced item this applies to; omit for every product.
    item_id: UUID | None = None
    #: Omit for every wastage type.
    wastage_type: WastageType | None = None
    max_percent: Pct


class WastageThresholdOut(BaseModel):
    id: str
    item_id: str | None
    sku: str | None
    wastage_type: WastageType | None
    max_percent: str
    created_at: datetime
