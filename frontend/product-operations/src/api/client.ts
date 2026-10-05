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

import { ApiError, apiRequest, settle } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";
import { milliToQty, qtyToMilli } from "@/lib/quantity";
import {
  attentionItems,
  inventoryValueMinor,
  lowStockItems,
  openPoCount,
  onTimeDeliveryPct,
  outOfStockCount,
  poStatusMix,
  spendByCategory,
  spendTrend,
  topSuppliers,
} from "@/data/derive";
import {
  FIXTURE_TODAY,
  INVENTORY,
  ITEM_ATTRIBUTES,
  ITEM_CATEGORIES,
  ITEM_VARIANTS,
  LOCATIONS,
  MOVEMENTS,
  ORDERS,
  PURCHASE_ORDERS,
  SUPPLIERS,
  UOMS,
  WAREHOUSES,
} from "./fixtures";
import type {
  AlertKind,
  AssignmentOfferOut,
  CancelIn,
  Category,
  CountLineOut,
  CountLineReviewIn,
  CountLineReviewStatus,
  CountSessionCancelIn,
  CountSessionIn,
  CountSessionOut,
  CountSessionStatus,
  DiscrepancyTrendPointOut,
  InventoryItemIn,
  InventoryItemOut,
  InventoryItemPatch,
  ChannelConfigPatch,
  ChannelConnectIn,
  ChannelHealthCheckOut,
  ChannelOut,
  ChannelReconnectIn,
  ChannelRetryResultOut,
  ChannelSkuMappingIn,
  ChannelSkuMappingOut,
  ChannelStatus,
  ChannelSyncLogOut,
  ChannelSyncResultOut,
  ChannelSyncStatus,
  ChannelSyncType,
  ItemAttributeIn,
  ItemAttributeOut,
  ItemCategoryIn,
  ItemCategoryOut,
  ItemCategoryPatch,
  ItemVariantIn,
  ItemVariantOut,
  ItemVariantPatch,
  LayoutGeometryIn,
  LayoutGeometryOut,
  LayoutValidationOut,
  LayoutVersionCreateIn,
  LayoutVersionOut,
  LocationIn,
  MerchantAccountIn,
  MerchantAccountOut,
  LocationOut,
  LocationPatch,
  OfferIn,
  OperationalAlertOut,
  OrderCancelIn,
  OrderChannel,
  OrderCheckoutOut,
  OrderCollectionOut,
  OrderHoldIn,
  OrderIn,
  OrderOut,
  OrderPaymentsOut,
  OrderRefundIn,
  OrderStatus,
  OrderVersionIn,
  OverviewOut,
  Page,
  PickTaskOut,
  PickTaskStatus,
  PoStatus,
  PublishedLayoutOut,
  PurchaseOrderIn,
  PurchaseOrderOut,
  PurchaseOrderPatch,
  QuoteIn,
  QuoteOut,
  ReceiveIn,
  ReconciliationOut,
  RouteOut,
  ShipmentAdvanceIn,
  ShipmentIn,
  ShipmentOut,
  ShipmentRefusalIn,
  ShipmentStatus,
  StockAdjustmentIn,
  StockMovementKind,
  StockMovementOut,
  SupplierIn,
  SupplierOut,
  SupplierPatch,
  SupplierStatus,
  TaskBacklogOut,
  TaskCancelIn,
  ThroughputOut,
  UomOut,
  WorkerIn,
  WorkerOut,
  WorkerPatch,
  WarehouseFloorIn,
  WarehouseFloorOut,
  WarehouseFloorPatch,
  WarehouseIn,
  WarehouseMapOut,
  WarehouseOut,
  WarehousePatch,
  WorkerWorkloadOut,
  ZoneBottleneckOut,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

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

/**
 * A refusal shaped like the gateway's `problem+json`, so a fixture create fails the way the server
 * does: an `ApiError` carrying the status and the server's sentence. The Excel importer reads a
 * 409 as "already exists" and a 422 as a row the server refused, and the forms read `errors` for
 * the field. `duplicate` names the field the way the server's own duplicate errors do.
 */
function refusal(status: 409 | 422, detail: string, duplicate?: string): ApiError {
  const problem = {
    type: `https://errors.opsgrids.dev/${status === 409 ? "conflict" : "validation"}`,
    title: status === 409 ? "Conflict" : "Validation failed",
    status,
    detail,
    ...(duplicate === undefined ? {} : { errors: [{ field: duplicate, code: "duplicate", message: detail }] }),
  };
  return new ApiError(status, detail, problem);
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
  /** Aborts the request — the Excel export cancels a long run through it. */
  signal?: AbortSignal;
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
  return apiRequest<Page<SupplierOut>>(`/operations/suppliers${query}`, { signal: params.signal });
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
    if (SUPPLIERS.some((s) => s.code === input.code)) {
      throw refusal(409, `A supplier coded '${input.code}' already exists`, "code");
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
  item_category_id?: string | "all";
  warehouse?: string | "all";
  low_stock?: boolean;
  sort?: "on_hand" | "value" | "sku";
  page?: number;
  size?: number;
  /** Aborts the request — the Excel export cancels a long run through it. */
  signal?: AbortSignal;
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
  const matchesItemCategory =
    !params.item_category_id ||
    params.item_category_id === "all" ||
    it.item_category_id === params.item_category_id;
  const matchesWarehouse =
    !params.warehouse || params.warehouse === "all" || it.warehouse === params.warehouse;
  const matchesLowStock = !params.low_stock || it.on_hand <= it.reorder_point;
  return matchesQuery && matchesCategory && matchesItemCategory && matchesWarehouse && matchesLowStock;
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
    item_category_id: params.item_category_id === "all" ? undefined : params.item_category_id,
    warehouse: params.warehouse === "all" ? undefined : params.warehouse,
    low_stock: params.low_stock,
    sort: params.sort,
  });
  return apiRequest<Page<InventoryItemOut>>(`/operations/inventory${query}`, { signal: params.signal });
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
    if (INVENTORY.some((i) => i.sku === input.sku)) {
      throw refusal(409, `An inventory item with SKU '${input.sku}' already exists`);
    }
    if (!WAREHOUSES.some((w) => w.name === input.warehouse)) {
      throw refusal(422, `'${input.warehouse}' does not match an existing warehouse`);
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
      item_category_id: input.item_category_id ?? null,
      secondary_uom_id: input.secondary_uom_id ?? null,
      hsn_code: input.hsn_code ?? "",
      track_lot: input.track_lot ?? false,
      track_roll: input.track_roll ?? false,
      track_expiry: input.track_expiry ?? false,
      track_quality: input.track_quality ?? false,
      is_active: input.is_active ?? true,
      // Fulfilment F2 (ADR-0024 §3): no create-item screen sets this yet — an item created here
      // starts with no selling price, the same as a freshly created live item would, and an order
      // line for it still needs its own explicit price.
      selling_price_minor: null,
      selling_price_currency: null,
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

/** `PATCH /operations/inventory/{id}` — `on_hand`, `sku` and `currency` are never accepted; 422 a
 *  warehouse that does not exist, 409 a different unit once stock has moved. */
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
      throw refusal(422, `'${patch.warehouse}' does not match an existing warehouse`);
    }
    // The unit is the scale every recorded quantity was written in, so it cannot change once stock
    // has moved. (The server also folds registry spellings, "Nos" for "pcs", into one unit; only
    // capitals are folded here, and the importer sends a unit only when it is a different one.)
    if (
      patch.uom !== undefined &&
      patch.uom.trim().toLowerCase() !== current.uom.trim().toLowerCase() &&
      (MOVEMENTS[id]?.length ?? 0) > 0
    ) {
      throw refusal(
        409,
        `${current.sku} already has stock history in '${current.uom}'; its unit of measure cannot change once any stock has moved`,
      );
    }
    const updated: InventoryItemOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.category !== undefined ? { category: patch.category } : {}),
      ...(patch.warehouse !== undefined ? { warehouse: patch.warehouse } : {}),
      ...(patch.uom !== undefined ? { uom: patch.uom } : {}),
      ...(patch.reorder_point !== undefined ? { reorder_point: patch.reorder_point } : {}),
      ...(patch.unit_value_minor !== undefined ? { unit_value_minor: patch.unit_value_minor } : {}),
      ...(patch.item_category_id !== undefined ? { item_category_id: patch.item_category_id } : {}),
      ...(patch.secondary_uom_id !== undefined ? { secondary_uom_id: patch.secondary_uom_id } : {}),
      ...(patch.hsn_code !== undefined ? { hsn_code: patch.hsn_code } : {}),
      ...(patch.track_lot !== undefined ? { track_lot: patch.track_lot } : {}),
      ...(patch.track_roll !== undefined ? { track_roll: patch.track_roll } : {}),
      ...(patch.track_expiry !== undefined ? { track_expiry: patch.track_expiry } : {}),
      ...(patch.track_quality !== undefined ? { track_quality: patch.track_quality } : {}),
      ...(patch.is_active !== undefined ? { is_active: patch.is_active } : {}),
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

/* ---------------------------- units of measure ----------------------------- */

/** `GET /operations/inventory/uoms` — the org's UOM directory, registry units first. */
export async function listUoms(
  params: { page?: number; size?: number; signal?: AbortSignal } = {},
): Promise<Page<UomOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? 200;
  if (USE_FIXTURES) {
    return settle(paginateFixture(UOMS, page, size));
  }
  const query = buildQuery({ page, size });
  return apiRequest<Page<UomOut>>(`/operations/inventory/uoms${query}`, { signal: params.signal });
}

/* ---------------------------- item categories ------------------------------ */

