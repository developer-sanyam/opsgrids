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
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain import normalise_cidr

Source = Literal["web", "mobile", "biometric"]
WorkMode = Literal["office", "field", "remote", "hybrid"]
AttendanceStatusName = Literal["present", "half_day", "absent", "regularized"]
#: A regularisation may name any observed or corrected state; the state machine in `domain.py`
#: decides whether the specific step from the record's current status is legal.
RegularizeTarget = Literal["present", "half_day", "absent", "regularized"]
RegularizationStatus = Literal["pending", "approved", "rejected", "failed"]

Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]
#: A fix's stated accuracy radius in metres. 100 km is a country-level guess and already absurd;
#: the bound is there to refuse garbage, not to judge a fix.
AccuracyM = Annotated[Decimal, Field(ge=0, le=100_000)]
#: What the client sends as its device id: an opaque, already-hashed token. Bounded and restricted
#: to a token alphabet so it is neither a place to smuggle text nor an unbounded value to seal.
DeviceIdHash = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{16,128}$")]
FlagReasonName = Literal[
    "mock_location", "impossible_travel", "shared_device", "ip_outside_allowed"
]
PunchKind = Literal["check_in", "check_out"]

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

    **Unknown fields are refused**, not ignored: a client that misspells a signal (`device_id`
    for `device_id_hash`) would otherwise quietly stop reporting it and nobody would know.

    The last three fields are optional **client-reported signals** (R22). They are claims, not
    facts: a client that lies about them is exactly what the rules exist to notice, and none of them
    can make a punch fail. They are only stored when the organization has switched a rule on.
    """

    model_config = ConfigDict(extra="forbid")

    work_mode: WorkMode = "office"
    source: Source = "web"
    location_code: LocationCode | None = None
    shift_id: uuid.UUID | None = None
    lat: Latitude | None = None
    lng: Longitude | None = None
    notes: str | None = Field(default=None, max_length=500)
    #: An opaque hash of a device identifier, computed by the client. Sealed at rest.
    device_id_hash: DeviceIdHash | None = None
    #: The accuracy radius the platform's location API reported for `lat`/`lng`, in metres.
    location_accuracy_m: AccuracyM | None = None
    #: True when the client's location API says the fix came from a mock provider.
    mock_location: bool | None = None


class CheckOutIn(BaseModel):
    """Close today's open attendance for the calling employee. Unknown fields are refused; the
    signal fields are the same optional client claims `CheckInIn` documents."""

    model_config = ConfigDict(extra="forbid")

    lat: Latitude | None = None
    lng: Longitude | None = None
    device_id_hash: DeviceIdHash | None = None
    location_accuracy_m: AccuracyM | None = None
    mock_location: bool | None = None


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


# --------------------------------------------------------------------- anti-spoofing (R22)


class SignalSettingsIn(BaseModel):
    """An organization's anti-spoofing rules — a full replacement, so the same body always
    produces the same state (`PUT` is idempotent). Every rule defaults off.

    Unknown fields are refused. There is deliberately **no** field that makes a flag block a
    punch: the rules are observe-only, and a blocking rule is a later decision (see
    `models.SignalSettings`).
    """

    model_config = ConfigDict(extra="forbid")

    detect_mock_location: bool = False
    detect_impossible_travel: bool = False
    detect_shared_device: bool = False
    detect_ip_outside_allowed: bool = Field(
        default=False,
        description=(
            "Flag a punch that came from outside every range in `allowed_cidrs`. "
            "**The office-network rule applies only to punches declared as office.** The work mode "
            "is the client's own claim, so a client that declares `remote` (or `field`, `hybrid`) "
            "is not judged by this rule; it catches an office punch from the wrong network, not a "
            "client that misstates where it is. Needs at least one range."
        ),
    )
    max_travel_speed_kmh: int = Field(default=900, ge=10, le=3000)
    shared_device_window_hours: int = Field(default=24, ge=1, le=168)
    #: Ranges an *office* punch may come from, e.g. `203.0.113.0/24`. Canonicalised and
    #: de-duplicated on the way in; a range that covers every address is refused.
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_cidrs")
    @classmethod
    def _canonical_ranges(cls, value: list[str]) -> list[str]:
        try:
            return list(dict.fromkeys(normalise_cidr(v) for v in value))
        except ValueError as exc:
            raise ValueError(f"Not a usable IP range: {exc}") from exc

    @model_validator(mode="after")
    def _ip_rule_needs_a_range(self) -> SignalSettingsIn:
        if self.detect_ip_outside_allowed and not self.allowed_cidrs:
            raise ValueError("Turning on the IP-range rule needs at least one allowed range")
        return self


class SignalSettingsOut(SignalSettingsIn):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


class SignalSummaryOut(BaseModel):
    """What a flag's reviewer sees of the punch's signals: **masked**. The IP address and the device
    id are personal data; the values are behind the unmask route, which needs its own permission
    and audits every read."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: PunchKind
    punched_at: datetime
    accuracy_m: Decimal | None
    mock_location: bool | None
    user_agent_class: str | None
    ip_masked: str | None
    device_id_masked: str | None


class FlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attendance_id: uuid.UUID
    employee_id: uuid.UUID
    #: The day the flagged punch belongs to.
    work_date: date | None = None
    reason_code: FlagReasonName
    #: Counts and measurements only — never an address, a device id or another employee's id.
    detail: dict[str, Any]
    created_at: datetime
    signal: SignalSummaryOut | None = None


SignalField = Literal["ip", "device_id"]


class SignalUnmaskIn(BaseModel):
    """Which sealed values to reveal. Naming them keeps an unmask a deliberate, minimal act — and
    the audit row records exactly this list."""

    model_config = ConfigDict(extra="forbid")

    fields: list[SignalField] = Field(min_length=1, max_length=2)

    @field_validator("fields")
    @classmethod
    def _no_duplicates(cls, value: list[SignalField]) -> list[SignalField]:
        return list(dict.fromkeys(value))


class SignalUnmasked(BaseModel):
    """The **only** response that carries these two values in the clear. Each is null when nothing
    was recorded or it was not asked for."""

    ip: str | None = None
    device_id: str | None = None
