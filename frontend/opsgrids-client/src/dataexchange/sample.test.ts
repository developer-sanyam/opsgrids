import { describe, expect, it } from "vitest";

import { InvalidCell } from "./cells";
import type { CellInput } from "./cells";
import { makeLookup } from "./lookups";
import { sampleMatcher, substituteSampleRows } from "./sample";
import { makeProductEntity, productLookups, productSamples } from "./testkit";
import type { ImportColumn, Lookup, LookupSet } from "./types";

const entity = makeProductEntity();

/** The first sample row as a sheet would hand it back. */
function sheetRow(overrides: Record<string, CellInput> = {}): Record<string, CellInput> {
  return {
    sku: "0012-BLK",
    name: "Steel bracket",
    status: "In stock",
    category: "Hardware",
    price: 1499.5,
    currency: "INR",
    quantity: 120,
    weight: 0.75,
    launched: new Date(Date.UTC(2026, 2, 4)),
    active: "Yes",
    email: "orders@example.com",
    phone: "+919876543210",
    ...overrides,
  };
}

const elsewhere: LookupSet = {
  categories: makeLookup([
    { label: "Tools", value: "t" },
    { label: "Paint", value: "p" },
  ]),
};

describe("substituteSampleRows", () => {
  it("leaves the examples alone when the organisation has what they name", () => {
    expect(substituteSampleRows(entity, productLookups)).toEqual(productSamples);
  });

  it("swaps a lookup example the organisation lacks for the lookup's first label", () => {
    const rows = substituteSampleRows(entity, elsewhere);
    expect(rows.map((row) => row.category)).toEqual(["Tools", "Tools"]);
    // Nothing else in the row moves.
    expect(rows[0]).toEqual({ ...productSamples[0], category: "Tools" });
  });

  it("does not touch the entity's own sample rows", () => {
    substituteSampleRows(entity, elsewhere);
    expect(entity.sampleRows[0]?.category).toBe("Hardware");
  });

  it("matches the organisation's labels ignoring case and spacing", () => {
    const lookups = { categories: makeLookup([{ label: "  hardware ", value: "h" }, { label: "Tools", value: "t" }]) };
    expect(substituteSampleRows(entity, lookups).map((row) => row.category)).toEqual(["Hardware", "Hardware"]);
  });

  it("leaves the example when the lookup is empty or was not loaded", () => {
    expect(substituteSampleRows(entity, { categories: makeLookup([]) }).map((row) => row.category)).toEqual([
      "Hardware",
      "Hardware",
    ]);
    expect(substituteSampleRows(entity, {}).map((row) => row.category)).toEqual(["Hardware", "Hardware"]);
  });

  describe("choosing the label that stands in", () => {
    it("skips a first entry that is ambiguous and takes the first that resolves", () => {
      // `makeLookup` no longer lists an ambiguous label; a lookup that still does must not be picked from.
      const lookup: Lookup = {
        labels: ["Twin", "Real one", "Another"],
        resolve: (label) => (["Real one", "Another"].includes(label) ? label : undefined),
        isAmbiguous: (label) => label === "Twin",
        labelOf: () => undefined,
      };
      expect(substituteSampleRows(entity, { categories: lookup }).map((row) => row.category)).toEqual([
        "Real one",
        "Real one",
      ]);
    });

    it("takes the first label of a lookup made by makeLookup even when an ambiguous pair came first", () => {
      const lookup = makeLookup([
        { label: "Twin", value: "t1" },
        { label: "twin", value: "t2" },
        { label: "Real", value: "r" },
      ]);
      expect(lookup.labels).toEqual(["Real"]);
      expect(substituteSampleRows(entity, { categories: lookup }).map((row) => row.category)).toEqual(["Real", "Real"]);
    });

    it("leaves the example when nothing in the lookup resolves", () => {
      const lookup: Lookup = {
        labels: ["Twin", "Twin too"],
        resolve: () => undefined,
        isAmbiguous: () => true,
        labelOf: () => undefined,
      };
      expect(substituteSampleRows(entity, { categories: lookup }).map((row) => row.category)).toEqual([
        "Hardware",
        "Hardware",
      ]);
      expect(substituteSampleRows(entity, { categories: makeLookup([]) }).map((row) => row.category)).toEqual([
        "Hardware",
        "Hardware",
      ]);
    });

    it("swaps an example that is ambiguous in this organisation, since it would be refused", () => {
      const lookup = makeLookup([
        { label: "Hardware", value: "h1" },
        { label: "hardware", value: "h2" },
        { label: "Tools", value: "t" },
      ]);
      expect(substituteSampleRows(entity, { categories: lookup }).map((row) => row.category)).toEqual(["Tools", "Tools"]);
    });

    it("keeps an example that resolves, in any case", () => {
      const lookup = makeLookup([{ label: "Tools", value: "t" }, { label: "HARDWARE", value: "h" }]);
      expect(substituteSampleRows(entity, { categories: lookup }).map((row) => row.category)).toEqual([
        "Hardware",
        "Hardware",
      ]);
    });
  });

  it("leaves a blank example blank", () => {
    const blank = makeProductEntity({ sampleRows: [{ sku: "A", name: "B" }, { sku: "C", name: "D", category: "  " }] });
    const rows = substituteSampleRows(blank, elsewhere);
    expect(rows[0]).toEqual({ sku: "A", name: "B" });
    expect(rows[1]?.category).toBe("  ");
  });

  it("handles every lookup column of the entity", () => {
    const columns: ImportColumn[] = [
      { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." },
      { key: "warehouse", header: "Warehouse", kind: "text", help: "Where.", lookup: "warehouses" },
      { key: "owner", header: "Owner", kind: "text", help: "Who.", lookup: "owners" },
    ];
    const both = makeProductEntity({
      columns,
      sampleRows: [{ sku: "A", warehouse: "Main", owner: "ada@example.com" }],
    });
    const lookups = {
      warehouses: makeLookup([{ label: "Depot", value: "d" }]),
      owners: makeLookup([{ label: "ada@example.com", value: "u1" }, { label: "bo@example.com", value: "u2" }]),
    };
    expect(substituteSampleRows(both, lookups)).toEqual([{ sku: "A", warehouse: "Depot", owner: "ada@example.com" }]);
  });
});

describe("sampleMatcher", () => {
  it("recognises the entity's own example rows once they come back from a sheet", () => {
    const isSample = sampleMatcher(entity, productLookups);
    expect(isSample(sheetRow())).toBe(true);
    expect(
      isSample({
        sku: "0013-WHT",
        name: "Steel bracket, white",
        status: "Discontinued",
        category: "Hardware",
        price: 18,
        currency: "JPY",
        quantity: 0,
        weight: null,
        launched: null,
        active: "No",
        email: null,
        phone: null,
      }),
    ).toBe(true);
  });

  it("is not thrown by how Excel hands a value back", () => {
    const isSample = sampleMatcher(entity, productLookups);
    expect(isSample(sheetRow({ price: "1499.50" }))).toBe(true);
    expect(isSample(sheetRow({ price: "1499.5" }))).toBe(true);
    expect(isSample(sheetRow({ status: "in_stock" }))).toBe(true);
    expect(isSample(sheetRow({ status: "IN STOCK" }))).toBe(true);
    expect(isSample(sheetRow({ active: true }))).toBe(true);
    expect(isSample(sheetRow({ sku: "  0012-blk " }))).toBe(true);
    expect(isSample(sheetRow({ launched: "2026-03-04" }))).toBe(true);
    expect(isSample(sheetRow({ quantity: "120" }))).toBe(true);
  });

  it("stops recognising a row the moment any cell is changed", () => {
    const isSample = sampleMatcher(entity, productLookups);
    for (const change of [
      { name: "Steel bracket, extended" },
      { price: 1500 },
      { quantity: 121 },
      { active: "No" },
      { launched: new Date(Date.UTC(2026, 2, 5)) },
      { phone: "+919876543211" },
      { category: "Fasteners" },
      { status: "Discontinued" },
    ]) {
      expect(isSample(sheetRow(change)), JSON.stringify(change)).toBe(false);
    }
  });

  it("does not recognise a row with an error cell", () => {
    const isSample = sampleMatcher(entity, productLookups);
    expect(isSample(sheetRow({ quantity: new InvalidCell("#N/A", "contains the error #N/A") }))).toBe(false);
  });

  it("does not recognise anything in a file that has none of the entity's columns", () => {
    expect(sampleMatcher(entity, productLookups)({})).toBe(false);
  });

  it("recognises a row that matches the example as shipped even when the organisation's sheet swapped it", () => {
    // The file was built for an organisation that has Hardware and opened in one that does not.
    const isSample = sampleMatcher(entity, elsewhere);
    expect(isSample(sheetRow({ category: "Hardware" }))).toBe(true);
  });

  it("recognises a row that matches the example as this organisation's sheet wrote it", () => {
    const isSample = sampleMatcher(entity, elsewhere);
    expect(isSample(sheetRow({ category: "Tools" }))).toBe(true);
    expect(isSample(sheetRow({ category: "Paint" }))).toBe(false);
  });
});

describe("sampleMatcher: only the columns the file has are compared", () => {
  const isSample = sampleMatcher(entity, productLookups);

  /** The example row as a sheet without those columns would hand it back. */
  function without(...keys: string[]): Record<string, CellInput> {
    const row = sheetRow();
    for (const key of keys) delete row[key];
    return row;
  }

  it("still recognises the examples after an optional column was deleted", () => {
    expect(isSample(without("weight"))).toBe(true);
    expect(isSample(without("weight", "phone", "email", "launched"))).toBe(true);
    // The first example has a value for `weight`; the second has none. Both are still examples.
    expect(
      isSample({ sku: "0013-WHT", name: "Steel bracket, white", status: "Discontinued", category: "Hardware", price: 18, currency: "JPY", quantity: 0, active: "No" }),
    ).toBe(true);
  });

  it("still recognises the examples when only the required columns are left", () => {
    expect(isSample({ sku: "0012-BLK", name: "Steel bracket" })).toBe(true);
    expect(isSample({ sku: "0013-WHT", name: "Steel bracket, white" })).toBe(true);
  });

  it("still recognises the examples after the columns were reordered", () => {
    const reversed = Object.fromEntries(Object.entries(sheetRow()).reverse());
    expect(isSample(reversed)).toBe(true);
  });

  it("ignores a column the entity does not know", () => {
    expect(isSample({ ...sheetRow(), "Favourite colour": "blue", extra: 1 })).toBe(true);
  });

  it("does not recognise a row once a cell of a column that is there is edited", () => {
    expect(isSample({ ...without("weight"), name: "Steel bracket, extended" })).toBe(false);
    expect(isSample({ sku: "0012-BLK", name: "Something else" })).toBe(false);
    expect(isSample({ sku: "NEW-1", name: "Steel bracket" })).toBe(false);
  });

  it("does not recognise a row once an example cell is cleared: the column is there and now differs", () => {
    expect(isSample(sheetRow({ name: null }))).toBe(false);
    expect(isSample(sheetRow({ weight: null }))).toBe(false);
    expect(isSample(sheetRow({ phone: "  " }))).toBe(false);
  });

  it("does not recognise a row that has a value where the example has none", () => {
    expect(
      isSample({ sku: "0013-WHT", name: "Steel bracket, white", weight: 2 }),
    ).toBe(false);
  });

  it("compares the columns that are there case-insensitively, as before", () => {
    expect(isSample({ sku: "0012-blk", name: "STEEL BRACKET" })).toBe(true);
  });

  it("a deleted column cannot hide an edit elsewhere in the same row", () => {
    expect(isSample({ ...without("weight", "phone"), quantity: 999 })).toBe(false);
  });
});
