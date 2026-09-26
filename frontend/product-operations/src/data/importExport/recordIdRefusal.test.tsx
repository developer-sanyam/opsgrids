/**
 * A row that carries a Record ID came out of an export: it is already in the system, and adding it
 * again would create a duplicate of a record the list has no natural key to catch (an item's name,
 * a category's name). Each of the four Operations lists names its id column as `recordIdKey`, and
 * the engine refuses such a row in the preview, before anything is sent. Driven through the real
 * dialog and hook, like `importFlow.test.tsx`.
 */
import type * as ExcelJS from "exceljs";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ImportSummary } from "@opsgrids/client";

import { Harness } from "./DialogHarness";
import { installBrowserGaps, jsonBody, openDialog, rowContaining, stat, uploadFile, workbookFile } from "./dialogTestKit";
import { itemCategoryOut, pageAt, requestsTo, stubGateway, uomOut, warehouseOut } from "./entityTestKit";
import type { RecordedRequest } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { warehouseImport } from "./warehouses";

installBrowserGaps();

const API = "/api/v1/operations";
const SLOW = { timeout: 8_000 };

/** The sentence the engine gives a row that came from an export. */
const RECORD_ID_REFUSAL =
  "This row has a Record ID, so it came from an export and is already in the system. " +
  "Clear the Record ID cell to add it as a new record.";

afterEach(() => {
  vi.unstubAllGlobals();
});

let recorded: RecordedRequest[] = [];

beforeEach(() => {
  recorded = stubGateway(({ url, init }) => {
    if ((init.method ?? "GET") === "POST") return { status: 201, body: { id: "new-id" } };
    const page = Number(url.searchParams.get("page") ?? "1");
    switch (url.pathname) {
      case `${API}/warehouses`:
        return { body: pageAt([[warehouseOut("Main Warehouse", "WH-MAIN")]], page) };
      case `${API}/inventory/uoms`:
        return { body: pageAt([[uomOut("kg", "Kilogram", true), uomOut("pcs", "Piece", true)]], page) };
      case `${API}/inventory/categories`:
        return { body: pageAt([[itemCategoryOut("METALS", "cat-metals")]], page) };
      default:
        throw new Error(`The gateway was not expecting ${init.method ?? "GET"} ${url.pathname}`);
    }
  });
});

interface Case {
  name: string;
  noun: string;
  /** The dialog for this list, wired to the real hook. */
  dialog: (onImported?: (summary: ImportSummary) => void) => ReactElement;
  /** Where the entity's create goes. */
  postPath: string;
  headers: string[];
  /** A row that carries a Record ID, and one that does not: the same columns, in header order. */
  fromExport: ExcelJS.CellValue[];
  newRecord: ExcelJS.CellValue[];
  /** The first required cell of each row, which is how the tables name it. */
  exportedLabel: string;
  newLabel: string;
  /** The JSON key of that cell in the create body. */
  labelKey: string;
}

