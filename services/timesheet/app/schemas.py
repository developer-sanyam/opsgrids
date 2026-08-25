"""Request and response contracts for the timesheet service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the PulseBoard SPA wires its client against it. The row/cell nesting mirrors
`frontend/product-pulseboard/src/api/types.ts` (`TimesheetRow` / `TimesheetCell`) exactly:
one row per person per week, seven daily cells, hours as decimal numbers on the wire (stored
as integer minutes — the wire value is exact for the grid's half-hours).

`person_id` on the wire is the person's stable `ref` (`p01`) — the id the SPA's roster is
keyed by, the same value the project service's `/projects/people` returns as `Person.id`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

TimesheetStatus = Literal["draft", "submitted", "approved"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ the grid


class TimesheetCellOut(BaseModel):
    """One day inside a person's week. Mirrors the SPA's `TimesheetCell`;
    `billable_hours <= entered_hours` always (a database CHECK holds the invariant)."""

    date: date
    entered_hours: float
    billable_hours: float


class TimesheetRowOut(BaseModel):
    """A person's timesheet for one week: seven daily cells and an approval state. Mirrors the
    SPA's `TimesheetRow`; `person_id` is the person's stable `ref` (`p01`)."""

    person_id: str
    week_start: date
    status: TimesheetStatus
    cells: list[TimesheetCellOut]


class TimesheetEntryIn(BaseModel):
    """One slot of work inside a submitted week: a date, an optional engagement code, and
    decimal hours (stored as minutes)."""

    date: date
    project_ref: str | None = Field(default=None, max_length=40)
    entered_hours: float = Field(default=0, ge=0, le=24)
    billable_hours: float = Field(default=0, ge=0, le=24)


class TimesheetWeekIn(BaseModel):
    """Record (or re-record) one person's week. `person_ref` names the roster person; the
    week's status moves as a unit. Used by an admin recording on someone's behalf — a person
    recording their own week relies on OWN scope matching their `person_id`."""

    person_ref: str = Field(min_length=1, max_length=40)
    person_id: str = Field(min_length=1, description="The roster person's UUID")
    week_start: date
    status: TimesheetStatus = "draft"
    capacity_hours: float = Field(default=40, ge=0, le=168)
    entries: list[TimesheetEntryIn] = Field(default_factory=list)


# ------------------------------------------------------------------ summary rollups


class TrendPoint(BaseModel):
    """A point on the utilization-over-time chart. Mirrors the SPA's `TrendPoint`."""

    label: str
    value: float


class TimesheetSummaryOut(BaseModel):
    """The Overview's time-side rollup for one week — the same arithmetic the SPA's
    `derive.ts` runs over the timesheet rows, exposed once. The engagement-side numbers
    (active projects, health) live on the project service's `/projects/summary`.

    `utilization_pct` is billable minutes over snapshotted capacity, one decimal.
    `series` is utilization by week: representative history with the queried week's live
    figure as its final point (marked representative until real history accumulates).
    """

    week_start: date
    entered_hours: float
    billable_hours: float
    capacity_hours: float
    utilization_pct: float
    series: list[TrendPoint]
