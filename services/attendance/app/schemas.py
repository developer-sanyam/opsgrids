"""Request and response contracts for the attendance service.

This module is the published API surface: a change here is a contract change, and the frontend
generates its client from it.

Coordinates are `Decimal` with real bounds (latitude ±90, longitude ±180): the database stores
them as `Numeric`, and a float would quietly lose precision on a boundary the geo-fence test
turns on. Times of day are `datetime.time`; instants are timezone-aware `datetime` in UTC.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["web", "mobile", "biometric"]
WorkMode = Literal["office", "field", "remote", "hybrid"]
AttendanceStatusName = Literal["present", "half_day", "absent", "regularized"]
#: A regularisation may name any observed or corrected state; the state machine in `domain.py`
#: decides whether the specific step from the record's current status is legal.
RegularizeTarget = Literal["present", "half_day", "absent", "regularized"]
RegularizationStatus = Literal["pending", "approved", "rejected", "failed"]

Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]

ShiftCode = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
LocationCode = Annotated[str, Field(min_length=1, max_length=40)]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- attendance


class CheckInIn(BaseModel):
    """Open today's attendance for the calling employee.

    There is no `employee_id`: a person punches their *own* attendance, so the record's employee
    is the caller's subject reference. A field punch (`work_mode="field"`) at a location that
    has a geo-fence must carry a coordinate, and it is checked against the fence server-side.
    """

    work_mode: WorkMode = "office"
    source: Source = "web"
    location_code: LocationCode | None = None
    shift_id: uuid.UUID | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None
    notes: str | None = Field(default=None, max_length=500)


class CheckOutIn(BaseModel):
    """Close today's open attendance for the calling employee."""

    lat: Latitude | None = None
    lng: Longitude | None = None


class RegularizeIn(BaseModel):
    """A direct, audited status correction by an authorised caller. No approval workflow yet
    (that is a later slice) — this is the correction itself, recorded with its reason."""

    to: RegularizeTarget
    reason: str = Field(min_length=1, max_length=500)


class RegularizationRequestIn(BaseModel):
    """A self-service ask for a correction to your own attendance record (ADR-0006 §11).

    There is no `employee_id`, for the same reason `CheckInIn` has none: a person raises a
    request against their own record, and the record named in the path already says whose it is.
    `reason` is required here — unlike the approver's decision comment, which the platform
    deliberately leaves optional (ADR-0006 §7), a request with no stated reason gives an approver
    nothing to route on.
    """

    requested_status: RegularizeTarget
    reason: str = Field(min_length=1, max_length=500)


class RegularizationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attendance_id: uuid.UUID
    employee_id: uuid.UUID
    requested_status: str
    reason: str
    status: RegularizationStatus
    approval_request_id: uuid.UUID | None
    #: Set once `core.approval.request.opened` has been applied to this row — see the field's
    #: docstring on the model. Null while the screen has nothing yet to say beyond "pending".
    approval_opened_at: datetime | None
    #: The routed approver's employee id from `.opened`, or null.
    approver_employee_id: uuid.UUID | None
    #: The routed approver's display name from `.opened`'s own `approver_name`, stored verbatim
    #: (`app/models.py`) — lets the screen render "Waiting on {Name}" directly, without a second
    #: resolve against employee-service. Null in every case `approver_employee_id` is null (routing
    #: not yet known, or the step opened unassigned) *and* whenever approval published the event
    #: without the key at all — an event from before approval's change landed, or a replay.
    approver_name: str | None
    decided_at: datetime | None
    #: The decider's comment from `.decided`, verbatim, or null if none was given or no decision
    #: has landed yet.
    decision_comment: str | None
    #: The decider's display name from `.decided`'s own `decided_by_name`, stored verbatim,
    #: alongside `decision_comment` on a decided row. Null when the decider is not in approval's
    #: hierarchy copy — legitimate for an org-level decider — and whenever the key was simply
    #: absent from the payload; never raised on either way (`app.consumers`).
    decided_by_name: str | None
    failure_reason: str | None
    created_at: datetime
    #: `None` until `.opened` has been applied (`approval_opened_at is None` — nothing is known
    #: about routing yet); `True`/`False` after, mirroring `approver_employee_id is None` at that
    #: point (ADR-0006 §4: a step can open with no approver at all). Computed by the route from
    #: `approval_opened_at`/`approver_employee_id` at read time, not a stored column — see
    #: `api/routes.py::_regularization_out` — so the two stored fields stay the single source of
    #: truth and cannot drift out of sync with a third column that duplicates them.
    approver_unassigned: bool | None = None
    #: The attendance record's own punch location, not a stored column on this row — composed by
    #: the route from the record it was raised against, at read time (open contract question,
    #: settled here: attendance carries no per-employee "home" location the way approval's
    #: hierarchy copy does, ADR-0006 §11's `*_location_code` fields; the punch's own
    #: `location_code` is the nearest fact this surface holds, and is `null` exactly when the
    #: punch itself carried none — the client falls back to labelled organization time, the same
    #: rule ADR-0006 §11 states for approval's responses). Never published on an outbox event:
    #: the `.requested` / `.applied` payloads are ADR-0006 §12's exact contract, not a place to
    #: add a field approval does not ask for.
    location_code: str | None = None


class AttendanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    work_date: date
    check_in_at: datetime
    check_out_at: datetime | None
    source: str
    location_code: str | None
    shift_id: uuid.UUID | None
    work_mode: str
    status: str
    check_in_lat: Decimal | None
    check_in_lng: Decimal | None
    check_out_lat: Decimal | None
    check_out_lng: Decimal | None
    notes: str | None
    created_at: datetime
    #: The most recent regularization request against this record, or null if none has ever been
    #: raised (ADR-0006 §11 / screens doc: pending approval | approved | rejected | failed, with
    #: its reason). Populated by the route from a second table — `from_attributes` never sees it
    #: on the ORM row, which is why the field carries a default rather than being required.
    regularization: RegularizationRequestOut | None = None
    #: What `domain.ALLOWED_TRANSITIONS` permits from this record's *current* status, so a
    #: correction dialog reads the server's own state machine instead of duplicating it
    #: client-side (screens doc, ADR-0006 §11 **C3**).
    allowed_transitions: list[AttendanceStatusName] = Field(default_factory=list)


# --------------------------------------------------------------------- shifts


class ShiftIn(BaseModel):
    code: ShiftCode
    name: str = Field(min_length=1, max_length=120)
    start_time: time
    end_time: time
    grace_minutes: int = Field(default=0, ge=0, le=240)
    is_night: bool = False


class ShiftPatch(BaseModel):
    """Partial update. Absent means "leave alone"; `code` is not editable — it is the org handle
    other records reference, so a rename is a new shift, not a mutation of the code in place."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    start_time: time | None = None
    end_time: time | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=240)
    is_night: bool | None = None


class ShiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    start_time: time
    end_time: time
    grace_minutes: int
    is_night: bool
    created_at: datetime


# --------------------------------------------------------------------- geofences


class GeofenceIn(BaseModel):
    location_code: LocationCode
    name: str = Field(min_length=1, max_length=120)
    center_lat: Latitude
    center_lng: Longitude
    radius_m: int = Field(ge=1, le=100_000)


class GeofencePatch(BaseModel):
    """Partial update. Absent means "leave alone"; `location_code` is the org handle a punch is
    matched against, so it is not renamed in place."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    center_lat: Latitude | None = None
    center_lng: Longitude | None = None
    radius_m: int | None = Field(default=None, ge=1, le=100_000)


class GeofenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    location_code: str
    name: str
    center_lat: Decimal
    center_lng: Decimal
    radius_m: int
    created_at: datetime
