"""Pydantic contracts for MRP — the OpenAPI surface. Quantities are decimal strings (at most three
decimals); ids are strings. Nothing here is ever a float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Qty

RunStatus = Literal["QUEUED", "COMPLETED", "FAILED"]
DemandSource = Literal["SALES_ORDER", "PRODUCTION_ORDER", "MANUAL"]
ShortageAction = Literal["BUY", "MAKE"]
RunMode = Literal["auto", "sync", "worker"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _all_sources() -> list[DemandSource]:
    return ["SALES_ORDER", "PRODUCTION_ORDER", "MANUAL"]


class ManualDemandIn(_In):
    item_id: UUID
    quantity: Qty
    due_date: date | None = None
    note: str = Field(default="", max_length=200)


class SubstituteChoiceIn(_In):
    """Plan with `substitute_item_id` wherever a BOM line names `component_item_id` and declares
    that substitute. Substitutes are ignored unless chosen here."""

    component_item_id: UUID
    substitute_item_id: UUID


class MrpRunIn(_In):
    sources: list[DemandSource] = Field(default_factory=_all_sources, max_length=3)
    manual_demands: list[ManualDemandIn] = Field(default_factory=list, max_length=500)
    substitutes: list[SubstituteChoiceIn] = Field(default_factory=list, max_length=200)
    #: BOM versions and component windows are evaluated on this date (default: today).
    plan_date: date | None = None
    notes: str = Field(default="", max_length=300)
    #: `auto` runs small organizations inside the request and queues large ones for the worker.
    mode: RunMode = "auto"

    @model_validator(mode="after")
    def _consistent(self) -> MrpRunIn:
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("sources must not repeat")
        if not self.sources:
            raise ValueError("choose at least one demand source")
        if self.manual_demands and "MANUAL" not in self.sources:
            raise ValueError("manual_demands were given but MANUAL is not among the sources")
        if "MANUAL" in self.sources and set(self.sources) == {"MANUAL"} and not self.manual_demands:
            raise ValueError("MANUAL is the only source but no manual_demands were given")
        return self


class MrpRunOut(BaseModel):
    id: str
    number: str
    status: RunStatus
    execution: Literal["SYNC", "WORKER"]
    plan_date: date
    sources: list[str]
    notes: str
    demand_count: int
    shortage_count: int
    items_planned: int
    result_hash: str | None
    error: str
    requested_by: str | None
    created_at: datetime
    completed_at: datetime | None


class MrpRunCreatedOut(MrpRunOut):
    #: True when an earlier request with the same Idempotency-Key already created this run.
    replayed: bool = False


class MrpDemandOut(BaseModel):
    id: str
    source_type: DemandSource
    source_id: str | None
    source_ref: str
    item_id: str
    sku: str
    name: str
    uom: str
    requested: str
    reserved: str
    quantity: str
    due_date: date | None
    note: str


class MrpShortageLineOut(BaseModel):
    source_type: DemandSource
    source_id: str | None
    source_ref: str
    parent_item_id: str | None
    parent_sku: str | None
    quantity: str
    due_date: date | None


class MrpShortageOut(BaseModel):
    id: str
    run_id: str
    item_id: str
    sku: str
    name: str
    uom: str
    action: ShortageAction
    level: int
    gross: str
    on_hand: str
    reserved: str
    available: str
    open_po: str
    open_production: str
    supply: str
    shortage: str
    need_by: date | None
    requisition_request_id: str | None
    requisition_requested_at: datetime | None
    requisition_id: str | None


class MrpShortageDetailOut(MrpShortageOut):
    lines: list[MrpShortageLineOut]


class RequisitionRequestIn(_In):
    #: Shortages to request; omitted means every un-requested `BUY` shortage of the run.
    shortage_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=500)
    note: str = Field(default="", max_length=300)


class RequisitionRequestLineOut(BaseModel):
    shortage_id: str
    item_id: str
    sku: str
    uom: str
    quantity: str
    need_by: date | None


class RequisitionRequestOut(BaseModel):
    request_id: str
    run_id: str
    run_number: str
    lines: list[RequisitionRequestLineOut]
    replayed: bool = False


class DemandSourcesOut(BaseModel):
    """What the next run would plan from, so the screen can say so before it is started."""

    sales_order_lines: int
    production_order_materials: int
    sync_limit: int
    mode_if_auto: Literal["sync", "worker"]
