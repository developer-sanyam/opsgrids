import type {
  AdjustmentOut,
  ReservationDetailOut,
  TransferLineOut,
  TransferOut,
  TransferSummaryOut,
} from "@/api/movementTypes";
import type { InventoryItemOut, LocationOut } from "@/api/types";

export const WH_SRC = { id: "wh-src", code: "WH-BHW", name: "Bhiwandi DC" };
export const WH_DST = { id: "wh-dst", code: "WH-PUN", name: "Pune Plant" };

export function transferLine(overrides: Partial<TransferLineOut> = {}): TransferLineOut {
  return {
    id: "line-1",
    line_no: 1,
    inventory_item_id: "item-1",
    sku: "RM-STL-4501",
    item_name: "Cold-rolled steel coil",
    uom: "MT",
    from_location_id: "bin-src",
    from_location_code: "A-01",
    to_location_id: null,
    in_transit_location_id: null,
    lot_id: null,
    roll_id: null,
    quantity: "40",
    dispatched: "0",
    received: "0",
    shortage: "0",
    in_transit: "0",
    ...overrides,
  };
}

export function transfer(overrides: Partial<TransferOut> = {}): TransferOut {
  return {
    id: "trf-1",
    number: "TRF-000001",
    status: "DRAFT",
    from_warehouse: WH_SRC,
    to_warehouse: WH_DST,
    line_count: 1,
    approval_route: null,
    expected_on: null,
    requested_at: null,
    dispatched_at: null,
    completed_at: null,
    created_at: "2026-10-01T08:00:00Z",
    note: "",
    approval_request_id: null,
    approver_name: null,
    requested_by: null,
    approved_by: null,
    approved_at: null,
    decision_comment: "",
    dispatched_by: null,
    cancelled_at: null,
    cancel_reason: "",
    awaiting_approval: false,
    lines: [transferLine()],
    transitions: [
      { from_status: null, to_status: "DRAFT", actor: "Asha Rao", note: "", created_at: "2026-10-01T08:00:00Z" },
    ],
    replayed: false,
    ...overrides,
  };
}

export function summary(overrides: Partial<TransferSummaryOut> = {}): TransferSummaryOut {
  const { id, number, status, from_warehouse, to_warehouse, approval_route, expected_on, created_at } = transfer();
  return {
    id,
    number,
    status,
    from_warehouse,
    to_warehouse,
    line_count: 1,
    approval_route,
    expected_on,
    requested_at: null,
    dispatched_at: null,
    completed_at: null,
    created_at,
    ...overrides,
  };
}

export function reservation(overrides: Partial<ReservationDetailOut> = {}): ReservationDetailOut {
  return {
    id: "res-1",
    inventory_item_id: "item-1",
    sku: "RM-STL-4501",
    item_name: "Cold-rolled steel coil",
    uom: "MT",
    location_id: "bin-src",
    location_code: "A-01",
    lot_id: null,
    roll_id: null,
    quantity: "20",
    consumed: "0",
    released: "0",
    transferred: "0",
    remaining: "20",
    status: "active",
    reference_type: "manual",
    reference_id: null,
    expires_at: null,
    expired_at: null,
    note: "",
    created_at: "2026-10-01T09:00:00Z",
    closed_at: null,
    replayed: false,
    ...overrides,
  };
}

export function adjustment(overrides: Partial<AdjustmentOut> = {}): AdjustmentOut {
  return {
    id: "adj-1",
    number: "ADJ-000012",
    inventory_item_id: "item-1",
    sku: "RM-STL-4501",
    item_name: "Cold-rolled steel coil",
    uom: "MT",
    location_id: "bin-src",
    location_code: "A-01",
    stock_status: "AVAILABLE",
    lot_id: null,
    roll_id: null,
    quantity_delta: "-5",
    reason: "damaged",
    note: "",
    evidence_document_id: null,
    occurred_on: "2026-10-04",
    status: "APPLIED",
    approval_required: false,
    approval_request_id: null,
    approver_name: null,
    decided_by_name: null,
    decided_at: null,
    decision_comment: "",
    requested_by: "Asha Rao",
    applied_move_id: null,
    applied_at: null,
    failure_reason: "",
    created_at: "2026-10-04T09:00:00Z",
    replayed: false,
    ...overrides,
  };
}

export const ITEM: InventoryItemOut = {
  id: "item-1",
  sku: "RM-STL-4501",
  name: "Cold-rolled steel coil",
  category: "raw_materials",
  warehouse: "Bhiwandi DC",
  on_hand: 82,
  on_hand_qty: "82.5",
  reserved_qty: "10",
  available_qty: "72.5",
  reorder_point: 40,
  uom: "MT",
  unit_value_minor: 6240000,
  currency: "INR",
  updated_at: "2026-08-23",
  item_category_id: null,
  secondary_uom_id: null,
  hsn_code: "",
  track_lot: false,
  track_roll: false,
  track_expiry: false,
  track_quality: false,
  is_active: true,
  receipt_tolerance_pct: "0",
  selling_price_minor: null,
  selling_price_currency: null,
};

export function location(overrides: Partial<LocationOut> = {}): LocationOut {
  return {
    id: "bin-src",
    warehouse_id: "wh-src",
    parent_id: null,
    kind: "bin",
    code: "A-01",
    name: "Bin A-01",
    is_default: false,
    barcode: null,
    pick_sequence: null,
    max_weight_grams: null,
    max_volume_milli: null,
    storage_type: null,
    ...overrides,
  };
}
