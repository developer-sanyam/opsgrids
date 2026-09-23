/**
 * Live-mode client tests: `USE_FIXTURES` is off by default in this suite (it only turns on with
 * `VITE_USE_FIXTURES=true`, and tests don't set it), so every function below hits `apiRequest`,
 * asserting the exact method, URL and JSON body the gateway receives.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  adjustStock,
  approvePurchaseOrder,
  cancelPurchaseOrder,
  closePurchaseOrder,
  createInventoryItem,
  createItemCategory,
  createItemVariant,
  createPurchaseOrder,
  createSupplier,
  createWarehouse,
  deleteInventoryItem,
  deleteItemAttribute,
  deleteItemCategory,
  deleteItemVariant,
  deletePurchaseOrder,
  deleteSupplier,
  deleteWarehouse,
  getInventoryItem,
  listInventory,
  listItemAttributes,
  listItemCategories,
  listItemVariants,
  listMovements,
  listPurchaseOrders,
  listSuppliers,
  listUoms,
  listWarehouses,
  receivePurchaseOrder,
  setItemAttribute,
  updateInventoryItem,
  updateItemCategory,
  updateItemVariant,
  updatePurchaseOrder,
  updateSupplier,
  updateWarehouse,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body, status));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requested(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  return { url: call[0], init: call[1] };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const PAGE_ENVELOPE = { items: [], total: 0, page: 1, size: 25, pages: 0 };

describe("listSuppliers", () => {
  it("passes q, status and sort through to the query string", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listSuppliers({ q: "meridian", status: "active", sort: "spend", page: 2, size: 10 });
    const { url, init } = requested(fetchMock);
    expect(url).toBe(
      "/api/v1/operations/suppliers?page=2&size=10&q=meridian&status=active&sort=spend",
    );
    expect(init.method).toBe("GET");
  });

  it("omits an 'all' filter rather than sending it literally", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listSuppliers({ status: "all", category: "all" });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/suppliers?page=1&size=25");
  });
});

describe("createSupplier", () => {
  it("POSTs the SupplierIn body verbatim", async () => {
    const fetchMock = stubFetch({ id: "sup-1" }, 201);
    const body = { code: "SUP-NEW", name: "New Co", category: "mro" as const };
    await createSupplier(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/suppliers");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateSupplier", () => {
  it("PATCHes only the changed fields", async () => {
    const fetchMock = stubFetch({ id: "sup-1" });
    await updateSupplier("sup-1", { status: "on_hold" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/suppliers/sup-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ status: "on_hold" });
  });
});

describe("deleteSupplier", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteSupplier("sup-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/suppliers/sup-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("listPurchaseOrders", () => {
  it("passes supplier_id through", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listPurchaseOrders({ supplier_id: "sup-1", status: "draft" });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders?page=1&size=25&status=draft&supplier_id=sup-1");
  });
});

describe("createPurchaseOrder", () => {
  it("POSTs the PurchaseOrderIn body verbatim, without a client-supplied status or number", async () => {
    const fetchMock = stubFetch({ id: "po-1" }, 201);
    const body = {
      supplier_id: "sup-1",
      warehouse: "Pune Plant",
      expected_date: "2026-09-30",
      lines: [{ sku: "RM-STL-4501", quantity: 10, unit_price_minor: 62500 }],
    };
    await createPurchaseOrder(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updatePurchaseOrder", () => {
  it("PATCHes the draft with a full line replacement when lines are present", async () => {
    const fetchMock = stubFetch({ id: "po-1" });
    const patch = { lines: [{ sku: "RM-STL-4501", quantity: 5, unit_price_minor: 62500 }] };
    await updatePurchaseOrder("po-1", patch);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual(patch);
  });
});

describe("deletePurchaseOrder", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deletePurchaseOrder("po-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("approvePurchaseOrder", () => {
  it("POSTs with no body", async () => {
    const fetchMock = stubFetch({ id: "po-1", status: "approved" });
    await approvePurchaseOrder("po-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1/approve");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});

describe("receivePurchaseOrder", () => {
  it("POSTs the optional received_date body", async () => {
    const fetchMock = stubFetch({ id: "po-1", status: "received" });
    await receivePurchaseOrder("po-1", { received_date: "2026-09-01" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1/receive");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ received_date: "2026-09-01" });
  });
});

describe("closePurchaseOrder", () => {
  it("POSTs with no body", async () => {
    const fetchMock = stubFetch({ id: "po-1", status: "closed" });
    await closePurchaseOrder("po-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1/close");
    expect(init.method).toBe("POST");
  });
});

describe("cancelPurchaseOrder", () => {
  it("POSTs the required reason", async () => {
    const fetchMock = stubFetch({ id: "po-1", status: "cancelled" });
    await cancelPurchaseOrder("po-1", { reason: "Budget withdrawn" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/purchase-orders/po-1/cancel");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ reason: "Budget withdrawn" });
  });
});

describe("listInventory", () => {
  it("passes low_stock through as a real boolean query param", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listInventory({ low_stock: true, sort: "value" });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory?page=1&size=25&low_stock=true&sort=value");
  });
});

describe("getInventoryItem", () => {
  it("calls GET /operations/inventory/:id", async () => {
    const fetchMock = stubFetch({ id: "inv-1" });
    await getInventoryItem("inv-1");
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1");
  });
});

describe("createInventoryItem", () => {
  it("POSTs the InventoryItemIn body verbatim, never on_hand", async () => {
    const fetchMock = stubFetch({ id: "inv-1" }, 201);
    const body = {
      sku: "RM-NEW-001",
      name: "New raw material",
      category: "raw_materials" as const,
      warehouse: "Pune Plant",
      uom: "MT",
      opening_on_hand: 40,
    };
    await createInventoryItem(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateInventoryItem", () => {
  it("PATCHes only the changed card fields", async () => {
    const fetchMock = stubFetch({ id: "inv-1" });
    await updateInventoryItem("inv-1", { reorder_point: 50 });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ reorder_point: 50 });
  });
});

describe("deleteInventoryItem", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteInventoryItem("inv-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("adjustStock", () => {
  it("POSTs a delta adjustment with its reason", async () => {
    const fetchMock = stubFetch({ id: "inv-1", on_hand: 12 }, 201);
    const body = { quantity_delta: -5, reason: "damaged" as const, note: "Water damage", occurred_on: "2026-09-01" };
    await adjustStock("inv-1", body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/adjustments");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });

  it("POSTs a counted-quantity adjustment with no reason", async () => {
    const fetchMock = stubFetch({ id: "inv-1", on_hand: 40 }, 201);
    const body = { counted_on_hand: 40, occurred_on: "2026-09-01" };
    await adjustStock("inv-1", body);
    const { init } = requested(fetchMock);
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("listMovements", () => {
  it("calls the item's movements endpoint with kind and paging", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listMovements("inv-1", { kind: "adjustment", page: 2 });
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/movements?page=2&size=25&kind=adjustment");
  });
});

describe("listWarehouses", () => {
  it("calls GET /operations/warehouses", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listWarehouses();
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/warehouses?page=1&size=25");
  });
});

describe("createWarehouse", () => {
  it("POSTs the WarehouseIn body verbatim", async () => {
    const fetchMock = stubFetch({ id: "wh-1" }, 201);
    const body = { code: "WH-NEW", name: "New DC", location: "Nagpur" };
    await createWarehouse(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/warehouses");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateWarehouse", () => {
  it("PATCHes name/location only — never code", async () => {
    const fetchMock = stubFetch({ id: "wh-1" });
    await updateWarehouse("wh-1", { name: "Renamed DC" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/warehouses/wh-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Renamed DC" });
  });
});

describe("deleteWarehouse", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteWarehouse("wh-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/warehouses/wh-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("listUoms", () => {
  it("calls GET /operations/inventory/uoms", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listUoms();
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/uoms?page=1&size=200");
  });
});

describe("listItemCategories", () => {
  it("calls GET /operations/inventory/categories", async () => {
    const fetchMock = stubFetch(PAGE_ENVELOPE);
    await listItemCategories();
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/categories?page=1&size=200");
  });
});

describe("createItemCategory", () => {
  it("POSTs the ItemCategoryIn body verbatim", async () => {
    const fetchMock = stubFetch({ id: "cat-1" }, 201);
    const body = { code: "FABRICS", name: "Fabrics" };
    await createItemCategory(body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/categories");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateItemCategory", () => {
  it("PATCHes only the changed fields", async () => {
    const fetchMock = stubFetch({ id: "cat-1" });
    await updateItemCategory("cat-1", { name: "Renamed" });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/categories/cat-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Renamed" });
  });
});

describe("deleteItemCategory", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteItemCategory("cat-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/categories/cat-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("listItemAttributes", () => {
  it("calls GET /operations/inventory/:id/attributes", async () => {
    const fetchMock = stubFetch([]);
    await listItemAttributes("inv-1");
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/attributes");
  });
});

describe("setItemAttribute", () => {
  it("PUTs exactly the one value field matching value_type", async () => {
    const fetchMock = stubFetch({ id: "attr-1", key: "gsm" });
    await setItemAttribute("inv-1", "gsm", { value_type: "number", value_number: 180 });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/attributes/gsm");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ value_type: "number", value_number: 180 });
  });
});

describe("deleteItemAttribute", () => {
  it("DELETEs by item id and key", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteItemAttribute("inv-1", "gsm");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/attributes/gsm");
    expect(init.method).toBe("DELETE");
  });
});

describe("listItemVariants", () => {
  it("calls GET /operations/inventory/:id/variants", async () => {
    const fetchMock = stubFetch([]);
    await listItemVariants("inv-1");
    const { url } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/variants");
  });
});

describe("createItemVariant", () => {
  it("POSTs the ItemVariantIn body verbatim", async () => {
    const fetchMock = stubFetch({ id: "var-1" }, 201);
    const body = { sku: "RM-ALU-7075-BLK", name: "Black anodised" };
    await createItemVariant("inv-1", body);
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/variants");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});

describe("updateItemVariant", () => {
  it("PATCHes only the changed fields", async () => {
    const fetchMock = stubFetch({ id: "var-1" });
    await updateItemVariant("inv-1", "var-1", { is_active: false });
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/variants/var-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ is_active: false });
  });
});

describe("deleteItemVariant", () => {
  it("DELETEs by item id and variant id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteItemVariant("inv-1", "var-1");
    const { url, init } = requested(fetchMock);
    expect(url).toBe("/api/v1/operations/inventory/inv-1/variants/var-1");
    expect(init.method).toBe("DELETE");
  });
});
