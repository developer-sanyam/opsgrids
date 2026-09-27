/**
 * Updating existing Operations records from a sheet, end to end and with nothing stood in for: the
 * real `useImportExport` and `ImportExportButton`, the real api client, a stubbed gateway that
 * holds the stored records, answers every write and records what it was sent, and workbooks built
 * in memory with exceljs. The tests choose "Add new and update existing" the way a person does,
 * upload a file, read the preview, click Import and hold the exact requests to what the update
 * endpoints accept. The runner spaces its requests by 350 ms, so a run takes a couple of seconds.
 */
import type * as ExcelJS from "exceljs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";

import type { ImportEntity, ImportSummary } from "@opsgrids/client";

import type { InventoryItemOut, ItemCategoryOut, SupplierOut, WarehouseOut } from "@/api/types";
import { Harness } from "./DialogHarness";
import { installBrowserGaps, jsonBody, openDialog, rowContaining, stat, uploadFile, workbookFile } from "./dialogTestKit";
import {
  inventoryItemOut,
  itemCategoryOut,
  pageAt,
  requestsTo,
  stubGateway,
  supplierOut,
  uomOut,
  warehouseOut,
} from "./entityTestKit";
import type { RecordedRequest } from "./entityTestKit";
import { inventoryImport } from "./inventory";
import { itemCategoryImport } from "./itemCategories";
import { supplierImport } from "./suppliers";
import { warehouseImport } from "./warehouses";

installBrowserGaps();

const API = "/api/v1/operations";
const TEST_TIMEOUT = 15_000;
const SLOW = { timeout: 8_000 };

afterEach(() => {
  vi.unstubAllGlobals();
});

/* ------------------------------ the gateway ------------------------------ */

interface Holdings {
  /** The pages of each list, as the server serves them. */
  inventory?: readonly (readonly InventoryItemOut[])[];
  suppliers?: readonly (readonly SupplierOut[])[];
  warehouses?: readonly (readonly WarehouseOut[])[];
  categories?: readonly (readonly ItemCategoryOut[])[];
  /** What the server says to a write, or nothing to accept it. */
  answer?: (write: Write) => { status: number; body: unknown } | undefined;
}

interface Write {
  method: string;
  path: string;
  body: Record<string, unknown>;
}

const UNIT_PAGES = [[uomOut("pcs", "Piece", true), uomOut("kg", "Kilogram", true), uomOut("l", "Litre", true)]];
const ORGANISATION_WAREHOUSES = [[warehouseOut("Main Warehouse", "WH-MAIN"), warehouseOut("Cold Store", "WH-COLD")]];
const ORGANISATION_CATEGORIES = [[itemCategoryOut("METALS", "cat-metals")], [itemCategoryOut("STEEL", "cat-steel", "cat-metals")]];

function conflict(detail: string, field?: string) {
  const errors = field === undefined ? [] : [{ field, code: "duplicate", message: detail }];
  return {
    status: 409,
    body: { type: "conflict", title: "Conflict", status: 409, detail, ...(errors.length > 0 ? { errors } : {}) },
  };
}

function unprocessable(detail: string) {
  return { status: 422, body: { type: "validation", title: "Validation failed", status: 422, detail } };
}

function gateway(holdings: Holdings): RecordedRequest[] {
  return stubGateway(({ url, init }) => {
    const method = init.method ?? "GET";
    const page = Number(url.searchParams.get("page") ?? "1");
    if (method === "GET") {
      switch (url.pathname) {
        case `${API}/inventory`:
          return { body: pageAt(holdings.inventory ?? [], page) };
        case `${API}/suppliers`:
          return { body: pageAt(holdings.suppliers ?? [], page) };
        case `${API}/warehouses`:
          return { body: pageAt(holdings.warehouses ?? ORGANISATION_WAREHOUSES, page) };
        case `${API}/inventory/categories`:
          return { body: pageAt(holdings.categories ?? ORGANISATION_CATEGORIES, page) };
        case `${API}/inventory/uoms`:
          return { body: pageAt(UNIT_PAGES, page) };
        default:
          throw new Error(`The gateway was not expecting GET ${url.pathname}`);
      }
    }
    const write: Write = { method, path: url.pathname, body: jsonBody(init) };
    return holdings.answer?.(write) ?? { status: method === "POST" ? 201 : 200, body: { id: "created-1" } };
  });
}

