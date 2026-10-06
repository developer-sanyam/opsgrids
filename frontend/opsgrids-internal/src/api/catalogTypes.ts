/**
 * Wire types for catalog's operator surface (`/api/v1/admin/catalog`), field for field from
 * `services/catalog/app/schemas.py` (`libs/contracts/openapi/catalog.json`). Kept apart from the
 * client functions so the fixtures-mode stand-in can share them without an import cycle.
 */

export type Audience = "public" | "restricted" | "all";
export type PlanState = "active" | "retired";
export type VersionState = "draft" | "published" | "grandfathered";
export type PriceKind = "flat" | "per_seat" | "usage";
export type BillingIntervalWire = "month" | "year" | "one_time";

export interface PriceOut {
  id: string;
  kind: PriceKind;
  amount_minor: number | null;
  currency: string;
  unit: string | null;
  unit_price_micros: number | null;
  meter_key: string | null;
}

export interface FeatureOut {
  feature_key: string;
  value: unknown;
}

export interface SeatClassOut {
  code: string;
  name: string;
  price_id: string | null;
  included_count: number | null;
  can_write: boolean;
  sort_order: number;
  description: string | null;
}

export interface PlanVersionOut {
  id: string;
  plan_id: string;
  plan_name: string | null;
  product_key: string | null;
  plan_state: string | null;
  version_no: number;
  state: VersionState;
  billing_interval: BillingIntervalWire;
  currency: string;
  restricted_to_org_id: string | null;
  published_at: string | null;
  prices: PriceOut[];
  features: FeatureOut[];
  seat_classes: SeatClassOut[];
}

export interface VersionCountsOut {
  draft: number;
  published: number;
  grandfathered: number;
  bespoke: number;
}

export interface PlanListItemOut {
  id: string;
  product_key: string;
  code: string;
  name: string;
  state: PlanState;
  sort_order: number;
  created_at: string;
  counts: VersionCountsOut;
  current: PlanVersionOut | null;
  latest_version_no: number;
}

export interface PlanListOut {
  items: PlanListItemOut[];
  next_cursor: string | null;
}

export interface VersionSummaryOut {
  id: string;
  plan_id: string;
  version_no: number;
  state: VersionState;
  billing_interval: BillingIntervalWire;
  currency: string;
  restricted_to_org_id: string | null;
  published_at: string | null;
  created_at: string;
  price_count: number;
  feature_count: number;
}

export interface PlanDetailOut {
  id: string;
  product_key: string;
  code: string;
  name: string;
  state: PlanState;
  sort_order: number;
  created_at: string;
  updated_at: string;
  counts: VersionCountsOut;
  latest_version_no: number;
  versions: VersionSummaryOut[];
  next_cursor: string | null;
}

export interface DeletedVersionOut {
  id: string;
  plan_id: string;
  version_no: number;
  deleted: true;
}

export interface PriceIn {
  kind: PriceKind;
  amount_minor?: number | null;
  currency: string;
  unit?: string | null;
  unit_price_micros?: number | null;
  meter_key?: string | null;
}

export interface FeatureIn {
  feature_key: string;
  value: unknown;
}

export interface SeatClassIn {
  code: string;
  name: string;
  price_index: number | null;
  included_count: number | null;
  can_write: boolean;
  sort_order: number;
  description: string | null;
}

/** `AdminVersionCreateIn` — always the whole sheet. */
export interface VersionCreateRequest {
  billing_interval: BillingIntervalWire;
  currency: string;
  restricted_to_org_id: string | null;
  prices: PriceIn[];
  features: FeatureIn[];
  seat_classes: SeatClassIn[];
  reason: string;
}

export interface PlanCreateRequest {
  product_key: string;
  code: string;
  name: string;
  sort_order: number;
  reason: string;
}

export interface ListPlansParams {
  productKey?: string | undefined;
  audience?: Audience | undefined;
  state?: PlanState | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}

/** One field-level refusal from the problem document's `errors[]`. */
export interface ProblemFieldError {
  field: string;
  code: string;
  message: string;
}
