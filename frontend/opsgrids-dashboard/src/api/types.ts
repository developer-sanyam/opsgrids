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
  currency: string;
  /** What this subscription switches on — the product itself, or a bundle's members. */
  entitled_products: string[];
  items: SubscriptionItem[];
  dunning: Dunning | null;
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
