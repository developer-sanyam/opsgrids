import { afterEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import type { InventoryItemOut, UomOut, WarehouseOut } from "@/api/types";
import { parseRow, pageOf, rowProblems, stubGateway } from "./entityTestKit";
import { inventoryImport } from "./inventory";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LOOKUPS: LookupSet = {
  warehouses: makeLookup([
    { label: "Main Warehouse", value: "Main Warehouse" },
    { label: "Cold Store", value: "Cold Store" },
  ]),
  units: makeLookup(["pcs", "kg", "l", "MT"].map((label) => ({ label, value: label }))),
  itemCategories: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
  ]),
};

function context(lookups: LookupSet = LOOKUPS): ImportContext {
  return { lookups, signal: new AbortController().signal };
}

function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  if (inventoryImport.loadLookups === undefined) throw new Error("inventory has no lookups");
  return inventoryImport.loadLookups(signal);
}

function exportSource() {
  if (inventoryImport.export === undefined) throw new Error("inventory has no export");
  return inventoryImport.export;
}

const MINIMAL_ROW: ParsedRow = {
  sku: "RM-STL-4501",
  name: "Cold-rolled steel coil",
  category: "raw_materials",
  warehouse: "Main Warehouse",
  uom: "kg",
};

const MINIMAL_PAYLOAD = inventoryImport.toPayload(MINIMAL_ROW, context());

describe("inventory columns", () => {
  it("names them in plain business English, importable ones first and read-only ones last", () => {
    expect(inventoryImport.columns.map((c) => c.header)).toEqual([
      "SKU",
      "Item name",
      "Category",
      "Warehouse",
      "Unit of measure",
      "Reorder point",
      "Unit value",
      "Currency",
      "Opening stock",
      "Item category code",
      "HSN code",
      "Track lots",
      "Track rolls",
      "Track expiry",
      "Track quality",
      "Active",
      "On hand",
      "Reserved",
      "Available",
      "Last updated",
      "ID",
    ]);
  });

  it("requires exactly what the API requires", () => {
    const required = inventoryImport.columns.filter((c) => c.required === true).map((c) => c.key);
    expect(required).toEqual(["sku", "name", "category", "warehouse", "uom"]);
  });

  it("marks stock quantities, the timestamp and the id read-only", () => {
    const readOnly = inventoryImport.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(readOnly).toEqual(["on_hand", "reserved", "available", "updated_at", "id"]);
  });

  it("offers the six procurement categories under the labels the screens use", () => {
    const category = inventoryImport.columns.find((c) => c.key === "category");
    expect(category?.options?.map((o) => o.label)).toEqual([
      "Raw materials",
      "Packaging",
      "Components",
      "MRO",
      "Logistics",
      "Services",
    ]);
    expect(category?.options?.map((o) => o.value)).toEqual([
      "raw_materials",
      "packaging",
      "components",
      "mro",
      "logistics",
      "services",
    ]);
  });

  it("keeps the unit value in major units beside its currency, and codes as text", () => {
    const byKey = new Map(inventoryImport.columns.map((c) => [c.key, c]));
    expect(byKey.get("unit_value")).toMatchObject({ kind: "money", currencyKey: "currency" });
    expect(byKey.get("sku")?.kind).toBe("text");
    expect(byKey.get("hsn_code")?.kind).toBe("text");
    expect(byKey.get("hsn_code")?.pattern?.test("0721")).toBe(true);
    expect(byKey.get("hsn_code")?.pattern?.test("072")).toBe(false);
  });

  it("explains the rules a first-time importer trips over", () => {
    const notes = (inventoryImport.notes ?? []).join(" ");
    expect(notes).toMatch(/warehouse must already exist/i);
    expect(notes).toMatch(/matched by name/i);
    expect(notes).toMatch(/unit of measure must already exist/i);
    expect(notes).toMatch(/opening stock is set only when the item is created/i);
    expect(notes).toMatch(/item category must exist/i);
  });
});