export async function listItemCategories(
  params: { page?: number; size?: number; signal?: AbortSignal } = {},
): Promise<Page<ItemCategoryOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? 200;
  if (USE_FIXTURES) {
    return settle(paginateFixture(ITEM_CATEGORIES, page, size));
  }
  const query = buildQuery({ page, size });
  return apiRequest<Page<ItemCategoryOut>>(`/operations/inventory/categories${query}`, {
    signal: params.signal,
  });
}

/** `POST /operations/inventory/categories` — `operations.inventory.item.write`. 409 duplicate
 *  code, 422 an unknown parent. */
export async function createItemCategory(input: ItemCategoryIn): Promise<ItemCategoryOut> {
  if (USE_FIXTURES) {
    if (ITEM_CATEGORIES.some((c) => c.code === input.code)) {
      throw refusal(409, `A category coded '${input.code}' already exists`, "code");
    }
    if (input.parent_id != null && !ITEM_CATEGORIES.some((c) => c.id === input.parent_id)) {
      throw refusal(422, "parent_id does not match an existing category");
    }
    const category: ItemCategoryOut = {
      id: fixtureId("cat"),
      code: input.code,
      name: input.name,
      parent_id: input.parent_id ?? null,
    };
    ITEM_CATEGORIES.push(category);
    return settle(category);
  }
  return apiRequest<ItemCategoryOut>("/operations/inventory/categories", {
    method: "POST",
    body: input,
  });
}

/** What the service checks when a category is given a parent, in its order: not itself, one that
 *  exists, and not one of its own descendants (which would close the tree into a loop). */
function refuseFixtureParent(id: string, parentId: string): void {
  if (parentId === id) throw refusal(422, "A category cannot be its own parent");
  if (!ITEM_CATEGORIES.some((c) => c.id === parentId)) {
    throw refusal(422, "parent_id does not match an existing category");
  }
  let ancestor: string | null = parentId;
  while (ancestor !== null) {
    if (ancestor === id) throw refusal(422, "That would make the category tree circular");
    const above: string | null = ITEM_CATEGORIES.find((c) => c.id === ancestor)?.parent_id ?? null;
    ancestor = above;
  }
}

/** `PATCH /operations/inventory/categories/{id}` — `operations.inventory.item.write`. 422 a parent
 *  that is missing, is the category itself, or would make the tree circular. */
