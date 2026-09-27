/**
 * Test support for the import definitions: a stubbed gateway that answers the api client's
 * requests by URL and records them, the paged-list body every list endpoint returns, and the few
 * reference records the lookups are built from. The rules of a definition (its columns, sample
 * rows and payloads) are checked by the engine's own `checkImportEntity` in
 * `checkEntities.test.ts`.
 */
import { vi } from "vitest";

import type { InventoryItemOut, ItemCategoryOut, SupplierOut, UomOut, WarehouseOut } from "@/api/types";

export interface RecordedRequest {
  url: URL;
  init: RequestInit;
}

/**
 * Stubs `fetch` with a handler that answers by URL and records every request, so a test drives the
 * real api client end to end and asserts the method, query and JSON body the gateway would receive.
 */
export function stubGateway(
  respond: (request: RecordedRequest) => { body: unknown; status?: number },
): RecordedRequest[] {
  const recorded: RecordedRequest[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init: RequestInit) => {
      const request = { url: new URL(input, "http://gateway.test"), init };
      recorded.push(request);
      const { body, status = 200 } = respond(request);
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        }),
      );
    }),
  );
  return recorded;
}

export function pageOf<T>(items: T[], page = 1, pages = 1): {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
} {
  return { items, total: items.length, page, size: 200, pages };
}

/** One page of a list held as pages: the body a paged endpoint answers with, `total` counting every page. */
export function pageAt<T>(pages: readonly (readonly T[])[], page: number) {
  return {
    items: pages[page - 1] ?? [],
    total: pages.reduce((sum, items) => sum + items.length, 0),
    page,
    size: 200,
    pages: Math.max(pages.length, 1),
  };
}

/** The requests a stub recorded for one method and path, in the order they were made. */
export function requestsTo(recorded: readonly RecordedRequest[], method: string, path: string): RecordedRequest[] {
  return recorded.filter((request) => (request.init.method ?? "GET") === method && request.url.pathname === path);
}

export function warehouseOut(name: string, code: string): WarehouseOut {
  return { id: `wh-${code}`, code, name, location: "" };
}

export function uomOut(code: string, name: string, isSystem: boolean): UomOut {
  return { id: `uom-${code}`, code, name, dimension: "count", is_discrete: false, is_system: isSystem };
}

export function itemCategoryOut(code: string, id: string, parentId: string | null = null): ItemCategoryOut {
  return { id, code, name: code, parent_id: parentId };
}

/** A stored inventory item, as the list returns it: steel coil in the main warehouse, no stock
 *  moved, every flag off. A test overrides only what it is about. */
export function inventoryItemOut(overrides: Partial<InventoryItemOut> = {}): InventoryItemOut {
  return {
    id: "inv-1",
    sku: "RM-STL-4501",
    name: "Cold-rolled steel coil",
    category: "raw_materials",
    warehouse: "Main Warehouse",
    on_hand: 8200,
    on_hand_qty: "8200",
    reserved_qty: "0",
    available_qty: "8200",
    reorder_point: 4000,
    uom: "kg",
    unit_value_minor: 6240,
    currency: "INR",
    updated_at: "2026-09-01T00:00:00Z",
    item_category_id: "cat-steel",
    secondary_uom_id: null,
    hsn_code: "7209",
    track_lot: false,
    track_roll: false,
    track_expiry: false,
    track_quality: false,
    is_active: true,
    ...overrides,
  };
}

/** A stored supplier, as the list returns it. */
export function supplierOut(overrides: Partial<SupplierOut> = {}): SupplierOut {
  return {
    id: "sup-1",
    code: "SUP-MER",
    name: "Meridian Metals",
    category: "raw_materials",
    status: "active",
    lead_time_days: 12,
    on_time_pct: 96,
    spend_minor: 0,
    currency: "INR",
    open_pos: 0,
    contact_name: "Rajesh Pillai",
    contact_email: "rajesh@meridianmetals.example",
    location: "Jamshedpur",
    since: "2019-04-02",
    ...overrides,
  };
}
