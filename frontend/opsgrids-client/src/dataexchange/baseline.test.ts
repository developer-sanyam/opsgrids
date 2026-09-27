/**
 * The export baseline, end to end with the real library: the exporter writes it, the reader finds
 * it, an edit is made the way a person would make it (in the workbook), and the preview is run. What
 * is asserted is what would be sent, never the format of the sheet alone.
 */

import type * as ExcelJS from "exceljs";
import { describe, expect, it, vi } from "vitest";

import { BASELINE_MARKER, BASELINE_SHEET, cellToken } from "./baseline";
import { DIGEST_LENGTH, DIGEST_PATTERN, SALT_PATTERN } from "./baselineHash";
import { InvalidCell } from "./cells";
import type { AnyEntity } from "./cells";
import { readBytes } from "./excel";
import { buildExportWorkbook } from "./exporter";
import { makeLookup } from "./lookups";
import { minorToMajor } from "./money";
import { prepareImport } from "./preview";
import { fileOf, loadBlob, makeProductEntity, makeProductUpdate, productLookups, storedProduct } from "./testkit";
import type { ProductItem } from "./testkit";
import { RowProblem, defineImportEntity } from "./types";
import type { ImportColumn, LookupSet, UpdateSpec } from "./types";
import { readWorkbook } from "./workbook";
import type { ParsedSheet } from "./workbook";
import { readZipText } from "./zipTestKit";

const ctx = { lookups: productLookups, signal: new AbortController().signal };
const EXPORTED_AT = new Date("2026-09-27T10:00:00Z");

/**
 * The digest the format describes, worked out here from the description alone: SHA-256 of the JSON
 * of [salt, column, record, cell], the first 16 bytes, in base64 without padding. A second
 * implementation, so a change to the module's encoding breaks the test instead of both agreeing.
 */
async function independentDigest(salt: string, key: string, recordId: string, token: string | null): Promise<string> {
  const message = new TextEncoder().encode(JSON.stringify([salt, key, recordId, token]));
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", message)).slice(0, 10);
  return btoa(String.fromCharCode(...hash)).replace(/=+$/, "");
}

/**
 * A baseline sheet as its layout says: the column keys (a JSON list in the header row) and, per record
 * id, one cell of hashes joined in that order, cut back into the hash of each column.
 */
function layoutOf(sheet: ExcelJS.Worksheet): { keys: string[]; hashes: Map<string, Map<string, string>> } {
  const parsed: unknown = JSON.parse(String(sheet.getCell("B2").value));
  const keys = Array.isArray(parsed) ? parsed.map(String) : [];
  const hashes = new Map<string, Map<string, string>>();
  sheet.eachRow((row, at) => {
    if (at <= 2) return;
    const packed = String(row.getCell(2).value);
    hashes.set(
      String(row.getCell(1).value),
      new Map(keys.map((key, index) => [key, packed.slice(index * DIGEST_LENGTH, (index + 1) * DIGEST_LENGTH)])),
    );
  });
  return { keys, hashes };
}

/** Puts `hash` where the baseline keeps record `id`'s hash for column `key`. */
function setHash(workbook: ExcelJS.Workbook, id: string, key: string, hash: string): void {
  const sheet = sheetOf(workbook, BASELINE_SHEET);
  const { keys } = layoutOf(sheet);
  sheet.eachRow((row, at) => {
    if (at <= 2 || String(row.getCell(1).value) !== id) return;
    const packed = String(row.getCell(2).value);
    const from = keys.indexOf(key) * DIGEST_LENGTH;
    row.getCell(2).value = `${packed.slice(0, from)}${hash}${packed.slice(from + DIGEST_LENGTH)}`;
  });
}

/** Exports `items` with `entity`, and hands back the workbook to edit as a person would. */
async function exportedBook(entity: AnyEntity, items: readonly unknown[], lookups: LookupSet = productLookups) {
  return loadBlob(await buildExportWorkbook(entity, items, lookups, EXPORTED_AT));
}

function sheetOf(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`No sheet ${name}`);
  return sheet;
}

/** The column of a header in the sheet's first row. */
function columnOf(sheet: ExcelJS.Worksheet, header: string): number {
  let found = 0;
  sheet.getRow(1).eachCell((cell, col) => {
    if (cell.text === header) found = col;
  });
  if (found === 0) throw new Error(`No column ${header}`);
  return found;
}

// --------------------------------------------------------------------------------- what is written

