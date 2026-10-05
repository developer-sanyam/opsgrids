"""Wire contracts for storage put-away and find-stock (the warehouse map / put-away slice).

Kept beside the code that serves them, like `tracking_schemas` — published through the same OpenAPI
document. Two rules shape every model here:

* **A worker's coordinates appear in exactly two places:** `PutawayIn` (the inbound body) and
  `PutawayLocationOut` (the audited reveal's response). No other model in this module carries a
  latitude or a longitude, and a test scans for it.
* **Every integer is an integer.** `lat_micro` / `lng_micro` / `accuracy_m` are `StrictInt`
  (micro-degrees, metres): a JSON float is a 422, never rounded. Quantities are decimal strings
  (`Qty`), as everywhere in Phase 1.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.schemas import LocationKind, Qty, StockStatus

# ------------------------------------------------------------------ put-away


class PutawayIn(BaseModel):
    """`POST /inventory/putaway` — store stock from a receiving or staging bin into the bin whose
    barcode the worker scanned.

    `to_barcode` takes the shape a location's barcode is stored in (printable ASCII, no space, no
    `/`, 1-64 characters): a scanner's trailing CR/LF is a 422 here — the apps trim it. The server
    resolves it to a bin (a worker holds no `location.read`). `suggested_location_id` is only what
    the device *showed*; whether the scan matched the server's own rule is computed server-side.
    An id that is unknown, deleted or another tenant's is ignored, not refused (warning
    `suggestion_ignored`), so a put-away queued offline is never parked over a stale suggestion.

    The GPS fix is audit evidence, never a location of record, and it never blocks the move: a fix
    the server cannot use (unpaired, out of range, negative accuracy) is dropped with a warning in
    the response, and the stock still moves. `captured_at` is the device's claim of when the fix
    was taken; it is replaced by the receipt time when absurd. None of the geo is part of the
    idempotency hash — a retry that differs only in geo replays the original."""

    model_config = ConfigDict(extra="forbid")

    inventory_item_id: UUID
    from_location_id: UUID
    to_barcode: str = Field(min_length=1, max_length=64, pattern=r"^[!-.0-~]+$")
    quantity: Qty
    lot_id: UUID | None = None
    roll_id: UUID | None = None
    suggested_location_id: UUID | None = None
    captured_at: datetime | None = None
    lat_micro: StrictInt | None = None
    lng_micro: StrictInt | None = None
    accuracy_m: StrictInt | None = None
    occurred_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=500)

    @field_validator("occurred_on")
    @classmethod
    def _not_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("occurred_on cannot be in the future")
        return value


class PutawayFromOut(BaseModel):
    id: str
    code: str
    name: str


class PutawayToOut(BaseModel):
    id: str
    code: str
    name: str
    kind: LocationKind


class PutawayOut(BaseModel):
    """The put-away's receipt: the move, the two bins, whether the scan matched the server's
    suggestion (`null` when it had none) and whether a fix was kept. **No coordinates.**

    `warnings` says what was adjusted without failing the request: `geo_dropped:<reason>`,
    `captured_at_adjusted`, `suggestion_ignored` (the `suggested_location_id` was not one of the
    caller's bins; it was not stored), `geo_not_recorded` (the capture row could not be written;
    the move stands)."""

    move_id: str
    replayed: bool
    from_location: PutawayFromOut
    to_location: PutawayToOut
    matched_suggestion: bool | None
    geo_recorded: bool
    warnings: list[str]


class PutawayDetailOut(BaseModel):
    """`GET /inventory/putaway/{move_id}` — what is recorded about a put-away. `has_location`
    says a fix exists; the fix itself is only ever returned by the audited reveal."""

    move_id: str
    to_location: PutawayToOut
    worker_id: str | None
    captured_at: datetime
    matched_suggestion: bool | None
    has_location: bool


class PutawayLocationOut(BaseModel):
    """`POST /inventory/putaway/{move_id}/location:reveal` — the one response that carries a
    worker's coordinates. Nulls when the capture holds no fix. Sent `Cache-Control: no-store`;
    every successful call writes an audit row."""

    lat_micro: int | None
    lng_micro: int | None
    accuracy_m: int | None
    captured_at: datetime


class PutawaySuggestionOut(BaseModel):
    """The bin the server would store the item in. `barcode` is that bin's own scannable label
    (`null` when it has none): a worker holds no `location.read`, so this is how the device
    compares a scanned label with the suggestion; the caller already holds `stock.transfer`."""

    location_id: str
    code: str
    barcode: str | None
    name: str
    path: str
    reason: Literal["holds_item", "empty_bin"]


class PutawaySuggestOut(BaseModel):
    """`GET /inventory/putaway/suggest` — `null` when the warehouse has no storable bin to offer."""

    suggestion: PutawaySuggestionOut | None


# ------------------------------------------------------------------ find stock


class LocatePlacementOut(BaseModel):
    """Where a bin sits on a floor's published layout, in integer millimetres. `source` is `own`
    when the bin itself is placed, `ancestor` when it is not and this is the nearest placed
    container above it (a rack standing for its bins)."""

    layout_version_id: str
    x_mm: int
    y_mm: int
    z_mm: int | None
    width_mm: int
    depth_mm: int
    height_mm: int | None
    source: Literal["own", "ancestor"]
    ancestor_location_id: str | None


class LocateRowOut(BaseModel):
    """One bin holding stock of the item, in one status (`quantity` sums its lots and rolls).

    `placement` is `null` with a `placement_reason`: `unplaced` (the warehouse has a published
    layout and this bin, nor any container above it, is on it), `no_published_layout` (the
    warehouse has none), or `no_permission` (the caller may read stock but not layouts, so no
    geometry — and no floor — is returned)."""

    warehouse_id: str
    warehouse_name: str
    floor_id: str | None
    floor_name: str | None
    location_id: str
    code: str
    name: str
    kind: LocationKind
    path: str
    quantity: Qty
    status: StockStatus
    placement: LocatePlacementOut | None
    placement_reason: Literal["unplaced", "no_published_layout", "no_permission"] | None


class LocateOut(BaseModel):
    """`GET /inventory/locate`. An empty `results` is a valid answer. `truncated` is true when
    more bins matched than are returned (the cap is far above a real warehouse's spread of one
    item; it exists to bound the query)."""

    results: list[LocateRowOut]
    truncated: bool = False
