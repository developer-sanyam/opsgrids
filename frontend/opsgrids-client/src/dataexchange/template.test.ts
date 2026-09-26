import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { loadExcel, validationsOf } from "./excel";
import { makeLookup } from "./lookups";
import { prepareImport } from "./preview";
import { buildSampleWorkbook, sampleFileName } from "./template";
import { fileOf, loadBlob, makeProductEntity, productColumns, productLookups, productSamples } from "./testkit";
import type { ImportColumn } from "./types";
import { readWorkbook } from "./workbook";

const entity = makeProductEntity();

async function buildAndLoad(target = entity, lookups = productLookups): Promise<ExcelJS.Workbook> {
  return loadBlob(await buildSampleWorkbook(target, lookups));
}

function sheetOf(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`No sheet ${name}`);
  return sheet;
}

/** Every cell of a sheet as text, row by row. */
function textOf(sheet: ExcelJS.Worksheet): string[][] {
  const rows: string[][] = [];
  sheet.eachRow((row) => {
    const cells: string[] = [];
    row.eachCell({ includeEmpty: false }, (cell) => cells.push(cell.text));
    rows.push(cells);
  });
  return rows;
}

/**
 * The validation on a column's first data cell. The file stores one range per column; exceljs
 * expands it to a key per cell when it reads the file back.
 */
function validationFor(sheet: ExcelJS.Worksheet, letter: string): ExcelJS.DataValidation | undefined {
  return validationsOf(sheet).model[`${letter}2`];
}

describe("sampleFileName", () => {
  it("is the stem plus -sample.xlsx", () => {
    expect(sampleFileName(entity)).toBe("products-sample.xlsx");
  });
});

describe("the sample workbook", () => {
  it("has Import, Instructions and Lists, in that order", async () => {
    const workbook = await buildAndLoad();
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Import", "Instructions", "Lists"]);
  });

  it("headed by the importable columns, required ones marked with an asterisk, no read-only ones", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    const headers = textOf(sheet)[0];
    expect(headers).toEqual([
      "SKU *",
      "Name *",
      "Status",
      "Category",
      "Price",
      "Currency",
      "Quantity",
      "Weight (kg)",
      "Launch date",
      "Active",
      "Supplier email",
      "Supplier phone",
    ]);
    expect(headers).not.toContain("Id");
    expect(headers).not.toContain("Created");
  });

  it("freezes the header row and styles it", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    expect(sheet.views[0]).toMatchObject({ state: "frozen", ySplit: 1 });
    expect(sheet.getCell("A1").font?.bold).toBe(true);
    expect(sheet.getCell("A1").fill).toMatchObject({ type: "pattern", pattern: "solid" });
  });

  it("gives every column a width that fits its header", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    expect(sheet.getColumn(1).width).toBeGreaterThanOrEqual(12);
    expect(sheet.getColumn(8).width).toBeGreaterThanOrEqual("Weight (kg)".length);
  });

  it("writes the sample rows with numbers as numbers, dates as dates and booleans as Yes or No", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    expect(sheet.getRow(2).values).toEqual([
      undefined,
      "0012-BLK",
      "Steel bracket",
      "In stock",
      "Hardware",
      1499.5,
      "INR",
      120,
      0.75,
      new Date(Date.UTC(2026, 2, 4)),
      "Yes",
      "orders@example.com",
      "+919876543210",
    ]);
    expect(sheet.getCell("C3").value).toBe("Discontinued");
    expect(sheet.getCell("J3").value).toBe("No");
  });

  it("shows a price with its currency's own decimal places", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    expect(sheet.getCell("E2").numFmt).toBe("#,##0.00");
    expect(sheet.getCell("E3").numFmt).toBe("#,##0");
  });

  it("formats text, phone, enum and email columns as text over the whole data range, so Excel keeps 0012 and +91", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    for (const address of ["A2", "A500", "A1001", "B2", "C2", "D2", "F2", "K2", "L2", "L1001"]) {
      expect(sheet.getCell(address).numFmt, address).toBe("@");
    }
    expect(sheet.getCell("A1002").numFmt).not.toBe("@");
    expect(sheet.getCell("G2").numFmt).not.toBe("@");
    expect(sheet.getCell("I2").numFmt).toBe("yyyy-mm-dd");
  });

  it("sizes the formatted range to the entity's own row limit", async () => {
    const sheet = sheetOf(await buildAndLoad(makeProductEntity({ maxRows: 50 })), "Import");
    expect(sheet.getCell("A51").numFmt).toBe("@");
    expect(sheet.getCell("A52").numFmt).not.toBe("@");
  });

  it("round-trips: the file it writes reads back with its own sample rows recognised", async () => {
    const workbook = await buildAndLoad();
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    expect(parsed.rows).toHaveLength(2);
    expect(parsed.missingRequired).toEqual([]);
    expect(parsed.unknownHeaders).toEqual([]);

    const { preview, prepared } = prepareImport(
      entity,
      parsed,
      { lookups: productLookups, signal: new AbortController().signal },
      "sample.xlsx",
    );
    expect(preview).toMatchObject({ total: 2, sample: 2, valid: 0, invalid: 0 });
    expect(prepared.valid).toEqual([]);
    expect(preview.rows.every((row) => row.sample && row.error === null)).toBe(true);
  });

  it("recognises the sample rows only while they are unchanged", async () => {
    const workbook = await buildAndLoad();
    const sheet = sheetOf(workbook, "Import");
    sheet.getCell("B2").value = "Steel bracket, extended";
    sheet.getRow(4).values = ["NEW-1", "A real product"];
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    const { preview, prepared } = prepareImport(
      entity,
      parsed,
      { lookups: productLookups, signal: new AbortController().signal },
      "edited.xlsx",
    );
    expect(preview).toMatchObject({ total: 3, sample: 1, valid: 2, invalid: 0 });
    expect(prepared.valid.map((row) => row.row)).toEqual([2, 4]);
  });
});

