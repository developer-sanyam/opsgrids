/**
 * The reference lists the import definitions look names up in: every warehouse, every unit of
 * measure and every item category the organisation has, read page by page from the API. Each is
 * read in full and each record once, by id: a list that repeats a record across two pages, or
 * comes back short of the server's own total, is refused rather than handed over partial, because
 * a lookup missing a real warehouse would call it unknown.
 */
import { collectPages, makeLookup } from "@opsgrids/client";
import type { Lookup } from "@opsgrids/client";

import { listItemCategories, listUoms, listWarehouses } from "@/api/client";
import { makeUnitLookup } from "./units";

/** Warehouse names. A name is what an inventory item or a purchase order stores, so the label and
 *  the value are both the name exactly as the API holds it. */
export async function loadWarehouseNames(signal: AbortSignal): Promise<Lookup> {
  const warehouses = await collectPages((page, size) => listWarehouses({ page, size, signal }), {
    signal,
    key: (warehouse) => warehouse.id,
  });
  return makeLookup(warehouses.map((warehouse) => ({ label: warehouse.name, value: warehouse.name })));
}

/** Item category codes, which a person knows, to the ids the API wants. */
export async function loadItemCategoryCodes(signal: AbortSignal): Promise<Lookup> {
  const categories = await collectPages((page, size) => listItemCategories({ page, size, signal }), {
    signal,
    key: (category) => category.id,
  });
  return makeLookup(categories.map((category) => ({ label: category.code, value: category.id })));
}

/** Units of measure, including the spellings the server folds onto them (see `units.ts`). */
export async function loadUnits(signal: AbortSignal): Promise<Lookup> {
  const units = await collectPages((page, size) => listUoms({ page, size, signal }), {
    signal,
    key: (unit) => unit.id,
  });
  return makeUnitLookup(units);
}