describe("the export writes a baseline only where an update can use it", () => {
  const items = [storedProduct(1), storedProduct(2)];

  it("is a very hidden sheet, after the export sheet, of a list that can be updated by Record ID", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const workbook = await exportedBook(entity, items);
    expect(workbook.worksheets.map((sheet) => [sheet.name, sheet.state])).toEqual([
      ["Export", "visible"],
      [BASELINE_SHEET, "veryHidden"],
    ]);
  });

  it("holds the marker, the format, the list, the export time and the file's salt, then the column keys, then a record per row", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const sheet = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    const marker = (sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1);
    expect(marker.slice(0, 4)).toEqual([BASELINE_MARKER, 2, "test.products", "2026-09-27T10:00:00.000Z"]);
    expect(marker).toHaveLength(5);
    expect(String(marker[4])).toMatch(SALT_PATTERN);
    expect((sheet.getRow(2).values as ExcelJS.CellValue[]).slice(1)).toEqual([
      "recordId",
      '["sku","name","status","category","price","currency","quantity","weight","launched","active","email","phone"]',
    ]);
    // The record ids are in the clear (they are opaque). After each comes a single cell: one hash per column, joined.
    expect(sheet.rowCount).toBe(4);
    for (const [row, id] of [
      [3, "p-1"],
      [4, "p-2"],
    ] as const) {
      expect(sheet.getCell(row, 1).value).toBe(id);
      const packed = String(sheet.getCell(row, 2).value);
      expect(packed).toHaveLength(12 * DIGEST_LENGTH);
      for (let at = 0; at < 12; at += 1) {
        expect(packed.slice(at * DIGEST_LENGTH, (at + 1) * DIGEST_LENGTH)).toMatch(DIGEST_PATTERN);
      }
      expect(sheet.getCell(row, 3).value).toBeNull();
    }
  });

  it("makes each digest SHA-256 of the salt, the column, the record and the cell, cut to 80 bits: pinned against a second implementation", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const sheet = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    const salt = String(sheet.getCell("E1").value);
    const { hashes } = layoutOf(sheet);
    // What the export wrote for record p-1: a number by its digits, yes/no for a boolean, null for blank.
    const written: Record<string, string | null> = {
      sku: "SKU-1",
      name: "Product 1",
      status: "in_stock",
      category: "Hardware",
      price: "10",
      currency: "INR",
      quantity: "1",
      weight: null,
      launched: null,
      active: "Yes",
      email: "orders@example.com",
      phone: null,
    };
    for (const [key, token] of Object.entries(written)) {
      expect(hashes.get("p-1")?.get(key), key).toBe(await independentDigest(salt, key, "p-1", token));
    }
  });

  it("gives equal values different hashes when they sit in different columns or different records", async () => {
    const twins = [storedProduct(1, { name: "5", quantity: 5 }), storedProduct(2, { name: "5", quantity: 5 })];
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items: twins }) });
    const { hashes } = layoutOf(sheetOf(await exportedBook(entity, twins), BASELINE_SHEET));
    const hash = (id: string, key: string) => hashes.get(id)?.get(key);
    // The same "5" twice in one record, and the same record twice in the file.
    expect(hash("p-1", "name")).not.toBe(hash("p-1", "quantity"));
    expect(hash("p-1", "name")).not.toBe(hash("p-2", "name"));
    expect(hash("p-1", "status")).not.toBe(hash("p-2", "status"));
    // Blank cells are hashed too, so a column of blanks does not stand out as a run of nothing.
    expect(hash("p-1", "weight")).toMatch(DIGEST_PATTERN);
    expect(hash("p-1", "weight")).not.toBe(hash("p-2", "weight"));
  });

  it("gives every export its own salt, so the same list exported twice shares no hash", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const first = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    const second = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    expect(first.getCell("E1").value).not.toBe(second.getCell("E1").value);
    const [a, b] = [layoutOf(first), layoutOf(second)];
    for (const key of a.keys) expect(a.hashes.get("p-1")?.get(key), key).not.toBe(b.hashes.get("p-1")?.get(key));
  });

  it("leaves out the read-only columns: an import never reads them", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const { keys } = layoutOf(sheetOf(await exportedBook(entity, items), BASELINE_SHEET));
    expect(keys).not.toContain("created");
    expect(keys).not.toContain("id");
  });

  it("is not written for a list that cannot be updated, or that has no Record ID for a row to carry", async () => {
    const plain = makeProductEntity({ recordIdKey: "id" });
    expect((await exportedBook(plain, items)).worksheets.map((sheet) => sheet.name)).toEqual(["Export"]);
    const keyOnly = makeProductEntity({ update: makeProductUpdate({ items }) });
    expect((await exportedBook(keyOnly, items)).worksheets.map((sheet) => sheet.name)).toEqual(["Export"]);
  });

  it("is not offered as the sheet to import, and no column of it is read", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const file = await fileOf(await exportedBook(entity, items));
    const sheet = await readWorkbook(file, entity);
    expect(sheet.sheetName).toBe("Export");
    expect(sheet.unknownHeaders).toEqual([]);
    expect(sheet.rows).toHaveLength(2);
  });

  it("records a list with no rows: an empty baseline is a baseline", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const book = await exportedBook(entity, []);
    const read = await readWorkbook(await fileOf(book), entity).catch((error: unknown) => error);
    // No rows to import is refused as ever; the sheet itself was written and is readable.
    expect(String(read)).toContain("There are no rows to import");
    expect(sheetOf(book, BASELINE_SHEET).rowCount).toBe(2);
  });
});

// ------------------------------------------------------------------------ what a returning file does

interface Setup {
  /** The records as they were when exported. */
  exportedItems: readonly ProductItem[];
  /** The records as they are when the file comes back. */
  live: readonly ProductItem[];
  spec?: (base: UpdateSpec<ProductItem>) => UpdateSpec<ProductItem>;
  edit?: (sheet: ExcelJS.Worksheet, workbook: ExcelJS.Workbook) => void;
  lookups?: LookupSet;
}

/** Export, edit, read and preview in update mode; the sends a run of the valid rows would make are recorded. */
async function returning(setup: Setup) {
  const sent: { id: string; body: Readonly<Record<string, unknown>> }[] = [];
  const base = makeProductUpdate({
    items: setup.live,
    send: (existing, body) => {
      sent.push({ id: existing.id, body });
      return Promise.resolve();
    },
  });
  const entity = makeProductEntity({ recordIdKey: "id", update: setup.spec?.(base) ?? base });
  const workbook = await exportedBook(entity, setup.exportedItems, setup.lookups);
  setup.edit?.(sheetOf(workbook, "Export"), workbook);
  const sheet = await readWorkbook(await fileOf(workbook), entity);
  const { preview, prepared } = prepareImport(entity, sheet, ctx, "back.xlsx", "addAndUpdate", setup.live);
  const run = async (): Promise<void> => {
    for (const row of prepared.valid) await row.update?.plan(ctx)?.run();
  };
  return { sheet, preview, prepared, sent, run, workbook };
}

/** A category label two categories share (productLookups has 'Twin' and 'twin'): unusable as a lookup. */
const withCategory = (base: UpdateSpec<ProductItem>): UpdateSpec<ProductItem> => ({
  ...base,
  notUpdatable: (base.notUpdatable ?? []).filter((key) => key !== "category"),
  fields: [
    ...base.fields,
    {
      column: "category",
      field: "category_id",
      current: (item) => item.category,
      resolve: (value, context) => {
        const label = String(value);
        if (context.lookups.categories?.isAmbiguous(label) === true) {
          throw new RowProblem(`'${label}' matches more than one category`);
        }
        return label;
      },
    },
  ],
});

