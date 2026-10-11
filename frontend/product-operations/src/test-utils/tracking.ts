/** Builders for the Phase 1 wire shapes and for the problem documents the server refuses with. */

import { ApiError } from "@opsgrids/client";
import type { LotOut, RollOut, StockMoveOut, StockRowOut } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";

export function item(overrides: Partial<InventoryItemOut> = {}): InventoryItemOut {
  return {
    id: "item-1",
    sku: "FAB-100",
    name: "Cotton poplin",
    category: "raw_materials",
    warehouse: "Bhiwandi DC",
    on_hand: 0,
    on_hand_qty: "0",
    reserved_qty: "0",
    available_qty: "0",
    reorder_point: 0,
    uom: "m",
    unit_value_minor: 0,
    currency: "INR",
    updated_at: "2026-10-01T00:00:00Z",
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
    ...overrides,
  };
}

export function stockRow(overrides: Partial<StockRowOut> = {}): StockRowOut {
  return {
    id: "bal-1",
    inventory_item_id: "item-1",
    sku: "FAB-100",
    item_name: "Cotton poplin",
    uom: "m",
    warehouse_id: "wh-1",
    warehouse_code: "WH-1",
    location_id: "loc-1",
    location_code: "A-01",
    location_kind: "bin",
    status: "AVAILABLE",
    lot_id: null,
    lot_no: null,
    roll_id: null,
    roll_no: null,
    on_hand_qty: "82.5",
    reserved_qty: "0",
    available_qty: "82.5",
    ...overrides,
  };
}

export function roll(overrides: Partial<RollOut> = {}): RollOut {
  return {
    id: "roll-1",
    roll_no: "R-001",
    inventory_item_id: "item-1",
    sku: "FAB-100",
    uom: "m",
    lot_id: "lot-1",
    lot_no: "LOT-1",
    state: "ACTIVE",
    primary_qty: "82.5",
    secondary_qty: "14.8",
    secondary_uom: "kg",
    width_mm: 1500,
    gsm: 120,
    shade: "Indigo",
    grade: "A",
    parent_roll_id: null,
    on_hand_qty: "82.5",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

export function lot(overrides: Partial<LotOut> = {}): LotOut {
  return {
    id: "lot-1",
    lot_no: "LOT-1",
    supplier_lot_no: null,
    inventory_item_id: "item-1",
    sku: "FAB-100",
    uom: "m",
    manufactured_on: null,
    received_on: null,
    expires_on: null,
    quality_status: "PENDING",
    source_type: null,
    source_id: null,
    attributes: {},
    on_hand_qty: "0",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

export function move(overrides: Partial<StockMoveOut> = {}): StockMoveOut {
  return {
    move_id: "move-1",
    kind: "relocation",
    reference_type: null,
    reference_id: null,
    occurred_on: "2026-10-01",
    replayed: false,
    lines: [],
    ...overrides,
  };
}

/** A 422 / 409 the way `ProblemError(detail, errors=[…])` sends it. */
export function problem(
  status: number,
  detail: string,
  errors: { field: string; code: string; message: string }[],
  type = "validation",
): ApiError {
  return new ApiError(status, detail, {
    type: `https://errors.opsgrids.dev/${type}`,
    title: detail,
    status,
    detail,
    errors,
  });
}

/** The 409 `insufficient-stock` the kernel raises, with the reservations that stand in the way. */
export function insufficientStock(reservations: unknown[]): ApiError {
  const detail =
    "Cannot take 40 m of FAB-100 from A-01: 82.5 m on hand, 60 m reserved, so 22.5 m available. Release the reservation(s) blocking it, then try again";
  return new ApiError(409, detail, {
    type: "https://errors.opsgrids.dev/insufficient-stock",
    title: "Not enough stock",
    status: 409,
    detail,
    errors: [
      {
        sku: "FAB-100",
        location: "A-01",
        status: "AVAILABLE",
        uom: "m",
        requested: "40",
        on_hand: "82.5",
        reserved: "60",
        available: "22.5",
        reservations,
      },
    ] as unknown as { field: string; code: string; message: string }[],
  });
}
