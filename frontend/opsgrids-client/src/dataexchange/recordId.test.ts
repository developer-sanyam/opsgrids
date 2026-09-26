import { describe, expect, it, vi } from "vitest";

import { InvalidCell } from "./cells";
import { buildExportWorkbook } from "./exporter";
import { prepareImport, RECORD_ID_MESSAGE } from "./preview";
import { fileOf, fileWithRows, loadBlob, makeProductEntity, productLookups, productSamples } from "./testkit";
import type { ProductItem } from "./testkit";
import type { ImportSummary } from "./types";
import { readWorkbook } from "./workbook";

const withId = makeProductEntity({ recordIdKey: "id" });
const ctx = { lookups: productLookups, signal: new AbortController().signal };

const HEADERS = ["Id", "SKU *", "Name *"];

function item(i: number, overrides: Partial<ProductItem> = {}): ProductItem {
  return {
    id: `p-${i}`,
    sku: `SKU-${i}`,
    name: `Product ${i}`,
    status: "in_stock",
    category: "Hardware",
    priceMinor: 1000,
    currency: "INR",
    quantity: i,
    active: true,
    email: "orders@example.com",
    createdAt: "2026-09-26T10:30:00Z",
    ...overrides,
  };
}

async function preview(rows: readonly (readonly (string | number | null)[])[], headers = HEADERS) {
  const parsed = await readWorkbook(await fileWithRows(headers, rows), withId);
  return { parsed, ...prepareImport(withId, parsed, ctx, "file.xlsx") };
}

describe("readWorkbook: the record id column", () => {
  it("is read when the entity names it, though read-only columns are otherwise ignored", async () => {
    const { parsed } = await preview([["p-1", "A-1", "Bracket"]]);
    expect(parsed.rows[0]?.cells).toMatchObject({ id: "p-1", sku: "A-1", name: "Bracket" });
  });

  it("is not an importable column and not an unknown header", async () => {
    const { parsed } = await preview([["p-1", "A-1", "Bracket"]]);
    expect(parsed.columns.map((column) => column.key)).toEqual(["sku", "name"]);
    expect(parsed.unknownHeaders).toEqual([]);
  });

  it("is ignored, as before, when the entity does not name a record id", async () => {
    const plain = makeProductEntity();
    const parsed = await readWorkbook(await fileWithRows(HEADERS, [["p-1", "A-1", "Bracket"]]), plain);
    expect(parsed.rows[0]?.cells).toEqual({ sku: "A-1", name: "Bracket" });
  });

  it("does not make a file without the column any different", async () => {
    const parsed = await readWorkbook(await fileWithRows(["SKU *", "Name *"], [["A-1", "Bracket"]]), withId);
    expect(parsed.rows[0]?.cells).toEqual({ sku: "A-1", name: "Bracket" });
  });

  it("counts a row that has only a record id as a row, so it is refused rather than dropped silently", async () => {
    const { parsed } = await preview([["p-1", null, null]]);
    expect(parsed.rows.map((row) => row.row)).toEqual([2]);
  });
});

describe("a row that carries a record id", () => {
  it("is refused in the preview with the explanation, is invalid, and is never sent", async () => {
    const { preview: shown, prepared } = await preview([["p-1", "A-1", "Bracket"]]);
    expect(shown).toMatchObject({ total: 1, valid: 0, invalid: 1, sample: 0 });
    expect(shown.rows[0]?.error).toBe(
      "This row has a Record ID, so it came from an export and is already in the system. Clear the Record ID cell to add it as a new record.",
    );
    expect(RECORD_ID_MESSAGE).toBe(shown.rows[0]?.error);
    expect(prepared.valid).toEqual([]);
    expect(prepared.invalid).toEqual([{ row: 2, status: "invalid", message: RECORD_ID_MESSAGE, label: "A-1" }]);
  });

  it("is refused alone: the column errors of a row that must not be sent would only bury the reason", async () => {
    const { preview: shown } = await preview([["p-1", "", ""]]);
    expect(shown.rows[0]?.error).toBe(RECORD_ID_MESSAGE);
  });

  it("does not stop the other rows: those with the id cleared still import", async () => {
    const { preview: shown, prepared } = await preview([
      ["p-1", "A-1", "Bracket"],
      [null, "A-2", "Nut"],
      ["  ", "A-3", "Bolt"],
      ["p-4", "A-4", "Washer"],
    ]);
    expect(shown).toMatchObject({ total: 4, valid: 2, invalid: 2 });
    expect(prepared.valid.map((row) => row.row)).toEqual([3, 4]);
    expect(shown.rows.map((row) => row.row)).toEqual([2, 5, 3, 4]);
  });

  it("is refused whether the id is text, a number or an error cell", async () => {
    const { preview: shown } = await preview([[12345, "A-1", "Bracket"]]);
    expect(shown.invalid).toBe(1);
    const { prepared } = prepareImport(
      withId,
      { sheetName: "Import", columns: [], rows: [{ row: 2, cells: { id: new InvalidCell("#N/A", "x") } }], unknownHeaders: [], missingRequired: [] },
      ctx,
      "f.xlsx",
    );
    expect(prepared.invalid).toHaveLength(1);
  });

  it("is refused even when it is otherwise an unchanged sample row", async () => {
    const { preview: shown } = await preview([["p-1", productSamples[0]?.sku as string, productSamples[0]?.name as string]]);
    expect(shown).toMatchObject({ invalid: 1, sample: 0 });
  });
});

