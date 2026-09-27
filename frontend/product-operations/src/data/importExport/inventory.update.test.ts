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
  stepsAndRequests,
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
    expect(plan({ ...SAME, warehouse: " main   warehouse ", uom: "KGS", item_category: "steel", currency: "inr" })).toBeNull();
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

describe("a row that changes several fields", () => {
  it("is one step and one request, so a rate-limited request is retried on its own", async () => {
    const p = plan({ ...SAME, name: "Renamed", warehouse: "Cold Store", uom: "l", item_category: "metals", hsn_code: "999999" });
    expect(p?.changed).toHaveLength(5);
    expect(await stepsAndRequests(p)).toEqual({ steps: 1, requests: 1 });
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
    expect(attempt).toThrow(
      "'furlongs' is not one of the accepted values for Unit of measure (see the Lists sheet). " +
        "Use a unit from the Lists sheet, or a common spelling of one such as Nos, or leave the cell blank to keep the item's unit. " +
        "A misspelling is refused so that it cannot create a new unit.",
    );
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

  it("refuses a warehouse that does not exist, naming it, in the engine's words and with the update hint", () => {
    const attempt = () => plan({ ...BLANK, warehouse: "Nowhere" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(
      "'Nowhere' is not one of the accepted values for Warehouse (see the Lists sheet). " +
        "Use a warehouse from the Lists sheet, matched by name ignoring capitals, or leave the cell blank to keep the item where it is.",
    );
  });

  it("refuses a warehouse name that two warehouses share rather than picking one", () => {
    const twins: LookupSet = {
      ...LOOKUPS,
      warehouses: makeLookup([
        { label: "Pune Plant", value: "Pune Plant" },
        { label: "Pune plant", value: "Pune plant" },
      ]),
    };
    const attempt = () => planFor(inventoryImport, ITEM, { ...BLANK, warehouse: "pune plant" }, { ...ctx, lookups: twins });
    expect(attempt).toThrow("'pune plant' matches more than one of the accepted values for Warehouse, so it cannot be chosen.");
  });

  it("says in the notes that a new warehouse moves the item's stock", () => {
    expect((inventoryImport.notes ?? []).join(" ")).toMatch(/warehouse moves the item's stock there.*while any of that stock is reserved/i);
  });
});

/**
 * Two lookups entries can carry one label (warehouses "Pune Plant" and "Pune plant": the server
 * compares names exactly and allows both), which makes `resolve` refuse it. An export still writes
 * the record's own label, so a cell that repeats it is no change, however the twin is spelled; a cell
 * that would CHANGE the field to a label both carry cannot say which one it means.
 */
describe("a label that two entries carry", () => {
  const twinWarehouses = makeLookup([
    { label: "Pune Plant", value: "Pune Plant" },
    { label: "Pune plant", value: "Pune plant" },
    { label: "Main Warehouse", value: "Main Warehouse" },
  ]);
  const twinCategories = makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "Metals", value: "cat-metals-2" },
    { label: "STEEL", value: "cat-steel" },
  ]);
  const twinUnits = makeUnitLookup([
    uomOut("kg", "Kilogram", true),
    uomOut("roll bag", "Roll Bag", false),
    uomOut("roll  bag", "Roll  Bag", false),
  ]);
  const twins: ImportContext = {
    lookups: { warehouses: twinWarehouses, units: twinUnits, itemCategories: twinCategories },
    signal: new AbortController().signal,
  };
  const planIn = (existing: typeof ITEM, row: ParsedRow, cleared: readonly string[] = []) =>
    planFor(inventoryImport, existing, row, twins, cleared);

  describe("the warehouse", () => {
    const upper = inventoryItemOut({ warehouse: "Pune Plant" });
    const lower = inventoryItemOut({ warehouse: "Pune plant" });

    it("is no change when the cell repeats the item's own warehouse exactly, for either twin", () => {
      expect(planIn(upper, { ...BLANK, warehouse: "Pune Plant" })).toBeNull();
      expect(planIn(lower, { ...BLANK, warehouse: "Pune plant" })).toBeNull();
    });

    it("leaves the other cells of such a row to change what they change, and sends nothing for the warehouse", async () => {
      const p = planIn(upper, { ...BLANK, warehouse: "Pune Plant", name: "Renamed" });
      expect(p?.changed).toEqual(["Item name"]);
      expect((await soleRequest(p)).body).toEqual({ name: "Renamed" });
    });

    it("refuses the other twin, and a spelling that fits both, since the row cannot say which it means", () => {
      const sentence = (label: string) =>
        `'${label}' matches more than one of the accepted values for Warehouse, so it cannot be chosen.`;
      expect(() => planIn(upper, { ...BLANK, warehouse: "Pune plant" })).toThrow(sentence("Pune plant"));
      expect(() => planIn(upper, { ...BLANK, warehouse: "PUNE PLANT" })).toThrow(sentence("PUNE PLANT"));
      expect(() => planIn(lower, { ...BLANK, warehouse: "pune plant" })).toThrow(sentence("pune plant"));
    });

    it("refuses moving an item that is elsewhere to a label both carry, and still moves it to a warehouse only one carries", async () => {
      const elsewhere = inventoryItemOut({ warehouse: "Main Warehouse" });
      expect(() => planIn(elsewhere, { ...BLANK, warehouse: "Pune Plant" })).toThrow(RowProblem);
      expect((await soleRequest(planIn(upper, { ...BLANK, warehouse: "MAIN warehouse" }))).body).toEqual({
        warehouse: "Main Warehouse",
      });
    });
  });

  describe("the item category code", () => {
    const metals = inventoryItemOut({ item_category_id: "cat-metals" });
    const metalsAgain = inventoryItemOut({ item_category_id: "cat-metals-2" });

    it("is no change when the cell repeats the code of the item's own category, for either twin", () => {
      expect(planIn(metals, { ...BLANK, item_category: "METALS" })).toBeNull();
      expect(planIn(metalsAgain, { ...BLANK, item_category: "Metals" })).toBeNull();
    });

    it("refuses the other twin and a spelling that fits both, in the engine's words", () => {
      expect(() => planIn(metals, { ...BLANK, item_category: "Metals" })).toThrow(
        "'Metals' matches more than one of the accepted values for Item category code, so it cannot be chosen.",
      );
      expect(() => planIn(metals, { ...BLANK, item_category: "metals" })).toThrow(RowProblem);
    });

    it("still moves the item to a category that only one entry carries, and empties it with [clear]", async () => {
      expect((await soleRequest(planIn(metals, { ...BLANK, item_category: "steel" }))).body).toEqual({
        item_category_id: "cat-steel",
      });
      expect((await soleRequest(planIn(metals, { ...BLANK }, ["item_category"]))).body).toEqual({
        item_category_id: null,
      });
    });

    it("sends an item that has no category to the one a cell names, twin or not, only when the cell is unambiguous", async () => {
      const bare = inventoryItemOut({ item_category_id: null });
      expect((await soleRequest(planIn(bare, { ...BLANK, item_category: "STEEL" }))).body).toEqual({
        item_category_id: "cat-steel",
      });
      expect(() => planIn(bare, { ...BLANK, item_category: "METALS" })).toThrow(RowProblem);
    });
  });

  describe("the unit of measure", () => {
    const stored = inventoryItemOut({ uom: "Roll  Bag" });

    it("is no change when the cell repeats the item's own unit exactly", () => {
      expect(planIn(stored, { ...BLANK, uom: "Roll  Bag" })).toBeNull();
    });

    it("refuses a spelling of the unit that fits two, and a change to one of them", () => {
      expect(() => planIn(stored, { ...BLANK, uom: "Roll Bag" })).toThrow(
        "'Roll Bag' matches more than one of the accepted values for Unit of measure, so it cannot be chosen.",
      );
      expect(() => planIn(inventoryItemOut({ uom: "kg" }), { ...BLANK, uom: "Roll  Bag" })).toThrow(RowProblem);
    });
  });
});

describe("the item category and the HSN code", () => {
  it("refuses an item category code that does not exist yet", () => {
    expect(() => plan({ ...BLANK, item_category: "FASTENERS" })).toThrow(
      "'FASTENERS' is not one of the accepted values for Item category code (see the Lists sheet). " +
        "Use a code from the Lists sheet, matched ignoring capitals. A blank cell keeps the item's category, and [clear] removes it.",
    );
  });

  it("never sends a guess when a lookup was not loaded", () => {
    expect(() => planFor(inventoryImport, ITEM, { ...BLANK, warehouse: "Main Warehouse" }, { ...ctx, lookups: {} })).toThrow(
      "The accepted values for Warehouse could not be loaded, so it cannot be checked",
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

  it("holds a row found by its Record ID to the SKU's exact spelling: other capitals is an attempt to change it", () => {
    // A row found by typing its SKU is handed the stored spelling by the engine (see updateFlow.test.tsx).
    expect(() => plan({ ...BLANK, sku: "rm-stl-4501" })).toThrow(
      "SKU cannot be changed once the record exists (it is 'RM-STL-4501')",
    );
  });

  it("refuses a currency other than the item's, even when no price is in the row", () => {
    expect(() => plan({ ...BLANK, currency: "USD" })).toThrow(
      "Currency cannot be changed once the record exists (it is 'INR')",
    );
  });

  it("refuses the whole row when the price comes with another currency, not just the price", () => {
    expect(() => plan({ ...SAME, name: "Renamed", unit_value: 7000, currency: "USD" })).toThrow(RowProblem);
  });

  it.each([500, 0])(
    "refuses an opening stock of %s without claiming what is stored, since stock changes only through adjustments",
    (stock) => {
      const attempt = () => plan({ ...BLANK, opening_stock: stock });
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow("Opening stock cannot be changed once the record exists. Leave the cell blank.");
    },
  );

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
    const p = plan({ ...SAME, currency: "inr", name: "Renamed" });
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

  it("matches by SKU, surrounding spaces ignored", () => {
    expect(matchAmong(inventoryImport, [steel, carton], null, "  PKG-BOX-330 ")).toEqual({
      kind: "found",
      item: carton,
      by: "key",
    });
  });

  it("names the item a row updates by its SKU and name, which are not sealed", () => {
    expect(updateSpec(inventoryImport).recordLabel?.(steel)).toBe("RM-STL-4501 · Cold-rolled steel coil");
    expect(updateSpec(inventoryImport).recordLabel?.(carton)).toBe("PKG-BOX-330 · Cold-rolled steel coil");
  });

  it("treats a SKU nobody has as a new item, and a Record ID nobody has as an error", () => {
    expect(matchAmong(inventoryImport, [steel, carton], null, "NEW-1")).toEqual({ kind: "new" });
    expect(matchAmong(inventoryImport, [steel, carton], "inv-404", "RM-STL-4501")).toEqual({ kind: "unknown-id" });
  });

  it("refuses a SKU that two live items share instead of picking one", () => {
    const twin = inventoryItemOut({ id: "inv-3", sku: "RM-STL-4501" });
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

  it("says that a SKU must be spelled as stored, and that other capitals need that spelling or the Record ID", () => {
    const notes = (inventoryImport.notes ?? []).join(" ");
    expect(notes).toMatch(/by its SKU spelled exactly as it is stored/);
    expect(notes).toMatch(/differs only in capitals is refused, naming the stored spelling: use that spelling, or the Record ID/);
    expect(notes).not.toMatch(/still finds its item/);
  });

  it("no longer says an existing SKU is always skipped", () => {
    expect((inventoryImport.notes ?? []).join(" ")).toMatch(
      /already exists is skipped, not changed, unless you choose Add new and update existing/,
    );
  });
});
