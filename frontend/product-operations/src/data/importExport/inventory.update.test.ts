/**
 * Updating existing inventory items from a sheet (ADR-0022 addendum). The engine's own pure rules
 * decide what a row changes; these tests hold the inventory spec to what the endpoint accepts: the
 * exact body sent (changed keys only, wire keys, no nulls), the columns that can never change, the
 * unit-of-measure trap, and a price read in the item's own currency.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import { inventoryItemOut, pageAt, stubGateway, uomOut } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import {
  containsNull,
  expectPlan,
  matchAmong,
  planFor,
  requestsOf,
  soleRequest,
  updateSpec,
} from "./updateTestKit";
import { makeUnitLookup } from "./units";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LOOKUPS: LookupSet = {
  warehouses: makeLookup([
    { label: "Main Warehouse", value: "Main Warehouse" },
    { label: "Cold Store", value: "Cold Store" },
  ]),
  units: makeUnitLookup([
    uomOut("pcs", "Piece", true),
    uomOut("kg", "Kilogram", true),
    uomOut("l", "Litre", true),
    uomOut("mt", "MT", false),
  ]),
  itemCategories: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
  ]),
};

const ctx: ImportContext = { lookups: LOOKUPS, signal: new AbortController().signal };

/** Stored with one flag on, so a cell that says "no" is a change and one that says "yes" is not. */
const ITEM = inventoryItemOut({ track_roll: true });

const PATH = "/api/v1/operations/inventory/inv-1";

/** Every importable cell equal to what `ITEM` stores. */
const SAME: ParsedRow = {
  sku: "RM-STL-4501",
  name: "Cold-rolled steel coil",
  category: "raw_materials",
  warehouse: "Main Warehouse",
  uom: "kg",
  reorder_point: 4000,
  unit_value: 6240,
  currency: "INR",
  item_category: "STEEL",
  hsn_code: "7209",
  track_lot: false,
  track_roll: true,
  track_expiry: false,
  track_quality: false,
  is_active: true,
};

const plan = (row: ParsedRow, cleared: readonly string[] = [], existing = ITEM) =>
  planFor(inventoryImport, existing, row, ctx, cleared);

const BLANK: ParsedRow = Object.fromEntries(Object.keys(SAME).map((key) => [key, null]));

describe("a row that changes nothing", () => {
  it("makes no request when every cell already matches the item", async () => {
    expect(plan(SAME)).toBeNull();
    expect(await requestsOf(plan(SAME))).toEqual([]);
  });

  it("recognises the same values written as another person would: capitals, spacing, a unit's other spelling", () => {
    expect(
      plan({ ...SAME, sku: "rm-stl-4501", warehouse: " main   warehouse ", uom: "KGS", item_category: "steel" }),
    ).toBeNull();
  });

  it("leaves every field alone when every cell is blank", () => {
    expect(plan(BLANK)).toBeNull();
  });
});

