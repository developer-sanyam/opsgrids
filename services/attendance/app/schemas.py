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
