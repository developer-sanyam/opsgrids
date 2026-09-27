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
 *
 * An update is one `PATCH /operations/inventory/categories/{id}` carrying only the fields that
 * differ. `code` is immutable. A parent is emptied with `null` (top level), but `name` never can be
 * (the server answers a null name with a 422). Whether a new parent would make the tree circular is
 * checked by the server against the tree as it stands when the row is sent, so a row that fails
 * for that reason is the server's own answer, and the order of the rows in a file can decide it.
 */
import { collectPages, defineImportEntity } from "@opsgrids/client";
import type { ImportColumn, LookupSet } from "@opsgrids/client";

import { createItemCategory, listItemCategories, updateItemCategory } from "@/api/client";
import type { ItemCategoryIn, ItemCategoryOut, ItemCategoryPatch } from "@/api/types";
import { loadItemCategoryLookups } from "./referenceData";
import {
  columnOf,
  optionalText,
  refuseNearDuplicate,
  requiredText,
  resolveLookupCell,
  resolveReference,
} from "./shared";

function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  return loadItemCategoryLookups(signal);
}

const COLUMNS: readonly ImportColumn[] = [
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
    help: "The code of the category this one sits under. Leave blank for a top-level category. To move an existing category to the top level, use [clear].",
    lookup: "parents",
    lookupHint: "Import the parent first, then run this file again: rows already imported are skipped.",
    lookupHintUpdate:
      "The parent must already exist, so add it in an earlier run. A blank cell keeps the category's parent, and [clear] moves it to the top level.",
  },
  {
    key: "id",
    header: "Record ID",
    kind: "text",
    help: "The category's identifier in OpsGrids.",
    readOnly: true,
  },
];

const PARENT = columnOf(COLUMNS, "parent");

export const itemCategoryImport = defineImportEntity<ItemCategoryIn, ItemCategoryOut>({
  id: "operations.item-categories",
  noun: "item categories",
  nounSingular: "item category",
  fileStem: "item-categories",
  recordIdKey: "id",

  columns: COLUMNS,

  sampleRows: [
    { code: "METALS", name: "Metals" },
    { code: "POLYMERS", name: "Polymers" },
    { code: "FASTENERS", name: "Fasteners" },
  ],

  notes: [
    "A parent category must already exist. To import a tree, import the top-level categories first, then run the file again with their children.",
    "A category code that already exists is skipped, not changed, unless you choose Add new and update existing.",
    "These categories are your own catalogue tree. The fixed Category on an inventory item is a separate list.",
    "Updating: Category code cannot be changed, and a cell that disagrees with the category is refused. A code typed in other capitals still finds its category.",
    "Updating: Parent category code moves the category under that parent, and [clear] moves it to the top level. Category name cannot be emptied.",
    "Updating: each move is checked as its row is sent, in file order. A category cannot be its own parent or move under one of its own descendants, and a move that would make the tree circular is refused for that row, so list the moves in the order they should happen.",
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

  update: {
    loadExisting: (ctx) =>
      collectPages((page, size) => listItemCategories({ page, size, signal: ctx.signal }), {
        signal: ctx.signal,
        key: (category) => category.id,
      }),
    idOf: (category) => category.id,
    keyColumn: "code",
    keyOf: (category) => category.code,

    fields: [
      { column: "code", current: (category) => category.code, immutable: true },
      { column: "name", current: (category) => category.name },
      {
        column: "parent",
        field: "parent_id",
        current: (category) => category.parent_id,
        resolve: resolveLookupCell(PARENT),
        clear: null,
      },
    ],

    async send(category, body) {
      await updateItemCategory(category.id, body as ItemCategoryPatch);
    },
  },
});