describe("inventory sample rows", () => {
  it("has two or three rows that obey the entity's own rules and use real lookup values", () => {
    expect(inventoryImport.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(inventoryImport.sampleRows.length).toBeLessThanOrEqual(3);
    for (const row of inventoryImport.sampleRows) {
      expect(rowProblems(inventoryImport, row, LOOKUPS)).toEqual([]);
    }
  });

  it("turns into a payload the API accepts", () => {
    for (const row of inventoryImport.sampleRows) {
      const payload = inventoryImport.toPayload(parseRow(inventoryImport, row), context());
      expect(payload.warehouse).toBe("Main Warehouse");
      expect(payload.unit_value_minor).toBeGreaterThan(0);
    }
  });
});

describe("inventory toPayload", () => {
  it("sends only what the row says, so the API's own defaults still apply", () => {
    expect(inventoryImport.toPayload(MINIMAL_ROW, context())).toStrictEqual({
      sku: "RM-STL-4501",
      name: "Cold-rolled steel coil",
      category: "raw_materials",
      warehouse: "Main Warehouse",
      uom: "kg",
    });
  });

  it("maps every column to the field the API takes, resolving names and keeping HSN as text", () => {
    const row: ParsedRow = {
      ...MINIMAL_ROW,
      warehouse: "main warehouse",
      uom: "KG",
      reorder_point: 4000,
      unit_value: 6240,
      currency: "inr",
      opening_stock: 8200,
      item_category: "steel",
      hsn_code: "0721",
      track_lot: true,
      track_roll: false,
      track_expiry: null,
      track_quality: null,
      is_active: false,
    };
    expect(inventoryImport.toPayload(row, context())).toStrictEqual({
      sku: "RM-STL-4501",
      name: "Cold-rolled steel coil",
      category: "raw_materials",
      warehouse: "Main Warehouse",
      uom: "kg",
      reorder_point: 4000,
      unit_value_minor: 6240,
      currency: "INR",
      opening_on_hand: 8200,
      item_category_id: "cat-steel",
      hsn_code: "0721",
      track_lot: true,
      track_roll: false,
      is_active: false,
    });
  });

  it("sends the warehouse as the API stores its name, not as typed", () => {
    const payload = inventoryImport.toPayload({ ...MINIMAL_ROW, warehouse: "  cold   STORE " }, context());
    expect(payload.warehouse).toBe("Cold Store");
  });

  it("refuses a warehouse that does not exist, naming it", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, warehouse: "Nowhere" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/no warehouse named "Nowhere"/);
  });

  it("refuses a warehouse name two warehouses share rather than picking one", () => {
    const lookups: LookupSet = {
      ...LOOKUPS,
      warehouses: makeLookup([
        { label: "Pune Plant", value: "Pune Plant" },
        { label: "Pune plant", value: "Pune plant" },
      ]),
    };
    const attempt = () =>
      inventoryImport.toPayload({ ...MINIMAL_ROW, warehouse: "pune plant" }, context(lookups));
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/More than one warehouse matches "pune plant"/);
  });

  it("refuses a unit of measure the organisation does not have instead of creating one", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, uom: "kgs" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/"kgs" is not one of your units of measure/);
  });

  it("refuses an item category code that does not exist yet", () => {
    const attempt = () =>
      inventoryImport.toPayload({ ...MINIMAL_ROW, item_category: "FASTENERS" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/no item category with the code "FASTENERS"/);
  });

  it("refuses a missing required cell", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, sku: null }, context());
    expect(attempt).toThrow(/SKU is required/);
  });
});

function warehouse(name: string): WarehouseOut {
  return { id: `wh-${name}`, code: name.toUpperCase().replace(/\W/g, ""), name, location: "" };
}

function unit(code: string, name: string, isSystem: boolean): UomOut {
  return {
    id: `uom-${code}`,
    code,
    name,
    dimension: "count",
    is_discrete: false,
    is_system: isSystem,
  };
}

