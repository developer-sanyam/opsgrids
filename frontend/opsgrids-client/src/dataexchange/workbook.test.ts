import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { InvalidCell } from "./cells";
import { coerceValue } from "./coerce";
import { columnOf, fileOf, fileWithRows, makeProductEntity, newWorkbook, productLookups } from "./testkit";
import { WorkbookReadError, readWorkbook } from "./workbook";

const entity = makeProductEntity();

async function readFailure(file: File, target = entity): Promise<string> {
  try {
    await readWorkbook(file, target);
  } catch (error) {
    expect(error).toBeInstanceOf(WorkbookReadError);
    return error instanceof Error ? error.message : "";
  }
  throw new Error("expected the file to be refused");
}

describe("readWorkbook: the file", () => {
  it("refuses .csv and .xls with the way out", async () => {
    for (const name of ["contacts.csv", "contacts.xls", "CONTACTS.XLS"]) {
      const message = await readFailure(new File(["a,b"], name));
      expect(message, name).toContain("Open it in Excel and choose Save As → Excel Workbook (.xlsx)");
      expect(message, name).toContain(name);
    }
  });

  it("refuses any other extension the same way", async () => {
    expect(await readFailure(new File(["x"], "contacts.numbers"))).toContain("Save As → Excel Workbook (.xlsx)");
    expect(await readFailure(new File(["x"], "contacts"))).toContain("is not an .xlsx file");
    expect(await readFailure(new File(["x"], "contacts.xlsx.exe"))).toContain("is not an .xlsx file");
  });

  it("accepts the extension in any case", async () => {
    const file = await fileWithRows(["SKU", "Name"], [["A-1", "Bracket"]], "IMPORT.XLSX");
    expect((await readWorkbook(file, entity)).rows).toHaveLength(1);
  });

  it("refuses a file over 5 MB and says how big it is", async () => {
    const big = new File([new Uint8Array(5 * 1024 * 1024 + 1)], "big.xlsx");
    const message = await readFailure(big);
    expect(message).toContain("5.0 MB");
    expect(message).toContain("limit is 5 MB");
  });

  it("accepts a file of exactly 5 MB as far as the size check goes", async () => {
    const edge = new File([new Uint8Array(5 * 1024 * 1024)], "edge.xlsx");
    expect(await readFailure(edge)).toContain("could not be read as an Excel workbook");
  });

  it("refuses an empty file, and one that is not a workbook", async () => {
    expect(await readFailure(new File([], "empty.xlsx"))).toContain("is empty");
    expect(await readFailure(new File(["not a zip"], "fake.xlsx"))).toContain("could not be read as an Excel workbook");
  });
});

describe("readWorkbook: sheets", () => {
  it("prefers the sheet named Import, in any case, over the order of the sheets", async () => {
    const workbook = await newWorkbook();
    workbook.addWorksheet("Notes").addRow(["Something", "else"]);
    const sheet = workbook.addWorksheet("  import ");
    sheet.addRow(["SKU", "Name"]);
    sheet.addRow(["A-1", "Bracket"]);
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    expect(parsed.sheetName).toBe("  import ");
    expect(parsed.rows).toHaveLength(1);
  });

  it("falls back to the first visible sheet that is not Instructions or Lists", async () => {
    const workbook = await newWorkbook();
    workbook.addWorksheet("Instructions").addRow(["How to"]);
    workbook.addWorksheet("Lists").addRow(["Category"]);
    const sheet = workbook.addWorksheet("Sheet1");
    sheet.addRow(["SKU", "Name"]);
    sheet.addRow(["A-1", "Bracket"]);
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    expect(parsed.sheetName).toBe("Sheet1");
  });

  it("never reads a hidden sheet", async () => {
    const workbook = await newWorkbook();
    const hidden = workbook.addWorksheet("Secret");
    hidden.state = "hidden";
    hidden.addRow(["SKU", "Name"]);
    hidden.addRow(["HIDDEN", "Should not be imported"]);
    const visible = workbook.addWorksheet("Visible");
    visible.addRow(["SKU", "Name"]);
    visible.addRow(["SHOWN", "Imported"]);
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    expect(parsed.sheetName).toBe("Visible");
    expect(parsed.rows.map((row) => row.cells.sku)).toEqual(["SHOWN"]);
  });

  it("refuses a workbook with nothing but hidden or helper sheets", async () => {
    const workbook = await newWorkbook();
    workbook.addWorksheet("Instructions").addRow(["x"]);
    const hidden = workbook.addWorksheet("Import");
    hidden.state = "veryHidden";
    hidden.addRow(["SKU", "Name"]);
    expect(await readFailure(await fileOf(workbook))).toContain("no sheet to import");
  });
});

