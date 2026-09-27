/**
 * The sample sheet each Operations list offers, downloaded through the real dialog and hook and
 * read back with exceljs: what a customer opens first. The header row, the examples, the
 * Instructions sheet, the drop-downs and the Lists sheet are all built by the engine from the
 * entity definitions, so these tests pin what actually reaches the file, for an organisation whose
 * own warehouses, units and categories differ from the examples.
 */
import * as ExcelJS from "exceljs";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ImportEntity } from "@opsgrids/client";

import { Harness } from "./DialogHarness";
import {
  XLSX_MIME,
  columnLetter,
  columnTexts,
  headerRow,
  installBrowserGaps,
  openDialog,
  readDownload,
  sheetOf,
  stat,
  textRows,
  uploadFile,
  validationOn,
} from "./dialogTestKit";
import type { Download } from "./dialogTestKit";
import { itemCategoryOut, pageAt, stubGateway, uomOut, warehouseOut } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { warehouseImport } from "./warehouses";

const downloads = installBrowserGaps();

const API = "/api/v1/operations";
const SLOW = { timeout: 8_000 };

beforeEach(() => {
  downloads.length = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * An organisation that has no "Main Warehouse" (the name every inventory example uses), a
 * warehouse whose name holds a comma (so its drop-down cannot be written inline), and its own
 * unit and item category lists.
 */
function organisationGateway() {
  const warehouses = [[warehouseOut("Pune Plant, Unit 2", "WH-PUN"), warehouseOut("Cold Store", "WH-COLD")]];
  const units = [[uomOut("pcs", "Piece", true), uomOut("kg", "Kilogram", true), uomOut("l", "Litre", true), uomOut("mt", "MT", false)]];
  const categories = [[itemCategoryOut("METALS", "cat-metals")], [itemCategoryOut("STEEL", "cat-steel", "cat-metals")]];
  return stubGateway(({ url, init }) => {
    const page = Number(url.searchParams.get("page") ?? "1");
    switch (`${init.method ?? "GET"} ${url.pathname}`) {
      case `GET ${API}/warehouses`:
        return { body: pageAt(warehouses, page) };
      case `GET ${API}/inventory/uoms`:
        return { body: pageAt(units, page) };
      case `GET ${API}/inventory/categories`:
        return { body: pageAt(categories, page) };
      default:
        throw new Error(`The gateway was not expecting ${init.method ?? "GET"} ${url.pathname}`);
    }
  });
}

/** Opens the dialog for `noun`, asks for the sample sheet and returns the file the browser was given. */
async function downloadSample(noun: string): Promise<Download> {
  const user = userEvent.setup();
  const dialog = await openDialog(user, noun);
  await user.click(within(dialog).getByRole("button", { name: "Download sample sheet" }));
  await waitFor(() => expect(downloads).toHaveLength(1), SLOW);
  const [download] = downloads;
  if (download === undefined) throw new Error("nothing was downloaded");
  return download;
}

/** The sheet's header row with the asterisks the sample sheet puts on required columns. */
function sampleHeaders(entity: Pick<ImportEntity<unknown>, "columns">): string[] {
  return entity.columns
    .filter((column) => column.readOnly !== true)
    .map((column) => (column.required === true ? `${column.header} *` : column.header));
}

/* ------------------------------ every entity ------------------------------ */

interface SampleCase {
  name: string;
  entity: Pick<ImportEntity<unknown>, "columns" | "sampleRows" | "notes" | "noun" | "fileStem">;
  page: () => ReactElement;
  hasLists: boolean;
  /** What the update rules on Instructions name for this list. */
  updating: { keyHeader: string; emptied: string; fixed: string };
}

const CASES: readonly SampleCase[] = [
  {
    name: "inventory items",
    entity: inventoryImport,
    page: () => <Harness entity={inventoryImport} />,
    hasLists: true,
    updating: { keyHeader: "SKU", emptied: "Item category code, HSN code", fixed: "SKU, Currency, Opening stock" },
  },
  {
    name: "suppliers",
    entity: supplierImport,
    page: () => <Harness entity={supplierImport} />,
    hasLists: false,
    updating: {
      keyHeader: "Supplier code",
      emptied: "Contact name, Contact email, Location",
      fixed: "Supplier code, Currency",
    },
  },
  {
    name: "warehouses",
    entity: warehouseImport,
    page: () => <Harness entity={warehouseImport} />,
    hasLists: false,
    updating: { keyHeader: "Warehouse code", emptied: "Location", fixed: "Warehouse code" },
  },
  {
    name: "item categories",
    entity: itemCategoryImport,
    page: () => <Harness entity={itemCategoryImport} />,
    hasLists: true,
    updating: { keyHeader: "Category code", emptied: "Parent category code", fixed: "Category code" },
  },
];

describe.each(CASES)("the $name sample sheet", ({ entity, page, hasLists, updating }) => {
  it("is named for the list and holds Import and Instructions, and Lists only where a column has a list", async () => {
    organisationGateway();
    render(page());

    const download = await downloadSample(entity.noun);
    const workbook = await readDownload(download);

    expect(download.fileName).toBe(`${entity.fileStem}-sample.xlsx`);
    expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(
      hasLists ? ["Import", "Instructions", "Lists"] : ["Import", "Instructions"],
    );
  });

  it("is headed by the importable columns with the required ones starred, and none of the read-only ones", async () => {
    organisationGateway();
    render(page());

    const workbook = await readDownload(await downloadSample(entity.noun));
    const headers = headerRow(sheetOf(workbook, "Import"));

    expect(headers).toEqual(sampleHeaders(entity));
    for (const column of entity.columns.filter((c) => c.readOnly === true)) {
      expect(headers.join("|")).not.toContain(column.header);
    }
    for (const column of entity.columns.filter((c) => c.required === true)) {
      expect(headers).toContain(`${column.header} *`);
    }
  });

  it("carries the example rows, and explains every column and every rule of the entity on Instructions", async () => {
    organisationGateway();
    render(page());

    const workbook = await readDownload(await downloadSample(entity.noun));
    const firstColumn = entity.columns.find((column) => column.readOnly !== true);
    const [, ...examples] = columnTexts(sheetOf(workbook, "Import"), 1);
    expect(examples).toEqual(entity.sampleRows.map((sample) => String(sample[firstColumn?.key ?? ""])));

    const instructions = textRows(sheetOf(workbook, "Instructions"));
    const explained = new Map(instructions.map((row) => [row[0], row]));
    for (const column of entity.columns.filter((c) => c.readOnly !== true)) {
      const row = explained.get(column.header);
      expect(row, `Instructions has no row for ${column.header}`).toBeDefined();
      expect(row?.[1]).toBe(column.required === true ? "Yes" : "No");
      expect(row).toContain(column.help);
    }
    const lines = instructions.flat();
    for (const note of entity.notes ?? []) expect(lines).toContain(note);
    expect(lines).toContain("Columns marked * are required.");
    expect(lines).toContain("Up to 1000 rows per file.");
    // The engine explains the Record ID twice: how an update finds its record, and in the general
    // rules why an exported row is not added. The entity's notes do not say it again: they mention
    // it once, to say what to do about a key typed in other capitals, which the server tells apart.
    const notes = entity.notes ?? [];
    expect(lines.filter((line) => /Record ID/.test(line) && !notes.includes(line))).toEqual([
      expect.stringContaining("A row is matched to an existing record by its Record ID"),
      "Rows exported from the system carry a Record ID; those rows are not imported, delete the Record ID to add a row as a new record.",
    ]);
    expect(notes.filter((note) => /Record ID/.test(note))).toEqual([
      expect.stringContaining("differs only in capitals is refused, naming the stored spelling: use that spelling, or the Record ID."),
    ]);
  });

  it("explains updating under its own heading: how a row finds its record, what blank and [clear] do, and what cannot change", async () => {
    organisationGateway();
    render(page());

    const workbook = await readDownload(await downloadSample(entity.noun));
    const lines = textRows(sheetOf(workbook, "Instructions")).flat();

    const heading = lines.indexOf(`Updating existing ${entity.noun}`);
    expect(heading, "Instructions has no update section").toBeGreaterThan(-1);
    // The wording of the engine's own rules (how a key is matched, what the warning says) is held by
    // the engine's tests; what belongs to the list is held here: its key, and what it can and cannot change.
    expect(lines.slice(heading + 1, lines.indexOf("General rules", heading))).toEqual(
      expect.arrayContaining([
        'Choose "Add new and update existing" in the import dialog to use these rules.',
        expect.stringMatching(
          new RegExp(`^A row is matched to an existing record by its Record ID .*A row without one is matched by ${updating.keyHeader}`),
        ),
        expect.stringMatching(/^A blank cell leaves the field alone\./),
        `Can be emptied with [clear]: ${updating.emptied}.`,
        `Cannot be changed once created: ${updating.fixed}. A row whose cell differs from the record is refused.`,
        expect.stringMatching(/^Warning: updating overwrites live data and cannot be undone\./),
      ]),
    );
    // Adding only still skips what exists, and the sheet says which mode that is.
    expect(lines).toContain(
      `With "Add new records only", existing ${entity.noun} are skipped, never overwritten. Importing the same file again is safe.`,
    );
  });
});

/* ------------------------------ inventory items in detail ------------------------------ */

describe("the inventory sample sheet for an organisation of its own", () => {
  it("swaps the example warehouse for one the organisation has, and writes the other examples as typed", async () => {
    organisationGateway();
    render(<Harness entity={inventoryImport} />);

    const sheet = sheetOf(await readDownload(await downloadSample("inventory items")), "Import");

    expect(headerRow(sheet)).toEqual([
      "SKU *",
      "Item name *",
      "Category *",
      "Warehouse *",
      "Unit of measure *",
      "Reorder point",
      "Unit value",
      "Currency",
      "Opening stock",
      "Item category code",
      "HSN code",
      "Track lots",
      "Track rolls",
      "Track expiry",
      "Track quality",
      "Active",
    ]);
    expect(columnTexts(sheet, 1)).toEqual(["SKU *", "RM-STL-4501", "PKG-BOX-330", "MRO-LUB-220"]);
    expect(columnTexts(sheet, 4)).toEqual([
      "Warehouse *",
      "Pune Plant, Unit 2",
      "Pune Plant, Unit 2",
      "Pune Plant, Unit 2",
    ]);
    expect(columnTexts(sheet, 3)).toEqual(["Category *", "Raw materials", "Packaging", "MRO"]);
    expect(columnTexts(sheet, 5)).toEqual(["Unit of measure *", "kg", "pcs", "l"]);

    // Money is a number in major units with its currency's decimals; the HSN is text with its digits.
    expect(sheet.getCell("G2").value).toBe(62.4);
    expect(sheet.getCell("G2").numFmt).toBe("#,##0.00");
    expect(sheet.getCell("G4").value).toBe(385.5);
    expect(sheet.getCell("K2").value).toBe("7209");
    expect(sheet.getCell("K2").type).toBe(ExcelJS.ValueType.String);
    expect(sheet.getCell("K2").numFmt).toBe("@");
    expect(sheet.getCell("L4").value).toBe("Yes");
    expect(sheet.getCell("L2").value).toBeNull();
  });

  it("puts a drop-down on every enum, yes/no and lookup column, and on no other", async () => {
    organisationGateway();
    render(<Harness entity={inventoryImport} />);

    const sheet = sheetOf(await readDownload(await downloadSample("inventory items")), "Import");
    const headers = headerRow(sheet);
    const dropdown = (header: string) => {
      const index = headers.findIndex((candidate) => candidate.replace(/ \*$/, "") === header);
      return validationOn(sheet, columnLetter(index + 1));
    };

    expect(dropdown("Category")).toMatchObject({
      type: "list",
      formulae: ['"Raw materials,Packaging,Components,MRO,Logistics,Services"'],
    });
    // A warehouse name with a comma cannot sit in an inline list, so its drop-down reads the Lists sheet.
    expect(dropdown("Warehouse")).toMatchObject({ type: "list", formulae: ["Lists!$A$2:$A$3"] });
    expect(dropdown("Unit of measure")).toMatchObject({ type: "list", formulae: ['"pcs,kg,l,MT"'] });
    expect(dropdown("Item category code")).toMatchObject({ type: "list", formulae: ['"METALS,STEEL"'] });
    for (const yesNo of ["Track lots", "Track rolls", "Track expiry", "Track quality", "Active"]) {
      expect(dropdown(yesNo)).toMatchObject({ type: "list", formulae: ['"Yes,No"'] });
    }
    for (const free of ["SKU", "Item name", "Reorder point", "Unit value", "Currency", "Opening stock", "HSN code"]) {
      expect(dropdown(free)).toBeUndefined();
    }
  });

  it("lists what is valid for this organisation on the Lists sheet, one column per lookup", async () => {
    organisationGateway();
    render(<Harness entity={inventoryImport} />);

    const lists = sheetOf(await readDownload(await downloadSample("inventory items")), "Lists");

    expect(headerRow(lists)).toEqual(["Warehouse", "Unit of measure", "Item category code"]);
    expect(columnTexts(lists, 1)).toEqual(["Warehouse", "Pune Plant, Unit 2", "Cold Store"]);
    expect(columnTexts(lists, 2)).toEqual(["Unit of measure", "pcs", "kg", "l", "MT"]);
    expect(columnTexts(lists, 3)).toEqual(["Item category code", "METALS", "STEEL"]);
  });

  it("shows the swapped warehouse as the example on Instructions, and points the column at the Lists sheet", async () => {
    organisationGateway();
    render(<Harness entity={inventoryImport} />);

    const workbook = await readDownload(await downloadSample("inventory items"));
    const rows = textRows(sheetOf(workbook, "Instructions"));
    const warehouse = rows.find((row) => row[0] === "Warehouse");

    expect(warehouse?.[1]).toBe("Yes");
    expect(warehouse?.[2]).toBe("One of the names on the Lists sheet");
    expect(warehouse?.[3]).toContain("Lists sheet");
    expect(warehouse?.[4]).toBe("Pune Plant, Unit 2");
    const category = rows.find((row) => row[0] === "Category");
    expect(category?.[3]).toBe("Raw materials, Packaging, Components, MRO, Logistics, Services");
    const reorderPoint = rows.find((row) => row[0] === "Reorder point");
    expect(reorderPoint?.[2]).toBe("Whole number, from 0 to 2147483");
    const unit = rows.find((row) => row[0] === "Unit of measure");
    expect(unit?.[3]).toContain("Lists sheet");
    expect(unit?.[5]).toContain("Nos, Kgs or Ltr");
    const openingStock = rows.find((row) => row[0] === "Opening stock");
    expect(openingStock?.[1]).toBe("No");
    expect(openingStock?.[2]).toBe("Whole number, from 0 to 1000000000000");
  });

  it("is recognised when uploaded again unchanged: every example is skipped, nothing can be imported", async () => {
    const user = userEvent.setup();
    organisationGateway();
    render(<Harness entity={inventoryImport} />);

    const sample = await downloadSample("inventory items");
    await uploadFile(user, new File([sample.blob], "inventory-items-sample.xlsx", { type: XLSX_MIME }));

    const review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
    expect(stat(review, "Rows in file")).toBe("3");
    expect(stat(review, "Ready to import")).toBe("0");
    expect(stat(review, "Need fixing")).toBe("0");
    expect(stat(review, "Sample rows skipped")).toBe("3");
    expect(within(review).getByText("Only the sample rows are in this file.")).toBeTruthy();
    const button = within(review).getByRole("button", { name: "Import 0 rows" });
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});

/* ------------------------------ item categories in detail ------------------------------ */

describe("the item category sample sheet for an organisation of its own", () => {
  it("offers the organisation's own categories as parents, and leaves the examples top-level", async () => {
    organisationGateway();
    render(<Harness entity={itemCategoryImport} />);

    const workbook = await readDownload(await downloadSample("item categories"));
    const sheet = sheetOf(workbook, "Import");

    expect(headerRow(sheet)).toEqual(["Category code *", "Category name *", "Parent category code"]);
    expect(columnTexts(sheet, 1)).toEqual(["Category code *", "METALS", "POLYMERS", "FASTENERS"]);
    expect(columnTexts(sheet, 3)).toEqual(["Parent category code"]);
    expect(validationOn(sheet, "C")).toMatchObject({ type: "list", formulae: ['"METALS,STEEL"'] });
    expect(validationOn(sheet, "A")).toBeUndefined();

    const lists = sheetOf(workbook, "Lists");
    expect(columnTexts(lists, 1)).toEqual(["Parent category code", "METALS", "STEEL"]);
  });
});
