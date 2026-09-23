"""Request and response contracts for leave.

This module is the OpenAPI surface: a change here is a change to the published contract, and
the frontend generates its client from it. Every write schema sets `extra="forbid"` — an unknown
field on a write is refused at the boundary rather than silently dropped, so a client typo (or a
field renamed on one side and not the other) is a 422, not a write that quietly ignored part of
what was sent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: A generous but finite horizon on either side of "today" — the sanity bound every date field
#: on a leave request is checked against, not a business rule about how far ahead leave may be
#: booked. Without it, a client (or a fuzzer) can name a `date` near `date.max`, and
#: `domain.count_half_days`'s day-by-day loop then either runs for millions of iterations or
#: raises a bare `OverflowError` on the final `current += timedelta(days=1)` step past
#: `date.max` — a 500, not a validation error, for input that was never a legitimate request.
MAX_LEAVE_DATE_HORIZON_DAYS = 3650
#: The longest a single leave request may span. Also a sanity bound, not a business rule: a
#: multi-year single request is never a legitimate ask, and without a cap the same
#: near-`date.max` risk applies to `end_date - start_date` even when both dates are individually
#: reasonable.
MAX_LEAVE_SPAN_DAYS = 366

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

    @field_validator("name", "is_paid", "active", mode="before")
    @classmethod
    def _not_null_fields_may_be_omitted_but_not_nulled(cls, value: object, info: object) -> object:
        """`name`, `is_paid` and `active` back `NOT NULL` columns (`app/models.py`'s
        `LeaveType`); the schema's `| None` on each is "omit this key to leave it alone"
        (`services.update_leave_type`'s `model_dump(exclude_unset=True)`), never "send it as
        `null`". A field validator only runs for a key the client actually sent — an absent key
        never reaches this function at all — so this rejects exactly the one shape that would
        otherwise reach the database as a `NOT NULL` violation: an explicit `null`. Refused here,
        at the boundary, as a field-level 422, rather than surfacing as a database
        `IntegrityError` translated into an unhelpful generic 409.
        """
        field_name = getattr(info, "field_name", "field")
        if value is None:
            raise ValueError(f"{field_name} may be omitted, but must not be null")
        return value


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

    @model_validator(mode="after")
    def _dates_are_within_a_sane_horizon(self) -> LeaveRequestIn:
        """A boundary check, not a business rule — `domain.count_half_days` still owns "end
        before start" and "no working days in range" (`services.apply_for_leave` calls it, and
        it raises a `BusinessRuleProblem`, not a 422, for those). This exists only to stop a
        `date` near `date.max` from ever reaching that loop at all — see the module constants'
        own docstrings for the crash it prevents."""
        today = datetime.now(UTC).date()
        horizon = timedelta(days=MAX_LEAVE_DATE_HORIZON_DAYS)
        for field_name, value in (("start_date", self.start_date), ("end_date", self.end_date)):
            if value < today - horizon or value > today + horizon:
                raise ValueError(
                    f"{field_name} must be within {MAX_LEAVE_DATE_HORIZON_DAYS} days of today"
                )
        if (self.end_date - self.start_date).days > MAX_LEAVE_SPAN_DAYS:
            raise ValueError(
                f"a single leave request cannot span more than {MAX_LEAVE_SPAN_DAYS} days"
            )
        return self


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
