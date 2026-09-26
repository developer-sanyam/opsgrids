import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { InvalidCell } from "./cells";
import { fromExcelValue, readCell } from "./cellValue";
import { coerceValue } from "./coerce";
import { columnOf, fileOf, makeProductEntity, newWorkbook, productLookups } from "./testkit";
import { readWorkbook } from "./workbook";

const target = "https://evil.example/steal?x=1";

/** What the library reports for a cell whose display is not plain text, which its types do not allow. */
function value(shape: unknown): ExcelJS.CellValue {
  return shape as ExcelJS.CellValue;
}

describe("fromExcelValue: a hyperlink is its display text and never its target", () => {
  it("reads a text display", () => {
    expect(fromExcelValue({ text: "Ada Lovelace", hyperlink: target })).toBe("Ada Lovelace");
  });

  it("reads a rich text display as the runs joined", () => {
    const rich = { richText: [{ text: "Ada " }, { text: "Lovelace", font: { bold: true } }] };
    expect(fromExcelValue(value({ text: rich, hyperlink: target }))).toBe("Ada Lovelace");
  });

  it("reads a numeric display as the number, not the link", () => {
    expect(fromExcelValue(value({ text: 12345, hyperlink: target }))).toBe(12345);
  });

  it("reads a formula display as its stored result, and never evaluates it", () => {
    expect(fromExcelValue(value({ text: { formula: 'HYPERLINK("x")', result: "Shown" }, hyperlink: target }))).toBe("Shown");
    expect(fromExcelValue(value({ text: { formula: "1+1", result: 2 }, hyperlink: target }))).toBe(2);
  });

  it("reads an error display as an invalid cell", () => {
    const cell = fromExcelValue(value({ text: { error: "#REF!" }, hyperlink: target }));
    expect(cell).toBeInstanceOf(InvalidCell);
  });

  it("reads a formula with no stored result as an invalid cell rather than the link", () => {
    expect(fromExcelValue(value({ text: { formula: "A1" }, hyperlink: target }))).toBeInstanceOf(InvalidCell);
  });

  it("is blank, not the link, when there is no display at all", () => {
    expect(fromExcelValue(value({ hyperlink: target }))).toBeNull();
    expect(fromExcelValue(value({ text: "", hyperlink: "mailto:evil@example.com" }))).toBe("");
    expect(fromExcelValue(value({ text: undefined, hyperlink: "mailto:evil@example.com" }))).toBeNull();
  });

  it("never lets the target reach a column: a mailto link over the word Ada is Ada", () => {
    const cell = fromExcelValue({ text: "Ada", hyperlink: "mailto:ada@evil.example" });
    expect(cell).toBe("Ada");
    expect(String(cell)).not.toContain("evil");
  });
});

describe("fromExcelValue: everything else", () => {
  it("passes plain values through and treats an empty cell as blank", () => {
    expect(fromExcelValue("a")).toBe("a");
    expect(fromExcelValue(0)).toBe(0);
    expect(fromExcelValue(false)).toBe(false);
    expect(fromExcelValue(null)).toBeNull();
    expect(fromExcelValue(undefined)).toBeNull();
  });

  it("joins rich text and reads a formula's stored result", () => {
    expect(fromExcelValue({ richText: [{ text: "a" }, { text: "b" }] })).toBe("ab");
    expect(fromExcelValue({ formula: "1+1", result: 2 })).toBe(2);
    expect(fromExcelValue({ sharedFormula: "A1", result: "x" })).toBe("x");
  });

  it("makes an error, an error result, an uncalculated formula and an invalid date invalid cells", () => {
    expect(fromExcelValue({ error: "#N/A" })).toBeInstanceOf(InvalidCell);
    expect(fromExcelValue({ formula: "1/0", result: { error: "#DIV/0!" } })).toBeInstanceOf(InvalidCell);
    expect(fromExcelValue({ formula: "1+1" })).toBeInstanceOf(InvalidCell);
    expect(fromExcelValue(new Date(Number.NaN))).toBeInstanceOf(InvalidCell);
  });
});

