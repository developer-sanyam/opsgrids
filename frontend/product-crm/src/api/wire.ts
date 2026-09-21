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