describe("a file that comes back from an export", () => {
  const monday = [storedProduct(1, { category: "Twin" }), storedProduct(2)];
  // On Tuesday a colleague renamed product 1, moved its stock and changed its category.
  const tuesday = [
    storedProduct(1, { name: "Renamed by Ravi", quantity: 77, category: "Fasteners", status: "discontinued" }),
    storedProduct(2),
  ];
  const editPrice = (sheet: ExcelJS.Worksheet): void => {
    sheet.getCell(2, columnOf(sheet, "Price")).value = 12.5;
  };

  it("sends only the cell that was edited, never a stale one a colleague has since changed", async () => {
    const { preview, sent, run } = await returning({ exportedItems: monday, live: tuesday, edit: editPrice });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Price"], error: null });
    await run();
    expect(sent).toEqual([{ id: "p-1", body: { price_minor: 1250 } }]);
  });

  it("would have sent every stale cell without the baseline: this is what it prevents", async () => {
    const { preview, sheet } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet, workbook) => {
        editPrice(sheet);
        // The exported category is a column an update refuses; blank it so the other stale cells are what is seen.
        sheet.getCell(2, columnOf(sheet, "Category")).value = null;
        workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id);
      },
    });
    expect(sheet.baseline).toEqual({ kind: "absent" });
    const row = preview.rows.find((entry) => entry.row === 2);
    // The exported name, status and stock overwrite the colleague's: the audit's scenario.
    expect(row).toMatchObject({ action: "update" });
    expect(row?.changes).toEqual(expect.arrayContaining(["Name", "Status", "Quantity", "Price"]));
  });

  it("does not refuse an untouched exported label the lookup calls ambiguous: it is not looked up at all", async () => {
    const { preview, sent, run } = await returning({
      exportedItems: monday,
      live: tuesday,
      spec: withCategory,
      edit: editPrice,
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Price"], error: null });
    await run();
    expect(sent[0]?.body).toEqual({ price_minor: 1250 });
  });

  it("still judges a label that was edited: an ambiguous one typed now is refused as ever", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      spec: withCategory,
      edit: (sheet) => {
        sheet.getCell(2, columnOf(sheet, "Category")).value = "twin";
      },
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({
      action: "invalid",
      error: "'twin' matches more than one category",
    });
  });

  it("refuses the same untouched label when the file has no baseline: today's behaviour, kept without one", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      spec: withCategory,
      edit: (_sheet, workbook) => workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id),
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({
      action: "invalid",
      error: "'Twin' matches more than one category",
    });
  });

  it("judges an untouched cell in a column the update refuses before anything else: it is blank", async () => {
    // Category is a column an update refuses a value in; the export fills it for every record.
    const { preview } = await returning({ exportedItems: monday, live: tuesday, edit: editPrice });
    expect(preview.rows.find((row) => row.row === 3)).toMatchObject({ action: "unchanged", error: null });
    const { preview: withoutBaseline } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (_sheet, workbook) => workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id),
    });
    expect(withoutBaseline.rows.find((row) => row.row === 3)?.error).toBe(
      "Category cannot be changed by an update: leave the cell blank.",
    );
  });

  it("treats an export sent back untouched as changing nothing, even after everything changed underneath", async () => {
    const { preview, sent, run } = await returning({ exportedItems: monday, live: tuesday });
    expect(preview).toMatchObject({ unchanged: 2, update: 0, invalid: 0, valid: 0, baseline: "used" });
    await run();
    expect(sent).toEqual([]);
  });

  it("uses a cell that was edited even when it was edited back to a value the record now has", async () => {
    // Edited to what Ravi set: it differs from the baseline, then equals the record, so nothing to send.
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.getCell(2, columnOf(sheet, "Name")).value = "Renamed by Ravi";
      },
    });
    expect(preview.rows.find((row) => row.row === 2)?.action).toBe("unchanged");
  });

  it("never lets [clear] equal a baseline: it empties the field, whatever the export wrote", async () => {
    const { preview, sent, run } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.getCell(2, columnOf(sheet, "Supplier email")).value = "[clear]";
      },
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Supplier email"] });
    await run();
    expect(sent).toEqual([{ id: "p-1", body: { email: "" } }]);
  });

  it("clears a field the export wrote blank and a colleague has filled since: an edit is an edit", async () => {
    const blank = [storedProduct(1, { email: "" }), storedProduct(2)];
    const filled = [storedProduct(1, { email: "new@example.com" }), storedProduct(2)];
    const { sent, run } = await returning({
      exportedItems: blank,
      live: filled,
      edit: (sheet) => {
        sheet.getCell(2, columnOf(sheet, "Supplier email")).value = "[clear]";
      },
    });
    await run();
    expect(sent).toEqual([{ id: "p-1", body: { email: "" } }]);
  });

  it("gives a blank cell its usual meaning: leave the field alone", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.getCell(2, columnOf(sheet, "Name")).value = null;
      },
    });
    expect(preview.rows.find((row) => row.row === 2)?.action).toBe("unchanged");
  });

  it("compares a record the export never had (created since) with the record as it is now", async () => {
    const created = storedProduct(9, { name: "Made on Tuesday" });
    const { preview } = await returning({
      exportedItems: monday,
      live: [...tuesday, created],
      edit: (sheet) => {
        // Row 3 was p-2's; it now names p-9, which has no baseline entry, so nothing in it is untouched.
        for (let col = 2; col <= sheet.columnCount; col += 1) sheet.getCell(3, col).value = null;
        sheet.getCell(3, 1).value = "p-9";
        sheet.getCell(3, columnOf(sheet, "Name")).value = "Renamed";
        sheet.getCell(3, columnOf(sheet, "Quantity")).value = 2;
      },
    });
    expect(preview.rows.find((row) => row.row === 3)).toMatchObject({ action: "update", changes: ["Name", "Quantity"] });
  });

  it("finds the Record ID with the spaces an editor may leave around it", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.getCell(2, 1).value = "  p-1  ";
        sheet.getCell(2, columnOf(sheet, "Price")).value = 12.5;
      },
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Price"] });
  });

  it("does nothing to the rows it does not name: a row with only a key has no baseline to use", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.getCell(2, 1).value = null;
        sheet.getCell(2, columnOf(sheet, "Category")).value = null;
        sheet.getCell(2, columnOf(sheet, "Price")).value = 12.5;
      },
    });
    // Matched by SKU with no Record ID: every filled cell is compared with the record as it is now.
    const row = preview.rows.find((entry) => entry.row === 2);
    expect(row).toMatchObject({ action: "update" });
    expect(row?.changes).toEqual(expect.arrayContaining(["Name", "Quantity", "Price"]));
  });
});

