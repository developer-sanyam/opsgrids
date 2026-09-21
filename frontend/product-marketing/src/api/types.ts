/**
 * Wire types for the Marketing product, mirroring `services/marketing/app/schemas.py` exactly —
 * see that module for the constraints (lengths, `ge=0`, the campaign status lifecycle). Money is
 * always an integer amount of **minor units** (paise for INR) plus an ISO 4217 currency — never a
 * float, per the platform money rule. Ratios (ROAS) and per-lead costs are derived, computed in
 * `@/data/derive` client-side and in `app.domain` server-side, never stored.
 */

export type Channel = "email" | "social" | "search" | "events";
export type CampaignStatus = "draft" | "live" | "paused" | "ended";

/** A marketing campaign. Budget / spend / revenue / pipeline are minor units of `currency`. */
export interface Campaign {
  id: string;
  name: string;
  channel: Channel;
  status: CampaignStatus;
  /** ISO 4217, e.g. "INR". Immutable after creation — re-denominating stored amounts silently
   *  would misstate every figure already recorded against the campaign. */
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
  /** Owner, for the campaign header. Free-text display name, not a user id. */
  owner: string;
}

/**
 * `POST /marketing/campaigns` — `audience_id` is required: the API refuses a campaign with no
 * audience, so an organization with none must create one first. `currency` defaults to `"INR"`
 * server-side when omitted; every other money/count field defaults to `0`.
 */
export interface CampaignIn {
  name: string;
  channel: Channel;
  status?: CampaignStatus;
  currency?: string;
  budget_minor?: number;
  spent_minor?: number;
  revenue_minor?: number;
  pipeline_minor?: number;
  leads?: number;
  conversions?: number;
  goal?: string;
  audience_id: string;
  start_date: string;
  end_date?: string | null;
  owner?: string;
}

/**
 * `PATCH /marketing/campaigns/{id}` — every field optional, only what is sent is applied.
 * `currency` is deliberately absent: re-denominating stored minor-unit amounts is not a thing a
 * patch does. `status` moves only through the domain's lifecycle (`draft → live`,
 * `live ⇄ paused`, `live | paused → ended`); an illegal move is a 409.
 */
export interface CampaignPatch {
  name?: string;
  channel?: Channel;
  status?: CampaignStatus;
  budget_minor?: number;
  spent_minor?: number;
  revenue_minor?: number;
  pipeline_minor?: number;
  leads?: number;
  conversions?: number;
  goal?: string;
  audience_id?: string;
  start_date?: string;
  end_date?: string | null;
  owner?: string;
}

/** A saved audience segment campaigns can target. */
export interface Audience {
  id: string;
  name: string;
  /** Reachable people in the segment. */
  size: number;
  /** A one-line human summary of the segment's criteria. */
  criteria: string;
  /** The segment's *definition* date — a plain calendar date, not the row's write timestamp. */
  created_at: string;
}

/** `POST /marketing/audiences` — `name` is unique per organization; `created_at` is required. */
export interface AudienceIn {
  name: string;
  size: number;
  criteria?: string;
  created_at: string;
}

/** `PATCH /marketing/audiences/{id}` — every field optional. Renaming re-checks uniqueness. */
export interface AudiencePatch {
  name?: string;
  size?: number;
  criteria?: string;
  created_at?: string;
}

/** The server's own `{items,total,page,size,pages}` envelope — never a client-side re-paging. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* --------------------------- overview rollups --------------------------- */

/** A point on a revenue-vs-spend time series (minor units). */
export interface RevenueSpendPoint {
  label: string;
  revenue_minor: number;
  spend_minor: number;
}

/** One channel's rolled-up performance, from the server-side rollup (`GET /marketing/channels`). */
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

/**
 * `GET /marketing/overview` — the Overview screen in one payload, computed server-side from the
 * same campaign set and monthly series the list endpoints expose, so this number and the number a
 * screen would derive from the lists can never disagree.
 */
export interface OverviewSummary {
  /** The org's one campaign currency; `"INR"` when the org has no campaigns yet. */
  currency: string;
  revenue_minor: number;
  spend_minor: number;
  pipeline_minor: number;
  leads: number;
  conversions: number;
  roas: number;
  cpl_minor: number | null;
  revenue_vs_spend: RevenueSpendPoint[];
  channels: ChannelPerformance[];
}
