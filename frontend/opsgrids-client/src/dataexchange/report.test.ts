import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { buildReportWorkbook, reportFileName } from "./report";
import { loadBlob, makeProductEntity } from "./testkit";
import type { ImportSummary } from "./types";

const entity = makeProductEntity();

const summary: ImportSummary = {
  created: 1,
  updated: 0,
  unchanged: 0,
  skipped: 1,
  failed: 1,
  invalid: 1,
  cancelled: false,
  outcomes: [
    { row: 7, status: "failed", message: "Forbidden (sku: not yours)", label: "SKU-7" },
    { row: 3, status: "created", message: null, id: "new-1", label: "SKU-3" },
    { row: 9, status: "invalid", message: "Name is required", label: "" },
    { row: 5, status: "skipped", message: "A product with this SKU already exists", label: "=SUM(1)" },
  ],
};

async function resultSheet(target = entity, data = summary): Promise<ExcelJS.Worksheet> {
  const workbook = await loadBlob(await buildReportWorkbook(target, data));
  const sheet = workbook.getWorksheet("Result");
  if (sheet === undefined) throw new Error("No Result sheet");
  return sheet;
}

describe("reportFileName", () => {
  it("is the stem plus -import-result.xlsx", () => {
    expect(reportFileName(entity)).toBe("products-import-result.xlsx");
  });
});

describe("the report workbook", () => {
  it("is one sheet, Result, headed Row, the first required column, Status and Message", async () => {
    const sheet = await resultSheet();
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual(["Row", "SKU", "Status", "Message"]);
    expect(sheet.views[0]).toMatchObject({ state: "frozen", ySplit: 1 });
  });

  it("orders the rows by sheet row, whatever order they were sent in", async () => {
    const sheet = await resultSheet();
    const rows = [2, 3, 4, 5].map((r) => sheet.getCell(r, 1).value);
    expect(rows).toEqual([3, 5, 7, 9]);
  });

  it("says what happened to each row, in words", async () => {
    const sheet = await resultSheet();
    expect([2, 3, 4, 5].map((r) => sheet.getCell(r, 3).value)).toEqual(["Created", "Skipped", "Failed", "Invalid"]);
    expect([2, 3, 4, 5].map((r) => sheet.getCell(r, 4).value)).toEqual([
      null,
      "A product with this SKU already exists",
      "Forbidden (sku: not yours)",
      "Name is required",
    ]);
  });

  it("writes the row label as text even when it looks like a formula", async () => {
    const sheet = await resultSheet();
    expect(sheet.getCell("B3").value).toBe("=SUM(1)");
    expect(sheet.getCell("B3").formula).toBeUndefined();
  });

  it("uses the first column as the label when nothing is required", async () => {
    const optional = makeProductEntity({
      columns: [{ key: "name", header: "Name", kind: "text", help: "The name." }],
      sampleRows: [{ name: "A" }],
    });
    const sheet = await resultSheet(optional);
    expect(sheet.getCell("B1").value).toBe("Name");
  });

  it("is only a header row for a summary with no outcomes", async () => {
    const sheet = await resultSheet(entity, { ...summary, outcomes: [], created: 0, skipped: 0, failed: 0, invalid: 0, cancelled: true });
    expect(sheet.actualRowCount).toBe(1);
  });
});
