/**
 * Warehouses, for the Import / Export button on the Warehouses screen (ADR-0022).
 *
 * One row is one `POST /operations/warehouses`. A warehouse's NAME is what inventory items and
 * purchase orders use to refer to it, so a duplicate name is refused as firmly as a duplicate code.
 * The server compares names exactly and would accept "main warehouse" beside "Main Warehouse", after
 * which no sheet could name either of them, so a name that differs from an existing one only in
 * capitals or spacing is refused here. So is one that differs from a name an EARLIER ROW of the same
 * file gives (`refuseNearDuplicate` keeps that per file), which the list loaded when the file was
 * opened cannot know: two renames to "Delta" and "delta" would each pass against it.
 *
 * An update is one `PATCH /operations/warehouses/{id}` carrying only the fields that differ. The
 * endpoint has no null guard, so the only empty value it is sent is `""` on `location`. A rename
 * rewrites the name on every inventory item and purchase order that carries it, silently, and the
 * server checks a new name only for an exact match, so the capitals-and-spacing rule above is
 * applied to a rename too (except to a different spelling of the warehouse's own name).
 *
 * A row finds its warehouse by Record ID or by its code, and the code is an exact key on the server
 * (`keyMatch` is left at the engine's default, `exact`), so a code that differs only in capitals is
 * never taken for the same warehouse. A row whose code is new and whose name another warehouse has
 * is not a rename: it is a create, and the server answers it with a 409 that update mode reports as
 * a failed row.
 */
import { collectPages, defineImportEntity } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createWarehouse, listWarehouses, updateWarehouse } from "@/api/client";
import type { WarehouseIn, WarehouseOut, WarehousePatch } from "@/api/types";
import { loadWarehouseNames } from "./referenceData";
import { optionalText, patchOf, refuseNearDuplicate, requiredText, textOrNull } from "./shared";
import type { NameWords } from "./shared";

const NAME_WORDS: NameWords = {
  names: "warehouse names",
  existing: (existing, typed) =>
    existing === null
      ? `More than one warehouse already has a name that differs from '${typed}' only in capitals or spacing, so it is not accepted.`
      : `A warehouse called '${existing}' already exists; names that differ only in capitals or spacing are not accepted.`,
  earlier: (earlier) =>
    `An earlier row of this file gives a warehouse the name '${earlier}'; names that differ only in capitals or spacing are not accepted.`,
};

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
    "A warehouse code that already exists is skipped, not changed, unless you choose Add new and update existing. A name that another warehouse already has is skipped too, and never renames that warehouse. A name that only differs from an existing one in capitals or spacing is refused, and so is a name that only differs in capitals or spacing from one an earlier row of the same file gives.",
    "Inventory items refer to a warehouse by its name, so import warehouses before importing the items that are stocked in them.",
    "Updating: a row finds its warehouse by its Record ID, or by its code spelled exactly as it is stored. A code that differs only in capitals is refused, naming the stored spelling: use that spelling, or the Record ID.",
    "Updating: Warehouse code cannot be changed, and a cell that disagrees with the warehouse is refused.",
    "Updating: Location can be emptied with [clear]. Warehouse name cannot be.",
    "Updating: renaming a warehouse also renames it, without notice, on every inventory item and purchase order that uses it. Run the rename first, then any inventory sheet that names the new warehouse.",
    "Updating: to rename a warehouse, keep its code and change its name. A row with a new code and a name that another warehouse has is not a rename: it fails, and that warehouse is left as it is.",
    "Updating: a new name that another warehouse already has, even in other capitals or spacing, is refused.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const code = requiredText(row, "code", "Warehouse code");
    const name = requiredText(row, "name", "Warehouse name");
    const location = optionalText(row, "location");
    refuseNearDuplicate(ctx, "warehouses", name, NAME_WORDS);
    return { code, name, ...(location === null ? {} : { location }) };
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
    recordLabel: (warehouse) => `${warehouse.code} · ${warehouse.name}`,

    fields: [
      { column: "code", current: (warehouse) => warehouse.code, immutable: true },
      { column: "name", current: (warehouse) => warehouse.name },
      { column: "location", current: (warehouse) => textOrNull(warehouse.location), clear: "" },
    ],

    check(row, warehouse, _changed, ctx) {
      const name = optionalText(row, "name");
      if (name === null || name === warehouse.name) return;
      // A different spelling of the warehouse's own name (a change of capitals) is a fair rename.
      refuseNearDuplicate(ctx, "warehouses", name, NAME_WORDS, warehouse.name);
    },

    async send(warehouse, body) {
      await updateWarehouse(warehouse.id, patchOf<WarehousePatch>(body));
    },
  },
});
