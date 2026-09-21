"""Request and response contracts for the itsm service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the ITSM SPA generates its client from it. The field names, nesting and enums mirror
`frontend/product-itsm/src/api/types.ts` exactly, so wiring the SPA from fixtures to live is a
flag flip rather than a rewrite.

The human identifiers the SPA speaks in are the API's identity: an incident's `id` is its
`INC-4821` number, a request's `id` is its `REQ-2051` number, a catalog item's `id` is its
`CAT-01` code, and an asset is keyed by its `AST-1001` tag. Money (`cost_minor`) is integer
minor units in `cost_currency` (ISO-4217), never a float.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ------------------------------------------------------------------ vocabularies (match the SPA)

Priority = Literal["P1", "P2", "P3", "P4"]
IncidentStatus = Literal["new", "in_progress", "on_hold", "resolved", "closed"]
SlaState = Literal["met", "on_track", "at_risk", "breached"]
IncidentCategory = Literal["network", "hardware", "software", "access", "email", "security"]
WorklogKind = Literal["note", "status", "assignment", "sla", "resolution"]
CatalogCategory = Literal["onboarding", "offboarding", "access", "hardware", "software"]
RequestStatus = Literal["approval", "in_progress", "fulfilled", "rejected"]
AssetType = Literal["laptop", "desktop", "server", "monitor", "phone", "network", "license"]
AssetStatus = Literal["in_use", "in_stock", "in_repair", "retired"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ incidents


class WorklogEntryOut(BaseModel):
    """One entry in an incident's activity thread. Mirrors the SPA's `WorklogEntry`."""

    id: str
    author: str
    body: str
    at: datetime
    kind: WorklogKind


class IncidentOut(BaseModel):
    """Mirrors the SPA's `Incident`. `id` is the human number; `affected_asset_tag` points at
    a CMDB asset by its tag; `worklog` is the ordered activity thread (empty on the list view)."""

    id: str
    summary: str
    description: str
    priority: Priority
    status: IncidentStatus
    category: IncidentCategory
    assignee: str
    requester: str
    affected_asset_tag: str | None
    sla_state: SlaState
    sla_minutes_remaining: int | None
    created_at: datetime
    updated_at: datetime
    worklog: list[WorklogEntryOut] = Field(default_factory=list)


class IncidentIn(BaseModel):
    """Raise an incident. `status` is not accepted — a new incident always starts `new`."""

    summary: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    priority: Priority = "P3"
    category: IncidentCategory = "software"
    requester: str = Field(min_length=1, max_length=120)
    assignee: str = Field(default="", max_length=120)
    affected_asset_tag: str | None = Field(default=None, max_length=40)


class WorklogIn(BaseModel):
    """Append one manual note to an incident's thread. There is no update or delete — by design.

    `author` stays client-supplied: `platform_core.auth.Principal` carries no name claim (no
    `Agent`/directory row is linked to a calling membership in this slice either), so the server
    has nothing else to derive a display name from — the same reasoning `support.ReplyIn` uses
    for `author_name`. What *is* derived server-side, and never trusted from the payload, is
    `kind`: the route that accepts this schema forces it to `"note"` regardless of what is sent,
    because `status` / `assignment` / `sla` / `resolution` entries are system-written by the
    lifecycle endpoints only — a client must never be able to make the thread claim a change that
    did not happen. The true identity of who posted stays out of band, in `created_by` /
    `AuditLog.actor_id`, which this endpoint already sets from the caller."""

    author: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=8000)
    kind: WorklogKind = "note"


class IncidentUpdate(BaseModel):
    """Edit an incident's mutable properties. Absent means "leave alone"; `status` stays on its
    own endpoint (`/status`) with its own transition rules, audit action and event.

    `priority` and `assignee` require ORG scope in `services.update_incident` — an OWN-scoped
    caller may edit their own ticket's summary, description, category and affected asset, but
    must not reprioritise or reassign it (an ITSM_USER resolving or escalating their own ticket
    is exactly the privilege the audit flags). `affected_asset_tag: null` clears the link."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=8000)
    category: IncidentCategory | None = None
    priority: Priority | None = None
    assignee: str | None = Field(default=None, max_length=120)
    affected_asset_tag: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class IncidentStatusIn(BaseModel):
    """Move an incident through its lifecycle (`app.domain.INCIDENT_TRANSITIONS`). Anything not a
    legal next state is a 409, not a free-form set. `note` becomes the resolution worklog entry
    when resolving, so it is required there — a "resolved" with no resolution text is not
    useful — and optional (but still recorded) on every other move."""

    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus
    note: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def _note_required_to_resolve(self) -> Self:
        if self.status == "resolved" and not (self.note and self.note.strip()):
            raise ValueError("note is required when resolving an incident")
        return self


# ------------------------------------------------------------------ service catalog & requests


