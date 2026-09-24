/**
 * Wire types for the operator console.
 *
 * These mirror the **deployed** admin-gateway contract field-for-field: console-service's
 * `app/schemas.py` (`libs/contracts/openapi/console.json`) and billing-service's staff
 * credit-ledger route (`libs/contracts/openapi/billing.json`). Money is always an **integer
 * minor amount + ISO-4217 currency** (never a float).
 *
 * Several of these are visibly thinner than a customer-facing screen would want — no seat counts,
 * no per-subscription id outside the pricing composite. That is not an oversight in this file: it
 * is what the service actually returns today. Where the console needs a field the server does not
 * send, the screen that reads it shows an honest "—", never a guess. See each screen for the
 * specific gap and `docs/adr` / the PR description for the backend follow-ups this implies.
 *
 * `name`/`slug` on `OrgStateOut` and `SubscriptionOut` are mirrored from account-service's
 * directory (`core.org.{created,updated}`, or the one-off backfill for an organization that
 * predates console's own event queue) — `null` for a row neither has reached yet, never a
 * guess or a fabricated fallback.
 */

/* ------------------------------ pagination ------------------------------ */

/** `platform_core.pagination.Page` — the *only* endpoint that actually uses it today is billing's
 *  staff credit-ledger read. Console-service's own list routes each use their own bespoke envelope
 *  (`OrganizationsOut`, `SubscriptionsOut`, `UsageListOut`, `AuditListOut` below) — there is no
 *  shared `{items, total}` shape across the admin surface, whatever a generic client might assume. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* ------------------------------ organizations (console) ------------------------------ */

/**
 * One entry of `OrgStateOut.subscription_states` — keyed by `product_key`. The server types this
 * as `dict[str, Any]`: `app/api/routes.py`'s own subscription-flattening code reads it with
 * `.get(...)`, defensively, rather than assuming every key is present. This mirrors that: every
 * field is optional and unknown extra keys are allowed.
 */
export interface SubscriptionStateEntry {
  state?: string;
  plan_version_id?: string | null;
  amount_minor?: number;
  currency?: string | null;
  trial_ends_at?: string | null;
  [key: string]: unknown;
}

/** `OrgStateOut` — one organization's commercial snapshot. `name`/`slug` are mirrored from
 *  account-service's directory and are `null` until the identity fold or the backfill has
 *  reached this organization — still no seat count, which this projection does not carry at all. */
export interface OrgStateOut {
  organization_id: string;
  name: string | null;
  slug: string | null;
  mrr_minor: number;
  currency: string | null;
  active_products: string[];
  subscription_states: Record<string, SubscriptionStateEntry>;
  trial_ends_at: string | null;
  locked: boolean;
  updated_at: string;
}

/** `MetricsOut` — platform-wide commercial metrics, computed off the transition log. */
export interface MetricsOut {
  mrr_minor: number;
  currency: string | null;
  trials_started: number;
  trial_conversions: number;
  trial_conversion_rate: number;
  active_subscriptions: number;
  churned: number;
  churn_rate: number;
}

/** `OrganizationsOut` — `GET /admin/organizations`. Not a `Page<T>`: every org, unfiltered, plus
 *  the platform metrics alongside it. The server accepts no search/status/pagination query params
 *  on this route, so any filtering happens client-side over the full set. */
export interface OrganizationsOut {
  organizations: OrgStateOut[];
  metrics: MetricsOut;
}

/** `LifecycleCountsOut` — organization counts by lifecycle bucket. */
export interface LifecycleCountsOut {
  suspended: number;
  active: number;
  trial: number;
  churned: number;
  none: number;
}

/** `MetricsOverviewOut` — `GET /admin/metrics/overview`. There is no historical series, no
 *  period-over-period delta and no revenue-this-period figure in this payload — those are Overview
 *  dashboard concepts this endpoint does not (yet) compute. */
