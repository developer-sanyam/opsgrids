/**
 * Wire types for the CRM ("OpsGrids Revenue") product.
 *
 * These mirror the planned `services/crm/app/schemas.py` field-for-field, so a generated client
 * can drop in later without the UI changing. The CRM service is a skeleton on this branch, so the
 * app runs entirely from `fixtures.ts` (see `client.ts`); the shapes below are what the endpoints
 * will return. Money is always an integer count of **minor units** (paise for INR) plus an ISO
 * currency — never a float.
 */

/** The pipeline stages, in board order. Closed-won and closed-lost are the two terminal columns. */
export type DealStage =
  | "lead"
  | "qualified"
  | "proposal"
  | "negotiation"
  | "closed_won"
  | "closed_lost";

/** A company's relationship to OpsGrids. */
export type CompanyStatus = "customer" | "prospect" | "lead" | "churned";

/** Where a contact sits in the funnel. */
export type ContactStatus = "customer" | "qualified" | "lead" | "dormant";

/** The kind of touch logged on the timeline. */
export type ActivityType = "call" | "email" | "meeting" | "note" | "task";

/** Inbound vs outbound, for calls and emails. `null` for notes, tasks and meetings. */
export type ActivityDirection = "inbound" | "outbound";

/** A sales rep who owns companies, contacts and deals. Resolved client-side from `owner_id`. */
export interface Owner {
  id: string;
  full_name: string;
  email: string;
  role: string;
}

/** Mirrors `CompanyOut`. `annual_revenue_minor` is minor units of `currency`. */
export interface Company {
  id: string;
  name: string;
  domain: string;
  industry: string;
  /** Headcount band, e.g. "201–500". A display string, not a number. */
  size_band: string;
  city: string;
  country: string;
  status: CompanyStatus;
  owner_id: string;
  annual_revenue_minor: number;
  currency: string;
  created_at: string;
}

/** Mirrors `ContactOut`. Email and phone are the masked read shape the service returns by default. */
export interface Contact {
  id: string;
  full_name: string;
  title: string;
  email: string;
  phone: string;
  company_id: string;
  status: ContactStatus;
  owner_id: string;
  /** ISO timestamp of the most recent activity, or `null` if never contacted. */
  last_activity_at: string | null;
  created_at: string;
}

/** Mirrors `DealOut`. `value_minor` is minor units of `currency`; `probability` is 0–100. */
export interface Deal {
  id: string;
  name: string;
  company_id: string;
  primary_contact_id: string | null;
  value_minor: number;
  currency: string;
  stage: DealStage;
  owner_id: string;
  probability: number;
  /** Expected close (ISO date). Past dates on open deals are overdue. */
  close_date: string;
  created_at: string;
  last_activity_at: string | null;
  source: string;
}

/** Mirrors `ActivityOut`. Exactly one of the target ids is usually set; owner always is. */
export interface Activity {
  id: string;
  type: ActivityType;
  subject: string;
  body: string | null;
  deal_id: string | null;
  company_id: string | null;
  contact_id: string | null;
  owner_id: string;
  occurred_at: string;
  direction: ActivityDirection | null;
}

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* ---------------------------- dashboard rollups ---------------------------- */

/** One column of the pipeline funnel — count and summed open value per stage. */
export interface StageRollup {
  stage: DealStage;
  label: string;
  count: number;
  value_minor: number;
  /** Share of the largest stage's value, 0–100 (for the funnel bar width). */
  pct: number;
}

/** A point on the pipeline-value-over-time chart. */
export interface TrendPoint {
  label: string;
  /** Open pipeline value at this point, in minor units. */
  value_minor: number;
}

/** A deal flagged for the Overview "needs attention" list, with the reason it surfaced. */
export interface AttentionItem {
  deal: Deal;
  reason: "stale" | "closing_soon" | "overdue";
  /** A short human explanation, e.g. "No activity in 21 days". */
  detail: string;
}