describe("inventory loadLookups", () => {
  it("reads every page of every list, so a large organisation's names are not silently missing", async () => {
    const category = (code: string, id: string) => ({ id, code, name: code, parent_id: null });
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      switch (url.pathname) {
        case "/api/v1/operations/warehouses":
          return { body: pageOf([warehouse(page === 1 ? "Main Warehouse" : "Cold Store")], page, 2) };
        case "/api/v1/operations/inventory/uoms":
          return {
            body: pageOf([unit("pcs", "Piece", true), unit("mt", "MT", false)], 1, 1),
          };
        case "/api/v1/operations/inventory/categories":
          // Ordering by a non-unique name can serve one record on two pages; it must stay one record.
          return {
            body: pageOf(
              page === 1
                ? [category("METALS", "cat-metals"), category("STEEL", "cat-steel")]
                : [category("STEEL", "cat-steel")],
              page,
              2,
            ),
          };
        default:
          throw new Error(`unexpected request to ${url.pathname}`);
      }
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.warehouses?.labels).toEqual(["Main Warehouse", "Cold Store"]);
    expect(lookups.warehouses?.resolve("cold store")).toBe("Cold Store");
    expect(lookups.itemCategories?.labels).toEqual(["METALS", "STEEL"]);
    expect(lookups.itemCategories?.resolve("steel")).toBe("cat-steel");
    expect(lookups.itemCategories?.isAmbiguous("STEEL")).toBe(false);
    for (const request of recorded) expect(request.url.searchParams.get("size")).toBe("200");
  });

  it("lists a registry unit by its code and a custom unit by the spelling it was created with", async () => {
    stubGateway(({ url }) => {
      if (url.pathname === "/api/v1/operations/inventory/uoms") {
        return {
          body: pageOf([
            unit("pcs", "Piece", true),
            unit("kg", "Kilogram", true),
            unit("mt", "MT", false),
          ]),
        };
      }
      return { body: pageOf([]) };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.units?.labels).toEqual(["pcs", "kg", "MT"]);
    expect(lookups.units?.resolve("Kilogram")).toBeUndefined();
    expect(lookups.units?.resolve("mt")).toBe("MT");
  });

  it("provides every lookup the columns name", async () => {
    stubGateway(() => ({ body: pageOf([]) }));
    const lookups = await loadLookups(new AbortController().signal);
    for (const column of inventoryImport.columns) {
      if (column.lookup !== undefined) expect(lookups[column.lookup]).toBeDefined();
    }
  });

  it("passes the abort signal on to every request", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageOf([]) }));
    await loadLookups(controller.signal);
    expect(recorded).toHaveLength(3);
    for (const request of recorded) expect(request.init.signal).toBe(controller.signal);
  });

  it("stops without a request when the dialog was already closed", async () => {
    const controller = new AbortController();
    controller.abort();
    const recorded = stubGateway(() => ({ body: pageOf([]) }));
    await expect(loadLookups(controller.signal)).rejects.toThrow();
    expect(recorded).toHaveLength(0);
  });
});

describe("inventory create", () => {
  it("posts the payload to the inventory endpoint and reports the new id", async () => {
    const recorded = stubGateway(() => ({ body: { id: "item-9" }, status: 201 }));
    const payload = inventoryImport.toPayload(
      { ...MINIMAL_ROW, unit_value: 6240, currency: "INR", opening_stock: 12 },
      context(),
    );

    const result = await inventoryImport.create(payload, context());

    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/inventory");
    expect(recorded[0]?.init.method).toBe("POST");
    expect(JSON.parse(String(recorded[0]?.init.body))).toEqual({
      sku: "RM-STL-4501",
      name: "Cold-rolled steel coil",
      category: "raw_materials",
      warehouse: "Main Warehouse",
      uom: "kg",
      unit_value_minor: 6240,
      currency: "INR",
      opening_on_hand: 12,
    });
    expect(result).toEqual({ id: "item-9" });
  });

  it("lets a duplicate SKU's refusal reach the engine so the row is reported as skipped", async () => {
    stubGateway(() => ({
      body: { title: "Conflict", status: 409, detail: "An inventory item with SKU 'RM-STL-4501' already exists" },
      status: 409,
    }));
    await expect(inventoryImport.create(MINIMAL_PAYLOAD, context())).rejects.toMatchObject({ status: 409 });
  });
});

