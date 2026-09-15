/**
 * Wire types for the ITSM product — the service desk for OpsGrids IT.
 *
 * These mirror the shape the incident / request / asset services will return
 * (`services/<name>/app/schemas.py`) so a generated client can drop in later without the UI changing.
 * The backend for this product is still a skeleton, so everything renders from `fixtures.ts`
 * today; the field names and enums are the forward-looking contract.
 *
 * Money (asset cost) is integer minor units with an ISO currency, per the platform rules — never
 * a float.
 */

/* ------------------------------- incidents ------------------------------- */

/** ITIL-style urgency band. P1 is a business-critical outage; P4 is a minor request-for-help. */
export type Priority = "P1" | "P2" | "P3" | "P4";

/** Where an incident sits in its lifecycle. */
export type IncidentStatus =
  | "new"
  | "in_progress"
  | "on_hold"
  | "resolved"
  | "closed";

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

/** One entry in an incident's activity / worklog thread. */
export interface WorklogEntry {
  id: string;
  author: string;
  /** A human sentence describing the event ("Assigned to…", "Added a note", "Resolved"). */
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
  /** Minutes remaining on the SLA clock; negative once breached; null once met/closed. */
  sla_minutes_remaining: number | null;
  created_at: string;
  updated_at: string;
  worklog: WorklogEntry[];
}

/* --------------------------- service requests --------------------------- */

/** Catalog grouping — the joiners-movers-leavers ("JML") flows plus everyday fulfilment. */
export type CatalogCategory =
  | "onboarding"
  | "offboarding"
  | "access"
  | "hardware"
  | "software";

/** One offering in the service catalog. */
export interface CatalogItem {
  id: string;
  name: string;
  category: CatalogCategory;
  description: string;
  /** Target fulfilment time in working days. */
  sla_days: number;
  /** How many of this item have been requested in the trailing 30 days (sample rollup). */
  requests_30d: number;
}

/** Where a service request sits: awaiting approval, being fulfilled, or done. */
export type RequestStatus = "approval" | "in_progress" | "fulfilled";

/** Mirrors `RequestOut`. `catalog_item_id` points at a `CatalogItem`. */
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
  created_at: string;
  updated_at: string;
}

/* -------------------------------- assets -------------------------------- */

/** Configuration-item class in the CMDB. */
export type AssetType =
  | "laptop"
  | "desktop"
  | "server"
  | "monitor"
  | "phone"
  | "network"
  | "license";

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
  /** Incident ids currently open against this CI (for the drill-down). */
  open_incident_ids: string[];
}

/* ------------------------------- envelopes ------------------------------- */

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* --------------------------- overview rollups --------------------------- */

/** One bar in the "by priority" or "by category" breakdown — grouped from the incident list. */
export interface Breakdown {
  key: string;
  label: string;
  count: number;
  /** Share of the resolved total, 0–100. */
  pct: number;
}

/** A point on the incident-volume-over-time chart. */
export interface TrendPoint {
  label: string;
  /** Incidents opened in the period. */
  opened: number;
  /** Incidents resolved in the period. */
  resolved: number;
}

export type TrendRange = "weekly" | "monthly" | "quarterly";