export async function updateItemCategory(
  id: string,
  patch: ItemCategoryPatch,
): Promise<ItemCategoryOut> {
  if (USE_FIXTURES) {
    const index = ITEM_CATEGORIES.findIndex((c) => c.id === id);
    if (index === -1) throw new NotFoundError("That category could not be found.");
    const current = ITEM_CATEGORIES[index];
    if (!current) throw new NotFoundError("That category could not be found.");
    if (patch.parent_id != null) refuseFixtureParent(id, patch.parent_id);
    const updated: ItemCategoryOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.parent_id !== undefined ? { parent_id: patch.parent_id } : {}),
    };
    ITEM_CATEGORIES[index] = updated;
    return settle(updated);
  }
  return apiRequest<ItemCategoryOut>(`/operations/inventory/categories/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /operations/inventory/categories/{id}` — 409 while children or items still use it. */
export async function deleteItemCategory(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = ITEM_CATEGORIES.findIndex((c) => c.id === id);
    if (index === -1) throw new NotFoundError("That category could not be found.");
    if (ITEM_CATEGORIES.some((c) => c.parent_id === id)) {
      throw new Error("This category still has sub-categories. Move or delete them first.");
    }
    if (INVENTORY.some((i) => i.item_category_id === id)) {
      throw new Error("This category still classifies items. Move or clear them first.");
    }
    ITEM_CATEGORIES.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/operations/inventory/categories/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/* ---------------------------- item attributes ------------------------------ */

export async function listItemAttributes(itemId: string): Promise<ItemAttributeOut[]> {
  if (USE_FIXTURES) {
    return settle(ITEM_ATTRIBUTES[itemId] ?? []);
  }
  return apiRequest<ItemAttributeOut[]>(
    `/operations/inventory/${encodeURIComponent(itemId)}/attributes`,
  );
}

/** `PUT /operations/inventory/{id}/attributes/{key}` — creates or overwrites. */
export async function setItemAttribute(
  itemId: string,
  key: string,
  input: ItemAttributeIn,
): Promise<ItemAttributeOut> {
  if (USE_FIXTURES) {
    const list = ITEM_ATTRIBUTES[itemId] ?? [];
    const attribute: ItemAttributeOut = {
      id: fixtureId("attr"),
      key,
      value_type: input.value_type,
      value_text: input.value_text ?? null,
      value_number: input.value_number ?? null,
      value_boolean: input.value_boolean ?? null,
      value_date: input.value_date ?? null,
    };
    const index = list.findIndex((a) => a.key === key);
    if (index === -1) list.push(attribute);
    else list[index] = { ...attribute, id: list[index]?.id ?? attribute.id };
    ITEM_ATTRIBUTES[itemId] = list;
    return settle(attribute);
  }
  return apiRequest<ItemAttributeOut>(
    `/operations/inventory/${encodeURIComponent(itemId)}/attributes/${encodeURIComponent(key)}`,
    { method: "PUT", body: input },
  );
}

export async function deleteItemAttribute(itemId: string, key: string): Promise<void> {
  if (USE_FIXTURES) {
    const list = ITEM_ATTRIBUTES[itemId] ?? [];
    const index = list.findIndex((a) => a.key === key);
    if (index === -1) throw new NotFoundError("That attribute could not be found.");
    list.splice(index, 1);
    ITEM_ATTRIBUTES[itemId] = list;
    return settle(undefined);
  }
  await apiRequest<undefined>(
    `/operations/inventory/${encodeURIComponent(itemId)}/attributes/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

/* ------------------------------ item variants ------------------------------ */

export async function listItemVariants(itemId: string): Promise<ItemVariantOut[]> {
  if (USE_FIXTURES) {
    return settle(ITEM_VARIANTS[itemId] ?? []);
  }
  return apiRequest<ItemVariantOut[]>(
    `/operations/inventory/${encodeURIComponent(itemId)}/variants`,
  );
}

/** `POST /operations/inventory/{id}/variants` — 409 duplicate SKU. */
export async function createItemVariant(
  itemId: string,
  input: ItemVariantIn,
): Promise<ItemVariantOut> {
  if (USE_FIXTURES) {
    const allVariants = Object.values(ITEM_VARIANTS).flat();
    if (allVariants.some((v) => v.sku.toLowerCase() === input.sku.toLowerCase())) {
      throw new Error("A variant with that SKU already exists.");
    }
    const variant: ItemVariantOut = {
      id: fixtureId("var"),
      inventory_item_id: itemId,
      sku: input.sku,
      name: input.name,
      is_active: true,
    };
    const list = ITEM_VARIANTS[itemId] ?? [];
    list.push(variant);
    ITEM_VARIANTS[itemId] = list;
    return settle(variant);
  }
  return apiRequest<ItemVariantOut>(`/operations/inventory/${encodeURIComponent(itemId)}/variants`, {
    method: "POST",
    body: input,
  });
}

export async function updateItemVariant(
  itemId: string,
  variantId: string,
  patch: ItemVariantPatch,
): Promise<ItemVariantOut> {
  if (USE_FIXTURES) {
    const list = ITEM_VARIANTS[itemId] ?? [];
    const index = list.findIndex((v) => v.id === variantId);
    if (index === -1) throw new NotFoundError("That variant could not be found.");
    const current = list[index];
    if (!current) throw new NotFoundError("That variant could not be found.");
    const updated: ItemVariantOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.is_active !== undefined ? { is_active: patch.is_active } : {}),
    };
    list[index] = updated;
    ITEM_VARIANTS[itemId] = list;
    return settle(updated);
  }
  return apiRequest<ItemVariantOut>(
    `/operations/inventory/${encodeURIComponent(itemId)}/variants/${encodeURIComponent(variantId)}`,
    { method: "PATCH", body: patch },
  );
}

export async function deleteItemVariant(itemId: string, variantId: string): Promise<void> {
  if (USE_FIXTURES) {
    const list = ITEM_VARIANTS[itemId] ?? [];
    const index = list.findIndex((v) => v.id === variantId);
    if (index === -1) throw new NotFoundError("That variant could not be found.");
    list.splice(index, 1);
    ITEM_VARIANTS[itemId] = list;
    return settle(undefined);
  }
  await apiRequest<undefined>(
    `/operations/inventory/${encodeURIComponent(itemId)}/variants/${encodeURIComponent(variantId)}`,
    { method: "DELETE" },
  );
}

/* ------------------------------- warehouses ------------------------------- */

export interface ListWarehousesParams {
  page?: number;
  size?: number;
  /** Aborts the request — the Excel export cancels a long run through it. */
  signal?: AbortSignal;
}

export async function listWarehouses(params: ListWarehousesParams = {}): Promise<Page<WarehouseOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const sorted = WAREHOUSES.slice().sort((a, b) => a.name.localeCompare(b.name));
    return settle(paginateFixture(sorted, page, size));
  }
  const query = buildQuery({ page, size });
  return apiRequest<Page<WarehouseOut>>(`/operations/warehouses${query}`, { signal: params.signal });
}

/**
 * One warehouse, by id — there is no `GET /operations/warehouses/{id}` endpoint today, so this
 * reads it off the same full list every warehouse picker already fetches (`FULL_PAGE`, a small
 * working set for one org) rather than inventing a route. Throws when the id doesn't match —
 * a bad link or a warehouse from another organization, which reads as a 404 the same way a real
 * single-resource fetch would.
 */
export async function getWarehouseById(id: string): Promise<WarehouseOut> {
  const page = await listWarehouses({ size: FULL_PAGE });
  const found = page.items.find((w) => w.id === id);
  if (!found) throw new NotFoundError("That warehouse could not be found.");
  return found;
}

/** `POST /operations/warehouses` — `operations.inventory.warehouse.write` (admin only). 409 on a
 *  duplicate code or name. */
export async function createWarehouse(input: WarehouseIn): Promise<WarehouseOut> {
  if (USE_FIXTURES) {
    // The server compares codes and names exactly, so a variant in other capitals is a new warehouse.
    if (WAREHOUSES.some((w) => w.code === input.code)) {
      throw refusal(409, `A warehouse coded '${input.code}' already exists`, "code");
    }
    if (WAREHOUSES.some((w) => w.name === input.name)) {
      throw refusal(409, `A warehouse named '${input.name}' already exists`, "name");
    }
    const warehouse: WarehouseOut = {
      id: fixtureId("wh"),
      code: input.code,
      name: input.name,
      location: input.location ?? "",
      ...(input.lat_micro !== undefined ? { lat_micro: input.lat_micro } : {}),
      ...(input.lng_micro !== undefined ? { lng_micro: input.lng_micro } : {}),
    };
    WAREHOUSES.push(warehouse);
    return settle(warehouse);
  }
  return apiRequest<WarehouseOut>("/operations/warehouses", { method: "POST", body: input });
}

/** `PATCH /operations/warehouses/{id}` — `code` is immutable. A rename cascades to POs and
 *  inventory items that carry the name; 409 when another warehouse already has the new name. */
export async function updateWarehouse(id: string, patch: WarehousePatch): Promise<WarehouseOut> {
  if (USE_FIXTURES) {
    const index = WAREHOUSES.findIndex((w) => w.id === id);
    if (index === -1) throw new NotFoundError("That warehouse could not be found.");
    const current = WAREHOUSES[index];
    if (!current) throw new NotFoundError("That warehouse could not be found.");
    // Exact, as the server compares it: "cold store" is free beside "Cold Store".
    if (patch.name !== undefined && WAREHOUSES.some((w) => w.id !== id && w.name === patch.name)) {
      throw refusal(409, `A warehouse named '${patch.name}' already exists`, "name");
    }
    const updated: WarehouseOut = {
      ...current,
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.location !== undefined ? { location: patch.location } : {}),
      ...(patch.lat_micro !== undefined ? { lat_micro: patch.lat_micro } : {}),
      ...(patch.lng_micro !== undefined ? { lng_micro: patch.lng_micro } : {}),
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

/**
 * `GET /operations/warehouses/map` — `operations.inventory.warehouse.read`. The warehouses that
 * have a position (with floor and stocked-bin counts) and, separately, those that do not, so the
 * sites map can offer "Set location" for each. **Live only** — no fixture twin: fixture warehouses
 * carry no floors or stock to count, and an invented answer would be a second implementation.
 */
export async function getWarehousesMap(params: { signal?: AbortSignal } = {}): Promise<WarehouseMapOut> {
  return apiRequest<WarehouseMapOut>("/operations/warehouses/map", { signal: params.signal });
}

/* -------------------------------- locations -------------------------------- */
/**
 * Fulfilment plan F1 (`docs/fulfilment/plan.md`). These wire types and calls are hand-rolled
 * against the plan's documented contract rather than generated: `libs/contracts/openapi/operations.json`
 * does not carry a `locations` path yet (checked 2026-09-28 — the backend half of F1 was still in
 * flight). Regenerate `opsgrids-client`'s types and swap these for the generated client once it
 * lands; nothing about this file's shape should need to change if the contract matches what this
 * slice was built against.
 *
 * Confirmed against the landed backend (`services/operations/app/api/routes.py`): the list route
 * returns `Page[LocationOut]` like every other list here, not a bare array — `listLocations` below
 * fetches it at `FULL_PAGE` (200, the offset endpoint's own max `size`) and unwraps `.items`, the
 * same "whole working set in one page" pattern `getWarehouseById` already uses in this file. A
 * warehouse holding more than 200 locations would silently lose the rest of its tree; there is no
 * pagination or virtualisation in the tree UI to fall back on, so that ceiling is a real one, just
 * not one F1 needs to solve (per the plan, warehouses this phase serves are dozens of locations).
 */

/**
 * A warehouse's whole location tree — `GET /warehouses/{warehouse_id}/locations`, fetched at
 * `FULL_PAGE` and unwrapped, since a tree is rendered whole or not at all.
 */
export async function listLocations(
  warehouseId: string,
  params: { signal?: AbortSignal } = {},
): Promise<LocationOut[]> {
  if (USE_FIXTURES) {
    return settle(LOCATIONS[warehouseId] ?? []);
  }
  const query = buildQuery({ size: FULL_PAGE });
  const page = await apiRequest<Page<LocationOut>>(
    `/operations/warehouses/${encodeURIComponent(warehouseId)}/locations${query}`,
    { signal: params.signal },
  );
  return page.items;
}

/** `POST /warehouses/{warehouse_id}/locations` — `operations.inventory.location.write`. 409 on a
 *  duplicate code (per warehouse) or a duplicate barcode (per org), 422 an unknown parent. */
export async function createLocation(warehouseId: string, input: LocationIn): Promise<LocationOut> {
  if (USE_FIXTURES) {
    const list = LOCATIONS[warehouseId] ?? [];
    if (list.some((l) => l.code === input.code)) {
      throw refusal(409, `A location coded '${input.code}' already exists in this warehouse`, "code");
    }
    const barcode = input.barcode?.trim() || null;
    if (barcode && Object.values(LOCATIONS).flat().some((l) => l.barcode === barcode)) {
      throw refusal(409, `A location with barcode '${barcode}' already exists`, "barcode");
    }
    if (input.parent_id != null && !list.some((l) => l.id === input.parent_id)) {
      throw refusal(422, "parent_id does not match an existing location in this warehouse");
    }
    const location: LocationOut = {
      id: fixtureId("loc"),
      warehouse_id: warehouseId,
      parent_id: input.parent_id ?? null,
      kind: input.kind,
      code: input.code,
      name: input.name,
      is_default: false,
      barcode,
      pick_sequence: input.pick_sequence ?? null,
      max_weight_grams: input.max_weight_grams ?? null,
      max_volume_milli: input.max_volume_milli ?? null,
      storage_type: input.storage_type ?? null,
    };
    list.push(location);
    LOCATIONS[warehouseId] = list;
    return settle(location);
  }
  return apiRequest<LocationOut>(
    `/operations/warehouses/${encodeURIComponent(warehouseId)}/locations`,
    { method: "POST", body: input },
  );
}

/** `PATCH /locations/{id}` — `operations.inventory.location.write`; `kind` and the capacity
 *  fields additionally need `operations.inventory.layout.publish` once the location has stock
 *  history (403 otherwise — this function passes that straight through as an `ApiError`). */
export async function updateLocation(id: string, patch: LocationPatch): Promise<LocationOut> {
  if (USE_FIXTURES) {
    for (const warehouseId of Object.keys(LOCATIONS)) {
      const list = LOCATIONS[warehouseId] ?? [];
      const index = list.findIndex((l) => l.id === id);
      if (index === -1) continue;
      const current = list[index];
      if (!current) continue;
      if (
        patch.code !== undefined &&
        list.some((l) => l.id !== id && l.code === patch.code)
      ) {
        throw refusal(409, `A location coded '${patch.code}' already exists in this warehouse`, "code");
      }
      const barcode = patch.barcode !== undefined ? patch.barcode?.trim() || null : current.barcode;
      if (
        barcode &&
        patch.barcode !== undefined &&
        Object.values(LOCATIONS).flat().some((l) => l.id !== id && l.barcode === barcode)
      ) {
        throw refusal(409, `A location with barcode '${barcode}' already exists`, "barcode");
      }
      const updated: LocationOut = {
        ...current,
        ...(patch.kind !== undefined ? { kind: patch.kind } : {}),
        ...(patch.code !== undefined ? { code: patch.code } : {}),
        ...(patch.name !== undefined ? { name: patch.name } : {}),
        ...(patch.barcode !== undefined ? { barcode } : {}),
        ...(patch.pick_sequence !== undefined ? { pick_sequence: patch.pick_sequence } : {}),
        ...(patch.max_weight_grams !== undefined ? { max_weight_grams: patch.max_weight_grams } : {}),
        ...(patch.max_volume_milli !== undefined ? { max_volume_milli: patch.max_volume_milli } : {}),
        ...(patch.storage_type !== undefined ? { storage_type: patch.storage_type } : {}),
      };
      list[index] = updated;
      LOCATIONS[warehouseId] = list;
      return settle(updated);
    }
    throw new NotFoundError("That location could not be found.");
  }
  return apiRequest<LocationOut>(`/operations/locations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/**
 * `GET /locations/by-barcode/{barcode}` — resolves a scanned or typed barcode to the exact
 * location it was printed for (the F1 exit criterion: printed label → scanned value → the same
 * location id). Not scoped to one warehouse — the result's own `warehouse_id` says which one.
 * No screen in this slice calls this yet (F1's UI scope is the tree and its labels, not a lookup
 * tool); it is here so the contract is exercised and ready for the worker app or a future
 * "find by barcode" affordance to reuse.
 */
export async function getLocationByBarcode(barcode: string): Promise<LocationOut> {
  if (USE_FIXTURES) {
    const found = Object.values(LOCATIONS)
      .flat()
      .find((l) => l.barcode === barcode);
    if (!found) throw new NotFoundError("No location has that barcode.");
    return settle(found);
  }
  return apiRequest<LocationOut>(`/operations/locations/by-barcode/${encodeURIComponent(barcode)}`);
}

/* ---------------------------------- orders (Fulfilment F2) ---------------------------------- */
/**
 * Confirmed against the landed backend (`services/operations/app/schemas.py`,
 * `app/api/routes.py`), not the plan doc's summary alone. Every mutating call carries the order's
 * current `version`; a 409 on a stale one is ordinary optimistic-lock traffic — callers refetch
 * with `getOrder` and let the operator retry from the current state, never a raw toast of the
 * server's sentence as if the same request would work a second time. `libs/contracts/openapi/
 * operations.json` already carries the `orders` paths for this slice (checked before writing this
 * file), so this is a hand-typed mirror of a real, regenerable contract — not a guess the way F1's
 * `locations` functions above had to be when their contract had not landed yet.
 */

export interface ListOrdersParams {
  status?: OrderStatus | "all";
  channel?: OrderChannel | "all";
  warehouse_id?: string;
  page?: number;
  size?: number;
}

function matchesOrderFilters(order: OrderOut, params: ListOrdersParams): boolean {
  const matchesStatus = !params.status || params.status === "all" || order.status === params.status;
  const matchesChannel = !params.channel || params.channel === "all" || order.channel === params.channel;
  const matchesWarehouse = !params.warehouse_id || order.warehouse_id === params.warehouse_id;
  return matchesStatus && matchesChannel && matchesWarehouse;
}

/** `GET /operations/orders` — `operations.orders.order.read`, filterable by `status`/`channel`/
 *  `warehouse_id`. */
export async function listOrders(params: ListOrdersParams = {}): Promise<Page<OrderOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = ORDERS.filter((o) => matchesOrderFilters(o, params)).slice().sort((a, b) =>
      b.created_at.localeCompare(a.created_at),
    );
    return settle(paginateFixture(filtered, page, size));
  }
  const query = buildQuery({
    page,
    size,
    status: params.status === "all" ? undefined : params.status,
    channel: params.channel === "all" ? undefined : params.channel,
    warehouse_id: params.warehouse_id,
  });
  return apiRequest<Page<OrderOut>>(`/operations/orders${query}`);
}

export async function getOrder(id: string): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const found = ORDERS.find((o) => o.id === id);
    if (!found) throw new NotFoundError("That order could not be found.");
    return settle(found);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}`);
}

/** `POST /operations/orders/quote` — `operations.orders.order.read`. Read-only: posts nothing,
 *  creates no order. Never 422s over a pricing or availability gap — `fully_priced`/
 *  `fully_available` and each line's `priced`/`sufficient` flag say so instead, so a screen can
 *  render "can't price this yet" / "short by N" rather than a refusal. */
export async function quoteOrder(input: QuoteIn): Promise<QuoteOut> {
  if (USE_FIXTURES) {
    const warehouse = WAREHOUSES.find((w) => w.id === input.warehouse_id);
    if (!warehouse) throw new NotFoundError("That warehouse is not part of this organization");
    const currency = (input.currency ?? "INR").toUpperCase();
    let fullyPriced = true;
    let fullyAvailable = true;
    let subtotal = 0;
    const lines = input.lines.map((line) => {
      const item = INVENTORY.find((i) => i.id === line.inventory_item_id);
      const priceMinor = line.selling_price_minor ?? item?.selling_price_minor ?? null;
      const priced = priceMinor !== null;
      if (!priced) fullyPriced = false;
      const lineTotal = priced ? Math.round((qtyToMilli(line.quantity) * (priceMinor ?? 0)) / 1000) : null;
      if (lineTotal !== null) subtotal += lineTotal;
      const availableQty = item?.available_qty ?? "0";
      const sufficient = item !== undefined && qtyToMilli(availableQty) >= qtyToMilli(line.quantity);
      if (!sufficient) fullyAvailable = false;
      return {
        inventory_item_id: line.inventory_item_id,
        sku: item?.sku ?? "",
        quantity: line.quantity,
        priced,
        selling_price_minor: priceMinor,
        line_total_minor: lineTotal,
        available_qty: availableQty,
        sufficient,
      };
    });
    return settle({
      warehouse_id: input.warehouse_id,
      currency,
      subtotal_minor: subtotal,
      total_minor: subtotal,
      fully_priced: fullyPriced,
      fully_available: fullyAvailable,
      lines,
    });
  }
  return apiRequest<QuoteOut>("/operations/orders/quote", { method: "POST", body: input });
}

let fixtureOrderSeq = ORDERS.length;

/** `POST /operations/orders` — `operations.orders.order.create`. Creates a `DRAFT`; every line's
 *  price starts `null` — `place` is what fills it. */
export async function createOrder(input: OrderIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    if (!input.customer_id && !input.customer_name?.trim() && !input.customer_phone?.trim()) {
      throw refusal(422, "a customer_id, or a customer_name/customer_phone pair, is required");
    }
    const warehouse = WAREHOUSES.find((w) => w.id === input.warehouse_id);
    if (!warehouse) throw refusal(422, "warehouse_id does not match an existing warehouse");
    if (input.lines.length === 0) throw refusal(422, "An order needs at least one line");
    const lines = input.lines.map((line, i) => {
      const item = INVENTORY.find((it) => it.id === line.inventory_item_id);
      if (!item) throw refusal(422, "inventory_item_id does not match an existing item");
      return {
        id: fixtureId(`oline-${i}`),
        inventory_item_id: item.id,
        sku: item.sku,
        quantity: line.quantity,
        selling_price_minor: null,
        line_total_minor: null,
      };
    });
    fixtureOrderSeq += 1;
    const now = new Date().toISOString();
    const order: OrderOut = {
      id: fixtureId("ord"),
      number: `ORD-2026-${String(300 + fixtureOrderSeq).padStart(4, "0")}`,
      channel: input.channel,
      status: "DRAFT",
      version: 0,
      warehouse_id: input.warehouse_id,
      customer_id: input.customer_id ?? null,
      customer_name: input.customer_name ?? "",
      customer_phone: input.customer_phone ?? "",
      subtotal_minor: 0,
      total_minor: 0,
      currency: (input.currency ?? "INR").toUpperCase(),
      lines,
      created_at: now,
      updated_at: now,
    };
    ORDERS.unshift(order);
    return settle(order);
  }
  return apiRequest<OrderOut>("/operations/orders", { method: "POST", body: input });
}

function fixtureOrderOrThrow(id: string): OrderOut {
  const found = ORDERS.find((o) => o.id === id);
  if (!found) throw new NotFoundError("That order could not be found.");
  return found;
}

/** ADR-0025 §1's order state machine, mirrored from `ORDER_TRANSITIONS` in
 *  `services/operations/app/domain.py` — every other move, including a no-op "move" to the same
 *  status, is refused with a 409, the same convention `can_transition_po` documents. */
const ORDER_TRANSITIONS: Record<OrderStatus, readonly OrderStatus[]> = {
  DRAFT: ["PLACED", "CANCELLED"],
  PLACED: ["ACCEPTED", "REJECTED", "CANCELLED"],
  ACCEPTED: ["IN_FULFILMENT", "ON_HOLD", "CANCELLED"],
  IN_FULFILMENT: ["READY", "ON_HOLD", "CANCELLED"],
  ON_HOLD: ["ACCEPTED", "IN_FULFILMENT", "READY", "CANCELLED"],
  READY: ["HANDED_OVER", "ON_HOLD", "CANCELLED"],
  HANDED_OVER: ["DELIVERED"],
  DELIVERED: ["COMPLETED"],
  COMPLETED: [],
  CANCELLED: [],
  REJECTED: [],
};

function canTransitionFixtureOrder(current: OrderStatus, next: OrderStatus): boolean {
  return ORDER_TRANSITIONS[current].includes(next);
}

function assertFixtureVersion(order: OrderOut, expected: number): void {
  if (order.version !== expected) {
    throw refusal(
      409,
      `Order ${order.number} is at version ${order.version}, not ${expected}; reload and try again`,
    );
  }
}

/** The `InsufficientStock` shape (ADR-0014 §11) — distinct from the platform's ordinary
 *  `{field, code, message}` field errors, so a client reads `problem.errors[]` here as
 *  `{sku, requested, on_hand, reserved, available, …}` instead. */
function fixtureInsufficientStock(item: InventoryItemOut, requestedQty: string): ApiError {
  const detail =
    `Cannot take ${requestedQty} ${item.uom} of ${item.sku}: ${item.on_hand_qty} on hand, ` +
    `${item.reserved_qty} reserved, so ${item.available_qty} available`;
  // `InsufficientStock.errors` (ADR-0014 §11) is not the platform's ordinary `{field, code,
  // message}` extension shape `ProblemJson` declares — the same way `readProblem` in `http.ts`
  // blindly casts an arbitrary wire body to `ProblemJson`, this fixture mirrors a real problem
  // document's actual (wider) shape rather than the narrower type that shape is declared with.
  const problem = {
    type: "https://errors.opsgrids.dev/insufficient-stock",
    title: "Not enough stock",
    status: 409,
    detail,
    errors: [
      {
        sku: item.sku,
        uom: item.uom,
        requested: requestedQty,
        on_hand: item.on_hand_qty,
        reserved: item.reserved_qty,
        available: item.available_qty,
      },
    ],
  } as unknown as ProblemJson;
  return new ApiError(409, detail, problem);
}

/** `POST /operations/orders/{id}/place` — `operations.orders.order.write`. `DRAFT -> PLACED`:
 *  prices every line (from the line's own price, or the item's `selling_price_minor`) and
 *  reserves nothing. A 409 names every SKU that still has no price, on either side, when pricing
 *  fails — matching the real endpoint's own refusal shape. */
export async function placeOrder(id: string, input: OrderVersionIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const order = fixtureOrderOrThrow(id);
    assertFixtureVersion(order, input.version);
    if (!canTransitionFixtureOrder(order.status, "PLACED")) {
      throw refusal(409, `Order ${order.number} is ${order.status} and cannot be placed`);
    }
    if (order.lines.length === 0) {
      throw refusal(409, `Order ${order.number} has no lines to place`);
    }
    const unpriced: string[] = [];
    const pricedLines = order.lines.map((line) => {
      const item = INVENTORY.find((i) => i.id === line.inventory_item_id);
      const priceMinor = line.selling_price_minor ?? item?.selling_price_minor ?? null;
      if (priceMinor === null) unpriced.push(line.sku);
      const lineTotal = priceMinor === null ? null : Math.round((qtyToMilli(line.quantity) * priceMinor) / 1000);
      return { ...line, selling_price_minor: priceMinor, line_total_minor: lineTotal };
    });
    if (unpriced.length > 0) {
      throw refusal(409, `Cannot place ${order.number}: no selling price for ${unpriced.join(", ")}`);
    }
    const subtotal = pricedLines.reduce((sum, l) => sum + (l.line_total_minor ?? 0), 0);
    const index = ORDERS.indexOf(order);
    const updated: OrderOut = {
      ...order,
      status: "PLACED",
      version: order.version + 1,
      lines: pricedLines,
      subtotal_minor: subtotal,
      total_minor: subtotal,
      updated_at: new Date().toISOString(),
    };
    ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}/place`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/orders/{id}/accept` — `operations.orders.order.accept:ORG`. `PLACED ->
 *  ACCEPTED`: every line checked against available stock before any is reserved — a short line
 *  refuses the whole accept with `InsufficientStock`, and nothing reserves, the same
 *  all-or-nothing shape the kernel's `reserve_many` guarantees live. */
export async function acceptOrder(id: string, input: OrderVersionIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const order = fixtureOrderOrThrow(id);
    assertFixtureVersion(order, input.version);
    if (!canTransitionFixtureOrder(order.status, "ACCEPTED")) {
      throw refusal(409, `Order ${order.number} is ${order.status} and cannot be accepted`);
    }
    if (order.lines.length === 0) {
      throw refusal(409, `Order ${order.number} has no lines to accept`);
    }
    const items = order.lines.map((line) => {
      const item = INVENTORY.find((i) => i.id === line.inventory_item_id);
      if (!item) throw refusal(409, "An order line names an item no longer in this organization");
      if (qtyToMilli(item.available_qty) < qtyToMilli(line.quantity)) {
        throw fixtureInsufficientStock(item, line.quantity);
      }
      return { item, line };
    });
    for (const { item, line } of items) {
      const index = INVENTORY.indexOf(item);
      const reservedMilli = qtyToMilli(item.reserved_qty) + qtyToMilli(line.quantity);
      const availableMilli = qtyToMilli(item.available_qty) - qtyToMilli(line.quantity);
      INVENTORY[index] = {
        ...item,
        reserved_qty: milliToQty(reservedMilli),
        available_qty: milliToQty(availableMilli),
      };
    }
    const orderIndex = ORDERS.indexOf(order);
    const updated: OrderOut = {
      ...order,
      status: "ACCEPTED",
      version: order.version + 1,
      updated_at: new Date().toISOString(),
    };
    ORDERS[orderIndex] = updated;
    return settle(updated);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}/accept`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/orders/{id}/hold` — `operations.orders.order.write`. A reason is required. */
export async function holdOrder(id: string, input: OrderHoldIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const order = fixtureOrderOrThrow(id);
    assertFixtureVersion(order, input.version);
    if (!canTransitionFixtureOrder(order.status, "ON_HOLD")) {
      throw refusal(409, `Order ${order.number} is ${order.status} and cannot be held`);
    }
    const index = ORDERS.indexOf(order);
    const updated: OrderOut = { ...order, status: "ON_HOLD", version: order.version + 1, updated_at: new Date().toISOString() };
    ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}/hold`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/orders/{id}/release` — `operations.orders.order.write`. The target is
 *  recomputed from the order's own facts (ADR-0025 §1), never remembered: with no fulfilment
 *  tasks yet (F3 has not landed), a release always lands back on `ACCEPTED`. */
export async function releaseOrder(id: string, input: OrderVersionIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const order = fixtureOrderOrThrow(id);
    assertFixtureVersion(order, input.version);
    if (order.status !== "ON_HOLD") {
      throw refusal(409, `Order ${order.number} is ${order.status}, not on hold`);
    }
    const index = ORDERS.indexOf(order);
    const updated: OrderOut = { ...order, status: "ACCEPTED", version: order.version + 1, updated_at: new Date().toISOString() };
    ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}/release`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/orders/{id}/cancel` — `operations.orders.order.cancel:ORG`. A reason is
 *  required; releases every open reservation the order holds (accepted lines that have not yet
 *  moved past a reservation — pick/pack/dispatch are F3–F5, out of this phase). */
export async function cancelOrder(id: string, input: OrderCancelIn): Promise<OrderOut> {
  if (USE_FIXTURES) {
    const order = fixtureOrderOrThrow(id);
    assertFixtureVersion(order, input.version);
    if (!canTransitionFixtureOrder(order.status, "CANCELLED")) {
      throw refusal(409, `Order ${order.number} is ${order.status} and cannot be cancelled`);
    }
    if (order.status !== "DRAFT" && order.status !== "PLACED") {
      for (const line of order.lines) {
        const item = INVENTORY.find((i) => i.id === line.inventory_item_id);
        if (!item) continue;
        const index = INVENTORY.indexOf(item);
        const reservedMilli = Math.max(0, qtyToMilli(item.reserved_qty) - qtyToMilli(line.quantity));
        const availableMilli = qtyToMilli(item.available_qty) + qtyToMilli(line.quantity);
        INVENTORY[index] = {
          ...item,
          reserved_qty: milliToQty(reservedMilli),
          available_qty: milliToQty(availableMilli),
        };
      }
    }
    const index = ORDERS.indexOf(order);
    const updated: OrderOut = { ...order, status: "CANCELLED", version: order.version + 1, updated_at: new Date().toISOString() };
    ORDERS[index] = updated;
    return settle(updated);
  }
  return apiRequest<OrderOut>(`/operations/orders/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: input,
  });
}

/* -------------------------------- combobox helpers ------------------------ */

/** For the PO line editor's SKU picker — a small cap search over the org's inventory. */
export async function searchInventory(q: string): Promise<InventoryItemOut[]> {
  const result = await listInventory({ q, size: 20, sort: "sku" });
  return result.items;
}

/** For the order line editor's item picker — same small cap search, but keyed on the item id an
 *  order line actually carries (`inventory_item_id`), not the free-text SKU a PO line takes. */
export async function searchOrderableItems(q: string): Promise<InventoryItemOut[]> {
  const result = await listInventory({ q, size: 20, sort: "sku" });
  return result.items;
}

/* --------------------------------- overview -------------------------------- */

/**
 * `GET /operations/overview` — the dashboard's single server-side rollup: open POs, stock value,
 * low/out-of-stock counts, the real spend-weighted on-time rate, and the spend/status/supplier
 * breakdowns, computed over every row for this organization (never a client-side scan of one
 * fetched page). Fixture mode derives the identical shape from the same seed arrays every other
 * fixture function reads, using the pure rollups in `data/derive.ts` that mirror the server's
 * `app/domain.py` exactly.
 */
export async function getOverview(): Promise<OverviewOut> {
  if (USE_FIXTURES) {
    const currency = SUPPLIERS[0]?.currency ?? "INR";
    return settle({
      open_pos: openPoCount(PURCHASE_ORDERS),
      inventory_value_minor: inventoryValueMinor(INVENTORY),
      low_stock_count: lowStockItems(INVENTORY).length,
      out_of_stock_count: outOfStockCount(INVENTORY),
      on_time_pct: onTimeDeliveryPct(PURCHASE_ORDERS),
      currency,
      spend_trend: spendTrend(PURCHASE_ORDERS),
      spend_by_category: spendByCategory(PURCHASE_ORDERS),
      po_status_mix: poStatusMix(PURCHASE_ORDERS),
      top_suppliers: topSuppliers(SUPPLIERS),
      attention: attentionItems(PURCHASE_ORDERS, INVENTORY, FIXTURE_TODAY),
    });
  }
  return apiRequest<OverviewOut>("/operations/overview");
}

/* -------------------------------- fulfilment (Fulfilment F3, ADR-0024 §7, ADR-0025 §2/§3) -------------------------------- */
/**
 * The dispatcher's surface only: list/offer/cancel a pick task, and the worker roster. Scan, pick
 * and pack confirmation, exceptions and packaging are worker actions the worker PWA
 * (`frontend/worker-app`, a separate app) calls directly against the same backend — this file
 * declares no functions for them, since nothing here would ever call them.
 *
 * No fixture-mode branch (unlike every function above): `VITE_USE_FIXTURES` stays a dev-only,
 * offline-design aid, and wiring an in-memory pick-task/offer state machine into it is real
 * additional scope this slice does not take on. Every function below always calls the real
 * endpoint through `apiRequest`, "live by default" without the fixture fallback other screens get.
 */

export interface ListTasksParams {
  status?: PickTaskStatus | "all";
  warehouse_id?: string;
  order_id?: string;
  page?: number;
  size?: number;
}

/** `GET /operations/fulfilment/tasks` — `operations.fulfilment.task.read`. Each row already
 *  carries its lines, its whole offer history and any open exceptions (N+1 avoidance on the
 *  server side) — the dispatcher board renders from this one call, no per-row follow-up. */
export async function listTasks(params: ListTasksParams = {}): Promise<Page<PickTaskOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    status: params.status === "all" ? undefined : params.status,
    warehouse_id: params.warehouse_id,
    order_id: params.order_id,
  });
  return apiRequest<Page<PickTaskOut>>(`/operations/fulfilment/tasks${query}`);
}

export async function getTask(id: string): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(`/operations/fulfilment/tasks/${encodeURIComponent(id)}`);
}

/** `POST /operations/fulfilment/tasks/{id}/offers` — `operations.fulfilment.task.assign:ORG`.
 *  Manual assignment: the only path into `OFFERED` in F3 (no auto-assign engine yet), used for a
 *  fresh `CREATED` task and for a manager assigning an `ESCALATED` one by hand alike. */
export async function offerTask(id: string, input: OfferIn): Promise<AssignmentOfferOut> {
  return apiRequest<AssignmentOfferOut>(`/operations/fulfilment/tasks/${encodeURIComponent(id)}/offers`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/fulfilment/tasks/{id}/cancel` — `operations.fulfilment.task.assign:ORG`. A
 *  task already `ACCEPTED`/`IN_PROGRESS`/`EXCEPTION` additionally needs
 *  `operations.fulfilment.task.override` — the caller gates the button on that, this call just
 *  carries whichever permission the caller actually holds through to the same endpoint. */
export async function cancelTask(id: string, input: TaskCancelIn): Promise<PickTaskOut> {
  return apiRequest<PickTaskOut>(`/operations/fulfilment/tasks/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: input,
  });
}

export interface ListWorkersParams {
  warehouse_id?: string;
  is_active?: boolean;
  q?: string;
  page?: number;
  size?: number;
}

/** `GET /operations/fulfilment/workers` — `operations.fulfilment.worker.read`. */
export async function listWorkers(params: ListWorkersParams = {}): Promise<Page<WorkerOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    warehouse_id: params.warehouse_id,
    is_active: params.is_active,
    q: params.q,
  });
  return apiRequest<Page<WorkerOut>>(`/operations/fulfilment/workers${query}`);
}

/** `POST /operations/fulfilment/workers` — `operations.fulfilment.worker.write:ORG`. */
export async function createWorker(input: WorkerIn): Promise<WorkerOut> {
  return apiRequest<WorkerOut>("/operations/fulfilment/workers", { method: "POST", body: input });
}

/** `PATCH /operations/fulfilment/workers/{id}` — `operations.fulfilment.worker.write:ORG`.
 *  `skills`/`zone_location_ids`, when given, replace the whole set. */
export async function updateWorker(id: string, patch: WorkerPatch): Promise<WorkerOut> {
  return apiRequest<WorkerOut>(`/operations/fulfilment/workers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/* ------------------------------------ dispatch (Fulfilment F5, ADR-0024 §6) ------------------------------------ */
/**
 * The carrier handover and a shipment's own lifecycle after that. No fixture-mode branch, the
 * same precedent the fulfilment section above sets: every function here always calls the real
 * endpoint through `apiRequest`.
 */

export interface ListShipmentsParams {
  status?: ShipmentStatus | "all";
  order_id?: string;
  warehouse_id?: string;
  page?: number;
  size?: number;
}

/** `GET /operations/dispatch/shipments` — `operations.dispatch.shipment.read`. */
export async function listShipments(params: ListShipmentsParams = {}): Promise<Page<ShipmentOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    status: params.status === "all" ? undefined : params.status,
    order_id: params.order_id,
    warehouse_id: params.warehouse_id,
  });
  return apiRequest<Page<ShipmentOut>>(`/operations/dispatch/shipments${query}`);
}

export async function getShipment(id: string): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>(`/operations/dispatch/shipments/${encodeURIComponent(id)}`);
}

/** `POST /operations/dispatch/shipments` — `operations.dispatch.shipment.write:ORG`. The carrier
 *  handover: creates the shipment and moves the order through the billing gate in one call, the
 *  delivery/courier counterpart to `hand_over_order` (`acceptOrder`/`holdOrder`'s neighbour
 *  above). A 409 here is the same billing-gate refusal `OrderDetail`'s counter path would see. */
export async function dispatchOrder(input: ShipmentIn): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>("/operations/dispatch/shipments", { method: "POST", body: input });
}