/** Every write the gateway received, in the order sent. */
function writes(recorded: readonly RecordedRequest[]): Write[] {
  return recorded
    .filter((request) => request.init.method === "PATCH" || request.init.method === "POST")
    .map((request) => ({ method: request.init.method ?? "", path: request.url.pathname, body: jsonBody(request.init) }));
}

function pagesAsked(recorded: readonly RecordedRequest[], path: string): (string | null)[] {
  return requestsTo(recorded, "GET", path).map((request) => request.url.searchParams.get("page"));
}

/* ------------------------------ the sheet and the dialog ------------------------------ */

/** A workbook whose header row holds the named columns, as the sample sheet writes them, and whose rows
 *  hold what each row says for them; a column a row does not mention is blank. */
function sheetFile<Payload, Item, Filters>(
  entity: ImportEntity<Payload, Item, Filters>,
  fileName: string,
  keys: readonly string[],
  rows: readonly Record<string, ExcelJS.CellValue>[],
): Promise<File> {
  const columns = keys.map((key) => {
    const column = entity.columns.find((candidate) => candidate.key === key);
    if (column === undefined) throw new Error(`${entity.id} has no column "${key}"`);
    return column;
  });
  return workbookFile(
    fileName,
    columns.map((column) => (column.required === true ? `${column.header} *` : column.header)),
    rows.map((row) => columns.map((column) => row[column.key] ?? null)),
  );
}

const UPDATE = "Add new and update existing";
const ADD_ONLY = "Add new records only";

async function chooseMode(user: UserEvent, dialog: HTMLElement, label: string): Promise<void> {
  await user.click(within(dialog).getByRole("radio", { name: label }));
}

/** Opens the dialog, chooses updating, uploads the file, and returns the preview. */
async function previewUpdate(user: UserEvent, noun: string, file: File): Promise<HTMLElement> {
  const menu = await openDialog(user, noun);
  await chooseMode(user, menu, UPDATE);
  await uploadFile(user, file);
  return screen.findByRole("dialog", { name: "Review your file" }, SLOW);
}

function previewTable(review: HTMLElement, fileName: string): HTMLElement {
  return within(review).getByRole("table", { name: `Rows found in ${fileName}` });
}

async function importAndWait(user: UserEvent, review: HTMLElement, label: string): Promise<HTMLElement> {
  await user.click(within(review).getByRole("button", { name: label }));
  return screen.findByRole("dialog", { name: "Import finished" }, SLOW);
}

function problemsTable(finished: HTMLElement): HTMLElement {
  return within(finished).getByRole("table", { name: "Rows that were not imported" });
}

/* ------------------------------ inventory items ------------------------------ */

const STEEL = inventoryItemOut({
  id: "inv-1",
  sku: "RM-STL-4501",
  name: "Cold-rolled steel coil",
  warehouse: "Main Warehouse",
  uom: "kg",
  reorder_point: 4000,
  unit_value_minor: 6240,
  item_category_id: "cat-steel",
  hsn_code: "7209",
});
/** Stored as "Nos": the API keeps the spelling it was sent, which is the unit pcs. Priced in yen. */
const CARTON = inventoryItemOut({
  id: "inv-2",
  sku: "PKG-BOX-330",
  name: "Export carton",
  category: "packaging",
  warehouse: "Cold Store",
  uom: "Nos",
  currency: "JPY",
  unit_value_minor: 500,
  item_category_id: null,
  hsn_code: "",
});
const OIL = inventoryItemOut({
  id: "inv-3",
  sku: "MRO-LUB-220",
  name: "Gear oil",
  category: "mro",
  uom: "l",
  currency: "JPY",
  unit_value_minor: 500,
  item_category_id: null,
  hsn_code: "",
});
const BOLT = inventoryItemOut({
  id: "inv-4",
  sku: "TAL-0001",
  name: "Hex bolt",
  category: "components",
  warehouse: "Cold Store",
  uom: "Nos",
  item_category_id: null,
  hsn_code: "",
});
const FITTING = inventoryItemOut({
  id: "inv-5",
  sku: "FIT-0001",
  name: "Elbow fitting",
  category: "components",
  item_category_id: "cat-steel",
  hsn_code: "0721",
});
/** Two pages, so a read that stopped at the first would call a real item new. */
const STORED_ITEMS = [[STEEL, CARTON, OIL], [BOLT, FITTING]];