describe("readWorkbook: headers", () => {
  it("matches ignoring case, spacing and a trailing asterisk", async () => {
    const file = await fileWithRows(["sku *", " NAME*", "Weight(kg)", "supplier  EMAIL"], [["A-1", "Bracket", 1, "a@b.co"]]);
    const parsed = await readWorkbook(file, entity);
    expect(parsed.columns.map((c) => c.key)).toEqual(["sku", "name", "weight", "email"]);
    expect(parsed.rows[0]?.cells).toEqual({ sku: "A-1", name: "Bracket", weight: 1, email: "a@b.co" });
    expect(parsed.unknownHeaders).toEqual([]);
  });

  it("reports unknown headers, ignores them, and stays silent about read-only ones", async () => {
    const file = await fileWithRows(["SKU", "Name", "Favourite colour", "Record ID", "Created"], [["A-1", "Bracket", "blue", "id-9", "2026-01-01"]]);
    const parsed = await readWorkbook(file, entity);
    expect(parsed.unknownHeaders).toEqual(["Favourite colour"]);
    expect(parsed.columns.map((c) => c.key)).toEqual(["sku", "name"]);
    expect(parsed.rows[0]?.cells).toEqual({ sku: "A-1", name: "Bracket" });
  });

  it("names the required headers the file lacks", async () => {
    const file = await fileWithRows(["Name", "Price"], [["Bracket", "10"]]);
    const parsed = await readWorkbook(file, entity);
    expect(parsed.missingRequired).toEqual(["SKU"]);
  });

  it("refuses two columns with the same header", async () => {
    const message = await readFailure(await fileWithRows(["SKU", "sku *", "Name"], [["A", "B", "C"]]));
    expect(message).toContain('Two columns are headed "sku *"');
  });

  it("refuses a sheet whose headers match nothing", async () => {
    const message = await readFailure(await fileWithRows(["Foo", "Bar"], [["1", "2"]]));
    expect(message).toContain("None of the headers");
  });

  it("refuses a sheet with an empty first row", async () => {
    const workbook = await newWorkbook();
    workbook.addWorksheet("Import").getRow(3).values = ["SKU", "Name"];
    expect(await readFailure(await fileOf(workbook))).toContain("first row of the sheet must hold the column headers");
  });

  it("refuses more than 60 columns", async () => {
    const headers = Array.from({ length: 61 }, (_, i) => (i === 0 ? "SKU" : `Extra ${i}`));
    const message = await readFailure(await fileWithRows(headers, [["A"]]));
    expect(message).toContain("61 columns");
    expect(message).toContain("limit is 60");
  });

  it("accepts exactly 60 columns", async () => {
    const headers = Array.from({ length: 60 }, (_, i) => (i === 0 ? "SKU" : `Extra ${i}`));
    const parsed = await readWorkbook(await fileWithRows(headers, [["A"]]), entity);
    expect(parsed.unknownHeaders).toHaveLength(59);
  });
});

describe("readWorkbook: rows", () => {
  it("skips blank rows but keeps the sheet's own row numbers", async () => {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.getRow(1).values = ["SKU", "Name"];
    sheet.getRow(2).values = ["A-1", "First"];
    sheet.getRow(4).values = ["A-2", "Third"];
    sheet.getRow(5).values = ["   ", ""];
    sheet.getRow(7).values = ["A-3", "Fifth"];
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    expect(parsed.rows.map((row) => row.row)).toEqual([2, 4, 7]);
  });

  it("ignores a row that only has values in columns it does not know", async () => {
    const file = await fileWithRows(["SKU", "Name", "Comment"], [["A-1", "First", ""], [null, null, "just a note"]]);
    expect((await readWorkbook(file, entity)).rows.map((row) => row.row)).toEqual([2]);
  });

  it("refuses a file with more rows than the entity allows, and counts them", async () => {
    const small = makeProductEntity({ maxRows: 3 });
    const rows = [["A-1"], ["A-2"], ["A-3"], ["A-4"], ["A-5"]];
    const message = await readFailure(await fileWithRows(["SKU"], rows), small);
    expect(message).toContain("The file has 5 rows");
    expect(message).toContain("limit is 3 per file");
  });

  it("accepts exactly the limit", async () => {
    const small = makeProductEntity({ maxRows: 3 });
    const parsed = await readWorkbook(await fileWithRows(["SKU"], [["A-1"], ["A-2"], ["A-3"]]), small);
    expect(parsed.rows).toHaveLength(3);
  });

  it("uses 5000 rows when the entity names no limit", async () => {
    const rows = Array.from({ length: 5001 }, (_, i) => [`A-${i}`]);
    expect(await readFailure(await fileWithRows(["SKU"], rows))).toContain("The file has 5001 rows");
  });

  it("refuses a sheet with a header and nothing under it", async () => {
    expect(await readFailure(await fileWithRows(["SKU", "Name"], []))).toContain("no rows to import");
  });
});