describe("what the preview says about the baseline", () => {
  const items = [storedProduct(1), storedProduct(2)];

  it("says it was used when a row named its record and the file had one", async () => {
    const { preview } = await returning({ exportedItems: items, live: items });
    expect(preview.baseline).toBe("used");
  });

  it("says there is none for a file that has no baseline sheet (a sample sheet, or one that lost it in an editor)", async () => {
    const { preview } = await returning({
      exportedItems: items,
      live: items,
      edit: (_sheet, workbook) => workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id),
    });
    expect(preview.baseline).toBe("none");
  });

  it("says nothing when no row names a stored record: there is nothing there to be stale", async () => {
    const { preview } = await returning({
      exportedItems: items,
      live: items,
      edit: (sheet) => {
        sheet.getCell(2, 1).value = null;
        sheet.getCell(3, 1).value = null;
        sheet.getCell(2, columnOf(sheet, "SKU")).value = "NEW-1";
        sheet.getCell(3, columnOf(sheet, "SKU")).value = "NEW-2";
      },
    });
    expect(preview).toMatchObject({ create: 2, baseline: null });
  });

  it("says nothing in add mode, where no row is matched", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const sheet = await readWorkbook(await fileOf(await exportedBook(entity, items)), entity);
    expect(prepareImport(entity, sheet, ctx, "f.xlsx", "add", null).preview.baseline).toBeNull();
  });
});

// ---------------------------------------------------------------------------- every kind of cell

interface Gadget {
  id: string;
  name: string;
  code: string;
  note: string;
  started: string;
  price: number;
  currency: string;
  active: boolean;
  percent: number;
  weight: number;
  tag: string;
  formula: string;
}

const gadgetColumns: readonly ImportColumn[] = [
  { key: "id", header: "Gadget ID", kind: "text", help: "The system's id.", readOnly: true },
  { key: "name", header: "Name", kind: "text", required: true, help: "What it is called." },
  { key: "code", header: "Code", kind: "text", help: "A code with leading zeros." },
  { key: "note", header: "Note", kind: "text", multiline: true, help: "Free text over several lines." },
  { key: "started", header: "Started", kind: "date", help: "A date." },
  { key: "price", header: "Price", kind: "money", help: "An amount.", min: 0 },
  {
    key: "currency",
    header: "Currency",
    kind: "enum",
    help: "The currency.",
    defaultCurrency: "INR",
    options: [
      { value: "INR", label: "INR" },
      { value: "JPY", label: "JPY" },
    ],
  },
  { key: "active", header: "Active", kind: "boolean", help: "Yes or No." },
  { key: "percent", header: "Progress %", kind: "integer", help: "A percentage.", min: 0, max: 100 },
  { key: "weight", header: "Weight", kind: "decimal", help: "Kilograms." },
  { key: "tag", header: "Tag", kind: "text", help: "A tag." },
  { key: "formula", header: "Formula text", kind: "text", help: "Text that looks like a formula." },
];

/** Every kind of cell the exporter writes, with the awkward values: zeros, line breaks, a control character, padding, `=`. */
const gadget: Gadget = {
  id: "g-1",
  name: "Widget",
  code: "00123",
  note: "First line\r\nSecond line\n\nFourth",
  started: "2026-03-04",
  price: 149950,
  currency: "INR",
  active: true,
  percent: 35,
  weight: 12.5,
  tag: "  padded\u0007 tag  ",
  formula: "=SUM(A1)",
};

/** The same record after a colleague changed every field: nothing an untouched file says is still true. */
const changedGadget: Gadget = {
  id: "g-1",
  name: "Colleague's widget",
  code: "00999",
  note: "Rewritten",
  started: "2026-01-01",
  price: 100,
  currency: "INR",
  active: false,
  percent: 99,
  weight: 1.5,
  tag: "other",
  formula: "plain",
};

function gadgetEntity(live: readonly Gadget[], options: { maxRows?: number } = {}) {
  const text = (column: string, field: keyof Gadget) => ({ column, current: (item: Gadget) => String(item[field]) });
  return defineImportEntity<Record<string, never>, Gadget>({
    id: "test.gadgets",
    noun: "gadgets",
    nounSingular: "gadget",
    fileStem: "gadgets",
    columns: gadgetColumns,
    sampleRows: [{ name: "Sample" }],
    recordIdKey: "id",
    toPayload: () => ({}),
    create: () => Promise.resolve(undefined),
    export: {
      maxRows: options.maxRows,
      fetchPage: () => Promise.resolve({ items: live, total: live.length, pages: 1 }),
      toRow: (item) => ({
        id: item.id,
        name: item.name,
        code: item.code,
        note: item.note,
        started: item.started,
        price: minorToMajor(item.price, item.currency),
        currency: item.currency,
        active: item.active,
        percent: item.percent,
        weight: item.weight,
        tag: item.tag,
        formula: item.formula,
      }),
    },
    update: {
      loadExisting: () => Promise.resolve(live),
      idOf: (item) => item.id,
      fields: [
        text("name", "name"),
        text("code", "code"),
        text("note", "note"),
        text("started", "started"),
        { column: "price", field: "price_minor", current: (item: Gadget) => item.price },
        { column: "currency", current: (item: Gadget) => item.currency, immutable: true },
        { column: "active", field: "is_active", current: (item: Gadget) => item.active },
        { column: "percent", current: (item: Gadget) => item.percent },
        { column: "weight", current: (item: Gadget) => item.weight },
        text("tag", "tag"),
        text("formula", "formula"),
      ],
      send: () => Promise.resolve(),
    },
  });
}

