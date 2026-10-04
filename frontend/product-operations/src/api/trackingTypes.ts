/**
 * Wire types for inventory Phase 1: lots, rolls, stock operations and the stock / ledger / trace
 * reads. They mirror `services/operations/app/inventory/tracking_schemas.py` field for field.
 *
 * Every quantity is a decimal **string** with at most three decimals (`Qty`, ADR-0014 §2). A write
 * model here never takes a `number` for a quantity, so a float cannot reach the wire.
 */

import type { Qty, StockStatus } from "./types";

/** The envelope the keyset-paginated reads return (`app/inventory/pagination.py`). No total: a
 *  ledger has none worth computing, so a screen pages with "Load more" until `next_cursor` is null. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export type LotQualityStatus = "PENDING" | "APPROVED" | "ON_HOLD" | "REJECTED";
export type RollState = "ACTIVE" | "SPLIT" | "MERGED";
export type LotRelation = "SPLIT" | "MERGE" | "BLEND" | "REWORK" | "DERIVED";

/** What a stock operation may move between: never the off-site statuses. */
export type OnSiteStatus = Exclude<StockStatus, "IN_TRANSIT" | "AT_JOB_WORKER">;

export type LedgerKind = "opening" | "receipt" | "adjustment" | "count" | "relocation" | "issue";

/* ---------------------------------- lots ---------------------------------- */

export type AttributeValue = string | number | boolean;

export interface LotOut {
  id: string;
  lot_no: string;
  supplier_lot_no: string | null;
  inventory_item_id: string;
  sku: string;
  uom: string;
  manufactured_on: string | null;
  received_on: string | null;
  expires_on: string | null;
  quality_status: LotQualityStatus;
  source_type: string | null;
  source_id: string | null;
  attributes: Record<string, AttributeValue>;
  on_hand_qty: Qty;
  created_at: string;
  updated_at: string;
}

/** `POST /inventory/lots`. `lot_no` is generated (`LOT-` + 8 hex) when omitted. */
export interface LotIn {
  inventory_item_id: string;
  lot_no?: string;
  supplier_lot_no?: string;
  manufactured_on?: string;
  received_on?: string;
  expires_on?: string;
  quality_status?: LotQualityStatus;
}

/* ---------------------------------- rolls --------------------------------- */

export interface RollOut {
  id: string;
  roll_no: string;
  inventory_item_id: string;
  sku: string;
  uom: string;
  lot_id: string | null;
  lot_no: string | null;
  state: RollState;
  primary_qty: Qty;
  secondary_qty: Qty | null;
  secondary_uom: string | null;
  width_mm: number | null;
  gsm: number | null;
  shade: string | null;
  grade: string | null;
  parent_roll_id: string | null;
  on_hand_qty: Qty;
  created_at: string;
  updated_at: string;
}

/** `POST /inventory/rolls`. `secondary_qty` and `secondary_uom` are given together or not at all. */
export interface RollIn {
  inventory_item_id: string;
  lot_id?: string;
  roll_no?: string;
  primary_qty: Qty;
  secondary_qty?: Qty;
  secondary_uom?: string;
  width_mm?: number;
  gsm?: number;
  shade?: string;
  grade?: string;
}

export interface RollEventOut {
  id: string;
  kind: string;
  related_roll_id: string | null;
  move_id: string | null;
  quantity: Qty | null;
  secondary_qty: Qty | null;
  detail: Record<string, unknown>;
  occurred_at: string;
}

/* ------------------------------ stock operations --------------------------- */

export interface OpeningLineIn {
  inventory_item_id: string;
  location_id: string;
  quantity: Qty;
  status?: OnSiteStatus;
  lot_id?: string;
  roll_id?: string;
}

export interface OpeningStockIn {
  lines: OpeningLineIn[];
  occurred_on?: string;
  note?: string;
}

export interface StockTransferIn {
  inventory_item_id: string;
  from_location_id: string;
  to_location_id: string;
  quantity: Qty;
  status?: OnSiteStatus;
  lot_id?: string;
  roll_id?: string;
  occurred_on?: string;
  note?: string;
}

export interface StockStatusChangeIn {
  inventory_item_id: string;
  location_id: string;
  from_status: OnSiteStatus;
  to_status: OnSiteStatus;
  quantity: Qty;
  lot_id?: string;
  roll_id?: string;
  occurred_on?: string;
  note?: string;
}

export interface SplitChildIn {
  quantity: Qty;
  roll_no?: string;
}

export interface RollSplitIn {
  location_id: string;
  status?: OnSiteStatus;
  children: SplitChildIn[];
  occurred_on?: string;
  note?: string;
}

export interface RollMergeIn {
  roll_ids: string[];
  location_id: string;
  status?: OnSiteStatus;
  roll_no?: string;
  occurred_on?: string;
  note?: string;
}

export interface StockMoveLineOut {
  ledger_entry_id: string;
  inventory_item_id: string;
  location_id: string;
  status: string;
  lot_id: string | null;
  roll_id: string | null;
  quantity: Qty;
  balance_after: Qty;
}

/** The move a stock operation posted. `replayed` is true when an `Idempotency-Key` matched an
 *  earlier request: nothing new was written and this is that request's result. */
export interface StockMoveOut {
  move_id: string;
  kind: string;
  reference_type: string | null;
  reference_id: string | null;
  occurred_on: string;
  replayed: boolean;
  lines: StockMoveLineOut[];
}

export interface RollSplitOut {
  move: StockMoveOut;
  parent: RollOut;
  children: RollOut[];
}

export interface RollMergeOut {
  move: StockMoveOut;
  result: RollOut;
  sources: RollOut[];
}

/* ----------------------------------- reads -------------------------------- */

/** One bucket of stock: an item at a location, in a status, optionally in a lot / on a roll. */
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
  status: StockStatus;
  lot_id: string | null;
  lot_no: string | null;
  roll_id: string | null;
  roll_no: string | null;
  on_hand_qty: Qty;
  reserved_qty: Qty;
  available_qty: Qty;
}

export interface LedgerRowOut {
  id: string;
  move_id: string;
  kind: LedgerKind;
  reason: string | null;
  reference_type: string | null;
  reference_id: string | null;
  note: string;
  occurred_on: string;
  inventory_item_id: string;
  sku: string;
  uom: string;
  location_id: string;
  location_code: string;
  status: StockStatus;
  lot_id: string | null;
  roll_id: string | null;
  quantity: Qty;
  balance_after: Qty;
  created_at: string;
  created_by: string | null;
}

/** One lot or roll reached by a trace. `from_id` is the node it was reached from. */
export interface TraceNodeOut {
  id: string;
  number: string;
  depth: number;
  from_id: string;
  relation: string | null;
  move_id: string | null;
  lot_id: string | null;
  lot_no: string | null;
}

export interface LotTraceOut {
  lot: LotOut;
  ancestors: TraceNodeOut[];
  descendants: TraceNodeOut[];
}

export interface RollTraceOut {
  roll: RollOut;
  lot: LotOut | null;
  ancestors: TraceNodeOut[];
  descendants: TraceNodeOut[];
  lot_ancestors: TraceNodeOut[];
}

/** An edge of the lot genealogy: this lot's material went into `child_lot_id`. */
export interface LotGenealogyOut {
  id: string;
  parent_lot_id: string;
  child_lot_id: string;
  relation: LotRelation;
  move_id: string | null;
  note: string;
  created_at: string;
}
