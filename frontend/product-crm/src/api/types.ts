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

/** Where a lead was captured. */
export type LeadSource = "form" | "import" | "manual";

/** A lead's place in the intake lifecycle. `converted` is reachable only through
 *  `POST /leads/{id}:convert`, never through a plain status edit. */
export type LeadStatus = "new" | "working" | "qualified" | "disqualified" | "converted";

/** The two sealed values a lead carries, and so the two the reveal endpoint can be asked for. */
export type LeadRevealField = "phone" | "email";

/** A sales rep who owns companies, contacts, deals and leads. Resolved client-side from
 *  `owner_id`. `is_active` false once a rep leaves — kept, never deleted, so every row that
 *  already carries their id still resolves to a name. */
export interface Owner {
  id: string;
  full_name: string;
  email: string;
  role: string;
  is_active: boolean;
}

/**
 * Mirrors `LeadOut`. `email` / `phone` are always the masked display form — the service seals
 * and blind-indexes the raw values on the way in and never returns them raw again.
 * `converted_*` are set together, once, by `:convert`; a lead's `status` is `"converted"` iff
 * they are set.
 */
export interface Lead {
  id: string;
  full_name: string;
  company: string | null;
  email: string | null;
  phone: string | null;
  source: LeadSource;
  status: LeadStatus;
  /** The statuses a plain status change may move this lead to -- the server's own state machine
   *  (`LeadOut.next_statuses`). The status control offers exactly these. Never `converted`. */
  next_statuses: LeadStatus[];
  owner_id: string | null;
  notes: string;
  converted_company_id: string | null;
  converted_contact_id: string | null;
  converted_deal_id: string | null;
  converted_at: string | null;
  created_at: string;
}

/** What the rep may set on the deal a conversion opens. Every field is optional: with none, the
 *  deal opens at zero in the pipeline's entry stage. `deal_value_minor` is integer minor units. */
export interface LeadConvertOptions {
  deal_value_minor?: number;
  deal_stage?: string;
}

/** The result of converting a lead — the company, contact (if any) and deal it became. */
export interface LeadConvertResult {
  lead_id: string;
  company_id: string;
  contact_id: string | null;
  deal_id: string;
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
  /** `null` for a phone-only contact (ADR-0038 D-14). */
  email: string | null;
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

/** The envelope every list endpoint returns, straight from `platform_core.pagination.Page`. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
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

/**
 * Mirrors `SummaryOut` — `GET /deals/summary`, the server's own rollup over the caller's scope.
 * The headline figures are the server's real totals, computed live from the deals and activities
 * in scope — the Overview reads these instead of counting a page of `getAllDeals()`, which is
 * capped and wrong once an org's book passes 200 rows.
 */
export interface Summary {
  pipeline_value_minor: number;
  weighted_pipeline_minor: number;
  open_deals: number;
  win_rate_pct: number;
  won_value_minor: number;
  /** Touches logged in the trailing seven days. */
  activities_this_week: number;
  currency: string;
  /** `null` until a dated pipeline-value history exists — no chart may treat this as a series. */
  trend: TrendPoint[] | null;
  funnel: StageRollup[];
}