const CASES: readonly Case[] = [
  {
    name: "inventory items",
    noun: "inventory items",
    dialog: (onImported) => <Harness entity={inventoryImport} onImported={onImported} />,
    postPath: `${API}/inventory`,
    headers: ["SKU *", "Item name *", "Category *", "Warehouse *", "Unit of measure *", "Record ID"],
    fromExport: ["EXP-0001", "Exported item", "Raw materials", "Main Warehouse", "kg", "item-0f3a"],
    newRecord: ["NEW-0001", "A new item", "Raw materials", "Main Warehouse", "kg", null],
    exportedLabel: "EXP-0001",
    newLabel: "NEW-0001",
    labelKey: "sku",
  },
  {
    name: "suppliers",
    noun: "suppliers",
    dialog: (onImported) => <Harness entity={supplierImport} onImported={onImported} />,
    postPath: `${API}/suppliers`,
    headers: ["Supplier code *", "Supplier name *", "Category *", "Record ID"],
    fromExport: ["SUP-EXP", "Exported supplier", "Raw materials", "sup-77b1"],
    newRecord: ["SUP-NEW", "A new supplier", "Raw materials", null],
    exportedLabel: "SUP-EXP",
    newLabel: "SUP-NEW",
    labelKey: "code",
  },
  {
    name: "warehouses",
    noun: "warehouses",
    dialog: (onImported) => <Harness entity={warehouseImport} onImported={onImported} />,
    postPath: `${API}/warehouses`,
    headers: ["Warehouse code *", "Warehouse name *", "Record ID"],
    fromExport: ["WH-EXP", "Exported Depot", "wh-19c4"],
    newRecord: ["WH-NEW", "New Depot", null],
    exportedLabel: "WH-EXP",
    newLabel: "WH-NEW",
    labelKey: "code",
  },
  {
    name: "item categories",
    noun: "item categories",
    dialog: (onImported) => <Harness entity={itemCategoryImport} onImported={onImported} />,
    postPath: `${API}/inventory/categories`,
    headers: ["Category code *", "Category name *", "Parent category code", "Record ID"],
    fromExport: ["EXP-CAT", "Exported category", null, "cat-5e02"],
    newRecord: ["NEW-CAT", "A new category", null, null],
    exportedLabel: "EXP-CAT",
    newLabel: "NEW-CAT",
    labelKey: "code",
  },
];

describe.each(CASES)("a row with a Record ID in $name", (scenario) => {
  it(
    "is refused in the preview, is never sent, and leaves the other rows to import",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      render(scenario.dialog(onImported));

      await openDialog(user, scenario.noun);
      await uploadFile(user, await workbookFile("rows.xlsx", scenario.headers, [scenario.fromExport, scenario.newRecord]));

      const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Rows in file")).toBe("2");
      expect(stat(review, "Ready to import")).toBe("1");
      expect(stat(review, "Need fixing")).toBe("1");
      // The Record ID column is the export's own: recognised, not reported as an unknown column.
      expect(within(review).queryByText(/These columns are ignored/)).toBeNull();
      const preview = within(review).getByRole("table", { name: "Rows found in rows.xlsx" });
      const refused = rowContaining(preview, scenario.exportedLabel).textContent ?? "";
      expect(refused).toContain("Needs fixing");
      expect(refused).toContain(RECORD_ID_REFUSAL);
      expect(rowContaining(preview, scenario.newLabel).textContent).toContain("Ready");

      await user.click(within(review).getByRole("button", { name: "Import 1 row" }));
      const finished = await screen.findByRole("dialog", { name: "Import finished" }, SLOW);

      // Only the new record was sent, and the Record ID went nowhere.
      const posts = requestsTo(recorded, "POST", scenario.postPath);
      expect(posts.map((request) => jsonBody(request.init)[scenario.labelKey])).toEqual([scenario.newLabel]);
      for (const request of posts) expect(String(request.init.body)).not.toMatch(/-(0f3a|77b1|19c4|5e02)/);

      expect(stat(finished, "Created")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("1");
      const problems = within(finished).getByRole("table", { name: "Rows that were not created" });
      expect(rowContaining(problems, scenario.exportedLabel).textContent).toContain(RECORD_ID_REFUSAL);
      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    },
    10_000,
  );

  it("is refused alone: the reason is the Record ID, not the other cells of a row that must not be sent", async () => {
    const user = userEvent.setup();
    render(scenario.dialog());
    // Blank every cell but the first required one and the Record ID.
    const [code, ...others] = scenario.fromExport;
    const spoiled = [code, ...others.map((cell, index) => (index === others.length - 1 ? cell : null))];

    await openDialog(user, scenario.noun);
    await uploadFile(user, await workbookFile("rows.xlsx", scenario.headers, [spoiled]));

    const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
    const preview = within(review).getByRole("table", { name: "Rows found in rows.xlsx" });
    const text = rowContaining(preview, scenario.exportedLabel).textContent ?? "";
    expect(text).toContain(RECORD_ID_REFUSAL);
    expect(text).not.toMatch(/is required/);
    expect(stat(review, "Ready to import")).toBe("0");
    expect(within(review).getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
  }, 10_000);
});