describe("every kind of cell an export writes comes back untouched", () => {
  const entity = gadgetEntity([changedGadget]);
  const gadgetCtx = { lookups: {}, signal: new AbortController().signal };

  async function comeBack(edit?: (sheet: ExcelJS.Worksheet) => void, keepBaseline = true) {
    const workbook = await loadBlob(await buildExportWorkbook(gadgetEntity([gadget]), [gadget], {}, EXPORTED_AT));
    const sheet = sheetOf(workbook, "Export");
    edit?.(sheet);
    if (!keepBaseline) workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id);
    const read = await readWorkbook(await fileOf(workbook), entity);
    return prepareImport(entity, read, gadgetCtx, "back.xlsx", "addAndUpdate", [changedGadget]).preview;
  }

  it("changes nothing, though the record differs in every field: text with zeros, dates, money, yes/no, numbers, line breaks, padding and control characters all equal what was written", async () => {
    const preview = await comeBack();
    expect(preview.rows[0]).toMatchObject({ action: "unchanged", changes: [], error: null });
    expect(preview.baseline).toBe("used");
  });

  it("shows what the test proves: without the baseline every one of those fields is a change", async () => {
    const preview = await comeBack(undefined, false);
    expect(preview.rows[0]?.changes).toEqual([
      "Name",
      "Code",
      "Note",
      "Started",
      "Price",
      "Active",
      "Progress %",
      "Weight",
      "Tag",
      "Formula text",
    ]);
  });

  it.each([
    ["Name", "Edited name"],
    ["Code", "00777"],
    ["Note", "One line now"],
    ["Started", "2026-04-05"],
    ["Price", 20],
    ["Active", "TRUE"],
    ["Progress %", 40],
    ["Weight", 2.25],
    ["Tag", "edited tag"],
    ["Formula text", "not a formula"],
  ] as const)("still sees an edit of %s as an edit, and only that one", async (header, value) => {
    const preview = await comeBack((sheet) => {
      sheet.getCell(2, columnOf(sheet, header)).value = value;
    });
    expect(preview.rows[0]).toMatchObject({ action: "update", changes: [header], error: null });
  });

  it("does not read a re-typed but equal cell as untouched: a value the user typed is judged as ever", async () => {
    // "Yes" typed as "yes" is not the cell the export wrote; it is compared with the record like any cell.
    const preview = await comeBack((sheet) => {
      sheet.getCell(2, columnOf(sheet, "Active")).value = "yes";
    });
    expect(preview.rows[0]).toMatchObject({ action: "update", changes: ["Active"] });
  });

  it("holds when a spreadsheet program has turned a text date, a text number or a yes into its own type", async () => {
    const preview = await comeBack((sheet) => {
      const started = sheet.getCell(2, columnOf(sheet, "Started"));
      started.numFmt = "yyyy-mm-dd";
      started.value = new Date(Date.UTC(2026, 2, 4));
      sheet.getCell(2, columnOf(sheet, "Progress %")).value = "35";
      sheet.getCell(2, columnOf(sheet, "Active")).value = true;
    });
    expect(preview.rows[0]).toMatchObject({ action: "unchanged", changes: [] });
  });

  it("holds when line breaks were saved with another line ending", async () => {
    const preview = await comeBack((sheet) => {
      sheet.getCell(2, columnOf(sheet, "Note")).value = "First line\nSecond line\n\nFourth";
    });
    expect(preview.rows[0]).toMatchObject({ action: "unchanged", changes: [] });
  });
});

describe("cellToken: one reading for what was written and what comes back", () => {
  it("reads a blank cell as null, whatever kind of blank", () => {
    expect(cellToken(null)).toBeNull();
    expect(cellToken(undefined)).toBeNull();
    expect(cellToken("   ")).toBeNull();
  });
  it("trims text and unifies its line endings, and keeps its case and inner spaces", () => {
    expect(cellToken("  Steel  Bracket ")).toBe("Steel  Bracket");
    expect(cellToken("a\r\nb\rc\nd")).toBe("a\nb\nc\nd");
  });
  it("reads a number by its digits, a date as ISO and a boolean as Yes or No", () => {
    expect(cellToken(1499.5)).toBe("1499.5");
    expect(cellToken(35)).toBe("35");
    expect(cellToken(new Date(Date.UTC(2026, 2, 4)))).toBe("2026-03-04");
    expect(cellToken(true)).toBe("Yes");
    expect(cellToken(false)).toBe("No");
  });
  it("gives an error cell no reading, so it equals nothing", () => {
    expect(cellToken(new InvalidCell("#N/A", "contains the error #N/A"))).toBeUndefined();
  });
});

// ------------------------------------------------------------------------ a baseline that cannot be trusted

