"""Request and response contracts for the employee service.

This module is the published API surface: a change here is a contract change, and the frontend
generates its client from it.

Note what is *absent*: no date of birth, no personal phone or email, no PAN/Aadhaar/UAN/ESI,
no bank details, no documents. Those are sensitive PII and there is no envelope helper in
platform-core yet, so this slice does not accept or return any of them — they land with the
envelope in slice 2 (see the models docstring and the backlog).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EmployeeNo = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
EmploymentType = Literal["full_time", "part_time", "contract", "intern"]
WorkMode = Literal["office", "field", "remote", "hybrid"]
Status = Literal["probation", "active", "on_notice", "exited"]
#: Only the non-terminal targets a transition may name — `probation` is an entry state a
#: transition never moves *to*, so it is excluded from the request contract.
TransitionTarget = Literal["active", "on_notice", "exited"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class EmployeeIn(BaseModel):
    """Create an employment record.

    `status` is not accepted: a new record always starts on probation, and the lifecycle is
    the only way it moves from there.
    """

    employee_no: EmployeeNo
    full_name: str = Field(min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: uuid.UUID | None = None
    location_code: str | None = Field(default=None, max_length=40)
    employment_type: EmploymentType = "full_time"
    work_mode: WorkMode = "office"
    date_of_joining: date | None = None
    reporting_manager_id: uuid.UUID | None = None
    membership_id: uuid.UUID | None = None


class EmployeePatch(BaseModel):
    """Partial update. Absent means "leave alone"; the lifecycle status is not editable here —
    it moves only through the transition endpoint and its state machine."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: uuid.UUID | None = None
    location_code: str | None = Field(default=None, max_length=40)
    employment_type: EmploymentType | None = None
    work_mode: WorkMode | None = None
    date_of_joining: date | None = None
    reporting_manager_id: uuid.UUID | None = None


class TransitionIn(BaseModel):
    to: TransitionTarget
    reason: str | None = Field(default=None, max_length=500)


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    membership_id: uuid.UUID | None
    employee_no: str
    full_name: str
    designation: str | None
    job_title: str | None
    department_id: uuid.UUID | None
    location_code: str | None
    employment_type: str
    work_mode: str
    date_of_joining: date | None
    reporting_manager_id: uuid.UUID | None
    status: str
    date_of_exit: date | None
    created_at: datetime