export interface MetricsOverviewOut extends MetricsOut {
  organization_count: number;
  organizations_by_lifecycle: LifecycleCountsOut;
}

/** `OrgDetailOut` — `GET /admin/organizations/{id}`. The *only* place the console reads an
 *  organization's directory identity (name, slug, status, country) — composed from
 *  account-service's staff directory route. `commercial` is `null` only when console has not yet
 *  projected anything for this org (never for "organization does not exist" — that is a 404). */
export interface OrgDetailOut {
  organization_id: string;
  slug: string;
  name: string;
  status: string;
  country: string | null;
  created_at: string;
  commercial: OrgStateOut | null;
}

/** `SubscriptionOut` — one row of `GET /admin/subscriptions`, flattened from every org's
 *  `subscription_states`. `name`/`slug` are the same account-service mirror as `OrgStateOut`'s;
 *  there is still no subscription id, no product/plan name and no seat count in this projection. */
export interface SubscriptionOut {
  organization_id: string;
  name: string | null;
  slug: string | null;
  product_key: string;
  state: string;
  plan_version_id: string | null;
  amount_minor: number;
  currency: string | null;
  trial_ends_at: string | null;
}

export interface SubscriptionsOut {
  subscriptions: SubscriptionOut[];
}

/** `UsageOut` — one row of `GET /admin/usage`. Just the meter and the raw quantity; no meter name,
 *  unit, included allowance or billability flag. */
export interface UsageOut {
  organization_id: string;
  meter_key: string;
  period: string;
  quantity: number;
}

export interface UsageListOut {
  usage: UsageOut[];
}

/** `AuditOut` — one row of `GET /admin/audit`. No actor name/email, no organization name, no
 *  human summary and no success/failure flag: a row is only ever written *after* the write it
 *  records succeeded (`services.record_override` / `record_organization_read`), so there is no
 *  "failed" row to distinguish. */
export interface AuditOut {
  id: string;
  organization_id: string;
  actor_id: string | null;
  acting_as: string | null;
  action: string;
  entity: string;
  entity_id: string | null;
  reason: string | null;
  at: string;
}

export interface AuditListOut {
  audit: AuditOut[];
}

/* --------------------------- override I/O (console) --------------------------- */

/** `OverrideIn` — `POST /admin/overrides`. `delegation.py` resolves exactly two actions today;
 *  posting anything else is a 400 ("unknown override action"). There is no `reactivate` and no
 *  `bespoke_price` action on this endpoint — bespoke pricing is `POST .../pricing` (below). */
export type OverrideRequest =
  | { action: "suspend"; organization_id: string; subscription_id: string; reason: string }
  | {
      action: "comp_grant";
      organization_id: string;
      amount_minor: number;
      currency: string;
      reason?: string;
    };

/** `OverrideOut`. No structured audit row comes back — `result` is whatever JSON the owning
 *  service's internal endpoint happened to return, `dict[str, Any]` server-side. */
export interface OverrideResult {
  action: string;
  organization_id: string;
  delegated_to: string;
  result: Record<string, unknown>;
}

/* ------------------------------ credit ledger (billing) ------------------------------ */

/** The values `credit_ledger.entry_type` is constrained to at the database
 *  (`entry_type in ('grant', 'consume', 'reversal', 'expiry')`). No `is_comp` flag exists anywhere
 *  in this schema — the console cannot distinguish a complimentary grant from a monetary one. */
export type LedgerEntryType = "grant" | "consume" | "reversal" | "expiry";

/** `CreditLedgerEntryAdminOut` — one row of `GET /admin/credit-ledger`. No organization name
 *  (only the id) and no actor. */
export interface CreditLedgerEntryAdminOut {
  id: string;
  organization_id: string;
  entry_type: string;
  amount_minor: number;
  currency: string;
  reason: string;
  valid_from: string;
  valid_until: string | null;
  reversal_of: string | null;
  drawn_from: string | null;
  created_at: string;
}

