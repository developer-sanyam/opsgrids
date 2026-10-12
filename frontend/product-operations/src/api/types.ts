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

export type PoStatus =
  | "draft"
  | "approved"
  | "partially_received"
  | "received"
  | "closed"
  | "cancelled";

/** One line on a purchase order. `unit_price_minor` is per-unit minor units of the PO currency. */
export interface PoLineItem {
  id: string;
  sku: string;
  description: string;
  quantity: number;
  uom: string;
  unit_price_minor: number;
  received_qty: number;
  /** The exact cumulative receipt in the item's stock unit (`received_qty` is its whole-unit view). */
  received_exact: Qty;
  /** The buyer closed this line short: nothing more is expected against it. */
  short_closed: boolean;
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
  /** How far over the ordered quantity a receipt may go, in percent (`"2.5"`) — two decimals at most. */
  receipt_tolerance_pct: string;
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
  receipt_tolerance_pct?: string;
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
  receipt_tolerance_pct?: string;
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
  /** A free vocabulary (`purchase_order`, `bin_transfer`, `putaway`, ...), not a single literal. */
  reference_type: string | null;
  reference_id: string | null;
  note: string;
  occurred_on: string;
  created_at: string;
  created_by: string | null;
}

/* ------------------------------- warehouses ------------------------------ */

/**
 * A stocking location. Reference data behind the warehouse names POs and inventory carry.
 *
 * `lat_micro` / `lng_micro` are the *site's* position as integer micro-degrees (degrees x 10^6),
 * given together or not at all. Optional on the type because the shape is constructed as a literal
 * in many places; the server always sends both, `null` until a position is set.
 */
export interface WarehouseOut {
  id: string;
  code: string;
  name: string;
  location: string;
  lat_micro?: number | null;
  lng_micro?: number | null;
}

/** `POST /warehouses`. `code` is immutable once created. The two coordinates are paired. */
export interface WarehouseIn {
  code: string;
  name: string;
  location?: string;
  lat_micro?: number | null;
  lng_micro?: number | null;
}

/** `PATCH /warehouses/{id}`. `code` never appears — it is immutable. The server checks the
 *  *merged* state of the coordinates; an explicit `null` for both clears the position. */
export interface WarehousePatch {
  name?: string;
  location?: string;
  lat_micro?: number | null;
  lng_micro?: number | null;
}

/** One warehouse with a position on `GET /operations/warehouses/map`. These are *site*
 *  coordinates (approved for display), never a worker's. */
export interface WarehouseMapSiteOut {
  id: string;
  code: string;
  name: string;
  location: string;
  lat_micro: number;
  lng_micro: number;
  floor_count: number;
  stocked_location_count: number;
}

/** A warehouse the map cannot place yet — it has no position. */
export interface WarehouseUnlocatedOut {
  id: string;
  code: string;
  name: string;
  location: string;
}

