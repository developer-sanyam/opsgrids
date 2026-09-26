/**
 * The assets import and export end to end, the way a person meets them: the real
 * `useImportExport` hook from `@opsgrids/client` behind the real `ImportExportButton` dialog from
 * `@opsgrids/ui`, with only the gateway stubbed. A workbook is built in memory, chosen in the
 * dialog, previewed, imported row by row and reported; the export and the sample sheet are read
 * back with exceljs.
 *
 * The runner spaces requests by 350 ms, so an import of four rows takes about a second here.
 */
import { useImportExport } from "@opsgrids/client";
import type { ImportSummary } from "@opsgrids/client";
import { ImportExportButton } from "@opsgrids/ui";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExcelJS from "exceljs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "@/api/types";

import { assetsEntity } from "./assets";
import type { AssetFilters } from "./assets";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const TEST_TIMEOUT = 15_000;

// ------------------------------------------------------------------------------------ jsdom gaps

interface Download {
  name: string;
  blob: Blob | undefined;
}

let downloads: Download[];

/** jsdom's `Blob` has no `arrayBuffer()`; the engine and this file both read files through it. */
function polyfillBlobArrayBuffer(): void {
  if (typeof Blob.prototype.arrayBuffer === "function") return;
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (reader.result instanceof ArrayBuffer) resolve(reader.result);
        else reject(new Error("The blob did not read as bytes"));
      };
      reader.onerror = () => reject(reader.error ?? new Error("The blob could not be read"));
      reader.readAsArrayBuffer(this);
    });
  };
}

