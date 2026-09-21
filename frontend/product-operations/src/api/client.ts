/**
 * Typed Operations API client.
 *
 * Live by default: every function calls the operations service through the gateway at the
 * same-origin `/api/v1/operations/...` prefix (Vite proxies it to the gateway in dev) with the
 * bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole app from
 * `fixtures.ts` with no login — handy for offline design work; fixture writes mutate the
 * in-memory fixture arrays so the screen behaves the same as live, and the flag is gated on
 * `import.meta.env.DEV` so it can never ship in a production build.
 *
 * Every list function takes the server's own filters, sort and paging and passes them straight
 * through — nothing here re-filters, re-sorts or caps a result set the server already decided.
 */

import { apiRequest, settle } from "@opsgrids/client";
import { milliToQty } from "@/lib/quantity";
import {
  FIXTURE_TODAY,
  INVENTORY,
  MOVEMENTS,
  PURCHASE_ORDERS,
  SUPPLIERS,
  WAREHOUSES,
} from "./fixtures";
import type {
  CancelIn,
  Category,
  InventoryItemIn,
  InventoryItemOut,
  InventoryItemPatch,
  Page,
  PoStatus,
  PurchaseOrderIn,
  PurchaseOrderOut,
  PurchaseOrderPatch,
  ReceiveIn,
  StockAdjustmentIn,
  StockMovementKind,
  StockMovementOut,
  SupplierIn,
  SupplierOut,
  SupplierPatch,
  SupplierStatus,
  WarehouseIn,
  WarehouseOut,
  WarehousePatch,
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

/** The real default page size for a paginated list screen. */
const DEFAULT_PAGE_SIZE = 25;
/** A generous page — used only where a screen genuinely needs the whole (small) working set for
 *  this org: the dashboard's rollups, which derive every KPI from the full set of rows. */
export const FULL_PAGE = 200;

class NotFoundError extends Error {}

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const s = query.toString();
  return s ? `?${s}` : "";
}

function paginateFixture<T>(items: T[], page: number, size: number): Page<T> {
  const total = items.length;
  const pages = total === 0 ? 0 : Math.ceil(total / size);
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total, page, size, pages };
}

function fixtureId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

/** Fixture stock is whole units; these fill in the exact fields the live API returns beside
 *  them, so fixture mode renders exactly what live mode does. */
function stockFields(onHand: number) {
  const qty = milliToQty(onHand * 1000);
  return { on_hand: onHand, on_hand_qty: qty, reserved_qty: "0", available_qty: qty };
}

function movementFields(delta: number, after: number) {
  return {
    quantity_delta: delta,
    on_hand_after: after,
    quantity: milliToQty(delta * 1000),
    balance_after: milliToQty(after * 1000),
    location_id: "fixture-default-bin",
    status: "AVAILABLE" as const,
  };
}

/* -------------------------------- suppliers ------------------------------- */

export interface ListSuppliersParams {
  q?: string;
  category?: Category | "all";
  status?: SupplierStatus | "all";
  sort?: "spend" | "on_time" | "lead_time" | "name";
  page?: number;
  size?: number;
}

function matchesSupplierFilters(s: SupplierOut, params: ListSuppliersParams): boolean {
  const q = params.q?.trim().toLowerCase() ?? "";
  const matchesQuery =
    q === "" ||
    s.code.toLowerCase().includes(q) ||
    s.name.toLowerCase().includes(q) ||
    s.contact_name.toLowerCase().includes(q) ||
    s.location.toLowerCase().includes(q);
  const matchesCategory =
    !params.category || params.category === "all" || s.category === params.category;
  const matchesStatus = !params.status || params.status === "all" || s.status === params.status;
  return matchesQuery && matchesCategory && matchesStatus;
}

function sortSuppliers(items: SupplierOut[], sort: ListSuppliersParams["sort"]): SupplierOut[] {
  const sorted = items.slice();
  switch (sort) {
    case "spend":
      return sorted.sort((a, b) => b.spend_minor - a.spend_minor);
    case "on_time":
      return sorted.sort((a, b) => b.on_time_pct - a.on_time_pct);
    case "lead_time":
      return sorted.sort((a, b) => a.lead_time_days - b.lead_time_days);
    case "name":
    default:
      return sorted.sort((a, b) => a.name.localeCompare(b.name));
  }
}

