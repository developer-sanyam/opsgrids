/**
 * The export baseline, end to end with the real library: the exporter writes it, the reader finds
 * it, an edit is made the way a person would make it (in the workbook), and the preview is run. What
 * is asserted is what would be sent, never the format of the sheet alone.
 */

import type * as ExcelJS from "exceljs";
import { describe, expect, it, vi } from "vitest";

import { BASELINE_MARKER, BASELINE_SHEET, cellToken } from "./baseline";
import { InvalidCell } from "./cells";
import type { AnyEntity } from "./cells";
import { buildExportWorkbook } from "./exporter";
import { makeLookup } from "./lookups";
import { minorToMajor } from "./money";
import { prepareImport } from "./preview";
import { fileOf, loadBlob, makeProductEntity, makeProductUpdate, productLookups, storedProduct } from "./testkit";
import type { ProductItem } from "./testkit";
import { RowProblem, defineImportEntity } from "./types";
import type { ImportColumn, LookupSet, UpdateSpec } from "./types";
import { readWorkbook } from "./workbook";

const ctx = { lookups: productLookups, signal: new AbortController().signal };
const EXPORTED_AT = new Date("2026-09-27T10:00:00Z");

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

  it("holds the marker, the format, the list and the export time, then the column keys, then a record per row", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const sheet = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    expect((sheet.getRow(1).values as ExcelJS.CellValue[]).slice(1)).toEqual([
      BASELINE_MARKER,
      1,
      "test.products",
      "2026-09-27T10:00:00.000Z",
    ]);
    expect((sheet.getRow(2).values as ExcelJS.CellValue[]).slice(1)).toEqual([
      "recordId",
      "sku",
      "name",
      "status",
      "category",
      "price",
      "currency",
      "quantity",
      "weight",
      "launched",
      "active",
      "email",
      "phone",
    ]);
    // What the export wrote in the data sheet, cell for cell: a number stays a number.
    expect(sheet.getCell("A3").value).toBe("p-1");
    expect(sheet.getCell("B3").value).toBe("SKU-1");
    expect(sheet.getCell("F3").value).toBe(10);
    expect(sheet.getCell("H3").value).toBe(1);
    expect(sheet.getCell("K3").value).toBe("Yes");
    expect(sheet.getCell("I3").value).toBeNull();
    expect(sheet.getCell("A4").value).toBe("p-2");
    expect(sheet.rowCount).toBe(4);
  });

  it("leaves out the read-only columns: an import never reads them", async () => {
    const entity = makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items }) });
    const sheet = sheetOf(await exportedBook(entity, items), BASELINE_SHEET);
    const keys = (sheet.getRow(2).values as ExcelJS.CellValue[]).slice(1);
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

  it("uses an intact one", async () => {
    const sheet = await readWith(() => undefined);
    expect(sheet.baseline.kind).toBe("found");
    if (sheet.baseline.kind === "found") {
      expect([...sheet.baseline.records.keys()]).toEqual(["p-1", "p-2", "p-3"]);
      expect(sheet.baseline.records.get("p-1")?.get("name")).toBe("Product 1");
      expect(sheet.baseline.records.get("p-1")?.get("weight")).toBeNull();
    }
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
    ["an unknown format", "B1", 2],
    ["a format that is text", "B1", "1"],
    ["no list named", "C1", null],
    ["a missing record header", "A2", "id"],
    ["a repeated column key", "C2", "sku"],
    ["a column key that is a number", "C2", 7],
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
      for (let col = 20; col < 80; col += 1) baseline.getCell(2, col).value = `extra${col}`;
    });
    expect(sheet.baseline).toEqual({ kind: "unusable" });
  });

  it("skips a column key this version of the list no longer has, and uses the rest", async () => {
    const sheet = await readWith((baseline) => {
      baseline.getCell("C2").value = "retired_column";
    });
    expect(sheet.baseline.kind).toBe("found");
    if (sheet.baseline.kind === "found") {
      expect(sheet.baseline.records.get("p-1")?.has("retired_column")).toBe(false);
      expect(sheet.baseline.records.get("p-1")?.has("name")).toBe(false);
      expect(sheet.baseline.records.get("p-1")?.get("sku")).toBe("SKU-1");
    }
  });

  it("leaves a cell with an error in it out of the record: it can never equal anything", async () => {
    const sheet = await readWith((baseline) => {
      baseline.getCell("C3").value = { error: "#DIV/0!" };
    });
    expect(sheet.baseline.kind).toBe("found");
    if (sheet.baseline.kind === "found") {
      expect(sheet.baseline.records.get("p-1")?.has("name")).toBe(false);
      expect(sheet.baseline.records.get("p-1")?.get("sku")).toBe("SKU-1");
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
