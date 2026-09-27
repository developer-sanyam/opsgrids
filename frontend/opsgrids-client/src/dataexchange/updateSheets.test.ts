import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { checkImportEntity } from "./checkEntity";
import { buildReportWorkbook } from "./report";
import { buildSampleWorkbook } from "./template";
import { loadBlob, makeProductEntity, makeProductUpdate, productColumns, productLookups, storedProduct } from "./testkit";
import type { ImportColumn, ImportSummary, UpdateSpec } from "./types";
import type { ProductItem } from "./testkit";

const items = [storedProduct(1)];
const updatable = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });

async function instructionLines(target: typeof updatable): Promise<string[]> {
  const workbook = await loadBlob(await buildSampleWorkbook(target, productLookups));
  const lines: string[] = [];
  workbook.getWorksheet("Instructions")?.eachRow((row) => row.eachCell({ includeEmpty: false }, (cell) => lines.push(cell.text)));
  return lines;
}

describe("the Instructions sheet of a list that can be updated", () => {
  it("explains how a row finds its record, by Record ID first and then by the key", async () => {
    const lines = await instructionLines(updatable);
    expect(lines).toContain("Updating existing products");
    expect(lines).toContain('Choose "Add new and update existing" in the import dialog to use these rules.');
    expect(lines).toContain(
      "A row is matched to an existing record by its Record ID (the column an export writes). A row without one is matched by SKU in its exact spelling (capitals and spaces count; a SKU that differs from an existing one only in capitals or spaces is refused, not matched). A row that matches nothing is added as a new record. If two records have the same SKU, the row is refused.",
    );
    expect(lines.join("\n")).not.toContain("ignoring capitals");
  });

  it("says a caseless key is matched ignoring capitals, and only then", async () => {
    const caseless = makeProductEntity({
      recordIdKey: "id",
      update: { ...makeProductUpdate({ items }), keyMatch: "caseless" },
    });
    const lines = await instructionLines(caseless);
    expect(lines).toContain(
      "A row is matched to an existing record by its Record ID (the column an export writes). A row without one is matched by SKU, ignoring capitals. A row that matches nothing is added as a new record. If two records have the same SKU, the row is refused.",
    );
    expect(lines.join("\n")).not.toContain("exact spelling");
  });

  it("lists the read-only columns an update ignores, except the Record ID and any it refuses", async () => {
    const lines = await instructionLines(updatable);
    expect(lines).toContain("Ignored when updating: Created.");

    const refusing = makeProductEntity({
      recordIdKey: "id",
      update: { ...makeProductUpdate({ items }), notUpdatable: ["category", "weight", "launched", "phone", "created"] },
    });
    expect((await instructionLines(refusing)).some((line) => line.startsWith("Ignored when updating"))).toBe(false);
  });

  it("says nothing about ignored columns when the list has no read-only column but its Record ID", async () => {
    const columns = productColumns.filter((column) => column.key !== "created");
    const lines = await instructionLines(makeProductEntity({ recordIdKey: "id", columns, update: makeProductUpdate({ items }) }));
    expect(lines.some((line) => line.startsWith("Ignored when updating"))).toBe(false);
  });

  it("says a blank cell leaves the field alone, and that only differing fields are changed", async () => {
    const lines = await instructionLines(updatable);
    expect(lines.some((line) => line.startsWith("A blank cell leaves the field alone."))).toBe(true);
  });

  it("names what [clear] can empty, from the spec's own fields", async () => {
    const lines = await instructionLines(updatable);
    expect(lines).toContain("Type [clear] in a cell to empty a field. Only some fields can be emptied.");
    expect(lines).toContain("Can be emptied with [clear]: Supplier email.");
  });

  it("names what cannot be changed once the record exists, from the spec's own fields", async () => {
    const lines = await instructionLines(updatable);
    expect(lines).toContain(
      "Cannot be changed once created: SKU, Currency. A row whose cell differs from the record is refused.",
    );
  });

  it("says when nothing can be emptied, and leaves out the immutable line when nothing is fixed", async () => {
    const spec = makeProductUpdate({ items });
    const bare: UpdateSpec<ProductItem> = {
      ...spec,
      fields: spec.fields.filter((field) => field.immutable !== true && field.clear === undefined),
    };
    const lines = await instructionLines(makeProductEntity({ recordIdKey: "id", update: bare }));
    expect(lines).toContain("No field can be emptied with [clear].");
    expect(lines.some((line) => line.startsWith("Cannot be changed once created"))).toBe(false);
  });

  it("warns that a file overwrites live data, with no undo and no protection from someone else's edit to a cell you changed", async () => {
    const lines = await instructionLines(updatable);
    const warning = lines.find((line) => line.startsWith("Warning: updating overwrites live data"));
    expect(warning).toContain("cannot be undone");
    expect(warning).toContain("changed a cell you did change after your export: your value replaces theirs");
  });

  it("dates the risk from the export, not from when the file is read", async () => {
    const lines = await instructionLines(updatable);
    const warning = lines.find((line) => line.startsWith("Warning: updating overwrites live data")) ?? "";
    expect(warning).not.toContain("is read");
    expect(warning).toContain("cells you did not change since the export are left alone");
  });

  it("matches by the key alone, or by Record ID alone, when that is all the list has", async () => {
    const keyOnly = await instructionLines(makeProductEntity({ update: makeProductUpdate({ items }) }));
    expect(keyOnly).toContain(
      "A row is matched to an existing record by its SKU in its exact spelling (capitals and spaces count; a SKU that differs from an existing one only in capitals or spaces is refused, not matched). A row that matches nothing is added as a new record. If two records have the same SKU, the row is refused.",
    );
    const idOnly = await instructionLines(
      makeProductEntity({ recordIdKey: "id", update: { ...makeProductUpdate({ items }), keyColumn: undefined } }),
    );
    expect(idOnly).toContain(
      "A row is matched to an existing record by its Record ID (the column an export writes). A row without one is added as a new record.",
    );
  });

  it("keeps the rule about existing records being skipped, now for the add-only choice", async () => {
    const lines = await instructionLines(updatable);
    expect(lines).toContain(
      'With "Add new records only", existing products are skipped, never overwritten. Importing the same file again is safe.',
    );
  });

  it("says nothing about updating for a list that cannot be updated, and keeps its rule as it was", async () => {
    const lines = await instructionLines(makeProductEntity() as typeof updatable);
    expect(lines.some((line) => line.includes("update"))).toBe(false);
    expect(lines).toContain("Existing products are skipped, never overwritten. Importing the same file again is safe.");
  });
});

