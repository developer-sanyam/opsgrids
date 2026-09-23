"""Request and response contracts for leave.

This module is the OpenAPI surface: a change here is a change to the published contract, and
the frontend generates its client from it. Every write schema sets `extra="forbid"` — an unknown
field on a write is refused at the boundary rather than silently dropped, so a client typo (or a
field renamed on one side and not the other) is a 422, not a write that quietly ignored part of
what was sent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LeaveRequestStatus = Literal["pending", "approved", "rejected", "cancelled", "failed"]
#: What a caller may post through the balance-grant endpoint. `consumption` and `reversal` are
#: posted only by this service itself, from the approval-decision and cancellation paths — never
#: accepted from an API caller, which is why they are not in this vocabulary.
GrantableEntryType = Literal["grant", "accrual", "adjustment"]
LedgerEntryType = Literal["grant", "accrual", "consumption", "reversal", "adjustment"]

LeaveTypeCode = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- settings


class LeaveSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=64)


class LeaveSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: uuid.UUID
    timezone: str
    updated_at: datetime


# --------------------------------------------------------------------- leave types


class LeaveTypeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = LeaveTypeCode
    name: str = Field(min_length=1, max_length=120)
    is_paid: bool = True
    annual_quota_half_days: int | None = Field(default=None, ge=0)
    monthly_accrual_half_days: int | None = Field(default=None, ge=0)
    carry_forward_cap_half_days: int | None = Field(default=None, ge=0)


class LeaveTypePatch(BaseModel):
    """Partial update. Absent means "leave alone"; `code` is not editable — it is the org handle
    a request references, so a rename is a new type, not a mutation in place."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_paid: bool | None = None
    annual_quota_half_days: int | None = Field(default=None, ge=0)
    monthly_accrual_half_days: int | None = Field(default=None, ge=0)
    carry_forward_cap_half_days: int | None = Field(default=None, ge=0)
    active: bool | None = None


class LeaveTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_paid: bool
    annual_quota_half_days: int | None
    monthly_accrual_half_days: int | None
    carry_forward_cap_half_days: int | None
    active: bool
    created_at: datetime


# --------------------------------------------------------------------- balances


class LeaveBalanceGrantIn(BaseModel):
    """Post a credit to one employee's ledger — a grant, an accrual, or a plain adjustment.

    `idempotency_key` is optional but strongly encouraged: without one, a retried request (a
    client timeout, a double-click) posts a second credit, because there is nothing here for the
    server to recognise as "the same request" (CLAUDE.md's idempotency rule applies to this
    write exactly as it does to a payment).
    """

    model_config = ConfigDict(extra="forbid")

    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    entry_type: GrantableEntryType = "grant"
    half_days: int = Field(gt=0, description="Always positive; this endpoint only credits.")
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class LeaveBalanceOut(BaseModel):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    leave_type_code: str
    balance_half_days: int


class LeaveLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    entry_type: LedgerEntryType
    delta_half_days: int
    leave_request_id: uuid.UUID | None
    note: str | None
    created_at: datetime


# --------------------------------------------------------------------- leave requests


class LeaveRequestIn(BaseModel):
    """Apply for leave. There is no `employee_id`: a person applies for their own leave, so the
    caller's own subject reference is the requester — the same shape `attendance.CheckInIn`
    uses for a self-service write."""

    model_config = ConfigDict(extra="forbid")

    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    half_day_start: bool = False
    half_day_end: bool = False
    reason: str = Field(min_length=1, max_length=500)


class LeaveCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    half_day_start: bool
    half_day_end: bool
    total_half_days: int
    reason: str
    status: LeaveRequestStatus
    approval_request_id: uuid.UUID | None
    approval_opened_at: datetime | None
    approver_employee_id: uuid.UUID | None
    approver_name: str | None
    #: Computed at read time from `approval_opened_at`/`approver_employee_id`, the same shape
    #: `attendance.api.routes._regularization_out` uses — never a stored column, so it cannot
    #: drift from the two fields it is derived from. `None` while routing is not yet known.
    approver_unassigned: bool | None = None
    decided_at: datetime | None
    decision_comment: str | None
    decided_by_name: str | None
    failure_reason: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
