/**
 * Wire types for the worker PWA. Mirrors `services/operations/app/schemas.py`'s fulfilment (and
 * the one location shape it needs) exactly — this app is a different role's surface on the same
 * backend `frontend/product-operations/src/api/types.ts` already mirrors, so the shapes below are
 * the same contract, not a fork of it.
 *
 * Fields the backend contract does not carry, worth stating once rather than at every call
 * site that wants them:
 * - `PickTaskLineOut` has no variant. Its item name/unit, bin code/name and the task's order number
 *   are display fields the server fills in only for a task assigned to the caller (ADR-0024 §7
 *   addendum) and are `null` otherwise — the screens fall back to the shortened id.
 * - Neither `OrderOut` nor `PickTaskOut` has a priority field.
 * - `PickConfirmIn`/`PackageIn`/`ScanIn` take no client idempotency key (contrast
 *   `POST /inventory/{id}/adjustments`, which does, in the same backend router).
 * See this app's `README.md` for the full account of what that limits on screen.
 */

export type Page<T> = { items: T[]; total: number; page: number; size: number; pages: number };

/** The API sends a quantity as a decimal string with at most three decimals (`"82.5"`). */
export type Qty = string;

export type PresenceStatus = "online" | "offline";

/** Mirrors `WorkerOut`. `zone_location_ids` is `null` unless the caller also holds
 *  `operations.fulfilment.worker.location_read`, which `OPERATIONS_WORKER` does not. */
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

/** `POST /operations/fulfilment/workers/{id}/presence` — a worker may only set their own. */
export interface WorkerPresenceIn {
  status: PresenceStatus;
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

/** One order line's worth of picking inside a task. The five display fields are `null` unless
 *  this task is assigned to the caller (or they hold the matching org-wide read). */
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
  item_name: string | null;
  item_unit: string | null;
  source_location_code: string | null;
  source_location_name: string | null;
  destination_location_code: string | null;
}

/** Mirrors `assignment_offers` — one row per offer, kept as history. */
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

/** Mirrors `PickTaskOut` — one task, its lines, its whole offer history and any open exceptions. */
export interface PickTaskOut {
  id: string;
  order_id: string;
  /** The human order number; `null` unless this task is assigned to the caller. */
  order_number: string | null;
  warehouse_id: string;
  zone_location_id: string | null;
  status: PickTaskStatus;
  round: number;
  max_rounds: number;
  lines: PickTaskLineOut[];
  offers: AssignmentOfferOut[];
  exceptions: PickExceptionOut[];
  created_at: string;
  updated_at: string;
}

export type TaskReleaseIn = Record<string, never>;

/** `POST /operations/fulfilment/tasks/{id}/scan` — one row in the append-only `scan_events`. */
export interface ScanIn {
  barcode: string;
  location_id?: string | undefined;
  inventory_item_id?: string | undefined;
}

/** `POST /operations/fulfilment/tasks/{id}/lines/{line_id}/pick`. `quantity` omitted confirms the
 *  line's whole remaining request; less than that raises a `pick_exceptions` row for the
 *  shortfall (`reason` required whenever it does). */
export interface PickConfirmIn {
  quantity?: Qty | undefined;
  reason?: PickExceptionReason | undefined;
  note?: string | undefined;
}

export interface ExceptionResolveIn {
  note?: string | undefined;
}

export interface PackagingMaterialOut {
  id: string;
  code: string;
  name: string;
  length_mm: number | null;
  width_mm: number | null;
  height_mm: number | null;
  max_weight_grams: number | null;
  tare_weight_grams: number | null;
  is_active: boolean;
}

export type PackageStatus = "OPEN" | "PACKED";

export interface PackageLineOut {
  id: string;
  pick_task_line_id: string;
  inventory_item_id: string;
  sku: string;
  quantity: Qty;
  item_name: string | null;
  item_unit: string | null;
}

export interface PackageLineIn {
  pick_task_line_id: string;
  quantity: Qty;
}

export interface PackageOut {
  id: string;
  order_id: string;
  packaging_material_id: string | null;
  destination_location_id: string;
  status: PackageStatus;
  weight_grams: number | null;
  lines: PackageLineOut[];
  created_at: string;
}

/** `POST /operations/fulfilment/packages` — one call opens, fills and posts the package; there is
 *  no separate confirm step. Rejected with a 409 naming the SKU if a line asks for more than is
 *  staged-and-unpacked, or if the order is not `IN_FULFILMENT`/`READY`. */
export interface PackageIn {
  order_id: string;
  packaging_material_id?: string | undefined;
  destination_location_id: string;
  weight_grams?: number | undefined;
  lines: PackageLineIn[];
}

/** A location kind, mirrors `Location.kind`'s ADR-0024 §5 vocabulary — only the few fields the
 *  worker app ever renders. */
export interface LocationOut {
  id: string;
  warehouse_id: string;
  code: string;
  name: string;
  barcode: string | null;
}

// ------------------------------------------------------------------ counting (ADR-0026, plan F8)
// Opening a session, moving it to review, and approving/rejecting a discrepancy are the
// manager's job (product-operations, a separate app) and are not declared here.

