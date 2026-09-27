/**
 * Warehouses, for the Import / Export button on the Warehouses screen (ADR-0022).
 *
 * One row is one `POST /operations/warehouses`. A warehouse's NAME is what inventory items and
 * purchase orders use to refer to it, so a duplicate name is refused as firmly as a duplicate code.
 * The server compares names exactly and would accept "main warehouse" beside "Main Warehouse", after
 * which no sheet could name either of them, so a name that differs from an existing one only in
 * capitals or spacing is refused here.
 *
 * An update is one `PATCH /operations/warehouses/{id}` carrying only the fields that differ. The
 * endpoint has no null guard, so the only empty value it is sent is `""` on `location`. A rename
 * rewrites the name on every inventory item and purchase order that carries it, silently, and the
 * server checks a new name only for an exact match, so the capitals-and-spacing rule above is
 * applied to a rename too (except to a different spelling of the warehouse's own name).
 */
import { collectPages, defineImportEntity } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createWarehouse, listWarehouses, updateWarehouse } from "@/api/client";
import type { WarehouseIn, WarehouseOut, WarehousePatch } from "@/api/types";
import { loadWarehouseNames } from "./referenceData";
import { optionalText, refuseNearDuplicate, requiredText, sameCode, textOrNull } from "./shared";

function nearDuplicateName(name: string, existing: string | null): string {
  return existing === null
    ? `More than one warehouse already has a name that differs from '${name}' only in capitals or spacing, so it is not accepted.`
    : `A warehouse called '${existing}' already exists; names that differ only in capitals or spacing are not accepted.`;
}

async function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  return { warehouses: await loadWarehouseNames(signal) };
}

export const warehouseImport = defineImportEntity<WarehouseIn, WarehouseOut>({
  id: "operations.warehouses",
  noun: "warehouses",
  nounSingular: "warehouse",
  fileStem: "warehouses",
  recordIdKey: "id",

  columns: [
    {
      key: "code",
      header: "Warehouse code",
      kind: "text",
      required: true,
      help: "The warehouse's unique code. It cannot be changed once the warehouse exists.",
      maxLength: 40,
      pattern: /^[A-Za-z0-9_-]+$/,
      patternHint: "Letters, digits, _ -",
    },
    {
      key: "name",
      header: "Warehouse name",
      kind: "text",
      required: true,
      help: "What inventory items and purchase orders call this warehouse. It must be unique. Renaming an existing warehouse renames it on every inventory item and purchase order that uses it.",
      maxLength: 120,
    },
    {
      key: "location",
      header: "Location",
      kind: "text",
      help: "Where the warehouse is, for example a town and state.",
      maxLength: 160,
    },
    {
      key: "id",
      header: "Record ID",
      kind: "text",
      help: "The warehouse's identifier in OpsGrids.",
      readOnly: true,
    },
  ],

  sampleRows: [
    { code: "WH-MAIN", name: "Main Warehouse", location: "Bhiwandi, Maharashtra" },
    { code: "WH-COLD", name: "Cold Store", location: "Nashik, Maharashtra" },
  ],

  notes: [
    "Only people who are allowed to create warehouses can import them.",
    "A warehouse code or name that already exists is skipped, not changed, unless you choose Add new and update existing. A name that only differs from an existing one in capitals or spacing is refused.",
    "Inventory items refer to a warehouse by its name, so import warehouses before importing the items that are stocked in them.",
    "Updating: Warehouse code cannot be changed, and a cell that disagrees with the warehouse is refused. A code typed in other capitals still finds its warehouse.",
    "Updating: Location can be emptied with [clear]. Warehouse name cannot be.",
    "Updating: renaming a warehouse also renames it, without notice, on every inventory item and purchase order that uses it. Run the rename first, then any inventory sheet that names the new warehouse.",
    "Updating: a new name that another warehouse already has, even in other capitals or spacing, is refused.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const name = requiredText(row, "name", "Warehouse name");
    refuseNearDuplicate(ctx.lookups.warehouses, name, (existing) => nearDuplicateName(name, existing));
    const location = optionalText(row, "location");
    return {
      code: requiredText(row, "code", "Warehouse code"),
      name,
      ...(location === null ? {} : { location }),
    };
  },

  async create(payload) {
    const created = await createWarehouse(payload);
    return { id: created.id };
  },

  export: {
    fetchPage: ({ page, size, signal }) => listWarehouses({ page, size, signal }),

    toRow: (warehouse) => ({
      code: warehouse.code,
      name: warehouse.name,
      location: warehouse.location === "" ? null : warehouse.location,
      id: warehouse.id,
    }),
  },

  update: {
    loadExisting: (ctx) =>
      collectPages((page, size) => listWarehouses({ page, size, signal: ctx.signal }), {
        signal: ctx.signal,
        key: (warehouse) => warehouse.id,
      }),
    idOf: (warehouse) => warehouse.id,
    keyColumn: "code",
    keyOf: (warehouse) => warehouse.code,

    fields: [
      { column: "code", current: (warehouse) => warehouse.code, same: sameCode, immutable: true },
      { column: "name", current: (warehouse) => warehouse.name },
      { column: "location", current: (warehouse) => textOrNull(warehouse.location), clear: "" },
    ],

    check(row, warehouse, _changed, ctx) {
      const name = optionalText(row, "name");
      if (name === null || name === warehouse.name) return;
      // A different spelling of the warehouse's own name (a change of capitals) is a fair rename.
      if (ctx.lookups.warehouses?.resolve(name) === warehouse.name) return;
      refuseNearDuplicate(ctx.lookups.warehouses, name, (existing) => nearDuplicateName(name, existing));
    },

    async send(warehouse, body) {
      await updateWarehouse(warehouse.id, body as WarehousePatch);
    },
  },
});
