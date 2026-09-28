import { afterEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import type { InventoryItemOut, UomOut } from "@/api/types";
import { itemCategoryOut, pageAt, pageOf, stubGateway, uomOut, warehouseOut } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import { makeUnitLookup } from "./units";

afterEach(() => {
  vi.unstubAllGlobals();
});

const UNITS: UomOut[] = [
  uomOut("pcs", "Piece", true),
  uomOut("kg", "Kilogram", true),
  uomOut("l", "Litre", true),
  uomOut("mt", "MT", false),
];

const LOOKUPS: LookupSet = {
  warehouses: makeLookup([
    { label: "Main Warehouse", value: "Main Warehouse" },
    { label: "Cold Store", value: "Cold Store" },
  ]),
  units: makeUnitLookup(UNITS),
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
      "Record ID",
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

  it("holds the reorder point to what the API's 32-bit column can take once the low-stock filter multiplies it by 1000", () => {
    const reorderPoint = inventoryImport.columns.find((c) => c.key === "reorder_point");
    expect(reorderPoint).toMatchObject({ kind: "integer", min: 0, max: 2_147_483 });
    // 2_147_483 * 1000 still fits an int4 (2_147_483_647); one more does not.
    expect(2_147_483 * 1000).toBeLessThanOrEqual(2_147_483_647);
    expect((2_147_483 + 1) * 1000).toBeGreaterThan(2_147_483_647);
    expect(reorderPoint?.help).toContain("2,147,483");
  });

  it("carries a lookupHint on every lookup column, saying how to fix a name that is not in the list", () => {
    const hints = Object.fromEntries(
      inventoryImport.columns.filter((c) => c.lookup !== undefined).map((c) => [c.lookup, c.lookupHint]),
    );
    expect(hints).toEqual({
      warehouses: "Warehouses must exist already and are matched by name, ignoring capitals.",
      units:
        "Only units that already exist can be used, so a misspelling cannot create a new one. See the Lists sheet.",
      itemCategories: "Item categories must exist already and are matched by code, ignoring capitals.",
    });
  });

  it("names its id column as the record id, so a row that came out of an export is not added again", () => {
    expect(inventoryImport.recordIdKey).toBe("id");
    const id = inventoryImport.columns.find((c) => c.key === inventoryImport.recordIdKey);
    expect(id?.readOnly).toBe(true);
  });

  it("marks opening stock import-only, because the list never returns it and an export would carry an empty column", () => {
    const importOnly = inventoryImport.columns.filter((c) => c.importOnly === true).map((c) => c.key);
    expect(importOnly).toEqual(["opening_stock"]);
    const openingStock = inventoryImport.columns.find((c) => c.key === "opening_stock");
    expect(openingStock?.readOnly).not.toBe(true);
    expect(openingStock?.help).not.toMatch(/export/i);
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
  // Whether they pass the column rules and turn into a payload is `checkEntities.test.ts`'s job.
  it("has two or three of them, all stocked in the warehouse a new organisation most likely has", () => {
    expect(inventoryImport.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(inventoryImport.sampleRows.length).toBeLessThanOrEqual(3);
    for (const row of inventoryImport.sampleRows) expect(row.warehouse).toBe("Main Warehouse");
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

  // The engine refuses these names, with the column's `lookupHint`, before `toPayload` runs. What
  // `toPayload` does with one anyway is fail the row instead of sending a guess.
  it("fails the row for a warehouse that does not exist, naming it", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, warehouse: "Nowhere" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Warehouse "Nowhere" does not match exactly one entry on the Lists sheet.');
  });

  it("fails the row for a warehouse name two warehouses share rather than picking one", () => {
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
  });

  it("fails the row for a unit of measure the organisation does not have instead of creating one", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, uom: "furlongs" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Unit of measure "furlongs" does not match exactly one entry');
  });

  it("fails the row for an item category code that does not exist yet", () => {
    const attempt = () =>
      inventoryImport.toPayload({ ...MINIMAL_ROW, item_category: "FASTENERS" }, context());
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Item category code "FASTENERS" does not match exactly one entry');
  });

  it("sends the unit a Tally spelling folds onto, as the registry code the server would store", () => {
    const send = (uom: string) => inventoryImport.toPayload({ ...MINIMAL_ROW, uom }, context()).uom;
    expect(send("Nos")).toBe("pcs");
    expect(send("KGS")).toBe("kg");
    expect(send("Ltr")).toBe("l");
    expect(send("mt")).toBe("MT");
  });

  it("refuses a missing required cell", () => {
    const attempt = () => inventoryImport.toPayload({ ...MINIMAL_ROW, sku: null }, context());
    expect(attempt).toThrow(/SKU is required/);
  });
});

describe("inventory loadLookups", () => {
  it("reads every page of every list, so a large organisation's names are not silently missing", async () => {
    const warehousePages = [[warehouseOut("Main Warehouse", "WH-MAIN")], [warehouseOut("Cold Store", "WH-COLD")]];
    const categoryPages = [[itemCategoryOut("METALS", "cat-metals")], [itemCategoryOut("STEEL", "cat-steel")]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      switch (url.pathname) {
        case "/api/v1/operations/warehouses":
          return { body: pageAt(warehousePages, page) };
        case "/api/v1/operations/inventory/uoms":
          return { body: pageAt([[uomOut("pcs", "Piece", true), uomOut("mt", "MT", false)]], page) };
        case "/api/v1/operations/inventory/categories":
          return { body: pageAt(categoryPages, page) };
        default:
          throw new Error(`unexpected request to ${url.pathname}`);
      }
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.warehouses?.labels).toEqual(["Main Warehouse", "Cold Store"]);
    expect(lookups.warehouses?.resolve("cold store")).toBe("Cold Store");
    expect(lookups.itemCategories?.labels).toEqual(["METALS", "STEEL"]);
    expect(lookups.itemCategories?.resolve("steel")).toBe("cat-steel");
    expect(recorded.map((request) => `${request.url.pathname}?page=${request.url.searchParams.get("page")}`).sort()).toEqual(
      [
        "/api/v1/operations/inventory/categories?page=1",
        "/api/v1/operations/inventory/categories?page=2",
        "/api/v1/operations/inventory/uoms?page=1",
        "/api/v1/operations/warehouses?page=1",
        "/api/v1/operations/warehouses?page=2",
      ],
    );
    for (const request of recorded) expect(request.url.searchParams.get("size")).toBe("200");
  });

  it("keeps a category the server served on two pages as one record, so its code is not ambiguous", async () => {
    const [metals, steel, aluminium] = [
      itemCategoryOut("METALS", "cat-metals"),
      itemCategoryOut("STEEL", "cat-steel"),
      itemCategoryOut("ALUMINIUM", "cat-alu"),
    ];
    // Ordered by a name that is not unique, STEEL sits on both sides of the page boundary.
    const served = [[metals, steel], [steel, aluminium]];
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      if (url.pathname === "/api/v1/operations/inventory/categories") {
        return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
      }
      return { body: pageOf([]) };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.itemCategories?.labels).toEqual(["METALS", "STEEL", "ALUMINIUM"]);
    expect(lookups.itemCategories?.isAmbiguous("STEEL")).toBe(false);
  });

  it("lists a registry unit by its code and a custom unit by the spelling it was created with", async () => {
    stubGateway(({ url }) => {
      if (url.pathname === "/api/v1/operations/inventory/uoms") {
        return {
          body: pageOf([
            uomOut("pcs", "Piece", true),
            uomOut("kg", "Kilogram", true),
            uomOut("mt", "MT", false),
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

  it("takes the spellings the server folds onto a unit, without listing them", async () => {
    stubGateway(({ url }) => {
      if (url.pathname === "/api/v1/operations/inventory/uoms") {
        return { body: pageOf([uomOut("pcs", "Piece", true), uomOut("kg", "Kilogram", true), uomOut("l", "Litre", true)]) };
      }
      return { body: pageOf([]) };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.units?.labels).toEqual(["pcs", "kg", "l"]);
    expect(["Nos", "Kgs", "Ltr", "Pcs"].map((spelling) => lookups.units?.resolve(spelling))).toEqual([
      "pcs",
      "kg",
      "l",
      "pcs",
    ]);
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

  it("refuses a list that stays short of the server's total, rather than lookups missing a real warehouse", async () => {
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      if (url.pathname === "/api/v1/operations/warehouses") {
        return { body: { items: [warehouseOut("Main Warehouse", "WH-MAIN")], total: 2, page, size: 200, pages: 1 } };
      }
      return { body: pageOf([]) };
    });
    await expect(loadLookups(new AbortController().signal)).rejects.toThrow("changed while it was being read");
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
    selling_price_minor: null,
    selling_price_currency: null,
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

  it("writes the unit's canonical label, whatever spelling the item was stored with, so the export imports again", () => {
    const written = (uom: string) => exportSource().toRow({ ...ITEM, uom }, LOOKUPS).uom;
    expect(written("Nos")).toBe("pcs");
    expect(written("KGS")).toBe("kg");
    expect(written("kg")).toBe("kg");
    expect(written("mt")).toBe("MT");
    // A unit no label reaches is written as stored, not blanked.
    expect(written("furlongs")).toBe("furlongs");
  });

  it("leaves the category blank rather than writing an id nobody can read", () => {
    const row = exportSource().toRow({ ...ITEM, item_category_id: "cat-deleted" }, LOOKUPS);
    expect(row.item_category).toBeNull();
  });

  it("writes a cell for every column an export carries, and none for the import-only ones", () => {
    const row = exportSource().toRow(ITEM, LOOKUPS);
    const carried = inventoryImport.columns.filter((c) => c.importOnly !== true).map((c) => c.key);
    expect(carried.filter((key) => !(key in row))).toEqual([]);
    expect(Object.keys(row).sort()).toEqual([...carried].sort());
  });
});