/** `POST /operations/dispatch/shipments/{id}/in-transit` — `operations.dispatch.shipment.write:ORG`.
 *  Also the retry path from `FAILED`. */
export async function markShipmentInTransit(id: string, input: ShipmentAdvanceIn = {}): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>(`/operations/dispatch/shipments/${encodeURIComponent(id)}/in-transit`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/dispatch/shipments/{id}/deliver` — `operations.dispatch.shipment.write:ORG`. */
export async function deliverShipment(id: string, input: ShipmentAdvanceIn = {}): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>(`/operations/dispatch/shipments/${encodeURIComponent(id)}/deliver`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/dispatch/shipments/{id}/fail` — `operations.dispatch.shipment.write:ORG`. A
 *  reason is required. */
export async function failShipment(id: string, input: ShipmentRefusalIn): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>(`/operations/dispatch/shipments/${encodeURIComponent(id)}/fail`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/dispatch/shipments/{id}/return` — `operations.dispatch.shipment.write:ORG`.
 *  A reason is required. */
export async function returnShipment(id: string, input: ShipmentRefusalIn): Promise<ShipmentOut> {
  return apiRequest<ShipmentOut>(`/operations/dispatch/shipments/${encodeURIComponent(id)}/return`, {
    method: "POST",
    body: input,
  });
}


/* -------------------------------- warehouse geometry (Fulfilment plan §2/F7) -------------------------------- */
/**
 * Floors, versioned layouts, a draft's placements/walk graph, validation, publish/rollback, and
 * routing — `app.warehouse` on the backend (`services/operations/app/api/routes.py`). Hand-rolled
 * against the landed contract (`libs/contracts/openapi/operations.json`), the same convention
 * every other section of this file follows.
 *
 * **Live only — no `USE_FIXTURES` branch.** This is a back-office floor-plan editor a handful of
 * warehouse admins use, not a screen any fixture-mode demo or component test exercises; adding a
 * second, in-memory implementation of a full versioned-geometry-and-graph-routing backend for a
 * page nothing reads in that mode was not a good trade against the size that code would be. If a
 * fixture-mode demo of this screen is ever wanted, add it the same way every function above does.
 */

export async function listWarehouseFloors(
  warehouseId: string,
  params: { signal?: AbortSignal } = {},
): Promise<WarehouseFloorOut[]> {
  const query = buildQuery({ size: FULL_PAGE });
  const page = await apiRequest<Page<WarehouseFloorOut>>(
    `/operations/warehouses/${encodeURIComponent(warehouseId)}/floors${query}`,
    { signal: params.signal },
  );
  return page.items;
}

/** `GET /warehouse-floors/{id}` — one floor. */
export async function getWarehouseFloor(id: string): Promise<WarehouseFloorOut> {
  return apiRequest<WarehouseFloorOut>(`/operations/warehouse-floors/${encodeURIComponent(id)}`);
}

/** `POST /warehouses/{warehouse_id}/floors` — `operations.warehouse.floor.write`. 409 on a
 *  duplicate floor number in this warehouse. */
export async function createWarehouseFloor(
  warehouseId: string,
  input: WarehouseFloorIn,
): Promise<WarehouseFloorOut> {
  return apiRequest<WarehouseFloorOut>(`/operations/warehouses/${encodeURIComponent(warehouseId)}/floors`, {
    method: "POST",
    body: input,
  });
}

/** `PATCH /warehouse-floors/{id}` — `operations.warehouse.floor.write`. */
export async function updateWarehouseFloor(id: string, patch: WarehouseFloorPatch): Promise<WarehouseFloorOut> {
  return apiRequest<WarehouseFloorOut>(`/operations/warehouse-floors/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** A floor's whole version history — draft, published and archived alike (never deleted once
 *  published), newest first. What the version-history / rollback list renders. */
export async function listLayoutVersions(
  floorId: string,
  params: { signal?: AbortSignal } = {},
): Promise<LayoutVersionOut[]> {
  const query = buildQuery({ size: FULL_PAGE });
  const page = await apiRequest<Page<LayoutVersionOut>>(
    `/operations/warehouse-floors/${encodeURIComponent(floorId)}/layout-versions${query}`,
    { signal: params.signal },
  );
  return page.items;
}

/** `POST /warehouse-floors/{floor_id}/layout-versions` — `operations.warehouse.layout.write`.
 *  409 while a draft is already open for this floor. */
export async function createLayoutVersion(
  floorId: string,
  input: LayoutVersionCreateIn = {},
): Promise<LayoutVersionOut> {
  return apiRequest<LayoutVersionOut>(
    `/operations/warehouse-floors/${encodeURIComponent(floorId)}/layout-versions`,
    { method: "POST", body: input },
  );
}

/** `GET /layout-versions/{id}` — one version's full geometry (placements + walk graph), what the
 *  canvas editor loads to render a draft or inspect a past published/archived version. */
export async function getLayoutGeometry(
  versionId: string,
  params: { signal?: AbortSignal } = {},
): Promise<LayoutGeometryOut> {
  return apiRequest<LayoutGeometryOut>(`/operations/layout-versions/${encodeURIComponent(versionId)}`, {
    signal: params.signal,
  });
}

/** `PUT /layout-versions/{id}/geometry` — `operations.warehouse.layout.write`. A full replace;
 *  see `LayoutGeometryIn`'s own docstring. 409 on anything but a `draft` version. */
export async function saveLayoutGeometry(
  versionId: string,
  input: LayoutGeometryIn,
): Promise<LayoutGeometryOut> {
  return apiRequest<LayoutGeometryOut>(`/operations/layout-versions/${encodeURIComponent(versionId)}/geometry`, {
    method: "PUT",
    body: input,
  });
}

/** `DELETE /layout-versions/{id}` — `operations.warehouse.layout.write`. Discards a draft
 *  outright; 409 on anything but a `draft` version. */
export async function discardLayoutVersion(versionId: string): Promise<void> {
  await apiRequest<undefined>(`/operations/layout-versions/${encodeURIComponent(versionId)}`, {
    method: "DELETE",
  });
}

/** `POST /layout-versions/{id}/validate` — `operations.warehouse.layout.read`. Read-only: checks
 *  for overlapping placements and disconnected required walk nodes without publishing anything. */
export async function validateLayoutVersion(versionId: string): Promise<LayoutValidationOut> {
  return apiRequest<LayoutValidationOut>(
    `/operations/layout-versions/${encodeURIComponent(versionId)}/validate`,
    { method: "POST" },
  );
}

/** `POST /layout-versions/{id}/publish` — `operations.warehouse.layout.publish` (dangerous).
 *  Publishes a draft, or rolls back by re-publishing an `archived` version. 422 when the layout
 *  fails validation; 409 when the version is not eligible to publish at all. */
export async function publishLayoutVersion(versionId: string): Promise<LayoutVersionOut> {
  return apiRequest<LayoutVersionOut>(`/operations/layout-versions/${encodeURIComponent(versionId)}/publish`, {
    method: "POST",
  });
}

/**
 * `GET /operations/floors/{floor_id}/published-layout` — `operations.warehouse.layout.read`. A floor's
 * *published* placements only (a draft never leaks), each with its location's code, name, kind and
 * parent. `published: false` with no placements when the floor has no published version; 404 for a
 * floor that is missing or belongs to another organization.
 */
export async function getPublishedLayout(
  floorId: string,
  params: { signal?: AbortSignal } = {},
): Promise<PublishedLayoutOut> {
  return apiRequest<PublishedLayoutOut>(`/operations/floors/${encodeURIComponent(floorId)}/published-layout`, {
    signal: params.signal,
  });
}

/** `GET /warehouse-floors/{floor_id}/route?from_location_id=&to_location_id=` —
 *  `operations.warehouse.layout.read`. The shortest walk path between two locations on the
 *  floor's **published** layout. 404 when there is no published layout or either location has no
 *  walk node in it; 409 when the graph has no traversable path between them. */
export async function computeWarehouseRoute(
  floorId: string,
  fromLocationId: string,
  toLocationId: string,
  params: { signal?: AbortSignal } = {},
): Promise<RouteOut> {
  const query = buildQuery({ from_location_id: fromLocationId, to_location_id: toLocationId });
  return apiRequest<RouteOut>(`/operations/warehouse-floors/${encodeURIComponent(floorId)}/route${query}`, {
    signal: params.signal,
  });
}

/* ------------------------------------ counting (ADR-0026, Fulfilment plan F8) ------------------------------------ */
/**
 * The manager's surface: open/review/close/cancel a session, and approve or reject a discrepancy.
 * Counting a line is the worker PWA's job (`frontend/worker-app`) — this file declares no
 * function for it. No fixture-mode branch, the same "live by default" shape `fulfilment`/
 * `dispatch` above already set.
 */

export interface ListCountSessionsParams {
  warehouse_id?: string;
  status?: CountSessionStatus | "all";
  page?: number;
  size?: number;
}

/** `GET /operations/counting/sessions` — `operations.inventory.count.read`. */
export async function listCountSessions(
  params: ListCountSessionsParams = {},
): Promise<Page<CountSessionOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    warehouse_id: params.warehouse_id,
    status: params.status === "all" ? undefined : params.status,
  });
  return apiRequest<Page<CountSessionOut>>(`/operations/counting/sessions${query}`);
}

