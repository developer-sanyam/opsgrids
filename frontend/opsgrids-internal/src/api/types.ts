/**
 * Wire types for the operator console.
 *
 * These mirror the documented admin-gateway contract (ADR-0003 §2, ADR-0004): the console reads
 * cross-tenant commercial data on the `operator` audience and drives the customer lifecycle. Money
 * is always an **integer minor amount + ISO-4217 currency** (never a float). The console is a
 * *consumer of the transition stream* — MRR, trial-conversion and churn are projections, not live
 * table queries — so these shapes carry already-computed metrics rather than raw event rows.
 */

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/** The lifecycle state shared by an organization and its primary subscription (ADR-0004 §4). */
export type AccountStatus = "active" | "trial" | "past_due" | "suspended" | "cancelled";

/** A person on the customer side — the operator's point of contact. */
export interface Contact {
  name: string;
  email: string;
}

/** One row in the cross-tenant accounts directory (BYPASSRLS SELECT-only read, ADR-0003 §2). */
export interface OrganizationRow {
  organization_id: string;
  name: string;
  slug: string;
  status: AccountStatus;
  /** Human plan label, e.g. "Scale". */
  plan_name: string;
  primary_product_key: string;
  primary_product_name: string;
  /** Monthly recurring revenue this account contributes, in minor units of `currency`. */
  mrr_minor: number;
  currency: string;
  seats: number;
  /** Pinned to a bespoke, audience-of-one plan version (ADR-0004 §2). */
  is_bespoke: boolean;
  /** Access is locked — a suspended account whose entitlement has lapsed to a 402. */
  locked: boolean;
  created_at: string;
  last_activity_at: string | null;
  /** Set only while `status === "trial"`. */
  trial_ends_at: string | null;
  region: string;
  primary_contact: Contact;
}

/** The activation projection for one product — what the gateway's `ent` claim gates on. */
export interface Entitlement {
  product_key: string;
  product_name: string;
  /** Whether `Activation.entitles` currently evaluates true (trial expiry read at read time). */
  entitled: boolean;
  state: AccountStatus;
  /** Trial end or grant lapse that will flip entitlement, if any. */
  expires_at: string | null;
}

/** A per-customer subscription and where it sits in its lifecycle. */
export interface Subscription {
  subscription_id: string;
  organization_id: string;
  organization_name: string;
  product_key: string;
  product_name: string;
  plan_name: string;
  plan_version_id: string;
  plan_version_no: number;
  is_bespoke: boolean;
  state: AccountStatus;
  seats: number;
  /** Monthly amount pinned from the plan version, in minor units. */
  amount_minor: number;
  currency: string;
  trial_ends_at: string | null;
  current_period_start: string;
  current_period_end: string;
  next_renewal_at: string | null;
  cancel_at: string | null;
  created_at: string;
  /**
   * The products this subscription switches on, snapshotted from the pinned version: the
   * product itself, or a bundle's members. Absent from a read model that predates it.
   */
  entitled_products?: string[];
}

/** One usage meter's roll-up for the current period. */
export interface UsageSummary {
  meter_key: string;
  meter_name: string;
  product_key: string;
  unit: string;
  quantity: number;
  /** Included allowance, if the plan meters it; null when unmetered. */
  included: number | null;
  /** `is_billable=false` until a plan version prices the meter (ADR-0004 §7). */
  is_billable: boolean;
  period_label: string;
}

/** A finalized-or-draft invoice. Frozen at finalize; corrections are new rows (ADR-0004). */
export interface Invoice {
  invoice_id: string;
  organization_id: string;
  number: string;
  state: "draft" | "finalized" | "void";
  currency: string;
  total_minor: number;
  period_start: string;
  period_end: string;
  finalized_at: string | null;
  issued_at: string;
}

/** The kind of movement a credit-ledger row records. */
export type LedgerKind = "grant" | "consumption" | "reversal";

/**
 * One append-only credit-ledger entry (ADR-0004 §3). A grant is positive; consumption is negative;
 * a reversal points at the entry it undoes. Balance and "who is on comp" are SUMs/queries over
 * these rows — never a stored column.
 */
export interface CreditLedgerEntry {
  entry_id: string;
  organization_id: string;
  organization_name: string;
  amount_minor: number;
  currency: string;
  kind: LedgerKind;
  reason: string;
  valid_from: string;
  /** null means no expiry. For a comp grant this is when the comp access lapses. */
  valid_until: string | null;
  created_at: string;
  actor: string;
  reversal_of: string | null;
  /** Complimentary access (vs a monetary credit) — the "who's on comp" filter. */
  is_comp: boolean;
}

/* ------------------------------ catalog ------------------------------ */

export interface Product {
  product_key: string;
  name: string;
  description: string;
}

export interface Plan {
  plan_id: string;
  product_key: string;
  code: string;
  name: string;
  state: "active" | "retired";
  sort_order: number;
}

export type PriceKind = "flat" | "per_seat" | "usage";

