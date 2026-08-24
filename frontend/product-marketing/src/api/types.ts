/**
 * Wire types for the Marketing product.
 *
 * These mirror the shapes the marketing service will expose (`services/marketing/app/schemas.py`
 * on a later branch) so a generated client drops in without the UI changing. Money is always an
 * integer amount of **minor units** (paise for INR) plus an ISO 4217 currency — never a float,
 * per the platform money rule. Ratios (ROAS) and per-lead costs are derived client-side in
 * `@/api/derive`, never stored, so a rounding choice lives in exactly one place.
 */

export type Channel = "email" | "social" | "search" | "events";
export type CampaignStatus = "draft" | "live" | "paused" | "ended";

/** A marketing campaign. Budget / spend / revenue / pipeline are minor units of `currency`. */
export interface Campaign {
  id: string;
  name: string;
  channel: Channel;
  status: CampaignStatus;
  /** ISO 4217, e.g. "INR". */
  currency: string;
  /** Approved budget, minor units. */
  budget_minor: number;
  /** Spend to date, minor units. */
  spent_minor: number;
  /** Closed-won revenue attributed to the campaign, minor units. */
  revenue_minor: number;
  /** Open pipeline (opportunity value) generated, minor units. */
  pipeline_minor: number;
  /** Qualified leads captured. */
  leads: number;
  /** Deals attributed (won). Drives per-channel CAC. */
  conversions: number;
  /** The primary objective, e.g. "Enterprise pipeline". */
  goal: string;
  /** The audience this campaign targets — resolves to an `Audience` in the same payload. */
  audience_id: string;
  /** Flight window (ISO date). `end_date` is null while a campaign is open-ended / draft. */
  start_date: string;
  end_date: string | null;
  /** Owner, for the campaign header. */
  owner: string;
}

/** A saved audience segment campaigns can target. */
export interface Audience {
  id: string;
  name: string;
  /** Reachable people in the segment. */
  size: number;
  /** A one-line human summary of the segment's criteria. */
  criteria: string;
  created_at: string;
}

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* --------------------------- overview rollups --------------------------- */

/** A point on the revenue-vs-spend time series (minor units). */
export interface RevenueSpendPoint {
  label: string;
  revenue_minor: number;
  spend_minor: number;
}

/** One channel's rolled-up performance, derived live from the campaign list. */
export interface ChannelPerformance {
  channel: Channel;
  campaigns: number;
  spend_minor: number;
  revenue_minor: number;
  pipeline_minor: number;
  leads: number;
  conversions: number;
  /** revenue / spend, as a plain ratio (e.g. 4.2). 0 when there is no spend. */
  roas: number;
  /** spend / conversions, minor units. Null when nothing converted. */
  cac_minor: number | null;
  /** spend / leads, minor units. Null when there are no leads. */
  cpl_minor: number | null;
}
