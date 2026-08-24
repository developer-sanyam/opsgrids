/**
 * Wire types for the Operations product — procurement, inventory and suppliers.
 *
 * These mirror the shapes the Operations service will return once its backend graduates past a
 * skeleton (`services/operations/app/schemas.py`). Money is carried as **integer minor units**
 * (paise) with an ISO `currency`, per the platform money rule — the UI never sees a float.
 * `supplier_id` / `department`-style references are plain string ids; names are denormalised onto
 * the read models so a list renders without a cross-service join.
 */

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* ------------------------------- suppliers ------------------------------- */

export type SupplierStatus = "active" | "on_hold" | "pending";

/** A procurement category — the spend bucket a PO or supplier rolls up to. */
export type Category =
  | "raw_materials"
  | "packaging"
  | "components"
  | "mro"
  | "logistics"
  | "services";

/** Mirrors `SupplierOut`. Spend is lifetime spend in minor units of `currency`. */
export interface SupplierOut {
  id: string;
  code: string;
  name: string;
  category: Category;
  status: SupplierStatus;
  lead_time_days: number;
  /** On-time delivery rate over the trailing window, 0–100. */
  on_time_pct: number;
  spend_minor: number;
  currency: string;
  open_pos: number;
  contact_name: string;
  contact_email: string;
  location: string;
  since: string;
}

/* ---------------------------- purchase orders ---------------------------- */

export type PoStatus = "draft" | "approved" | "received" | "closed";

/** One line on a purchase order. `unit_price_minor` is per-unit minor units of the PO currency. */
export interface PoLineItem {
  id: string;
  sku: string;
  description: string;
  quantity: number;
  uom: string;
  unit_price_minor: number;
  received_qty: number;
}

/** Mirrors `PurchaseOrderOut`. The total is derived from `line_items`, never stored. */
export interface PurchaseOrderOut {
  id: string;
  po_number: string;
  supplier_id: string;
  supplier_name: string;
  category: Category;
  status: PoStatus;
  currency: string;
  warehouse: string;
  raised_by: string;
  created_date: string;
  expected_date: string;
  received_date: string | null;
  line_items: PoLineItem[];
  notes: string | null;
}

/* ------------------------------- inventory ------------------------------- */

/** Mirrors `InventoryItemOut`. Stock value = `on_hand * unit_value_minor`. */
export interface InventoryItemOut {
  id: string;
  sku: string;
  name: string;
  category: Category;
  warehouse: string;
  on_hand: number;
  reorder_point: number;
  uom: string;
  unit_value_minor: number;
  currency: string;
  updated_at: string;
}

/* --------------------------- dashboard rollups --------------------------- */

/** One category's share of committed spend — grouped live from purchase orders. */
export interface CategorySpend {
  key: Category;
  name: string;
  spend_minor: number;
  /** Share of the resolved total, 0–100. */
  pct: number;
}

/** One slice of the PO-status donut — grouped live by status. */
export interface StatusSlice {
  key: PoStatus;
  label: string;
  count: number;
  pct: number;
}

/** A point on the spend-over-time chart (representative series, minor units). */
export interface SpendPoint {
  label: string;
  spend_minor: number;
}

export type SpendRange = "monthly" | "quarterly" | "yearly";

/** One row in the "needs attention" list. */
export interface AttentionItem {
  id: string;
  kind: "low_stock" | "out_of_stock" | "late_delivery";
  title: string;
  detail: string;
  href: string;
  tone: "danger" | "late";
}