class CatalogItemOut(BaseModel):
    """Mirrors the SPA's `CatalogItem`. `id` is the human `CAT-01` code."""

    id: str
    name: str
    category: CatalogCategory
    description: str
    sla_days: int
    requests_30d: int


class CatalogItemIn(BaseModel):
    """Add a service-catalog offering. `id` is the human `CAT-..` code, unique within the
    organization; requests key on it by string, not a foreign key, so it is immutable once set."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    category: CatalogCategory
    description: str = Field(default="", max_length=2000)
    sla_days: int = Field(default=1, ge=1)


class CatalogItemUpdate(BaseModel):
    """Edit a catalog item. `id` (the code) is not here — it is the API identity and the
    denormalised join key `requests.catalog_item_code` reads through, so it cannot move."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: CatalogCategory | None = None
    description: str | None = Field(default=None, max_length=2000)
    sla_days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class ServiceRequestOut(BaseModel):
    """Mirrors the SPA's `ServiceRequest`. `id` is the human number; `catalog_item_id` is the
    catalog item's code, with its name denormalised so a table row renders without a lookup.
    `decision_note` is set once, by `/decision`, and is most often populated on a rejection."""

    id: str
    catalog_item_id: str
    catalog_item_name: str
    category: CatalogCategory
    requester: str
    subject: str
    status: RequestStatus
    assignee: str | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


class ServiceRequestIn(BaseModel):
    """Raise a service request against a catalog item — the entry point for the JML flows.
    `catalog_item_id` is the `CAT-..` code; an unknown one is a 404, not a 422, because it is a
    reference to another row rather than a shape the client got wrong. `status` is not accepted:
    every new request starts `approval`, and `requester_id` is the caller, not a client field."""

    model_config = ConfigDict(extra="forbid")

    catalog_item_id: str = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=1, max_length=200)
    requester: str = Field(min_length=1, max_length=120)


class RequestDecisionIn(BaseModel):
    """Approve or reject a request awaiting approval. `note` becomes `decision_note`; required on
    a rejection (a rejection with no visible reason is not usable), optional on an approval."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _note_required_to_reject(self) -> Self:
        if self.decision == "reject" and not (self.note and self.note.strip()):
            raise ValueError("note is required when rejecting a request")
        return self


class RequestPatch(BaseModel):
    """The one field an agent may set directly on a request outside the decision/fulfil
    endpoints. `assignee: null` unassigns it."""

    model_config = ConfigDict(extra="forbid")

    assignee: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


# ------------------------------------------------------------------ assets


class AssetOut(BaseModel):
    """Mirrors the SPA's `Asset`. Keyed by `tag`; cost is minor units in `cost_currency`.
    `open_incident_ids` is derived from the incidents currently open against this asset."""

    tag: str
    name: str
    type: AssetType
    status: AssetStatus
    owner: str | None
    location: str
    serial: str
    purchased_at: date | None
    warranty_expires_at: date | None
    cost_minor: int
    cost_currency: str
    open_incident_ids: list[str] = Field(default_factory=list)


class AssetIn(BaseModel):
    """Create a CMDB asset."""

    tag: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    type: AssetType
    status: AssetStatus = "in_stock"
    owner: str | None = Field(default=None, max_length=120)
    location: str = Field(default="", max_length=160)
    serial: str = Field(default="", max_length=120)
    purchased_at: date | None = None
    warranty_expires_at: date | None = None
    cost_minor: int = Field(default=0, ge=0)
    cost_currency: str = Field(default="INR", min_length=3, max_length=3)


class AssetUpdate(BaseModel):
    """Edit a CMDB asset. `tag` cannot appear here — it is the API identity and the join key
    incidents key on by `affected_asset_tag`, so it is immutable (`Must not be built`). A status
    change, including retiring an asset, rides this same PATCH rather than a separate endpoint."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: AssetType | None = None
    status: AssetStatus | None = None
    owner: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=160)
    serial: str | None = Field(default=None, max_length=120)
    purchased_at: date | None = None
    warranty_expires_at: date | None = None
    cost_minor: int | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


# ------------------------------------------------------------------ overview rollups


class Breakdown(BaseModel):
    """One bar in a by-priority or by-category breakdown of open incidents."""

    key: str
    label: str
    count: int
    #: Share of the open total, 0-100.
    pct: int


class TrendPoint(BaseModel):
    """A point on the incident-volume-over-time chart."""

    label: str
    opened: int
    resolved: int


class OverviewOut(BaseModel):
    """The Overview dashboard's server-side rollup. The SPA can derive these from the list
    endpoints; this is the same arithmetic exposed once, so a reporting view can share it."""

    open_incidents: int
    #: Share of incidents whose SLA clock has not been breached, 0-100 (one decimal).
    sla_compliance_pct: float
    requests_awaiting_approval: int
    assets_under_management: int
    volume: list[TrendPoint]
    by_priority: list[Breakdown]
    by_category: list[Breakdown]
