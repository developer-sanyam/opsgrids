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
  /** The item master's own category tree (ADR-0014 Phase 1) — additive to `category` above. */
  item_category_id: string | null;
  /** An alternate reporting/purchase unit; its ratio to `uom` is a per-item conversion. */
  secondary_uom_id: string | null;
  hsn_code: string;
  track_lot: boolean;
  track_roll: boolean;
  track_expiry: boolean;
  track_quality: boolean;
  is_active: boolean;
  /** What a customer order line is priced at (ADR-0024 §3, fulfilment plan F2) — distinct from
   *  `unit_value_minor` above, which is cost. Both `null` (no selling price set yet — an order
   *  line still needs its own explicit price then) or both set. */
  selling_price_minor: number | null;
  selling_price_currency: string | null;
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
  item_category_id?: string | null;
  secondary_uom_id?: string | null;
  hsn_code?: string;
  track_lot?: boolean;
  track_roll?: boolean;
  track_expiry?: boolean;
  track_quality?: boolean;
  is_active?: boolean;
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
  item_category_id?: string | null;
  secondary_uom_id?: string | null;
  hsn_code?: string;
  track_lot?: boolean;
  track_roll?: boolean;
  track_expiry?: boolean;
  track_quality?: boolean;
  is_active?: boolean;
}

/* ---------------------------- item master (Phase 1) ----------------------- */

/** A node in the item master's own category tree — additive to the fixed `Category` enum. */
export interface ItemCategoryOut {
  id: string;
  code: string;
  name: string;
  parent_id: string | null;
}

/** `POST /inventory/categories`. */
export interface ItemCategoryIn {
  code: string;
  name: string;
  parent_id?: string | null;
}

/** `PATCH /inventory/categories/{id}`. `code` is immutable once created. */
export interface ItemCategoryPatch {
  name?: string;
  parent_id?: string | null;
}

/** One entry in the org's UOM directory. `is_system` marks a unit seeded from the fixed
 *  registry; both are equally usable as an item's `uom`. */
export interface UomOut {
  id: string;
  code: string;
  name: string;
  dimension: string;
  is_discrete: boolean;
  is_system: boolean;
}

export type ItemAttributeType = "text" | "number" | "boolean" | "date";

/** One typed key/value fact about an item (GSM, width, shade, …). Exactly one of the four
 *  `value_*` fields is non-null, matching `value_type`. */
export interface ItemAttributeOut {
  id: string;
  key: string;
  value_type: ItemAttributeType;
  value_text: string | null;
  value_number: number | null;
  value_boolean: boolean | null;
  value_date: string | null;
}

/** `PUT /inventory/{id}/attributes/{key}`. Typed values only: set exactly the one `value_*`
 *  field matching `value_type`, and no other — the API rejects anything else. */
export interface ItemAttributeIn {
  value_type: ItemAttributeType;
  value_text?: string | null;
  value_number?: number | null;
  value_boolean?: boolean | null;
  value_date?: string | null;
}

/** A purchasable/stockable variant of an item master row — its own SKU. */
export interface ItemVariantOut {
  id: string;
  inventory_item_id: string;
  sku: string;
  name: string;
  is_active: boolean;
}

/** `POST /inventory/{id}/variants`. */
export interface ItemVariantIn {
  sku: string;
  name: string;
}