export async function listSuppliers(params: ListSuppliersParams = {}): Promise<Page<SupplierOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = sortSuppliers(SUPPLIERS.filter((s) => matchesSupplierFilters(s, params)), params.sort);
    return settle(paginateFixture(filtered, page, size));
  }
  const query = buildQuery({
    page,
    size,
    q: params.q,
    category: params.category === "all" ? undefined : params.category,
    status: params.status === "all" ? undefined : params.status,
    sort: params.sort,
  });
  return apiRequest<Page<SupplierOut>>(`/operations/suppliers${query}`);
}

export async function getSupplier(id: string): Promise<SupplierOut> {
  if (USE_FIXTURES) {
    const found = SUPPLIERS.find((s) => s.id === id);
    if (!found) throw new NotFoundError("That supplier could not be found.");
    return settle(found);
  }
  return apiRequest<SupplierOut>(`/operations/suppliers/${encodeURIComponent(id)}`);
}

/** `POST /operations/suppliers` — `operations.procurement.supplier.write`. 409 on duplicate code. */
export async function createSupplier(input: SupplierIn): Promise<SupplierOut> {
  if (USE_FIXTURES) {
    if (SUPPLIERS.some((s) => s.code.toLowerCase() === input.code.toLowerCase())) {
      throw new Error("A supplier with that code already exists.");
    }
    const supplier: SupplierOut = {
      id: fixtureId("sup"),
      code: input.code,
      name: input.name,
      category: input.category,
      status: input.status ?? "active",
      lead_time_days: input.lead_time_days ?? 0,
      on_time_pct: 0,
      spend_minor: 0,
      currency: (input.currency ?? "INR").toUpperCase(),
      open_pos: 0,
      contact_name: input.contact_name ?? "",
      contact_email: input.contact_email ?? "",
      location: input.location ?? "",
      since: input.since ?? FIXTURE_TODAY,
    };
    SUPPLIERS.unshift(supplier);
    return settle(supplier);
  }
  return apiRequest<SupplierOut>("/operations/suppliers", { method: "POST", body: input });
}

/** `PATCH /operations/suppliers/{id}` — same permission. No `code`/`currency`; this is also how
 *  status changes. */
