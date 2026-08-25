/**
 * Typed Operations API client.
 *
 * Live by default: every function calls the operations service through the gateway at the
 * same-origin `/api/v1/operations/...` prefix (Vite proxies it to `http://localhost:8100` in
 * dev) with the bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole
 * app from `fixtures.ts` with no login — handy for offline design work.
 */

import { apiRequest } from "./http";
import { INVENTORY, PURCHASE_ORDERS, SUPPLIERS } from "./fixtures";
import type {
  InventoryItemOut,
  Page,
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

export async function listPurchaseOrders(): Promise<Page<PurchaseOrderOut>> {
  if (USE_FIXTURES) return settle({ items: PURCHASE_ORDERS, total: PURCHASE_ORDERS.length });
  return apiRequest<Page<PurchaseOrderOut>>(`/operations/purchase-orders?size=${FULL_PAGE}`);
}

export async function getPurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const found = PURCHASE_ORDERS.find((p) => p.id === id);
    if (!found) throw new Error(`Purchase order ${id} not found`);
    return settle(found);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}`);
}

/* -------------------------------- inventory ------------------------------- */

export async function listInventory(): Promise<Page<InventoryItemOut>> {
  if (USE_FIXTURES) return settle({ items: INVENTORY, total: INVENTORY.length });
  return apiRequest<Page<InventoryItemOut>>(`/operations/inventory?size=${FULL_PAGE}`);
}
