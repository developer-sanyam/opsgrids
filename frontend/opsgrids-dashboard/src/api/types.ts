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
  /** A bundle (`suite`) is sold, never opened: a subscription to it entitles its members. */
  is_bundle: boolean;
}

/** `ActivationOut` — whether this organisation may use a product right now. */
export interface Activation {
  product_key: string;
  status: string;
  entitles: boolean;
  activated_at: string;
  trial_ends_at: string | null;
  deactivated_at: string | null;
  /** Keys of the subscriptions entitling it now: its own, and/or a bundle's (`suite`). */
  entitled_by: string[];
}

/**
 * `TenantPriceOut` — a price component's shape, never its amount. Pricing is negotiated per
 * organization by staff (the owner's call), so a client never reads a list price off this API;
 * `kind` is enough to tell a flat offer exists to start a trial against.
 */
export interface Price {
  kind: "flat" | "per_seat" | "usage" | string;
  unit: string | null;
  meter_key: string | null;
}

/** `TenantPlanVersionOut` — an immutable, published version a subscription pins to. No amount
 * or currency: those are set per organization by staff, not read off the catalogue. */
export interface PlanVersion {
  id: string;
  version_no: number;
  state: string;
  billing_interval: string;
  restricted_to_org_id: string | null;
  published_at: string | null;
  prices: Price[];
  /** The version's feature sheet. A bundle names its members in `bundled_products`. */
  features: Feature[];
}

