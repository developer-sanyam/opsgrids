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
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

Priority = Literal["P1", "P2", "P3", "P4"]
IncidentStatus = Literal["new", "in_progress", "on_hold", "resolved", "closed"]
SlaState = Literal["met", "on_track", "at_risk", "breached"]
IncidentCategory = Literal["network", "hardware", "software", "access", "email", "security"]
WorklogKind = Literal["note", "status", "assignment", "sla", "resolution"]
CatalogCategory = Literal["onboarding", "offboarding", "access", "hardware", "software"]
RequestStatus = Literal["approval", "in_progress", "fulfilled"]
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
    """Append one entry to an incident's thread. There is no update or delete — by design."""

    author: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=8000)
    kind: WorklogKind = "note"


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
    """Add an offering to the service catalog.

    `requests_30d` is not accepted: the trailing-30-day rollup is counted from the requests
    themselves, so it is the server's to state and never the client's to assert.
    """

    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    category: CatalogCategory
    description: str = Field(default="", max_length=8000)
    #: Target fulfilment time in working days.
    sla_days: int = Field(default=1, ge=1, le=365)


class ServiceRequestOut(BaseModel):
    """Mirrors the SPA's `ServiceRequest`. `id` is the human number; `catalog_item_id` is the
    catalog item's code, with its name denormalised so a table row renders without a lookup."""

    id: str
    catalog_item_id: str
    catalog_item_name: str
    category: CatalogCategory
    requester: str
    subject: str
    status: RequestStatus
    assignee: str | None
    created_at: datetime
    updated_at: datetime


class ServiceRequestIn(BaseModel):
    """Raise a service request against a catalog item.

    `number` is the organization's own `REQ-2051` identity, minted the way an asset carries its
    tag — a clash is refused rather than quietly renumbered. `category` is not accepted: it is
    denormalised from the catalog item the request names. Neither is `status` — a new request
    always starts awaiting approval, so no payload can skip the approval step. The requester's
    directory id comes from the caller, which is what OWN scope later reads back.
    """

    number: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    catalog_item_code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    requester: str = Field(min_length=1, max_length=120)
    #: Who the request is for — often the requester, but not for JML flows.
    subject: str = Field(min_length=1, max_length=200)
    assignee: str | None = Field(default=None, max_length=120)


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
