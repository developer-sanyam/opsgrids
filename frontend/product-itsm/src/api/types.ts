/**
 * Wire types for the ITSM product — the service desk for OpsGrids IT.
 *
 * These mirror `services/itsm/app/schemas.py` field-for-field: the `Out` types are what a route
 * returns, the `In`/`Update`/`Patch` types are exactly what a route accepts and nothing more
 * (every write schema is `extra="forbid"` server-side, so an invented field is a 422, not a
 * silently ignored one).
 *
 * The human identifiers are the API's identity: an incident's `id` is its `INC-4821` number, a
 * request's `id` is its `REQ-2051` number, a catalog item's `id` is its `CAT-01` code, and an
 * asset is keyed by its `AST-1001` tag. Money (`cost_minor`) is integer minor units in
 * `cost_currency` (ISO-4217), never a float.
 */

/* ------------------------------- incidents ------------------------------- */

/** ITIL-style urgency band. P1 is a business-critical outage; P4 is a minor request-for-help. */
export type Priority = "P1" | "P2" | "P3" | "P4";

/** Where an incident sits in its lifecycle. */
export type IncidentStatus = "new" | "in_progress" | "on_hold" | "resolved" | "closed";

/**
 * SLA clock state, always surfaced with a text label (never colour alone):
 * met — resolved inside target; on_track — running, comfortably ahead;
 * at_risk — running, close to target; breached — target already missed.
 */
export type SlaState = "met" | "on_track" | "at_risk" | "breached";

/** The functional area an incident belongs to — drives routing and the breakdown chart. */
export type IncidentCategory =
  | "network"
  | "hardware"
  | "software"
  | "access"
  | "email"
  | "security";

/** One entry in an incident's activity / worklog thread. Append-only — no edit, no delete. */
export interface WorklogEntry {
  id: string;
  author: string;
  body: string;
  at: string;
  kind: "note" | "status" | "assignment" | "sla" | "resolution";
}

/** Mirrors `IncidentOut`. `affected_asset_tag` points at a CMDB asset by its tag. */
export interface Incident {
  id: string;
  summary: string;
  description: string;
  priority: Priority;
  status: IncidentStatus;
  category: IncidentCategory;
  assignee: string;
  requester: string;
  affected_asset_tag: string | null;
  sla_state: SlaState;
  /** Minutes remaining on the SLA clock; negative once breached; null once met/closed. Stamped
   *  once server-side and not recomputed live — treat as approximate, not a running clock. */
  sla_minutes_remaining: number | null;
  created_at: string;
  updated_at: string;
  /** Empty on the list view; populated on the detail read. */
  worklog: WorklogEntry[];
}

/** `POST /itsm/incidents`. `status` is never sent — a new incident always starts `new`. */
export interface IncidentIn {
  summary: string;
  description?: string;
  priority?: Priority;
  category?: IncidentCategory;
  requester: string;
  assignee?: string;
  affected_asset_tag?: string | null;
}

/** `POST /itsm/incidents/{number}/worklog`. `kind` is forced to `"note"` server-side. */
export interface WorklogIn {
  author: string;
  body: string;
}

/**
 * `PATCH /itsm/incidents/{number}`. Every field optional (absent = "leave alone"), `extra=forbid`
 * server-side. `affected_asset_tag: null` clears the link. `priority` and `assignee` need ORG
 * scope; the rest is available at OWN.
 */
export interface IncidentUpdate {
  summary?: string;
  description?: string;
  category?: IncidentCategory;
  priority?: Priority;
  assignee?: string;
  affected_asset_tag?: string | null;
}

/** `POST /itsm/incidents/{number}/status`. `note` is required when `status === "resolved"`. */
export interface IncidentStatusIn {
  status: IncidentStatus;
  note?: string;
}

/* --------------------------- service requests --------------------------- */

/** Catalog grouping — the joiners-movers-leavers ("JML") flows plus everyday fulfilment. */
export type CatalogCategory = "onboarding" | "offboarding" | "access" | "hardware" | "software";

/** One offering in the service catalog. */
export interface CatalogItem {
  id: string;
  name: string;
  category: CatalogCategory;
  description: string;
  /** Target fulfilment time in working days. */
  sla_days: number;
  /** How many of this item have been requested in the trailing 30 days (seeded, not recounted). */
  requests_30d: number;
}

/** `POST /itsm/catalog`. `id` is the human `CAT-..` code, unique per org and immutable. */
export interface CatalogItemIn {
  id: string;
  name: string;
  category: CatalogCategory;
  description?: string;
  sla_days?: number;
}