export async function updateSupplier(id: string, patch: SupplierPatch): Promise<SupplierOut> {
  if (USE_FIXTURES) {
    const index = SUPPLIERS.findIndex((s) => s.id === id);
    if (index === -1) throw new NotFoundError("That supplier could not be found.");
    const current = SUPPLIERS[index];
    if (!current) throw new NotFoundError("That supplier could not be found.");
    const updated: SupplierOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.category !== undefined ? { category: patch.category } : {}),
      ...(patch.status !== undefined ? { status: patch.status } : {}),
      ...(patch.lead_time_days !== undefined ? { lead_time_days: patch.lead_time_days } : {}),
      ...(patch.contact_name !== undefined ? { contact_name: patch.contact_name } : {}),
      ...(patch.contact_email !== undefined ? { contact_email: patch.contact_email } : {}),
      ...(patch.location !== undefined ? { location: patch.location } : {}),
      ...(patch.since !== undefined ? { since: patch.since } : {}),
    };
    SUPPLIERS[index] = updated;
    // Denormalised supplier_name cascades onto its purchase orders, same as the service does.
    if (patch.name !== undefined) {
      for (let i = 0; i < PURCHASE_ORDERS.length; i += 1) {
        const po = PURCHASE_ORDERS[i];
        if (po && po.supplier_id === id) PURCHASE_ORDERS[i] = { ...po, supplier_name: patch.name };
      }
    }
    return settle(updated);
  }
  return apiRequest<SupplierOut>(`/operations/suppliers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /operations/suppliers/{id}` — same permission. 409 while a draft/approved PO names it. */
export async function deleteSupplier(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = SUPPLIERS.findIndex((s) => s.id === id);
    if (index === -1) throw new NotFoundError("That supplier could not be found.");
    if (PURCHASE_ORDERS.some((po) => po.supplier_id === id && (po.status === "draft" || po.status === "approved"))) {
      throw new Error("This supplier still has an open purchase order. Put it on hold instead.");
    }
    SUPPLIERS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/operations/suppliers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** A small cap search for the PO form's supplier picker — active suppliers only. */
export async function searchActiveSuppliers(q: string): Promise<SupplierOut[]> {
  const result = await listSuppliers({ q, status: "active", size: 20, sort: "name" });
  return result.items;
}

/* ----------------------------- purchase orders ---------------------------- */

export interface ListPurchaseOrdersParams {
  q?: string;
  status?: PoStatus | "all";
  category?: Category | "all";
  supplier_id?: string;
  sort?: "expected" | "created" | "number";
  page?: number;
  size?: number;
}

function matchesPoFilters(po: PurchaseOrderOut, params: ListPurchaseOrdersParams): boolean {
  const q = params.q?.trim().toLowerCase() ?? "";
  const matchesQuery =
    q === "" ||
    po.po_number.toLowerCase().includes(q) ||
    po.supplier_name.toLowerCase().includes(q) ||
    po.raised_by.toLowerCase().includes(q) ||
    po.warehouse.toLowerCase().includes(q);
  const matchesStatus = !params.status || params.status === "all" || po.status === params.status;
  const matchesCategory =
    !params.category || params.category === "all" || po.category === params.category;
  const matchesSupplier = !params.supplier_id || po.supplier_id === params.supplier_id;
  return matchesQuery && matchesStatus && matchesCategory && matchesSupplier;
}

function sortPos(items: PurchaseOrderOut[], sort: ListPurchaseOrdersParams["sort"]): PurchaseOrderOut[] {
  const sorted = items.slice();
  switch (sort) {
    case "expected":
      return sorted.sort((a, b) => a.expected_date.localeCompare(b.expected_date));
    case "created":
      return sorted.sort((a, b) => b.created_date.localeCompare(a.created_date));
    case "number":
    default:
      return sorted.sort((a, b) => b.po_number.localeCompare(a.po_number));
  }
}

export async function listPurchaseOrders(
  params: ListPurchaseOrdersParams = {},
): Promise<Page<PurchaseOrderOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = sortPos(PURCHASE_ORDERS.filter((po) => matchesPoFilters(po, params)), params.sort);
    return settle(paginateFixture(filtered, page, size));
  }
  const query = buildQuery({
    page,
    size,
    q: params.q,
    status: params.status === "all" ? undefined : params.status,
    category: params.category === "all" ? undefined : params.category,
    supplier_id: params.supplier_id,
    sort: params.sort,
  });
  return apiRequest<Page<PurchaseOrderOut>>(`/operations/purchase-orders${query}`);
}

