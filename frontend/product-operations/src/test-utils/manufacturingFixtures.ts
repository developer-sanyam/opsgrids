import type {
  BomComponentOut,
  BomOut,
  BomVersionDetailOut,
  Page,
  ProductionMaterialOut,
  ProductionOrderDetailOut,
  ProductionOrderOut,
  ProductionPickOut,
} from "@/api/types";

/** Builders for the manufacturing wire shapes, so a test states only what it cares about. */

export function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length === 0 ? 0 : 1 };
}

export function bom(overrides: Partial<BomOut> = {}): BomOut {
  return {
    id: "bom-1",
    code: "SHIRT-01",
    name: "Cotton shirt",
    item_id: "item-shirt",
    sku: "SHIRT",
    item_name: "Shirt",
    uom: "pc",
    description: "",
    is_active: true,
    version_count: 2,
    latest_version_no: 2,
    current_version_id: "ver-1",
    current_version_no: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

export function component(overrides: Partial<BomComponentOut> = {}): BomComponentOut {
  return {
    id: "cmp-1",
    line_no: 1,
    component_item_id: "item-fabric",
    sku: "FABRIC",
    name: "Cotton fabric",
    uom: "m",
    quantity: "1.5",
    scrap_percent: "2.5",
    effective_from: null,
    effective_to: null,
    notes: "",
    is_produced: false,
    substitutes: [],
    ...overrides,
  };
}

export function version(overrides: Partial<BomVersionDetailOut> = {}): BomVersionDetailOut {
  return {
    id: "ver-2",
    bom_id: "bom-1",
    version_no: 2,
    status: "DRAFT",
    output_quantity: "10",
    effective_from: null,
    effective_to: null,
    notes: "",
    published_at: null,
    published_by: null,
    content_hash: null,
    component_count: 1,
    created_at: "2026-09-02T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    components: [component()],
    ...overrides,
  };
}

export function material(overrides: Partial<ProductionMaterialOut> = {}): ProductionMaterialOut {
  return {
    id: "mat-1",
    line_no: 1,
    component_item_id: "item-fabric",
    component_sku: "FABRIC",
    item_id: "item-fabric",
    sku: "FABRIC",
    name: "Cotton fabric",
    uom: "m",
    is_substitute: false,
    ratio: "1",
    scrap_percent: "2.5",
    required: "15.375",
    reserved: "0",
    issued: "0",
    consumed: "0",
    returned: "0",
    wasted: "0",
    wip: "0",
    open_need: "15.375",
    ...overrides,
  };
}

export function pick(overrides: Partial<ProductionPickOut> = {}): ProductionPickOut {
  return {
    id: "pick-1",
    material_id: "mat-1",
    item_id: "item-fabric",
    source_location_id: "loc-1",
    wip_location_id: "loc-wip",
    lot_id: null,
    roll_id: null,
    reservation_id: null,
    reserved: "0",
    issued: "0",
    consumed: "0",
    returned: "0",
    wasted: "0",
    wip: "0",
    ...overrides,
  };
}

export function orderSummary(overrides: Partial<ProductionOrderOut> = {}): ProductionOrderOut {
  return {
    id: "ord-1",
    number: "PO-0001",
    status: "DRAFT",
    bom_id: "bom-1",
    bom_code: "SHIRT-01",
    bom_version_id: "ver-1",
    bom_version_no: 1,
    item_id: "item-shirt",
    sku: "SHIRT",
    name: "Shirt",
    uom: "pc",
    planned_quantity: "10",
    produced_quantity: "0",
    output_location_id: "loc-out",
    wip_location_id: null,
    planned_start: null,
    due_date: "2026-10-20",
    approved_at: null,
    started_at: null,
    completed_at: null,
    cancelled_at: null,
    cancel_reason: "",
    notes: "",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

export function order(overrides: Partial<ProductionOrderDetailOut> = {}): ProductionOrderDetailOut {
  return {
    ...orderSummary(),
    materials: [material()],
    picks: [],
    outputs: [],
    wastage: [],
    variance: { planned_quantity: "10", produced_quantity: "0", lines: [] },
    ...overrides,
  };
}

/** The warehouse and location answers every order screen asks for to label its locations. */
export const WAREHOUSE_PAGE = page([{ id: "wh-1", code: "WH1", name: "Main", location: "Pune" }]);
export const LOCATIONS_PAGE = page([
  { id: "loc-out", warehouse_id: "wh-1", parent_id: null, kind: "bin", code: "FG-1", name: "Finished goods", is_default: false, barcode: null, pick_sequence: null, max_weight_grams: null, max_volume_milli: null, storage_type: null },
  { id: "loc-1", warehouse_id: "wh-1", parent_id: null, kind: "bin", code: "RM-1", name: "Raw material", is_default: false, barcode: null, pick_sequence: null, max_weight_grams: null, max_volume_milli: null, storage_type: null },
]);
