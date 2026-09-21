/**
 * Typed Operations API client.
 *
 * Live by default: every function calls the operations service through the gateway at the
 * same-origin `/api/v1/operations/...` prefix (Vite proxies it to `http://localhost:8100` in
 * dev) with the bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole
 * app from `fixtures.ts` with no login — handy for offline design work.
 */

import { apiRequest } from "@opsgrids/client";
import { INVENTORY, PURCHASE_ORDERS, SUPPLIERS, TODAY } from "./fixtures";
import type {
  InventoryItemOut,
  Page,
  PurchaseOrderCreateIn,
  PurchaseOrderOut,
  SupplierOut,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/**
 * The Overview's trend history (KPI deltas / sparklines and the spend-over-time series) has no
 * reporting service yet, so it is representative rather than live. When the app runs live those
 * surfaces carry a "Sample data" chip; in fixture mode the whole app is illustrative, so nothing
 * is singled out.
 */
export const TRENDS_ARE_SAMPLE = LIVE;

/** A generous page size — one request returns the whole (small) working set for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* -------------------------------- suppliers ------------------------------- */

export async function listSuppliers(): Promise<Page<SupplierOut>> {
  if (USE_FIXTURES) return settle({ items: SUPPLIERS, total: SUPPLIERS.length });
  return apiRequest<Page<SupplierOut>>(`/operations/suppliers?size=${FULL_PAGE}`);
}

export async function getSupplier(id: string): Promise<SupplierOut> {
  if (USE_FIXTURES) {
    const found = SUPPLIERS.find((s) => s.id === id);
    if (!found) throw new Error(`Supplier ${id} not found`);
    return settle(found);
  }
  return apiRequest<SupplierOut>(`/operations/suppliers/${encodeURIComponent(id)}`);
}

/* ----------------------------- purchase orders ---------------------------- */

/** Fixture-mode purchase orders, mutable: a created PO is prepended here so the list — and a
 *  later `getPurchaseOrder` — reflect it without a page reload. `fixtures.ts`'s own export stays
 *  the frozen seed; this is the working copy this module owns. */
let purchaseOrdersFixture: PurchaseOrderOut[] = [...PURCHASE_ORDERS];

export async function listPurchaseOrders(): Promise<Page<PurchaseOrderOut>> {
  if (USE_FIXTURES) {
    return settle({ items: purchaseOrdersFixture, total: purchaseOrdersFixture.length });
  }
  return apiRequest<Page<PurchaseOrderOut>>(`/operations/purchase-orders?size=${FULL_PAGE}`);
}

export async function getPurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const found = purchaseOrdersFixture.find((p) => p.id === id);
    if (!found) throw new Error(`Purchase order ${id} not found`);
    return settle(found);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}`);
}

/**
 * Raise a purchase order (`PurchaseOrderIn` → `PurchaseOrderOut`,
 * `services/operations/app/schemas.py`). The caller — `NewPurchaseOrderDialog`, via
 * `purchaseOrderForm.ts`'s `validatePurchaseOrder` / `buildPurchaseOrderPayload` — has already
 * validated the form; the two checks below re-verify what a client can confirm without a round
 * trip (a supplier was named, there is at least one line), because client validation mirrors the
 * server's rules and never replaces them — the server checks again regardless.
 */
export async function createPurchaseOrder(input: PurchaseOrderCreateIn): Promise<PurchaseOrderOut> {
  if (!input.supplier_code && !input.supplier_id) {
    throw new Error("Name the supplier with supplier_id or supplier_code.");
  }
  if (input.lines.length === 0) {
    throw new Error("Add at least one line item.");
  }

  if (USE_FIXTURES) {
    const supplier = input.supplier_code
      ? SUPPLIERS.find((s) => s.code === input.supplier_code)
      : SUPPLIERS.find((s) => s.id === input.supplier_id);
    if (!supplier) {
      throw new Error(`Unknown supplier: ${input.supplier_code ?? input.supplier_id ?? ""}`);
    }

    const stamp = Date.now().toString(36);
    const created: PurchaseOrderOut = {
      id: `po-${stamp}`,
      po_number: input.number,
      supplier_id: supplier.id,
      supplier_name: supplier.name,
      category: supplier.category,
      status: input.status,
      currency: input.currency,
      warehouse: input.warehouse,
      raised_by: "",
      created_date: TODAY,
      expected_date: input.expected_date,
      received_date: null,
      notes: input.notes ?? null,
      line_items: input.lines.map((line, i) => ({
        id: `po-${stamp}-${i}`,
        sku: line.sku,
        description: line.description,
        quantity: line.quantity,
        uom: line.uom,
        unit_price_minor: line.unit_price_minor,
        received_qty: 0,
      })),
    };
    purchaseOrdersFixture = [created, ...purchaseOrdersFixture];
    return settle(created, 320);
  }

  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders`, {
    method: "POST",
    body: input,
  });
}

/* -------------------------------- inventory ------------------------------- */

export async function listInventory(): Promise<Page<InventoryItemOut>> {
  if (USE_FIXTURES) return settle({ items: INVENTORY, total: INVENTORY.length });
  return apiRequest<Page<InventoryItemOut>>(`/operations/inventory?size=${FULL_PAGE}`);
}