export async function getCountSession(id: string): Promise<CountSessionOut> {
  return apiRequest<CountSessionOut>(`/operations/counting/sessions/${encodeURIComponent(id)}`);
}

/** `POST /operations/counting/sessions` — `operations.inventory.count.manage:ORG`. Refused (409)
 *  while the warehouse already has a session `ACTIVE` or under `REVIEW` (ADR-0026 §5). */
export async function openCountSession(input: CountSessionIn): Promise<CountSessionOut> {
  return apiRequest<CountSessionOut>("/operations/counting/sessions", { method: "POST", body: input });
}

/** `POST /operations/counting/sessions/{id}/review` — ACTIVE -> REVIEW. */
export async function startCountReview(id: string): Promise<CountSessionOut> {
  return apiRequest<CountSessionOut>(
    `/operations/counting/sessions/${encodeURIComponent(id)}/review`,
    { method: "POST" },
  );
}

/** `POST /operations/counting/sessions/{id}/close` — REVIEW -> CLOSED. Refused (409) while any
 *  line is still `PENDING` review. */
export async function closeCountSession(id: string): Promise<CountSessionOut> {
  return apiRequest<CountSessionOut>(`/operations/counting/sessions/${encodeURIComponent(id)}/close`, {
    method: "POST",
  });
}

