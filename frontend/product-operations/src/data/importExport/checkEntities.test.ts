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

import { uomOut } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { makeUnitLookup } from "./units";
import { warehouseImport } from "./warehouses";

const WAREHOUSE_LOOKUPS: LookupSet = {
  warehouses: makeLookup([
    { label: "Main Warehouse", value: "Main Warehouse" },
    { label: "Cold Store", value: "Cold Store" },
  ]),
};

const INVENTORY_LOOKUPS: LookupSet = {
  ...WAREHOUSE_LOOKUPS,
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

const CATEGORY_LOOKUPS: LookupSet = {
  parents: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
  ]),
  codes: makeLookup([
    { label: "METALS", value: "METALS" },
    { label: "STEEL", value: "STEEL" },
  ]),
};

describe("checkImportEntity over the Operations definitions", () => {
  it("finds nothing wrong with inventory items", () => {
    expect(checkImportEntity(inventoryImport, INVENTORY_LOOKUPS)).toEqual([]);
  });

  it("finds nothing wrong with suppliers, which load no reference data", () => {
    expect(checkImportEntity(supplierImport, {})).toEqual([]);
  });

  it("finds nothing wrong with warehouses, given the names that already exist", () => {
    expect(checkImportEntity(warehouseImport, WAREHOUSE_LOOKUPS)).toEqual([]);
    expect(checkImportEntity(warehouseImport, { warehouses: makeLookup([]) })).toEqual([]);
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
    expect(checkImportEntity(itemCategoryImport, { parents: makeLookup([]), codes: makeLookup([]) })).toEqual([]);
  });

  it.each([
    ["inventory items", inventoryImport],
    ["suppliers", supplierImport],
    ["warehouses", warehouseImport],
    ["item categories", itemCategoryImport],
  ])("names a read-only id column as the record id of %s", (_name, entity) => {
    expect(entity.recordIdKey).toBe("id");
    expect(entity.columns.find((column) => column.key === "id")).toMatchObject({ readOnly: true });
  });

  it.each([
    ["inventory items", inventoryImport],
    ["item categories", itemCategoryImport],
  ])("gives every lookup column of %s a lookupHint, so an unknown name is told how to be fixed", (_name, entity) => {
    const lookupColumns = entity.columns.filter((column) => column.lookup !== undefined);
    expect(lookupColumns.length).toBeGreaterThan(0);
    for (const column of lookupColumns) {
      expect(column.lookupHint, `${column.header} has no lookupHint`).toBeTruthy();
    }
  });

  it.each([
    ["inventory items", inventoryImport],
    ["suppliers", supplierImport],
    ["warehouses", warehouseImport],
    ["item categories", itemCategoryImport],
  ])(
    "accounts for every importable column of %s in its update spec: an update field, an extra column or not updatable",
    (_name, entity) => {
      const update = entity.update;
      if (update === undefined) throw new Error(`${entity.id} has no update spec`);
      const accounted = new Set([
        ...update.fields.map((field) => field.column),
        ...(update.extraColumns ?? []),
        ...(update.notUpdatable ?? []),
      ]);
      const importable = entity.columns
        .filter((column) => column.readOnly !== true && column.importOnly !== true)
        .map((column) => column.key);
      expect(importable.filter((key) => !accounted.has(key))).toEqual([]);
    },
  );

  it("leaves nothing to notUpdatable or extraColumns: every importable column of these lists is an update field, so a filled cell is compared or refused with its reason", () => {
    for (const entity of [inventoryImport, supplierImport, warehouseImport, itemCategoryImport]) {
      expect(entity.update?.notUpdatable ?? [], entity.id).toEqual([]);
      expect(entity.update?.extraColumns ?? [], entity.id).toEqual([]);
    }
  });

  it.each([
    ["inventory items", inventoryImport],
    ["item categories", itemCategoryImport],
  ])("gives every lookup column of %s a hint for an update that differs from the add-mode hint", (_name, entity) => {
    for (const column of entity.columns.filter((candidate) => candidate.lookup !== undefined)) {
      expect(column.lookupHintUpdate, `${column.header} has no lookupHintUpdate`).toBeTruthy();
      expect(column.lookupHintUpdate).not.toBe(column.lookupHint);
    }
  });

  it("gives every update field on a lookup column a resolve, which is the only guard for a label on a row that updates", () => {
    for (const entity of [inventoryImport, itemCategoryImport]) {
      const lookupColumns = new Set(entity.columns.filter((column) => column.lookup !== undefined).map((column) => column.key));
      for (const field of entity.update?.fields ?? []) {
        if (lookupColumns.has(field.column)) expect(field.resolve, `${field.column} has no resolve`).toBeDefined();
      }
    }
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