describe("readWorkbook: cells", () => {
  async function cellsOf(...values: ExcelJS.CellValue[]): Promise<Record<string, unknown>> {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.getRow(1).values = ["SKU", "Name", "Quantity", "Weight (kg)", "Launch date", "Active", "Category"];
    sheet.getRow(2).values = values;
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    return { ...(parsed.rows[0]?.cells ?? {}) };
  }

  it("reads strings, numbers, booleans and dates as they are", async () => {
    const launched = new Date(Date.UTC(2026, 2, 4));
    const cells = await cellsOf("A-1", "Bracket", 12, 0.5, launched, true);
    expect(cells).toEqual({
      sku: "A-1",
      name: "Bracket",
      quantity: 12,
      weight: 0.5,
      launched,
      active: true,
      category: null,
    });
  });

  it("joins the runs of a rich text cell", async () => {
    const cells = await cellsOf("A-1", { richText: [{ text: "Steel " }, { text: "bracket", font: { bold: true } }] });
    expect(cells.name).toBe("Steel bracket");
  });

  it("reads a hyperlink cell as its text", async () => {
    const cells = await cellsOf("A-1", { text: "Steel bracket", hyperlink: "https://example.com/item" });
    expect(cells.name).toBe("Steel bracket");
  });

  it("takes a formula's stored result and never evaluates it", async () => {
    const cells = await cellsOf(
      "A-1",
      { formula: 'HYPERLINK("https://evil.example","click")', result: "Stored text" },
      { formula: "SUM(1,2)", result: 3 },
    );
    expect(cells.name).toBe("Stored text");
    expect(cells.quantity).toBe(3);
  });

  it("does not calculate a formula that has no stored result: it is an invalid cell", async () => {
    const cells = await cellsOf("A-1", "Bracket", { formula: "1+1" });
    expect(cells.quantity).toBeInstanceOf(InvalidCell);
    const result = coerceValue(columnOf(entity, "quantity"), cells.quantity as InvalidCell, {
      lookups: productLookups,
    });
    expect(result.ok).toBe(false);
  });

  it("makes an error result an invalid cell", async () => {
    const cells = await cellsOf("A-1", "Bracket", { formula: "1/0", result: { error: "#DIV/0!" } });
    expect(cells.quantity).toBeInstanceOf(InvalidCell);
    expect((cells.quantity as InvalidCell).reason).toBe("contains the error #DIV/0!");
  });

  it("makes an error cell an invalid cell", async () => {
    const cells = await cellsOf("A-1", "Bracket", { error: "#N/A" });
    expect(cells.quantity).toBeInstanceOf(InvalidCell);
    expect((cells.quantity as InvalidCell).shown).toBe("#N/A");
  });

  it("keeps text that looks like a formula as text", async () => {
    const cells = await cellsOf("=SUM(A1:A9)", '=HYPERLINK("x")');
    expect(cells.sku).toBe("=SUM(A1:A9)");
    expect(cells.name).toBe('=HYPERLINK("x")');
  });

  it("reads an Excel date serial as a UTC date that coerces to the same calendar day", async () => {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.getRow(1).values = ["SKU", "Launch date"];
    const cell = sheet.getCell(2, 1);
    cell.value = "A-1";
    const date = sheet.getCell(2, 2);
    date.value = new Date(Date.UTC(2026, 2, 4));
    date.numFmt = "dd/mm/yyyy";
    const parsed = await readWorkbook(await fileOf(workbook), entity);
    const launched = parsed.rows[0]?.cells.launched;
    expect(launched).toBeInstanceOf(Date);
    expect(coerceValue(columnOf(entity, "launched"), launched, { lookups: productLookups })).toEqual({
      ok: true,
      value: "2026-03-04",
    });
  });
});
