/**
 * Wire types for the Operations product — procurement, inventory and suppliers.
 *
 * These mirror `services/operations/app/schemas.py` exactly: field names, nesting and enums.
 * Money is carried as **integer minor units** (paise) with an ISO `currency`, per the platform
 * money rule — the UI never sees a float. `supplier_id` / warehouse references are plain string
 * ids or names; names are denormalised onto the read models so a list renders without a
 * cross-service join.
 */

/** The envelope every list endpoint returns (`platform_core.pagination.Page`). */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
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

/** `POST /suppliers`. `spend_minor`, `open_pos` and `on_time_pct` are system-maintained and
 *  never sent. */
export interface SupplierIn {
  code: string;
  name: string;
  category: Category;
  status?: SupplierStatus;
  lead_time_days?: number;
  currency?: string;
  contact_name?: string;
  contact_email?: string;
  location?: string;
  since?: string;
}

/** `PATCH /suppliers/{id}`. No `code`/`currency` — join key and accumulated unit. Status change
 *  (`active`/`on_hold`/`pending`) goes through this. */
export interface SupplierPatch {
  name?: string;
  category?: Category;
  status?: SupplierStatus;
  lead_time_days?: number;
  contact_name?: string;
  contact_email?: string;
  location?: string;
  since?: string;
}

/* ---------------------------- purchase orders ---------------------------- */

export type PoStatus = "draft" | "approved" | "received" | "closed" | "cancelled";

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

/** One line on `PurchaseOrderIn`/`PurchaseOrderPatch.lines`. `sku` is free text — a line with no
 *  matching inventory item is a service/logistics line, not an error. */
export interface PoLineIn {
  sku: string;
  description?: string;
  quantity: number;
  uom?: string;
  unit_price_minor?: number;
}

/** `POST /purchase-orders`. `status`, `number`, `received_date` and `received_qty` are
 *  server-owned and never sent. */
export interface PurchaseOrderIn {
  supplier_id: string;
  warehouse: string;
  expected_date: string;
  currency?: string;
  notes?: string;
  raised_by?: string;
  lines: PoLineIn[];
}

/** `PATCH /purchase-orders/{id}`, draft only. `lines`, when present, is a full replacement. */
export interface PurchaseOrderPatch {
  supplier_id?: string;
  warehouse?: string;
  expected_date?: string;
  currency?: string;
  notes?: string;
  lines?: PoLineIn[];
}

/** `POST /purchase-orders/{id}/receive`. Optional body — an empty POST receives as of today. */
export interface ReceiveIn {
  received_date?: string;
}

/** `POST /purchase-orders/{id}/cancel`. */
export interface CancelIn {
  reason: string;
}

/* ------------------------------- inventory ------------------------------- */

/** An exact stock quantity: a decimal string with at most three decimals (`"82.5"`). Never
 *  parsed into a float for arithmetic — see `lib/quantity.ts` (ADR-0014 §2). */
export type Qty = string;

/** Mirrors `InventoryItemOut`. Stock is read from the ledger's balances: `on_hand` is the
 *  whole-unit figure the API has always returned; `on_hand_qty` / `reserved_qty` /
 *  `available_qty` are exact and are what the screens show. */
export interface InventoryItemOut {
  id: string;
  sku: string;
  name: string;
  category: Category;
  warehouse: string;
  on_hand: number;
  on_hand_qty: Qty;
  reserved_qty: Qty;
  available_qty: Qty;
  reorder_point: number;
  uom: string;
  unit_value_minor: number;
  currency: string;
  updated_at: string;
}

/** `POST /inventory`. `on_hand` is never accepted — `opening_on_hand` becomes the item's first
 *  `opening` movement instead. `warehouse` must name an existing warehouse. */
export interface InventoryItemIn {
  sku: string;
  name: string;
  category: Category;
  warehouse: string;
  uom: string;
  reorder_point?: number;
  unit_value_minor?: number;
  currency?: string;
  opening_on_hand?: number;
}

/** `PATCH /inventory/{id}`. `sku`, `currency` and — above all — `on_hand` are never accepted:
 *  `on_hand` may only move through `/adjustments`. */
export interface InventoryItemPatch {
  name?: string;
  category?: Category;
  warehouse?: string;
  uom?: string;
  reorder_point?: number;
  unit_value_minor?: number;
}

export type StockMovementKind =
  | "opening"
  | "receipt"
  | "adjustment"
  | "count"
  | "relocation"
  | "issue";
export type StockStatus =
  | "AVAILABLE"
  | "QUALITY_HOLD"
  | "QUARANTINE"
  | "DAMAGED"
  | "REJECTED"
  | "WIP"
  | "IN_TRANSIT"
  | "AT_JOB_WORKER"
  | "BLOCKED";
export type StockMovementReason = "damaged" | "lost" | "found" | "consumed" | "returned" | "other";

/** `POST /inventory/{id}/adjustments`. Exactly one of `quantity_delta` or `counted_on_hand`. */
export interface StockAdjustmentIn {
  quantity_delta?: number;
  counted_on_hand?: number;
  reason?: StockMovementReason;
  note?: string;
  occurred_on?: string;
}

/** One row of an item's ledger. Append-only, newest first. `quantity` / `balance_after` are
 *  exact; `quantity_delta` / `on_hand_after` are the whole-unit compatibility figures. */
export interface StockMovementOut {
  id: string;
  kind: StockMovementKind;
  reason: StockMovementReason | null;
  quantity_delta: number;
  on_hand_after: number;
  quantity: Qty;
  balance_after: Qty;
  location_id: string;
  status: StockStatus;
  reference_type: "purchase_order" | null;
  reference_id: string | null;
  note: string;
  occurred_on: string;
  created_at: string;
  created_by: string | null;
}

/* ------------------------------- warehouses ------------------------------ */

/** A stocking location. Reference data behind the warehouse names POs and inventory carry. */
export interface WarehouseOut {
  id: string;
  code: string;
  name: string;
  location: string;
}

/** `POST /warehouses`. `code` is immutable once created. */
export interface WarehouseIn {
  code: string;
  name: string;
  location?: string;
}

/** `PATCH /warehouses/{id}`. `code` never appears — it is immutable. */
export interface WarehousePatch {
  name?: string;
  location?: string;
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