describe("a row that changes one field", () => {
  const CHANGES: [what: string, cells: ParsedRow, changed: string[], body: Record<string, unknown>][] = [
    ["the name", { name: "Cold-rolled steel coil, 1.5 mm" }, ["Item name"], { name: "Cold-rolled steel coil, 1.5 mm" }],
    ["the procurement category", { category: "components" }, ["Category"], { category: "components" }],
    ["the reorder point", { reorder_point: 5000 }, ["Reorder point"], { reorder_point: 5000 }],
    ["a reorder point of zero, which is a value and not a blank", { reorder_point: 0 }, ["Reorder point"], { reorder_point: 0 }],
    ["the unit value, under the API's own key", { unit_value: 7000 }, ["Unit value"], { unit_value_minor: 7000 }],
    ["a unit value of zero", { unit_value: 0 }, ["Unit value"], { unit_value_minor: 0 }],
    ["the warehouse, as the API stores its name", { warehouse: "cold   STORE" }, ["Warehouse"], { warehouse: "Cold Store" }],
    ["the item category, as its id", { item_category: "metals" }, ["Item category code"], { item_category_id: "cat-metals" }],
    ["the HSN code", { hsn_code: "720919" }, ["HSN code"], { hsn_code: "720919" }],
    ["a flag turned on", { track_lot: true }, ["Track lots"], { track_lot: true }],
    ["a flag turned off, which is a value and not a blank", { track_roll: false }, ["Track rolls"], { track_roll: false }],
    ["track expiry", { track_expiry: true }, ["Track expiry"], { track_expiry: true }],
    ["track quality", { track_quality: true }, ["Track quality"], { track_quality: true }],
    ["the item deactivated", { is_active: false }, ["Active"], { is_active: false }],
  ];

  it.each(CHANGES)("sends only %s, in one PATCH to the item", async (_what, cells, changed, body) => {
    const p = plan({ ...SAME, ...cells });
    expect(p?.changed).toEqual(changed);
    const request = await soleRequest(p);
    expect(request).toEqual({ method: "PATCH", path: PATH, body });
    expect(containsNull(request.body)).toBe(false);
  });

  it("sends only the fields that differ when a row changes several and repeats the rest", async () => {
    const p = plan({ ...SAME, name: "Steel coil 1.2 mm", reorder_point: 4500, is_active: false });
    expect(p?.changed).toEqual(["Item name", "Reorder point", "Active"]);
    expect((await soleRequest(p)).body).toEqual({ name: "Steel coil 1.2 mm", reorder_point: 4500, is_active: false });
  });

  it("changes only the columns the sheet has, leaving the item's other fields as they are", async () => {
    const p = plan({ ...BLANK, name: "Renamed" });
    expect((await soleRequest(p)).body).toEqual({ name: "Renamed" });
  });
});

