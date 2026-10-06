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

/** `OrgStateOut` — one organization's commercial snapshot. `name`/`slug`/`country`/`created_at`
 *  are mirrored from account-service's directory and are `null` until the identity fold or the
 *  backfill has reached this organization. `lifecycle` is computed server-side
 *  (`selectors.lifecycle_of`): `suspended` (any subscription locked) beats `active` (any
 *  subscription active) beats `trial` beats `churned` (history but nothing live) beats `none`
 *  (the fold has never touched this organization) — the client never reimplements this rule.
 *  `user_count` is merged in live from account-service's bulk member-count read: `null` means
 *  that call could not be reached in time, never "zero members". */
export interface OrgStateOut {
  organization_id: string;
  name: string | null;
  slug: string | null;
  country: string | null;
  created_at: string | null;
  lifecycle: "suspended" | "active" | "trial" | "churned" | "none";
  mrr_minor: number;
  currency: string | null;
  active_products: string[];
  subscription_states: Record<string, SubscriptionStateEntry>;
  trial_ends_at: string | null;
  locked: boolean;
  updated_at: string;
  user_count: number | null;
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

/** `MetricsOverviewOut` — `GET /admin/metrics/overview`. A live snapshot: no historical series and
 *  no period-over-period delta of its own — those come from `GET /admin/metrics/series` below. */
export interface MetricsOverviewOut extends MetricsOut {
  organization_count: number;
  organizations_by_lifecycle: LifecycleCountsOut;
}

/** One point on a monthly series. `value` is `null` for a month before this series has any
 *  visibility at all — never `0`, which would claim a real, counted "nothing happened" the data
 *  cannot back. A delta between two points is only ever shown when **both** are non-null. */
export interface SeriesPointOut {
  period: string;
  value: number | null;
}

/** `MetricsSeriesOut` — `GET /admin/metrics/series?months=`. Monthly account growth and commercial
 *  history, oldest first; every series carries the same ordered list of periods. Feeds the account
 *  growth chart and every "vs last month" delta. */
export interface MetricsSeriesOut {
  currency: string | null;
  mrr_minor: SeriesPointOut[];
  new_organizations: SeriesPointOut[];
  trials_started: SeriesPointOut[];
  trial_conversions: SeriesPointOut[];
  churned: SeriesPointOut[];
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
 *  `subscription_states`. `name`/`slug` are the same account-service mirror as `OrgStateOut`'s.
 *  `product_name`/`plan_name` are catalog's display names, best-effort — `null` when catalog could
 *  not be reached or no longer knows the version; there is still no subscription id or seat count
 *  in this projection. */
export interface SubscriptionOut {
  organization_id: string;
  name: string | null;
  slug: string | null;
  product_key: string;
  product_name: string | null;
  state: string;
  plan_version_id: string | null;
  plan_name: string | null;
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

/** `OverrideIn` — `POST /admin/overrides`. `delegation.py` resolves exactly three actions;
 *  posting anything else is a 400 ("unknown override action"). There is no `bespoke_price` action
 *  on this endpoint — bespoke pricing is `POST .../pricing` (below).
 *
 *  `reactivate` requires a non-blank `reason` (at most 200 characters) — `delegation.py` rejects a
 *  blank one before the call ever reaches subscription-service, because lifting a hold is exactly
 *  the act whose why an audit trail must carry. */
export type OverrideRequest =
  | { action: "suspend"; organization_id: string; subscription_id: string; reason: string }
  | { action: "reactivate"; organization_id: string; subscription_id: string; reason: string }
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
  /** The pinned plan's display name ("Growth") — null when catalog no longer knows the version. */
  plan_name: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  /** The version waiting for the next period, when a change is scheduled. A scheduled change emits
   *  no event, so it never reaches the commercial projection — this live composite is the only
   *  place it shows. */
  pending_plan_version_id: string | null;
  pending_plan_name: string | null;
  cancel_at: string | null;
  /** What the subscription switches on — one key, or a bundle's members. */
  entitled_products: string[];
  /** When the current period — for a trial, the trial itself — began. UTC. */
  current_period_start: string | null;
  /** While trialling: the latest the trial may be extended to, stated by the subscription service
   *  from its own cap (ADR-0031). Null for any other state. UTC. */
  trial_max_ends_at: string | null;
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

/* ------------------------------ plan switching (console) ------------------------------ */
/* Verified field-for-field against `services/console/app/schemas.py`'s "staff plan switching"
 * models (ADR-0029). The server decides direction and timing; nothing here computes either. */

/** The state the operator saw — the live state a preview returns, sent back with the confirm.
 *  The subscription compares it under its row lock; a mismatch is a 409 `state_changed`. */
export interface ExpectedState {
  state: string;
  plan_version_id: string;
  pending_plan_version_id: string | null;
}

/** One priced component of a plan option. Integer minor units, or integer micros for a usage rate
 *  — never a float. */
export interface PlanOptionPrice {
  kind: "flat" | "per_seat" | "usage" | string;
  amount_minor: number | null;
  currency: string;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

/** One plan version the picker may offer, and whether this subscription can take it. */
export interface PlanOption {
  plan_id: string;
  plan_name: string;
  plan_version_id: string;
  version_no: number;
  billing_interval: "month" | "year" | "one_time" | string;
  currency: string;
  /** Negotiated for this organization alone. */
  is_bespoke: boolean;
  /** Flat plus per-seat at quantity one, for display only — the preview decides direction. */
  recurring_minor: number;
  prices: PlanOptionPrice[];
  bundled_products: string[] | null;
  trial_days: number | null;
  is_current: boolean;
  is_pending: boolean;
  /** `currency` or `cadence` when this subscription cannot take the version; null when it can. */
  blocked_code: "currency" | "cadence" | string | null;
  blocked_reason: string | null;
}

export interface PlanOptionsSubscription {
  id: string;
  state: string;
  plan_version_id: string;
  plan_name: string | null;
  pending_plan_version_id: string | null;
  pending_plan_name: string | null;
  currency: string;
  billing_interval: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  /** `state` when the subscription cannot change plan at all (past_due, suspended, cancelled). */
  change_blocked_code: "state" | string | null;
}

/** `GET /api/v1/admin/organizations/{id}/plan-options?product_key=`. `owner` and `billing_ops`. */
export interface PlanOptions {
  organization_id: string;
  product_key: string;
  subscription: PlanOptionsSubscription;
  options: PlanOption[];
}

/** `auto`: the server decides from the subscription's state and the change's direction.
 *  `next_period` states the same rule explicitly. There is deliberately no `now`: staff do not
 *  force a paying subscription to switch immediately (goodwill is a comp credit). */
export type PlanChangeTiming = "auto" | "next_period";

export interface PlanChangePreviewRequest {
  plan_version_id: string;
  timing: PlanChangeTiming;
}

export type PlanChangeDirection = "upgrade" | "downgrade" | "equal";
export type PlanChangeEffective = "now" | "next_period";

/** `POST .../subscriptions/{product_key}/plan-change:preview` — subscription's own answer, no
 *  amount (a paid change is priced by the billing run when it claims the period). */
export interface PlanChangePreview {
  expected: ExpectedState;
  action: "switch" | "withdraw_scheduled";
  direction: PlanChangeDirection | null;
  effective: PlanChangeEffective | null;
  /** When a `next_period` change lands — the end of the current period, UTC. */
  effective_at: string | null;
  would_clear_pending: boolean;
  entitled_added: string[];
  entitled_removed: string[];
  /** A usage-priced component is involved; direction compares the recurring part only. */
  usage_priced: boolean;
  /** Applying now closes the usage metered so far at the **current** plan's usage rates, as its
   *  own invoice the billing run raises; usage from then on is priced on the new plan. When set,
   *  an equal-price switch is "no proration", never "nothing is charged". */
  usage_closed_now: boolean;
  /** Set when the confirm would be refused (`not_offerable`: the catalogue no longer offers this
   *  version to this organization — superseded by a newer one, or its plan retired). */
  refusal_code: string | null;
  refusal_detail: string | null;
}

/** `POST .../subscriptions/{product_key}/plan-change` body. The `Idempotency-Key` header is
 *  required and travels separately. */
export interface PlanChangeRequest {
  plan_version_id: string;
  timing: PlanChangeTiming;
  /** Why — required, 1-500 characters, kept on the audit trail only (never on the subscription's
   *  event, which the customer org's own notification templates can render). */
  reason: string;
  expected: ExpectedState;
}

/** What the change did, read back off subscription's own response. */
export interface PlanChangeResult {
  effective: PlanChangeEffective;
  state: string;
  plan_version_id: string;
  pending_plan_version_id: string | null;
  effective_at: string | null;
  /** True when this is the recorded result of an earlier request with the same key. */
  replayed: boolean;
}

/* ------------------------- add product, extend trial (console) ------------------------- */
/* Verified field-for-field against `services/console/app/schemas.py`'s "staff add product" and
 * "staff extend trial" models (ADR-0031). */

/** One plan version the Add product picker may offer. Amounts are integer minor units. */
export interface AddProductOption {
  plan_id: string;
  plan_name: string;
  plan_version_id: string;
  version_no: number;
  billing_interval: "month" | "year" | "one_time" | string;
  currency: string;
  is_bespoke: boolean;
  /** Flat plus per-seat at quantity one — display only. */
  recurring_minor: number;
  prices: PlanOptionPrice[];
  bundled_products: string[] | null;
  /** The version's own trial length; null means the platform default. */
  trial_days: number | null;
  /** False when the version sells no trial — only a paid add is possible. */
  offers_trial: boolean;
  /** A bundle's members the organization already pays for through another subscription. */
  overlapping_products: string[];
  /** `org_currency_mismatch` or `one_time` when the organization cannot take it; null when it can. */
  blocked_code: "org_currency_mismatch" | "one_time" | string | null;
  blocked_reason: string | null;
}

export interface AddProductCandidate {
  product_key: string;
  product_name: string;
  /** The live subscriptions (by product key — a bundle) that already entitle the organization to
   *  this product. Adding it on its own bills both, with no credit. */
  entitled_through: string[];
  versions: AddProductOption[];
}

/** A product the organization already has a row for, in any state — never offered. */
export interface ExistingSubscriptionRef {
  product_key: string;
  state: string;
}

/** `GET /api/v1/admin/organizations/{id}/add-product-options`. `owner` and `billing_ops`. */
export interface AddProductOptions {
  organization_id: string;
  /** The currency the organization's live subscriptions are billed in, when there is one. */
  currency: string | null;
  existing: ExistingSubscriptionRef[];
  products: AddProductCandidate[];
}

/** `POST /api/v1/admin/organizations/{id}/subscriptions` body. The `Idempotency-Key` header is
 *  required and travels separately. A paid add (`trial: false`) is refused unless
 *  `acknowledge_immediate_charge` is true: its first period is claimed for payment at once. */
export interface AddProductRequest {
  product_key: string;
  plan_version_id: string;
  trial: boolean;
  acknowledge_immediate_charge: boolean;
  reason: string;
}

/** What was opened, read back off the subscription service's own answer. */
export interface AddProductResult {
  subscription_id: string;
  product_key: string;
  state: string;
  plan_version_id: string;
  currency: string;
  trial_ends_at: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  entitled_products: string[];
  /** True for a paid add: its first period was claimed for billing in the same transaction. */
  charge_claimed: boolean;
  replayed: boolean;
}

/** `POST .../subscriptions/{product_key}/extend-trial` body — **absolute** instants, both with a
 *  zone. `expected_trial_ends_at` is the end the operator saw, sent back exactly as it was read. */
export interface ExtendTrialRequest {
  new_trial_ends_at: string;
  expected_trial_ends_at: string;
  reason: string;
}

export interface ExtendTrialResult {
  subscription_id: string;
  product_key: string;
  state: string;
  trial_ends_at: string | null;
  previous_trial_ends_at: string | null;
  replayed: boolean;
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
