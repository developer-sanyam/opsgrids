/**
 * Typed Operations API client.
 *
 * Fixtures-first: the Operations backend is a skeleton, so `USE_FIXTURES` defaults **true** and
 * the whole app renders from `fixtures.ts` with no login. Flip `VITE_USE_FIXTURES=false` once the
 * service ships and every function falls through to the gateway at the same-origin `/api/v1`
 * prefix (Vite proxies it to `http://localhost:8100` in dev) with the bearer token from the
 * session — so wiring the live backend is a flag flip, not a rewrite.
 */

import { apiRequest } from "./http";
import { INVENTORY, PURCHASE_ORDERS, SUPPLIERS } from "./fixtures";
import type {
  InventoryItemOut,
  Page,
  PurchaseOrderOut,
  SupplierOut,
} from "./types";

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` points the app at the gateway. */
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) working set for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* -------------------------------- suppliers ------------------------------- */

export async function listSuppliers(): Promise<Page<SupplierOut>> {
  if (USE_FIXTURES) return settle({ items: SUPPLIERS, total: SUPPLIERS.length });
  return apiRequest<Page<SupplierOut>>(`/suppliers?size=${FULL_PAGE}`);
}

export async function getSupplier(id: string): Promise<SupplierOut> {
  if (USE_FIXTURES) {
    const found = SUPPLIERS.find((s) => s.id === id);
    if (!found) throw new Error(`Supplier ${id} not found`);
    return settle(found);
  }
  return apiRequest<SupplierOut>(`/suppliers/${id}`);
}

/* ----------------------------- purchase orders ---------------------------- */

export async function listPurchaseOrders(): Promise<Page<PurchaseOrderOut>> {
  if (USE_FIXTURES) return settle({ items: PURCHASE_ORDERS, total: PURCHASE_ORDERS.length });
  return apiRequest<Page<PurchaseOrderOut>>(`/purchase-orders?size=${FULL_PAGE}`);
}

export async function getPurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const found = PURCHASE_ORDERS.find((p) => p.id === id);
    if (!found) throw new Error(`Purchase order ${id} not found`);
    return settle(found);
  }
  return apiRequest<PurchaseOrderOut>(`/purchase-orders/${id}`);
}

/* -------------------------------- inventory ------------------------------- */

export async function listInventory(): Promise<Page<InventoryItemOut>> {
  if (USE_FIXTURES) return settle({ items: INVENTORY, total: INVENTORY.length });
  return apiRequest<Page<InventoryItemOut>>(`/inventory?size=${FULL_PAGE}`);
}