/** `PATCH /inventory/{id}/variants/{id}`. `sku` is immutable once created. */
export interface ItemVariantPatch {
  name?: string;
  is_active?: boolean;
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

/* -------------------------------- locations -------------------------------- */

/**
 * The full `Location.kind` vocabulary (Fulfilment plan F1, ADR-0024 §5). Not every kind nests
 * the same way — `zone` sits at the top of a warehouse's tree; `staging`, `pack_station`, `dock`,
 * `receiving`, `returns` and `quarantine` are also usually top-level, functional areas rather than
 * storage; `aisle`, `rack`, `shelf` and `bin` usually nest under a `zone`. The server does not
 * enforce a parent/child shape beyond the tree itself, so this UI does not either.
 */
export type LocationKind =
  | "zone"
  | "aisle"
  | "rack"
  | "shelf"
  | "bin"
  | "staging"
  | "pack_station"
  | "dock"
  | "receiving"
  | "returns"
  | "quarantine";

/**
 * A node in a warehouse's location tree — mirrors `LocationOut` (`services/operations/app/schemas.py`).
 * `barcode` is unique per org where set; `pick_sequence` orders a picker's walk through a zone.
 * The three capacity fields (`max_weight_grams`, `max_volume_milli`, `storage_type`) and `kind`
 * are guarded by `operations.inventory.layout.publish` once a location has stock history, on top
 * of the ordinary `operations.inventory.location.write`.
 */
export interface LocationOut {
  id: string;
  warehouse_id: string;
  parent_id: string | null;
  kind: LocationKind;
  code: string;
  name: string;
  is_default: boolean;
  barcode: string | null;
  pick_sequence: number | null;
  max_weight_grams: number | null;
  max_volume_milli: number | null;
  storage_type: string | null;
}

/** `POST /warehouses/{warehouse_id}/locations` — `operations.inventory.location.write`. 409 on a
 *  duplicate code (per warehouse) or a duplicate barcode (per org). */
export interface LocationIn {
  kind: LocationKind;
  code: string;
  name: string;
  parent_id?: string | null;
  barcode?: string | null;
  pick_sequence?: number | null;
  max_weight_grams?: number | null;
  max_volume_milli?: number | null;
  storage_type?: string | null;
}

/** `PATCH /locations/{id}`. Changing `kind` or a capacity field on a location that already has
 *  stock history needs `operations.inventory.layout.publish`, not just `.write` — the API answers
 *  403 rather than silently accepting the base-write scope for those fields. */
export interface LocationPatch {
  kind?: LocationKind;
  code?: string;
  name?: string;
  barcode?: string | null;
  pick_sequence?: number | null;
  max_weight_grams?: number | null;
  max_volume_milli?: number | null;
  storage_type?: string | null;
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

/** A point on the spend-over-time chart, in minor units. Real committed spend by month. */
export interface SpendPoint {
  label: string;
  spend_minor: number;
}

/** One supplier's share of lifetime spend — the top-suppliers breakdown on the overview. */
export interface SupplierSpend {
  id: string;
  name: string;
  category: Category;
  spend_minor: number;
  /** Share of total supplier spend, 0–100. */
  pct: number;
}

/** One row in the "needs attention" list. */
export interface AttentionItem {
  id: string;
  kind: "low_stock" | "out_of_stock" | "late_delivery";
  title: string;
  detail: string;
  href: string;
  tone: "danger" | "late";
}

/**
 * `GET /operations/overview` — the procurement & inventory rollup, computed server-side from
 * every purchase order, inventory item and supplier row for this organization (never capped to a
 * single fetched page). `on_time_pct` is `null` until this organization has received its first
 * purchase order — a real absence of data, not a manufactured 0%.
 */
export interface OverviewOut {
  open_pos: number;
  inventory_value_minor: number;
  low_stock_count: number;
  out_of_stock_count: number;
  on_time_pct: number | null;
  currency: string;
  spend_trend: SpendPoint[];
  spend_by_category: CategorySpend[];
  po_status_mix: StatusSlice[];
  top_suppliers: SupplierSpend[];
  attention: AttentionItem[];
}

/* ---------------------------------- orders (Fulfilment F2, ADR-0024/0025) ---------------------------------- */
/**
 * Mirrors `services/operations/app/schemas.py`'s order section exactly (confirmed against the
 * landed backend, not the plan doc alone): `OrderChannel`, `OrderStatus`, `OrderLineItem`,
 * `OrderLineIn`, `OrderOut`, `OrderIn`, `OrderVersionIn`/`OrderHoldIn`/`OrderCancelIn`, `QuoteIn`/
 * `QuoteOut` and their line shapes. Every mutating call below carries the order's current
 * `version` — a 409 on a stale one is ordinary optimistic-lock traffic, not a fault.
 */

export type OrderChannel = "phone" | "manual" | "sales" | "walk_in";

/** The order state machine's full vocabulary (ADR-0025 §1) — build a status filter or badge set
 *  from exactly this list, never a subset invented for one screen. */
export type OrderStatus =
  | "DRAFT"
  | "PLACED"
  | "ACCEPTED"
  | "IN_FULFILMENT"
  | "ON_HOLD"
  | "READY"
  | "HANDED_OVER"
  | "DELIVERED"
  | "COMPLETED"
  | "CANCELLED"
  | "REJECTED";

/** One line on an order. `selling_price_minor`/`line_total_minor` are `null` until `place` prices
 *  the line — NOT `null` on any line of a `PLACED` order or later. */
export interface OrderLineItem {
  id: string;
  inventory_item_id: string;
  sku: string;
  quantity: Qty;
  selling_price_minor: number | null;
  line_total_minor: number | null;
}

/** One line on `OrderIn`/`QuoteIn`. `selling_price_minor`, when given, is this line's own price
 *  and is never overwritten by the item's current price at `place`; when omitted, `place` fills it
 *  from the item, and refuses the whole order if the item has no selling price either. */
export interface OrderLineIn {
  inventory_item_id: string;
  quantity: Qty;
  selling_price_minor?: number;
}

/** Mirrors `OrderOut`. `total_minor` equals `subtotal_minor` — F2 carries no discount, tax or
 *  shipping line. Both are `0` until `place` prices the lines. */
export interface OrderOut {
  id: string;
  number: string;
  channel: OrderChannel;
  status: OrderStatus;
  /** The optimistic-lock version. Every mutating call below must send the value this order was
   *  last read at; a mismatch is a 409 naming the order's current version. */
  version: number;
  warehouse_id: string;
  customer_id: string | null;
  customer_name: string;
  customer_phone: string;
  subtotal_minor: number;
  total_minor: number;
  currency: string;
  /** Billing-gate facts (ADR-0025 §1). Optional on the wire type because fixture orders omit
   *  them; the live API always sends them. `paid_at` is set by a counter/carrier attestation or
   *  — for an order paid online — only by a provider-verified capture. */
  invoice_id?: string | null;
  paid_at?: string | null;
  /** The online payment whose verified capture set `paid_at`, when it was paid online. */
  paid_via_charge_id?: string | null;
  pickup_code_verified_at?: string | null;
  lines: OrderLineItem[];
  created_at: string;
  updated_at: string;
}

/* ------------------------------ online payment (ADR-0024 A4, Fulfilment F10) ------------------------------ */

/** One online payment attempt's state, `services/payment`'s vocabulary. "Pending" is `created`;
 *  `authorized` is money held with capture on its way; only `captured` is paid. */
export type CollectionState = "created" | "authorized" | "captured" | "failed" | "cancelled" | "expired";

export interface CollectionRefundOut {
  id: string;
  provider_refund_id: string | null;
  amount_minor: number;
  currency: string;
  /** `created` (pending at the provider) | `processed` | `failed`. */
  state: string;
  reason: string | null;
  created_at: string;
}

export interface CollectionHistoryOut {
  from_state: string | null;
  to_state: string;
  /** What moved it: `api` | `webhook` | `sync` | `sweep`. */
  source: string;
  reason: string | null;
  at: string;
}

export interface OrderCollectionOut {
  id: string;
  state: CollectionState;
  amount_minor: number;
  currency: string;
  provider: string;
  provider_order_id: string | null;
  provider_payment_id: string | null;
  failure_reason: string | null;
  refunded_minor: number;
  refunds: CollectionRefundOut[];
  history: CollectionHistoryOut[];
  created_at: string;
  updated_at: string;
}

/** `GET /orders/{id}/payments`. */
export interface OrderPaymentsOut {
  order_id: string;
  total_minor: number;
  currency: string;
  paid_at: string | null;
  paid_via_charge_id: string | null;
  payment_term: string;
  can_collect: boolean;
  collect_blocked_reason: string | null;
  online_payments_configured: boolean;
  mode: "test" | "live" | null;
  /** `stub`: no provider call leaves the platform — a checkout cannot be paid for real. */
  wire: "stub" | "razorpay";
  collections: OrderCollectionOut[];
}

/** What the browser needs to open Razorpay's hosted Checkout — the **public** key id and the
 *  provider Order id. There is no secret here, and there never will be. */
export interface OrderCheckoutSessionOut {
  provider: string;
  key_id: string;
  provider_order_id: string;
  amount_minor: number;
  currency: string;
  mode: "test" | "live";
  openable: boolean;
  order_number: string;
  prefill: { name: string; contact: string };
}

export interface OrderCheckoutOut {
  collection: OrderCollectionOut;
  checkout: OrderCheckoutSessionOut | null;
}

export interface OrderRefundIn {
  amount_minor: number;
  reason: string;
}

/** `GET/PUT /payment/collections/merchant-account` — the organization's own Razorpay account. */
export interface MerchantAccountOut {
  configured: boolean;
  id?: string | null;
  provider?: string | null;
  key_id?: string | null;
  mode?: "test" | "live" | null;
  key_secret_masked?: string | null;
  webhook_secret_masked?: string | null;
  /** Path (with `?account=`) to register as the webhook URL in the Razorpay dashboard. */
  webhook_path?: string | null;
  webhook_events: string[];
  rotated_at?: string | null;
  created_at?: string | null;
  live_keys_allowed: boolean;
  wire: "stub" | "razorpay";
}

export interface MerchantAccountIn {
  provider: "razorpay";
  key_id: string;
  key_secret: string;
  webhook_secret: string;
}

export interface ReconciliationRowOut {
  charge_id: string;
  reference_type: string | null;
  reference_id: string | null;
  state: string;
  amount_minor: number;
  currency: string;
  provider_order_id: string | null;
  provider_payment_id: string | null;
  refunded_minor: number;
  provider_status: string | null;
  mismatches: string[];
  lookup_failed: boolean;
}

export interface ReconciliationOut {
  generated_at: string;
  checked: number;
  mismatched: number;
  unchecked: number;
  rows: ReconciliationRowOut[];
}

/** `POST /orders`. Creates a `DRAFT`. At least one of `customer_id` or a `customer_name`/
 *  `customer_phone` pair is required — the server 422s an order for nobody. */
export interface OrderIn {
  channel: OrderChannel;
  warehouse_id: string;
  customer_id?: string | null;
  customer_name?: string;
  customer_phone?: string;
  currency?: string;
  lines: OrderLineIn[];
}

/** The optimistic-lock envelope every order transition takes. */
export interface OrderVersionIn {
  version: number;
}

/** `POST /orders/{id}/hold`. A reason is required. */
export interface OrderHoldIn extends OrderVersionIn {
  reason: string;
}

/** `POST /orders/{id}/cancel`. A reason is required, recorded on the transition row. */
export interface OrderCancelIn extends OrderVersionIn {
  reason: string;
}

/** One line of `POST /orders/quote` — same shape as `OrderLineIn`. */
export interface QuoteLineIn {
  inventory_item_id: string;
  quantity: Qty;
  selling_price_minor?: number;
}

/** `POST /orders/quote`. Read-only — posts nothing, creates no order. */
export interface QuoteIn {
  warehouse_id: string;
  currency?: string;
  lines: QuoteLineIn[];
}

/** One priced (or unpriceable) line of a quote preview, with its availability at the requested
 *  warehouse. `priced` is `false` when the item has no selling price and the line named none
 *  either — the same refusal `place` would give, surfaced here instead so a preview never 422s. */
export interface QuoteLineOut {
  inventory_item_id: string;
  sku: string;
  quantity: Qty;
  priced: boolean;
  selling_price_minor: number | null;
  line_total_minor: number | null;
  available_qty: Qty;
  sufficient: boolean;
}

/** What `POST /orders/quote` answers with. `fully_priced`/`fully_available` are `false` when any
 *  line couldn't be priced, or is short — never a 422; a quote is a preview, not a refusal. */
export interface QuoteOut {
  warehouse_id: string;
  currency: string;
  subtotal_minor: number;
  total_minor: number;
  fully_priced: boolean;
  fully_available: boolean;
  lines: QuoteLineOut[];
}

/* --------------------------- fulfilment (Fulfilment F3, ADR-0024 §7, ADR-0025 §2/§3) -------------------------- */
/**
 * Mirrors `services/operations/app/schemas.py`'s fulfilment section exactly. `product-operations`
 * is the dispatcher's surface — the worker PWA (`frontend/worker-app`) is a separate app and is
 * not what these types serve; only the shapes the dispatcher board and the worker roster actually
 * render or submit are declared here.
 */

export type PresenceStatus = "online" | "offline";

/** Mirrors `WorkerOut`. `zone_location_ids` is `null` unless the caller also holds
 *  `operations.fulfilment.worker.location_read` — progressive disclosure, not always-present data. */
export interface WorkerOut {
  id: string;
  employee_id: string;
  warehouse_id: string;
  display_name: string;
  is_active: boolean;
  skills: string[];
  presence_status: PresenceStatus;
  presence_last_seen_at: string | null;
  zone_location_ids: string[] | null;
  created_at: string;
  updated_at: string;
}

/** `POST /operations/fulfilment/workers` — `operations.fulfilment.worker.write`, ORG scope. */
export interface WorkerIn {
  employee_id: string;
  warehouse_id: string;
  display_name?: string;
  is_active?: boolean;
  skills?: string[];
  zone_location_ids?: string[];
}

/** `PATCH /operations/fulfilment/workers/{id}`. `skills`/`zone_location_ids`, when given,
 *  **replace** the whole set. */
export interface WorkerPatch {
  warehouse_id?: string;
  display_name?: string;
  is_active?: boolean;
  skills?: string[];
  zone_location_ids?: string[];
}

/** ADR-0025 §2's exact vocabulary. `CLOSED` and `CANCELLED` are terminal. */
export type PickTaskStatus =
  | "CREATED"
  | "OFFERED"
  | "ESCALATED"
  | "ACCEPTED"
  | "IN_PROGRESS"
  | "EXCEPTION"
  | "PICKED"
  | "CLOSED"
  | "CANCELLED";

/** ADR-0025 §3's exact vocabulary for `assignment_offers.state`. */
export type OfferState = "OFFERED" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "WITHDRAWN" | "RELEASED";

export type PickTaskLineStatus = "pending" | "partial" | "picked" | "short";

/** One order line's worth of picking inside a task. */
export interface PickTaskLineOut {
  id: string;
  order_line_id: string;
  inventory_item_id: string;
  sku: string;
  source_location_id: string;
  destination_location_id: string;
  quantity: Qty;
  picked: Qty;
  short: Qty;
  sequence: number | null;
  status: PickTaskLineStatus;
}

/** Mirrors `assignment_offers` (ADR-0025 §3's exact row shape) — one row per offer, kept as
 *  history rather than summarised away, so "why did this take forty minutes" stays answerable. */
export interface AssignmentOfferOut {
  id: string;
  task_id: string;
  worker_id: string;
  round: number;
  state: OfferState;
  score_breakdown: Record<string, unknown>;
  offered_at: string;
  expires_at: string;
  responded_at: string | null;
}

export type PickExceptionReason = "short_pick" | "damaged" | "not_found" | "other";

export interface PickExceptionOut {
  id: string;
  task_id: string;
  task_line_id: string;
  reason: PickExceptionReason;
  quantity: Qty;
  note: string;
  status: "open" | "resolved";
  raised_at: string;
  resolved_at: string | null;
}

/** Mirrors `PickTaskOut` — the dispatcher board's one row per task, with its lines, its whole
 *  offer history and any open exceptions embedded, so the board renders from one list call with
 *  no per-row follow-up request (N+1 avoidance). */
export interface PickTaskOut {
  id: string;
  order_id: string;
  warehouse_id: string;
  zone_location_id: string | null;
  status: PickTaskStatus;
  round: number;
  max_rounds: number;
  lines: PickTaskLineOut[];
  offers: AssignmentOfferOut[];
  exceptions: PickExceptionOut[];
  /** Set when auto-assignment ran and found no eligible worker (e.g. "No eligible worker online
   *  for zone DOCK-A") — `null` otherwise, including whenever a live offer exists (auto or
   *  manual). The board's own visible reason to offer this task by hand. */
  auto_assign_failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /operations/fulfilment/tasks/{id}/offers` — a dispatcher offering a task to one worker.
 *  Also the manual-override path when auto-assignment already made a live, not-yet-accepted
 *  offer: a caller holding `operations.fulfilment.task.override` withdraws it and offers to
 *  whoever this call names instead. */
export interface OfferIn {
  worker_id: string;
}

/** `POST /operations/fulfilment/tasks/{id}/cancel`. In-flight tasks (`ACCEPTED`, `IN_PROGRESS`,
 *  `EXCEPTION`) additionally need `operations.fulfilment.task.override`. */
export interface TaskCancelIn {
  reason: string;
}

/* --------------------------------- dispatch (Fulfilment F5, ADR-0024 §6) --------------------------------- */
/**
 * Mirrors `services/operations/app/schemas.py`'s dispatch section exactly. The carrier handover —
 * the delivery/courier counterpart to `POST /orders/{id}/hand-over` — and a shipment's own
 * lifecycle after that (never a second write to the order's own status, which already reaches
 * `DELIVERED` the moment the shipment is created).
 */

/** The carrier lifecycle (`app.domain.SHIPMENT_TRANSITIONS`). `DELIVERED` is reached only from
 *  `IN_TRANSIT` — build a status filter or badge set from exactly this list. */
export type ShipmentStatus = "PICKUP_HANDOVER" | "IN_TRANSIT" | "DELIVERED" | "FAILED" | "RETURNED";

/** Mirrors `ShipmentOut`. */
export interface ShipmentOut {
  id: string;
  order_id: string;
  warehouse_id: string;
  status: ShipmentStatus;
  carrier_name: string;
  tracking_reference: string | null;
  failure_reason: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /dispatch/shipments` — the carrier handover: creates the shipment, already at
 *  `PICKUP_HANDOVER`, and moves the order `READY -> HANDED_OVER -> DELIVERED` in the same call
 *  once the billing gate passes. `carrier_name`/`tracking_reference` are manual text only. */
export interface ShipmentIn {
  order_id: string;
  order_version: number;
  carrier_name: string;
  tracking_reference?: string;
}

/** `POST /dispatch/shipments/{id}/in-transit` and `.../deliver`. No reason needed for a
 *  forward-moving update; `tracking_reference` may be set or corrected here. */
export interface ShipmentAdvanceIn {
  tracking_reference?: string;
}

/** `POST /dispatch/shipments/{id}/fail` and `.../return`. A reason is required. */
export interface ShipmentRefusalIn {
  reason: string;
}

/* -------------------------------- warehouse geometry (Fulfilment plan §2/F7) -------------------------------- */
/**
 * Floors, versioned layouts, a draft's placements and walk graph, validation, publish/rollback,
 * and routing over a floor's published layout — `app.warehouse`
 * (`services/operations/app/api/routes.py`). A `LayoutVersion` is `draft -> published ->
 * archived -> published` (a rollback is `POST .../publish` called again on an `archived`
 * version, never a distinct action). Coordinates are integer millimetres throughout, mirroring
 * the backend's no-float rule.
 */

export type LayoutVersionStatus = "draft" | "published" | "archived";

export interface WarehouseFloorOut {
  id: string;
  warehouse_id: string;
  floor_number: number;
  name: string;
}

/** `POST /warehouses/{warehouse_id}/floors` — `operations.warehouse.floor.write`. 409 on a
 *  duplicate `floor_number` within the warehouse. */
export interface WarehouseFloorIn {
  floor_number: number;
  name?: string;
}

/** `PATCH /warehouse-floors/{id}` — `operations.warehouse.floor.write`. */
export interface WarehouseFloorPatch {
  floor_number?: number;
  name?: string;
}

export interface LayoutVersionOut {
  id: string;
  warehouse_floor_id: string;
  version_number: number;
  status: LayoutVersionStatus;
  label: string;
  published_at: string | null;
  published_by: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /warehouse-floors/{floor_id}/layout-versions` — `operations.warehouse.layout.write`.
 *  409 while a draft is already open for this floor. `clone_from_published` (default `true`)
 *  starts the new draft from a copy of the floor's current published geometry. */
export interface LayoutVersionCreateIn {
  label?: string;
  clone_from_published?: boolean;
}

/** One entry of `LayoutGeometryIn.placements` — where `location_id` sits on this floor, in this
 *  draft version only (never leaks into what is published until it is). */
export interface LocationPlacementIn {
  location_id: string;
  x_mm: number;
  y_mm: number;
  z_mm?: number | null;
  width_mm?: number;
  depth_mm?: number;
  height_mm?: number | null;
}

/** Mirrors `LocationPlacementIn`, plus the location's own `code`/`kind` so a canvas can render a
 *  placement without a second lookup. */
export interface LocationPlacementOut {
  location_id: string;
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
  width_mm: number;
  depth_mm: number;
  height_mm: number | null;
  location_code: string;
  location_kind: LocationKind;
}

/** One entry of `LayoutGeometryIn.walk_nodes`. `code` is this draft's own reference for the
 *  node — what a `WalkEdgeIn` names in `from_code`/`to_code` — never a database id. */
export interface WalkNodeIn {
  code: string;
  location_id?: string | null;
  x_mm: number;
  y_mm: number;
  z_mm?: number | null;
}

export interface WalkNodeOut {
  id: string;
  code: string;
  location_id: string | null;
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
}

/** One entry of `LayoutGeometryIn.walk_edges`, naming its endpoints by the `code`s in the same
 *  payload's `walk_nodes`. `bidirectional` (default `true`) makes routing treat this one row as
 *  two directed edges; `is_traversable` (default `true`) blocks it without deleting it. */
export interface WalkEdgeIn {
  from_code: string;
  to_code: string;
  distance_mm: number;
  is_traversable?: boolean;
  bidirectional?: boolean;
}

export interface WalkEdgeOut {
  id: string;
  from_node_id: string;
  to_node_id: string;
  from_code: string;
  to_code: string;
  distance_mm: number;
  is_traversable: boolean;
  bidirectional: boolean;
}

/** `PUT /layout-versions/{id}/geometry` — the whole "save draft" payload a canvas editor posts.
 *  A **full replace**: everything the draft had is dropped and rebuilt from exactly this. 409 on
 *  anything but a `draft` version. */
export interface LayoutGeometryIn {
  placements: LocationPlacementIn[];
  walk_nodes: WalkNodeIn[];
  walk_edges: WalkEdgeIn[];
}

export interface LayoutGeometryOut {
  version: LayoutVersionOut;
  placements: LocationPlacementOut[];
  walk_nodes: WalkNodeOut[];
  walk_edges: WalkEdgeOut[];
}

/** One problem `POST /layout-versions/{id}/validate` (or a refused publish) found. */
export interface LayoutValidationIssueOut {
  kind: string;
  message: string;
  location_ids: string[];
  node_id: string | null;
}

export interface LayoutValidationOut {
  is_valid: boolean;
  issues: LayoutValidationIssueOut[];
}

/** `GET /warehouse-floors/{floor_id}/route?from_location_id=&to_location_id=` — a computed path
 *  over the floor's **published** layout only. */
export interface RouteOut {
  from_location_id: string;
  to_location_id: string;
  node_ids: string[];
  distance_mm: number;
}

/* --------------------------------- counting (ADR-0026, Fulfilment plan F8) --------------------------------- */
/**
 * Mirrors `services/operations/app/schemas.py`'s counting section exactly. This screen is the
 * manager's — opening a session, moving it to review, and approving or rejecting each
 * discrepancy. Counting a line itself is the worker PWA's job (`frontend/worker-app`), a separate
 * app, and is not what these types serve beyond what the review screen needs to render.
 */

export type CountScopeKind = "WAREHOUSE" | "ZONE" | "LOCATIONS";
export type CountSessionStatus = "ACTIVE" | "REVIEW" | "CLOSED" | "CANCELLED";
export type CountLineReviewStatus = "NONE" | "PENDING" | "APPROVED" | "REJECTED";
export type CountMethod = "scan" | "manual";

/** Mirrors `CountSessionOut`. `line_count`/`counted_count`/`pending_review_count` are computed
 *  read-model rollups (never stored), the same "derived, not cached" shape `Supplier.open_pos`
 *  and this build's other rollups already use. */
export interface CountSessionOut {
  id: string;
  warehouse_id: string;
  scope_kind: CountScopeKind;
  zone_location_id: string | null;
  location_ids: string[] | null;
  status: CountSessionStatus;
  label: string;
  line_count: number;
  counted_count: number;
  pending_review_count: number;
  created_at: string;
  updated_at: string;
}

/** `POST /counting/sessions` — refused (409) while the warehouse already has a session `ACTIVE`
 *  or under `REVIEW` (ADR-0026 §5, one session per warehouse at a time). */
export interface CountSessionIn {
  warehouse_id: string;
  scope_kind: CountScopeKind;
  zone_location_id?: string;
  location_ids?: string[];
  label?: string;
}

/** `POST /counting/sessions/{id}/cancel`. */
export interface CountSessionCancelIn {
  reason?: string;
}

/** Mirrors `CountLineOut`. `expected` is the open-time snapshot; a live balance that moved since
 *  is not re-fetched here — `app.inventory.ledger.post_move` recomputes the real delta from the
 *  live figure at approval time regardless (ADR-0026 §6). */
export interface CountLineOut {
  id: string;
  session_id: string;
  inventory_item_id: string;
  sku: string;
  location_id: string;
  status: string;
  lot_id: string | null;
  roll_id: string | null;
  is_unlisted: boolean;
  expected: Qty;
  counted: Qty | null;
  count_method: CountMethod | null;
  scanned_barcode: string | null;
  counted_by: string | null;
  counted_at: string | null;
  review_status: CountLineReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string;
  posted_move_id: string | null;
  created_at: string;
}

/** `POST /counting/sessions/{id}/lines/{line_id}/review` — approving posts through the kernel in
 *  the same call; rejecting needs a note and never touches the ledger. */
export interface CountLineReviewIn {
  decision: "approve" | "reject";
  note?: string;
}

/* ------------------------- operational alerts and insights (F12) ------------------------- */

export type AlertKind = "low_stock" | "task_delayed" | "shipment_delayed";
export type AlertSubjectType = "inventory_item" | "pick_task" | "shipment";

/** Mirrors `OperationalAlertOut`. `detail` is exactly what the alert's own domain event
 *  carried — enough to render the row without a further fetch. */
export interface OperationalAlertOut {
  id: string;
  kind: AlertKind;
  subject_type: AlertSubjectType;
  subject_id: string;
  warehouse_id: string | null;
  detail: Record<string, unknown>;
  detected_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

/** Open pick-task count and age per warehouse — the backlog half of the backlog/throughput
 *  insight view. */
export interface TaskBacklogOut {
  warehouse_id: string;
  open_tasks: number;
  avg_age_minutes: number;
  max_age_minutes: number;
}

/** Tasks closed in the reporting window, and their average cycle time — the throughput half. */
export interface ThroughputOut {
  warehouse_id: string;
  closed_tasks: number;
  avg_cycle_minutes: number;
}

/** Which zone's tasks took longest to close, most recently — slowest first. */
export interface ZoneBottleneckOut {
  zone_location_id: string;
  closed_tasks: number;
  avg_cycle_minutes: number;
}

/** Open task count per worker, busiest first. */
export interface WorkerWorkloadOut {
  worker_id: string;
  worker_name: string;
  open_tasks: number;
}

/** One day's count of a discrepancy kind — the reconciler's own findings, never a synthetic
 *  series. */
export interface DiscrepancyTrendPointOut {
  date: string;
  kind: "balance" | "reserved";
  count: number;
}

/* --------------------------------- channels (Fulfilment F11) --------------------------------- */
/**
 * External sales-channel connections — a marketplace/storefront platform this business also
 * sells on. Mirrors `services/operations/app/schemas.py`'s channels section exactly. Not to be
 * confused with `OrderOut.channel` (the intake-mode string `phone | manual | sales | walk_in |
 * marketplace`): a `ChannelOut` row is the connector itself.
 */

export type ChannelProvider = "mock_marketplace";
export type ChannelStatus = "connected" | "disconnected" | "error";
export type ChannelSyncType = "order_import" | "inventory_push" | "fulfilment_push" | "health_check";
export type ChannelSyncStatus = "success" | "failed";

export interface ChannelOut {
  id: string;
  provider: ChannelProvider;
  name: string;
  status: ChannelStatus;
  external_shop_id: string;
  api_key_masked: string;
  default_warehouse_id: string | null;
  default_currency: string;
  auto_accept_orders: boolean;
  last_orders_synced_at: string | null;
  last_inventory_synced_at: string | null;
  last_health_check_at: string | null;
  consecutive_failures: number;
  last_error_code: string | null;
  last_error_message: string | null;
  connected_at: string | null;
  disconnected_at: string | null;
  created_at: string;
  updated_at: string;
}

/** `POST /operations/channels`. Only `provider: "mock_marketplace"` exists today — this build's
 *  self-hosted sandbox, in place of real Shopify/Amazon credentials this environment does not
 *  have. */
export interface ChannelConnectIn {
  provider: ChannelProvider;
  name: string;
  external_shop_id: string;
  api_key: string;
  default_warehouse_id: string;
  default_currency?: string;
  auto_accept_orders?: boolean;
}

/** `PATCH /operations/channels/{id}` — configuration only; the credential is rotated separately
 *  (`ChannelReconnectIn`). */
export interface ChannelConfigPatch {
  name?: string;
  default_warehouse_id?: string;
  auto_accept_orders?: boolean;
}

/** `POST /operations/channels/{id}/reconnect`. */
export interface ChannelReconnectIn {
  api_key: string;
}

export interface ChannelSkuMappingOut {
  id: string;
  channel_id: string;
  external_sku: string;
  inventory_item_id: string;
  last_pushed_available_milli: number | null;
  last_pushed_at: string | null;
  last_push_status: "ok" | "failed" | null;
  last_push_error: string | null;
}

export interface ChannelSkuMappingIn {
  external_sku: string;
  inventory_item_id: string;
}

export interface ChannelSyncLogOut {
  id: string;
  channel_id: string;
  sync_type: ChannelSyncType;
  status: ChannelSyncStatus;
  external_ref: string;
  detail: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  attempted_at: string;
}

/** What a manual "sync now" or "retry" action returns — real counts from the sync that just ran. */
export interface ChannelSyncResultOut {
  attempted: number;
  succeeded: number;
  rejected: number;
}

export interface ChannelRetryResultOut {
  succeeded: boolean;
}

export interface ChannelHealthCheckOut {
  healthy: boolean;
}
