import type { InspectionOut, Page } from "@/api/types";

/** One inspection exactly as `GET /operations/quality/inspections/{id}` answers it. */
export function inspectionFixture(over: Partial<InspectionOut> = {}): InspectionOut {
  return {
    id: "insp-1",
    kind: "incoming",
    status: "PENDING",
    template_id: null,
    item_id: "item-1",
    item_sku: "FAB-001",
    item_name: "Cotton twill",
    uom: "m",
    location_id: "loc-1",
    location_code: "WH-A/BIN-1",
    lot_id: null,
    roll_id: null,
    supplier_id: null,
    reference_type: "",
    reference_id: null,
    quantity: "40.125",
    accepted: null,
    rejected: null,
    rejected_bucket: null,
    notes: "",
    decision_note: "",
    decided_by: null,
    decided_at: null,
    disposition: "NONE",
    disposition_note: "",
    disposition_reference: "",
    disposed_at: null,
    hold_move_id: "move-1",
    decision_move_id: null,
    disposition_move_id: null,
    suggested_outcome: "PENDING",
    can_release: true,
    results: [],
    created_by: "user-1",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    ...over,
  };
}

export function pageOf<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}
