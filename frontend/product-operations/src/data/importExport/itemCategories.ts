/**
 * Item categories, for the Import / Export button on the Item categories screen (ADR-0022).
 *
 * One row is one `POST /operations/inventory/categories`. A parent is named by its code and turned
 * into the id the API wants from the categories that exist when the dialog opens. A child whose
 * parent is created by the same file therefore cannot be resolved: the row is refused, and the
 * column's `lookupHint` says to import the parent first and run the file again.
 *
 * The server compares codes exactly and would accept "metals" beside "METALS", after which no sheet
 * could name either as a parent or as an item's category, so a code that differs from an existing
 * one only in capitals or spacing is refused here.
 */
import { defineImportEntity } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createItemCategory, listItemCategories } from "@/api/client";
import type { ItemCategoryIn, ItemCategoryOut } from "@/api/types";
import { loadItemCategoryLookups } from "./referenceData";
import { optionalText, refuseNearDuplicate, requiredText, resolveReference } from "./shared";

function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  return loadItemCategoryLookups(signal);
}

export const itemCategoryImport = defineImportEntity<ItemCategoryIn, ItemCategoryOut>({
  id: "operations.item-categories",
  noun: "item categories",
  nounSingular: "item category",
  fileStem: "item-categories",
  recordIdKey: "id",

  columns: [
    {
      key: "code",
      header: "Category code",
      kind: "text",
      required: true,
      help: "The category's unique code. Inventory items name their category by this code.",
      maxLength: 40,
      pattern: /^[A-Za-z0-9._-]+$/,
      patternHint: "Letters, digits, . _ -",
    },
    {
      key: "name",
      header: "Category name",
      kind: "text",
      required: true,
      help: "What the category is called.",
      maxLength: 120,
    },
    {
      key: "parent",
      header: "Parent category code",
      kind: "text",
      help: "The code of the category this one sits under. Leave blank for a top-level category.",
      lookup: "parents",
      lookupHint: "Import the parent first, then run this file again: rows already imported are skipped.",
    },
    {
      key: "id",
      header: "Record ID",
      kind: "text",
      help: "The category's identifier in OpsGrids.",
      readOnly: true,
    },
  ],

  sampleRows: [
    { code: "METALS", name: "Metals" },
    { code: "POLYMERS", name: "Polymers" },
    { code: "FASTENERS", name: "Fasteners" },
  ],

  notes: [
    "A parent category must already exist. To import a tree, import the top-level categories first, then run the file again with their children.",
    "A category code that already exists is skipped, not changed.",
    "These categories are your own catalogue tree. The fixed Category on an inventory item is a separate list.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const code = requiredText(row, "code", "Category code");
    refuseNearDuplicate(ctx.lookups.codes, code, (existing) =>
      existing === null
        ? `More than one item category already has a code that differs from '${code}' only in capitals, so it is not accepted.`
        : `An item category with the code '${existing}' already exists; codes that differ only in capitals are not accepted.`,
    );
    const parentCode = optionalText(row, "parent");
    const parentId =
      parentCode === null ? null : resolveReference(ctx.lookups, "parents", parentCode, "Parent category code");
    return {
      code,
      name: requiredText(row, "name", "Category name"),
      ...(parentId === null ? {} : { parent_id: parentId }),
    };
  },

  async create(payload) {
    const created = await createItemCategory(payload);
    return { id: created.id };
  },

  export: {
    fetchPage: ({ page, size, signal }) => listItemCategories({ page, size, signal }),

    toRow: (category, lookups) => ({
      code: category.code,
      name: category.name,
      parent: category.parent_id === null ? null : (lookups.parents?.labelOf(category.parent_id) ?? null),
      id: category.id,
    }),
  },
});