describe("dropdowns", () => {
  it("attaches a list to enum, boolean and lookup columns", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    expect(validationFor(sheet, "C")).toMatchObject({ type: "list", formulae: ['"In stock,Discontinued"'] });
    expect(validationFor(sheet, "D")).toMatchObject({ type: "list", formulae: ['"Hardware,Fasteners,Twin,twin"'] });
    expect(validationFor(sheet, "F")).toMatchObject({ type: "list", formulae: ['"INR,USD,JPY,KWD"'] });
    expect(validationFor(sheet, "J")).toMatchObject({ type: "list", formulae: ['"Yes,No"'] });
  });

  it("covers the same data range as the text format and never blocks a blank", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    const { model } = validationsOf(sheet);
    for (const letter of ["C", "D", "F", "J"]) {
      expect(model[`${letter}2`], letter).toBeDefined();
      expect(model[`${letter}1001`], letter).toBeDefined();
      expect(model[`${letter}1002`], letter).toBeUndefined();
      expect(model[`${letter}1`], letter).toBeUndefined();
    }
    expect(validationFor(sheet, "C")).toMatchObject({ allowBlank: true, showErrorMessage: true });
  });

  it("puts no list on columns that are not enum, boolean or lookup", async () => {
    const sheet = sheetOf(await buildAndLoad(), "Import");
    for (const letter of ["A", "B", "E", "G", "H", "I", "K", "L"]) {
      expect(validationFor(sheet, letter), letter).toBeUndefined();
    }
  });

  it("uses a range on the Lists sheet when the labels are too long to sit inline", async () => {
    const labels = Array.from({ length: 60 }, (_, i) => `Warehouse number ${i + 1}`);
    const lookups = { categories: makeLookup(labels.map((label, i) => ({ label, value: `w${i}` }))) };
    const workbook = await buildAndLoad(makeProductEntity({ sampleRows: [{ sku: "A", name: "B" }] }), lookups);
    expect(validationFor(sheetOf(workbook, "Import"), "D")).toMatchObject({ formulae: ["Lists!$A$2:$A$61"] });
  });

  it("uses a range when a label contains a comma, which an inline list cannot hold", async () => {
    const lookups = { categories: makeLookup([{ label: "Nuts, bolts", value: "c1" }, { label: "Hardware", value: "c2" }]) };
    const workbook = await buildAndLoad(makeProductEntity({ sampleRows: [{ sku: "A", name: "B" }] }), lookups);
    expect(validationFor(sheetOf(workbook, "Import"), "D")).toMatchObject({ formulae: ["Lists!$A$2:$A$3"] });
  });

  it("puts an enum that is too long for an inline list on the Lists sheet, after the lookups", async () => {
    const options = Array.from({ length: 40 }, (_, i) => ({ value: `v${i}`, label: `A rather long option label ${i}` }));
    const columns: ImportColumn[] = [
      { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." },
      { key: "grade", header: "Grade", kind: "enum", help: "The grade.", options },
    ];
    const long = makeProductEntity({ columns, sampleRows: [{ sku: "A", grade: "A rather long option label 3" }] });
    const workbook = await buildAndLoad(long, {});
    expect(validationFor(sheetOf(workbook, "Import"), "B")).toMatchObject({ formulae: ["Lists!$A$2:$A$41"] });
    const lists = sheetOf(workbook, "Lists");
    expect(lists.getCell("A1").value).toBe("Grade");
    expect(lists.getCell("A41").value).toBe("A rather long option label 39");
  });

  it("leaves a lookup with nothing in it without a list rather than block every entry", async () => {
    const workbook = await buildAndLoad(makeProductEntity({ sampleRows: [{ sku: "A", name: "B" }] }), {
      categories: makeLookup([]),
    });
    expect(validationFor(sheetOf(workbook, "Import"), "D")).toBeUndefined();
    expect(sheetOf(workbook, "Lists").getCell("A1").value).toBe("Category");
  });
});