describe("importing an export", () => {
  async function exportedFile(items: readonly ProductItem[]): Promise<File> {
    return fileOf(await loadBlob(await buildExportWorkbook(withId, items, productLookups)));
  }

  it("refuses every row of an export of records, so re-importing it creates nothing", async () => {
    const parsed = await readWorkbook(await exportedFile([item(1), item(2), item(3)]), withId);
    const { preview: shown, prepared } = prepareImport(withId, parsed, ctx, "export.xlsx");
    expect(shown).toMatchObject({ total: 3, valid: 0, invalid: 3 });
    expect(prepared.valid).toEqual([]);
    expect(shown.rows.every((row) => row.error === RECORD_ID_MESSAGE)).toBe(true);
  });

  it("refuses an export of the sample rows too, though each looks exactly like an example", async () => {
    // The exact data of the entity's examples, but with the ids an export gives them.
    const first = productSamples[0];
    const asRecords = [
      item(1, { sku: String(first?.sku), name: String(first?.name), priceMinor: 149950, quantity: 120 }),
      item(2, { sku: "0013-WHT", name: "Steel bracket, white", status: "discontinued", priceMinor: 18, currency: "JPY", quantity: 0, active: false }),
    ];
    const parsed = await readWorkbook(await exportedFile(asRecords), withId);
    const { preview: shown, prepared } = prepareImport(withId, parsed, ctx, "export.xlsx");
    expect(shown).toMatchObject({ total: 2, valid: 0, invalid: 2, sample: 0 });
    expect(prepared.valid).toEqual([]);
  });

  it("imports the rows whose Record ID was cleared, as new records", async () => {
    const workbook = await loadBlob(await buildExportWorkbook(withId, [item(1), item(2)], productLookups));
    const sheet = workbook.getWorksheet("Export");
    if (sheet === undefined) throw new Error("No Export sheet");
    sheet.getCell("A3").value = null;
    const parsed = await readWorkbook(await fileOf(workbook), withId);
    const { preview: shown, prepared } = prepareImport(withId, parsed, ctx, "edited.xlsx");
    expect(shown).toMatchObject({ valid: 1, invalid: 1 });
    expect(prepared.valid.map((row) => row.values.sku)).toEqual(["SKU-2"]);
  });

  it("would have added them all again without a record id, which is what the column is for", async () => {
    const plain = makeProductEntity();
    const file = await fileOf(await loadBlob(await buildExportWorkbook(plain, [item(1), item(2)], productLookups)));
    const parsed = await readWorkbook(file, plain);
    const { preview: shown } = prepareImport(plain, parsed, ctx, "export.xlsx");
    expect(shown).toMatchObject({ valid: 2, invalid: 0 });
  });
});

describe("the sample sheet's rule about record ids", () => {
  it("is on the Instructions sheet only for an entity that names a record id", async () => {
    const { buildSampleWorkbook } = await import("./template");
    const lines = async (entity: typeof withId): Promise<string[]> => {
      const sheet = (await loadBlob(await buildSampleWorkbook(entity, productLookups))).getWorksheet("Instructions");
      const texts: string[] = [];
      sheet?.eachRow((row) => row.eachCell((cell) => texts.push(cell.text)));
      return texts;
    };
    const rule =
      "Rows exported from the system carry a Record ID; those rows are skipped, delete the Record ID to add a row as a new record.";
    expect(await lines(withId)).toContain(rule);
    expect(await lines(makeProductEntity())).not.toContain(rule);
  });
});

describe("no request is sent for a row that carries a record id", () => {
  it("through the whole path: preview, start, and a create that must never be called", async () => {
    const create = vi.fn(() => Promise.resolve({ id: "new" }));
    const entity = makeProductEntity({ recordIdKey: "id", create });
    const { createExchange } = await import("./exchange");
    const published: string[] = [];
    const summaries: ImportSummary[] = [];
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: (state) => {
        published.push(state.phase);
        if (state.summary !== null) summaries.push(state.summary);
      },
      imported: () => undefined,
    });
    const file = await fileOf(await loadBlob(await buildExportWorkbook(entity, [item(1), item(2)], productLookups)));
    await exchange.actions.openFile(file);
    await exchange.actions.startImport();
    expect(create).not.toHaveBeenCalled();
    expect(published.at(-1)).toBe("done");
    const summary = summaries.at(-1);
    expect(summary).toMatchObject({ created: 0, invalid: 2 });
    expect(summary?.outcomes.map((outcome) => outcome.status)).toEqual(["invalid", "invalid"]);
    expect(summary?.outcomes[0]?.message).toBe(RECORD_ID_MESSAGE);
  });
});