describe("a baseline that is not this list's, or is damaged, is ignored and never crashes the import", () => {
  const items = [storedProduct(1), storedProduct(2), storedProduct(3)];
  const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });

  async function readWith(mutate: (baseline: ExcelJS.Worksheet, workbook: ExcelJS.Workbook) => void, target = entity) {
    const workbook = await exportedBook(entity, items);
    mutate(sheetOf(workbook, BASELINE_SHEET), workbook);
    return readWorkbook(await fileOf(workbook), target);
  }

  /** The columns of row `row` that the reader found untouched, or undefined when the baseline names no such row. */
  const untouchedOf = (sheet: ParsedSheet, row: number): string[] | undefined => {
    if (sheet.baseline.kind !== "found") throw new Error(`The baseline is ${sheet.baseline.kind}`);
    const columns = sheet.baseline.untouched.get(row);
    return columns === undefined ? undefined : [...columns].sort();
  };
  const ALL_COLUMNS = [
    "active",
    "category",
    "currency",
    "email",
    "launched",
    "name",
    "phone",
    "price",
    "quantity",
    "sku",
    "status",
    "weight",
  ];

  it("uses an intact one: every cell of every row is still what the export wrote, blank cells included", async () => {
    const sheet = await readWith(() => undefined);
    expect(sheet.baseline.kind).toBe("found");
    if (sheet.baseline.kind === "found") expect([...sheet.baseline.untouched.keys()]).toEqual([2, 3, 4]);
    for (const row of [2, 3, 4]) expect(untouchedOf(sheet, row)).toEqual(ALL_COLUMNS);
  });

  it("ignores a baseline written for another list, and says so", async () => {
    const other = { ...entity, id: "other.list" };
    const sheet = await readWith(() => undefined, other);
    expect(sheet.baseline).toEqual({ kind: "unusable" });
    const { preview } = prepareImport(other, sheet, ctx, "f.xlsx", "addAndUpdate", items);
    expect(preview.baseline).toBe("unusable");
  });

  const damages: readonly [name: string, cell: string, value: ExcelJS.CellValue][] = [
    ["a different marker", "A1", "not-a-baseline"],
    ["the old format, which held the values themselves", "B1", 1],
    ["a format from the future", "B1", 3],
    ["a format that is text", "B1", "2"],
    ["no list named", "C1", null],
    ["no salt", "E1", null],
    ["a salt that is not one", "E1", "short"],
    ["a salt that is a number", "E1", 12345],
    ["a missing record header", "A2", "id"],
    ["no list of columns", "B2", null],
    ["a list of columns that is not a list", "B2", "sku,name"],
    ["a list of columns that is an object", "B2", '{"sku":1}'],
    ["a repeated column key", "B2", '["sku","sku"]'],
    ["an empty column key", "B2", '["sku",""]'],
    ["a column key that is a number", "B2", '["sku",7]'],
    ["a record with no id", "A4", null],
    ["a record id that is an error", "A4", { error: "#N/A" }],
    ["two records with one id", "A5", "p-1"],
  ];

  it.each(damages)("ignores a baseline with %s", async (_name, cell, value) => {
    const sheet = await readWith((baseline) => {
      baseline.getCell(cell).value = value;
    });
    expect(sheet.baseline).toEqual({ kind: "unusable" });
    // The import itself is untouched: the rows are read as they always were.
    expect(sheet.rows).toHaveLength(3);
  });

  it("ignores a baseline that holds more records than an export writes", async () => {
    const small = makeProductEntity({ recordIdKey: "id", exportMaxRows: 2, update: makeProductUpdate({ items }) });
    const sheet = await readWith(() => undefined, small);
    expect(sheet.baseline).toEqual({ kind: "unusable" });
    const exactly = makeProductEntity({ recordIdKey: "id", exportMaxRows: 3, update: makeProductUpdate({ items }) });
    expect((await readWith(() => undefined, exactly)).baseline.kind).toBe("found");
  });

  it("ignores a baseline with more columns than a sheet may have", async () => {
    const sheet = await readWith((baseline) => {
      baseline.getCell("B2").value = JSON.stringify(Array.from({ length: 80 }, (_, index) => `column${index}`));
    });
    expect(sheet.baseline).toEqual({ kind: "unusable" });
  });

  it("skips a column key this version of the list no longer has, and uses the rest", async () => {
    const sheet = await readWith((baseline) => {
      baseline.getCell("B2").value = JSON.stringify(layoutOf(baseline).keys.map((key) => (key === "name" ? "retired_column" : key)));
    });
    // The hashes under the renamed key belong to a column that is not there, so name counts as edited.
    expect(untouchedOf(sheet, 2)).toEqual(ALL_COLUMNS.filter((key) => key !== "name"));
    expect(untouchedOf(sheet, 3)).toEqual(ALL_COLUMNS.filter((key) => key !== "name"));
  });

  it("counts a cell whose hash is not a hash, or is another's, as edited, and only that cell", async () => {
    const sheet = await readWith((_baseline, workbook) => {
      setHash(workbook, "p-1", "name", "!!!!!!!!!!!!!!");
      setHash(workbook, "p-1", "status", "12345678901234");
      setHash(workbook, "p-1", "price", "AAAAAAAAAAAAAA");
    });
    expect(sheet.baseline.kind).toBe("found");
    expect(untouchedOf(sheet, 2)).toEqual(ALL_COLUMNS.filter((key) => !["name", "status", "price"].includes(key)));
    expect(untouchedOf(sheet, 3)).toEqual(ALL_COLUMNS);
  });

  it("gives a record whose hashes are missing, the wrong length, a number or an error none to offer, and touches no other record", async () => {
    const mangles: ExcelJS.CellValue[] = [null, "short", `${"A".repeat(12 * DIGEST_LENGTH)}A`, 1234567890, { error: "#N/A" }];
    for (const mangled of mangles) {
      const sheet = await readWith((baseline) => {
        baseline.getCell("B3").value = mangled;
      });
      expect(sheet.baseline.kind, String(mangled)).toBe("found");
      expect(untouchedOf(sheet, 2), String(mangled)).toEqual([]);
      expect(untouchedOf(sheet, 3), String(mangled)).toEqual(ALL_COLUMNS);
    }
  });

  it("finds the sheet even if a program made it visible, and still imports the export sheet", async () => {
    const sheet = await readWith((baseline) => {
      baseline.state = "visible";
    });
    expect(sheet.baseline.kind).toBe("found");
    expect(sheet.sheetName).toBe("Export");
  });

  it("never offers the baseline as the sheet to import, even when it is the only sheet left visible", async () => {
    const file = await (async () => {
      const workbook = await exportedBook(entity, items);
      workbook.removeWorksheet(sheetOf(workbook, "Export").id);
      sheetOf(workbook, BASELINE_SHEET).state = "visible";
      return fileOf(workbook);
    })();
    await expect(readWorkbook(file, entity)).rejects.toThrow("The workbook has no sheet to import");
  });

  it("takes no baseline from a list that cannot use one, and never reads its sheet", async () => {
    const plain = makeProductEntity({ recordIdKey: "id" });
    const sheet = await readWith(() => undefined, plain);
    expect(sheet.baseline).toEqual({ kind: "absent" });
  });

  it("does not let a baseline add anything: a cell it has and the file lacks is not a value", async () => {
    // A baseline can only take cells away from an update. It holds no way to give a row a value.
    const workbook = await exportedBook(entity, items);
    const book = sheetOf(workbook, "Export");
    book.getCell(2, columnOf(book, "Name")).value = null;
    const sheet = await readWorkbook(await fileOf(workbook), entity);
    expect(sheet.rows[0]?.cells.name).toBeNull();
    const { preview } = prepareImport(entity, sheet, ctx, "f.xlsx", "addAndUpdate", items);
    expect(preview.rows.find((row) => row.row === 2)?.action).toBe("unchanged");
  });
});

// ------------------------------------------------------------------ the baseline keeps no value

/** A sheet's file inside an .xlsx: found the way a program finds it, by its name in workbook.xml and that entry's relationship. */
function sheetPart(files: ReadonlyMap<string, string>, sheetName: string): string {
  const book = files.get("xl/workbook.xml") ?? "";
  const tag = [...book.matchAll(/<sheet\b[^>]*>/g)].map(([text]) => text).find((text) => text.includes(`name="${sheetName}"`));
  const relationship = /\br:id="([^"]*)"/.exec(tag ?? "")?.[1];
  const relations = files.get("xl/_rels/workbook.xml.rels") ?? "";
  const entry = [...relations.matchAll(/<Relationship\b[^>]*>/g)]
    .map(([text]) => text)
    .find((text) => text.includes(`Id="${relationship}"`));
  const target = /\bTarget="([^"]*)"/.exec(entry ?? "")?.[1];
  const part = files.get(`xl/${target}`);
  if (part === undefined) throw new Error(`No file for the sheet ${sheetName}`);
  return part;
}