describe("the Instructions sheet", () => {
  it("lists every importable column with whether it is required, its format, values, example and help", async () => {
    const rows = textOf(sheetOf(await buildAndLoad(), "Instructions"));
    const byColumn = new Map(rows.map((row) => [row[0], row]));

    for (const column of entity.columns.filter((c) => c.readOnly !== true)) {
      const row = byColumn.get(column.header);
      expect(row, column.header).toBeDefined();
      expect(row?.[1]).toBe(column.required === true ? "Yes" : "No");
      expect(row).toContain(column.help);
    }
    expect(byColumn.has("Id")).toBe(false);
    expect(byColumn.has("Created")).toBe(false);

    expect(byColumn.get("Status")).toContain("In stock, Discontinued");
    expect(byColumn.get("Active")).toContain("Yes, No");
    expect(byColumn.get("SKU")?.[2]).toBe("Text, up to 20 characters. Letters, digits, . _ -");
    expect(byColumn.get("Quantity")?.[2]).toBe("Whole number, from 0 to 1000000");
    expect(byColumn.get("Price")?.[2]).toContain('currency in the "Currency" column');
    expect(byColumn.get("Launch date")?.[2]).toBe("Date written YYYY-MM-DD, e.g. 2026-03-04");
    expect(byColumn.get("Supplier phone")?.[2]).toContain("+919876543210");
    expect(byColumn.get("Category")?.[3]).toBe('The "Category" list on the Lists sheet');
  });

  it("shows an example from the sample rows", async () => {
    const rows = textOf(sheetOf(await buildAndLoad(), "Instructions"));
    const byColumn = new Map(rows.map((row) => [row[0], row]));
    expect(byColumn.get("SKU")).toContain("0012-BLK");
    expect(byColumn.get("Launch date")).toContain("2026-03-04");
    expect(byColumn.get("Active")).toContain("Yes");
  });

  it("carries the entity's notes and then the general rules", async () => {
    const lines = textOf(sheetOf(await buildAndLoad(), "Instructions")).flat();
    expect(lines).toContain("Notes");
    expect(lines).toContain("A SKU that already exists is skipped.");
    expect(lines).toContain("General rules");
    expect(lines).toContain("Up to 1000 rows per file.");
    expect(lines).toContain("Existing products are skipped, never overwritten. Importing the same file again is safe.");
    expect(lines).toContain(
      "The example rows on the Import sheet are ignored while they are unchanged. Delete them or replace them with your own.",
    );
    expect(lines).toContain("Write dates as YYYY-MM-DD, e.g. 2026-03-04.");
    expect(lines).toContain("Write money in major units (1499.50, not 149950) with the currency in the currency column.");
    expect(lines).toContain("Write phone numbers with the country code, starting with +.");
    expect(lines).toContain("Do not rename the headers on the Import sheet.");
    expect(lines.indexOf("Notes")).toBeLessThan(lines.indexOf("General rules"));
  });

  it("states the entity's own row limit", async () => {
    const lines = textOf(sheetOf(await buildAndLoad(makeProductEntity({ maxRows: 250 })), "Instructions")).flat();
    expect(lines).toContain("Up to 250 rows per file.");
  });

  it("leaves out the rules about kinds of column the entity does not have", async () => {
    const plain: ImportColumn[] = [
      { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." },
      { key: "name", header: "Name", kind: "text", help: "The name." },
    ];
    const lines = textOf(
      sheetOf(await buildAndLoad(makeProductEntity({ columns: plain, sampleRows: [{ sku: "A" }] }), {}), "Instructions"),
    ).flat();
    expect(lines.some((line) => line.includes("YYYY-MM-DD, e.g. 2026-03-04."))).toBe(false);
    expect(lines.some((line) => line.includes("major units"))).toBe(false);
    expect(lines.some((line) => line.includes("country code"))).toBe(false);
  });

  it("writes a note that starts with a formula character as text", async () => {
    const risky = makeProductEntity({});
    const withNote = { ...risky, notes: ['=HYPERLINK("x")'] };
    const sheet = sheetOf(await buildAndLoad(withNote), "Instructions");
    const cells: ExcelJS.Cell[] = [];
    sheet.eachRow((row) => row.eachCell((cell) => cells.push(cell)));
    const note = cells.find((cell) => cell.text === '=HYPERLINK("x")');
    expect(note?.type).toBe((await loadExcel()).ValueType.String);
  });
});

describe("the Lists sheet", () => {
  it("has one column per lookup with the labels in display order", async () => {
    const lists = sheetOf(await buildAndLoad(), "Lists");
    const column = [1, 2, 3, 4, 5].map((row) => lists.getCell(row, 1).value);
    expect(column).toEqual(["Category", "Hardware", "Fasteners", "Twin", "twin"]);
    expect(lists.getCell(1, 2).value).toBeNull();
  });

  it("writes labels as text and styles the header", async () => {
    const lists = sheetOf(await buildAndLoad(), "Lists");
    expect(lists.getCell("A2").numFmt).toBe("@");
    expect(lists.getCell("A1").font?.bold).toBe(true);
  });

  it("shares one column between two columns that use the same lookup, under the first one's header", async () => {
    const columns: ImportColumn[] = [
      { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." },
      { key: "primary", header: "Primary category", kind: "text", help: "Main.", lookup: "categories" },
      { key: "secondary", header: "Secondary category", kind: "text", help: "Other.", lookup: "categories" },
    ];
    const workbook = await buildAndLoad(makeProductEntity({ columns, sampleRows: [{ sku: "A", primary: "Hardware" }] }));
    const lists = sheetOf(workbook, "Lists");
    expect(lists.getCell("A1").value).toBe("Primary category");
    expect(lists.getCell("B1").value).toBeNull();
    const imports = sheetOf(workbook, "Import");
    expect(validationFor(imports, "B")?.formulae).toEqual(validationFor(imports, "C")?.formulae);
  });

  it("is absent when nothing on the entity needs a list", async () => {
    const plain: ImportColumn[] = [{ key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." }];
    const workbook = await buildAndLoad(makeProductEntity({ columns: plain, sampleRows: [{ sku: "A" }] }), {});
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Import", "Instructions"]);
  });
});

describe("examples that name something this organisation lacks", () => {
  const elsewhere = {
    categories: makeLookup([
      { label: "Tools", value: "t" },
      { label: "Paint", value: "p" },
    ]),
  };
  const context = { lookups: elsewhere, signal: new AbortController().signal };

  it("writes the lookup's first label in place of the example, on the sheet and on Instructions", async () => {
    const workbook = await buildAndLoad(entity, elsewhere);
    const imports = sheetOf(workbook, "Import");
    expect(imports.getCell("D2").value).toBe("Tools");
    expect(imports.getCell("D3").value).toBe("Tools");
    const rows = textOf(sheetOf(workbook, "Instructions"));
    expect(rows.find((row) => row[0] === "Category")).toContain("Tools");
    // Everything else about the example row is as shipped.
    expect(imports.getCell("A2").value).toBe("0012-BLK");
    expect(imports.getCell("E2").value).toBe(1499.5);
  });

  it("keeps the example when the organisation has it, in any case", async () => {
    const lookups = { categories: makeLookup([{ label: "hardware", value: "h" }, { label: "Tools", value: "t" }]) };
    const imports = sheetOf(await buildAndLoad(entity, lookups), "Import");
    expect(imports.getCell("D2").value).toBe("Hardware");
  });

  it("keeps the example when the lookup is empty", async () => {
    const imports = sheetOf(await buildAndLoad(entity, { categories: makeLookup([]) }), "Import");
    expect(imports.getCell("D2").value).toBe("Hardware");
  });

  it("reads back as sample rows, none of them wrong, though the organisation has no Hardware", async () => {
    const parsed = await readWorkbook(await fileOf(await buildAndLoad(entity, elsewhere)), entity);
    const { preview } = prepareImport(entity, parsed, context, "sample.xlsx");
    expect(preview).toMatchObject({ total: 2, sample: 2, valid: 0, invalid: 0 });
  });

  it("also reads back as sample rows when the file was built for an organisation that had Hardware", async () => {
    const parsed = await readWorkbook(await fileOf(await buildAndLoad(entity, productLookups)), entity);
    const { preview } = prepareImport(entity, parsed, context, "sample.xlsx");
    expect(preview).toMatchObject({ total: 2, sample: 2, valid: 0, invalid: 0 });
  });

  it("stops being a sample the moment the user changes it, and is then judged against the lists", async () => {
    const workbook = await buildAndLoad(entity, elsewhere);
    sheetOf(workbook, "Import").getCell("D2").value = "Hardware";
    sheetOf(workbook, "Import").getCell("B2").value = "Steel bracket, extended";
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    const { preview } = prepareImport(entity, parsed, context, "edited.xlsx");
    expect(preview).toMatchObject({ total: 2, sample: 1, valid: 0, invalid: 1 });
    expect(preview.rows[0]?.error).toBe("'Hardware' is not in the list of Categories (see the Lists sheet)");
  });
});

describe("import-only and read-only columns", () => {
  const opening: ImportColumn = {
    key: "opening",
    header: "Opening stock",
    kind: "integer",
    help: "Units on hand when the record is created.",
    importOnly: true,
    min: 0,
  };
  const withOpening = makeProductEntity({
    columns: [...productColumns, opening],
    sampleRows: [{ ...productSamples[0], opening: 40 }],
  });

  it("puts an import-only column on the sample sheet and on Instructions, and no read-only one", async () => {
    const workbook = await buildAndLoad(withOpening);
    const headers = textOf(sheetOf(workbook, "Import"))[0];
    expect(headers).toContain("Opening stock");
    expect(headers).not.toContain("Id");
    expect(headers).not.toContain("Created");
    expect(sheetOf(workbook, "Import").getCell("M2").value).toBe(40);
    const rows = textOf(sheetOf(workbook, "Instructions"));
    expect(rows.find((row) => row[0] === "Opening stock")).toBeDefined();
  });

  it("reads an import-only column when importing", async () => {
    const workbook = await buildAndLoad(withOpening);
    const parsed = await readWorkbook(await fileOf(workbook), withOpening);
    expect(parsed.columns.map((column) => column.key)).toContain("opening");
    expect(parsed.rows[0]?.cells.opening).toBe(40);
  });
});
