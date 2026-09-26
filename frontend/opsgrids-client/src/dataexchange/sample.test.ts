import { describe, expect, it } from "vitest";

import { InvalidCell } from "./cells";
import type { CellInput } from "./cells";
import { makeLookup } from "./lookups";
import { sampleMatcher, substituteSampleRows } from "./sample";
import { makeProductEntity, productLookups, productSamples } from "./testkit";
import type { ImportColumn, LookupSet } from "./types";

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

  it("does not recognise a row that only shares some of the cells, or one with an error cell", () => {
    const isSample = sampleMatcher(entity, productLookups);
    expect(isSample({ sku: "0012-BLK", name: "Steel bracket" })).toBe(false);
    expect(isSample(sheetRow({ quantity: new InvalidCell("#N/A", "contains the error #N/A") }))).toBe(false);
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