describe("the result report of an update", () => {
  const summary: ImportSummary = {
    created: 1,
    updated: 1,
    unchanged: 1,
    skipped: 0,
    failed: 1,
    invalid: 0,
    cancelled: false,
    outcomes: [
      { row: 2, status: "updated", message: null, label: "SKU-1", changes: ["Name", "Price"] },
      { row: 3, status: "created", message: null, label: "NEW-1", id: "n-1" },
      { row: 4, status: "unchanged", message: "Every cell already matches the record, so nothing was sent.", label: "SKU-2" },
      { row: 5, status: "failed", message: "A product with that name already exists", label: "SKU-3", changes: ["Name"] },
    ],
  };

  async function sheetOf(data: ImportSummary): Promise<ExcelJS.Worksheet> {
    const workbook = await loadBlob(await buildReportWorkbook(updatable, data));
    const sheet = workbook.getWorksheet("Result");
    if (sheet === undefined) throw new Error("No Result sheet");
    return sheet;
  }

  it("has a Changes column, after Message, when a row updated or tried to", async () => {
    const sheet = await sheetOf(summary);
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual([
      "Row",
      "SKU",
      "Status",
      "Message",
      "Changes",
    ]);
    expect([2, 3, 4, 5].map((r) => sheet.getCell(r, 5).value)).toEqual(["Name, Price", null, null, "Name"]);
  });

  it("names the stored record of every row that names one, in a column after Changes", async () => {
    const named: ImportSummary = {
      ...summary,
      outcomes: summary.outcomes.map((outcome) =>
        outcome.status === "created" ? outcome : { ...outcome, record: `Bracket (${outcome.label})` },
      ),
    };
    const sheet = await sheetOf(named);
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual([
      "Row",
      "SKU",
      "Status",
      "Message",
      "Changes",
      "Existing record",
    ]);
    expect([2, 3, 4, 5].map((r) => sheet.getCell(r, 6).value)).toEqual([
      "Bracket (SKU-1)",
      null,
      "Bracket (SKU-2)",
      "Bracket (SKU-3)",
    ]);
  });

  it("has the Existing record column even when no row changed anything, and no Changes column then", async () => {
    const sheet = await sheetOf({
      ...summary,
      updated: 0,
      failed: 0,
      created: 0,
      outcomes: [{ row: 4, status: "unchanged", message: "Every cell already matches the record, so nothing was sent.", label: "SKU-2", record: "Bracket" }],
    });
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual(["Row", "SKU", "Status", "Message", "Existing record"]);
    expect(sheet.getCell("E2").value).toBe("Bracket");
  });

  it("writes a record label as text, so nothing in it is ever a formula", async () => {
    const sheet = await sheetOf({
      ...summary,
      outcomes: [{ row: 2, status: "updated", message: null, label: "A", changes: ["Name"], record: "=SUM(1)" }],
    });
    expect(sheet.getCell("F2").value).toBe("=SUM(1)");
    expect(sheet.getCell("F2").formula).toBeUndefined();
  });

  it("says Updated and Unchanged in words, beside Created and Failed", async () => {
    const sheet = await sheetOf(summary);
    expect([2, 3, 4, 5].map((r) => sheet.getCell(r, 3).value)).toEqual(["Updated", "Created", "Unchanged", "Failed"]);
    expect(sheet.getCell(4, 4).value).toBe("Every cell already matches the record, so nothing was sent.");
    expect(sheet.getCell(5, 4).value).toBe("A product with that name already exists");
  });

  it("writes a change list as text, so nothing in it is ever a formula", async () => {
    const sheet = await sheetOf({
      ...summary,
      outcomes: [{ row: 2, status: "updated", message: null, label: "A", changes: ["=SUM(1)"] }],
    });
    expect(sheet.getCell("E2").value).toBe("=SUM(1)");
    expect(sheet.getCell("E2").formula).toBeUndefined();
  });

  it("has no Changes column for a run that only added records", async () => {
    const sheet = await sheetOf({ ...summary, updated: 0, unchanged: 0, failed: 0, outcomes: [summary.outcomes[1] as never] });
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual(["Row", "SKU", "Status", "Message"]);
  });
});