const unescapeXml = (text: string): string =>
  text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

/** What each cell of a sheet's XML says, shared strings resolved, numbers as written: what a program reading the sheet sees. */
function cellTexts(files: ReadonlyMap<string, string>, sheetXml: string): string[] {
  const shared = [...(files.get("xl/sharedStrings.xml") ?? "").matchAll(/<si>([\s\S]*?)<\/si>/g)].map(([, body]) =>
    unescapeXml([...(body ?? "").matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(([, text]) => text ?? "").join("")),
  );
  const texts: string[] = [];
  for (const [, attributes, inner] of sheetXml.matchAll(/<c\b([^>]*?)>([\s\S]*?)<\/c>/g)) {
    const value = /<v>([\s\S]*?)<\/v>/.exec(inner ?? "")?.[1];
    const inline = /<is>[\s\S]*?<t\b[^>]*>([\s\S]*?)<\/t>/.exec(inner ?? "")?.[1];
    if (/\bt="s"/.test(attributes ?? "") && value !== undefined) texts.push(shared[Number(value)] ?? "");
    else if (inline !== undefined) texts.push(unescapeXml(inline));
    else if (value !== undefined) texts.push(unescapeXml(value));
  }
  return texts;
}

async function zipped(workbook: ExcelJS.Workbook): Promise<Map<string, string>> {
  return readZipText(new Uint8Array(await workbook.xlsx.writeBuffer()));
}

describe("the baseline holds no value of any exported cell", () => {
  const secrets = [
    storedProduct(1, {
      sku: "SKU-ZX-4471",
      name: "Confidential Renewal Terms",
      email: "secret.person@example.com",
      quantity: 7331,
      priceMinor: 987654,
    }),
    storedProduct(2, {
      sku: "SKU-QW-9982",
      name: "Second Confidential Name",
      email: "another.secret@example.org",
      quantity: 8442,
      priceMinor: 123456,
    }),
  ];
  const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items: secrets }) });
  const TEXT_SECRETS = [
    "SKU-ZX-4471",
    "Confidential Renewal Terms",
    "secret.person@example.com",
    "SKU-QW-9982",
    "Second Confidential Name",
    "another.secret@example.org",
  ];
  const NUMBER_SECRETS = ["7331", "8442", "9876.54", "1234.56"];

  it("has every value in the sheet the user sees and none in the baseline sheet, in the XML of the .xlsx itself", async () => {
    const files = await zipped(await exportedBook(entity, secrets));
    const exportTexts = cellTexts(files, sheetPart(files, "Export"));
    const baselineXml = sheetPart(files, BASELINE_SHEET);
    const baselineTexts = cellTexts(files, baselineXml);

    // The control: the same reading finds every value where the user can see it.
    for (const secret of [...TEXT_SECRETS, ...NUMBER_SECRETS]) expect(exportTexts, `export sheet: ${secret}`).toContain(secret);
    // The baseline has the marker, the format, the list and the export time, the salt, the column keys, the ids and the hashes, and no value.
    for (const secret of [...TEXT_SECRETS, ...NUMBER_SECRETS]) expect(baselineTexts, `baseline sheet: ${secret}`).not.toContain(secret);
    for (const secret of TEXT_SECRETS) expect(baselineXml, `baseline XML: ${secret}`).not.toContain(secret);
    const hashed = baselineTexts.filter((text) => /^[A-Za-z0-9+/]+$/.test(text) && text.length === 12 * DIGEST_LENGTH);
    expect(hashed).toHaveLength(secrets.length);
    expect(baselineTexts).toContain("p-1");
  });

  it("leaves nothing of a column, or of a row, that the user deletes before sharing the file", async () => {
    const workbook = await exportedBook(entity, secrets);
    const sheet = sheetOf(workbook, "Export");
    sheet.spliceColumns(columnOf(sheet, "Supplier email"), 1);
    sheet.spliceRows(3, 1);
    const everything = [...(await zipped(workbook)).values()].join("\n");

    for (const gone of ["secret.person@example.com", "another.secret@example.org", "Second Confidential Name", "SKU-QW-9982"]) {
      expect(everything, gone).not.toContain(gone);
    }
    // What was not deleted is still there, so this looked at the right file.
    expect(everything).toContain("Confidential Renewal Terms");
    expect(everything).toContain("SKU-ZX-4471");
  });
});

// ------------------------------------------------------------------ a baseline someone altered

describe("a baseline that was altered only ever makes cells count as edited", () => {
  const monday = [storedProduct(1), storedProduct(2)];
  // On Tuesday a colleague renamed product 1 and moved its stock.
  const tuesday = [storedProduct(1, { name: "Renamed by Ravi", quantity: 77 }), storedProduct(2)];

  it("sends nothing for a file nobody altered, whatever changed underneath: the control", async () => {
    const { preview, sent, run } = await returning({ exportedItems: monday, live: tuesday });
    expect(preview).toMatchObject({ unchanged: 2, update: 0, baseline: "used" });
    await run();
    expect(sent).toEqual([]);
  });

  it("counts a cell whose hash was altered as edited: only that cell, and it is then judged as any edit is", async () => {
    const { preview, sent, run } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (_sheet, workbook) => {
        setHash(workbook, "p-1", "name", "AAAAAAAAAAAAAA");
      },
    });
    // The stale name is now an edit and goes back; the stale stock, whose hash is intact, still does not.
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Name"] });
    expect(preview.rows.find((row) => row.row === 3)).toMatchObject({ action: "unchanged" });
    await run();
    expect(sent).toEqual([{ id: "p-1", body: { name: "Product 1" } }]);
  });

  it("counts every cell as edited when the salt was altered: nothing can match, and nothing is protected or refused for it", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet, workbook) => {
        // The exported category is a column an update refuses; blank it so the other stale cells are what is seen.
        sheet.getCell(2, columnOf(sheet, "Category")).value = null;
        sheetOf(workbook, BASELINE_SHEET).getCell("E1").value = "AAAAAAAAAAAAAAAAAAAAAA";
      },
    });
    const row = preview.rows.find((entry) => entry.row === 2);
    expect(row).toMatchObject({ action: "update", error: null });
    expect(row?.changes).toEqual(expect.arrayContaining(["Name", "Quantity"]));
  });

  it("counts a hash that was moved to another record's row as edited: a hash belongs to its own record", async () => {
    const { preview } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (_sheet, workbook) => {
        // p-2's hash of the name, put where p-1's is: a different record id went into it.
        const other = layoutOf(sheetOf(workbook, BASELINE_SHEET)).hashes.get("p-2")?.get("name") ?? "";
        setHash(workbook, "p-1", "name", other);
      },
    });
    expect(preview.rows.find((row) => row.row === 2)).toMatchObject({ action: "update", changes: ["Name"] });
  });
});

