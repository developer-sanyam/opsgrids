/**
 * Inventory items, for the Import / Export button on Stock on Hand (ADR-0022).
 *
 * One row is one `POST /operations/inventory`. Three things the API is strict about shape the
 * definition:
 *  - `warehouse` must equal an existing warehouse's NAME (not its code), so the sheet names the
 *    warehouse and the lookup hands back the name exactly as the API stores it.
 *  - `uom` is free text on the API and an unknown value silently creates a custom unit, so a typo
 *    would mint one. The unit is checked against the organisation's own units instead, and against
 *    the spellings the server folds onto them ("Nos" is `pcs`), which is what a Tally sheet says.
 *  - `item_category_id` wants the id, while a person knows the code: the sheet carries the code and
 *    the lookup turns it into the id.
 */
import { defineImportEntity, minorToMajor } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createInventoryItem, listInventory } from "@/api/client";
import type { ListInventoryParams } from "@/api/client";
import type { InventoryItemIn, InventoryItemOut } from "@/api/types";
import { loadItemCategoryCodes, loadUnits, loadWarehouseNames } from "./referenceData";
import {
  CATEGORY_OPTIONS,
  CURRENCY_HINT,
  CURRENCY_PATTERN,
  optionLabel,
  optionalBoolean,
  optionalNumber,
  optionalText,
  requiredOption,
  requiredText,
  resolveReference,
} from "./shared";
import { canonicalUnitLabel } from "./units";

/** What Stock on Hand filters by: the export is the whole filtered list, not the visible page. */
export type InventoryFilters = Omit<ListInventoryParams, "page" | "size" | "signal">;

const HSN_PATTERN = /^(\d{4}(\d{2}(\d{2})?)?)?$/;

/** The largest reorder point the API can hold. The column is a 32-bit integer and the low-stock
 *  filter multiplies it by 1000 (milli-units), so one above this makes `GET /inventory?low_stock=true`
 *  fail for the whole organisation, not just for the item. */
const MAX_REORDER_POINT = 2_147_483;

async function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  const [warehouses, units, itemCategories] = await Promise.all([
    loadWarehouseNames(signal),
    loadUnits(signal),
    loadItemCategoryCodes(signal),
  ]);
  return { warehouses, units, itemCategories };
}