export async function getPurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const found = PURCHASE_ORDERS.find((p) => p.id === id);
    if (!found) throw new NotFoundError("That purchase order could not be found.");
    return settle(found);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}`);
}

/** `POST /operations/purchase-orders` — `operations.procurement.purchase_order.write`. Server
 *  assigns `number`, `status="draft"`, dates and `raised_by_id`. */
export async function createPurchaseOrder(input: PurchaseOrderIn): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const supplier = SUPPLIERS.find((s) => s.id === input.supplier_id);
    if (!supplier) throw new NotFoundError("That supplier could not be found.");
    if (supplier.status !== "active") throw new Error(`${supplier.name} is not active.`);
    if (!WAREHOUSES.some((w) => w.name === input.warehouse)) {
      throw new Error(`${input.warehouse} does not match an existing warehouse.`);
    }
    const seq = PURCHASE_ORDERS.length + 149;
    const po: PurchaseOrderOut = {
      id: fixtureId("po"),
      po_number: `PO-2026-${String(seq).padStart(4, "0")}`,
      supplier_id: supplier.id,
      supplier_name: supplier.name,
      category: supplier.category,
      status: "draft",
      currency: input.currency ?? supplier.currency,
      warehouse: input.warehouse,
      raised_by: input.raised_by ?? "",
      created_date: FIXTURE_TODAY,
      expected_date: input.expected_date,
      received_date: null,
      notes: input.notes ?? null,
      line_items: input.lines.map((l, i) => ({
        id: fixtureId(`line-${i}`),
        sku: l.sku,
        description: l.description ?? "",
        quantity: l.quantity,
        uom: l.uom ?? "",
        unit_price_minor: l.unit_price_minor ?? 0,
        received_qty: 0,
      })),
    };
    PURCHASE_ORDERS.unshift(po);
    supplier.open_pos += 1;
    return settle(po);
  }
  return apiRequest<PurchaseOrderOut>("/operations/purchase-orders", { method: "POST", body: input });
}

/** `PATCH /operations/purchase-orders/{id}` — draft only (409 otherwise). */
export async function updatePurchaseOrder(
  id: string,
  patch: PurchaseOrderPatch,
): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const index = PURCHASE_ORDERS.findIndex((p) => p.id === id);
    if (index === -1) throw new NotFoundError("That purchase order could not be found.");
    const current = PURCHASE_ORDERS[index];
    if (!current) throw new NotFoundError("That purchase order could not be found.");
    if (current.status !== "draft") throw new Error("Only a draft purchase order can be edited.");
    let supplierName = current.supplier_name;
    let category = current.category;
    if (patch.supplier_id !== undefined) {
      const supplier = SUPPLIERS.find((s) => s.id === patch.supplier_id);
      if (!supplier) throw new NotFoundError("That supplier could not be found.");
      supplierName = supplier.name;
      category = supplier.category;
    }
    const updated: PurchaseOrderOut = {
      ...current,
      ...(patch.supplier_id !== undefined ? { supplier_id: patch.supplier_id, supplier_name: supplierName, category } : {}),
      ...(patch.warehouse !== undefined ? { warehouse: patch.warehouse } : {}),
      ...(patch.expected_date !== undefined ? { expected_date: patch.expected_date } : {}),
      ...(patch.currency !== undefined ? { currency: patch.currency } : {}),
      ...(patch.notes !== undefined ? { notes: patch.notes } : {}),
      ...(patch.lines !== undefined
        ? {
            line_items: patch.lines.map((l, i) => ({
              id: fixtureId(`line-${i}`),
              sku: l.sku,
              description: l.description ?? "",
              quantity: l.quantity,
              uom: l.uom ?? "",
              unit_price_minor: l.unit_price_minor ?? 0,
              received_qty: 0,
            })),
          }
        : {}),
    };
    PURCHASE_ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /operations/purchase-orders/{id}` — draft only. */
export async function deletePurchaseOrder(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = PURCHASE_ORDERS.findIndex((p) => p.id === id);
    if (index === -1) throw new NotFoundError("That purchase order could not be found.");
    const current = PURCHASE_ORDERS[index];
    if (!current) throw new NotFoundError("That purchase order could not be found.");
    if (current.status !== "draft") throw new Error("Only a draft purchase order can be deleted.");
    PURCHASE_ORDERS.splice(index, 1);
    const supplier = SUPPLIERS.find((s) => s.id === current.supplier_id);
    if (supplier) supplier.open_pos = Math.max(0, supplier.open_pos - 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/operations/purchase-orders/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

function fixturePoOrThrow(id: string): PurchaseOrderOut {
  const found = PURCHASE_ORDERS.find((p) => p.id === id);
  if (!found) throw new NotFoundError("That purchase order could not be found.");
  return found;
}

/** `POST /operations/purchase-orders/{id}/approve` — `operations.procurement.purchase_order.approve`,
 *  draft → approved. */
export async function approvePurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const po = fixturePoOrThrow(id);
    if (po.status !== "draft") throw new Error(`Purchase order ${po.po_number} is ${po.status} and cannot be approved.`);
    const index = PURCHASE_ORDERS.indexOf(po);
    const updated: PurchaseOrderOut = { ...po, status: "approved" };
    PURCHASE_ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });
}

/** `POST /operations/purchase-orders/{id}/receive` — draft|approved → received; posts stock for
 *  every line whose SKU matches a live inventory item. */
