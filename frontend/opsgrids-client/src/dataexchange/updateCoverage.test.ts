import { describe, expect, it } from "vitest";

import { checkImportEntity } from "./checkEntity";
import { prepareImport } from "./preview";
import { buildSampleWorkbook } from "./template";
import {
  fileWithRows,
  loadBlob,
  makeProductEntity,
  makeProductUpdate,
  productColumns,
  productLookups,
  storedProduct,
} from "./testkit";
import type { ProductItem } from "./testkit";
import type { ImportColumn, UpdateSpec } from "./types";
import { readWorkbook } from "./workbook";

const ctx = { lookups: productLookups, signal: new AbortController().signal };
const items = [storedProduct(1), storedProduct(2)];
const updater = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });

const HEADERS = ["Record ID", "SKU *", "Name *", "Weight (kg)", "Launch date", "Supplier phone"];
type Cell = string | number | null;

async function preview(rows: readonly (readonly Cell[])[]) {
  const sheet = await readWorkbook(await fileWithRows(HEADERS, rows), updater);
  return prepareImport(updater, sheet, ctx, "f.xlsx", "addAndUpdate", items).preview;
}

describe("a filled cell in a column an update does not handle", () => {
  it("refuses a row that updates a record, naming the column and how to fix it", async () => {
    const shown = await preview([["p-1", null, "Bracket", "2.5", null, null]]);
    expect(shown.rows[0]).toMatchObject({
      action: "invalid",
      error: "Weight (kg) cannot be changed by an update: leave the cell blank.",
    });
    expect(shown.valid).toBe(0);
  });

  it("refuses a [clear] there too, since an emptied field would look changed", async () => {
    const shown = await preview([["p-1", null, null, null, "[clear]", null]]);
    expect(shown.rows[0]?.error).toBe("Launch date cannot be changed by an update: leave the cell blank.");
  });

  it("names every such column, and does so for a row matched by its key as well", async () => {
    const shown = await preview([[null, "SKU-2", null, "1", "2026-01-02", "+919876543210"]]);
    expect(shown.rows[0]?.error).toBe(
      "Weight (kg) cannot be changed by an update: leave the cell blank; Launch date cannot be changed by an update: leave the cell blank; Supplier phone cannot be changed by an update: leave the cell blank",
    );
  });

  it("says so before it reads the cell: a bad value there is not what the row is refused for", async () => {
    const shown = await preview([["p-1", null, null, "heavy", null, null]]);
    expect(shown.rows[0]?.error).toBe("Weight (kg) cannot be changed by an update: leave the cell blank.");
  });

  it("lets a blank cell through, so the same file works once the cell is emptied", async () => {
    const shown = await preview([["p-1", null, "Bracket", "", "  ", null]]);
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Name"] });
  });

  it("does not stop a row that creates a record: there the column is written as ever", async () => {
    const shown = await preview([[null, "NEW-1", "Washer", "0.5", "2026-03-04", "+919876543210"]]);
    expect(shown.rows[0]).toMatchObject({ action: "create", error: null });
    expect(shown.create).toBe(1);
  });

  it("does not let a refused row claim its record", async () => {
    const shown = await preview([
      ["p-1", null, null, "2", null, null],
      ["p-1", null, "Second", null, null, null],
    ]);
    expect(shown.rows.map((row) => [row.row, row.action])).toEqual([
      [2, "invalid"],
      [3, "update"],
    ]);
  });
});

describe("the sheet's instructions for what an update leaves alone", () => {
  it("names the columns a filled cell is refused in", async () => {
    const workbook = await loadBlob(await buildSampleWorkbook(updater, productLookups));
    const lines: string[] = [];
    workbook.getWorksheet("Instructions")?.eachRow((row) => row.eachCell((cell) => lines.push(cell.text)));
    expect(lines).toContain(
      "Not changed by an update, leave blank: Category, Weight (kg), Launch date, Supplier phone. A row with a value there is refused.",
    );
  });
});

describe("checkImportEntity: every importable column is accounted for by an update", () => {
  const good = makeProductUpdate({ items });
  const notUpdatable = good.notUpdatable ?? [];
  const check = (update: UpdateSpec<ProductItem>, columns: readonly ImportColumn[] = productColumns) =>
    checkImportEntity(makeProductEntity({ recordIdKey: "id", columns, update }), productLookups);
  const without = (spec: UpdateSpec<ProductItem>, key: "notUpdatable" | "extraColumns"): UpdateSpec<ProductItem> => {
    const copy = { ...spec };
    delete copy[key];
    return copy;
  };
  const twice = (key: string) =>
    `The column "${key}" is listed more than once across the update's fields, extraColumns and notUpdatable`;

  it("accepts a spec that covers every column", () => {
    expect(check(good)).toEqual([]);
  });

  it("fails a column that is a field, an extra column or not updatable in none of them", () => {
    const problems = check({ ...good, notUpdatable: notUpdatable.filter((key) => key !== "weight") });
    expect(problems).toEqual([
      'The column "weight" is not covered by the update: list it in fields, extraColumns (an extra step handles it) or notUpdatable (a filled cell is refused)',
    ]);
  });

  it("counts a column handled by an extra step as covered", () => {
    expect(check({ ...without(good, "notUpdatable"), extraColumns: ["category", "weight", "launched", "phone"] })).toEqual([]);
  });

  it("does not ask for a read-only column or the Record ID column: an import never reads them", () => {
    expect(check(good)).toEqual([]);
    expect(productColumns.filter((column) => column.readOnly === true).map((column) => column.key)).toEqual(["id", "created"]);
  });

  it("does ask for the key column and for an import-only column", () => {
    const opening: ImportColumn = { key: "opening", header: "Opening stock", kind: "integer", help: "Units.", importOnly: true };
    expect(check(good, [...productColumns, opening])).toEqual([
      'The column "opening" is not covered by the update: list it in fields, extraColumns (an extra step handles it) or notUpdatable (a filled cell is refused)',
    ]);
    const withoutSkuField: UpdateSpec<ProductItem> = { ...good, fields: good.fields.filter((field) => field.column !== "sku") };
    expect(check(withoutSkuField)).toEqual([
      'The key column "sku" is not an update field: list it in fields (immutable when the key cannot change) so a row that names its record by Record ID has its key cell compared with the record instead of ignored',
    ]);
  });

  it("fails a column listed in two places", () => {
    expect(check({ ...good, notUpdatable: [...notUpdatable, "name"] })).toContain(twice("name"));
    expect(check({ ...good, extraColumns: ["weight"] })).toContain(twice("weight"));
    expect(check({ ...good, notUpdatable: [...notUpdatable, "weight"] })).toContain(twice("weight"));
  });

  it("fails a name in notUpdatable or extraColumns that is not a column", () => {
    expect(check({ ...good, notUpdatable: [...notUpdatable, "colour"] })).toContain(
      'The update\'s notUpdatable names "colour", which is no column',
    );
    expect(check({ ...good, extraColumns: ["colour"] })).toContain('The update\'s extraColumns names "colour", which is no column');
  });
});