export const inventoryImport = defineImportEntity<InventoryItemIn, InventoryItemOut, InventoryFilters>({
  id: "operations.inventory",
  noun: "inventory items",
  nounSingular: "inventory item",
  fileStem: "inventory-items",
  recordIdKey: "id",

  columns: [
    {
      key: "sku",
      header: "SKU",
      kind: "text",
      required: true,
      help: "The item's unique code. It cannot be changed once the item exists.",
      maxLength: 60,
      pattern: /^[A-Za-z0-9._-]+$/,
      patternHint: "Letters, digits, . _ -",
    },
    {
      key: "name",
      header: "Item name",
      kind: "text",
      required: true,
      help: "What the item is called.",
      maxLength: 200,
    },
    {
      key: "category",
      header: "Category",
      kind: "enum",
      required: true,
      help: "The procurement category the item's spend rolls up to.",
      options: CATEGORY_OPTIONS,
    },
    {
      key: "warehouse",
      header: "Warehouse",
      kind: "text",
      required: true,
      help: "Where the item is stocked. The name of an existing warehouse, as on the Lists sheet.",
      maxLength: 120,
      lookup: "warehouses",
      lookupHint: "Warehouses must exist already and are matched by name, ignoring capitals.",
    },
    {
      key: "uom",
      header: "Unit of measure",
      kind: "text",
      required: true,
      help: "How the item is counted, such as pcs, kg or l. Must be a unit on the Lists sheet, or a common spelling of one such as Nos, Kgs or Ltr.",
      maxLength: 20,
      lookup: "units",
      lookupHint:
        "Only units that already exist can be used, so a misspelling cannot create a new one. See the Lists sheet.",
    },
    {
      key: "reorder_point",
      header: "Reorder point",
      kind: "integer",
      help: "Stock at or below this many whole units counts as low stock, from 0 to 2,147,483. Blank means 0.",
      min: 0,
      max: MAX_REORDER_POINT,
    },
    {
      key: "unit_value",
      header: "Unit value",
      kind: "money",
      help: "Cost of one unit of measure, in the currency below, for example 62.40. Blank means 0.",
      min: 0,
      currencyKey: "currency",
    },
    {
      key: "currency",
      header: "Currency",
      kind: "text",
      help: "The currency of the unit value. Blank means INR.",
      maxLength: 3,
      pattern: CURRENCY_PATTERN,
      patternHint: CURRENCY_HINT,
    },
    {
      key: "opening_stock",
      header: "Opening stock",
      kind: "integer",
      help: "Whole units in stock when the item is created. Blank means 0.",
      min: 0,
      max: 1_000_000_000_000,
      importOnly: true,
    },
    {
      key: "item_category",
      header: "Item category code",
      kind: "text",
      help: "The code of the item's category in your catalogue tree. That category must already exist.",
      lookup: "itemCategories",
      lookupHint: "Item categories must exist already and are matched by code, ignoring capitals.",
    },
    {
      key: "hsn_code",
      header: "HSN code",
      kind: "text",
      help: "Tax classification code. Keep the cell as text so leading zeros survive.",
      pattern: HSN_PATTERN,
      patternHint: "4, 6 or 8 digits",
    },
    {
      key: "track_lot",
      header: "Track lots",
      kind: "boolean",
      help: "Whether stock of this item is tracked by lot. Blank means no.",
    },
    {
      key: "track_roll",
      header: "Track rolls",
      kind: "boolean",
      help: "Whether stock of this item is tracked by roll. Blank means no.",
    },
    {
      key: "track_expiry",
      header: "Track expiry",
      kind: "boolean",
      help: "Whether stock of this item carries an expiry date. Blank means no.",
    },
    {
      key: "track_quality",
      header: "Track quality",
      kind: "boolean",
      help: "Whether stock of this item carries a quality status. Blank means no.",
    },
    {
      key: "is_active",
      header: "Active",
      kind: "boolean",
      help: "Whether the item appears in pickers and new transactions. Blank means active.",
    },
    {
      key: "on_hand",
      header: "On hand",
      kind: "decimal",
      help: "Stock on hand, worked out by the app from stock movements.",
      readOnly: true,
    },
    {
      key: "reserved",
      header: "Reserved",
      kind: "decimal",
      help: "Stock set aside for reservations, worked out by the app.",
      readOnly: true,
    },
    {
      key: "available",
      header: "Available",
      kind: "decimal",
      help: "Stock on hand less what is reserved, worked out by the app.",
      readOnly: true,
    },
    {
      key: "updated_at",
      header: "Last updated",
      kind: "text",
      help: "When the item was last changed.",
      readOnly: true,
    },
    {
      key: "id",
      header: "ID",
      kind: "text",
      help: "The item's identifier in OpsGrids.",
      readOnly: true,
    },
  ],

  sampleRows: [
    {
      sku: "RM-STL-4501",
      name: "Cold-rolled steel coil, 1.2 mm",
      category: "Raw materials",
      warehouse: "Main Warehouse",
      uom: "kg",
      reorder_point: 4000,
      unit_value: "62.40",
      currency: "INR",
      opening_stock: 8200,
      hsn_code: "7209",
    },
    {
      sku: "PKG-BOX-330",
      name: "Double-wall export carton",
      category: "Packaging",
      warehouse: "Main Warehouse",
      uom: "pcs",
      reorder_point: 5000,
      unit_value: "78",
      currency: "INR",
      opening_stock: 12400,
      hsn_code: "481910",
    },
    {
      sku: "MRO-LUB-220",
      name: "Industrial gear oil, ISO 220",
      category: "MRO",
      warehouse: "Main Warehouse",
      uom: "l",
      reorder_point: 60,
      unit_value: "385.50",
      currency: "INR",
      opening_stock: 180,
      track_lot: true,
      track_expiry: true,
    },
  ],

  notes: [
    "The warehouse must already exist. It is matched by name, so copy it from the Lists sheet; a warehouse that is not there is refused.",
    "The unit of measure must already exist. A unit that is not on the Lists sheet is refused instead of creating a new unit, so a typo cannot slip in. Common spellings such as Nos, Kgs, Ltr and Mtr are accepted and stored as pcs, kg, l and m.",
    "Opening stock is set only when the item is created. To change the stock of an item later, use Adjust stock in the app.",
    "An item category must exist before items can use it. Import item categories first, then give each item the category's code.",
    "Category is the fixed procurement category. Item category code places the item in your own catalogue tree.",
    "A SKU that already exists is skipped, not changed. The same SKU cannot be used twice, even as a variant SKU.",
    "The On hand, Reserved, Available and Last updated columns are worked out by the app and ignored on import. A row that has an ID came out of OpsGrids in an export, so it is not added again.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const category = requiredOption(row, "category", "Category", CATEGORY_OPTIONS);
    const warehouseName = requiredText(row, "warehouse", "Warehouse");
    const warehouse = resolveReference(ctx.lookups, "warehouses", warehouseName, "Warehouse");
    const unitName = requiredText(row, "uom", "Unit of measure");
    const uom = resolveReference(ctx.lookups, "units", unitName, "Unit of measure");

    const itemCategoryCode = optionalText(row, "item_category");
    const itemCategoryId =
      itemCategoryCode === null
        ? null
        : resolveReference(ctx.lookups, "itemCategories", itemCategoryCode, "Item category code");

    const reorderPoint = optionalNumber(row, "reorder_point");
    const unitValueMinor = optionalNumber(row, "unit_value");
    const currency = optionalText(row, "currency");
    const openingStock = optionalNumber(row, "opening_stock");
    const hsnCode = optionalText(row, "hsn_code");
    const trackLot = optionalBoolean(row, "track_lot");
    const trackRoll = optionalBoolean(row, "track_roll");
    const trackExpiry = optionalBoolean(row, "track_expiry");
    const trackQuality = optionalBoolean(row, "track_quality");
    const isActive = optionalBoolean(row, "is_active");

    return {
      sku: requiredText(row, "sku", "SKU"),
      name: requiredText(row, "name", "Item name"),
      category,
      warehouse,
      uom,
      ...(reorderPoint === null ? {} : { reorder_point: reorderPoint }),
      ...(unitValueMinor === null ? {} : { unit_value_minor: unitValueMinor }),
      ...(currency === null ? {} : { currency: currency.toUpperCase() }),
      ...(openingStock === null ? {} : { opening_on_hand: openingStock }),
      ...(itemCategoryId === null ? {} : { item_category_id: itemCategoryId }),
      ...(hsnCode === null ? {} : { hsn_code: hsnCode }),
      ...(trackLot === null ? {} : { track_lot: trackLot }),
      ...(trackRoll === null ? {} : { track_roll: trackRoll }),
      ...(trackExpiry === null ? {} : { track_expiry: trackExpiry }),
      ...(trackQuality === null ? {} : { track_quality: trackQuality }),
      ...(isActive === null ? {} : { is_active: isActive }),
    };
  },

  async create(payload) {
    const created = await createInventoryItem(payload);
    return { id: created.id };
  },

  export: {
    fetchPage: ({ page, size, filters, signal }) => listInventory({ ...filters, page, size, signal }),

    toRow: (item, lookups) => ({
      sku: item.sku,
      name: item.name,
      category: optionLabel(CATEGORY_OPTIONS, item.category),
      warehouse: item.warehouse,
      uom: canonicalUnitLabel(lookups.units, item.uom),
      reorder_point: item.reorder_point,
      unit_value: minorToMajor(item.unit_value_minor, item.currency),
      currency: item.currency,
      item_category:
        item.item_category_id === null ? null : (lookups.itemCategories?.labelOf(item.item_category_id) ?? null),
      hsn_code: item.hsn_code === "" ? null : item.hsn_code,
      track_lot: item.track_lot,
      track_roll: item.track_roll,
      track_expiry: item.track_expiry,
      track_quality: item.track_quality,
      is_active: item.is_active,
      on_hand: item.on_hand_qty,
      reserved: item.reserved_qty,
      available: item.available_qty,
      updated_at: item.updated_at,
      id: item.id,
    }),
  },
});
