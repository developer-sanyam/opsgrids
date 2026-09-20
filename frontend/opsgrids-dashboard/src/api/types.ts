/**
 * Wire types, field for field from the services' Pydantic schemas:
 * `product/app/schemas.py`, `catalog/app/schemas.py`, `subscription/app/schemas.py`,
 * `billing/app/schemas.py`, `account/app/schemas.py`. Money is integer minor units with an
 * ISO currency everywhere (ADR-0000 §8); nothing here is a float.
 */

/** `ProductOut` — one row of the marketplace. */
export interface Product {
  key: string;
  name: string;
  tagline: string | null;
  description: string | null;
  category: string | null;
  /** `available` | `beta` | `soon`. Only `available` can be started. */
  status: string;
  connects_to: string[];
  features: string[];
  is_platform: boolean;
}

/** `ActivationOut` — whether this organisation may use a product right now. */
export interface Activation {
  product_key: string;
  status: string;
  entitles: boolean;
  activated_at: string;
  trial_ends_at: string | null;
  deactivated_at: string | null;
}

/** `PriceOut` — one price on a plan version. `flat` is what ADR-0013 §2 sells. */
export interface Price {
  id: string;
  kind: "flat" | "per_seat" | "usage" | string;
  amount_minor: number | null;
  currency: string;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

/** `PlanVersionOut` — an immutable, published version a subscription pins to. */
export interface PlanVersion {
  id: string;
  version_no: number;
  state: string;
  billing_interval: string;
  currency: string;
  restricted_to_org_id: string | null;
  published_at: string | null;
  prices: Price[];
}

/** `PlanOut` — a product's plan with the versions this organisation may see. */
export interface Plan {
  id: string;
  product_key: string;
  code: string;
  name: string;
  state: string;
  sort_order: number;
  versions: PlanVersion[];
}

/** `SubscriptionOut` — the organisation's commitment to a product. */
export interface Subscription {
  id: string;
  product_key: string;
  plan_version_id: string;
  /** `trial` | `active` | `past_due` | `suspended` | `cancelled` */
  state: string;
  trial_ends_at: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at: string | null;
  currency: string;
}

/** `InvoiceOut` — frozen at finalisation, never edited (ADR-0000 §9). */
export interface Invoice {
  id: string;
  number: string;
  subscription_id: string | null;
  state: string;
  currency: string;
  total_minor: number;
  period_start: string;
  period_end: string;
  finalized_at: string | null;
  voided_at: string | null;
}

/** `SignupIn` / `SignupOut` — no password anywhere in either (ADR-0013 §3). */
export interface SignupRequest {
  organization_name: string;
  slug: string;
  full_name: string;
  email: string;
}

export interface SignupResponse {
  organization_id: string;
  slug: string;
  email: string;
  next_step: string;
}