/** What receiving answers with: the order, plus the SKUs whose lines matched no inventory item. */
export type PurchaseOrderReceiptOut = PurchaseOrderOut & { unmatched_skus: string[] };

export async function receivePurchaseOrder(id: string, input: ReceiveIn = {}): Promise<PurchaseOrderReceiptOut> {
  if (USE_FIXTURES) {
    const po = fixturePoOrThrow(id);
    if (po.status !== "draft" && po.status !== "approved") {
      throw new Error(`Purchase order ${po.po_number} is ${po.status} and cannot be received.`);
    }
    const landed = input.received_date ?? FIXTURE_TODAY;
    const index = PURCHASE_ORDERS.indexOf(po);
    const updated: PurchaseOrderOut = {
      ...po,
      status: "received",
      received_date: landed,
      line_items: po.line_items.map((l) => ({ ...l, received_qty: l.quantity })),
    };
    PURCHASE_ORDERS[index] = updated;
    const unmatched: string[] = [];
    for (const line of updated.line_items) {
      const item = INVENTORY.find((i) => i.sku === line.sku);
      if (!item) {
        unmatched.push(line.sku);
        continue;
      }
      const after = item.on_hand + line.quantity;
      Object.assign(item, stockFields(after));
      item.updated_at = landed;
      const list = MOVEMENTS[item.id] ?? [];
      list.unshift({
        id: fixtureId("mv"),
        kind: "receipt",
        reason: null,
        ...movementFields(line.quantity, after),
        reference_type: "purchase_order",
        reference_id: po.id,
        note: "",
        occurred_on: landed,
        created_at: `${landed}T00:00:00Z`,
        created_by: "fixture",
      });
      MOVEMENTS[item.id] = list;
    }
    const supplier = SUPPLIERS.find((s) => s.id === po.supplier_id);
    if (supplier) supplier.open_pos = Math.max(0, supplier.open_pos - 1);
    return settle({ ...updated, unmatched_skus: unmatched });
  }
  return apiRequest<PurchaseOrderReceiptOut>(`/operations/purchase-orders/${encodeURIComponent(id)}/receive`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/purchase-orders/{id}/close` — received → closed. */
export async function closePurchaseOrder(id: string): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const po = fixturePoOrThrow(id);
    if (po.status !== "received") throw new Error(`Purchase order ${po.po_number} is ${po.status} and cannot be closed.`);
    const index = PURCHASE_ORDERS.indexOf(po);
    const updated: PurchaseOrderOut = { ...po, status: "closed" };
    PURCHASE_ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}/close`, {
    method: "POST",
  });
}

