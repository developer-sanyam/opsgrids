import { describe, expect, it } from "vitest";

import { prepareImport } from "./preview";
import { fileWithRows, productColumns, productLookups, productSamples, toProductPayload } from "./testkit";
import { RowProblem, RowSkipped, defineImportEntity } from "./types";
import type { ProductItem, ProductPayload } from "./testkit";
import { readWorkbook } from "./workbook";

const entity = defineImportEntity<ProductPayload, ProductItem, never>({
  id: "test.skipped",
  noun: "products",
  nounSingular: "product",
  fileStem: "products",
  columns: productColumns,
  sampleRows: productSamples,
  notes: [],
  loadLookups: () => Promise.resolve(productLookups),
  toPayload: (row, ctx) => {
    if (row.sku === "DUP-1") throw new RowSkipped("A product with that SKU is already on file.");
    if (row.sku === "BAD-1") throw new RowProblem("That SKU is not allowed.");
    return toProductPayload(row, ctx.lookups);
  },
  create: () => Promise.resolve({ id: "new-id" }),
});

const ctx = { lookups: productLookups, signal: new AbortController().signal };

describe("a row that already exists", () => {
  it("is previewed as already existing and reported as skipped, not as needing a fix", async () => {
    const parsed = await readWorkbook(
      await fileWithRows(
        ["Record ID", "SKU *", "Name *"],
        [
          [null, "NEW-1", "Bracket"],
          [null, "DUP-1", "Washer"],
          [null, "BAD-1", "Nut"],
        ],
      ),
      entity,
    );
    const { preview, prepared } = prepareImport(entity, parsed, ctx, "file.xlsx");
    expect(preview).toMatchObject({ total: 3, valid: 1, invalid: 1, exists: 1 });
    expect(preview.rows.map((r) => [r.row, r.action])).toEqual(
      expect.arrayContaining([
        [2, "create"],
        [3, "exists"],
        [4, "invalid"],
      ]),
    );
    const exists = preview.rows.find((r) => r.action === "exists");
    expect(exists?.error).toBeNull();
    expect(exists?.note).toBe("A product with that SKU is already on file.");
    expect(prepared.skipped).toEqual([
      { row: 3, status: "skipped", message: "A product with that SKU is already on file.", label: "DUP-1" },
    ]);
    expect(prepared.invalid.map((o) => o.row)).toEqual([4]);
  });
});
