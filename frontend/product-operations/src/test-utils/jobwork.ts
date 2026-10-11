import type {
  ChallanOut,
  JobWorkMaterialOut,
  JobWorkOrderDetailOut,
  JobWorkOrderOut,
  JobWorkReconciliationOut,
  Page,
} from "@/api/types";

export function material(overrides: Partial<JobWorkMaterialOut> = {}): JobWorkMaterialOut {
  return {
    id: "mat-1",
    line_no: 1,
    item_id: "item-1",
    sku: "FAB-GREIGE",
    uom: "m",
    lot_id: null,
    roll_id: null,
    location_id: "loc-1",
    quantity: "82.5",
    sent: "82.5",
    received: "0",
    rejected: "0",
    shortage: "0",
    outstanding: "82.5",
    unit_value_minor: 40000,
    currency: "INR",
    ...overrides,
  };
}

export function reconciliation(
  overrides: Partial<JobWorkReconciliationOut> = {},
): JobWorkReconciliationOut {
  return {
    order_id: "jw-1",
    order_status: "ISSUED",
    issued: true,
    lines: [],
    total_sent: "82.5",
    total_received: "0",
    total_rejected: "0",
    total_shortage: "0",
    total_outstanding: "82.5",
    documents_settled: false,
    ledger_consistent: true,
    drift: [],
    balanced: false,
    ...overrides,
  };
}

export function challan(overrides: Partial<ChallanOut> = {}): ChallanOut {
  return {
    id: "ch-1",
    order_id: "jw-1",
    order_number: "JW-2026-0001",
    challan_number: "DC/26-27/0001",
    kind: "DELIVERY",
    issued_at: "2026-10-01T09:30:00Z",
    supplier_id: "sup-1",
    supplier_name: "Rainbow Dyers",
    process: "Dyeing",
    lines: [
      {
        sku: "FAB-GREIGE",
        uom: "m",
        quantity: "82.5",
        unit_value_minor: 40000,
        value_minor: 3300000,
        lot_id: null,
        roll_id: null,
      },
    ],
    total_value_minor: 3300000,
    currency: "INR",
    vehicle_no: "MH12AB1234",
    transporter_id: null,
    distance_km: 90,
    eway_status: "NOT_REQUIRED",
    eway_bill_no: null,
    eway_requested_at: null,
    ...overrides,
  };
}

export function orderSummary(overrides: Partial<JobWorkOrderOut> = {}): JobWorkOrderOut {
  return {
    id: "jw-1",
    number: "JW-2026-0001",
    supplier_id: "sup-1",
    supplier_name: "Rainbow Dyers",
    process: "Dyeing",
    status: "ISSUED",
    due_date: "2026-10-20",
    is_overdue: false,
    days_overdue: 0,
    qc_required: false,
    notes: "",
    version: 3,
    source_warehouse_id: null,
    receive_location_id: null,
    material_count: 1,
    total_sent: "82.5",
    total_outstanding: "82.5",
    issued_at: "2026-10-01T09:30:00Z",
    closed_at: null,
    created_at: "2026-09-30T09:00:00Z",
    updated_at: "2026-10-01T09:30:00Z",
    ...overrides,
  };
}

export function orderDetail(overrides: Partial<JobWorkOrderDetailOut> = {}): JobWorkOrderDetailOut {
  return {
    ...orderSummary(),
    materials: [material()],
    receipts: [],
    challans: [challan()],
    reconciliation: reconciliation(),
    ...overrides,
  };
}

export function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length === 0 ? 0 : 1 };
}