/** `GET /operations/warehouses/map` — `operations.inventory.warehouse.read`. */
export interface WarehouseMapOut {
  sites: WarehouseMapSiteOut[];
  without_coordinates: WarehouseUnlocatedOut[];
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

/* ---------------------------------- fulfilment settings (INVENTORY-02) ---------------------------------- */

export type InvoiceTiming = "on_accept" | "on_dispatch" | "on_delivery";
export type PaymentTerm = "prepaid" | "cash" | "credit";

/** Mirrors `FulfilmentSettingsIn`: the whole policy row, every field required. */
export interface FulfilmentSettingsIn {
  invoice_timing: InvoiceTiming;
  /** A channel left out pays on the `cash` term. */
  payment_defaults: Partial<Record<string, PaymentTerm>>;
  offer_timeout_seconds: number;
  offer_rounds: number;
  require_pickup_code: boolean;
  /** A decimal string, 0.00–100.00. */
  default_tax_rate_pct: string;
  auto_assignment_enabled: boolean;
  /** Order invoicing opt-in (INVENTORY-01): READY orders raise a tax invoice only when true. */
  auto_invoice_orders: boolean;
  task_sla_minutes: number;
  alert_email: string | null;
  alert_phone: string | null;
}

/** Mirrors `FulfilmentSettingsOut`: the effective policy and where it came from. */
export interface FulfilmentSettingsOut extends FulfilmentSettingsIn {
  warehouse_id: string | null;
  source: "warehouse" | "organization" | "default";
  updated_at: string | null;
  updated_by: string | null;
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
  /** `stub`: no Razorpay call leaves the platform. Kept for older servers; `provider_wire` is
   *  the connected provider's own. */
  wire: "stub" | "razorpay";
  /** The connected account's provider, and whether a checkout with it can be paid for real. */
  provider?: CollectionsProvider | null;
  provider_wire?: "stub" | "live" | null;
  collections: OrderCollectionOut[];
}

/** The payment providers a merchant can connect (ADR-0033). */
export type CollectionsProvider = "razorpay" | "cashfree";

/** What the browser needs to open Razorpay's hosted Checkout — the **public** key id and the
 *  provider Order id. There is no secret here, and there never will be. */
export interface RazorpayOrderCheckoutSessionOut {
  provider: "razorpay";
  key_id: string;
  provider_order_id: string;
  amount_minor: number;
  currency: string;
  mode: "test" | "live";
  openable: boolean;
  order_number: string;
  prefill: { name: string; contact: string };
}

/** What `Cashfree({mode}).checkout({paymentSessionId})` needs. The session id is fetched from
 *  Cashfree for this one response and is never stored; no key, no customer detail (the phone
 *  went to Cashfree server to server). */
export interface CashfreeOrderCheckoutSessionOut {
  provider: "cashfree";
  payment_session_id: string;
  mode: "sandbox" | "production";
  provider_order_id: string;
  amount_minor: number;
  currency: string;
  openable: boolean;
  order_number: string;
  /** Never sent for Cashfree: no key reaches the browser, and the payer is not prefilled here. */
  key_id?: never;
  prefill?: never;
}

/** The browser's handle, by provider. */
export type OrderCheckoutSessionOut = RazorpayOrderCheckoutSessionOut | CashfreeOrderCheckoutSessionOut;

export interface OrderCheckoutOut {
  collection: OrderCollectionOut;
  checkout: OrderCheckoutSessionOut | null;
}

export interface OrderRefundIn {
  amount_minor: number;
  reason: string;
}

/** `unverified` (never checked, or changed since), `verified`, or `failed` (the provider refused
 *  the credentials: `last_error_code`). */
export type ConnectionState = "unverified" | "verified" | "failed";

/** `GET/PUT /payment/collections/merchant-account` — the organization's own payment-provider
 *  account (Razorpay or Cashfree, ADR-0033). */
export interface MerchantAccountOut {
  configured: boolean;
  id?: string | null;
  provider?: CollectionsProvider | null;
  /** A Razorpay key id, or a Cashfree App ID. */
  key_id?: string | null;
  /** `test` / `live` — for Cashfree, Sandbox / Production. */
  mode?: "test" | "live" | null;
  key_secret_masked?: string | null;
  /** Razorpay only: a Cashfree account has no webhook secret (its Secret Key signs deliveries). */
  webhook_secret_masked?: string | null;
  /** Path (with `?account=`) to register as the webhook URL in the provider's dashboard. */
  webhook_path?: string | null;
  /** The absolute URL to register, from the platform's own public address. */
  webhook_url?: string | null;
  webhook_events: string[];
  rotated_at?: string | null;
  created_at?: string | null;
  connection_state?: ConnectionState | null;
  verified_at?: string | null;
  last_error_code?: string | null;
  /** When a webhook from this account's provider last arrived (since connecting). */
  last_webhook_at?: string | null;
  live_keys_allowed: boolean;
  wire: "stub" | "razorpay";
  /** Per provider: whether a merchant's checkout can be paid for real (`live`) or only on the stub. */
  wires?: Record<CollectionsProvider, "stub" | "live">;
}

/** `PUT /payment/collections/merchant-account` — Razorpay's shape or Cashfree's. Needs a session
 *  that signed in with a one-time code. */
export type MerchantAccountIn =
  | { provider: "razorpay"; key_id: string; key_secret: string; webhook_secret: string }
  | { provider: "cashfree"; key_id: string; key_secret: string; mode: "sandbox" | "production" };

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

/** One location on a floor's *published* layout (`GET /operations/floors/{floor_id}/published-layout`).
 *  Millimetres throughout. `z_mm` / `height_mm` are `null` when the layout never set them. */
export interface PublishedPlacementOut {
  location_id: string;
  code: string;
  name: string;
  kind: LocationKind;
  parent_id: string | null;
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
  width_mm: number;
  depth_mm: number;
  height_mm: number | null;
}

/** `GET /operations/floors/{floor_id}/published-layout` — `operations.warehouse.layout.read`.
 *  Only ever the published version: `published: false` with no placements when the floor has none.
 *  A draft never appears here. */
export interface PublishedLayoutOut {
  floor_id: string;
  warehouse_id: string;
  floor_name: string;
  published: boolean;
  layout_version_id: string | null;
  placements: PublishedPlacementOut[];
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

/* ------------------- receiving and purchase (inventory Phase 2) ------------------- */

/** `1 box = 12 pcs` is `{uom: "box", numerator: 12, denominator: 1}`. */
export interface UomConversionOut {
  id: string;
  inventory_item_id: string;
  uom_id: string;
  uom: string;
  numerator: number;
  denominator: number;
  stock_uom: string;
  created_at: string;
}

/** `POST /inventory/{item_id}/uom-conversions`. */
export interface UomConversionIn {
  uom: string;
  numerator: number;
  denominator?: number;
}

/** `PATCH /inventory/uom-conversions/{id}` — numerator and/or denominator. */
export interface UomConversionPatch {
  numerator?: number;
  denominator?: number;
}

export type RequisitionStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "CONVERTED" | "CANCELLED";

export interface RequisitionLineIn {
  inventory_item_id: string;
  /** In the item's own stock unit — a decimal string, greater than zero. */
  quantity: Qty;
  suggested_supplier_id?: string | null;
  est_unit_price_minor?: number | null;
  note?: string | null;
}

export interface RequisitionIn {
  warehouse_id?: string | null;
  needed_by?: string | null;
  reason?: string | null;
  requested_by: string;
  lines: RequisitionLineIn[];
}

/** `PATCH /purchase-requisitions/{id}`, draft only. `lines`, when present, replaces them. */
export interface RequisitionPatch {
  warehouse_id?: string | null;
  needed_by?: string | null;
  reason?: string | null;
  lines?: RequisitionLineIn[];
}

export interface RequisitionDecisionIn {
  note?: string | null;
}

export interface RequisitionRejectIn {
  reason: string;
}

export interface RequisitionPriceIn {
  line_id: string;
  unit_price_minor: number;
}

export interface RequisitionConvertIn {
  supplier_id: string;
  expected_date: string;
  /** A warehouse *name* (a purchase order names its warehouse by name). */
  warehouse?: string | null;
  currency?: string | null;
  notes?: string | null;
  prices?: RequisitionPriceIn[];
}

export interface RequisitionLineOut {
  id: string;
  line_no: number;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  quantity: Qty;
  suggested_supplier_id: string | null;
  est_unit_price_minor: number | null;
  note: string | null;
}

export interface RequisitionOut {
  id: string;
  number: string;
  status: RequisitionStatus;
  requested_by: string;
  requested_by_id: string | null;
  warehouse_id: string | null;
  warehouse_name: string | null;
  needed_by: string | null;
  reason: string | null;
  decision_note: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  converted_po_id: string | null;
  converted_po_number: string | null;
  created_at: string;
  updated_at: string;
  lines: RequisitionLineOut[];
}

export interface RequisitionSummaryOut {
  id: string;
  number: string;
  status: RequisitionStatus;
  requested_by: string;
  warehouse_name: string | null;
  needed_by: string | null;
  line_count: number;
  converted_po_number: string | null;
  created_at: string;
}

export type ReceiptOutcome = "PARTIAL" | "EXACT" | "SHORT" | "OVER";

export interface ReceiptRollIn {
  roll_no?: string | null;
  /** In the item's stock unit (82.5 m). */
  primary_qty: Qty;
  /** A separately measured figure in another unit (14.8 kg) — never derived. */
  secondary_qty?: string | null;
  secondary_uom?: string | null;
  width_mm?: number | null;
  gsm?: number | null;
  shade?: string | null;
  grade?: string | null;
}

/** An existing lot (`lot_id`) or the fields of a new one — never both. */
export interface ReceiptLotIn {
  lot_id?: string | null;
  lot_no?: string | null;
  supplier_lot_no?: string | null;
  manufactured_on?: string | null;
  expires_on?: string | null;
}

export interface ReceiptLineIn {
  purchase_order_line_id: string;
  /** Non-negative decimal string, in `uom`; `"0"` only with `close_short`. */
  quantity: string;
  uom?: string | null;
  location_id?: string | null;
  hold?: boolean;
  close_short?: boolean;
  lot?: ReceiptLotIn | null;
  rolls?: ReceiptRollIn[];
}

export interface GoodsReceiptIn {
  purchase_order_id: string;
  received_on?: string;
  delivery_note?: string | null;
  note?: string | null;
  lines: ReceiptLineIn[];
}

export interface ReceiptRollOut {
  id: string;
  roll_no: string;
  primary_qty: Qty;
  secondary_qty: Qty | null;
  secondary_uom: string | null;
}

export interface GoodsReceiptLineOut {
  id: string;
  line_no: number;
  purchase_order_line_id: string;
  sku: string;
  description: string;
  inventory_item_id: string | null;
  stock_uom: string | null;
  location_id: string | null;
  location_label: string | null;
  stock_status: StockStatus | null;
  lot_id: string | null;
  lot_no: string | null;
  supplier_lot_no: string | null;
  rolls: ReceiptRollOut[];
  entered_qty: Qty;
  entered_uom: string;
  /** In the item's stock unit — what actually went onto the books. */
  received_qty: Qty;
  ordered_qty: Qty;
  prior_received_qty: Qty;
  cumulative_qty: Qty;
  tolerance_pct: string;
  close_short: boolean;
  outcome: ReceiptOutcome;
  unit_cost_minor: number | null;
  unit_cost_currency: string | null;
}

export interface GoodsReceiptOut {
  id: string;
  number: string;
  purchase_order_id: string;
  po_number: string;
  po_status: string;
  supplier_id: string;
  supplier_name: string;
  received_on: string;
  delivery_note: string | null;
  note: string | null;
  status: "POSTED";
  /** True when this is the first result of a retried `Idempotency-Key`, not a new posting. */
  replayed: boolean;
  created_at: string;
  created_by: string | null;
  lines: GoodsReceiptLineOut[];
}

export interface GoodsReceiptSummaryOut {
  id: string;
  number: string;
  purchase_order_id: string;
  po_number: string;
  supplier_name: string;
  received_on: string;
  delivery_note: string | null;
  line_count: number;
  created_at: string;
}

export interface ReceivingConversionOut {
  uom: string;
  numerator: number;
  denominator: number;
}

export interface ReceivingLineOut {
  purchase_order_line_id: string;
  sku: string;
  description: string;
  inventory_item_id: string | null;
  stock_uom: string | null;
  ordered_qty: Qty;
  received_qty: Qty;
  remaining_qty: Qty;
  /** The most that may still be received (ordered + tolerance - received), never negative. */
  max_receivable_qty: Qty;
  tolerance_pct: string;
  short_closed: boolean;
  track_lot: boolean;
  track_roll: boolean;
  track_expiry: boolean;
  track_quality: boolean;
  default_location_id: string | null;
  default_location_label: string | null;
  conversions: ReceivingConversionOut[];
}

export interface ReceivingContextOut {
  purchase_order_id: string;
  po_number: string;
  po_status: string;
  warehouse: string;
  supplier_name: string;
  /** Whether the PO is in a state a goods receipt can be posted against. */
  can_receive: boolean;
  lines: ReceivingLineOut[];
}

/* ------------------------------------ quality (inventory plan Phase 4) ------------------------------------ */
/*
 * Mirrors `app/quality/schemas.py`. Every quantity, bound and reading is a decimal **string** with
 * at most three decimals (`Qty`) — never a JS number; the server converts to milli-units at the
 * edge and this file never does arithmetic on one.
 */

export type QcKind = "incoming" | "in-process" | "finished";
export type QcParamKind = "numeric" | "pass_fail";
export type InspectionStatus = "PENDING" | "PASSED" | "FAILED" | "HOLD" | "REWORK" | "PARTIAL_PASS";
export type RejectBucket = "REJECTED" | "QUARANTINE";
export type DispositionAction = "RETURN_TO_VENDOR" | "SCRAP";
export type Disposition = "NONE" | DispositionAction;

export interface QcParamIn {
  name: string;
  kind: QcParamKind;
  unit?: string;
  min?: Qty | null;
  max?: Qty | null;
  is_critical?: boolean;
}

export interface QcParamOut {
  id: string;
  name: string;
  kind: QcParamKind;
  unit: string;
  min: Qty | null;
  max: Qty | null;
  is_critical: boolean;
  position: number;
}

export interface QcTemplateIn {
  code: string;
  name: string;
  kind: QcKind;
  item_id?: string | null;
  description?: string;
  is_active?: boolean;
  params: QcParamIn[];
}

/** `PATCH` — a supplied `params` replaces the whole list; omit it to leave parameters alone. */
export interface QcTemplatePatch {
  name?: string;
  description?: string;
  is_active?: boolean;
  params?: QcParamIn[];
}

export interface QcTemplateOut {
  id: string;
  code: string;
  name: string;
  kind: QcKind;
  item_id: string | null;
  description: string;
  is_active: boolean;
  params: QcParamOut[];
  created_at: string;
  updated_at: string;
}

export interface InspectionIn {
  kind: QcKind;
  item_id: string;
  location_id: string;
  quantity: Qty;
  lot_id?: string | null;
  roll_id?: string | null;
  template_id?: string | null;
  supplier_id?: string | null;
  reference_type?: string;
  reference_id?: string | null;
  /** Default true: moves the quantity AVAILABLE -> QUALITY_HOLD as part of opening. */
  hold_stock?: boolean;
  notes?: string;
}

export interface InspectionResultIn {
  param_name: string;
  value?: Qty | null;
  passed?: boolean | null;
  note?: string;
}

export interface InspectionResultOut {
  id: string;
  param_name: string;
  param_kind: QcParamKind;
  unit: string;
  min: Qty | null;
  max: Qty | null;
  is_critical: boolean;
  value: Qty | null;
  passed: boolean;
  note: string;
  recorded_by: string | null;
}

export interface InspectionOut {
  id: string;
  kind: QcKind;
  status: InspectionStatus;
  template_id: string | null;
  item_id: string;
  item_sku: string;
  item_name: string;
  uom: string;
  location_id: string;
  location_code: string;
  lot_id: string | null;
  roll_id: string | null;
  supplier_id: string | null;
  reference_type: string;
  reference_id: string | null;
  quantity: Qty;
  accepted: Qty | null;
  rejected: Qty | null;
  rejected_bucket: RejectBucket | null;
  notes: string;
  decision_note: string;
  decided_by: string | null;
  decided_at: string | null;
  disposition: Disposition;
  disposition_note: string;
  disposition_reference: string;
  disposed_at: string | null;
  hold_move_id: string | null;
  decision_move_id: string | null;
  disposition_move_id: string | null;
  /** What the readings alone recommend — never applied automatically. */
  suggested_outcome: InspectionStatus;
  /** False while a critical parameter has failed. */
  can_release: boolean;
  results: InspectionResultOut[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReleaseIn {
  /** Omit to release everything (`PASSED`); less is a `PARTIAL_PASS`. */
  accepted?: Qty | null;
  rejected_bucket?: RejectBucket;
  note?: string;
}

export interface RejectIn {
  reason: string;
  bucket?: RejectBucket;
}

export interface DispositionIn {
  action: DispositionAction;
  note?: string;
  reference?: string;
}

/* ----------------------------- manufacturing ----------------------------- */
/**
 * Inventory Phase 5 — bills of materials, production orders, wastage. Mirrors
 * `services/operations/app/manufacturing/schemas.py` (`libs/contracts/openapi/operations.json`).
 * Every id is a string; every quantity is a decimal `Qty` string with at most three decimals and
 * every percentage a decimal string with at most two — none of them is ever parsed into a float.
 */

/** A percentage as a decimal string: `"2.5"`. At most two decimals. */
export type Pct = string;

export type BomVersionStatus = "DRAFT" | "PUBLISHED" | "RETIRED";
export type ProductionOrderStatus = "DRAFT" | "APPROVED" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";
export type WastageType = "CUTTING" | "DEFECT" | "SPILLAGE" | "DAMAGE" | "PROCESS_LOSS" | "LEFTOVER" | "OTHER";
export type OutputStatus = "AVAILABLE" | "QUALITY_HOLD";

export interface BomSubstituteIn {
  substitute_item_id: string;
  ratio?: Qty;
  priority?: number;
}

export interface BomSubstituteOut {
  id: string;
  substitute_item_id: string;
  sku: string;
  name: string;
  uom: string;
  ratio: Qty;
  priority: number;
}

export interface BomComponentIn {
  component_item_id: string;
  quantity: Qty;
  scrap_percent?: Pct;
  effective_from?: string | null;
  effective_to?: string | null;
  notes?: string;
}

export interface BomComponentPatch {
  quantity?: Qty;
  scrap_percent?: Pct;
  effective_from?: string | null;
  effective_to?: string | null;
  notes?: string;
}

export interface BomComponentOut {
  id: string;
  line_no: number;
  component_item_id: string;
  sku: string;
  name: string;
  uom: string;
  quantity: Qty;
  scrap_percent: Pct;
  effective_from: string | null;
  effective_to: string | null;
  notes: string;
  /** True when this component has a BOM of its own — what makes the BOM multi-level. */
  is_produced: boolean;
  substitutes: BomSubstituteOut[];
}

export interface BomVersionIn {
  output_quantity?: Qty;
  effective_from?: string | null;
  effective_to?: string | null;
  notes?: string;
  copy_from_version_id?: string | null;
}

export interface BomVersionPatch {
  output_quantity?: Qty;
  effective_from?: string | null;
  effective_to?: string | null;
  notes?: string;
}

export interface BomVersionOut {
  id: string;
  bom_id: string;
  version_no: number;
  status: BomVersionStatus;
  output_quantity: Qty;
  effective_from: string | null;
  effective_to: string | null;
  notes: string;
  published_at: string | null;
  published_by: string | null;
  content_hash: string | null;
  component_count: number;
  created_at: string;
  updated_at: string;
}

export interface BomVersionDetailOut extends BomVersionOut {
  components: BomComponentOut[];
}

export interface BomIn {
  code: string;
  name: string;
  item_id: string;
  description?: string;
  output_quantity?: Qty;
}

export interface BomPatch {
  name?: string;
  description?: string;
  is_active?: boolean;
}

export interface BomOut {
  id: string;
  code: string;
  name: string;
  item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  description: string;
  is_active: boolean;
  version_count: number;
  latest_version_no: number | null;
  /** The published version in force today (null while nothing is published). */
  current_version_id: string | null;
  current_version_no: number | null;
  created_at: string;
  updated_at: string;
}

export interface BomDetailOut extends BomOut {
  versions: BomVersionOut[];
}

export interface ExplosionNodeOut {
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  level: number;
  quantity: Qty;
  is_produced: boolean;
  children: ExplosionNodeOut[];
}

export interface ExplosionTotalOut {
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  quantity: Qty;
}

export interface ExplosionOut {
  bom_id: string;
  version_id: string;
  quantity: Qty;
  root: ExplosionNodeOut;
  leaf_totals: ExplosionTotalOut[];
}

export interface ProductionOrderIn {
  bom_id?: string | null;
  item_id?: string | null;
  bom_version_id?: string | null;
  quantity: Qty;
  output_location_id: string;
  wip_location_id?: string | null;
  planned_start?: string | null;
  due_date?: string | null;
  notes?: string;
}

export interface ProductionOrderPatch {
  quantity?: Qty;
  output_location_id?: string;
  wip_location_id?: string | null;
  planned_start?: string | null;
  due_date?: string | null;
  notes?: string;
}

export interface ProductionCancelIn {
  reason?: string;
}

export interface MaterialSubstituteIn {
  /** One of the BOM line's declared substitutes; omit to go back to the BOM's own item. */
  substitute_item_id?: string | null;
}

export interface ReserveLineIn {
  material_id: string;
  location_id?: string | null;
  lot_id?: string | null;
  roll_id?: string | null;
  quantity?: Qty | null;
}

export interface ReserveIn {
  lines?: ReserveLineIn[] | null;
}

export interface IssueLineIn {
  material_id?: string | null;
  pick_id?: string | null;
  location_id?: string | null;
  lot_id?: string | null;
  roll_id?: string | null;
  quantity?: Qty | null;
}

export interface IssueIn {
  lines?: IssueLineIn[] | null;
  over_issue?: boolean;
}

export interface ReturnLineIn {
  pick_id: string;
  quantity?: Qty | null;
  location_id?: string | null;
}

export interface ReturnIn {
  lines: ReturnLineIn[];
}

export interface OutputIn {
  quantity: Qty;
  location_id?: string | null;
  lot_id?: string | null;
  roll_id?: string | null;
  status?: OutputStatus;
  note?: string;
}

export interface WastageIn {
  material_id: string;
  wastage_type: WastageType;
  quantity: Qty;
  pick_id?: string | null;
  note?: string;
}

export interface CompleteIn {
  /** Required when anything is left in WIP: put it back, or write it off as wastage. */
  leftover?: "return" | "scrap" | null;
  note?: string;
}

export interface ProductionMaterialOut {
  id: string;
  line_no: number;
  component_item_id: string;
  component_sku: string;
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  is_substitute: boolean;
  ratio: Qty;
  scrap_percent: Pct;
  required: Qty;
  reserved: Qty;
  issued: Qty;
  consumed: Qty;
  returned: Qty;
  wasted: Qty;
  wip: Qty;
  /** What is still not covered by a reservation or an issue. */
  open_need: Qty;
}

export interface ProductionPickOut {
  id: string;
  material_id: string;
  item_id: string;
  source_location_id: string;
  wip_location_id: string;
  lot_id: string | null;
  roll_id: string | null;
  reservation_id: string | null;
  reserved: Qty;
  issued: Qty;
  consumed: Qty;
  returned: Qty;
  wasted: Qty;
  wip: Qty;
}

export interface ProductionOutputOut {
  id: string;
  production_order_id: string;
  item_id: string;
  quantity: Qty;
  location_id: string;
  status: OutputStatus;
  lot_id: string | null;
  roll_id: string | null;
  note: string;
  created_at: string;
}

export interface ProductionWastageOut {
  id: string;
  production_order_id: string;
  material_id: string;
  pick_id: string;
  event_id: string;
  item_id: string;
  wastage_type: WastageType;
  quantity: Qty;
  location_id: string;
  lot_id: string | null;
  roll_id: string | null;
  note: string;
  created_at: string;
}

export interface VarianceLineOut {
  material_id: string;
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  /** What the BOM (scrap allowance included) says the produced quantity should have taken. */
  standard: Qty;
  /** The same, without the scrap allowance. */
  theoretical: Qty;
  consumed: Qty;
  wasted: Qty;
  /** consumed + wasted. */
  actual: Qty;
  /** actual - standard; positive is over-use. */
  variance: Qty;
  /** variance as a percentage of standard, two decimals; null when standard is zero. */
  variance_percent: string | null;
}

export interface VarianceOut {
  planned_quantity: Qty;
  produced_quantity: Qty;
  lines: VarianceLineOut[];
}

export interface ProductionOrderOut {
  id: string;
  number: string;
  status: ProductionOrderStatus;
  bom_id: string;
  bom_code: string;
  bom_version_id: string;
  bom_version_no: number;
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  planned_quantity: Qty;
  produced_quantity: Qty;
  output_location_id: string;
  wip_location_id: string | null;
  planned_start: string | null;
  due_date: string | null;
  approved_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface ProductionOrderDetailOut extends ProductionOrderOut {
  materials: ProductionMaterialOut[];
  picks: ProductionPickOut[];
  outputs: ProductionOutputOut[];
  wastage: ProductionWastageOut[];
  variance: VarianceOut;
}

export interface ShortageOut {
  material_id: string;
  item_id: string;
  sku: string;
  required: Qty;
  covered: Qty;
  short: Qty;
}

/** The order after an action; `shortages` is filled by a reservation that could not cover
 *  everything. `replayed` is true when an `Idempotency-Key` matched an earlier request. */
export interface ProductionActionOut extends ProductionOrderDetailOut {
  shortages: ShortageOut[];
  replayed: boolean;
}

export interface WastageThresholdIn {
  item_id?: string | null;
  wastage_type?: WastageType | null;
  max_percent: Pct;
}

export interface WastageThresholdOut {
  id: string;
  item_id: string | null;
  sku: string | null;
  wastage_type: WastageType | null;
  max_percent: Pct;
  created_at: string;
}

/* ----------------------------------- MRP ---------------------------------- */
/*
 * Inventory Phase 7 (`services/operations/app/mrp/schemas.py`). Quantities are decimal strings,
 * never numbers; ids are strings.
 */

export type MrpRunStatus = "QUEUED" | "COMPLETED" | "FAILED";
export type MrpDemandSource = "SALES_ORDER" | "PRODUCTION_ORDER" | "MANUAL";
export type MrpShortageAction = "BUY" | "MAKE";
export type MrpRunMode = "auto" | "sync" | "worker";

export interface MrpManualDemandIn {
  item_id: string;
  quantity: Qty;
  due_date?: string | null;
  note?: string;
}

export interface MrpRunIn {
  sources: MrpDemandSource[];
  manual_demands?: MrpManualDemandIn[];
  substitutes?: { component_item_id: string; substitute_item_id: string }[];
  plan_date?: string | null;
  notes?: string;
  mode?: MrpRunMode;
}

export interface MrpRunOut {
  id: string;
  number: string;
  status: MrpRunStatus;
  execution: "SYNC" | "WORKER";
  plan_date: string;
  sources: MrpDemandSource[];
  notes: string;
  demand_count: number;
  shortage_count: number;
  items_planned: number;
  result_hash: string | null;
  error: string;
  requested_by: string | null;
  created_at: string;
  completed_at: string | null;
}

/** `replayed` is true when an `Idempotency-Key` matched an earlier request. */
export interface MrpRunCreatedOut extends MrpRunOut {
  replayed: boolean;
}

export interface MrpDemandOut {
  id: string;
  source_type: MrpDemandSource;
  source_id: string | null;
  source_ref: string;
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  requested: Qty;
  reserved: Qty;
  quantity: Qty;
  due_date: string | null;
  note: string;
}

export interface MrpShortageOut {
  id: string;
  run_id: string;
  item_id: string;
  sku: string;
  name: string;
  uom: string;
  action: MrpShortageAction;
  level: number;
  gross: Qty;
  on_hand: Qty;
  reserved: Qty;
  available: Qty;
  open_po: Qty;
  open_production: Qty;
  supply: Qty;
  shortage: Qty;
  need_by: string | null;
  requisition_request_id: string | null;
  requisition_requested_at: string | null;
  requisition_id: string | null;
}

export interface MrpShortageLineOut {
  source_type: MrpDemandSource;
  source_id: string | null;
  source_ref: string;
  parent_item_id: string | null;
  parent_sku: string | null;
  quantity: Qty;
  due_date: string | null;
}

export interface MrpShortageDetailOut extends MrpShortageOut {
  lines: MrpShortageLineOut[];
}

export interface RequisitionRequestIn {
  shortage_ids?: string[];
  note?: string;
}

export interface RequisitionRequestLineOut {
  shortage_id: string;
  item_id: string;
  sku: string;
  uom: string;
  quantity: Qty;
  need_by: string | null;
}

export interface RequisitionRequestOut {
  request_id: string;
  run_id: string;
  run_number: string;
  lines: RequisitionRequestLineOut[];
  replayed: boolean;
}

export interface MrpDemandSourcesOut {
  sales_order_lines: number;
  production_order_materials: number;
  sync_limit: number;
  mode_if_auto: "sync" | "worker";
}

/* --------------------------------- job work (Inventory plan Phase 6) --------------------------------- */
/**
 * Mirrors `services/operations/app/jobwork/schemas.py`. Every quantity is a decimal **string**
 * (`Qty`), never a number — a float cannot hold `0.001` of a metre exactly, and a reconciliation
 * that is off by a milli-unit is a reconciliation that does not balance. Money is integer minor
 * units with an ISO currency.
 */

export type JobWorkStatus =
  | "DRAFT"
  | "APPROVED"
  | "ISSUED"
  | "PARTIALLY_RECEIVED"
  | "RECEIVED"
  | "CLOSED"
  | "CANCELLED";
export type EwayStatus = "NOT_REQUIRED" | "REQUESTED" | "GENERATED" | "FAILED";
export type JobWorkGoodStatus = "AVAILABLE" | "QUALITY_HOLD";

export interface JobWorkOrderOut {
  id: string;
  number: string;
  supplier_id: string;
  supplier_name: string;
  process: string;
  status: JobWorkStatus;
  due_date: string | null;
  is_overdue: boolean;
  days_overdue: number;
  qc_required: boolean;
  notes: string;
  version: number;
  source_warehouse_id: string | null;
  receive_location_id: string | null;
  material_count: number;
  total_sent: Qty;
  total_outstanding: Qty;
  issued_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobWorkMaterialOut {
  id: string;
  line_no: number;
  item_id: string;
  sku: string;
  uom: string;
  lot_id: string | null;
  roll_id: string | null;
  location_id: string;
  quantity: Qty;
  sent: Qty;
  received: Qty;
  rejected: Qty;
  shortage: Qty;
  outstanding: Qty;
  unit_value_minor: number;
  currency: string;
}

export interface JobWorkReceiptOut {
  id: string;
  receipt_no: string;
  material_id: string;
  sku: string;
  received: Qty;
  rejected: Qty;
  shortage: Qty;
  location_id: string;
  good_status: JobWorkGoodStatus;
  qc_handoff: boolean;
  received_on: string;
  note: string;
  created_by: string | null;
  created_at: string;
}

export interface ChallanLineOut {
  sku: string;
  uom: string;
  quantity: Qty;
  unit_value_minor: number;
  value_minor: number;
  lot_id: string | null;
  roll_id: string | null;
}

export interface ChallanOut {
  id: string;
  order_id: string;
  order_number: string;
  challan_number: string;
  kind: "DELIVERY";
  issued_at: string;
  supplier_id: string;
  supplier_name: string;
  process: string;
  lines: ChallanLineOut[];
  total_value_minor: number;
  currency: string;
  vehicle_no: string | null;
  transporter_id: string | null;
  distance_km: number | null;
  eway_status: EwayStatus;
  eway_bill_no: string | null;
  eway_requested_at: string | null;
}

export interface JobWorkReconciliationLineOut {
  material_id: string;
  sku: string;
  uom: string;
  sent: Qty;
  received: Qty;
  rejected: Qty;
  shortage: Qty;
  outstanding: Qty;
}

export interface JobWorkReconciliationDriftOut {
  item_id: string;
  sku: string;
  documents: Qty;
  ledger: Qty;
}

export interface JobWorkReconciliationOut {
  order_id: string;
  order_status: JobWorkStatus;
  issued: boolean;
  lines: JobWorkReconciliationLineOut[];
  total_sent: Qty;
  total_received: Qty;
  total_rejected: Qty;
  total_shortage: Qty;
  total_outstanding: Qty;
  documents_settled: boolean;
  ledger_consistent: boolean;
  drift: JobWorkReconciliationDriftOut[];
  /** Settled **and** consistent — the one condition `:close` requires. */
  balanced: boolean;
}

export interface JobWorkOrderDetailOut extends JobWorkOrderOut {
  materials: JobWorkMaterialOut[];
  receipts: JobWorkReceiptOut[];
  challans: ChallanOut[];
  reconciliation: JobWorkReconciliationOut;
}

export interface JobWorkMaterialIn {
  item_id: string;
  quantity: Qty;
  location_id?: string;
  lot_id?: string;
  roll_id?: string;
}

export interface JobWorkOrderIn {
  supplier_id: string;
  process?: string;
  due_date?: string | null;
  source_warehouse_id?: string;
  receive_location_id?: string;
  qc_required?: boolean;
  notes?: string;
  materials: JobWorkMaterialIn[];
}

export interface JobWorkIssueIn {
  vehicle_no?: string;
  transporter_id?: string;
  distance_km?: number;
  note?: string;
}

export interface JobWorkReceiptLineIn {
  material_id: string;
  received: Qty;
  rejected: Qty;
  shortage: Qty;
  location_id?: string;
}

export interface JobWorkReceiveIn {
  lines: JobWorkReceiptLineIn[];
  received_on?: string;
  note?: string;
}

export interface ChallanEwayIn {
  distance_km: number;
  vehicle_no?: string;
  transporter_id?: string;
}