describe("the unit of measure", () => {
  it.each([
    ["an item stored as Nos, for pcs", "Nos", "pcs"],
    ["an item stored as pcs, for Nos", "pcs", "Nos"],
    ["an item stored as KGS, for kg", "KGS", "kg"],
    ["an item stored as kg, for Kgs", "kg", "Kgs"],
    ["a custom unit stored as MT, for mt", "MT", "mt"],
  ])("is no change for %s: it is the same unit, so nothing is sent and the stored spelling stays", async (_what, stored, cell) => {
    const p = plan({ ...BLANK, uom: cell }, [], inventoryItemOut({ uom: stored }));
    expect(p).toBeNull();
    expect(await requestsOf(p)).toEqual([]);
  });

  it.each([
    ["from kg to a spelling of pcs", "kg", "Nos", "pcs"],
    ["from a spelling of kg to a spelling of l", "Kgs", "Ltr", "l"],
    ["from kg to a custom unit", "kg", "mt", "MT"],
    ["from a unit the organisation no longer lists", "furlong", "kg", "kg"],
  ])("sends the organisation's own spelling of the unit when it changes %s, never the spelling typed", async (_what, stored, cell, sent) => {
    const p = plan({ ...BLANK, uom: cell }, [], inventoryItemOut({ uom: stored }));
    expect(p?.changed).toEqual(["Unit of measure"]);
    expect((await soleRequest(p)).body).toEqual({ uom: sent });
  });

  it("refuses a unit the organisation does not have, instead of letting the server create one", () => {
    const attempt = () => plan({ ...BLANK, uom: "furlongs" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Unit of measure "furlongs" does not match exactly one entry on the Lists sheet.');
  });

  it("shows the server's refusal of a unit change on an item that has had stock as that row's failure", async () => {
    const sentence = "RM-STL-4501 already has stock history in 'kg'; its unit of measure cannot change once any stock has moved";
    stubGateway(() => ({
      status: 409,
      body: { type: "https://errors.opsgrids.dev/conflict", title: "Conflict", status: 409, detail: sentence },
    }));
    const p = expectPlan(plan({ ...BLANK, uom: "Nos" }));
    await expect(p.run()).rejects.toBeInstanceOf(ApiError);
    await expect(p.run()).rejects.toMatchObject({ status: 409, message: sentence });
  });
});

describe("the warehouse", () => {
  it("is sent only when the name differs, and as the warehouse's own spelling", async () => {
    expect(plan({ ...BLANK, warehouse: "MAIN WAREHOUSE" })).toBeNull();
    expect((await soleRequest(plan({ ...BLANK, warehouse: "COLD STORE" }))).body).toEqual({ warehouse: "Cold Store" });
  });

  it("refuses a warehouse that does not exist, naming it, and one that two warehouses share", () => {
    expect(() => plan({ ...BLANK, warehouse: "Nowhere" })).toThrow(
      'Warehouse "Nowhere" does not match exactly one entry on the Lists sheet.',
    );
    const twins: LookupSet = {
      ...LOOKUPS,
      warehouses: makeLookup([
        { label: "Pune Plant", value: "Pune Plant" },
        { label: "Pune plant", value: "Pune plant" },
      ]),
    };
    expect(() => planFor(inventoryImport, ITEM, { ...BLANK, warehouse: "pune plant" }, { ...ctx, lookups: twins })).toThrow(
      RowProblem,
    );
  });

  it("says in the notes that a new warehouse moves the item's stock", () => {
    expect((inventoryImport.notes ?? []).join(" ")).toMatch(/warehouse moves the item's stock there.*while any of that stock is reserved/i);
  });
});

describe("the item category and the HSN code", () => {
  it("refuses an item category code that does not exist yet", () => {
    expect(() => plan({ ...BLANK, item_category: "FASTENERS" })).toThrow(
      'Item category code "FASTENERS" does not match exactly one entry on the Lists sheet.',
    );
  });

  it("never sends a guess when a lookup was not loaded", () => {
    expect(() => planFor(inventoryImport, ITEM, { ...BLANK, warehouse: "Main Warehouse" }, { ...ctx, lookups: {} })).toThrow(
      RowProblem,
    );
  });
});

describe("[clear]", () => {
  it("empties the item category with null, the one place this endpoint takes one", async () => {
    const p = plan({ ...BLANK }, ["item_category"]);
    expect(p?.changed).toEqual(["Item category code"]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { item_category_id: null } });
  });

  it("empties the HSN code with an empty string, because null is refused", async () => {
    const p = plan({ ...BLANK }, ["hsn_code"]);
    expect(p?.changed).toEqual(["HSN code"]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { hsn_code: "" } });
  });

  it("does nothing to a field that is already empty", () => {
    const bare = inventoryItemOut({ item_category_id: null, hsn_code: "" });
    expect(plan({ ...BLANK }, ["item_category", "hsn_code"], bare)).toBeNull();
  });

  it.each([
    ["name", "Item name"],
    ["category", "Category"],
    ["warehouse", "Warehouse"],
    ["uom", "Unit of measure"],
    ["reorder_point", "Reorder point"],
    ["unit_value", "Unit value"],
    ["track_lot", "Track lots"],
    ["track_roll", "Track rolls"],
    ["track_expiry", "Track expiry"],
    ["track_quality", "Track quality"],
    ["is_active", "Active"],
  ])("refuses to clear %s, which the endpoint answers with a 422", (column, header) => {
    expect(() => plan({ ...BLANK }, [column])).toThrow(RowProblem);
    expect(() => plan({ ...BLANK }, [column])).toThrow(`${header} cannot be cleared`);
  });
});

describe("what can never change", () => {
  it("refuses a SKU that names another item, saying what is stored, and sends nothing", () => {
    const attempt = () => plan({ ...SAME, sku: "RM-STL-9999", name: "Renamed" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("SKU cannot be changed once the record exists (it is 'RM-STL-4501')");
  });

  it("accepts the item's own SKU in other capitals, which is how a row found it", () => {
    expect(plan({ ...BLANK, sku: "rm-stl-4501" })).toBeNull();
  });

  it("refuses a currency other than the item's, even when no price is in the row", () => {
    expect(() => plan({ ...BLANK, currency: "USD" })).toThrow(
      "Currency cannot be changed once the record exists (it is 'INR')",
    );
  });

  it("refuses the whole row when the price comes with another currency, not just the price", () => {
    expect(() => plan({ ...SAME, name: "Renamed", unit_value: 7000, currency: "USD" })).toThrow(RowProblem);
  });

  it.each([500, 0])("refuses an opening stock of %s: stock changes only through adjustments", (stock) => {
    const attempt = () => plan({ ...BLANK, opening_stock: stock });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("Opening stock cannot be changed once the record exists");
  });

  it("leaves a blank opening stock alone", () => {
    expect(plan({ ...BLANK, opening_stock: null })).toBeNull();
  });

  it.each([
    ["sku", "SKU"],
    ["currency", "Currency"],
    ["opening_stock", "Opening stock"],
  ])("refuses to clear %s", (column, header) => {
    expect(() => plan({ ...BLANK }, [column])).toThrow(`${header} cannot be cleared: it cannot be changed once the record exists`);
  });

  it("never puts sku, currency or stock in a body, whatever the row says", async () => {
    const p = plan({ ...SAME, sku: "rm-stl-4501", currency: "inr", name: "Renamed" });
    const body = (await soleRequest(p)).body;
    expect(Object.keys(body as object)).toEqual(["name"]);
  });
});

describe("a price in a currency other than rupees", () => {
  const yen = inventoryItemOut({ currency: "JPY", unit_value_minor: 500 });
  const dinar = inventoryItemOut({ currency: "KWD", unit_value_minor: 12500 });

  it("names the item's own currency for the engine to convert a cell with", () => {
    expect(updateSpec(inventoryImport).currencyOf?.(yen)).toBe("JPY");
    expect(updateSpec(inventoryImport).currencyOf?.(dinar)).toBe("KWD");
  });

  it("leaves the price alone for the same amount with a blank currency, instead of re-pricing it in rupees", () => {
    expect(planFor(inventoryImport, yen, { ...BLANK, unit_value: 500, currency: null }, ctx)).toBeNull();
  });

  it("sends the new amount alone for a blank currency, never a currency", async () => {
    const p = planFor(inventoryImport, yen, { ...BLANK, unit_value: 650, currency: null }, ctx);
    expect((await soleRequest(p)).body).toEqual({ unit_value_minor: 650 });
  });

  it("accepts the item's own currency written out in any case, and still sends only the amount", async () => {
    const p = planFor(inventoryImport, dinar, { ...BLANK, unit_value: 13000, currency: "kwd" }, ctx);
    expect(p?.changed).toEqual(["Unit value"]);
    expect((await soleRequest(p)).body).toEqual({ unit_value_minor: 13000 });
  });

  it("refuses rupees on a yen item rather than sending yen amounts as rupees", () => {
    const attempt = () => planFor(inventoryImport, yen, { ...BLANK, unit_value: 65000, currency: "INR" }, ctx);
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("Currency cannot be changed once the record exists (it is 'JPY')");
  });
});

describe("finding the item a row names", () => {
  const steel = inventoryItemOut();
  const carton = inventoryItemOut({ id: "inv-2", sku: "PKG-BOX-330" });

  it("matches by Record ID first", () => {
    expect(matchAmong(inventoryImport, [steel, carton], "inv-2", null)).toEqual({ kind: "found", item: carton, by: "id" });
  });

  it("matches by SKU, ignoring capitals and spacing", () => {
    expect(matchAmong(inventoryImport, [steel, carton], null, "  pkg-box-330 ")).toEqual({
      kind: "found",
      item: carton,
      by: "key",
    });
  });

  it("treats a SKU nobody has as a new item, and a Record ID nobody has as an error", () => {
    expect(matchAmong(inventoryImport, [steel, carton], null, "NEW-1")).toEqual({ kind: "new" });
    expect(matchAmong(inventoryImport, [steel, carton], "inv-404", "RM-STL-4501")).toEqual({ kind: "unknown-id" });
  });

  it("refuses a SKU that two live items share instead of picking one", () => {
    const twin = inventoryItemOut({ id: "inv-3", sku: "rm-stl-4501" });
    expect(matchAmong(inventoryImport, [steel, twin], null, "RM-STL-4501")).toEqual({ kind: "ambiguous" });
  });

  it("holds a row whose Record ID and SKU name two different items to the SKU of the one its Record ID found", () => {
    const found = matchAmong(inventoryImport, [steel, carton], "inv-2", "RM-STL-4501");
    expect(found).toMatchObject({ kind: "found", by: "id" });
    expect(() => planFor(inventoryImport, carton, { ...BLANK, sku: "RM-STL-4501" }, ctx)).toThrow(
      "SKU cannot be changed once the record exists (it is 'PKG-BOX-330')",
    );
  });
});

describe("inventory loadExisting", () => {
  const spec = updateSpec(inventoryImport);
  const [a, b, c] = [
    inventoryItemOut({ id: "inv-1", sku: "A-1" }),
    inventoryItemOut({ id: "inv-2", sku: "B-2" }),
    inventoryItemOut({ id: "inv-3", sku: "C-3" }),
  ];

  it("reads every page of the whole list, with no filter, and keeps each item once", async () => {
    // Ordered by a name that is not unique, B sits on both sides of the page boundary.
    const served = [[a, b], [b, c]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const items = await spec.loadExisting(ctx);

    expect(items.map((item) => item.id)).toEqual(["inv-1", "inv-2", "inv-3"]);
    expect(recorded.map((request) => request.url.pathname)).toEqual([
      "/api/v1/operations/inventory",
      "/api/v1/operations/inventory",
    ]);
    expect(recorded.map((request) => Object.fromEntries(request.url.searchParams))).toEqual([
      { page: "1", size: "200" },
      { page: "2", size: "200" },
    ]);
  });

  it("reads the list afresh on every call, so a warehouse renamed in between is seen by the next run", async () => {
    let warehouse = "Main Warehouse";
    stubGateway(() => ({ body: pageAt([[inventoryItemOut({ warehouse })]], 1) }));

    const before = await spec.loadExisting(ctx);
    warehouse = "Central Warehouse";
    const after = await spec.loadExisting(ctx);

    expect(before.map((item) => item.warehouse)).toEqual(["Main Warehouse"]);
    expect(after.map((item) => item.warehouse)).toEqual(["Central Warehouse"]);
  });

  it("passes the abort signal on, and refuses a list that stays short of the server's total", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(({ url }) => ({
      body: { items: [a], total: 2, page: Number(url.searchParams.get("page")), size: 200, pages: 1 },
    }));

    await expect(spec.loadExisting({ ...ctx, signal: controller.signal })).rejects.toThrow("changed while it was being read");
    for (const request of recorded) expect(request.init.signal).toBe(controller.signal);
  });
});

describe("inventory update notes", () => {
  it("says what cannot change and which columns [clear] can empty, without repeating the engine's general rules", () => {
    const notes = (inventoryImport.notes ?? []).filter((note) => note.startsWith("Updating:")).join(" ");
    expect(notes).toMatch(/SKU, Currency and Opening stock cannot be changed/);
    expect(notes).toMatch(/Item category code and HSN code can be emptied with \[clear\]/);
    expect(notes).toMatch(/naming another currency refuses the row/);
    expect(notes).toMatch(/refused once any stock has moved/);
  });

  it("no longer says an existing SKU is always skipped", () => {
    expect((inventoryImport.notes ?? []).join(" ")).toMatch(
      /already exists is skipped, not changed, unless you choose Add new and update existing/,
    );
  });
});