export type CountSessionStatus = "ACTIVE" | "REVIEW" | "CLOSED" | "CANCELLED";

export interface CountSessionOut {
  id: string;
  warehouse_id: string;
  scope_kind: "WAREHOUSE" | "ZONE" | "LOCATIONS";
  status: CountSessionStatus;
  label: string;
  line_count: number;
  counted_count: number;
  pending_review_count: number;
  created_at: string;
  updated_at: string;
}

export type CountLineReviewStatus = "NONE" | "PENDING" | "APPROVED" | "REJECTED";
export type CountMethod = "scan" | "manual";

export interface CountLineOut {
  id: string;
  session_id: string;
  inventory_item_id: string;
  sku: string;
  location_id: string;
  status: string;
  is_unlisted: boolean;
  expected: string;
  counted: string | null;
  count_method: CountMethod | null;
  scanned_barcode: string | null;
  counted_by: string | null;
  counted_at: string | null;
  review_status: CountLineReviewStatus;
}

/** `POST /counting/sessions/{id}/lines/{line_id}/count`. */
export interface CountLineSubmitIn {
  counted: string;
  count_method: CountMethod;
  scanned_barcode?: string;
}

/** `POST /counting/sessions/{id}/lines` — a bucket the session's snapshot did not expect. */
export interface CountLineAddIn {
  inventory_item_id: string;
  location_id: string;
  status?: string;
  counted: string;
  count_method: CountMethod;
  scanned_barcode?: string;
}

// ------------------------------------------------------------------ storage put-away (ADR-0014 §12)
// Storage put-away is the move from a receiving or staging bin into its storage bin, recorded by
// scanning the destination bin's label. It is not ADR-0025's "putaway task" (the order-cancel
// reversal). The worker's GPS fix rides on the request as integer micro-degrees and is never
// echoed back: `PutawayOut` carries no coordinates, and only the audited admin reveal does.

/** The envelope the keyset-paginated reads return. No total; page until `next_cursor` is null. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

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

/** Mirrors the server's `LocationKind` — what a location is for. Only `receiving` and `staging` are
 *  places storage put-away may start from (spec decision D1). */
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

/** Mirrors `StockRowOut` — one bucket of stock: an item at a location, in a status, optionally in
 *  a lot / on a roll. `GET /operations/inventory/stock`, `operations.inventory.stock.read`. */
export interface StockRowOut {
  id: string;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  warehouse_id: string;
  warehouse_code: string;
  location_id: string;
  location_code: string;
  /** The kind of `location_id`. Absent or `null` from a server that predates it, so nothing may
   *  assume it is there: see `canPutAwayFrom`. */
  location_kind?: LocationKind | null | undefined;
  status: StockStatus;
  lot_id: string | null;
  lot_no: string | null;
  roll_id: string | null;
  roll_no: string | null;
  on_hand_qty: Qty;
  reserved_qty: Qty;
  available_qty: Qty;
}

/** `POST /operations/inventory/putaway`. Every number here is an integer — coordinates are
 *  micro-degrees (`Math.round(degrees * 1e6)`), never a float. `to_barcode` is the scanned bin
 *  label; the server resolves it (a worker has no `location.read`). */
export interface PutawayIn {
  inventory_item_id: string;
  from_location_id: string;
  to_barcode: string;
  quantity: Qty;
  lot_id?: string;
  roll_id?: string;
  suggested_location_id?: string;
  captured_at?: string;
  lat_micro?: number;
  lng_micro?: number;
  accuracy_m?: number;
  occurred_on?: string;
  note?: string;
}

export interface PutawayFromLocationOut {
  id: string;
  code: string;
  name: string;
}

export interface PutawayToLocationOut extends PutawayFromLocationOut {
  kind: string;
}

/** `PutawayOut` — 201 fresh, 200 replay of the same `Idempotency-Key`. `warnings` are
 *  machine-readable notes on what the server did not take as sent: `geo_dropped:<reason>`,
 *  `captured_at_adjusted`, `suggestion_ignored` (the `suggested_location_id` was not one of the
 *  caller's bins and was not stored), `geo_not_recorded`. The move itself is recorded in every case. */
export interface PutawayOut {
  move_id: string;
  replayed: boolean;
  from_location: PutawayFromLocationOut;
  to_location: PutawayToLocationOut;
  matched_suggestion: boolean | null;
  geo_recorded: boolean;
  warnings: string[];
}

export type PutawaySuggestionReason = "holds_item" | "empty_bin";

export interface PutawaySuggestion {
  location_id: string;
  code: string;
  /** The bin's own barcode label, `null` when it has none. A worker has no `location.read`, so this
   *  is how a scanned label is matched to the suggestion. */
  barcode: string | null;
  name: string;
  path: string;
  reason: PutawaySuggestionReason;
}

/** `GET /operations/inventory/putaway/suggest` — `suggestion` is `null` when no storable bin in
 *  the warehouse is a fit. */
export interface PutawaySuggestionOut {
  suggestion: PutawaySuggestion | null;
}
