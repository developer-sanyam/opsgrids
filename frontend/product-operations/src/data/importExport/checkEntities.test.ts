/**
 * The engine's own lint for an import definition (`checkImportEntity`), run over all four
 * Operations entities with lookups shaped like the ones their `loadLookups` builds. It coerces
 * every sample row through the real column rules and the entity's own `toPayload`, so a sample
 * row that would fail a customer's first import, a lookup nobody loads, or a column that breaks
 * the contract fails here instead.
 */
import { describe, expect, it } from "vitest";

import { checkImportEntity, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { warehouseImport } from "./warehouses";

const INVENTORY_LOOKUPS: LookupSet = {
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

const CATEGORY_LOOKUPS: LookupSet = {
  parents: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
  ]),
};

describe("checkImportEntity over the Operations definitions", () => {
  it("finds nothing wrong with inventory items", () => {
    expect(checkImportEntity(inventoryImport, INVENTORY_LOOKUPS)).toEqual([]);
  });

  it("finds nothing wrong with suppliers, which load no reference data", () => {
    expect(checkImportEntity(supplierImport, {})).toEqual([]);
  });

  it("finds nothing wrong with warehouses, which load no reference data", () => {
    expect(checkImportEntity(warehouseImport, {})).toEqual([]);
  });

  it("finds nothing wrong with item categories", () => {
    expect(checkImportEntity(itemCategoryImport, CATEGORY_LOOKUPS)).toEqual([]);
  });

  it("still passes for an organisation that has none of the warehouses the examples name", () => {
    // The sample sheet swaps an example naming a warehouse this organisation lacks for one it has.
    const lookups: LookupSet = {
      ...INVENTORY_LOOKUPS,
      warehouses: makeLookup([{ label: "Pune Plant", value: "Pune Plant" }]),
    };
    expect(checkImportEntity(inventoryImport, lookups)).toEqual([]);
  });

  it("still passes for an organisation with no item categories yet, whose examples name none", () => {
    const lookups: LookupSet = { ...INVENTORY_LOOKUPS, itemCategories: makeLookup([]) };
    expect(checkImportEntity(inventoryImport, lookups)).toEqual([]);
    expect(checkImportEntity(itemCategoryImport, { parents: makeLookup([]) })).toEqual([]);
  });

  it("has teeth: a lookup the definition names but nobody loaded is reported", () => {
    const problems = checkImportEntity(inventoryImport, {});
    expect(problems).toEqual(
      expect.arrayContaining([
        expect.stringContaining('uses lookup "warehouses", which loadLookups does not provide'),
        expect.stringContaining('uses lookup "units", which loadLookups does not provide'),
        expect.stringContaining('uses lookup "itemCategories", which loadLookups does not provide'),
      ]),
    );
  });
});
