import { describe, expect, it } from "vitest";

import { prepareImport, RECORD_ID_MESSAGE, RECORD_ID_UPDATE_HINT, UNKNOWN_RECORD_ID_MESSAGE } from "./preview";
import { fileWithRows, makeProductEntity, makeProductUpdate, productLookups, productSamples, storedProduct } from "./testkit";
import type { ProductItem } from "./testkit";
import { RowProblem } from "./types";
import type { ImportMode } from "./types";
import { readWorkbook } from "./workbook";

const ctx = { lookups: productLookups, signal: new AbortController().signal };

const HEADERS = ["Id", "SKU *", "Name *", "Price", "Currency", "Quantity", "Supplier email"];
type Cell = string | number | null;

const stored: ProductItem[] = [
  storedProduct(1),
  storedProduct(2, { currency: "JPY", priceMinor: 500 }),
  storedProduct(3, { email: "" }),
  storedProduct(4, { sku: "twin-1" }),
  storedProduct(5, { sku: "TWIN-1" }),
];

const updater = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items: stored }) });

async function preview(
  rows: readonly (readonly Cell[])[],
  options: { entity?: typeof updater; headers?: readonly string[]; mode?: ImportMode } = {},
) {
  const target = options.entity ?? updater;
  const sheet = await readWorkbook(await fileWithRows(options.headers ?? HEADERS, rows), target);
  return { sheet, ...prepareImport(target, sheet, ctx, "file.xlsx", options.mode ?? "addAndUpdate", stored) };
}

describe("update mode: finding the record a row names", () => {
  it("updates the record whose Record ID the row carries, and says which columns change", async () => {
    const { preview: shown, prepared } = await preview([["p-1", "SKU-1", "Bracket", "12.50", null, 7, null]]);
    expect(shown).toMatchObject({ mode: "addAndUpdate", total: 1, valid: 1, invalid: 0, create: 0, update: 1, unchanged: 0 });
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Name", "Price", "Quantity"], error: null });
    expect(prepared.valid).toHaveLength(1);
    expect(prepared.valid[0]?.update).toBeDefined();
  });

  it("finds the record by the list's unique key when the row has no Record ID, ignoring case and spacing", async () => {
    const { preview: shown } = await preview([[null, "  sku-1 ", "Bracket", null, null, null, null]]);
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Name"] });
  });

  it("prefers the Record ID to the key, and refuses a key that is another record's", async () => {
    const { preview: shown } = await preview([["p-1", "SKU-2", null, null, null, null, null]]);
    expect(shown.rows[0]?.action).toBe("invalid");
    expect(shown.rows[0]?.error).toBe("SKU cannot be changed once the record exists (it is 'SKU-1'). Leave the cell as it is, or blank.");
  });

  it("refuses a Record ID that no record has, with what to check", async () => {
    const { preview: shown, prepared } = await preview([["p-404", "SKU-1", "Bracket", null, null, null, null]]);
    expect(shown.rows[0]).toMatchObject({ action: "invalid", error: UNKNOWN_RECORD_ID_MESSAGE });
    expect(UNKNOWN_RECORD_ID_MESSAGE).toBe(
      "That Record ID is not in this list: the record may have been deleted, or the file came from another organisation.",
    );
    expect(prepared.valid).toEqual([]);
    expect(prepared.invalid).toEqual([{ row: 2, status: "invalid", message: UNKNOWN_RECORD_ID_MESSAGE, label: "SKU-1" }]);
  });

  it("refuses a key that two records share, naming the column, instead of guessing", async () => {
    const { preview: shown } = await preview([[null, "TWIN-1", "Bracket", null, null, null, null]]);
    expect(shown.rows[0]).toMatchObject({
      action: "invalid",
      error: "More than one record has that SKU, so the row cannot say which to update.",
    });
  });

  it("adds a record when the key matches nothing", async () => {
    const { preview: shown, prepared } = await preview([[null, "NEW-1", "Washer", null, null, null, null]]);
    expect(shown).toMatchObject({ create: 1, update: 0, valid: 1 });
    expect(shown.rows[0]).toMatchObject({ action: "create", changes: [] });
    expect(prepared.valid[0]?.update).toBeUndefined();
  });

  it("refuses a second row for the same record: two rows would fight over it", async () => {
    const { preview: shown, prepared } = await preview([
      [null, "SKU-1", "First", null, null, null, null],
      ["p-1", null, "Second", null, null, null, null],
      [null, "SKU-2", "Other", null, null, null, null],
    ]);
    expect(shown).toMatchObject({ update: 2, invalid: 1 });
    expect(shown.rows[0]).toMatchObject({ row: 3, action: "invalid" });
    expect(shown.rows[0]?.error).toBe("This row updates the same record as row 2. Keep only one of the two rows.");
    expect(prepared.valid.map((row) => row.row)).toEqual([2, 4]);
  });

  it("does not let a row that was refused claim the record", async () => {
    const { preview: shown } = await preview([
      [null, "SKU-1", null, null, null, "many", null],
      [null, "SKU-1", "Second", null, null, null, null],
    ]);
    expect(shown.rows.map((row) => [row.row, row.action])).toEqual([
      [2, "invalid"],
      [3, "update"],
    ]);
  });

  it("recognises an unchanged sample row before it looks for a record", async () => {
    const sample = productSamples[0];
    const { preview: shown } = await preview([[null, String(sample?.sku), String(sample?.name)]], {
      headers: ["Id", "SKU *", "Name *"],
    });
    expect(shown).toMatchObject({ sample: 1, create: 0, update: 0 });
    expect(shown.rows[0]?.action).toBe("sample");
  });

  it("uses the stored key as the row's label when the sheet has no such column", async () => {
    const { prepared } = await preview([["p-1", null, "Bracket"]], { headers: ["Id", "SKU *", "Name *"] });
    expect(prepared.valid[0]?.label).toBe("SKU-1");
  });
});