export interface Price {
  price_id: string;
  kind: PriceKind;
  amount_minor: number;
  currency: string;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

export type PlanVersionState = "draft" | "published" | "grandfathered";

export interface PlanVersion {
  plan_version_id: string;
  plan_id: string;
  product_key: string;
  plan_name: string;
  version_no: number;
  state: PlanVersionState;
  published_at: string | null;
  billing_interval: "monthly" | "yearly";
  currency: string;
  /** Non-null makes this an audience-of-one bespoke version (ADR-0004 §2). No FK — a plain UUID. */
  restricted_to_org_id: string | null;
  restricted_to_org_name: string | null;
  prices: Price[];
  subscriber_count: number;
  /** A bundle's members, from its `bundled_products` feature. Absent for a single product. */
  bundled_products?: string[];
}

/* ------------------------------ audit ------------------------------- */

export type OverrideKind =
  | "suspend"
  | "reactivate"
  | "comp_grant"
  | "bespoke_price"
  | "plan_migration";

export interface AuditEntry {
  audit_id: string;
  occurred_at: string;
  actor: string;
  actor_email: string;
  /** e.g. "override.suspend", "override.comp_grant", "catalog.bespoke_price.created". */
  action: string;
  organization_id: string | null;
  organization_name: string | null;
  summary: string;
  result: "success" | "failed";
  target_ref: string | null;
}

/* ----------------------------- metrics ------------------------------ */

/** One point on the MRR-over-time series. */
export interface MrrPoint {
  label: string;
  mrr_minor: number;
}

/** A labelled segment of a breakdown bar (trial funnel, churn split). */
export interface BreakdownSegment {
  key: string;
  label: string;
  count: number;
  tone: "active" | "trial" | "pastdue" | "suspended" | "cancelled" | "comp" | "accent";
}

/** The Overview dashboard's headline metrics — all pre-computed projections. */
export interface OverviewMetrics {
  currency: string;
  period_label: string;

  mrr_minor: number;
  mrr_delta_pct: number;
  mrr_sparkline: number[];
  mrr_series: MrrPoint[];

  active_accounts: number;
  active_delta_pct: number;
  active_sparkline: number[];

  trials_in_progress: number;
  trials_delta_pct: number;
  trials_sparkline: number[];

  trial_conversion_pct: number;
  conversion_delta_pct: number;
  conversion_sparkline: number[];

  churn_pct: number;
  churn_delta_pct: number;
  churn_sparkline: number[];

  locked_accounts: number;
  suspended_accounts: number;

  revenue_period_minor: number;
  revenue_delta_pct: number;

  /** Trial funnel over the trailing 90 days: started → converted / still-trialing / expired. */
  trial_funnel: BreakdownSegment[];
  /** Net revenue-retention split for the period: retained / expansion / churned. */
  churn_breakdown: BreakdownSegment[];
}

/** The composed account-detail payload the console reads for one org. */
export interface OrganizationDetail {
  organization: OrganizationRow;
  subscriptions: Subscription[];
  entitlements: Entitlement[];
  usage: UsageSummary[];
  invoices: Invoice[];
  ledger: CreditLedgerEntry[];
  /** SUM over ledger entries whose window covers now — the live comp/credit balance. */
  comp_balance_minor: number;
}

/* --------------------------- override I/O --------------------------- */

/** Request bodies for `POST /api/v1/admin/overrides`, discriminated on `kind`. */
export type OverrideRequest =
  | { kind: "suspend"; organization_id: string; reason: string }
  | { kind: "reactivate"; organization_id: string; reason: string }
  | {
      kind: "comp_grant";
      organization_id: string;
      amount_minor: number;
      currency: string;
      is_comp: boolean;
      reason: string;
      valid_until: string | null;
    }
  | {
      kind: "bespoke_price";
      organization_id: string;
      product_key: string;
      base_plan_id: string;
      amount_minor: number;
      currency: string;
      reason: string;
    }
  | {
      kind: "plan_migration";
      organization_id: string;
      subscription_id: string;
      target_plan_version_id: string;
      reason: string;
    };

/**
 * What the console needs to say about an accepted override: that it was, in a sentence, and which
 * audit row records it when the owning service names one. Deliberately not the wire body — the
 * shape `POST /admin/overrides` answers with is `OverrideOut`, and the screens read only this.
 */
export interface OverrideResult {
  status: "accepted";
  summary: string;
  audit_id: string | null;
}

/** What the accounts list may be narrowed by. */
export interface ListOrganizationsParams {
  search?: string;
  status?: AccountStatus | "all";
  bespoke?: boolean;
}

/** What the subscriptions list may be narrowed by. */
export interface ListSubscriptionsParams {
  search?: string;
  state?: AccountStatus | "all";
}

/** What the credit ledger may be narrowed by. */
export interface ListLedgerParams {
  search?: string;
  compOnly?: boolean;
}

/** What the audit log may be narrowed by. */
export interface ListAuditParams {
  search?: string;
  action?: string | "all";
}

/** The catalog as one read: what is sold, the plans under it, and every version of each. */
export interface CatalogBundle {
  products: Product[];
  plans: Plan[];
  versions: PlanVersion[];
}