describe("checkImportEntity on an update spec", () => {
  const withUpdate = (update: UpdateSpec<ProductItem>) =>
    checkImportEntity(makeProductEntity({ recordIdKey: "id", update }), productLookups);
  const good = makeProductUpdate({ items });
  const withoutKeyOf = (spec: UpdateSpec<ProductItem>): UpdateSpec<ProductItem> => {
    const copy = { ...spec };
    delete copy.keyOf;
    return copy;
  };
  const idColumn = productColumns.find((column) => column.key === "id");

  it("finds nothing wrong with a sound spec", () => {
    expect(withUpdate(good)).toEqual([]);
  });

  it("flags a keyColumn that names no column, or a column an import never reads", () => {
    expect(withUpdate({ ...good, keyColumn: "code" })).toContain('The update\'s keyColumn "code" names no column');
    expect(withUpdate({ ...good, keyColumn: "created" })).toContain(
      'The update\'s keyColumn "created" is read-only, so an import never reads it',
    );
  });

  it("flags a keyColumn with no keyOf, and a keyOf with no keyColumn", () => {
    expect(withUpdate(withoutKeyOf(good))).toContain(
      'The update has a keyColumn ("sku") but no keyOf, so no stored record can be matched by it',
    );
    expect(withUpdate({ ...good, keyColumn: undefined })).toContain("The update has keyOf but no keyColumn, so nothing reads it");
  });

  it("flags an entity with an id column and an update but no recordIdKey", () => {
    expect(checkImportEntity(makeProductEntity({ update: good }), productLookups)).toContain(
      'The entity has an "id" column and an update but no recordIdKey, so a row cannot name its record by Record ID',
    );
    expect(idColumn?.readOnly).toBe(true);
  });

  it("flags an update that has no way to name a record", () => {
    const problems = checkImportEntity(
      makeProductEntity({
        columns: productColumns.filter((column) => column.key !== "id"),
        update: { ...withoutKeyOf(good), keyColumn: undefined },
      }),
      productLookups,
    );
    expect(problems).toContain("The update has neither a recordIdKey nor a keyColumn, so no row can name a record to update");
  });

  it("flags a field for a column that does not exist, or that is read-only", () => {
    expect(withUpdate({ ...good, fields: [...good.fields, { column: "weight2", current: () => null }] })).toContain(
      'Update field "weight2" names no column',
    );
    expect(withUpdate({ ...good, fields: [...good.fields, { column: "created", current: () => null }] })).toContain(
      'Update field "created" is for a read-only column, which an import never reads',
    );
  });

  it("lets an immutable field name the Record ID column: it is an identity, not a change", () => {
    expect(withUpdate({ ...good, fields: [...good.fields, { column: "id", current: (item) => item.id, immutable: true }] })).toEqual([]);
  });

  it("flags two fields for one column", () => {
    expect(withUpdate({ ...good, fields: [...good.fields, { column: "name", current: () => null }] })).toContain(
      'More than one update field is for the column "name"',
    );
  });

  it("flags a field that can be cleared on a required column", () => {
    const fields = good.fields.map((field) => (field.column === "name" ? { ...field, clear: "" as const } : field));
    expect(withUpdate({ ...good, fields })).toContain('Update field "name" can be cleared but the column is required');
    const nullClear = good.fields.map((field) => (field.column === "name" ? { ...field, clear: null } : field));
    expect(withUpdate({ ...good, fields: nullClear })).toContain('Update field "name" can be cleared but the column is required');
  });

  it("flags an immutable field that also says how to clear it", () => {
    const fields = good.fields.map((field) => (field.column === "currency" ? { ...field, clear: "" as const } : field));
    expect(withUpdate({ ...good, fields })).toContain('Update field "currency" is immutable and also says how to clear it');
  });

  it("flags an update with no fields", () => {
    expect(withUpdate({ ...good, fields: [] })).toContain("The update has no fields, so it could change nothing");
  });

  it("flags a key column that is not an update field: a Record ID row's key cell would be ignored", () => {
    const fields = good.fields.filter((field) => field.column !== "sku");
    expect(withUpdate({ ...good, fields })).toEqual([
      'The key column "sku" is not an update field: list it in fields (immutable when the key cannot change) so a row that names its record by Record ID has its key cell compared with the record instead of ignored',
    ]);
  });

  it("accepts a key column that is handled by being refused, like any other column", () => {
    const fields = good.fields.filter((field) => field.column !== "sku");
    expect(withUpdate({ ...good, fields, notUpdatable: [...(good.notUpdatable ?? []), "sku"] })).toEqual([]);
  });

  it("flags an import-only column the update does not handle, and accepts one it refuses or reads as unknowable", () => {
    const opening: ImportColumn = { key: "opening", header: "Opening stock", kind: "integer", help: "Units on hand at the start.", importOnly: true };
    const entity = (update: UpdateSpec<ProductItem>) =>
      checkImportEntity(makeProductEntity({ recordIdKey: "id", columns: [...productColumns, opening], update }), productLookups);
    expect(entity(good)).toEqual([
      'The column "opening" is not covered by the update: list it in fields, extraColumns (an extra step handles it) or notUpdatable (a filled cell is refused)',
    ]);
    expect(entity({ ...good, notUpdatable: [...(good.notUpdatable ?? []), "opening"] })).toEqual([]);
    expect(entity({ ...good, fields: [...good.fields, { column: "opening", current: () => undefined, immutable: true }] })).toEqual([]);
  });

  it("flags keyMatch on a list with no key column", () => {
    const keyless = { ...withoutKeyOf(good), keyColumn: undefined, keyMatch: "caseless" as const };
    expect(withUpdate(keyless)).toContain("The update has keyMatch but no keyColumn, so nothing reads it");
  });

  it("accepts a field whose stored value the list never returns, and prints nothing of it", () => {
    const opening = { column: "quantity", current: () => undefined, immutable: true } as const;
    const fields = good.fields.map((field) => (field.column === "quantity" ? opening : field));
    const problems = withUpdate({ ...good, fields });
    expect(problems).toEqual([]);
    expect(problems.join(" ")).not.toContain("undefined");
  });
});
