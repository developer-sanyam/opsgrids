import { describe, expect, it } from "vitest";

import { checkImportEntity } from "./checkEntity";
import { coerceRow, unacceptedLookupValue } from "./coerce";
import { makeLookup } from "./lookups";
import { prepareImport } from "./preview";
import { runImport } from "./runner";
import type { RunnerRow } from "./runner";
import { fileWithRows, makeProductEntity, makeProductUpdate, productColumns, productLookups, storedProduct } from "./testkit";
import type { ProductItem } from "./testkit";
import { RowProblem } from "./types";
import type { ExtraStep, ImportColumn, ImportContext, UpdateField, UpdateSpec } from "./types";
import { readWorkbook } from "./workbook";

const ctx: ImportContext = { lookups: productLookups, signal: new AbortController().signal };

/** A category that exists on a record but is no longer offered (like a rep who was deactivated). */
const retired = storedProduct(1, { category: "Retired" });
const active = storedProduct(2, { category: "Hardware" });

const categoryColumn = productColumns.find((column) => column.key === "category") as ImportColumn;

const categoryField: UpdateField<ProductItem> = {
  column: "category",
  field: "category_id",
  current: (item, { lookups }) => (item.category === "Retired" ? "cat-old" : (lookups.categories?.resolve(item.category) ?? null)),
  resolve: (value, { lookups }) => {
    if (typeof value !== "string") return null;
    if (value.toLowerCase() === "retired") return "cat-old";
    const lookup = lookups.categories;
    const id = lookup?.resolve(value);
    if (lookup === undefined || id === undefined) {
      throw new RowProblem(unacceptedLookupValue(categoryColumn, value, lookup ?? makeLookup([]), true));
    }
    return id;
  },
  clear: null,
};

function withCategory(extra: Partial<UpdateSpec<ProductItem>> = {}) {
  const base = makeProductUpdate({ items: [retired, active] });
  return makeProductEntity({
    recordIdKey: "id",
    update: {
      ...base,
      fields: [...base.fields, categoryField],
      notUpdatable: (base.notUpdatable ?? []).filter((key) => key !== "category"),
      ...extra,
    },
  });
}

async function preview(rows: readonly (readonly (string | null)[])[], entity = withCategory()) {
  const sheet = await readWorkbook(await fileWithRows(["Id", "SKU *", "Name *", "Category"], rows), entity);
  return prepareImport(entity, sheet, ctx, "f.xlsx", "addAndUpdate", [retired, active]);
}

describe("a lookup column on a row that updates a record", () => {
  it("is plain text: a label the lookup does not hold is not refused, since the record may already have it", async () => {
    const { preview: shown } = await preview([["p-1", null, null, "Retired"]]);
    expect(shown.rows[0]).toMatchObject({ action: "unchanged", error: null });
  });

  it("is judged by the update field's own resolve, which says what it thinks of an unknown label", async () => {
    const { preview: shown } = await preview([["p-2", null, null, "Nowhere"]]);
    expect(shown.rows[0]?.error).toBe("'Nowhere' is not one of the accepted values for Category (see the Lists sheet).");
  });

  it("changes when the label is one the lookup does hold, and it differs from what is stored", async () => {
    const { preview: shown } = await preview([["p-2", null, null, "Fasteners"]]);
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Category"] });
  });

  it("still empties with [clear], without asking the lookup about the token", async () => {
    const { preview: shown } = await preview([["p-1", null, null, "[clear]"]]);
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Category"] });
  });

  it("keeps the ordinary check on a row that creates a record", async () => {
    const { preview: shown } = await preview([[null, "NEW-1", "Washer", "Retired"]]);
    expect(shown.rows[0]?.error).toBe("'Retired' is not one of the accepted values for Category (see the Lists sheet).");
    const created = await preview([[null, "NEW-2", "Washer", "Hardware"]]);
    expect(created.preview.rows[0]?.action).toBe("create");
  });

  it("is checked as before by coerceRow for a row that creates a record, and left as text for one that updates", () => {
    const entity = withCategory();
    const cells = { sku: "A-1", name: "Bracket", category: "Retired" };
    expect(coerceRow(entity, cells, ctx, { kind: "create" }).errors).toHaveLength(1);
    const updating = coerceRow(entity, cells, ctx, { kind: "update", storedCurrency: "INR" });
    expect(updating.errors).toEqual([]);
    expect(updating.row.category).toBe("Retired");
  });
});

