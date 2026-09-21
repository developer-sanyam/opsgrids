/**
 * The gateway's real wire shapes for this product — field-for-field mirrors of
 * `services/customer/app/schemas.py` and `services/deal/app/schemas.py`.
 *
 * Kept separate from `./types.ts` (this app's domain types) so the mapping from the CRM
 * service's vocabulary to this product's ("customer" -> "company", the customer service's
 * `/customers/owners` -> the rep directory) is one explicit, typed seam in `./mappers.ts`
 * rather than an assumption baked into every screen. Today the fields happen to line up
 * one-for-one — the two schema modules say as much — but the seam is what keeps a future
 * divergence a change to `mappers.ts` instead of a seek-and-replace across the app.
 */

import type {
  ActivityDirection,
  ActivityType,
  CompanyStatus,
  ContactStatus,
  DealStage,
} from "./types";

/** The envelope every paginated gateway list returns (`platform_core.pagination.Page`). */
export interface WirePage<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/** Mirrors `OwnerOut` — the rep directory `GET /customers/owners` returns. */
export interface OwnerOut {
  id: string;
  full_name: string;
  email: string;
  role: string;
}

/** Mirrors `CompanyOut` — what `GET /customers` and `GET /customers/{id}` return. */
export interface CompanyOut {
  id: string;
  name: string;
  domain: string;
  industry: string;
  size_band: string;
  city: string;
  country: string;
  status: CompanyStatus;
  owner_id: string;
  annual_revenue_minor: number;
  currency: string;
  created_at: string;
}

/** Mirrors `ContactOut` — what `GET /contacts` and `GET /contacts/{id}` return. */
export interface ContactOut {
  id: string;
  full_name: string;
  title: string;
  email: string;
  phone: string;
  company_id: string;
  status: ContactStatus;
  owner_id: string;
  last_activity_at: string | null;
  created_at: string;
}

/** Mirrors `DealOut` — what `GET /deals` and `GET /deals/{id}` return. */
export interface DealOut {
  id: string;
  name: string;
  company_id: string;
  primary_contact_id: string | null;
  value_minor: number;
  currency: string;
  stage: DealStage;
  owner_id: string;
  probability: number;
  close_date: string;
  created_at: string;
  last_activity_at: string | null;
  source: string;
}

/** Mirrors `ActivityOut` — what `GET /deals/activities` returns. */
export interface ActivityOut {
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

/** Mirrors `TrendPointOut`. */
export interface TrendPointOut {
  label: string;
  value_minor: number;
}

/** Mirrors `StageRollupOut`. */
export interface StageRollupOut {
  stage: DealStage;
  label: string;
  count: number;
  value_minor: number;
  pct: number;
}

/** Mirrors `SummaryOut` — `GET /deals/summary`. */
export interface SummaryOut {
  pipeline_value_minor: number;
  weighted_pipeline_minor: number;
  open_deals: number;
  win_rate_pct: number;
  won_value_minor: number;
  activities_this_week: number;
  currency: string;
  trend: TrendPointOut[];
  funnel: StageRollupOut[];
}

/* ------------------------------------------------------------------------ write bodies
 * Mirrors the `*In` / `*Patch` request schemas in `services/customer/app/schemas.py` and
 * `services/deal/app/schemas.py`. Every one of these is `extra="forbid"` server-side, so a stray
 * field here would be refused with a 422 rather than silently dropped — the wire type is the
 * request body verbatim, not a superset of it.
 */

/** Mirrors `OwnerIn`. The first write a production org makes — everything else needs an owner. */
export interface OwnerIn {
  full_name: string;
  email: string;
  role?: string;
}

/** Mirrors `CompanyIn`. */
export interface CompanyIn {
  name: string;
  owner_id: string;
  domain?: string;
  industry?: string;
  size_band?: string;
  city?: string;
  country?: string;
  status?: CompanyStatus;
  annual_revenue_minor?: number;
  currency?: string;
}

/** Mirrors `CompanyPatch` — every `CompanyIn` field, optional; absent means "leave alone". */
export interface CompanyPatch {
  name?: string;
  owner_id?: string;
  domain?: string;
  industry?: string;
  size_band?: string;
  city?: string;
  country?: string;
  status?: CompanyStatus;
  annual_revenue_minor?: number;
  currency?: string;
}

/** Mirrors `ContactIn`. Deliberately no `phone` — see `ContactPhoneIn` below. */
export interface ContactIn {
  full_name: string;
  email: string;
  company_id: string;
  owner_id: string;
  title?: string;
  status?: ContactStatus;
  raw_phone?: string;
}

/** Mirrors `ContactPatch`. Deliberately no `phone`, no `raw_phone` — the number only ever moves
 *  through `ContactPhoneIn` on its own route. */
export interface ContactPatch {
  full_name?: string;
  title?: string;
  email?: string;
  company_id?: string;
  owner_id?: string;
  status?: ContactStatus;
}

/** Mirrors `ContactPhoneIn` — the only way a raw number reaches the service. */
export interface ContactPhoneIn {
  raw_phone: string;
}

/** Mirrors `DealIn`. */
export interface DealIn {
  name: string;
  company_id: string;
  owner_id: string;
  value_minor: number;
  close_date: string;
  currency?: string;
  stage?: DealStage;
  probability?: number | null;
  primary_contact_id?: string | null;
  source?: string;
}

/** Mirrors `DealPatch`. Deliberately no `stage` — a stage move only ever goes through
 *  `POST /deals/{id}/stage` (`StageMoveIn`), so the transition rule can never be bypassed. */
export interface DealPatch {
  name?: string;
  company_id?: string;
  primary_contact_id?: string | null;
  owner_id?: string;
  value_minor?: number;
  currency?: string;
  close_date?: string;
  probability?: number | null;
  source?: string;
}

/** Mirrors `StageMoveIn`. */
export interface StageMoveIn {
  stage: DealStage;
  probability?: number | null;
}

/** Mirrors `ActivityIn`. Append-only — there is no matching patch shape. */
export interface ActivityIn {
  type: ActivityType;
  subject: string;
  body?: string | null;
  deal_id?: string | null;
  company_id?: string | null;
  contact_id?: string | null;
  direction?: ActivityDirection | null;
  occurred_at?: string | null;
  owner_id?: string | null;
}