beforeEach(() => {
  polyfillBlobArrayBuffer();
  downloads = [];
  const blobs = new Map<string, Blob>();
  URL.createObjectURL = vi.fn((blob: Blob) => {
    const url = `blob:test/${blobs.size + 1}`;
    blobs.set(url, blob);
    return url;
  });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
    downloads.push({ name: this.download, blob: blobs.get(this.href) });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------------ the gateway

interface Recorded {
  method: string;
  path: string;
  params: URLSearchParams;
  body: unknown;
}

function asset(over: Partial<Asset>): Asset {
  return {
    tag: "LT-0000",
    name: "Something",
    type: "laptop",
    status: "in_use",
    owner: null,
    location: "",
    serial: "",
    purchased_at: null,
    warranty_expires_at: null,
    cost_minor: 0,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

/** The register, served two to a page whatever `size` the caller asks for. Text that looks like a formula is data here. */
const REGISTER: Asset[] = [
  asset({
    tag: "LT-0001",
    name: "=1+1",
    owner: "+91 98765 43210",
    location: "@SUM(A1:A9)",
    serial: "-2+3",
    purchased_at: "2025-08-14",
    cost_minor: 8450000,
    cost_currency: "INR",
    open_incident_ids: ["INC-1", "INC-2"],
  }),
  asset({ tag: "MN-0002", name: "Dell U2723QE monitor", type: "monitor", cost_minor: 1250, cost_currency: "KWD" }),
  asset({ tag: "SW-0003", name: "Design licences", type: "license", status: "retired", cost_minor: 250000, cost_currency: "JPY" }),
];
const PER_PAGE = 2;

function problem(status: number, title: string, detail: string, errors?: unknown): Response {
  return new Response(JSON.stringify({ status, title, detail, ...(errors === undefined ? {} : { errors }) }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });
}

function ok(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * A gateway with the register and `POST /itsm/assets`. A tag already on the register answers 409;
 * NW-0009 answers 422; anything else is created.
 */
function stubGateway() {
  const requests: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, "http://gateway.test");
      const method = init?.method ?? "GET";
      const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      requests.push({ method, path: url.pathname, params: url.searchParams, body });

      if (method === "GET" && url.pathname === "/api/v1/itsm/assets") {
        const page = Number(url.searchParams.get("page") ?? "1");
        const items = REGISTER.slice((page - 1) * PER_PAGE, page * PER_PAGE);
        return ok(200, { items, total: REGISTER.length, page, size: PER_PAGE, pages: 2 });
      }
      if (method === "POST" && url.pathname === "/api/v1/itsm/assets" && isRecord(body)) {
        const tag = String(body.tag);
        if (REGISTER.some((existing) => existing.tag === tag)) {
          return problem(409, "Conflict", "That tag is already in use.");
        }
        if (tag === "NW-0009") {
          return problem(422, "Unprocessable Entity", "Request validation failed", [
            {
              field: "warranty_expires_at",
              code: "date_order",
              message: "The warranty cannot end before the purchase date.",
            },
          ]);
        }
        return ok(201, asset({ tag, name: String(body.name) }));
      }
      throw new Error(`Unexpected request: ${method} ${input}`);
    }),
  );
  return {
    posts: () => requests.filter((request) => request.method === "POST"),
    lists: () => requests.filter((request) => request.method === "GET"),
  };
}

// ------------------------------------------------------------------------------------ the dialog

const NO_FILTERS: AssetFilters = { search: "", type: "all", status: "all" };

interface HarnessProps {
  filters: AssetFilters;
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

function Harness({ filters, onImported }: HarnessProps) {
  const dx = useImportExport(assetsEntity, { filters, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}

async function openDialog(filters: AssetFilters = NO_FILTERS, onImported?: HarnessProps["onImported"]) {
  const user = userEvent.setup();
  render(<Harness filters={filters} onImported={onImported} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  await screen.findByRole("dialog", { name: "Import / Export assets" });
  return user;
}

/** The figure beside a count's label, from the term and its definition. */
function figure(label: string): string | null | undefined {
  return screen.getAllByRole("term").find((term) => term.textContent === label)?.nextElementSibling?.textContent;
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (input === null) throw new Error("The dialog has no file input");
  return input;
}

// ------------------------------------------------------------------------------------ workbooks

/** Every column the entity has, read-only ones included: a file exported earlier has them all. */
const FILE_COLUMNS = assetsEntity.columns;

async function importFile(rows: readonly Record<string, ExcelJS.CellValue>[]): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...FILE_COLUMNS.map((c) => (c.required === true ? `${c.header} *` : c.header)), "Purchase order"]);
  for (const row of rows) sheet.addRow([...FILE_COLUMNS.map((c) => row[c.key] ?? null), row.purchase_order ?? null]);
  for (const key of ["purchased_at", "warranty_expires_at"]) {
    sheet.getColumn(FILE_COLUMNS.findIndex((c) => c.key === key) + 1).numFmt = "yyyy-mm-dd";
  }
  return new File([await workbook.xlsx.writeBuffer()], "assets.xlsx", { type: XLSX_TYPE });
}

async function readDownload(download: Download | undefined): Promise<ExcelJS.Workbook> {
  if (download?.blob === undefined) throw new Error("Nothing was downloaded");
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await download.blob.arrayBuffer());
  return workbook;
}

function sheetNamed(workbook: ExcelJS.Workbook, name: string): ExcelJS.Worksheet {
  const sheet = workbook.getWorksheet(name);
  if (sheet === undefined) throw new Error(`The workbook has no sheet "${name}"`);
  return sheet;
}

/** A row of a sheet as text by its header, so a test reads "Cost" and not column J. */
function rowByHeader(sheet: ExcelJS.Worksheet, rowNumber: number): Record<string, string> {
  const record: Record<string, string> = {};
  sheet.getRow(1).eachCell({ includeEmpty: false }, (header, column) => {
    record[header.text.replace(/ \*$/, "")] = sheet.getRow(rowNumber).getCell(column).text;
  });
  return record;
}

// ------------------------------------------------------------------------------------ importing

const SAMPLE_ROW = assetsEntity.sampleRows[0] ?? {};

const FILE_ROWS: Record<string, ExcelJS.CellValue>[] = [
  // 2: a full row. The cost is an amount in dollars, the purchase date is a real Excel date, the
  // serial keeps its leading zeros, and the file carries columns an import must ignore.
  {
    tag: "SRV-0042",
    name: "Rack server",
    type: "Server",
    status: "In repair",
    owner: "Data centre team",
    location: "Mumbai DC, row 4",
    serial: "00123ABC",
    purchased_at: new Date(Date.UTC(2025, 10, 3)),
    warranty_expires_at: "2028-11-02",
    cost: 12500.5,
    cost_currency: "usd",
    open_incidents: 3,
    purchase_order: "PO-77",
  },
  // 3: a currency with no minor unit, and a type written as the API's own value.
  { tag: "SW-0310", name: "Design licences", type: "license", cost: 250000, cost_currency: "JPY" },
  // 4: the register already has this tag.
  { tag: "MN-0002", name: "Duplicate monitor", type: "Monitor" },
  // 5: the server refuses this one.
  {
    tag: "NW-0009",
    name: "Core switch",
    type: "Network",
    purchased_at: "2026-05-01",
    warranty_expires_at: "2025-01-01",
  },
  // 6: no name.
  { tag: "MN-0100", type: "Monitor" },
  // 7: a type the list does not have.
  { tag: "TO-0001", name: "Toaster", type: "Toaster" },
  // 8: more decimals than a dollar has.
  { tag: "SRV-0043", name: "Second server", type: "Server", cost: "12.345", cost_currency: "USD" },
  // 9: the sample sheet's first example, untouched.
  { ...SAMPLE_ROW },
];

const MISSING_NAME = "Name is required";
const UNKNOWN_TYPE =
  "'Toaster' is not one of the allowed values for Type: Laptop, Desktop, Server, Monitor, Phone, Network, License";
const TOO_PRECISE = "Cost can have at most 2 decimal places for USD";
const REFUSED = "Request validation failed (warranty_expires_at: The warranty cannot end before the purchase date.)";

describe("importing assets from Excel", () => {
  it(
    "previews the file, sends each valid row as the API's own body, and reports what happened to every row",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);

      await user.upload(fileInput(), await importFile(FILE_ROWS));
      await screen.findByRole("dialog", { name: "Review your file" });

      // The preview: counts first, then the reason against each row that cannot go.
      expect(figure("Rows in file")).toBe("8");
      expect(figure("Ready to import")).toBe("4");
      expect(figure("Need fixing")).toBe("3");
      expect(figure("Sample rows skipped")).toBe("1");
      expect(screen.getByText(MISSING_NAME)).toBeTruthy();
      expect(screen.getByText(UNKNOWN_TYPE)).toBeTruthy();
      expect(screen.getByText(TOO_PRECISE)).toBeTruthy();
      expect(screen.getByText("An unchanged example from the sample sheet. It is skipped.")).toBeTruthy();
      expect(screen.getByText("These columns are ignored: Purchase order.")).toBeTruthy();
      expect(gateway.posts()).toHaveLength(0);

      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      // (a) What was sent, in sheet order: minor units by each currency's own decimals, ISO dates,
      // and no currency where there was no cost.
      const bodies = gateway.posts().map((request) => request.body);
      expect(bodies).toEqual([
        {
          tag: "SRV-0042",
          name: "Rack server",
          type: "server",
          status: "in_repair",
          owner: "Data centre team",
          location: "Mumbai DC, row 4",
          serial: "00123ABC",
          purchased_at: "2025-11-03",
          warranty_expires_at: "2028-11-02",
          cost_minor: 1250050,
          cost_currency: "USD",
        },
        { tag: "SW-0310", name: "Design licences", type: "license", cost_minor: 250000, cost_currency: "JPY" },
        { tag: "MN-0002", name: "Duplicate monitor", type: "monitor" },
        {
          tag: "NW-0009",
          name: "Core switch",
          type: "network",
          purchased_at: "2026-05-01",
          warranty_expires_at: "2025-01-01",
        },
      ]);
      for (const body of bodies) {
        expect(body).not.toHaveProperty("open_incidents");
        expect(body).not.toHaveProperty("purchase_order");
      }

      // (b) The outcome: four counts, one sentence, and the server's own words against its rows.
      expect(figure("Created")).toBe("2");
      expect(figure("Skipped, already exist")).toBe("1");
      expect(figure("Failed")).toBe("1");
      expect(figure("Invalid")).toBe("3");
      expect(screen.getByText("4 rows were not imported. 2 assets were created.")).toBeTruthy();
      const table = within(screen.getByRole("region", { name: "Rows that were not created" }));
      expect(table.getByText("That tag is already in use.")).toBeTruthy();
      expect(table.getByText(REFUSED)).toBeTruthy();
      expect(table.getByText(MISSING_NAME)).toBeTruthy();
      expect(table.getByText(UNKNOWN_TYPE)).toBeTruthy();
      expect(table.getByText(TOO_PRECISE)).toBeTruthy();
      // Each is against its own row in the sheet: the sheet's row numbers, header excluded.
      const numberOf = (text: string) => {
        const row = table.getByText(text).closest("tr");
        return row === null ? null : within(row).getByRole("rowheader").textContent;
      };
      expect(numberOf("That tag is already in use.")).toBe("4");
      expect(numberOf(REFUSED)).toBe("5");
      expect(numberOf(MISSING_NAME)).toBe("6");
      expect(numberOf(UNKNOWN_TYPE)).toBe("7");
      expect(numberOf(TOO_PRECISE)).toBe("8");

      // (c) The screen is told once, so it can reload its list.
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(
        expect.objectContaining({ created: 2, skipped: 1, failed: 1, invalid: 3, cancelled: false }),
      );

      await user.click(screen.getByRole("button", { name: "Done" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(onImported).toHaveBeenCalledTimes(1);
    },
    TEST_TIMEOUT,
  );

  it(
    "keeps the result of the run as a workbook the person can download",
    async () => {
      stubGateway();
      const user = await openDialog();
      await user.upload(fileInput(), await importFile(FILE_ROWS));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      await user.click(screen.getByRole("button", { name: "Download result report" }));
      await waitFor(() => expect(downloads).toHaveLength(1));

      expect(downloads[0]?.name).toBe("assets-import-result.xlsx");
      const sheet = sheetNamed(await readDownload(downloads[0]), "Result");
      const lines: string[] = [];
      sheet.eachRow((row) => lines.push([1, 2, 3, 4].map((column) => row.getCell(column).text).join(" | ")));
      expect(lines).toEqual([
        "Row | Asset tag | Status | Message",
        "2 | SRV-0042 | Created | ",
        "3 | SW-0310 | Created | ",
        "4 | MN-0002 | Skipped | That tag is already in use.",
        `5 | NW-0009 | Failed | ${REFUSED}`,
        `6 | MN-0100 | Invalid | ${MISSING_NAME}`,
        `7 | TO-0001 | Invalid | ${UNKNOWN_TYPE}`,
        `8 | SRV-0043 | Invalid | ${TOO_PRECISE}`,
      ]);
    },
    TEST_TIMEOUT,
  );

  it(
    "creates nothing when every row already exists, and does not ask the screen to reload",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);
      const again = [
        { tag: "LT-0001", name: "Laptop", type: "Laptop" },
        { tag: "MN-0002", name: "Monitor", type: "Monitor" },
      ];

      await user.upload(fileInput(), await importFile(again));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 2 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      expect(gateway.posts()).toHaveLength(2);
      expect(screen.getByText("Nothing new to import. Every row already exists, so nothing was overwritten.")).toBeTruthy();
      expect(figure("Created")).toBe("0");
      expect(figure("Skipped, already exist")).toBe("2");
      expect(onImported).not.toHaveBeenCalled();
    },
    TEST_TIMEOUT,
  );

  it(
    "stops after the row in flight when cancelled, keeps what was created, and tells the screen once",
    async () => {
      const gateway = stubGateway();
      const onImported = vi.fn();
      const user = await openDialog(NO_FILTERS, onImported);
      const rows = [3021, 3022, 3023, 3024].map((number) => ({
        tag: `SRV-${number}`,
        name: `Server ${number}`,
        type: "Server",
      }));

      await user.upload(fileInput(), await importFile(rows));
      await screen.findByRole("dialog", { name: "Review your file" });
      await user.click(screen.getByRole("button", { name: "Import 4 rows" }));
      await waitFor(() => expect(gateway.posts().length).toBeGreaterThan(0));
      await user.click(screen.getByRole("button", { name: "Cancel import" }));
      await screen.findByRole("dialog", { name: "Import cancelled" }, { timeout: 10_000 });

      const sent = gateway.posts().length;
      expect(sent).toBeGreaterThanOrEqual(1);
      expect(sent).toBeLessThan(4);
      expect(figure("Created")).toBe(String(sent));
      expect(screen.getByText(/The remaining rows were not sent\.$/)).toBeTruthy();
      expect(onImported).toHaveBeenCalledTimes(1);
      expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ created: sent, cancelled: true }));
    },
    TEST_TIMEOUT,
  );

  it(
    "refuses a file that lacks the required columns before anything is sent",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      const workbook = new ExcelJS.Workbook();
      const sheet = workbook.addWorksheet("Import");
      sheet.addRow(["Owner", "Location"]);
      sheet.addRow(["Anita Desai", "Pune"]);
      const file = new File([await workbook.xlsx.writeBuffer()], "partial.xlsx", { type: XLSX_TYPE });

      await user.upload(fileInput(), file);
      await screen.findByRole("dialog", { name: "Review your file" });

      expect(screen.getByText("Your file is missing required columns: Asset tag, Name, Type.")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(gateway.posts()).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ exporting

describe("exporting assets to Excel", () => {
  it(
    "asks the list for every page with the screen's filters and writes a workbook with no formulas",
    async () => {
      const gateway = stubGateway();
      const filters: AssetFilters = { search: "dell", type: "monitor", status: "in_use" };
      const user = await openDialog(filters);

      await user.click(screen.getByRole("button", { name: "Export to Excel" }));
      await waitFor(() => expect(downloads).toHaveLength(1));

      // The list endpoint, once per page, with the filters the screen had; not the page window.
      expect(gateway.lists().map((request) => Object.fromEntries(request.params))).toEqual([
        { q: "dell", type: "monitor", status: "in_use", page: "1", size: "200" },
        { q: "dell", type: "monitor", status: "in_use", page: "2", size: "200" },
      ]);

      expect(downloads[0]?.name).toMatch(/^assets-\d{4}-\d{2}-\d{2}\.xlsx$/);
      const sheet = sheetNamed(await readDownload(downloads[0]), "Export");
      const headers: string[] = [];
      sheet.getRow(1).eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      expect(headers).toEqual(assetsEntity.columns.map((column) => column.header));
      expect(sheet.actualRowCount).toBe(1 + REGISTER.length);

      expect(rowByHeader(sheet, 3)).toMatchObject({
        "Asset tag": "MN-0002",
        Type: "Monitor",
        Status: "In use",
        "Cost currency": "KWD",
      });
      // Money is a number in major units (1250 fils is 1.25 dinars), shown with the currency's own decimals.
      const costColumn = headers.indexOf("Cost") + 1;
      const inr = sheet.getCell(2, costColumn);
      const kwd = sheet.getCell(3, costColumn);
      const jpy = sheet.getCell(4, costColumn);
      expect([inr.value, kwd.value, jpy.value]).toEqual([84500, 1.25, 250000]);
      expect([inr.numFmt, kwd.numFmt, jpy.numFmt]).toEqual(["#,##0.00", "#,##0.000", "#,##0"]);
      expect(sheet.getCell(2, headers.indexOf("Open incidents") + 1).value).toBe(2);

      // Text that looks like a formula is a string, and no cell anywhere is a formula.
      for (const [header, text] of [
        ["Name", "=1+1"],
        ["Owner", "+91 98765 43210"],
        ["Location", "@SUM(A1:A9)"],
        ["Serial number", "-2+3"],
      ] as const) {
        const cell = sheet.getCell(2, headers.indexOf(header) + 1);
        expect(cell.value, header).toBe(text);
        expect(cell.type, header).toBe(ExcelJS.ValueType.String);
      }
      const formulas: string[] = [];
      sheet.eachRow((row) =>
        row.eachCell({ includeEmpty: false }, (cell) => {
          if (cell.type === ExcelJS.ValueType.Formula) formulas.push(cell.address);
        }),
      );
      expect(formulas).toEqual([]);
    },
    TEST_TIMEOUT,
  );

  it(
    "writes a file that imports again as the same records, cost and currency included",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Export to Excel" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const blob = downloads[0]?.blob;
      if (blob === undefined) throw new Error("Nothing was downloaded");

      await user.upload(fileInput(), new File([blob], "assets-export.xlsx", { type: XLSX_TYPE }));
      await screen.findByRole("dialog", { name: "Review your file" });
      expect(figure("Rows in file")).toBe("3");
      expect(figure("Ready to import")).toBe("3");
      expect(figure("Need fixing")).toBe("0");

      await user.click(screen.getByRole("button", { name: "Import 3 rows" }));
      await screen.findByRole("dialog", { name: "Import finished" }, { timeout: 10_000 });

      // Every tag is on the register already, so each row is skipped; what was sent is what the
      // API had returned, down to the minor unit of each currency, and never the incident count.
      expect(gateway.posts().map((request) => request.body)).toEqual([
        {
          tag: "LT-0001",
          name: "=1+1",
          type: "laptop",
          status: "in_use",
          owner: "+91 98765 43210",
          location: "@SUM(A1:A9)",
          serial: "-2+3",
          purchased_at: "2025-08-14",
          cost_minor: 8450000,
          cost_currency: "INR",
        },
        { tag: "MN-0002", name: "Dell U2723QE monitor", type: "monitor", status: "in_use", cost_minor: 1250, cost_currency: "KWD" },
        {
          tag: "SW-0003",
          name: "Design licences",
          type: "license",
          status: "retired",
          cost_minor: 250000,
          cost_currency: "JPY",
        },
      ]);
      expect(figure("Skipped, already exist")).toBe("3");
    },
    TEST_TIMEOUT,
  );
});

// ------------------------------------------------------------------------------------ sample sheet

describe("the assets sample sheet", () => {
  async function downloadSample(): Promise<ExcelJS.Workbook> {
    stubGateway();
    const user = await openDialog();
    await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe("assets-sample.xlsx");
    return readDownload(downloads[0]);
  }

  const importable = assetsEntity.columns.filter((column) => column.readOnly !== true);

  it(
    "has Import and Instructions, with required headers marked * and nothing the system sets",
    async () => {
      const workbook = await downloadSample();
      // Assets have no reference data, so there is nothing for a Lists sheet to hold.
      expect(workbook.worksheets.map((sheet) => sheet.name)).toEqual(["Import", "Instructions"]);

      const headers: string[] = [];
      sheetNamed(workbook, "Import")
        .getRow(1)
        .eachCell({ includeEmpty: false }, (cell) => headers.push(cell.text));
      expect(headers).toEqual(
        importable.map((column) => (column.required === true ? `${column.header} *` : column.header)),
      );
      expect(headers.filter((header) => header.endsWith("*"))).toEqual(["Asset tag *", "Name *", "Type *"]);
      expect(headers).not.toContain("Open incidents");
    },
    TEST_TIMEOUT,
  );

  it(
    "carries the example rows, with money as numbers and dates as dates",
    async () => {
      const sheet = sheetNamed(await downloadSample(), "Import");
      expect(sheet.actualRowCount).toBe(1 + assetsEntity.sampleRows.length);

      expect(rowByHeader(sheet, 2)).toMatchObject({
        "Asset tag": "LT-2041",
        Name: "Dell Latitude 7440",
        Type: "Laptop",
        Status: "In use",
        "Cost currency": "INR",
      });
      expect(rowByHeader(sheet, 3)).toMatchObject({ "Asset tag": "SW-0310", Type: "License", "Cost currency": "USD" });
      expect(rowByHeader(sheet, 4)).toMatchObject({ "Asset tag": "MN-0087", Status: "In stock", "Cost currency": "" });

      const position = (key: string) => importable.findIndex((column) => column.key === key) + 1;
      const cost = sheet.getCell(2, position("cost"));
      expect(cost.value).toBe(84500);
      expect(cost.numFmt).toBe("#,##0.00");
      const purchased = sheet.getCell(2, position("purchased_at")).value;
      if (!(purchased instanceof Date)) throw new Error("The purchase date was not written as a date");
      expect(purchased.toISOString().slice(0, 10)).toBe("2025-08-14");
    },
    TEST_TIMEOUT,
  );

  it(
    "explains every column and repeats every rule the entity states",
    async () => {
      const sheet = sheetNamed(await downloadSample(), "Instructions");
      const firstColumn: string[] = [];
      const rows = new Map<string, string[]>();
      sheet.eachRow((row) => {
        const cells: string[] = [];
        for (let column = 1; column <= 6; column += 1) cells.push(row.getCell(column).text);
        firstColumn.push(cells[0] ?? "");
        rows.set(cells[0] ?? "", cells);
      });

      for (const column of importable) {
        const line = rows.get(column.header);
        expect(line, column.header).toBeDefined();
        expect(line?.[1], `${column.header} required`).toBe(column.required === true ? "Yes" : "No");
        expect(line?.[5], `${column.header} help`).toBe(column.help);
      }
      expect(rows.get("Type")?.[3]).toBe("Laptop, Desktop, Server, Monitor, Phone, Network, License");
      expect(rows.get("Status")?.[3]).toBe("In use, In stock, In repair, Retired");
      expect(rows.get("Cost")?.[2]).toBe(
        'Amount in major units (1499.50, not 149950); currency in the "Cost currency" column, at least 0',
      );
      expect(firstColumn).not.toContain("Open incidents");
      for (const note of assetsEntity.notes ?? []) expect(firstColumn, note).toContain(note);
    },
    TEST_TIMEOUT,
  );

  it(
    "offers dropdowns over the types and statuses, and none over free text, money or dates",
    async () => {
      const imports = sheetNamed(await downloadSample(), "Import");
      const validationOf = (key: string): ExcelJS.DataValidation | undefined =>
        imports.getCell(2, importable.findIndex((column) => column.key === key) + 1).dataValidation;

      expect(validationOf("type")).toMatchObject({
        type: "list",
        formulae: ['"Laptop,Desktop,Server,Monitor,Phone,Network,License"'],
      });
      expect(validationOf("status")).toMatchObject({
        type: "list",
        formulae: ['"In use,In stock,In repair,Retired"'],
      });
      for (const key of ["tag", "name", "owner", "serial", "purchased_at", "cost", "cost_currency"]) {
        expect(validationOf(key), key).toBeUndefined();
      }
    },
    TEST_TIMEOUT,
  );

  it(
    "is recognised as examples when it is chosen again unchanged, so nothing is imported",
    async () => {
      const gateway = stubGateway();
      const user = await openDialog();
      await user.click(screen.getByRole("button", { name: "Download sample sheet" }));
      await waitFor(() => expect(downloads).toHaveLength(1));
      const blob = downloads[0]?.blob;
      if (blob === undefined) throw new Error("Nothing was downloaded");

      await user.upload(fileInput(), new File([blob], "assets-sample.xlsx", { type: XLSX_TYPE }));
      await screen.findByRole("dialog", { name: "Review your file" });

      expect(figure("Rows in file")).toBe("3");
      expect(figure("Sample rows skipped")).toBe("3");
      expect(figure("Ready to import")).toBe("0");
      expect(screen.getByText("Only the sample rows are in this file.")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Import 0 rows" }).hasAttribute("disabled")).toBe(true);
      expect(gateway.posts()).toHaveLength(0);
    },
    TEST_TIMEOUT,
  );
});