/** `POST /operations/counting/sessions/{id}/cancel`. */
export async function cancelCountSession(
  id: string,
  input: CountSessionCancelIn = {},
): Promise<CountSessionOut> {
  return apiRequest<CountSessionOut>(`/operations/counting/sessions/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: input,
  });
}

export interface ListCountLinesParams {
  review_status?: CountLineReviewStatus;
  page?: number;
  size?: number;
}

/** `GET /operations/counting/sessions/{id}/lines` — `operations.inventory.count.read`. */
export async function listCountLines(
  sessionId: string,
  params: ListCountLinesParams = {},
): Promise<Page<CountLineOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    review_status: params.review_status,
  });
  return apiRequest<Page<CountLineOut>>(
    `/operations/counting/sessions/${encodeURIComponent(sessionId)}/lines${query}`,
  );
}

/** `POST /operations/counting/sessions/{id}/lines/{line_id}/review` —
 *  `operations.inventory.count.review:ORG`. Approving posts through the stock kernel in the same
 *  call; rejecting needs a note and never touches the ledger. */
export async function reviewCountLine(
  sessionId: string,
  lineId: string,
  input: CountLineReviewIn,
): Promise<CountLineOut> {
  return apiRequest<CountLineOut>(
    `/operations/counting/sessions/${encodeURIComponent(sessionId)}/lines/${encodeURIComponent(lineId)}/review`,
    { method: "POST", body: input },
  );
}

/* ----------------------------- operational alerts and insights (F12) ----------------------------
 * No fixture data of its own yet (`USE_FIXTURES` renders an empty list/zero series rather than
 * fabricated numbers) — every value on these screens is a live read from `app.alerts`, and this
 * module ships no synthetic series to stand in for it. */

export interface ListAlertsParams {
  kind?: AlertKind | undefined;
  include_resolved?: boolean;
  page?: number;
  size?: number;
  signal?: AbortSignal;
}

/** `GET /operations/alerts` — `operations.automation.alert.read:ORG`. */
export async function listAlerts(params: ListAlertsParams = {}): Promise<Page<OperationalAlertOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    return settle({ items: [], total: 0, page, size, pages: 0 });
  }
  const query = buildQuery({ page, size, kind: params.kind, include_resolved: params.include_resolved });
  return apiRequest<Page<OperationalAlertOut>>(`/operations/alerts${query}`, { signal: params.signal });
}

/** `POST /operations/alerts/{id}/resolve` — `operations.automation.alert.manage:ORG`. Dismisses
 *  an open alert by hand, before the sweep would close it itself. */
export async function resolveAlert(id: string): Promise<OperationalAlertOut> {
  if (USE_FIXTURES) throw new NotFoundError("That alert could not be found.");
  return apiRequest<OperationalAlertOut>(`/operations/alerts/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
  });
}