export interface ListLedgerParams {
  organizationId?: string;
  currency?: string;
  entryType?: string;
  page?: number;
  size?: number;
}

/* ------------------------------ client pricing (console) ------------------------------ */
/* Verified field-for-field against `services/console/app/schemas.py`'s pricing models — this
 * section was already correct against the live contract and is unchanged. */

/** `month` or `year` — the two billing cadences a plan version can carry (ADR-0004 §1). */
export type BillingInterval = "month" | "year";

/** The minimal organization reference the pricing card needs — an id to call the pricing route
 *  with, and a name for its dialogs. Deliberately not the bulk list row: `/admin/organizations`
 *  does not carry a name, only `/admin/organizations/{id}` does. */
export interface PricingOrgRef {
  organization_id: string;
  name: string;
}

/** The subscription reference a pricing row carries — enough to know what "applies now" means
 *  without pulling a whole subscription shape across a second endpoint. `state` is a free string
 *  server-side (`ProductSubscriptionOut.state: str`), not a closed enum. */
export interface PricingSubscriptionRef {
  id: string;
  state: string;
  plan_version_id: string;
  trial_ends_at: string | null;
  current_period_end: string | null;
}

/** What this organization is paying right now for one product — `null` until a price has ever
 *  been set for them. `is_bespoke` is the "Custom" vs "List price" badge. */
export interface ProductCurrentPrice {
  plan_version_id: string;
  is_bespoke: boolean;
  amount_minor: number;
  currency: string;
  billing_interval: BillingInterval;
}

/** The public flat price for reference — what a non-bespoke customer on this product's standard
 *  plan pays. Also the plan a bespoke price clones from; absent when the product has no public
 *  flat plan to clone (the 409 `Set price` can return). */
export interface ProductPublicPrice {
  plan_version_id: string;
  amount_minor: number;
  currency: string;
  billing_interval: BillingInterval;
}

/** One row of who set a price, to what, when and why. */
export interface PricingHistoryEntry {
  plan_version_id: string;
  amount_minor: number;
  currency: string;
  billing_interval: BillingInterval;
  created_at: string;
  /** PII-masked per CLAUDE.md's platform rules — never the operator's full address. `null` when
   *  the row has no attributable actor (a migration, or a staff account since deleted). */
  created_by_email_masked: string | null;
  reason: string;
}

/** One product's whole pricing picture for one organization. */
export interface ProductPricing {
  product_key: string;
  product_name: string;
  subscription: PricingSubscriptionRef | null;
  current_price: ProductCurrentPrice | null;
  public_price: ProductPublicPrice | null;
  history: PricingHistoryEntry[];
}

/** `GET /api/v1/admin/organizations/{id}/pricing`. `console.pricing.read`. */
export interface OrganizationPricing {
  organization_id: string;
  products: ProductPricing[];
}

/** `POST /api/v1/admin/organizations/{id}/pricing` body. Amount is exact integer paise, parsed from
 *  the operator's rupee entry with string arithmetic — never a float (ADR-0000 §8). */
export interface SetPriceRequest {
  product_key: string;
  amount_minor: number;
  currency: "INR";
  billing_interval: BillingInterval;
  reason: string;
  apply_to_subscription: boolean;
}

/** When the new price takes hold. */
export type PricingEffect = "now" | "next_period" | "not_subscribed";

/** `201` from setting a price. `console.pricing.write`. */
export interface SetPriceResult {
  plan_version_id: string;
  applied_to_subscription_id: string | null;
  effective: PricingEffect;
}

/* ----------------------------- shared UI vocabulary ------------------------------ */

/** A labelled segment of a breakdown bar. Used today for the real
 *  `organizations_by_lifecycle` counts the overview metrics endpoint returns. */
export interface BreakdownSegment {
  key: string;
  label: string;
  count: number;
  tone: "active" | "trial" | "pastdue" | "suspended" | "cancelled" | "comp" | "accent";
}