describe("update mode: what a row does to the record", () => {
  it("makes no request for a row that already matches its record", async () => {
    const { preview: shown, prepared } = await preview([
      ["p-1", "SKU-1", "Product 1", "10", "INR", 1, "orders@example.com"],
    ]);
    expect(shown).toMatchObject({ unchanged: 1, valid: 0, update: 0 });
    expect(shown.rows[0]).toMatchObject({ action: "unchanged", error: null, changes: [] });
    expect(prepared.valid).toEqual([]);
    expect(prepared.unchanged).toEqual([
      { row: 2, status: "unchanged", message: "Every cell already matches the record, so nothing was sent.", label: "SKU-1" },
    ]);
  });

  it("leaves a field alone when its cell is blank, so a sheet of a few columns cannot wipe anything", async () => {
    const { preview: shown } = await preview([["p-1", null, null, null, null, null, null]]);
    expect(shown.rows[0]?.action).toBe("unchanged");
  });

  it("does not ask an update row for the cells a new row would need", async () => {
    const { preview: shown } = await preview([
      ["p-1", "", "", "20", "", "", ""],
      [null, "NEW-1", "", "20", "", "", ""],
    ]);
    expect(shown.rows.find((row) => row.row === 2)?.action).toBe("update");
    expect(shown.rows.find((row) => row.row === 3)).toMatchObject({ action: "invalid", error: "Name is required to add a new record" });
  });

  it("empties a field only where the cell says [clear], and only if it is not already empty", async () => {
    const { preview: shown } = await preview([
      ["p-1", null, null, null, null, null, "[clear]"],
      ["p-3", null, null, null, null, null, "[clear]"],
    ]);
    expect(shown.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Supplier email"] });
    expect(shown.rows.find((row) => row.row === 3)?.action).toBe("unchanged");
  });

  it("refuses [clear] on a field that cannot be emptied, and on a new record", async () => {
    const { preview: shown } = await preview([
      ["p-1", null, "[clear]", null, null, null, null],
      [null, "NEW-1", "Washer", null, null, null, "[clear]"],
    ]);
    expect(shown.rows.map((row) => row.error)).toEqual([
      "Name cannot be cleared",
      "[clear] only works when updating existing records",
    ]);
  });

  it("converts a price in the record's own currency when the row names none: a yen price is not re-priced as rupees", async () => {
    const { preview: shown, prepared } = await preview([["p-2", null, null, "600", null, null, null]]);
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Price"] });
    expect(prepared.valid[0]?.values).toMatchObject({ price: 600, currency: null });
  });

  it("keeps a blank currency out of the change: it is never defaulted to INR on an update", async () => {
    const { preview: shown } = await preview([["p-2", null, null, "500", null, null, null]]);
    // 500 JPY is what the record already has; INR would have made it 50000 and a change.
    expect(shown.rows[0]?.action).toBe("unchanged");
  });

  it("refuses a currency the record does not have, since the list cannot change it", async () => {
    const { preview: shown } = await preview([["p-2", null, null, "500", "USD", null, null]]);
    expect(shown.rows[0]?.error).toBe(
      "Currency cannot be changed once the record exists (it is 'JPY'). Leave the cell as it is, or blank.",
    );
  });

  it("lets the list refuse a change its own rules forbid", async () => {
    const guarded = makeProductEntity({
      recordIdKey: "id",
      update: makeProductUpdate({
        items: stored,
        check: (_row, _existing, changed) => {
          if (changed.includes("Quantity")) throw new RowProblem("Stock can only change through an adjustment");
        },
      }),
    });
    const { preview: shown } = await preview([["p-1", null, null, null, null, 9, null]], { entity: guarded });
    expect(shown.rows[0]?.error).toBe("Stock can only change through an adjustment");
  });

  it("does not stop a file that lacks a required column: blank means leave alone", async () => {
    const lean = await preview([["p-1", "20"]], { headers: ["Id", "Price"] });
    expect(lean.preview.missingRequired).toEqual([]);
    expect(lean.preview.rows[0]?.action).toBe("update");
    expect(lean.sheet.missingRequired).toEqual(["SKU", "Name"]);

    const adding = await preview([["p-1", "20"]], { headers: ["Id", "Price"], mode: "add" });
    expect(adding.preview.missingRequired).toEqual(["SKU", "Name"]);
  });

  it("accepts a file of only the key column and the columns to change, and updates by the key", async () => {
    const { preview: shown } = await preview(
      [
        ["SKU-1", "20"],
        ["sku-3", "10"],
      ],
      { headers: ["SKU *", "Price"] },
    );
    expect(shown.missingRequired).toEqual([]);
    expect(shown).toMatchObject({ update: 1, unchanged: 1, invalid: 0, matchWarning: null });
  });

  it("says what a row that would ADD a record lacks, and only that row: a matched row needs nothing", async () => {
    const { preview: shown, prepared } = await preview(
      [
        ["SKU-1", "20"],
        ["NEW-1", "30"],
      ],
      { headers: ["SKU *", "Price"] },
    );
    expect(shown.rows.map((row) => [row.row, row.action, row.error])).toEqual([
      [3, "invalid", "Name is required to add a new record"],
      [2, "update", null],
    ]);
    expect(prepared.valid.map((row) => row.row)).toEqual([2]);
  });

  it("says a required key column that is missing entirely needs a value to add a record", async () => {
    const { preview: shown } = await preview([["Bracket"]], { headers: ["Name *"] });
    expect(shown.rows[0]?.error).toBe("SKU is required to add a new record");
  });

  it("warns, and still previews the rows as additions, when the file has neither a Record ID nor the key column", async () => {
    const { preview: shown } = await preview([["Bracket", "20"]], { headers: ["Name *", "Price"] });
    expect(shown.matchWarning).toBe(
      "This file has no Id or SKU column, so it cannot update existing records; every row would be added as new.",
    );
    expect(shown.missingRequired).toEqual([]);
    expect(shown.rows[0]).toMatchObject({ action: "invalid", error: "SKU is required to add a new record" });
    expect(shown).toMatchObject({ create: 0, update: 0 });
  });

  it("names only the column that could exist when the list has just one way to match", async () => {
    const keyOnly = makeProductEntity({ update: makeProductUpdate({ items: stored }) });
    const { preview: shown } = await preview([["Bracket"]], { headers: ["Name *"], entity: keyOnly });
    expect(shown.matchWarning).toBe("This file has no SKU column, so it cannot update existing records; every row would be added as new.");

    const idOnly = makeProductEntity({
      recordIdKey: "id",
      update: { ...makeProductUpdate({ items: stored }), keyColumn: undefined },
    });
    const { preview: byId } = await preview([["Bracket"]], { headers: ["Name *"], entity: idOnly });
    expect(byId.matchWarning).toBe("This file has no Id column, so it cannot update existing records; every row would be added as new.");
  });

  it("does not warn when either column is there", async () => {
    expect((await preview([["p-1", "20"]], { headers: ["Id", "Price"] })).preview.matchWarning).toBeNull();
    expect((await preview([["SKU-1", "20"]], { headers: ["SKU *", "Price"] })).preview.matchWarning).toBeNull();
  });

  it("takes the key cell of a row found by its key as the record's key, so its capitals are not a rename", async () => {
    const byName = makeProductEntity({
      update: {
        ...makeProductUpdate({ items: stored }),
        keyColumn: "name",
        keyOf: (item) => item.name,
      },
    });
    const { preview: shown } = await preview([["product 1", "20"]], { headers: ["Name *", "Price"], entity: byName });
    expect(shown.rows[0]).toMatchObject({ action: "update", changes: ["Price"] });
  });

  it("orders rows that need fixing first, and the counts add up", async () => {
    const { preview: shown } = await preview([
      ["p-1", null, "Bracket", null, null, null, null],
      [null, "NEW-1", "Washer", null, null, null, null],
      ["p-1", null, "Again", null, null, null, null],
      ["p-3", null, null, null, null, null, null],
      ["p-404", null, null, null, null, null, null],
    ]);
    expect(shown.rows.map((row) => row.action)).toEqual(["invalid", "invalid", "update", "create", "unchanged"]);
    expect(shown.create + shown.update + shown.unchanged + shown.invalid + shown.sample).toBe(shown.total);
    expect(shown.valid).toBe(shown.create + shown.update);
  });
});

describe("add mode is as it was", () => {
  it("treats a row with the same key as a stored record as a new one: the server's 409 will skip it", async () => {
    const { preview: shown } = await preview([[null, "SKU-1", "Bracket", null, null, null, null]], { mode: "add" });
    expect(shown).toMatchObject({ mode: "add", create: 1, update: 0, unchanged: 0, valid: 1 });
    expect(shown.rows[0]).toMatchObject({ action: "create", changes: [] });
  });

  it("keeps a missing required column a blocker, and the plain sentence for a blank required cell", async () => {
    const { preview: shown } = await preview([["Bracket", "20"]], { headers: ["Name *", "Price"], mode: "add" });
    expect(shown.missingRequired).toEqual(["SKU"]);
    expect(shown.matchWarning).toBeNull();
    expect(shown.rows[0]?.error).toBe("SKU is required");
  });

  it("still refuses a row with a Record ID, and now says how to update it", async () => {
    const { preview: shown, prepared } = await preview([["p-1", "SKU-1", "Bracket", null, null, null, null]], { mode: "add" });
    const message = `${RECORD_ID_MESSAGE} ${RECORD_ID_UPDATE_HINT}`;
    expect(RECORD_ID_UPDATE_HINT).toBe("Switch to Add new and update existing to update it.");
    expect(message.endsWith("Clear the Record ID cell to add it as a new record. Switch to Add new and update existing to update it.")).toBe(true);
    expect(shown.rows[0]).toMatchObject({ action: "invalid", error: message });
    expect(prepared.invalid).toEqual([{ row: 2, status: "invalid", message, label: "SKU-1" }]);
  });

  it("does not offer a mode that the list does not have", async () => {
    const plain = makeProductEntity({ recordIdKey: "id" });
    const { preview: shown } = await preview([["p-1", "SKU-1", "Bracket", null, null, null, null]], {
      entity: plain,
      mode: "addAndUpdate",
    });
    expect(shown.mode).toBe("add");
    expect(shown.rows[0]?.error).toBe(RECORD_ID_MESSAGE);
  });

  it("refuses [clear] with the explanation, for every list", async () => {
    const { preview: shown } = await preview([[null, "NEW-1", "Washer", null, null, null, "[clear]"]], { mode: "add" });
    expect(shown.rows[0]?.error).toBe("[clear] only works when updating existing records");
  });

  it("reports a plain add as create rows only", async () => {
    const { preview: shown } = await preview(
      [
        [null, "A-1", "Bracket", null, null, null, null],
        [null, "A-2", "Nut", null, null, null, null],
      ],
      { mode: "add" },
    );
    expect(shown).toMatchObject({ create: 2, update: 0, unchanged: 0, valid: 2, invalid: 0 });
  });
});