/** `GET /operations/insights/backlog` — `operations.automation.insight.read:ORG`. */
export async function insightBacklog(): Promise<TaskBacklogOut[]> {
  if (USE_FIXTURES) return settle([]);
  return apiRequest<TaskBacklogOut[]>("/operations/insights/backlog");
}

/** `GET /operations/insights/throughput` — `operations.automation.insight.read:ORG`. */
export async function insightThroughput(days = 7): Promise<ThroughputOut[]> {
  if (USE_FIXTURES) return settle([]);
  return apiRequest<ThroughputOut[]>(`/operations/insights/throughput${buildQuery({ days })}`);
}

/** `GET /operations/insights/bottlenecks` — `operations.automation.insight.read:ORG`. */
export async function insightBottlenecks(days = 7): Promise<ZoneBottleneckOut[]> {
  if (USE_FIXTURES) return settle([]);
  return apiRequest<ZoneBottleneckOut[]>(`/operations/insights/bottlenecks${buildQuery({ days })}`);
}

/** `GET /operations/insights/workload` — `operations.automation.insight.read:ORG`. */
export async function insightWorkload(): Promise<WorkerWorkloadOut[]> {
  if (USE_FIXTURES) return settle([]);
  return apiRequest<WorkerWorkloadOut[]>("/operations/insights/workload");
}

/** `GET /operations/insights/discrepancies` — `operations.automation.insight.read:ORG`. */
export async function insightDiscrepancyTrend(days = 30): Promise<DiscrepancyTrendPointOut[]> {
  if (USE_FIXTURES) return settle([]);
  return apiRequest<DiscrepancyTrendPointOut[]>(`/operations/insights/discrepancies${buildQuery({ days })}`);
}


/* ------------------------------------ online payment (ADR-0024 A4, Fulfilment F10) ------------------------------------ */
/**
 * An order's online payment, and the organization's own payment-provider connection (Razorpay or
 * Cashfree, ADR-0030). No fixture-mode branch (the dispatch section's precedent): every function
 * calls the real endpoint.
 *
 * Nothing here can mark an order paid. `startOrderCheckout` returns what the provider's hosted
 * checkout needs (Razorpay: a public key id and an Order id; Cashfree: a payment session id);
 * whether the customer then paid is asked of the provider server-side by `syncOrderPayment`,
 * which sends no body at all — whatever the checkout's callback says is never sent anywhere.
 */

/** `GET /operations/orders/{id}/payments` — `operations.orders.order.read`. */
export async function getOrderPayments(orderId: string): Promise<OrderPaymentsOut> {
  return apiRequest<OrderPaymentsOut>(`/operations/orders/${encodeURIComponent(orderId)}/payments`);
}

