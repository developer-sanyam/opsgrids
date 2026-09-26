import type * as ExcelJS from "exceljs";
import { describe, expect, it } from "vitest";

import { buildExportWorkbook, exportFileName } from "./exporter";
import { loadExcel } from "./excel";
import { prepareImport } from "./preview";
import { fileOf, loadBlob, makeProductEntity, productColumns, productLookups } from "./testkit";
import type { ProductItem } from "./testkit";
import type { ImportColumn } from "./types";
import { readWorkbook } from "./workbook";

const entity = makeProductEntity();

function item(overrides: Partial<ProductItem> = {}): ProductItem {
  return {
    id: "p-1",
    sku: "0012-BLK",
    name: "Steel bracket",
    status: "in_stock",
    category: "Hardware",
    priceMinor: 149950,
    currency: "INR",
    quantity: 120,
    active: true,
    email: "orders@example.com",
    createdAt: "2026-09-26T10:30:00Z",
    ...overrides,
  };
}

async function exported(items: readonly ProductItem[], target = entity): Promise<ExcelJS.Worksheet> {
  const workbook = await loadBlob(await buildExportWorkbook(target, items, productLookups));
  const sheet = workbook.getWorksheet("Export");
  if (sheet === undefined) throw new Error("No Export sheet");
  return sheet;
}

describe("exportFileName", () => {
  it("is the stem and the day the user is on, in ISO form", () => {
    expect(exportFileName(entity, new Date(2026, 8, 26, 23, 59))).toBe("products-2026-09-26.xlsx");
    expect(exportFileName(entity, new Date(2026, 0, 5, 0, 1))).toBe("products-2026-01-05.xlsx");
  });
});

describe("the export workbook", () => {
  it("is one sheet named Export", async () => {
    const workbook = await loadBlob(await buildExportWorkbook(entity, [item()], productLookups));
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Export"]);
  });

  it("is headed by the import columns and the read-only ones, without asterisks", async () => {
    const sheet = await exported([item()]);
    const headers = (sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1);
    expect(headers).toEqual([
      "Id",
      "SKU",
      "Name",
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
      "Created",
    ]);
  });

  it("freezes and styles the header and adds a filter", async () => {
    const sheet = await exported([item()]);
    expect(sheet.views[0]).toMatchObject({ state: "frozen", ySplit: 1 });
    expect(sheet.getCell("A1").font?.bold).toBe(true);
    expect(sheet.autoFilter).toBeTruthy();
  });

  it("writes numbers as numbers, money in major units, dates as ISO text and booleans as Yes or No", async () => {
    const sheet = await exported([item()]);
    expect(sheet.getCell("H2").value).toBe(120);
    expect(sheet.getCell("F2").value).toBe(1499.5);
    expect(sheet.getCell("F2").numFmt).toBe("#,##0.00");
    expect(sheet.getCell("K2").value).toBe("Yes");
    expect(sheet.getCell("N2").value).toBe("2026-09-26T10:30:00Z");
    expect(sheet.getCell("D2").value).toBe("in_stock");
  });

  it("shows money with each row's own currency places", async () => {
    const sheet = await exported([
      item({ priceMinor: 500, currency: "JPY" }),
      item({ priceMinor: 1234, currency: "KWD" }),
      item({ priceMinor: 250, currency: "USD" }),
    ]);
    expect(sheet.getCell("F2").value).toBe(500);
    expect(sheet.getCell("F2").numFmt).toBe("#,##0");
    expect(sheet.getCell("F3").value).toBe(1.234);
    expect(sheet.getCell("F3").numFmt).toBe("#,##0.000");
    expect(sheet.getCell("F4").value).toBe(2.5);
    expect(sheet.getCell("F4").numFmt).toBe("#,##0.00");
  });

  it("leaves a value the row does not have empty", async () => {
    const sheet = await exported([item()]);
    expect(sheet.getCell("I2").value).toBeNull();
    expect(sheet.getCell("J2").value).toBeNull();
    expect(sheet.getCell("M2").value).toBeNull();
  });

  it("formats the text columns as text so editing a cell cannot turn it into something else", async () => {
    const sheet = await exported([item()]);
    for (const address of ["A2", "B2", "C2", "D2", "E2", "G2", "K2", "L2", "N2", "B50"]) {
      expect(sheet.getCell(address).numFmt, address).toBe("@");
    }
    expect(sheet.getCell("H2").numFmt).not.toBe("@");
  });

  it("is only a header row when there is nothing to export", async () => {
    const sheet = await exported([]);
    expect(sheet.actualRowCount).toBe(1);
  });

  it("writes as many rows as it is given, in order", async () => {
    const items = Array.from({ length: 250 }, (_, i) => item({ id: `p-${i}`, sku: `SKU-${i}` }));
    const sheet = await exported(items);
    expect(sheet.actualRowCount).toBe(251);
    expect(sheet.getCell("B2").value).toBe("SKU-0");
    expect(sheet.getCell("B251").value).toBe("SKU-249");
  });

  it("cuts a value Excel could not hold", async () => {
    const sheet = await exported([item({ name: "x".repeat(40_000) })]);
    const written = sheet.getCell("C2").value;
    expect(typeof written === "string" && written.length).toBe(32_767);
  });

  it("refuses an entity that cannot be exported", async () => {
    await expect(buildExportWorkbook({ ...entity, export: undefined }, [item()], productLookups)).rejects.toThrow(
      "products cannot be exported",
    );
  });

  it("leaves out an import-only column, wherever it sits, and keeps the read-only ones", async () => {
    const opening: ImportColumn = {
      key: "opening",
      header: "Opening stock",
      kind: "integer",
      help: "Units when the record is created.",
      importOnly: true,
    };
    const withOpening = makeProductEntity({
      columns: [...productColumns.slice(0, 2), opening, ...productColumns.slice(2)],
    });
    const sheet = await exported([item()], withOpening);
    const headers = (sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1);
    expect(headers).not.toContain("Opening stock");
    expect(headers).toContain("Id");
    expect(headers).toContain("Created");
    expect(headers).toHaveLength(14);
    // The values still line up under their headers.
    expect(sheet.getCell("B2").value).toBe("0012-BLK");
    expect(sheet.getCell("F2").value).toBe(1499.5);
  });

  it("writes a masked value exactly as the list returned it", async () => {
    const masked = makeProductEntity();
    const withMask = {
      ...masked,
      export: {
        ...masked.export,
        fetchPage: masked.export?.fetchPage ?? (() => Promise.resolve({ items: [], total: 0, pages: 1 })),
        toRow: () => ({ sku: "A-1", name: "Bracket", phone: "••••••3210", email: "o•••@example.com" }),
      },
    };
    const sheet = await exported([item()], withMask);
    expect(sheet.getCell("M2").value).toBe("••••••3210");
    expect(sheet.getCell("L2").value).toBe("o•••@example.com");
  });
});

