/**
 * Warehouses, for the Import / Export button on the Warehouses screen (ADR-0022).
 *
 * One row is one `POST /operations/warehouses`. A warehouse's NAME is what inventory items and
 * purchase orders use to refer to it, so a duplicate name is refused as firmly as a duplicate code.
 * The server compares names exactly and would accept "main warehouse" beside "Main Warehouse", after
 * which no sheet could name either of them, so a name that differs from an existing one only in
 * capitals or spacing is refused here.
 */
import { defineImportEntity } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createWarehouse, listWarehouses } from "@/api/client";
import type { WarehouseIn, WarehouseOut } from "@/api/types";
import { loadWarehouseNames } from "./referenceData";
import { optionalText, refuseNearDuplicate, requiredText } from "./shared";

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
      help: "What inventory items and purchase orders call this warehouse. It must be unique.",
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
    "A warehouse code or name that already exists is skipped, not changed. A name that only differs from an existing one in capitals or spacing is refused.",
    "Inventory items refer to a warehouse by its name, so import warehouses before importing the items that are stocked in them.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const name = requiredText(row, "name", "Warehouse name");
    refuseNearDuplicate(ctx.lookups.warehouses, name, (existing) =>
      existing === null
        ? `More than one warehouse already has a name that differs from '${name}' only in capitals or spacing, so it is not accepted.`
        : `A warehouse called '${existing}' already exists; names that differ only in capitals or spacing are not accepted.`,
    );
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
});