/** `POST /operations/orders/{id}/payments/checkout` — `operations.orders.payment.collect:ORG`.
 *  The amount is the order's own stored total, decided by the server; nothing is sent. */
export async function startOrderCheckout(orderId: string): Promise<OrderCheckoutOut> {
  return apiRequest<OrderCheckoutOut>(`/operations/orders/${encodeURIComponent(orderId)}/payments/checkout`, {
    method: "POST",
  });
}

/** `POST /operations/orders/{id}/payments/{charge}/sync` — the server asks the provider. */
export async function syncOrderPayment(orderId: string, chargeId: string): Promise<OrderCollectionOut> {
  return apiRequest<OrderCollectionOut>(
    `/operations/orders/${encodeURIComponent(orderId)}/payments/${encodeURIComponent(chargeId)}/sync`,
    { method: "POST" },
  );
}

/** `POST /operations/orders/{id}/payments/{charge}/cancel` — only an unpaid checkout. */
export async function cancelOrderPayment(orderId: string, chargeId: string): Promise<OrderCollectionOut> {
  return apiRequest<OrderCollectionOut>(
    `/operations/orders/${encodeURIComponent(orderId)}/payments/${encodeURIComponent(chargeId)}/cancel`,
    { method: "POST" },
  );
}

/** `POST /operations/orders/{id}/payments/{charge}/refunds` — `operations.orders.payment.refund:ORG`.
 *  `idempotencyKey` is minted once per refund the operator means to make, and re-sent on a retry,
 *  so a retry after a lost answer is the same refund — here and at the provider. */
export async function refundOrderPayment(
  orderId: string,
  chargeId: string,
  input: OrderRefundIn,
  idempotencyKey: string,
): Promise<OrderCollectionOut> {
  return apiRequest<OrderCollectionOut>(
    `/operations/orders/${encodeURIComponent(orderId)}/payments/${encodeURIComponent(chargeId)}/refunds`,
    { method: "POST", body: input, headers: { "Idempotency-Key": idempotencyKey } },
  );
}

/** `GET /payment/collections/merchant-account` — `operations.payments.merchant_account.read:ORG`.
 *  Secrets come back masked, always. */
export async function getMerchantAccount(): Promise<MerchantAccountOut> {
  return apiRequest<MerchantAccountOut>("/payment/collections/merchant-account");
}

/** `PUT /payment/collections/merchant-account` — `operations.payments.merchant_account.write:ORG`.
 *  Connects, or rotates in place (the webhook URL is kept). The secrets are sealed on arrival. */
export async function saveMerchantAccount(input: MerchantAccountIn): Promise<MerchantAccountOut> {
  return apiRequest<MerchantAccountOut>("/payment/collections/merchant-account", { method: "PUT", body: input });
}

/** `DELETE /payment/collections/merchant-account` — `operations.payments.merchant_account.write:ORG`
 *  and a one-time-code session. Refused (409, with the count) while an online payment is open. */
export async function removeMerchantAccount(): Promise<void> {
  await apiRequest<undefined>("/payment/collections/merchant-account", { method: "DELETE" });
}

/** `POST /payment/collections/merchant-account/verify` — the server asks the provider, with the
 *  account's own keys, whether they work, and records the answer. A 422 `credentials-rejected`
 *  means the provider refused them (recorded too); a 503 that it could not be asked. */
export async function verifyMerchantAccount(): Promise<MerchantAccountOut> {
  return apiRequest<MerchantAccountOut>("/payment/collections/merchant-account/verify", { method: "POST" });
}

/** `GET /payment/collections/reconciliation` — `operations.payments.reconciliation.read:ORG`.
 *  Read-only: it lists disagreements with the provider and corrects nothing. */
export async function getPaymentReconciliation(): Promise<ReconciliationOut> {
  return apiRequest<ReconciliationOut>("/payment/collections/reconciliation");
}


/* ------------------------------------ channels (Fulfilment F11) ------------------------------------ */
/**
 * External sales-channel connections. No fixture-mode branch — the same precedent dispatch sets
 * above: every function here always calls the real endpoint through `apiRequest`, backed by the
 * real connector and the self-hosted sandbox marketplace (`bin/mock_marketplace`), never fake
 * numbers.
 */

export interface ListChannelsParams {
  status?: ChannelStatus | "all";
  page?: number;
  size?: number;
}

/** `GET /operations/channels` — `operations.channels.channel.read`. */
export async function listChannels(params: ListChannelsParams = {}): Promise<Page<ChannelOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    status: params.status === "all" ? undefined : params.status,
  });
  return apiRequest<Page<ChannelOut>>(`/operations/channels${query}`);
}

export async function getChannel(id: string): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${encodeURIComponent(id)}`);
}

/** `POST /operations/channels` — `operations.channels.channel.write:ORG`. Connects a new channel;
 *  only `provider: "mock_marketplace"` exists in this build. */
export async function connectChannel(input: ChannelConnectIn): Promise<ChannelOut> {
  return apiRequest<ChannelOut>("/operations/channels", { method: "POST", body: input });
}

/** `PATCH /operations/channels/{id}` — `operations.channels.channel.write:ORG`. */
export async function updateChannel(id: string, patch: ChannelConfigPatch): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `POST /operations/channels/{id}/reconnect` — `operations.channels.channel.write:ORG`. Rotates
 *  the credential and clears an `error` status. */
export async function reconnectChannel(id: string, input: ChannelReconnectIn): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${encodeURIComponent(id)}/reconnect`, {
    method: "POST",
    body: input,
  });
}

/** `POST /operations/channels/{id}/disconnect` — `operations.channels.channel.write:ORG`. */
export async function disconnectChannel(id: string): Promise<ChannelOut> {
  return apiRequest<ChannelOut>(`/operations/channels/${encodeURIComponent(id)}/disconnect`, {
    method: "POST",
  });
}

/** `GET /operations/channels/{id}/sku-mappings` — `operations.channels.channel.read`. */
export async function listSkuMappings(
  channelId: string,
  params: { page?: number; size?: number } = {},
): Promise<Page<ChannelSkuMappingOut>> {
  const query = buildQuery({ page: params.page ?? 1, size: params.size ?? FULL_PAGE });
  return apiRequest<Page<ChannelSkuMappingOut>>(
    `/operations/channels/${encodeURIComponent(channelId)}/sku-mappings${query}`,
  );
}

/** `POST /operations/channels/{id}/sku-mappings` — `operations.channels.channel.write:ORG`. Creates
 *  or updates the mapping for `input.external_sku`. */
export async function upsertSkuMapping(
  channelId: string,
  input: ChannelSkuMappingIn,
): Promise<ChannelSkuMappingOut> {
  return apiRequest<ChannelSkuMappingOut>(
    `/operations/channels/${encodeURIComponent(channelId)}/sku-mappings`,
    { method: "POST", body: input },
  );
}

/** `DELETE /operations/channels/{id}/sku-mappings/{mappingId}` —
 *  `operations.channels.channel.write:ORG`. */
export async function deleteSkuMapping(channelId: string, mappingId: string): Promise<void> {
  await apiRequest<void>(
    `/operations/channels/${encodeURIComponent(channelId)}/sku-mappings/${encodeURIComponent(mappingId)}`,
    { method: "DELETE" },
  );
}

export interface ListSyncLogsParams {
  sync_type?: ChannelSyncType;
  status?: ChannelSyncStatus | "all";
  page?: number;
  size?: number;
}

/** `GET /operations/channels/{id}/sync-logs` — `operations.channels.channel.read`. */
export async function listSyncLogs(
  channelId: string,
  params: ListSyncLogsParams = {},
): Promise<Page<ChannelSyncLogOut>> {
  const query = buildQuery({
    page: params.page ?? 1,
    size: params.size ?? DEFAULT_PAGE_SIZE,
    sync_type: params.sync_type,
    status: params.status === "all" ? undefined : params.status,
  });
  return apiRequest<Page<ChannelSyncLogOut>>(
    `/operations/channels/${encodeURIComponent(channelId)}/sync-logs${query}`,
  );
}

/** `POST /operations/channels/{id}/sync/orders` — `operations.channels.channel.sync:ORG`. Polls
 *  the channel for new orders right now instead of waiting for the scheduled sync. */
export async function syncChannelOrdersNow(channelId: string): Promise<ChannelSyncResultOut> {
  return apiRequest<ChannelSyncResultOut>(
    `/operations/channels/${encodeURIComponent(channelId)}/sync/orders`,
    { method: "POST" },
  );
}

/** `POST /operations/channels/{id}/sync/inventory` — `operations.channels.channel.sync:ORG`. */
export async function syncChannelInventoryNow(channelId: string): Promise<ChannelSyncResultOut> {
  return apiRequest<ChannelSyncResultOut>(
    `/operations/channels/${encodeURIComponent(channelId)}/sync/inventory`,
    { method: "POST" },
  );
}

/** `POST /operations/channels/{id}/health-check` — `operations.channels.channel.sync:ORG`. */
export async function checkChannelHealth(channelId: string): Promise<ChannelHealthCheckOut> {
  return apiRequest<ChannelHealthCheckOut>(
    `/operations/channels/${encodeURIComponent(channelId)}/health-check`,
    { method: "POST" },
  );
}

/** `POST /operations/channels/{id}/sync-logs/{logId}/retry` —
 *  `operations.channels.channel.sync:ORG`. Re-attempts a failed sync log entry from current data. */
export async function retryChannelSyncLog(
  channelId: string,
  logId: string,
): Promise<ChannelRetryResultOut> {
  return apiRequest<ChannelRetryResultOut>(
    `/operations/channels/${encodeURIComponent(channelId)}/sync-logs/${encodeURIComponent(logId)}/retry`,
    { method: "POST" },
  );
}