describe("formula injection", () => {
  const hostile = [
    '=HYPERLINK("https://evil.example","click me")',
    "+1",
    "-5+2",
    "@SUM(1,2)",
    "=cmd|' /C calc'!A0",
    "\t=1+1",
  ];

  it("writes every string as a string cell, never a formula", async () => {
    const excel = await loadExcel();
    const sheet = await exported(hostile.map((text, i) => item({ id: `p-${i}`, name: text, sku: text })));
    hostile.forEach((text, index) => {
      const row = index + 2;
      for (const column of ["B", "C"]) {
        const cell = sheet.getCell(`${column}${row}`);
        expect(cell.type, `${column}${row}`).toBe(excel.ValueType.String);
        expect(cell.value).toBe(text);
        expect(cell.formula).toBeUndefined();
        expect(cell.numFmt).toBe("@");
      }
    });
  });

  it("never yields a formula when the exported file is read back by the importer", async () => {
    const blob = await buildExportWorkbook(
      entity,
      hostile.map((text, i) => item({ id: `p-${i}`, name: text, sku: text })),
      productLookups,
    );
    const parsed = await readWorkbook(await fileOf(await loadBlob(blob)), entity);
    expect(parsed.rows).toHaveLength(hostile.length);
    hostile.forEach((text, index) => {
      const cells = parsed.rows[index]?.cells;
      expect(cells?.sku).toBe(text);
      expect(cells?.name).toBe(text);
      expect(typeof cells?.sku).toBe("string");
    });
  });

  it("keeps a number-looking string as text so a leading zero survives", async () => {
    const sheet = await exported([item({ sku: "0012", name: "007" })]);
    expect(sheet.getCell("B2").value).toBe("0012");
    expect(sheet.getCell("C2").value).toBe("007");
  });
});

describe("re-importing an export", () => {
  it("reads back as valid rows: the read-only columns are ignored and the rest coerces", async () => {
    const items = [
      item({ id: "p-1", sku: "A-1" }),
      item({ id: "p-2", sku: "A-2", priceMinor: 999, currency: "JPY", status: "discontinued", active: false }),
    ];
    const blob = await buildExportWorkbook(entity, items, productLookups);
    const parsed = await readWorkbook(await fileOf(await loadBlob(blob)), entity);
    expect(parsed.unknownHeaders).toEqual([]);
    const { preview, prepared } = prepareImport(
      entity,
      parsed,
      { lookups: productLookups, signal: new AbortController().signal },
      "export.xlsx",
    );
    expect(preview).toMatchObject({ total: 2, valid: 2, invalid: 0, sample: 0 });
    expect(prepared.valid[0]?.values).toMatchObject({ sku: "A-1", price: 149950, currency: "INR", status: "in_stock", active: true });
    expect(prepared.valid[1]?.values).toMatchObject({ sku: "A-2", price: 999, currency: "JPY", status: "discontinued", active: false });
  });
});