describe("a hyperlink cell in a workbook file", () => {
  it("imports the display text of a link, not where it goes", async () => {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.addRow(["SKU", "Name"]);
    sheet.addRow(["A-1", { text: "Steel bracket", hyperlink: target }]);
    sheet.addRow(["A-2", { text: { richText: [{ text: "Steel " }, { text: "nut" }] } as unknown as string, hyperlink: target }]);
    const parsed = await readWorkbook(await fileOf(workbook), makeProductEntity());
    const names = parsed.rows.map((row) => row.cells.name);
    expect(names).toHaveLength(2);
    for (const name of names) expect(String(name)).not.toContain("evil.example");
    expect(names[0]).toBe("Steel bracket");
  });
});

describe("a number formatted as zeros keeps its zeros", () => {
  async function cellsWith(format: string | undefined, stored: number) {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.addRow(["SKU", "Name"]);
    const cell = sheet.getCell("A2");
    cell.value = stored;
    if (format !== undefined) cell.numFmt = format;
    sheet.getCell("B2").value = "Bracket";
    const parsed = await readWorkbook(await fileOf(workbook), makeProductEntity());
    return parsed.rows[0]?.cells;
  }

  it("reads 123 formatted 00000 as the text it displays, 00123", async () => {
    expect((await cellsWith("00000", 123))?.sku).toBe("00123");
  });

  it("pads to the width of the format, whatever it is", async () => {
    expect((await cellsWith("0000000000", 42))?.sku).toBe("0000000042");
    expect((await cellsWith("000", 7))?.sku).toBe("007");
  });

  it("leaves a number longer than the format alone, since nothing is padded", async () => {
    expect((await cellsWith("000", 123456))?.sku).toBe("123456");
  });

  it("leaves numbers with any other format as the numbers they are", async () => {
    for (const format of [undefined, "General", "0", "0.00", "#,##0", "00.0", "000-00", "@"]) {
      const cell = (await cellsWith(format, 123))?.sku;
      expect(cell === 123 || cell === "123", String(format)).toBe(true);
    }
    expect((await cellsWith("0.00", 123))?.sku).toBe(123);
    expect((await cellsWith("#,##0", 1234))?.sku).toBe(1234);
  });

  it("does not pad a fraction or a negative number", async () => {
    expect((await cellsWith("00000", 1.5))?.sku).toBe(1.5);
    expect((await cellsWith("00000", -12))?.sku).toBe(-12);
  });

  it("pads zero itself", async () => {
    expect((await cellsWith("00000", 0))?.sku).toBe("00000");
  });

  it("lets the padded text through a text column, so the code arrives as written", async () => {
    const cell = (await cellsWith("00000", 123))?.sku;
    expect(coerceValue(columnOf(makeProductEntity(), "sku"), cell, { lookups: productLookups })).toEqual({
      ok: true,
      value: "00123",
    });
  });

  it("still reads as a number in a numeric column: the zeros are only formatting there", async () => {
    const workbook = await newWorkbook();
    const sheet = workbook.addWorksheet("Import");
    sheet.addRow(["SKU", "Name", "Quantity"]);
    sheet.getCell("A2").value = "A-1";
    sheet.getCell("B2").value = "Bracket";
    const quantity = sheet.getCell("C2");
    quantity.value = 12;
    quantity.numFmt = "0000";
    const parsed = await readWorkbook(await fileOf(workbook), makeProductEntity());
    const cell = parsed.rows[0]?.cells.quantity;
    expect(coerceValue(columnOf(makeProductEntity(), "quantity"), cell, { lookups: productLookups })).toEqual({
      ok: true,
      value: 12,
    });
  });

  it("applies to the cell reader itself, whatever produced the number", () => {
    const cell = { value: 5, numFmt: "000" } as unknown as ExcelJS.Cell;
    expect(readCell(cell)).toBe("005");
    expect(readCell({ value: 5, numFmt: undefined } as unknown as ExcelJS.Cell)).toBe(5);
    expect(readCell({ value: "5", numFmt: "000" } as unknown as ExcelJS.Cell)).toBe("5");
  });
});
