/**
 * The shape of the four Operations `update` specs, held against what the update endpoints accept
 * (the backend survey behind ADR-0022's addendum). A column added to a list without a decision here,
 * a `clear` value that the endpoint answers with a 409 or a 500, or a key the endpoint refuses with
 * a 422, fails this file instead of a customer's sheet.
 */
import { describe, expect, it } from "vitest";

import type { ImportEntity } from "@opsgrids/client";

import type { InventoryItemPatch, ItemCategoryPatch, SupplierPatch, WarehousePatch } from "@/api/types";
import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { updateSpec } from "./updateTestKit";
import { warehouseImport } from "./warehouses";

interface Expected {
  keyColumn: string;
  /** Columns the update compares but never sends. */
  immutable: string[];
  /** What each clearable column is sent to empty it. Everything else refuses `[clear]`. */
  clear: Record<string, "" | null>;
  /** The keys a body can carry: the endpoint's own field names, and no other. Each list below is
   *  declared `satisfies readonly (keyof ...Patch)[]`, so a name the endpoint's patch type lacks does not
   *  compile, and the test holds the spec's own wire keys equal to it. */
  wireKeys: readonly string[];
  pricedInItsOwnCurrency: boolean;
}

function describeSpec<Payload, Item, Filters>(
  name: string,
  entity: ImportEntity<Payload, Item, Filters>,
  expected: Expected,
): void {
  describe(`the ${name} update spec`, () => {
    const spec = updateSpec(entity);
    const columns = new Map(entity.columns.map((column) => [column.key, column]));

    it("has one field for every importable column, and none for a read-only one", () => {
      const importable = entity.columns.filter((column) => column.readOnly !== true).map((column) => column.key);
      expect(spec.fields.map((field) => field.column).sort()).toEqual([...importable].sort());
    });

    it("uses one field per column", () => {
      const seen = spec.fields.map((field) => field.column);
      expect(new Set(seen).size).toBe(seen.length);
    });

    it("keys records by the column that is unique and required, and matches by Record ID first", () => {
      expect(spec.keyColumn).toBe(expected.keyColumn);
      expect(columns.get(expected.keyColumn)?.required).toBe(true);
      expect(entity.recordIdKey).toBe("id");
    });

    it("matches its key exactly: the server compares it exactly, so the engine's default (`exact`) must stand", () => {
      expect(spec.keyMatch).toBeUndefined();
    });

    it("names the record a row updates, so that a shifted Record ID column is seen in the preview", () => {
      expect(spec.recordLabel).toBeDefined();
    });

    it("holds the identity, currency and stock columns immutable, and nothing else", () => {
      const immutable = spec.fields.filter((field) => field.immutable === true).map((field) => field.column);
      expect(immutable.sort()).toEqual([...expected.immutable].sort());
    });

    it("lets [clear] empty only the columns whose endpoint takes an empty value, with the value it takes", () => {
      const clear = Object.fromEntries(
        spec.fields.filter((field) => "clear" in field).map((field) => [field.column, field.clear]),
      );
      expect(clear).toEqual(expected.clear);
    });

    it("sends under the endpoint's own field names, and never sends an immutable column", () => {
      const wire = spec.fields.filter((field) => field.immutable !== true).map((field) => field.field ?? field.column);
      expect(wire.sort()).toEqual([...expected.wireKeys].sort());
    });

    it("resolves every lookup column the way create does, and no other", () => {
      const lookups = entity.columns.filter((column) => column.lookup !== undefined).map((column) => column.key);
      const resolving = spec.fields.filter((field) => field.resolve !== undefined).map((field) => field.column);
      expect(resolving.sort()).toEqual(lookups.sort());
    });

    it("names the stored currency only for a list whose amounts carry one", () => {
      expect(spec.currencyOf !== undefined).toBe(expected.pricedInItsOwnCurrency);
    });
  });
}

describeSpec("inventory items", inventoryImport, {
  keyColumn: "sku",
  immutable: ["sku", "currency", "opening_stock"],
  clear: { item_category: null, hsn_code: "" },
  wireKeys: [
    "name",
    "category",
    "warehouse",
    "uom",
    "reorder_point",
    "unit_value_minor",
    "item_category_id",
    "hsn_code",
    "track_lot",
    "track_roll",
    "track_expiry",
    "track_quality",
    "is_active",
  ] as const satisfies readonly (keyof InventoryItemPatch)[],
  pricedInItsOwnCurrency: true,
});

describeSpec("suppliers", supplierImport, {
  keyColumn: "code",
  immutable: ["code", "currency"],
  clear: { contact_name: "", contact_email: "", location: "" },
  wireKeys: [
    "name",
    "category",
    "status",
    "lead_time_days",
    "contact_name",
    "contact_email",
    "location",
    "since",
  ] as const satisfies readonly (keyof SupplierPatch)[],
  pricedInItsOwnCurrency: true,
});

describeSpec("warehouses", warehouseImport, {
  keyColumn: "code",
  immutable: ["code"],
  clear: { location: "" },
  wireKeys: ["name", "location"] as const satisfies readonly (keyof WarehousePatch)[],
  pricedInItsOwnCurrency: false,
});

describeSpec("item categories", itemCategoryImport, {
  keyColumn: "code",
  immutable: ["code"],
  clear: { parent: null },
  wireKeys: ["name", "parent_id"] as const satisfies readonly (keyof ItemCategoryPatch)[],
  pricedInItsOwnCurrency: false,
});