const ITEM_KEYS = [
  "sku",
  "name",
  "category",
  "warehouse",
  "uom",
  "reorder_point",
  "unit_value",
  "currency",
  "opening_stock",
  "item_category",
  "hsn_code",
  "id",
];

describe("updating inventory items through the real dialog", () => {
  it(
    "previews what each row will do, then sends only the fields that differ",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      const recorded = gateway({ inventory: STORED_ITEMS });
      render(<Harness entity={inventoryImport} onImported={onImported} />);
      const file = await sheetFile(inventoryImport, "stock-update.xlsx", ITEM_KEYS, [
        // Row 2: found by its SKU in other capitals; the warehouse, unit and price are the same items
        // written another way (62.4 is 6240 paise). Only the name differs.
        {
          sku: "rm-stl-4501",
          name: "Cold-rolled steel coil, 1.5 mm",
          warehouse: "main warehouse",
          uom: "KGS",
          unit_value: 62.4,
          hsn_code: "7209",
        },
        // Row 3: stored as "Nos" and priced in yen; "pcs" is that unit and 500 yen is that price, and a
        // blank Currency cell must not turn 500 yen into 500 rupees.
        { sku: "PKG-BOX-330", uom: "pcs", unit_value: 500 },
        // Row 4: a new yen price with a blank Currency cell: 650 yen is 650 minor units, where reading
        // it as rupees would send 65000.
        { sku: "MRO-LUB-220", unit_value: 650 },
        // Row 5: a different unit and a different warehouse.
        { sku: "TAL-0001", warehouse: "Main Warehouse", uom: "kg" },
        // Row 6: [clear] on the item category and the HSN code.
        { sku: "FIT-0001", item_category: "[clear]", hsn_code: "[clear]" },
        // Row 7: nobody has this SKU, so it is added.
        {
          sku: "NEW-0001",
          name: "Brand new bracket",
          category: "Components",
          warehouse: "Cold Store",
          uom: "Nos",
          unit_value: 12.5,
        },
      ]);

      const review = await previewUpdate(user, "inventory items", file);

      // (a) The preview: counts, then an action for every row.
      expect(stat(review, "Rows in file")).toBe("6");
      expect(stat(review, "New records")).toBe("1");
      expect(stat(review, "Updates")).toBe("4");
      expect(stat(review, "No change")).toBe("1");
      expect(stat(review, "Need fixing")).toBe("0");
      expect(stat(review, "Sample rows skipped")).toBe("0");
      expect(within(review).queryByText(/missing required columns/i)).toBeNull();
      const table = previewTable(review, "stock-update.xlsx");
      const action = (text: string) => rowContaining(table, text).textContent ?? "";
      expect(action("rm-stl-4501")).toContain("Update");
      expect(action("rm-stl-4501")).toContain("Changes: Item name");
      expect(action("PKG-BOX-330")).toContain("No change");
      expect(action("MRO-LUB-220")).toContain("Changes: Unit value");
      expect(action("TAL-0001")).toContain("Changes: Warehouse, Unit of measure");
      expect(action("FIT-0001")).toContain("Changes: Item category code, HSN code");
      expect(action("NEW-0001")).toContain("New");
      expect(within(review).getByRole("button", { name: "Import 5 rows" })).toBeTruthy();
      // Both pages of the stored list were read for the comparison, and nothing was written yet.
      expect(pagesAsked(recorded, `${API}/inventory`)).toEqual(["1", "2"]);
      expect(writes(recorded)).toEqual([]);

      const finished = await importAndWait(user, review, "Import 5 rows");

      // (b) Exactly what was sent, in sheet order: changed keys only, the API's own key names, the
      // documented empty value, a unit only when it is a different unit, and nothing for row 3.
      expect(writes(recorded)).toEqual([
        { method: "PATCH", path: `${API}/inventory/inv-1`, body: { name: "Cold-rolled steel coil, 1.5 mm" } },
        { method: "PATCH", path: `${API}/inventory/inv-3`, body: { unit_value_minor: 650 } },
        { method: "PATCH", path: `${API}/inventory/inv-4`, body: { warehouse: "Main Warehouse", uom: "kg" } },
        { method: "PATCH", path: `${API}/inventory/inv-5`, body: { item_category_id: null, hsn_code: "" } },
        {
          method: "POST",
          path: `${API}/inventory`,
          body: {
            sku: "NEW-0001",
            name: "Brand new bracket",
            category: "components",
            warehouse: "Cold Store",
            uom: "pcs",
            unit_value_minor: 1250,
            currency: "INR",
          },
        },
      ]);

      // (c) The result: counts, the sentence, and nothing listed as a problem.
      expect(stat(finished, "Created")).toBe("1");
      expect(stat(finished, "Updated")).toBe("4");
      expect(stat(finished, "Unchanged")).toBe("1");
      expect(stat(finished, "Failed")).toBe("0");
      expect(stat(finished, "Invalid")).toBe("0");
      expect(within(finished).getByText("Everything imported. 4 updated, 1 created, 1 unchanged.")).toBeTruthy();
      expect(within(finished).queryByRole("table", { name: "Rows that were not imported" })).toBeNull();

      // (d) The screen is told once, so it reloads its list.
      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 1, updated: 4, unchanged: 1, failed: 0, invalid: 0, cancelled: false }),
      );
      await user.click(within(finished).getByRole("button", { name: "Done" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(onImported).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "accepts a sheet of only a SKU and one column, and changes nothing but that column",
    async () => {
      const user = userEvent.setup();
      const recorded = gateway({ inventory: STORED_ITEMS });
      render(<Harness entity={inventoryImport} />);
      const file = await sheetFile(inventoryImport, "reorder.xlsx", ["sku", "reorder_point"], [
        { sku: "RM-STL-4501", reorder_point: 5000 },
        { sku: "MRO-LUB-220", reorder_point: 0 },
      ]);

      const review = await previewUpdate(user, "inventory items", file);

      expect(stat(review, "Updates")).toBe("2");
      expect(stat(review, "Need fixing")).toBe("0");
      expect(within(review).queryByText(/missing required columns/i)).toBeNull();
      const table = previewTable(review, "reorder.xlsx");
      expect(rowContaining(table, "RM-STL-4501").textContent).toContain("Changes: Reorder point");

      await importAndWait(user, review, "Import 2 rows");

      // A reorder point of zero is a value, not a blank cell.
      expect(writes(recorded)).toEqual([
        { method: "PATCH", path: `${API}/inventory/inv-1`, body: { reorder_point: 5000 } },
        { method: "PATCH", path: `${API}/inventory/inv-3`, body: { reorder_point: 0 } },
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses a row that would change what cannot change, or names what is not there, and sends nothing",
    async () => {
      const user = userEvent.setup();
      const recorded = gateway({ inventory: STORED_ITEMS });
      render(<Harness entity={inventoryImport} />);
      const file = await sheetFile(inventoryImport, "refusals.xlsx", ITEM_KEYS, [
        // Row 2: the Record ID names the steel coil, and the SKU names something else.
        { id: "inv-1", sku: "WRONG-SKU" },
        // Row 3: the yen carton, given a currency of dollars.
        { sku: "PKG-BOX-330", currency: "USD" },
        // Row 4: stock changes only through an adjustment.
        { sku: "MRO-LUB-220", opening_stock: 100 },
        // Row 5: a Record ID that no item has.
        { id: "inv-404", name: "Ghost" },
        // Row 6: a warehouse the organisation does not have.
        { sku: "TAL-0001", warehouse: "Atlantis Depot" },
        // Row 7: a unit the organisation does not have; the server would create one.
        { sku: "FIT-0001", uom: "furlongs" },
        // Row 8: a name cannot be emptied.
        { sku: "RM-STL-4501", name: "[clear]" },
      ]);

      const review = await previewUpdate(user, "inventory items", file);

      expect(stat(review, "Rows in file")).toBe("7");
      expect(stat(review, "Need fixing")).toBe("7");
      expect(stat(review, "Updates")).toBe("0");
      expect(stat(review, "New records")).toBe("0");
      const table = previewTable(review, "refusals.xlsx");
      const refusal = (text: string) => {
        const row = rowContaining(table, text).textContent ?? "";
        expect(row).toContain("Needs fixing");
        return row;
      };
      expect(refusal("WRONG-SKU")).toContain(
        "SKU cannot be changed once the record exists (it is 'RM-STL-4501'). Leave the cell as it is, or blank.",
      );
      expect(refusal("PKG-BOX-330")).toContain(
        "Currency cannot be changed once the record exists (it is 'JPY'). Leave the cell as it is, or blank.",
      );
      expect(refusal("MRO-LUB-220")).toContain("Opening stock cannot be changed once the record exists. Leave the cell blank.");
      expect(refusal("Ghost")).toContain(
        "That Record ID is not in this list: the record may have been deleted, or the file came from another organisation.",
      );
      expect(refusal("Atlantis Depot")).toContain(
        "'Atlantis Depot' is not one of the accepted values for Warehouse (see the Lists sheet). " +
          "Use a warehouse from the Lists sheet, matched by name ignoring capitals, or leave the cell blank to keep the item where it is.",
      );
      expect(refusal("furlongs")).toContain(
        "'furlongs' is not one of the accepted values for Unit of measure (see the Lists sheet). " +
          "Use a unit from the Lists sheet, or a common spelling of one such as Nos, or leave the cell blank to keep the item's unit.",
      );
      expect(refusal("[clear]")).toContain("Item name cannot be cleared");
      // Nothing can be sent, so the button says so and cannot be pressed.
      const button = within(review).getByRole("button", { name: "Nothing to change" });
      expect(button.hasAttribute("disabled")).toBe(true);
      expect(writes(recorded)).toEqual([]);
    },
    TEST_TIMEOUT,
  );

  it(
    "checks the same file again in the other mode, without reading it again or asking for the records twice",
    async () => {
      const user = userEvent.setup();
      const recorded = gateway({ inventory: STORED_ITEMS });
      render(<Harness entity={inventoryImport} />);
      const file = await sheetFile(inventoryImport, "mixed.xlsx", ["sku", "name", "category", "warehouse", "uom", "id"], [
        // Row 2: an export's row, carrying its Record ID.
        { id: "inv-1", name: "Renamed steel coil" },
        // Row 3: the SKU of an item that exists, with every required cell.
        {
          sku: "PKG-BOX-330",
          name: "Export carton",
          category: "Packaging",
          warehouse: "Cold Store",
          uom: "pcs",
        },
      ]);

      // Adding only, which is what the dialog starts in: the Record ID row is refused and says how to update it.
      const menu = await openDialog(user, "inventory items");
      expect(within(menu).getByRole("radio", { name: ADD_ONLY })).toHaveProperty("checked", true);
      await uploadFile(user, file);
      let review = await screen.findByRole("dialog", { name: "Review your file" }, SLOW);
      expect(stat(review, "Ready to import")).toBe("1");
      expect(stat(review, "Need fixing")).toBe("1");
      let table = previewTable(review, "mixed.xlsx");
      expect(rowContaining(table, "Renamed steel coil").textContent).toContain(
        "This row has a Record ID, so it came from an export and is already in the system. " +
          "Clear the Record ID cell to add it as a new record. Switch to Add new and update existing to update it.",
      );
      expect(rowContaining(table, "PKG-BOX-330").textContent).toContain("New");
      expect(requestsTo(recorded, "GET", `${API}/inventory`)).toHaveLength(0);

      // Switching to updating: the same file, checked against the stored items.
      await chooseMode(user, review, UPDATE);
      await waitFor(() => expect(stat(screen.getByRole("dialog", { name: "Review your file" }), "Updates")).toBe("1"));
      review = screen.getByRole("dialog", { name: "Review your file" });
      expect(stat(review, "New records")).toBe("0");
      expect(stat(review, "No change")).toBe("1");
      expect(stat(review, "Need fixing")).toBe("0");
      table = previewTable(review, "mixed.xlsx");
      expect(rowContaining(table, "Renamed steel coil").textContent).toContain("Changes: Item name");
      expect(rowContaining(table, "PKG-BOX-330").textContent).toContain("No change");
      expect(pagesAsked(recorded, `${API}/inventory`)).toEqual(["1", "2"]);

      // And back: adding only again, and updating once more, need no second read of the list.
      await chooseMode(user, review, ADD_ONLY);
      await waitFor(() => expect(within(screen.getByRole("dialog", { name: "Review your file" })).queryByText("Updates")).toBeNull());
      review = screen.getByRole("dialog", { name: "Review your file" });
      expect(stat(review, "Need fixing")).toBe("1");
      await chooseMode(user, review, UPDATE);
      await waitFor(() => expect(stat(screen.getByRole("dialog", { name: "Review your file" }), "Updates")).toBe("1"));
      expect(pagesAsked(recorded, `${API}/inventory`)).toEqual(["1", "2"]);
      expect(writes(recorded)).toEqual([]);
    },
    TEST_TIMEOUT,
  );
});

/* ------------------------------ warehouses ------------------------------ */

const MAIN = warehouseOut("Main Warehouse", "WH-MAIN");
const COLD = warehouseOut("Cold Store", "WH-COLD");
const EAST = { ...warehouseOut("East Depot", "WH-EAST"), location: "Kolkata" };
const WEST = { ...warehouseOut("West Depot", "WH-WEST"), location: "Surat" };

describe("updating warehouses through the real dialog", () => {
  it(
    "reports a rename onto a name that exists as a failed row, never as skipped, and refuses a near-duplicate before sending",
    async () => {
      const user = userEvent.setup();
      const onImported = vi.fn<(summary: ImportSummary) => void>();
      const recorded = gateway({
        warehouses: [[MAIN, COLD, EAST, WEST]],
        answer: ({ path, body }) =>
          path === `${API}/warehouses/wh-WH-MAIN` && body.name === "Cold Store"
            ? conflict("A warehouse named 'Cold Store' already exists", "name")
            : undefined,
      });
      render(<Harness entity={warehouseImport} onImported={onImported} />);
      const file = await sheetFile(warehouseImport, "warehouses-update.xlsx", ["code", "name", "location"], [
        // Row 2: the server refuses this name, which exactly matches another warehouse.
        { code: "WH-MAIN", name: "Cold Store" },
        // Row 3: a new location.
        { code: "WH-COLD", location: "Nashik, Maharashtra" },
        // Row 4: a name that only differs from Cold Store in capitals, which the server would accept.
        { code: "WH-EAST", name: "cold store" },
        // Row 5: [clear] empties the location.
        { code: "WH-WEST", location: "[clear]" },
      ]);

      const review = await previewUpdate(user, "warehouses", file);

      expect(stat(review, "Updates")).toBe("3");
      expect(stat(review, "Need fixing")).toBe("1");
      const table = previewTable(review, "warehouses-update.xlsx");
      expect(rowContaining(table, "WH-EAST").textContent).toContain(
        "A warehouse called 'Cold Store' already exists; names that differ only in capitals or spacing are not accepted.",
      );
      expect(rowContaining(table, "WH-WEST").textContent).toContain("Changes: Location");

      const finished = await importAndWait(user, review, "Import 3 rows");

      expect(writes(recorded)).toEqual([
        { method: "PATCH", path: `${API}/warehouses/wh-WH-MAIN`, body: { name: "Cold Store" } },
        { method: "PATCH", path: `${API}/warehouses/wh-WH-COLD`, body: { location: "Nashik, Maharashtra" } },
        { method: "PATCH", path: `${API}/warehouses/wh-WH-WEST`, body: { location: "" } },
      ]);
      expect(stat(finished, "Updated")).toBe("2");
      expect(stat(finished, "Failed")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("1");
      expect(within(finished).queryByText("Skipped, already exist")).toBeNull();
      expect(within(finished).getByText("2 updated, 2 failed.")).toBeTruthy();
      const problems = problemsTable(finished);
      const collision = rowContaining(problems, "WH-MAIN").textContent ?? "";
      expect(collision).toMatch(/failed/i);
      expect(collision).not.toMatch(/skipped/i);
      expect(collision).toContain("A warehouse named 'Cold Store' already exists");
      await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
      expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ updated: 2, failed: 1, invalid: 1 }));
    },
    TEST_TIMEOUT,
  );
});

/* ------------------------------ suppliers ------------------------------ */

describe("updating suppliers through the real dialog", () => {
  it(
    "finds a supplier by its code in other capitals, empties an email with an empty string, and refuses a currency or a since it cannot change",
    async () => {
      const user = userEvent.setup();
      const recorded = gateway({
        suppliers: [
          [
            supplierOut(),
            supplierOut({ id: "sup-2", code: "SUP-APX", name: "Apex Packaging" }),
            supplierOut({ id: "sup-3", code: "SUP-CRL", name: "Crestline Logistics" }),
          ],
        ],
      });
      render(<Harness entity={supplierImport} />);
      const file = await sheetFile(supplierImport, "suppliers-update.xlsx", ["code", "name", "currency", "contact_name", "contact_email", "since"], [
        // Row 2: found by "sup-mer"; a new contact, the email emptied, a new date.
        { code: "sup-mer", contact_name: "Farhan Qureshi", contact_email: "[clear]", since: "2020-01-15" },
        // Row 3: dollars for a rupee supplier.
        { code: "SUP-APX", currency: "USD" },
        // Row 4: a since date cannot be emptied (the server answers a null with a 500).
        { code: "SUP-CRL", since: "[clear]" },
      ]);

      const review = await previewUpdate(user, "suppliers", file);

      expect(stat(review, "Updates")).toBe("1");
      expect(stat(review, "Need fixing")).toBe("2");
      const table = previewTable(review, "suppliers-update.xlsx");
      expect(rowContaining(table, "sup-mer").textContent).toContain("Changes: Contact name, Contact email, Supplier since");
      expect(rowContaining(table, "SUP-APX").textContent).toContain(
        "Currency cannot be changed once the record exists (it is 'INR'). Leave the cell as it is, or blank.",
      );
      expect(rowContaining(table, "SUP-CRL").textContent).toContain("Supplier since cannot be cleared");

      const finished = await importAndWait(user, review, "Import 1 row");

      expect(writes(recorded)).toEqual([
        {
          method: "PATCH",
          path: `${API}/suppliers/sup-1`,
          body: { contact_name: "Farhan Qureshi", contact_email: "", since: "2020-01-15" },
        },
      ]);
      expect(stat(finished, "Updated")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("2");
      expect(within(finished).getByText("1 updated, 2 failed.")).toBeTruthy();
    },
    TEST_TIMEOUT,
  );
});

/* ------------------------------ item categories ------------------------------ */

const METALS = itemCategoryOut("METALS", "cat-metals");
const STEEL_CATEGORY = itemCategoryOut("STEEL", "cat-steel", "cat-metals");
const ALUMINIUM = itemCategoryOut("ALUMINIUM", "cat-alu", "cat-metals");
const POLYMERS = itemCategoryOut("POLYMERS", "cat-poly");

describe("updating item categories through the real dialog", () => {
  it(
    "moves a category to the top level with null, re-parents by code, and shows the server's refusal of a loop on its own row",
    async () => {
      const user = userEvent.setup();
      const recorded = gateway({
        categories: [[METALS, STEEL_CATEGORY, ALUMINIUM, POLYMERS]],
        answer: ({ path, body }) =>
          path === `${API}/inventory/categories/cat-metals` && body.parent_id === "cat-steel"
            ? unprocessable("That would make the category tree circular")
            : undefined,
      });
      render(<Harness entity={itemCategoryImport} />);
      const file = await sheetFile(itemCategoryImport, "categories-update.xlsx", ["code", "name", "parent"], [
        // Row 2: to the top level.
        { code: "ALUMINIUM", parent: "[clear]" },
        // Row 3: under another parent, by its code in other capitals.
        { code: "POLYMERS", parent: "metals" },
        // Row 4: METALS under its own child.
        { code: "METALS", parent: "STEEL" },
        // Row 5: a parent that does not exist, with the update hint.
        { code: "STEEL", parent: "NOPE" },
      ]);

      const review = await previewUpdate(user, "item categories", file);

      expect(stat(review, "Updates")).toBe("3");
      expect(stat(review, "Need fixing")).toBe("1");
      const table = previewTable(review, "categories-update.xlsx");
      expect(rowContaining(table, "ALUMINIUM").textContent).toContain("Changes: Parent category code");
      expect(rowContaining(table, "NOPE").textContent).toContain(
        "'NOPE' is not one of the accepted values for Parent category code (see the Lists sheet). " +
          "The parent must already exist, so add it in an earlier run. " +
          "A blank cell keeps the category's parent, and [clear] moves it to the top level.",
      );

      const finished = await importAndWait(user, review, "Import 3 rows");

      expect(writes(recorded)).toEqual([
        { method: "PATCH", path: `${API}/inventory/categories/cat-alu`, body: { parent_id: null } },
        { method: "PATCH", path: `${API}/inventory/categories/cat-poly`, body: { parent_id: "cat-metals" } },
        { method: "PATCH", path: `${API}/inventory/categories/cat-metals`, body: { parent_id: "cat-steel" } },
      ]);
      expect(stat(finished, "Updated")).toBe("2");
      expect(stat(finished, "Failed")).toBe("1");
      expect(stat(finished, "Invalid")).toBe("1");
      const loop = rowContaining(problemsTable(finished), "METALS").textContent ?? "";
      expect(loop).toMatch(/failed/i);
      expect(loop).toContain("That would make the category tree circular");
    },
    TEST_TIMEOUT,
  );
});