/** `POST /operations/purchase-orders/{id}/cancel` — draft|approved → cancelled. */
export async function cancelPurchaseOrder(id: string, input: CancelIn): Promise<PurchaseOrderOut> {
  if (USE_FIXTURES) {
    const po = fixturePoOrThrow(id);
    if (po.status !== "draft" && po.status !== "approved") {
      throw new Error(`Purchase order ${po.po_number} is ${po.status} and cannot be cancelled.`);
    }
    const index = PURCHASE_ORDERS.indexOf(po);
    const updated: PurchaseOrderOut = { ...po, status: "cancelled" };
    PURCHASE_ORDERS[index] = updated;
    const supplier = SUPPLIERS.find((s) => s.id === po.supplier_id);
    if (supplier) supplier.open_pos = Math.max(0, supplier.open_pos - 1);
    return settle(updated);
  }
  return apiRequest<PurchaseOrderOut>(`/operations/purchase-orders/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: input,
  });
}

/* -------------------------------- inventory ------------------------------- */

export interface ListInventoryParams {
  q?: string;
  category?: Category | "all";
  warehouse?: string | "all";
  low_stock?: boolean;
  sort?: "on_hand" | "value" | "sku";
  page?: number;
  size?: number;
}

function matchesInventoryFilters(it: InventoryItemOut, params: ListInventoryParams): boolean {
  const q = params.q?.trim().toLowerCase() ?? "";
  const matchesQuery =
    q === "" ||
    it.sku.toLowerCase().includes(q) ||
    it.name.toLowerCase().includes(q) ||
    it.warehouse.toLowerCase().includes(q);
  const matchesCategory =
    !params.category || params.category === "all" || it.category === params.category;
  const matchesWarehouse =
    !params.warehouse || params.warehouse === "all" || it.warehouse === params.warehouse;
  const matchesLowStock = !params.low_stock || it.on_hand <= it.reorder_point;
  return matchesQuery && matchesCategory && matchesWarehouse && matchesLowStock;
}

function sortInventory(items: InventoryItemOut[], sort: ListInventoryParams["sort"]): InventoryItemOut[] {
  const sorted = items.slice();
  switch (sort) {
    case "on_hand":
      return sorted.sort((a, b) => b.on_hand - a.on_hand);
    case "value":
      return sorted.sort((a, b) => b.on_hand * b.unit_value_minor - a.on_hand * a.unit_value_minor);
    case "sku":
    default:
      return sorted.sort((a, b) => a.sku.localeCompare(b.sku));
  }
}

export async function listInventory(params: ListInventoryParams = {}): Promise<Page<InventoryItemOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = sortInventory(INVENTORY.filter((it) => matchesInventoryFilters(it, params)), params.sort);
    return settle(paginateFixture(filtered, page, size));
  }
  const query = buildQuery({
    page,
    size,
    q: params.q,
    category: params.category === "all" ? undefined : params.category,
    warehouse: params.warehouse === "all" ? undefined : params.warehouse,
    low_stock: params.low_stock,
    sort: params.sort,
  });
  return apiRequest<Page<InventoryItemOut>>(`/operations/inventory${query}`);
}

export async function getInventoryItem(id: string): Promise<InventoryItemOut> {
  if (USE_FIXTURES) {
    const found = INVENTORY.find((i) => i.id === id);
    if (!found) throw new NotFoundError("That inventory item could not be found.");
    return settle(found);
  }
  return apiRequest<InventoryItemOut>(`/operations/inventory/${encodeURIComponent(id)}`);
}

/** `POST /operations/inventory` — `operations.inventory.item.write`. 409 duplicate SKU, 422 an
 *  unknown warehouse. */
export async function createInventoryItem(input: InventoryItemIn): Promise<InventoryItemOut> {
  if (USE_FIXTURES) {
    if (INVENTORY.some((i) => i.sku.toLowerCase() === input.sku.toLowerCase())) {
      throw new Error("An item with that SKU already exists.");
    }
    if (!WAREHOUSES.some((w) => w.name === input.warehouse)) {
      throw new Error(`${input.warehouse} does not match an existing warehouse.`);
    }
    const item: InventoryItemOut = {
      id: fixtureId("inv"),
      sku: input.sku,
      name: input.name,
      category: input.category,
      warehouse: input.warehouse,
      ...stockFields(input.opening_on_hand ?? 0),
      reorder_point: input.reorder_point ?? 0,
      uom: input.uom,
      unit_value_minor: input.unit_value_minor ?? 0,
      currency: (input.currency ?? "INR").toUpperCase(),
      updated_at: FIXTURE_TODAY,
    };
    INVENTORY.unshift(item);
    MOVEMENTS[item.id] = [
      {
        id: fixtureId("mv"),
        kind: "opening",
        reason: null,
        ...movementFields(item.on_hand, item.on_hand),
        reference_type: null,
        reference_id: null,
        note: "Opening balance",
        occurred_on: FIXTURE_TODAY,
        created_at: `${FIXTURE_TODAY}T00:00:00Z`,
        created_by: "fixture",
      },
    ];
    return settle(item);
  }
  return apiRequest<InventoryItemOut>("/operations/inventory", { method: "POST", body: input });
}

/** `PATCH /operations/inventory/{id}` — `on_hand`, `sku` and `currency` are never accepted. */
export async function updateInventoryItem(
  id: string,
  patch: InventoryItemPatch,
): Promise<InventoryItemOut> {
  if (USE_FIXTURES) {
    const index = INVENTORY.findIndex((i) => i.id === id);
    if (index === -1) throw new NotFoundError("That inventory item could not be found.");
    const current = INVENTORY[index];
    if (!current) throw new NotFoundError("That inventory item could not be found.");
    if (patch.warehouse !== undefined && !WAREHOUSES.some((w) => w.name === patch.warehouse)) {
      throw new Error(`${patch.warehouse} does not match an existing warehouse.`);
    }
    const updated: InventoryItemOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.category !== undefined ? { category: patch.category } : {}),
      ...(patch.warehouse !== undefined ? { warehouse: patch.warehouse } : {}),
      ...(patch.uom !== undefined ? { uom: patch.uom } : {}),
      ...(patch.reorder_point !== undefined ? { reorder_point: patch.reorder_point } : {}),
      ...(patch.unit_value_minor !== undefined ? { unit_value_minor: patch.unit_value_minor } : {}),
    };
    INVENTORY[index] = updated;
    return settle(updated);
  }
  return apiRequest<InventoryItemOut>(`/operations/inventory/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /operations/inventory/{id}` — 409 unless `on_hand == 0`. */
export async function deleteInventoryItem(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = INVENTORY.findIndex((i) => i.id === id);
    if (index === -1) throw new NotFoundError("That inventory item could not be found.");
    const current = INVENTORY[index];
    if (!current) throw new NotFoundError("That inventory item could not be found.");
    if (current.on_hand !== 0) {
      throw new Error("This item still has stock on hand. Adjust it to zero first.");
    }
    INVENTORY.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/operations/inventory/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** `POST /operations/inventory/{id}/adjustments` — `operations.inventory.stock.adjust`. Exactly
 *  one of `quantity_delta` or `counted_on_hand`. 409 if the result would go negative. */
export async function adjustStock(
  id: string,
  input: StockAdjustmentIn,
  options: { idempotencyKey?: string } = {},
): Promise<InventoryItemOut> {
  if (USE_FIXTURES) {
    const index = INVENTORY.findIndex((i) => i.id === id);
    if (index === -1) throw new NotFoundError("That inventory item could not be found.");
    const current = INVENTORY[index];
    if (!current) throw new NotFoundError("That inventory item could not be found.");
    const delta =
      input.quantity_delta !== undefined ? input.quantity_delta : (input.counted_on_hand ?? 0) - current.on_hand;
    const after = current.on_hand + delta;
    if (after < 0) {
      throw new Error(`That would take ${current.sku} below zero — ${current.on_hand} ${current.uom} on hand.`);
    }
    const occurred = input.occurred_on ?? FIXTURE_TODAY;
    const updated: InventoryItemOut = { ...current, ...stockFields(after), updated_at: occurred };
    INVENTORY[index] = updated;
    const list = MOVEMENTS[id] ?? [];
    list.unshift({
      id: fixtureId("mv"),
      kind: input.quantity_delta !== undefined ? "adjustment" : "count",
      reason: input.reason ?? null,
      ...movementFields(delta, after),
      reference_type: null,
      reference_id: null,
      note: input.note ?? "",
      occurred_on: occurred,
      created_at: `${occurred}T00:00:00Z`,
      created_by: "fixture",
    });
    MOVEMENTS[id] = list;
    return settle(updated);
  }
  return apiRequest<InventoryItemOut>(`/operations/inventory/${encodeURIComponent(id)}/adjustments`, {
    method: "POST",
    body: input,
    ...(options.idempotencyKey ? { headers: { "Idempotency-Key": options.idempotencyKey } } : {}),
  });
}

export interface ListMovementsParams {
  kind?: StockMovementKind | "all";
  page?: number;
  size?: number;
}

export async function listMovements(
  itemId: string,
  params: ListMovementsParams = {},
): Promise<Page<StockMovementOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const all = MOVEMENTS[itemId] ?? [];
    const filtered =
      !params.kind || params.kind === "all" ? all : all.filter((m) => m.kind === params.kind);
    return settle(paginateFixture(filtered, page, size));
  }
  const query = buildQuery({ page, size, kind: params.kind === "all" ? undefined : params.kind });
  return apiRequest<Page<StockMovementOut>>(
    `/operations/inventory/${encodeURIComponent(itemId)}/movements${query}`,
  );
}

/* ------------------------------- warehouses ------------------------------- */

export interface ListWarehousesParams {
  page?: number;
  size?: number;
}

export async function listWarehouses(params: ListWarehousesParams = {}): Promise<Page<WarehouseOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const sorted = WAREHOUSES.slice().sort((a, b) => a.name.localeCompare(b.name));
    return settle(paginateFixture(sorted, page, size));
  }
  const query = buildQuery({ page, size });
  return apiRequest<Page<WarehouseOut>>(`/operations/warehouses${query}`);
}

/** `POST /operations/warehouses` — `operations.inventory.warehouse.write` (admin only). 409 on a
 *  duplicate code or name. */
export async function createWarehouse(input: WarehouseIn): Promise<WarehouseOut> {
  if (USE_FIXTURES) {
    if (WAREHOUSES.some((w) => w.code.toLowerCase() === input.code.toLowerCase())) {
      throw new Error("A warehouse with that code already exists.");
    }
    if (WAREHOUSES.some((w) => w.name.toLowerCase() === input.name.toLowerCase())) {
      throw new Error("A warehouse with that name already exists.");
    }
    const warehouse: WarehouseOut = {
      id: fixtureId("wh"),
      code: input.code,
      name: input.name,
      location: input.location ?? "",
    };
    WAREHOUSES.push(warehouse);
    return settle(warehouse);
  }
  return apiRequest<WarehouseOut>("/operations/warehouses", { method: "POST", body: input });
}

/** `PATCH /operations/warehouses/{id}` — `code` is immutable. A rename cascades to POs and
 *  inventory items that carry the name. */
export async function updateWarehouse(id: string, patch: WarehousePatch): Promise<WarehouseOut> {
  if (USE_FIXTURES) {
    const index = WAREHOUSES.findIndex((w) => w.id === id);
    if (index === -1) throw new NotFoundError("That warehouse could not be found.");
    const current = WAREHOUSES[index];
    if (!current) throw new NotFoundError("That warehouse could not be found.");
    if (
      patch.name !== undefined &&
      WAREHOUSES.some((w) => w.id !== id && w.name.toLowerCase() === patch.name?.toLowerCase())
    ) {
      throw new Error("A warehouse with that name already exists.");
    }
    const updated: WarehouseOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.location !== undefined ? { location: patch.location } : {}),
    };
    WAREHOUSES[index] = updated;
    if (patch.name !== undefined && patch.name !== current.name) {
      for (let i = 0; i < INVENTORY.length; i += 1) {
        const item = INVENTORY[i];
        if (item && item.warehouse === current.name) INVENTORY[i] = { ...item, warehouse: patch.name };
      }
      for (let i = 0; i < PURCHASE_ORDERS.length; i += 1) {
        const po = PURCHASE_ORDERS[i];
        if (po && po.warehouse === current.name) PURCHASE_ORDERS[i] = { ...po, warehouse: patch.name };
      }
    }
    return settle(updated);
  }
  return apiRequest<WarehouseOut>(`/operations/warehouses/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /operations/warehouses/{id}` — 409 while a live item or open PO carries it. */
export async function deleteWarehouse(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = WAREHOUSES.findIndex((w) => w.id === id);
    if (index === -1) throw new NotFoundError("That warehouse could not be found.");
    const current = WAREHOUSES[index];
    if (!current) throw new NotFoundError("That warehouse could not be found.");
    if (INVENTORY.some((i) => i.warehouse === current.name)) {
      throw new Error("This warehouse still holds inventory. Move the stock first.");
    }
    if (PURCHASE_ORDERS.some((po) => po.warehouse === current.name && (po.status === "draft" || po.status === "approved"))) {
      throw new Error("This warehouse is the destination of an open purchase order.");
    }
    WAREHOUSES.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/operations/warehouses/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* -------------------------------- combobox helpers ------------------------ */

/** For the PO line editor's SKU picker — a small cap search over the org's inventory. */
export async function searchInventory(q: string): Promise<InventoryItemOut[]> {
  const result = await listInventory({ q, size: 20, sort: "sku" });
  return result.items;
}