describe("inventory export", () => {
  const ITEM: InventoryItemOut = {
    id: "item-1",
    sku: "RM-STL-4501",
    name: "Cold-rolled steel coil",
    category: "raw_materials",
    warehouse: "Main Warehouse",
    on_hand: 82,
    on_hand_qty: "82.5",
    reserved_qty: "2.25",
    available_qty: "80.25",
    reorder_point: 40,
    uom: "kg",
    unit_value_minor: 6240,
    currency: "INR",
    updated_at: "2026-09-25T09:30:00Z",
    item_category_id: "cat-steel",
    secondary_uom_id: null,
    hsn_code: "0721",
    track_lot: true,
    track_roll: false,
    track_expiry: false,
    track_quality: false,
    is_active: true,
  };

  it("asks for the page under the screen's current filters, not just the visible page", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageOf([], 2, 3) }));

    await exportSource().fetchPage({
      page: 2,
      size: 200,
      signal: controller.signal,
      filters: {
        q: "steel",
        warehouse: "Main Warehouse",
        item_category_id: "all",
        sort: "value",
        low_stock: true,
      },
    });

    const params = recorded[0]?.url.searchParams;
    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/inventory");
    expect(params?.get("page")).toBe("2");
    expect(params?.get("size")).toBe("200");
    expect(params?.get("q")).toBe("steel");
    expect(params?.get("warehouse")).toBe("Main Warehouse");
    expect(params?.get("low_stock")).toBe("true");
    expect(params?.get("sort")).toBe("value");
    expect(params?.has("item_category_id")).toBe(false);
    expect(recorded[0]?.init.signal).toBe(controller.signal);
  });

  it("sends no filters when the screen has none", async () => {
    const recorded = stubGateway(() => ({ body: pageOf([]) }));
    await exportSource().fetchPage({
      page: 1,
      size: 200,
      signal: new AbortController().signal,
      filters: {},
    });
    expect([...(recorded[0]?.url.searchParams.keys() ?? [])].sort()).toEqual(["page", "size"]);
  });


  it("writes one sheet row per item, with the category as its code and money in major units", () => {
    expect(exportSource().toRow(ITEM, LOOKUPS)).toStrictEqual({
      sku: "RM-STL-4501",
      name: "Cold-rolled steel coil",
      category: "Raw materials",
      warehouse: "Main Warehouse",
      uom: "kg",
      reorder_point: 40,
      unit_value: "62.40",
      currency: "INR",
      item_category: "STEEL",
      hsn_code: "0721",
      track_lot: true,
      track_roll: false,
      track_expiry: false,
      track_quality: false,
      is_active: true,
      on_hand: "82.5",
      reserved: "2.25",
      available: "80.25",
      updated_at: "2026-09-25T09:30:00Z",
      id: "item-1",
    });
  });

  it("uses the currency's own decimals and leaves unset optional cells blank", () => {
    const row = exportSource().toRow(
      { ...ITEM, currency: "KWD", unit_value_minor: 1250, item_category_id: null, hsn_code: "" },
      LOOKUPS,
    );
    expect(row.unit_value).toBe("1.250");
    expect(row.currency).toBe("KWD");
    expect(row.item_category).toBeNull();
    expect(row.hsn_code).toBeNull();
  });

  it("leaves the category blank rather than writing an id nobody can read", () => {
    const row = exportSource().toRow({ ...ITEM, item_category_id: "cat-deleted" }, LOOKUPS);
    expect(row.item_category).toBeNull();
  });

  it("writes a cell for every column except opening stock, which is only ever an input", () => {
    const row = exportSource().toRow(ITEM, LOOKUPS);
    const missing = inventoryImport.columns.map((c) => c.key).filter((key) => !(key in row));
    expect(missing).toEqual(["opening_stock"]);
  });
});