/** `FeatureOut` — `value` is whatever JSON the catalogue stored. */
export interface Feature {
  feature_key: string;
  value: unknown;
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

/** `SubscriptionItemOut`. */
export interface SubscriptionItem {
  id: string;
  price_id: string;
  quantity: number;
}

/** `DunningOut` — the live grace window, present only while a collection cycle is open. */
export interface Dunning {
  cycle_id: string;
  opened_at: string;
  grace_ends_at: string;
  next_attempt_at: string | null;
  attempts_made: number;
  max_attempts: number;
  amount_minor: number | null;
  currency: string | null;
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
  /** A plan change waiting for the next period: the version it moves to. Null when none waits. */
  pending_plan_version_id?: string | null;
  currency: string;
  /** What this subscription switches on — the product itself, or a bundle's members. */
  entitled_products: string[];
  items: SubscriptionItem[];
  dunning: Dunning | null;
}

/**
 * `CustomerExpectedState` — the subscription as a preview saw it, sent back whole with the change.
 * The period end and the billing run's pending claim decide when a change lands and whether it is
 * charged, so a roll or a claim in between is refused (`state_changed`) rather than confirmed blind.
 */
export interface ExpectedState {
  state: string;
  plan_version_id: string;
  pending_plan_version_id: string | null;
  current_period_end: string | null;
  lifecycle_claim_id: string | null;
}

/** Who scheduled the waiting change: the OpsGrids team (a negotiated price) or the organisation. */
export type ScheduledBy = "opsgrids" | "customer";

/** `PlanPriceOut` — one component of the organisation's own negotiated price. Integer minor units. */
export interface PlanPrice {
  kind: string;
  amount_minor: number | null;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

/**
 * `PlanRefOut` — a plan as the customer may see it. `amount_visible` is true only for a price
 * negotiated for this organisation, shown to someone who may read its invoices; a public (list)
 * price is never sent (pricing is agreed per client).
 */
export interface PlanRef {
  plan_version_id: string;
  plan_id: string | null;
  plan_name: string | null;
  version_no: number | null;
  billing_interval: string | null;
  negotiated: boolean;
  amount_visible: boolean;
  currency: string | null;
  recurring_minor: number | null;
  prices: PlanPrice[] | null;
}

/** `PlanOptionOut` — one plan the picker offers, and whether this subscription can take it. */
export interface PlanOption extends PlanRef {
  is_current: boolean;
  is_pending: boolean;
  /** `current`, `pending`, `cadence`, `currency` or `bundle`; null when it can be chosen. */
  blocked_code: string | null;
  blocked_reason: string | null;
}

/** `PlanOptionsOut` — the Change plan picker, from live reads. */
export interface PlanOptions {
  subscription_id: string;
  product_key: string;
  state: string;
  /** `bundle`, `state` or `scheduled_by_opsgrids` when this subscription cannot change plan at all. */
  change_blocked_code: string | null;
  current: PlanRef | null;
  pending: PlanRef | null;
  pending_effective_at: string | null;
  pending_scheduled_by: ScheduledBy | null;
  options: PlanOption[];
}

/** `SubscriptionPlanSummaryOut` — one row's plan line. A cancelled row never has a scheduled change. */
export interface SubscriptionPlanSummary {
  subscription_id: string;
  current: PlanRef | null;
  pending: PlanRef | null;
  pending_effective_at: string | null;
  pending_scheduled_by: ScheduledBy | null;
}

/** `ChangePlanPreviewOut` — what a change would do, as the server computes it. No amount. */
export interface PlanChangePreview {
  expected: ExpectedState;
  action: "switch" | "withdraw_scheduled";
  /** Null for a refusal, a withdrawal, and a change that is free (`paid` false). */
  direction: "upgrade" | "downgrade" | "equal" | null;
  effective: "now" | "next_period" | null;
  effective_at: string | null;
  would_clear_pending: boolean;
  entitled_added: string[];
  entitled_removed: string[];
  usage_priced: boolean;
  usage_closed_now: boolean;
  paid: boolean;
  prorated_charge_now: boolean;
  refusal_code: string | null;
  refusal_detail: string | null;
}

/** `InvoiceLineOut`. */
export interface InvoiceLine {
  id: string;
  description: string;
  /** A decimal on the wire (`Decimal`); kept as a string so nothing rounds it in transit. */
  quantity: string;
  unit_price_micros: number | null;
  amount_minor: number;
  plan_version_id: string | null;
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
  lines: InvoiceLine[];
}

/** `CreditBalanceOut`. */
export interface CreditBalance {
  organization_id: string;
  currency: string;
  balance_minor: number;
  as_of: string;
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

// ------------------------------------------------------------------ paging envelope

/** `platform_core.pagination.Page[T]` — offset paging, counted and fetched in one transaction. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

// ------------------------------------------------------------------ organization profile

/** `OrganizationOut` — `services/account/app/schemas.py`. */
export interface Organization {
  id: string;
  slug: string;
  name: string;
  legal_name: string | null;
  industry: string | null;
  status: string;
  country: string | null;
  state_code: string | null;
  timezone: string;
  currency: string;
  locale: string;
  fiscal_year_start_month: number;
  tax_ids: Record<string, unknown>;
  registered_address: Record<string, unknown> | null;
  primary_contact_email: string | null;
  settings: Record<string, unknown>;
  parent_org_id: string | null;
  trial_ends_at: string | null;
  created_at: string;
}

/** `OrganizationPatch` — every field optional; absent means "leave alone", never a null-out. */
export interface OrganizationPatch {
  name?: string;
  legal_name?: string;
  industry?: string;
  country?: string;
  state_code?: string;
  timezone?: string;
  locale?: string;
  fiscal_year_start_month?: number;
  primary_contact_email?: string;
  tax_ids?: Record<string, string>;
  registered_address?: Record<string, string>;
}

// ------------------------------------------------------------------ org units

/** `OrgUnitOut` plus `children` (`OrgUnitNode`), so the tree renders in one call. */
export interface OrgUnit {
  id: string;
  name: string;
  code: string;
  parent_id: string | null;
  head_membership_id: string | null;
  kind: string;
  is_active: boolean;
  children: OrgUnit[];
}

export interface OrgUnitCreate {
  name: string;
  code: string;
  parent_id?: string;
  kind?: string;
}

/** `OrgUnitPatch` — `code` is immutable server-side. */
export interface OrgUnitPatch {
  name?: string;
  kind?: string;
  head_membership_id?: string | null;
  is_active?: boolean;
}

// ------------------------------------------------------------------ locations

export interface Location {
  id: string;
  name: string;
  code: string;
  timezone: string;
  country: string | null;
  address: Record<string, string> | null;
  is_active: boolean;
}

export interface LocationCreate {
  name: string;
  code: string;
  timezone?: string;
  country?: string;
  address?: Record<string, string>;
}

/** `LocationPatch` — `code` is immutable: it is embedded in members' `location_codes`. */
export interface LocationPatch {
  name?: string;
  timezone?: string;
  country?: string;
  address?: Record<string, string>;
  is_active?: boolean;
}

// ------------------------------------------------------------------ members

/** `MemberOut` — a membership, plus the parts of the person a colleague may see. */
export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  display_name: string | null;
  job_title: string | null;
  status: string;
  is_owner: boolean;
  org_unit_id: string | null;
  manager_membership_id: string | null;
  location_codes: string[];
  employee_id: string | null;
  roles: string[];
  joined_at: string | null;
  started_on: string | null;
}

/** `MemberPatch` — send only the fields that changed. `employee_id` is never sent from here. */
export interface MemberPatch {
  display_name?: string;
  job_title?: string;
  org_unit_id?: string | null;
  manager_membership_id?: string | null;
  location_codes?: string[];
  started_on?: string | null;
}

// ------------------------------------------------------------------ invitations

/** `InvitationOut`. */
export interface Invitation {
  id: string;
  email: string;
  role_codes: string[];
  product_codes: string[];
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** `InvitationCreated` — `InvitationOut` plus the raw token, returned exactly once. */
export interface InvitationCreated extends Invitation {
  token: string;
}

export interface InvitationCreate {
  email: string;
  role_codes: string[];
  product_codes: string[];
  expires_in_days: number;
}

// ------------------------------------------------------------------ roles and permissions

export type Scope = "OWN" | "TEAM" | "LOCATION" | "ORG";

/** `GrantOut` / `GrantIn`. */
export interface Grant {
  permission_code: string;
  scope: Scope;
}

/** `RoleOut`. */
export interface Role {
  id: string;
  code: string;
  name: string;
  description: string | null;
  product: string | null;
  is_default: boolean;
  is_system: boolean;
  grants: Grant[];
}

/** `RoleIn` — `code` and `name` are required by the schema on every write, even a grants-only edit. */
export interface RoleWrite {
  code: string;
  name: string;
  description?: string | null;
  product?: string | null;
  grants: Grant[];
}

/** `PermissionOut` — the read-only catalogue the picker is built from. */
export interface Permission {
  code: string;
  product: string;
  module: string;
  resource: string;
  action: string;
  description: string | null;
  default_scope: Scope;
  max_scope: Scope;
  is_dangerous: boolean;
}

// ------------------------------------------------------------------ email domains (SSO)

/**
 * `DomainOut`. `verification_host`/`verification_value` are what to publish as a DNS TXT
 * record; `expires_at` is `null` once `verified_at` is set — the claim clock stops being read
 * at all past that point.
 */
export interface Domain {
  id: string;
  domain: string;
  is_primary: boolean;
  auto_join: boolean;
  verified_at: string | null;
  created_at: string;
  expires_at: string | null;
  verification_host: string;
  verification_value: string;
  /** The periodic re-check's own findings. All three `null` means "never re-checked yet" —
   * never an enforcement signal, since sign-in still gates on `verified_at` alone. */
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  /** Set once a re-check first finds the record missing after previously finding it; cleared
   * the moment it is found again. Never means the domain was silently unverified. */
  failing_since: string | null;
}

/** `DomainIn`. */
export interface DomainCreate {
  domain: string;
}

// ------------------------------------------------------------------ the owner/executive home
//
// The Home screen is a cross-product fan-out: every type below mirrors one other product's own
// OpenAPI contract, field for field, the same way the types above mirror account-service's. Home
// never duplicates a product's business logic — it only reads what that product's own service
// already computed.

/**
 * `MonthlyRevenuePoint` (`services/finance/app/schemas.py`) — one point of finance's real
 * 12-month history. Never a sample: `revenue_minor`/`received_minor`/`outstanding_minor` are all
 * derived from this tenant's own invoices, payments and allocations.
 */
export interface MonthlyRevenuePoint {
  month: string;
  label: string;
  revenue_minor: number;
  received_minor: number;
  outstanding_minor: number;
}

/**
 * `CurrencyRevenueHistory` — a real revenue/receivables history, scoped to one currency. A
 * `*_delta_pct` is `null`, not `0`, when there is no previous month to compare against; the UI
 * must omit the delta rather than render a manufactured percentage.
 */
export interface CurrencyRevenueHistory {
  currency: string;
  revenue_current_minor: number;
  revenue_previous_minor: number;
  revenue_delta_pct: number | null;
  received_current_minor: number;
  received_previous_minor: number;
  received_delta_pct: number | null;
  outstanding_current_minor: number;
  outstanding_previous_minor: number;
  outstanding_delta_pct: number | null;
  months: MonthlyRevenuePoint[];
}

/**
 * `OverviewSummary` (`GET /finance/overview`) — trimmed to the fields Home actually reads.
 * `kpis`/`cashflow`/`cashflow_sample` carry finance's own sample deltas and series (the audit's
 * do-not-render list) and are deliberately not declared here: Home never reads them.
 */
export interface FinanceOverview {
  period_label: string;
  revenue_history: CurrencyRevenueHistory[];
}

/** `InvoiceOut`, trimmed to what the Recent Invoices card and the status donut read. */
export interface FinanceInvoiceSummary {
  id: string;
  number: string;
  customer_id: string;
  issued_date: string;
  status: string;
  currency: string;
  total_minor: number;
}

/** `CustomerOut` (`services/finance/app/schemas.py`) — finance's own billed party, trimmed to the
 * name a Recent Invoices row resolves `customer_id` to. Distinct from the CRM `Company` below:
 * finance bills its own customer record, never a cross-schema join to CRM's book. */
export interface FinanceCustomer {
  id: string;
  name: string;
}

/** `CustomerSummary` — one row of `GET /finance/customers`: the party, wrapped with a receivable
 * position this screen never reads. The party is nested, not flattened, the same shape the wire
 * carries it in — `product-finance`'s own `getCustomerById` reads it exactly this way. */
export interface FinanceCustomerSummary {
  customer: FinanceCustomer;
}

/** `CompanyOut` (`services/customer/app/schemas.py`), trimmed to the Total Customers count. */
export interface Company {
  id: string;
  name: string;
  status: string;
}

/** `TaskOut` (`services/projects/app/schemas.py`) — one board card, trimmed to what the Pending
 * Tasks KPI and the Tasks card read. */
export interface ProjectTask {
  id: string;
  title: string;
  stage: "backlog" | "in_progress" | "review" | "done";
  priority: "low" | "medium" | "high" | "urgent";
  due_date: string | null;
  project_code: string;
}

/** `ActivityOut` (`services/deal/app/schemas.py`) — one CRM timeline entry, trimmed to what
 * Recent Activity reads. `occurred_at` is a real timestamp, never a sample. */
export interface DealActivity {
  id: string;
  subject: string;
  occurred_at: string;
}

// ------------------------------------------------------------------ the employee workspace

/** `AttendanceOut` (`services/attendance/app/schemas.py`), trimmed to what "Attendance Today"
 * reads. A row exists only once the day has been punched — there is nothing to trim it to a
 * `null` for. */
export interface AttendanceRecord {
  id: string;
  work_date: string;
  check_in_at: string;
  check_out_at: string | null;
  status: string;
}

/** `LeaveBalanceOut` (`services/leave/app/schemas.py`) — one leave type's running balance, in
 * half-days. */
export interface LeaveBalance {
  employee_id: string;
  leave_type_id: string;
  leave_type_code: string;
  balance_half_days: number;
}

/** `AuditEntryOut` (`libs/platform-core/src/platform_core/audit_read.py`): one audit row as the
 * organization may read it. Field *names* only; no value ever leaves the service. */
export interface AuditEntry {
  id: string;
  at: string;
  service: string;
  product: string;
  /** The user id (`sub`) of the person who acted; `null` for a system action. */
  actor_id: string | null;
  /** OpsGrids staff acted on the organization's behalf. */
  by_operator: boolean;
  action: string;
  entity: string;
  entity_id: string | null;
  /** The fields this entry touched; for a reveal, the fields that were opened. */
  fields: string[];
  /** A personal-data reveal (`entity.sensitive_read`). */
  sensitive: boolean;
  reason: string | null;
}

/** `AuditPageOut`: one source's page, newest first. */
export interface AuditPage {
  service: string;
  product: string;
  items: AuditEntry[];
  next_cursor: string | null;
}

export interface AuditQuery {
  /** Inclusive lower bound, an ISO instant with an offset. */
  from?: string | undefined;
  /** Exclusive upper bound, an ISO instant with an offset. */
  to?: string | undefined;
  actor_id?: string | undefined;
  action?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}