/** `PATCH /itsm/catalog/{code}`. Everything but the code. */
export interface CatalogItemUpdate {
  name?: string;
  category?: CatalogCategory;
  description?: string;
  sla_days?: number;
}

/** Where a service request sits: awaiting approval, being fulfilled, done, or turned down. */
export type RequestStatus = "approval" | "in_progress" | "fulfilled" | "rejected";

/** Mirrors `ServiceRequestOut`. `catalog_item_id` points at a `CatalogItem` by its code. */
export interface ServiceRequest {
  id: string;
  catalog_item_id: string;
  /** Denormalised for the table so a row renders without a second lookup. */
  catalog_item_name: string;
  category: CatalogCategory;
  requester: string;
  /** Who the request is for — often the same as the requester, but not for JML flows. */
  subject: string;
  status: RequestStatus;
  /** Present while awaiting approval / in progress; who holds the next step. */
  assignee: string | null;
  /** Set once, by `/decision` — the reason behind an approval or (always, on) a rejection. */
  decision_note: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /itsm/service-requests`. `status` and `requester_id` are never sent. */
export interface ServiceRequestIn {
  catalog_item_id: string;
  subject: string;
  requester: string;
}

/** `POST /itsm/service-requests/{number}/decision`. `note` is required to reject. */
export interface RequestDecisionIn {
  decision: "approve" | "reject";
  note?: string;
}

/** `PATCH /itsm/service-requests/{number}`. `assignee: null` unassigns. */
export interface RequestPatch {
  assignee: string | null;
}

/* -------------------------------- assets -------------------------------- */

/** Configuration-item class in the CMDB. */
export type AssetType = "laptop" | "desktop" | "server" | "monitor" | "phone" | "network" | "license";

/** Asset lifecycle state. */
export type AssetStatus = "in_use" | "in_stock" | "in_repair" | "retired";

/** Mirrors `AssetOut`. Cost is minor units in `cost_currency`. */
export interface Asset {
  tag: string;
  name: string;
  type: AssetType;
  status: AssetStatus;
  owner: string | null;
  location: string;
  serial: string;
  /** ISO date, or null when the purchase date was never recorded. */
  purchased_at: string | null;
  /** Warranty / support expiry; drives the "expiring soon" flag. Null when untracked. */
  warranty_expires_at: string | null;
  cost_minor: number;
  cost_currency: string;
  /** Incident numbers currently open against this CI (for the drill-down). */
  open_incident_ids: string[];
}

/** `POST /itsm/assets`. `tag` is the API identity and immutable once created. */
export interface AssetIn {
  tag: string;
  name: string;
  type: AssetType;
  status?: AssetStatus;
  owner?: string | null;
  location?: string;
  serial?: string;
  purchased_at?: string | null;
  warranty_expires_at?: string | null;
  cost_minor?: number;
  cost_currency?: string;
}

/** `PATCH /itsm/assets/{tag}`. Every `AssetIn` field but `tag`, which cannot appear here. A
 *  status change — including retiring an asset — rides this same PATCH. */
export interface AssetUpdate {
  name?: string;
  type?: AssetType;
  status?: AssetStatus;
  owner?: string | null;
  location?: string;
  serial?: string;
  purchased_at?: string | null;
  warranty_expires_at?: string | null;
  cost_minor?: number;
  cost_currency?: string;
}

/* ------------------------------- envelopes ------------------------------- */

/** The `{items, total, page, size, pages}` envelope every list endpoint returns
 *  (`platform_core.pagination.Page`). Paging is server-side; nothing here caps or drops a page. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* --------------------------- overview rollups --------------------------- */

/** One bar in the "by priority" or "by category" breakdown of open incidents. */
export interface Breakdown {
  key: string;
  label: string;
  count: number;
  /** Share of the open total, 0–100. */
  pct: number;
}

/** One weekly bucket on the incident-volume chart. */
export interface TrendPoint {
  label: string;
  /** Incidents opened in the period. */
  opened: number;
  /** Incidents resolved in the period. */
  resolved: number;
}

/** Mirrors `OverviewOut` — `GET /itsm/overview`, computed over every in-scope row, not a page. */
export interface Overview {
  open_incidents: number;
  /** Share of incidents whose SLA clock has not been breached, 0–100 (one decimal). */
  sla_compliance_pct: number;
  requests_awaiting_approval: number;
  assets_under_management: number;
  /** Six trailing weekly buckets. */
  volume: TrendPoint[];
  by_priority: Breakdown[];
  by_category: Breakdown[];
}
