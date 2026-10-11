"""Request and response contracts for quality (plan Phase 4) — part of the OpenAPI surface.

Quantities, bounds and readings are exact decimal **strings** with at most three decimals
(`Qty`), never JSON numbers; the server converts to milli-units at the edge.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Qty

QcKind = Literal["incoming", "in-process", "finished"]
ParamKind = Literal["numeric", "pass_fail"]
InspectionStatus = Literal["PENDING", "PASSED", "FAILED", "HOLD", "REWORK", "PARTIAL_PASS"]
RejectBucket = Literal["REJECTED", "QUARANTINE"]
DispositionAction = Literal["RETURN_TO_VENDOR", "SCRAP"]
Disposition = Literal["NONE", "RETURN_TO_VENDOR", "SCRAP"]


# ------------------------------------------------------------------ templates


class QcParamIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    kind: ParamKind = "numeric"
    unit: str = Field(default="", max_length=20)
    min: Qty | None = None
    max: Qty | None = None
    is_critical: bool = False

    @model_validator(mode="after")
    def _bounds_only_on_numeric(self) -> QcParamIn:
        if self.kind == "pass_fail" and (self.min is not None or self.max is not None):
            raise ValueError("A pass/fail parameter has no bounds")
        return self


class QcParamOut(BaseModel):
    id: str
    name: str
    kind: ParamKind
    unit: str
    min: str | None
    max: str | None
    is_critical: bool
    position: int


class QcTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=160)
    kind: QcKind
    item_id: UUID | None = None
    description: str = Field(default="", max_length=500)
    is_active: bool = True
    params: list[QcParamIn] = Field(default_factory=list, max_length=100)


class QcTemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    #: Present = replace the whole parameter list; absent = leave it alone.
    params: list[QcParamIn] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> QcTemplatePatch:
        for field in ("name", "description", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class QcTemplateOut(BaseModel):
    id: str
    code: str
    name: str
    kind: QcKind
    item_id: str | None
    description: str
    is_active: bool
    params: list[QcParamOut]
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ inspections


class InspectionIn(BaseModel):
    """`POST /quality/inspections`. By default the inspected quantity is moved `AVAILABLE ->
    QUALITY_HOLD` in the same transaction; `hold_stock: false` says it is already on hold
    (received there), which is verified against the balance."""

    model_config = ConfigDict(extra="forbid")

    kind: QcKind
    item_id: UUID
    location_id: UUID
    quantity: Qty
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    template_id: UUID | None = None
    supplier_id: UUID | None = None
    reference_type: str = Field(default="", max_length=40)
    reference_id: UUID | None = None
    hold_stock: bool = True
    notes: str = Field(default="", max_length=500)


class InspectionResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    param_name: str = Field(min_length=1, max_length=80)
    #: Numeric parameters: the reading. Ignored for pass/fail.
    value: Qty | None = None
    #: Pass/fail parameters: the verdict. Ignored (the server judges) for numeric.
    passed: bool | None = None
    note: str = Field(default="", max_length=500)


class InspectionResultsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[InspectionResultIn] = Field(min_length=1, max_length=100)


class InspectionResultOut(BaseModel):
    id: str
    param_name: str
    param_kind: ParamKind
    unit: str
    min: str | None
    max: str | None
    is_critical: bool
    value: str | None
    passed: bool
    note: str
    recorded_by: str | None


class InspectionOut(BaseModel):
    id: str
    kind: QcKind
    status: InspectionStatus
    template_id: str | None
    item_id: str
    item_sku: str
    item_name: str
    uom: str
    location_id: str
    location_code: str
    lot_id: str | None
    roll_id: str | None
    supplier_id: str | None
    reference_type: str
    reference_id: str | None
    quantity: Qty
    accepted: Qty | None
    rejected: Qty | None
    rejected_bucket: RejectBucket | None
    notes: str
    decision_note: str
    decided_by: str | None
    decided_at: datetime | None
    disposition: Disposition
    disposition_note: str
    disposition_reference: str
    disposed_at: datetime | None
    hold_move_id: str | None
    decision_move_id: str | None
    disposition_move_id: str | None
    #: What the readings alone recommend (never applied automatically) and whether a release is
    #: currently allowed — a failed critical parameter blocks it.
    suggested_outcome: InspectionStatus
    can_release: bool
    results: list[InspectionResultOut]
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class InspectionNoteIn(BaseModel):
    """`hold` / `rework` / `reinspect` — a note for the audit trail."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class ReleaseIn(BaseModel):
    """`POST /quality/inspections/{id}/release` — omit `accepted` to release everything
    (`PASSED`); a smaller figure is a `PARTIAL_PASS` and the remainder goes to `rejected_bucket`."""

    model_config = ConfigDict(extra="forbid")

    accepted: Qty | None = None
    rejected_bucket: RejectBucket = "REJECTED"
    note: str = Field(default="", max_length=500)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    bucket: RejectBucket = "REJECTED"


class DispositionIn(BaseModel):
    """The follow-on for stock a rejection left behind: return it to the supplier, or scrap it."""

    model_config = ConfigDict(extra="forbid")

    action: DispositionAction
    note: str = Field(default="", max_length=500)
    #: A debit-note / RMA / gate-pass number, whatever the supplier or auditor will ask for.
    reference: str = Field(default="", max_length=80)