describe("the hint for a lookup label that is not accepted", () => {
  const lookup = makeLookup([
    { label: "Asha", value: "a" },
    { label: "Twin", value: "t1" },
    { label: "twin", value: "t2" },
  ]);
  const owner: ImportColumn = {
    key: "owner",
    header: "Owner",
    kind: "text",
    help: "The owner.",
    lookup: "owners",
    lookupHint: "Leave blank to assign it to yourself.",
    lookupHintUpdate: "Leave blank to keep the current owner.",
  };

  it("is the add-mode hint when adding", () => {
    expect(unacceptedLookupValue(owner, "Nobody", lookup, false)).toBe(
      "'Nobody' is not one of the accepted values for Owner (see the Lists sheet). Leave blank to assign it to yourself.",
    );
  });

  it("is the update hint instead, when the row updates a record", () => {
    expect(unacceptedLookupValue(owner, "Nobody", lookup, true)).toBe(
      "'Nobody' is not one of the accepted values for Owner (see the Lists sheet). Leave blank to keep the current owner.",
    );
  });

  it("says a label that names two entries cannot be chosen, with the same hint", () => {
    expect(unacceptedLookupValue(owner, "Twin", lookup, true)).toBe(
      "'Twin' matches more than one of the accepted values for Owner, so it cannot be chosen. Leave blank to keep the current owner.",
    );
  });

  it("falls back to the add-mode hint where the column has no update hint, and to none where it has neither", () => {
    const shared: ImportColumn = { ...owner };
    delete shared.lookupHintUpdate;
    expect(unacceptedLookupValue(shared, "Nobody", lookup, true)).toContain("Leave blank to assign it to yourself.");
    const bare: ImportColumn = { ...shared };
    delete bare.lookupHint;
    expect(unacceptedLookupValue(bare, "Nobody", lookup, true)).toBe(
      "'Nobody' is not one of the accepted values for Owner (see the Lists sheet).",
    );
  });

  it("is what a create row's own message uses, and is unchanged by an update hint", () => {
    const entity = makeProductEntity({
      columns: productColumns.map((column) =>
        column.key === "category" ? { ...column, lookupHint: "Create it first.", lookupHintUpdate: "Ignored when adding." } : column,
      ),
    });
    const { errors } = coerceRow(entity, { sku: "A", name: "B", category: "Nowhere" }, ctx);
    expect(errors).toEqual(["'Nowhere' is not one of the accepted values for Category (see the Lists sheet). Create it first."]);
  });
});

describe("checkImportEntity on lookup columns and their hints", () => {
  it("fails an update field on a lookup column that has no resolve: the label would be sent as the value", () => {
    const noResolve: UpdateField<ProductItem> = { ...categoryField };
    delete noResolve.resolve;
    const base = makeProductUpdate({ items: [] });
    const entity = makeProductEntity({ recordIdKey: "id", update: { ...base, fields: [...base.fields, noResolve] } });
    expect(checkImportEntity(entity, productLookups)).toContain(
      'Update field "category" is on the lookup column "categories" but has no resolve, so the label itself would be compared and sent as the value',
    );
  });

  it("accepts the same field with a resolve", () => {
    expect(checkImportEntity(withCategory(), productLookups)).toEqual([]);
  });

  it("flags a lookupHintUpdate on a column that is not a lookup column", () => {
    const columns = productColumns.map((column) => (column.key === "name" ? { ...column, lookupHintUpdate: "Hint." } : column));
    expect(checkImportEntity(makeProductEntity({ columns }), productLookups)).toContain(
      'Column "name" has a lookupHintUpdate but is not a lookup column',
    );
  });
});

describe("a plan that refuses while it is being made", () => {
  const phoneRefused = (): readonly ExtraStep[] => {
    throw new RowProblem("Phone cannot be emptied: no endpoint can clear a number");
  };

  it("makes the row invalid in the preview with its own sentence, whichever part of the plan refused", async () => {
    const entity = withCategory({ extras: phoneRefused });
    const { preview: shown, prepared } = await preview([["p-2", null, "Nut", null]], entity);
    expect(shown.rows[0]).toMatchObject({
      action: "invalid",
      error: "Phone cannot be emptied: no endpoint can clear a number",
    });
    expect(prepared.valid).toEqual([]);
    expect(prepared.invalid[0]).toMatchObject({ status: "invalid", message: "Phone cannot be emptied: no endpoint can clear a number" });
  });

  it("makes the row invalid in the run too, if the plan refuses only when its turn comes, and sends nothing", async () => {
    const row: RunnerRow = {
      row: 2,
      values: {},
      label: "SKU-2",
      update: {
        plan: () => {
          throw new RowProblem("The record changed and this row no longer applies");
        },
      },
    };
    const result = await runImport({
      entity: withCategory(),
      rows: [row],
      ctx,
      mode: "addAndUpdate",
      onProgress: () => undefined,
      isCancelled: () => false,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(result).toMatchObject({ invalid: 1, updated: 0, failed: 0 });
    expect(result.outcomes[0]).toMatchObject({ status: "invalid", message: "The record changed and this row no longer applies" });
  });
});
