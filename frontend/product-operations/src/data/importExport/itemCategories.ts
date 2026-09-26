/**
 * Item categories, for the Import / Export button on the Item categories screen (ADR-0022).
 *
 * One row is one `POST /operations/inventory/categories`. A parent is named by its code and turned
 * into the id the API wants from the categories that exist when the dialog opens. A child whose
 * parent is created by the same file therefore cannot be resolved: the row is refused with a
 * sentence that says to import the parent first and run the file again.
 */
import { defineImportEntity, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";

import { createItemCategory, listItemCategories } from "@/api/client";
import type { ItemCategoryIn, ItemCategoryOut } from "@/api/types";
import { collectAll, labelForValue, optionalText, requiredText, resolveReference } from "./shared";

async function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  const categories = await collectAll((page, size) => listItemCategories({ page, size, signal }), signal);
  return { parents: makeLookup(categories.map((c) => ({ label: c.code, value: c.id }))) };
}

export const itemCategoryImport = defineImportEntity<ItemCategoryIn, ItemCategoryOut>({
  id: "operations.item-categories",
  noun: "item categories",
  nounSingular: "item category",
  fileStem: "item-categories",

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
    },
    {
      key: "id",
      header: "ID",
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
    "The ID column is worked out by the app and ignored on import.",
  ],

  loadLookups,

  toPayload(row, ctx) {
    const parentCode = optionalText(row, "parent");
    const parentId =
      parentCode === null
        ? null
        : resolveReference(ctx.lookups, "parents", parentCode, {
            unknown: (label) =>
              `The parent category "${label}" does not exist yet. Import the parent first, then run this file again.`,
            ambiguous: (label) =>
              `More than one category has a code matching "${label}" (codes that differ only by capitals), so the parent cannot be chosen. Rename one of them in the app, then import again.`,
          });
    return {
      code: requiredText(row, "code", "Category code"),
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
      parent: labelForValue(lookups.parents, category.parent_id),
      id: category.id,
    }),
  },
});