// ------------------------------------------------------------------ a trimmed export

describe("an export the user has trimmed still works", () => {
  const monday = [storedProduct(1), storedProduct(2), storedProduct(3)];
  const tuesday = [storedProduct(1, { name: "Renamed by Ravi", quantity: 77 }), storedProduct(2), storedProduct(3)];

  it("after columns and rows were deleted from the visible sheet: what is left is judged by the baseline, and what is not is not there to send", async () => {
    const { preview, sent, run } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        sheet.spliceColumns(columnOf(sheet, "Name"), 1);
        sheet.spliceRows(4, 1);
        sheet.spliceRows(3, 1);
        sheet.getCell(2, columnOf(sheet, "Price")).value = 12.5;
      },
    });
    expect(preview).toMatchObject({ total: 1, update: 1, invalid: 0, baseline: "used" });
    expect(preview.rows[0]).toMatchObject({ changes: ["Price"] });
    await run();
    // The stale stock was not sent, and the deleted name column could not be.
    expect(sent).toEqual([{ id: "p-1", body: { price_minor: 1250 } }]);
  });

  it("with only the Record ID and one column left", async () => {
    const { preview, sent, run } = await returning({
      exportedItems: monday,
      live: tuesday,
      edit: (sheet) => {
        for (const header of ["SKU", "Name", "Status", "Category", "Currency", "Weight (kg)", "Launch date", "Active", "Supplier email", "Supplier phone"]) {
          sheet.spliceColumns(columnOf(sheet, header), 1);
        }
        sheet.getCell(2, columnOf(sheet, "Quantity")).value = 77;
        sheet.getCell(3, columnOf(sheet, "Quantity")).value = 5;
      },
    });
    // Row 2's quantity is the one a colleague set: an edit that equals the record. Row 3's is a real edit,
    // and row 4's is untouched.
    expect(preview).toMatchObject({ update: 1, unchanged: 2, baseline: "used" });
    await run();
    expect(sent).toEqual([{ id: "p-2", body: { quantity: 5 } }]);
  });
});

// ------------------------------------------------------------------ a context that cannot hash

describe("where the browser context cannot hash (crypto.subtle is only there over HTTPS or on localhost)", () => {
  const items = [storedProduct(1), storedProduct(2)];
  const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
  const withoutSubtle = () => vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });

  it("writes an export without a baseline, rather than failing the export or writing a value in the clear", async () => {
    withoutSubtle();
    let blob: Blob;
    try {
      blob = await buildExportWorkbook(entity, items, productLookups, EXPORTED_AT);
    } finally {
      vi.unstubAllGlobals();
    }
    const workbook = await loadBlob(blob);
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Export"]);
    expect(sheetOf(workbook, "Export").rowCount).toBe(3);
  });

  it("ignores a baseline it cannot check, says it was ignored, and still reads the file", async () => {
    const file = await fileOf(await exportedBook(entity, items));
    withoutSubtle();
    let sheet: ParsedSheet;
    try {
      sheet = await readWorkbook(file, entity);
    } finally {
      vi.unstubAllGlobals();
    }
    expect(sheet.baseline).toEqual({ kind: "unusable" });
    expect(sheet.rows).toHaveLength(2);
    expect(prepareImport(entity, sheet, ctx, "f.xlsx", "addAndUpdate", items).preview.baseline).toBe("unusable");
  });
});

describe("the .xlsx's bytes", () => {
  it("are what the tests unzip: an export is a real ZIP with the baseline as its own sheet", async () => {
    const items = [storedProduct(1)];
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const bytes = await readBytes(await buildExportWorkbook(entity, items, productLookups, EXPORTED_AT));
    const files = await readZipText(new Uint8Array(bytes));
    expect([...files.keys()]).toEqual(expect.arrayContaining(["xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"]));
    expect(files.get("xl/workbook.xml")).toContain('state="veryHidden"');
  });
});

describe("an untouched label is never looked up", () => {
  /** Exports a product whose category is 'Fasteners', reads the file back, and reports which labels were looked up. */
  async function lookedUp(withoutBaseline: boolean): Promise<string[]> {
    const resolve = vi.fn((label: string) => label);
    const spied: LookupSet = {
      ...productLookups,
      categories: { ...makeLookup([{ label: "Fasteners", value: "cat-2" }]), resolve },
    };
    const items = [storedProduct(1, { category: "Fasteners" })];
    const base = makeProductUpdate({ items });
    const spec: UpdateSpec<ProductItem> = {
      ...base,
      notUpdatable: (base.notUpdatable ?? []).filter((key) => key !== "category"),
      fields: [
        ...base.fields,
        {
          column: "category",
          current: (item) => item.category,
          resolve: (value, context) => context.lookups.categories?.resolve(String(value)) ?? null,
        },
      ],
    };
    const entity = makeProductEntity({ recordIdKey: "id", update: spec });
    const workbook = await exportedBook(entity, items, spied);
    if (withoutBaseline) workbook.removeWorksheet(sheetOf(workbook, BASELINE_SHEET).id);
    const read = await readWorkbook(await fileOf(workbook), entity);
    resolve.mockClear();
    prepareImport(entity, read, { lookups: spied, signal: ctx.signal }, "f.xlsx", "addAndUpdate", items);
    // The sample rows are judged with the same lookup; only the label the file carries is asked about.
    return resolve.mock.calls.map(([label]) => label).filter((label) => label === "Fasteners");
  }

  it("does not call the lookup for a cell nobody changed", async () => {
    expect(await lookedUp(false)).toEqual([]);
  });

  it("calls it, as ever, when the file has no baseline: the test would notice a baseline that did nothing", async () => {
    expect(await lookedUp(true)).toContain("Fasteners");
  });
});
