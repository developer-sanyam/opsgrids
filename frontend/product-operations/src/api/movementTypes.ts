/**
 * Wire shapes for inventory Phase 3 — transfers, the in-transit report, reservations, adjustment
 * requests and the approval thresholds. Mirrors `services/operations/app/movements/schemas.py`.
 *
 * Every quantity is a decimal **string** with at most three decimals (`Qty`), never a JS number.
 */

import type { Qty } from "./types";

/** `?cursor=&limit=` pages: the server hands back the cursor for the next page, or `null`. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

/* ------------------------------ control settings ------------------------------ */

export interface ControlSettingsOut {
  transfer_approval_threshold: Qty | null;
  adjustment_approval_threshold: Qty | null;
  updated_at: string | null;
  updated_by: string | null;
}

/** `PUT /inventory/control-settings` replaces both; `null` clears one. */
export interface ControlSettingsIn {
  transfer_approval_threshold: Qty | null;
  adjustment_approval_threshold: Qty | null;
}

/* ---------------------------------- transfers --------------------------------- */

export type TransferStatus =
  | "DRAFT"
  | "REQUESTED"
  | "APPROVED"
  | "IN_TRANSIT"
  | "RECEIVED"
  | "CANCELLED"
  | "REJECTED";

export type ApprovalRoute = "AUTO" | "DIRECT" | "SERVICE";

export interface TransferLineIn {
  inventory_item_id: string;
  from_location_id: string;
  quantity: Qty;
  lot_id?: string | null;
  roll_id?: string | null;
}

export interface TransferIn {
  from_warehouse_id: string;
  to_warehouse_id: string;
  lines: TransferLineIn[];
  note?: string;
  expected_on?: string | null;
}

/** `PATCH` — a DRAFT only. `lines`, when present, replaces them. */
export interface TransferPatch {
  to_warehouse_id?: string;
  lines?: TransferLineIn[];
  note?: string;
  expected_on?: string | null;
}

export interface TransferDecisionIn {
  comment?: string;
}

export interface TransferCancelIn {
  reason: string;
}

export interface TransferDispatchIn {
  note?: string;
  expected_on?: string | null;
}

export interface TransferReceiveLineIn {
  line_id: string;
  received_quantity: Qty;
  shortage_quantity: Qty;
  to_location_id?: string | null;
}

export interface TransferReceiveIn {
  lines: TransferReceiveLineIn[];
  note?: string;
}

export interface WarehouseRefOut {
  id: string;
  code: string;
  name: string;
}

export interface TransferLineOut {
  id: string;
  line_no: number;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  from_location_id: string;
  from_location_code: string;
  to_location_id: string | null;
  in_transit_location_id: string | null;
  lot_id: string | null;
  roll_id: string | null;
  quantity: Qty;
  dispatched: Qty;
  received: Qty;
  shortage: Qty;
  /** dispatched - received - shortage: still on its way. */
  in_transit: Qty;
}

export interface TransferTransitionOut {
  from_status: TransferStatus | null;
  to_status: TransferStatus;
  actor: string;
  note: string;
  created_at: string;
}

export interface TransferSummaryOut {
  id: string;
  number: string;
  status: TransferStatus;
  from_warehouse: WarehouseRefOut;
  to_warehouse: WarehouseRefOut;
  line_count: number;
  approval_route: ApprovalRoute | null;
  expected_on: string | null;
  requested_at: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface TransferOut extends TransferSummaryOut {
  note: string;
  approval_request_id: string | null;
  approver_name: string | null;
  requested_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  decision_comment: string;
  dispatched_by: string | null;
  cancelled_at: string | null;
  cancel_reason: string;
  awaiting_approval: boolean;
  lines: TransferLineOut[];
  transitions: TransferTransitionOut[];
  replayed: boolean;
}

export interface InTransitRowOut {
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  warehouse_id: string;
  warehouse_code: string;
  in_transit: Qty;
  /** On hand in that warehouse — reported beside `in_transit`, never summed with it. */
  physical: Qty;
}

/* -------------------------------- reservations -------------------------------- */

export type ReservationReference = "order" | "production_order" | "manual";
export type ReservationStatus = "active" | "released" | "consumed" | "transferred";

export interface ReservationIn {
  inventory_item_id: string;
  location_id: string;
  quantity: Qty;
  lot_id?: string | null;
  roll_id?: string | null;
  reference_type?: ReservationReference;
  reference_id?: string | null;
  expires_at?: string | null;
  note?: string;
}

export interface ReservationDetailOut {
  id: string;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  location_id: string;
  location_code: string;
  lot_id: string | null;
  roll_id: string | null;
  quantity: Qty;
  consumed: Qty;
  released: Qty;
  transferred: Qty;
  remaining: Qty;
  status: ReservationStatus;
  reference_type: string | null;
  reference_id: string | null;
  expires_at: string | null;
  expired_at: string | null;
  note: string;
  created_at: string;
  closed_at: string | null;
  replayed: boolean;
}

/** `POST /inventory/reservations/{id}:release`; an empty body releases the whole remainder. */
export interface ReservationReleaseIn {
  quantity?: Qty;
}

/** What `:release` answers with — the reservation's quantities, without the item/location labels. */
export interface ReservationReleaseOut {
  id: string;
  inventory_item_id: string;
  quantity: Qty;
  consumed: Qty;
  released: Qty;
  transferred: Qty;
  remaining: Qty;
  status: ReservationStatus;
  reference_type: string | null;
  reference_id: string | null;
}

/* --------------------------------- adjustments -------------------------------- */

export type AdjustmentStatus = "PENDING_APPROVAL" | "APPLIED" | "REJECTED" | "CANCELLED" | "FAILED";
export type AdjustmentReason = "damaged" | "lost" | "found" | "consumed" | "returned" | "other";

export interface AdjustmentIn {
  inventory_item_id: string;
  location_id: string;
  quantity_delta: Qty;
  reason: AdjustmentReason;
  status?: string;
  lot_id?: string | null;
  roll_id?: string | null;
  note?: string;
  evidence_document_id?: string | null;
  occurred_on?: string;
}

export interface AdjustmentCancelIn {
  reason?: string;
}

export interface AdjustmentOut {
  id: string;
  number: string;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  location_id: string;
  location_code: string;
  stock_status: string;
  lot_id: string | null;
  roll_id: string | null;
  quantity_delta: Qty;
  reason: AdjustmentReason;
  note: string;
  evidence_document_id: string | null;
  occurred_on: string;
  status: AdjustmentStatus;
  approval_required: boolean;
  approval_request_id: string | null;
  approver_name: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_comment: string;
  requested_by: string;
  applied_move_id: string | null;
  applied_at: string | null;
  failure_reason: string;
  created_at: string;
  replayed: boolean;
}
