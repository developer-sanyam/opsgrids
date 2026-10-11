import type {
  MrpDemandOut,
  MrpDemandSourcesOut,
  MrpRunOut,
  MrpShortageDetailOut,
  MrpShortageOut,
  RequisitionRequestOut,
} from "@/api/types";

/** Builders for the MRP wire shapes, so a test states only what it cares about. */

export function run(overrides: Partial<MrpRunOut> = {}): MrpRunOut {
  return {
    id: "run-1",
    number: "MRP-00001",
    status: "COMPLETED",
    execution: "SYNC",
    plan_date: "2026-10-05",
    sources: ["SALES_ORDER", "PRODUCTION_ORDER"],
    notes: "",
    demand_count: 3,
    shortage_count: 2,
    items_planned: 5,
    result_hash: "a".repeat(64),
    error: "",
    requested_by: "user-1",
    created_at: "2026-10-05T09:00:00Z",
    completed_at: "2026-10-05T09:00:01Z",
    ...overrides,
  };
}

export function shortage(overrides: Partial<MrpShortageOut> = {}): MrpShortageOut {
  return {
    id: "sh-1",
    run_id: "run-1",
    item_id: "item-btn",
    sku: "BTN-14MM",
    name: "Button, 14 mm",
    uom: "pcs",
    action: "BUY",
    level: 1,
    gross: "50",
    on_hand: "0",
    reserved: "0",
    available: "0",
    open_po: "15",
    open_production: "0",
    supply: "15",
    shortage: "35",
    need_by: "2026-11-01",
    requisition_request_id: null,
    requisition_requested_at: null,
    requisition_id: null,
    ...overrides,
  };
}

export function shortageDetail(overrides: Partial<MrpShortageDetailOut> = {}): MrpShortageDetailOut {
  return {
    ...shortage(),
    lines: [
      {
        source_type: "SALES_ORDER",
        source_id: "so-1",
        source_ref: "SO-1",
        parent_item_id: "item-shirt",
        parent_sku: "SHIRT-01",
        quantity: "50",
        due_date: "2026-11-01",
      },
    ],
    ...overrides,
  };
}

export function demand(overrides: Partial<MrpDemandOut> = {}): MrpDemandOut {
  return {
    id: "d-1",
    source_type: "SALES_ORDER",
    source_id: "so-1",
    source_ref: "SO-1",
    item_id: "item-shirt",
    sku: "SHIRT-01",
    name: "Cotton shirt",
    uom: "pcs",
    requested: "10",
    reserved: "2",
    quantity: "8",
    due_date: null,
    note: "",
    ...overrides,
  };
}

export function requisition(overrides: Partial<RequisitionRequestOut> = {}): RequisitionRequestOut {
  return {
    request_id: "9f8e7d6c-0000-4000-8000-000000000000",
    run_id: "run-1",
    run_number: "MRP-00001",
    replayed: false,
    lines: [
      { shortage_id: "sh-1", item_id: "item-btn", sku: "BTN-14MM", uom: "pcs", quantity: "35", need_by: "2026-11-01" },
    ],
    ...overrides,
  };
}

export function demandSources(overrides: Partial<MrpDemandSourcesOut> = {}): MrpDemandSourcesOut {
  return {
    sales_order_lines: 4,
    production_order_materials: 2,
    sync_limit: 200,
    mode_if_auto: "sync",
    ...overrides,
  };
}
